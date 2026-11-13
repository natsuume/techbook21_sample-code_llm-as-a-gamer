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

from collections.abc import Callable
from pathlib import Path

from switch2_agent.dashboard_records import DetectorSnapshot, FrameStep, PadReport
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.meaningful_frames import (
    MeaningfulFrameStore,
    SharpnessValleyDetector,
)
from switch2_agent.record_writer import BoundedRecordWriter

# frames のキューの上限 (件)。10 枚/秒で 60 秒分
FRAMES_QUEUE_CAPACITY = 600
# pad のキューの上限 (件)
PAD_QUEUE_CAPACITY = 2000
# close で、2 つのキューを書き切るのを待つ時間の上限 (秒。2 つ合わせて)
RECORDER_CLOSE_WAIT_S = 3.0


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
        raise NotImplementedError

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
        raise NotImplementedError

    def record_pad(self, report: PadReport) -> None:
        """report を pad の writer に put する。例外を出さない。

        PadOutput の on_report として、PadOutput のロックの中から呼ぶ。キューに入れる以外のことをしない。
        """
        raise NotImplementedError

    def close(self) -> None:
        """記録の受け付けをやめ、キューの分を書き切るのを待ってから、log に record_end を 1 回渡す。

        1. frames と pad の writer の stop_accepting を呼ぶ (両方の受け付けを先に止める)
        2. 2 つの writer の wait_until_written を、合わせて最大 RECORDER_CLOSE_WAIT_S 秒待つ (締め切りは
           time.monotonic で測り、2 つ目には残りの時間を渡す。書き切れなかった分は、writer が dropped に足す)
        3. log(build_record_end_event({"frames": 書いた件数, "pad": 書いた件数},
           {"frames": 捨てた件数, "pad": 捨てた件数})) を渡す。log の例外 (Exception) は捕まえて logging の警告にする
        例外を出さない。2 回目以降の呼び出しは何もしない。
        """
        raise NotImplementedError


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
    raise NotImplementedError
