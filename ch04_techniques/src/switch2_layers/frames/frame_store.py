"""フレームの画像の保存 (frames/interval/・frames/surface/・frames/latest/) と、保存した画像の一覧 frames.jsonl。

- 一定間隔の保存: キャプチャーのフレームを interval_fps のフレームレートで間引き、幅 interval_width に縮小して
  frames/interval/<tミリ秒9桁>.jpg に保存する。保存の予定時刻を 1 / interval_fps 秒ずつ進め (advance_interval_schedule)、
  予定時刻以後の最初のフレームを保存する。予定時刻を保存したフレームの t からではなく前回の予定時刻から進めるため、
  キャプチャーのフレームの間隔 (0.1 秒) の倍数でない間隔 (3・7・9 fps など) でも、平均して指定のレートになる
- surface の画像の保存: surface に送った JPEG を frames/surface/ に保存する。キャプチャー時刻 (t のミリ秒) と、
  画像に書き込んだ box の内容 (名前・枠の色を決める番号・座標。frames.surface_frames.drawn_boxes_key) の組ごとに
  1 回だけ保存する。
  ファイル名は、その t で最初に保存する内容なら <tミリ秒9桁>.jpg、同じ t で内容の違う 2 つ目以降は
  <tミリ秒9桁>-2.jpg・<tミリ秒9桁>-3.jpg … (内容ごとの番号は、保存に失敗しても変えない)
- 画像は置き換えの書き込み (file_io.write_bytes_atomic) で書き、画像を書き終えた後に frames.jsonl に
  {"t", "kind": "interval" | "surface", "file": 実行ディレクトリーからの相対パス, "width", "height"} の 1 行を足す
- 最新のフレームの置き換え: latest_frames (frames.latest_frames.LatestFrames) を渡したときだけ (Orchestrator 型の run)、
  一定間隔のフレームの画像を frames/interval/ に書いて frames.jsonl に行を足した後に、
  latest_frames.add_image(t, frames/interval/ の相対パス, 縮小した同じ画像 (BGR), jpeg_quality) を呼ぶ (時刻の
  書き込みと frames/latest/ の置き換えは LatestFrames が行う)。行を足さなかったフレーム (書き込みの失敗・close が
  あきらめた後) は渡さない。latest_frames を渡さなければ (advisor 型) frames/latest/ を書かない。
  frames/interval/ の画像と frames.jsonl の行は、latest_frames の有無で変わらない

書き込み (縮小・JPEG 化・ファイルの書き込み) は専用の書き出しスレッドで行い、受け付けは上限付きのキューで行う。
キューが満杯のときは保存を捨て、捨てた件数を dropped に数える (終了時に run.jsonl の frames_dropped に記録する)。
受け付ける側 (キャプチャーのスレッドと surface のスレッド) は I/O をせず、書き出しスレッドを待たない。
close の待ちが時間切れになったら、書き出しスレッドが画像を書いている途中の 1 件も捨てたものとして数え、その 1 件を
後から書き終えても frames.jsonl に行を足さない。行を足すと決めることと、あきらめて捨てたと数えることは同じロックの中で
決めるため、1 件は written と dropped のどちらか一方にだけ数える。行の追記 (I/O) はロックの外で行うので、close は
追記が止まっていても timeout_s を過ぎて待たない。
"""

import logging
import threading
from collections import deque
from collections.abc import Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np

from switch2_layers.file_io import append_json_line, read_json_lines, write_bytes_atomic
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import (
    DEFAULT_JPEG_QUALITY,
    EncodedImage,
    encode_jpeg,
    nv12_to_bgr,
    shrink_to_width,
)
from switch2_layers.run_clock import t_to_milliseconds
from switch2_layers.run_directory import RunPaths

if TYPE_CHECKING:
    # 型注釈のためだけに読む (frames.latest_frames は frames.surface_frames を読み、その先の deep.deep_scheduler が
    # このモジュールを読むため、実行時に読むと循環する)
    from switch2_layers.frames.latest_frames import LatestFrames

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

DEFAULT_QUEUE_CAPACITY = 64  # キューに入れておける保存の件数の上限
# フレームの t と保存の予定時刻を比べるときの許容 (秒)。t はミリ秒に丸めた値で、予定時刻は 1 / interval_fps の
# 足し算の誤差を含むため、丸めの半分だけ早いフレームも予定時刻に達したものとして扱う
INTERVAL_TIME_TOLERANCE_S = 0.0005
DEFAULT_CLOSE_TIMEOUT_S = 5.0

FrameKind = Literal["interval", "surface"]


@dataclass(frozen=True)
class FrameRecord:
    """frames.jsonl の 1 行。

    t        フレームの時刻 (実行の経過秒)
    kind     "interval" (一定間隔) か "surface" (surface に送った画像)
    file     画像の実行ディレクトリーからの相対パス (例: frames/interval/000012345.jpg)
    width    画像の幅
    height   画像の高さ
    """

    t: float
    kind: FrameKind
    file: str
    width: int
    height: int


@dataclass(frozen=True)
class _SaveItem:
    """キューに入れる保存の 1 件。interval なら nv12、surface なら image と、内容 (content) とその番号 (number) を持つ。"""

    kind: FrameKind
    t: float
    nv12: np.ndarray | None = None
    image: EncodedImage | None = None
    content: Hashable = ()
    number: int = 1


def _record_from_row(row: dict[str, object]) -> FrameRecord | None:
    """frames.jsonl の 1 行を FrameRecord にする。キーが足りないか型が合わなければ None。"""
    t = row.get("t")
    kind = row.get("kind")
    file = row.get("file")
    width = row.get("width")
    height = row.get("height")
    if (
        not _is_number(t)
        or kind not in ("interval", "surface")
        or not isinstance(file, str)
    ):
        return None
    if not _is_int(width) or not _is_int(height):
        return None
    return FrameRecord(t=float(t), kind=kind, file=file, width=width, height=height)


def _is_number(value: object) -> bool:
    """value が数 (int・float。bool を除く) なら True。"""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_int(value: object) -> bool:
    """value が整数 (bool を除く) なら True。"""
    return isinstance(value, int) and not isinstance(value, bool)


def frame_record_row(record: FrameRecord) -> dict[str, object]:
    """frames.jsonl の 1 行の辞書 {"t", "kind", "file", "width", "height"} を返す。"""
    return {
        "t": record.t,
        "kind": record.kind,
        "file": record.file,
        "width": record.width,
        "height": record.height,
    }


def read_frame_records(paths: RunPaths) -> list[FrameRecord]:
    """frames.jsonl を読み (file_io.read_json_lines)、書かれた順の FrameRecord のリストを返す。

    ファイルが無ければ空のリスト。キーが足りない行と型が合わない行は読み飛ばす。
    """
    records: list[FrameRecord] = []
    for row in read_json_lines(paths.frames_log):
        record = _record_from_row(row)
        if record is not None:
            records.append(record)
    return records


def latest_frame_t(paths: RunPaths) -> float | None:
    """frames.jsonl の行の t の最大値 (保存した最新のフレームの時刻) を返す。行が無ければ None。"""
    records = read_frame_records(paths)
    if not records:
        return None
    return max(record.t for record in records)


def advance_interval_schedule(
    next_due_t: float | None, t: float, interval_fps: float
) -> tuple[bool, float]:
    """時刻 t のフレームを一定間隔の保存の対象にするかを決め、(対象なら True, 次の予定時刻) を返す。

    period = 1 / interval_fps として:
        - next_due_t が None (最初のフレーム): 対象。次の予定時刻は t + period
        - t < next_due_t - INTERVAL_TIME_TOLERANCE_S (予定時刻の前): 対象でない。次の予定時刻は next_due_t のまま
        - 予定時刻に達していて、遅れ (t - next_due_t) が 2 * period 未満: 対象。次の予定時刻は next_due_t + period
          (保存したフレームの t からではなく、前回の予定時刻から進める。フレームの到着時刻のゆらぎで予定時刻の
          直前のフレームを逃しても、次のフレームを 1 周期分の遅れで保存し、予定時刻は置き直さない)
        - 遅れが 2 * period 以上 (フレームが途切れて予定時刻を 1 回以上丸ごと逃した): 対象。次の予定時刻は
          t + period に置き直す (途切れた分を後のフレームで取り戻そうとして、続けて保存しない)
    """
    period = 1 / interval_fps
    if next_due_t is None:
        return True, t + period
    if t < next_due_t - INTERVAL_TIME_TOLERANCE_S:
        return False, next_due_t
    if t - next_due_t < 2 * period:
        return True, next_due_t + period
    return True, t + period


class FrameStore:
    """フレームの画像を保存し、frames.jsonl に記録する。

    paths            実行ディレクトリー (frames/interval/ と frames/surface/ は作ってあるものとする)
    interval_fps     一定間隔の保存のフレームレート
    interval_width   一定間隔のフレームを縮小する幅 (image_encoding.shrink_to_width)
    capacity         キューに入れておける件数の上限 (書いている途中の 1 件は含めない)
    jpeg_quality     一定間隔のフレームの JPEG の品質 (frames/latest/ の画像にも使う)
    start_thread     True (既定) なら書き出しスレッド (daemon) を起動する。False ならテストから write_queued を呼ぶ
    latest_frames    一定間隔のフレームの最新の数枚の置き場 (Orchestrator 型の run)。None (既定。advisor 型) なら
                     frames/latest/ を書かない (モジュールの docstring の「最新のフレームの置き換え」)
    """

    def __init__(
        self,
        paths: RunPaths,
        interval_fps: float,
        interval_width: int,
        capacity: int = DEFAULT_QUEUE_CAPACITY,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        start_thread: bool = True,
        latest_frames: "LatestFrames | None" = None,
    ) -> None:
        """空のキューで作る。"""
        self.paths = paths
        self.interval_fps = interval_fps
        self.interval_width = interval_width
        self.capacity = capacity
        self.jpeg_quality = jpeg_quality
        self.latest_frames = latest_frames
        # 受け付け・キュー・件数と、行を足すか close があきらめるかを決めるロック。
        # 受け付ける側と close が待たないよう、この中では I/O をしない
        self._condition = threading.Condition()
        self._queue: deque[_SaveItem] = deque()
        self._next_due_t: float | None = None
        # 予約した (キューに入れた、または書けた) surface の画像の (t のミリ秒, 内容)
        self._reserved_surface: set[tuple[int, Hashable]] = set()
        # surface の画像の t のミリ秒 → その t で受け付けた内容の並び (位置 + 1 がファイル名の番号)
        self._surface_contents: dict[int, list[Hashable]] = {}
        self._written = 0
        self._dropped = 0
        self._accepting = True
        self._closed = False
        # close の時間切れで書き出しをやめた
        self._given_up = False
        # 書き出しスレッドが 1 件を書いている途中で、まだ written にも dropped にも数えていない
        self._writing = False
        # 書いている途中の 1 件について、frames.jsonl に行を足すと決めた (追記を終えたら書き出しスレッドが数える)
        self._appending = False
        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(
                target=self._run_writer, name="frame-store-writer", daemon=True
            )
            self._thread.start()

    def offer_interval(self, frame: CapturedFrame) -> bool:
        """frame が一定間隔の保存の対象 (advance_interval_schedule) なら、保存の分としてキューに入れ、入れたら True を返す。

        - 対象でなければ何もせず False
        - 対象なら、キューに入れられたかに関わらず、次の予定時刻を advance_interval_schedule の値に進める
        - キューが満杯か、close の後なら入れずに dropped を 1 増やして False
        縮小・JPEG 化・書き込みはしない (書き出しスレッドで行う)。
        """
        with self._condition:
            due, self._next_due_t = advance_interval_schedule(
                self._next_due_t, frame.t, self.interval_fps
            )
            if not due:
                return False
            if not self._accepting or len(self._queue) >= self.capacity:
                self._dropped += 1
                return False
            self._queue.append(_SaveItem(kind="interval", t=frame.t, nv12=frame.nv12))
            self._condition.notify_all()
            return True

    def save_surface(
        self, t: float, image: EncodedImage, content: Hashable = ()
    ) -> str | None:
        """surface に送った時刻 t のフレームの画像 image を、保存の分としてキューに入れる。

        content は画像に書き込んだ box の内容 (frames.surface_frames.drawn_boxes_key の値。box を書き込んでいなければ
        空のタプル)。戻り値は保存先の実行ディレクトリーからの相対パスで、t (ミリ秒) と content の組ごとに決まる:
        その t で最初に渡された content なら frames/surface/<tミリ秒9桁>.jpg、同じ t で n 番目 (n >= 2) に渡された
        違う content なら frames/surface/<tミリ秒9桁>-<n>.jpg。組の番号は一度決めたら変えない。
        この相対パスは「保存を予約した」ことを表し、画像を書き終えたことは表さない (書き込みに失敗するか、close で
        捨てられて、画像が書かれないことがある)。
        - 同じ t (ミリ秒) と content の組の画像をキューに入れて、まだ書き込みに失敗していなければ、入れずに同じ
          相対パスを返す (組ごとに 1 回だけ保存する)
        - キューが満杯か、close の後なら入れずに dropped を 1 増やして None を返す
          (その組は保存していないものとして扱い、次に同じ組が渡されたときにもう一度入れようとする)
        - 書き込みに失敗した組と、close で書き終えられずに捨てた組は、予約から外す。次に同じ組が渡されたときに
          もう一度 (同じ相対パスで) 入れようとする
        """
        milliseconds = t_to_milliseconds(t)
        with self._condition:
            contents = self._surface_contents.setdefault(milliseconds, [])
            if content in contents:
                number = contents.index(content) + 1
            else:
                number = len(contents) + 1
            relative = self.paths.relative(self.paths.surface_frame(t, number))
            if (milliseconds, content) in self._reserved_surface:
                return relative
            if not self._accepting or len(self._queue) >= self.capacity:
                self._dropped += 1
                return None
            if content not in contents:
                contents.append(content)
            self._reserved_surface.add((milliseconds, content))
            self._queue.append(
                _SaveItem(
                    kind="surface", t=t, image=image, content=content, number=number
                )
            )
            self._condition.notify_all()
            return relative

    def write_queued(self) -> int:
        """キューにある分を古い順にすべて書き、書けた件数を返す (start_thread が False のときにテストから呼ぶ)。

        1 件ごとに、画像を置き換えの書き込みで書いてから、frames.jsonl に 1 行足す。latest_frames があれば、一定間隔の
        フレームは行を足した後に latest_frames.add_image に渡す (モジュールの docstring の「最新のフレームの置き換え」)。
        書き込みに失敗した 1 件 (OSError・ValueError) は dropped に数え、surface の画像なら予約から外して、次の件に進む。
        書き出しスレッドも、1 件ごとに同じ処理をする。
        """
        count = 0
        while True:
            with self._condition:
                if not self._queue:
                    return count
                item = self._queue.popleft()
            if self._write_item(item):
                count += 1

    def close(self, timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S) -> None:
        """以後の受け付けをやめ、書き出しスレッドがキューの分を書き終えるまで最大 timeout_s 秒待つ。

        待ちが時間切れになったら (書き出しスレッドがまだ動いていたら)、書き終えるのをあきらめる:
            - キューに残った分と、書き出しスレッドが画像を書いている途中の 1 件を dropped に足す
              (キューに残った surface の画像は予約から外す)
            - 以後、書き出しスレッドは frames.jsonl に行を足さない。画像を書いている途中の 1 件は、画像を書き終えても
              行を足さず、written にも数えない (書いた画像のファイルは消さない)
            - 書き出しスレッドが既に行を足すと決めていた 1 件 (行の追記の途中) は dropped に足さない。その 1 件は、
              追記を終えたときに書き出しスレッドが written (追記に失敗したら dropped) に数える
        あきらめることと、書き出しスレッドが行を足すと決めること (_write_item) は同じロック (_condition) の中で決める。
        そのため書いている途中の 1 件は、written と dropped のどちらか一方だけに数える。行の追記 (I/O) はロックの外なので、
        close は追記の終わりを待たない (待つのは書き出しスレッドの join の timeout_s 秒まで)。
        書き出しスレッドが無ければ、キューに残った分を dropped に足す。
        2 回目以降の呼び出しは何もしない。
        """
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._accepting = False
            self._condition.notify_all()
        thread = self._thread
        if thread is not None:
            thread.join(max(timeout_s, 0.0))
        # 書き出しスレッドが行を足すと決めるのと同じロックの中であきらめる
        # (書いている途中の 1 件を、written と dropped の一方だけに数える)
        with self._condition:
            if thread is None or thread.is_alive():
                # 書き終えられなかった分を捨てた件数に数え、書き出しスレッドにはこれ以上書かせない。
                # 行を足すと決めた後の 1 件は、追記を終えた書き出しスレッドが数える
                in_flight = 1 if self._writing and not self._appending else 0
                self._dropped += len(self._queue) + in_flight
                for item in self._queue:
                    self._release_reservation_locked(item)
                self._queue.clear()
                self._given_up = True
                self._condition.notify_all()

    @property
    def dropped(self) -> int:
        """捨てた保存の件数 (キューが満杯・close の後・書き込みの失敗・close で書き終えられなかった分)。"""
        with self._condition:
            return self._dropped

    @property
    def written(self) -> int:
        """画像を書いて frames.jsonl に行を足した件数。"""
        with self._condition:
            return self._written

    def _write_item(self, item: _SaveItem) -> bool:
        """1 件を書く (画像を置き換えの書き込みで書いてから、frames.jsonl に 1 行足す)。書けたら True。

        1. 画像を書く。失敗したら (OSError・ValueError) dropped に数え、surface の画像なら予約から外して False を返す。
           close があきらめた後の失敗は数えない (もう dropped に数えてある)
        2. close があきらめたかを確かめ、あきらめていなければ行を足すと決める (_appending)。この 2 つは、close が
           あきらめて dropped に足すのと同じロック (_condition) の中で行う。あきらめた後なら、行を足さず、数えずに
           False を返す (もう dropped に数えてある)
        3. ロックの外で行を足し、written に数えて True を返す。追記に失敗したら (OSError・ValueError) dropped に数え、
           surface の画像なら予約から外して False を返す。2 で行を足すと決めた 1 件は close が数えないので、
           close があきらめた後でも、ここで written か dropped に数える
        """
        try:
            record = self._write_image(item)
        except (OSError, ValueError):
            logger.warning("failed to save a %s frame", item.kind, exc_info=True)
            with self._condition:
                if not self._given_up:
                    self._dropped += 1
                    self._writing = False
                    self._release_reservation_locked(item)
            return False
        with self._condition:
            if self._given_up:
                return False
            self._appending = True
        try:
            append_json_line(self.paths.frames_log, frame_record_row(record))
        except (OSError, ValueError):
            logger.warning("failed to record a %s frame", item.kind, exc_info=True)
            with self._condition:
                self._dropped += 1
                self._appending = False
                self._writing = False
                self._release_reservation_locked(item)
            return False
        with self._condition:
            self._written += 1
            self._appending = False
            self._writing = False
        return True

    def _write_image(self, item: _SaveItem) -> FrameRecord:
        """item の画像を書き、frames.jsonl に足す FrameRecord を返す。"""
        if item.kind == "interval":
            if item.nv12 is None:
                raise ValueError("interval frame without NV12 data")
            image = encode_jpeg(
                shrink_to_width(nv12_to_bgr(item.nv12), self.interval_width),
                self.jpeg_quality,
            )
            path = self.paths.interval_frame(item.t)
        else:
            if item.image is None:
                raise ValueError("surface frame without image")
            image = item.image
            path = self.paths.surface_frame(item.t, item.number)
        write_bytes_atomic(path, image.jpeg)
        return FrameRecord(
            t=item.t,
            kind=item.kind,
            file=self.paths.relative(path),
            width=image.width,
            height=image.height,
        )

    def _release_reservation_locked(self, item: _SaveItem) -> None:
        """surface の画像なら、その t と内容の組の予約を外す (組の番号は残す)。呼び出し側が _condition を持つ。"""
        if item.kind == "surface":
            self._reserved_surface.discard((t_to_milliseconds(item.t), item.content))

    def _run_writer(self) -> None:
        """書き出しスレッドの本体。キューの分を古い順に書き、受け付けをやめてキューが空になったら終わる。"""
        while True:
            with self._condition:
                while not self._queue and self._accepting and not self._given_up:
                    self._condition.wait()
                if self._given_up or not self._queue:
                    return
                item = self._queue.popleft()
                self._writing = True
            self._write_item(item)
            with self._condition:
                self._writing = False
                if self._given_up:
                    return
