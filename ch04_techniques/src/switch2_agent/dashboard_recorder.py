"""ダッシュボードが読む、フレームの判定と縮小画像・送ったレポートの記録を書く (起動引数 --record-dashboard)。

書くファイル (run_dir は実行のディレクトリー runs/<YYYYmmdd-HHMMSS>/。行の形は dashboard_records):
    run_dir/frames.jsonl       判定に入れたフレーム 1 枚につき 1 行
    run_dir/thumbs/<名前>.jpg   判定に入れたフレームの縮小画像 (320x180、JPEG の品質 70)。名前は
                               dashboard_records.thumb_file_name
    run_dir/pad.jsonl          PadOutput がレポートを送ろうとした 1 回につき 1 行 (送信に失敗したときも書く)
    run.jsonl (log に渡す)      書き込みの失敗の record_error と、終了時の record_end

frames と pad は、種類ごとに上限つきのキューと専用の書き出しスレッド (record_writer.BoundedRecordWriter) で書く。
記録する側 (フレームの読み取りスレッドと PadOutput のロックの中) はキューに入れるだけにし、縮小画像の作成
(NV12 から BGR への変換、縮小、JPEG の符号化) と、直前のフレームとの差分の計算は、書き出しスレッドで行う。
書き込みに失敗したら、その種類の記録だけをやめ、log に record_error を渡す。エージェントは止めない。
強制終了などで close を呼ばずに終わった実行には、record_end が無い。
"""

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

import numpy as np
from switch2_remote.capture import DEVICE_HEIGHT

from switch2_agent.dashboard_records import (
    FRAMES_RECORD_FILE_NAME,
    PAD_RECORD_FILE_NAME,
    THUMB_JPEG_QUALITY,
    THUMB_WIDTH,
    THUMBS_DIR_NAME,
    DetectorSnapshot,
    FrameStep,
    PadReport,
    build_frame_record,
    build_pad_record,
    build_record_end_event,
    build_record_error_event,
    snapshot_detector,
    thumb_file_name,
)
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.meaningful_frames import (
    MeaningfulFrameStore,
    SharpnessValleyDetector,
)
from switch2_agent.image_metrics import mean_abs_diff, nv12_to_jpeg
from switch2_agent.record_writer import BoundedRecordWriter

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# frames のキューの上限 (件)。10 枚/秒で 60 秒分
FRAMES_QUEUE_CAPACITY = 600
# pad のキューの上限 (件)
PAD_QUEUE_CAPACITY = 2000
# close で、2 つのキューを書き切るのを待つ時間の上限 (秒。2 つ合わせて)
RECORDER_CLOSE_WAIT_S = 3.0


def _open_record_file(path: Path) -> TextIO:
    """JSON Lines の記録のファイル path を、UTF-8 の追記で開く。

    UTF-8 に符号化できない文字 (対になっていないサロゲート) は、errors="backslashreplace" で `\\udXXX` の形の
    まま書く。JSON の文字列の中では同じ escape になるため、書いた行は JSON として読め、読むと元の文字に戻る。
    追記にするのは、run.jsonl と同じく、同じ実行のディレクトリーに既にある行を消さないため。
    """
    return path.open("a", encoding="utf-8", errors="backslashreplace")


def _write_record_line(file: TextIO, record: dict) -> None:
    """record を JSON (ensure_ascii=False) の 1 行にし、改行まで 1 回の write で file に書いて flush する。

    行の途中までがファイルに出ないよう 1 回の write で書き、ほかのプロセスから書いた行をすぐに読めるよう flush する。
    """
    file.write(json.dumps(record, ensure_ascii=False) + "\n")
    file.flush()


class DashboardRecorder:
    """frames.jsonl・thumbs/・pad.jsonl を、種類ごとの書き出しスレッドで書く。

    run_dir   実行のディレクトリー (呼び出し側が作る)
    log       run.jsonl に 1 件を書く関数 (run では RunLog.write)。record_error と record_end を渡す。
              書き出しスレッドと、close を呼んだスレッドから呼ぶ

    frames の 1 件の書き出し (書き出しスレッド):
        1. image_metrics.nv12_to_jpeg(frame.nv12, DEVICE_HEIGHT, THUMB_WIDTH, THUMB_JPEG_QUALITY) (320x180 になる) を
           run_dir / thumb_file_name(frame.time) に書く
        2. diff を求める: 直前に書き出しスレッドが扱ったフレームの gray_small との image_metrics.mean_abs_diff。
           最初のフレームと、FrameStep.after_drop が True のときは None
        3. build_frame_record(step, diff) の辞書を JSON (ensure_ascii=False) の 1 行にし、改行まで 1 回の write で
           frames.jsonl に書いて flush する (ほかのプロセスから、書いた行をすぐに読めるようにするため)
    pad の 1 件の書き出し: build_pad_record(report) の辞書を、同じく pad.jsonl に 1 行書いて flush する。
    JSON Lines のファイルは、それぞれの書き出しスレッドが最初の 1 件を書くときに開いて持ち (UTF-8、
    errors="backslashreplace"。UTF-8 で書けない単独のサロゲートを `\\udXXX` の escape として書く)、その writer の
    on_finish で閉じる (開いていなければ何もしない)。on_finish は書き出しスレッドが終わるとき (書き切ったとき、
    書き込みの失敗で止めたとき、close の時間切れで手放した後に書いている途中の 1 件が終わったとき) に呼ばれる。
    書き込みの失敗 (BoundedRecordWriter の on_error) は、log(build_record_error_event(kind, error)) で知らせる。
    log の例外 (Exception) は捕まえて logging の警告にする。
    """

    _frames_writer: BoundedRecordWriter[FrameStep]
    _pad_writer: BoundedRecordWriter[PadReport]

    def __init__(self, run_dir: Path, log: Callable[[dict], None]):
        """run_dir / THUMBS_DIR_NAME を作り (既にあってもよい)、frames と pad の BoundedRecordWriter を作る。

        frames の writer は kind="frames"・capacity=FRAMES_QUEUE_CAPACITY、pad の writer は kind="pad"・
        capacity=PAD_QUEUE_CAPACITY で作る。どちらにも、on_finish として、その writer の JSON Lines のファイル
        (frames.jsonl か pad.jsonl。書き出しスレッドが最初の 1 件で開いたもの) を閉じる関数を渡す
        (開いていなければ何もしない)。
        """
        self.run_dir = run_dir
        self.log = log
        (run_dir / THUMBS_DIR_NAME).mkdir(exist_ok=True)
        # 開いている JSON Lines のファイル。それぞれの書き出しスレッドだけが読み書きする
        self._frames_file: TextIO | None = None
        self._pad_file: TextIO | None = None
        # 書き出しスレッドが直前に扱ったフレームの縮小したグレー画像 (diff を求めるため)。
        # frames の書き出しスレッドだけが読み書きする
        self._previous_gray: np.ndarray | None = None
        # 前回の frames の put で記録を捨てたか。record_frame を呼ぶフレームの読み取りスレッドだけが読み書きする
        self._previous_frame_dropped = False
        self._closed = False
        self._frames_writer = BoundedRecordWriter(
            "frames",
            FRAMES_QUEUE_CAPACITY,
            self._write_frame,
            self._report_record_error,
            on_finish=self._close_frames_file,
        )
        self._pad_writer = BoundedRecordWriter(
            "pad",
            PAD_QUEUE_CAPACITY,
            self._write_pad,
            self._report_record_error,
            on_finish=self._close_pad_file,
        )

    def record_frame(
        self,
        frame: TimedFrame,
        before: DetectorSnapshot,
        after: DetectorSnapshot,
        kept: TimedFrame | None,
        valley_ratio: float,
    ) -> None:
        """判定に入れたフレーム 1 枚分の FrameStep を組み立て、frames の writer に put する。例外を出さない。

        feed_detector から、フレームの読み取りスレッドで呼ぶ。キューに入れる以外の I/O・符号化・縮小をしない。
        FrameStep の after_drop は、この DashboardRecorder の前回の frames の put が False (捨てた) なら True にする。
        最初のフレームでは False。
        """
        step = FrameStep(
            frame=frame,
            before=before,
            after=after,
            kept=kept,
            valley_ratio=valley_ratio,
            after_drop=self._previous_frame_dropped,
        )
        self._previous_frame_dropped = not self._frames_writer.put(step)

    def record_pad(self, report: PadReport) -> None:
        """report を pad の writer に put する。例外を出さない。

        PadOutput の on_report として、PadOutput のロックの中から呼ぶ。キューに入れる以外のことをしない。
        """
        self._pad_writer.put(report)

    def close(self) -> None:
        """記録の受け付けをやめ、キューの分を書き切るのを待ってから、log に record_end を 1 回渡す。

        1. frames と pad の writer の stop_accepting を呼ぶ (両方の受け付けを先に止める)
        2. 2 つの writer の wait_until_written を、合わせて最大 RECORDER_CLOSE_WAIT_S 秒待つ (締め切りは
           time.monotonic で測り、2 つ目には残りの時間を渡す。書き切れなかった分は、writer が dropped に足す)
        3. log(build_record_end_event({"frames": 書いた件数, "pad": 書いた件数},
           {"frames": 捨てた件数, "pad": 捨てた件数})) を渡す。log の例外 (Exception) は捕まえて logging の警告にする
        例外を出さない。2 回目以降の呼び出しは何もしない。
        """
        if self._closed:
            return
        self._closed = True
        writers = {"frames": self._frames_writer, "pad": self._pad_writer}
        for writer in writers.values():
            writer.stop_accepting()
        deadline = time.monotonic() + RECORDER_CLOSE_WAIT_S
        for writer in writers.values():
            writer.wait_until_written(deadline - time.monotonic())
        record_end = build_record_end_event(
            {kind: writer.written for kind, writer in writers.items()},
            {kind: writer.dropped for kind, writer in writers.items()},
        )
        try:
            self.log(record_end)
        except Exception:
            logger.warning("failed to log the end of dashboard records", exc_info=True)

    def _write_frame(self, step: FrameStep) -> None:
        """frames の 1 件を書く (frames の書き出しスレッドの write_item。手順はクラスの docstring)。"""
        frame = step.frame
        thumb = nv12_to_jpeg(frame.nv12, DEVICE_HEIGHT, THUMB_WIDTH, THUMB_JPEG_QUALITY)
        (self.run_dir / thumb_file_name(frame.time)).write_bytes(thumb)
        previous_gray = self._previous_gray
        self._previous_gray = frame.gray_small
        # 直前の記録を捨てたときは、直前に扱ったフレームが 1 つ前のフレームではないため、差分を求めない
        if previous_gray is None or step.after_drop:
            diff = None
        else:
            diff = mean_abs_diff(previous_gray, frame.gray_small)
        if self._frames_file is None:
            self._frames_file = _open_record_file(
                self.run_dir / FRAMES_RECORD_FILE_NAME
            )
        _write_record_line(self._frames_file, build_frame_record(step, diff))

    def _write_pad(self, report: PadReport) -> None:
        """pad の 1 件を pad.jsonl に書く (pad の書き出しスレッドの write_item)。"""
        if self._pad_file is None:
            self._pad_file = _open_record_file(self.run_dir / PAD_RECORD_FILE_NAME)
        _write_record_line(self._pad_file, build_pad_record(report))

    def _close_frames_file(self) -> None:
        """frames.jsonl を開いていれば閉じる (frames の書き出しスレッドの on_finish)。"""
        file = self._frames_file
        self._frames_file = None
        if file is not None:
            file.close()

    def _close_pad_file(self) -> None:
        """pad.jsonl を開いていれば閉じる (pad の書き出しスレッドの on_finish)。"""
        file = self._pad_file
        self._pad_file = None
        if file is not None:
            file.close()

    def _report_record_error(self, kind: str, error: str) -> None:
        """書き込みの失敗を log に record_error として渡す (BoundedRecordWriter の on_error。書き出しスレッドから呼ぶ)。

        log の例外 (Exception) は捕まえて logging の警告にする。
        """
        try:
            self.log(build_record_error_event(kind, error))
        except Exception:
            logger.warning(
                "failed to log the record error of %s records", kind, exc_info=True
            )


def feed_detector(
    frame: TimedFrame,
    detector: SharpnessValleyDetector,
    store: MeaningfulFrameStore,
    recorder: DashboardRecorder | None,
) -> None:
    """frame を意味のあるフレームの判定に入れ、残したフレームを保管に足し、recorder があれば判定の前後を記録する。

    run.main の CaptureWithHistory の on_frame から、フレームの読み取りスレッドで呼ぶ。
        1. before = snapshot_detector(detector)
        2. kept = detector.feed(frame)。kept が None でなければ store.add(kept)
        3. recorder が None でなければ、recorder.record_frame(frame, before, snapshot_detector(detector), kept,
           detector.valley_ratio)
    recorder が None なら、判定に入れて残したフレームを保管に足すだけ。
    """
    before = snapshot_detector(detector)
    kept = detector.feed(frame)
    if kept is not None:
        store.add(kept)
    if recorder is not None:
        recorder.record_frame(
            frame, before, snapshot_detector(detector), kept, detector.valley_ratio
        )
