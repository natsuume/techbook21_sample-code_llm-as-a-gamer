"""フレームの画像の保存 (frames/interval/・frames/surface/) と、保存した画像の一覧 frames.jsonl。

- 一定間隔の保存: キャプチャーのフレームを interval_fps のフレームレートで間引き、幅 interval_width に縮小して
  frames/interval/<tミリ秒9桁>.jpg に保存する
- surface の画像の保存: surface に送った JPEG を frames/surface/<tミリ秒9桁>.jpg に保存する。
  同じキャプチャー時刻 (t のミリ秒) のフレームを複数の要求で送った場合は 1 回だけ保存する
- 画像は置き換えの書き込み (file_io.write_bytes_atomic) で書き、画像を書き終えた後に frames.jsonl に
  {"t", "kind": "interval" | "surface", "file": 実行ディレクトリーからの相対パス, "width", "height"} の 1 行を足す

書き込み (縮小・JPEG 化・ファイルの書き込み) は専用の書き出しスレッドで行い、受け付けは上限付きのキューで行う。
キューが満杯のときは保存を捨て、捨てた件数を dropped に数える (終了時に run.jsonl の frames_dropped に記録する)。
受け付ける側 (キャプチャーのスレッドと surface のスレッド) は I/O をせず、書き出しスレッドを待たない。
"""

from dataclasses import dataclass
from typing import Literal

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY, EncodedImage
from switch2_layers.run_directory import RunPaths

DEFAULT_QUEUE_CAPACITY = 64  # キューに入れておける保存の件数の上限
# 一定間隔の保存で、前に保存の対象にしたフレームから 1 / interval_fps 秒のこの割合以上たったフレームを次の対象にする。
# キャプチャーのフレームの間隔の揺らぎで、保存の間隔が 1 フレーム分延びないようにするため
INTERVAL_JITTER_RATIO = 0.9
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


def frame_record_row(record: FrameRecord) -> dict[str, object]:
    """frames.jsonl の 1 行の辞書 {"t", "kind", "file", "width", "height"} を返す。"""
    raise NotImplementedError


def read_frame_records(paths: RunPaths) -> list[FrameRecord]:
    """frames.jsonl を読み (file_io.read_json_lines)、書かれた順の FrameRecord のリストを返す。

    ファイルが無ければ空のリスト。キーが足りない行と型が合わない行は読み飛ばす。
    """
    raise NotImplementedError


def latest_frame_t(paths: RunPaths) -> float | None:
    """frames.jsonl の行の t の最大値 (保存した最新のフレームの時刻) を返す。行が無ければ None。"""
    raise NotImplementedError


def is_interval_due(last_t: float | None, t: float, interval_fps: float) -> bool:
    """時刻 t のフレームが一定間隔の保存の対象かを返す。

    last_t (前に対象にしたフレームの t) が None なら True。そうでなければ
    t - last_t >= INTERVAL_JITTER_RATIO / interval_fps なら True。
    """
    raise NotImplementedError


class FrameStore:
    """フレームの画像を保存し、frames.jsonl に記録する。

    paths            実行ディレクトリー (frames/interval/ と frames/surface/ は作ってあるものとする)
    interval_fps     一定間隔の保存のフレームレート
    interval_width   一定間隔のフレームを縮小する幅 (image_encoding.shrink_to_width)
    capacity         キューに入れておける件数の上限 (書いている途中の 1 件は含めない)
    jpeg_quality     一定間隔のフレームの JPEG の品質
    start_thread     True (既定) なら書き出しスレッド (daemon) を起動する。False ならテストから write_queued を呼ぶ
    """

    def __init__(
        self,
        paths: RunPaths,
        interval_fps: float,
        interval_width: int,
        capacity: int = DEFAULT_QUEUE_CAPACITY,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        start_thread: bool = True,
    ) -> None:
        """空のキューで作る。"""
        raise NotImplementedError

    def offer_interval(self, frame: CapturedFrame) -> bool:
        """frame が一定間隔の保存の対象 (is_interval_due) なら、保存の分としてキューに入れ、入れたら True を返す。

        - 対象でなければ何もせず False
        - 対象なら、キューに入れられたかに関わらず、次の対象の判定の起点 (last_t) を frame.t にする
        - キューが満杯か、close の後なら入れずに dropped を 1 増やして False
        縮小・JPEG 化・書き込みはしない (書き出しスレッドで行う)。
        """
        raise NotImplementedError

    def save_surface(self, t: float, image: EncodedImage) -> str | None:
        """surface に送った時刻 t のフレームの画像 image を、保存の分としてキューに入れる。

        戻り値は保存先の実行ディレクトリーからの相対パス (frames/surface/<tミリ秒9桁>.jpg)。
        - 同じ t (ミリ秒) の画像を前にキューに入れていれば、入れずに同じ相対パスを返す (1 回だけ保存する)
        - キューが満杯か、close の後なら入れずに dropped を 1 増やして None を返す
          (その t は保存していないものとして扱い、次に同じ t が渡されたときにもう一度入れようとする)
        """
        raise NotImplementedError

    def write_queued(self) -> int:
        """キューにある分を古い順にすべて書き、書けた件数を返す (start_thread が False のときにテストから呼ぶ)。

        1 件ごとに、画像を置き換えの書き込みで書いてから、frames.jsonl に 1 行足す。
        書き込みに失敗した 1 件 (OSError・ValueError) は dropped に数え、次の件に進む。
        """
        raise NotImplementedError

    def close(self, timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S) -> None:
        """以後の受け付けをやめ、書き出しスレッドがキューの分を書き終えるまで最大 timeout_s 秒待つ。

        書き終えられなかった分は dropped に足す。書き出しスレッドが無ければ、キューに残った分を dropped に足す。
        2 回目以降の呼び出しは何もしない。
        """
        raise NotImplementedError

    @property
    def dropped(self) -> int:
        """捨てた保存の件数 (キューが満杯・close の後・書き込みの失敗・close で書き終えられなかった分)。"""
        raise NotImplementedError

    @property
    def written(self) -> int:
        """画像を書いて frames.jsonl に行を足した件数。"""
        raise NotImplementedError
