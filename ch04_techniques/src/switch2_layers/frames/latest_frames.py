"""一定間隔のフレームの最新の LATEST_FRAME_COUNT 枚を、メモリーと frames/latest/<n>.jpg に置き換え続ける (LatestFrames)。

Orchestrator 型の run だけで使う (advisor 型では作らず、frames/latest/ を書かない)。
    画像 (make_latest_frame)   一定間隔のフレームを縮小した画像 (frames/interval/ と同じ幅) の左上に、時刻を
                               frames.surface_frames.draw_frame_time で書き込み (t=12.345 の形)、JPEG にする
    足す (add_image・add)      frames.frame_store.FrameStore の書き出しスレッドが、一定間隔のフレームの画像を
                               frames/interval/ に書いて frames.jsonl に行を足した後に、縮小した同じ画像を add_image に
                               渡す (make_latest_frame で画像を作り、add で足す)
    メモリー                   新しい順に最大 LATEST_FRAME_COUNT 枚を持つ。超えたら、最も古いものを外す
    ファイル                   add のたびに、メモリーの n 番目 (1 始まり。1 が最新) の画像で frames/latest/<n>.jpg を
                               1.jpg から順に書き直す。1 つの番号の書き直しは、一時ファイルに書いてから os.replace で
                               置き換える (file_io.write_bytes_atomic) ため、主ループがいつ Read しても書きかけの画像は
                               読まない。番号を rename で送らないので、どの番号のファイルも消えている瞬間が無く、1.jpg から
                               書き直すので、同じ画像が 2 つの番号に並ぶ瞬間も無い。まだ LATEST_FRAME_COUNT 枚に満たない
                               間は、ある分の番号だけを書く (無い番号のファイルは作らない)
    受け取る (snapshot)        主ループは、入力を作るときに snapshot を 1 回呼び、同じ時点の最新の画像をまとめて受け取る
                               (ファイルを 1 枚ずつ読まない)
add_image と add は書き出しスレッドから、snapshot は主ループのスレッドから呼ぶ。メモリーの読み書きは 1 つのロックの中で
行い、ファイルの書き込み (I/O) はロックの外で行う (snapshot はファイルの書き込みを待たない)。
"""

from dataclasses import dataclass

import numpy as np

from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY
from switch2_layers.run_directory import RunPaths

# frames/latest/ に置く画像の枚数 (1.jpg〜5.jpg)
LATEST_FRAME_COUNT = 5


@dataclass(frozen=True)
class LatestFrame:
    """最新の一定間隔のフレームの画像 1 枚。

    t             フレームの時刻 (実行の経過秒)
    source_file   元のフレームの画像の、実行ディレクトリーからの相対パス (frames/interval/<tミリ秒9桁>.jpg。
                  frames.jsonl の file)
    jpeg          時刻を書き込んだ画像の JPEG (make_latest_frame の値。幅は元のフレームの画像と同じ)
    """

    t: float
    source_file: str
    jpeg: bytes


def make_latest_frame(
    t: float,
    source_file: str,
    image: np.ndarray,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> LatestFrame:
    """縮小した一定間隔のフレームの画像 image (BGR) から、frames/latest/ に置く LatestFrame を作る (I/O なし)。

    jpeg は image_encoding.encode_jpeg(surface_frames.draw_frame_time(image, t), jpeg_quality).jpeg (左上に
    format_frame_label(t) を書き込んだ画像。image 自体は変えない)。t と source_file はそのまま持つ。
    """
    raise NotImplementedError


class LatestFrames:
    """一定間隔のフレームの最新の LATEST_FRAME_COUNT 枚を持ち、frames/latest/<n>.jpg に置き換え続ける
    (モジュールの docstring)。

    paths   実行ディレクトリー。frames/latest/ (RunPaths.latest_frames_dir) は、最初の add で親を含めて作る
    """

    def __init__(self, paths: RunPaths) -> None:
        """画像を持たない状態で作る。ファイルもディレクトリーも作らない。"""
        self.paths = paths

    def add_image(
        self,
        t: float,
        source_file: str,
        image: np.ndarray,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    ) -> None:
        """縮小した一定間隔のフレームの画像 image (BGR) を、add(make_latest_frame(t, source_file, image, jpeg_quality))
        で足す (frames.frame_store.FrameStore が呼ぶ)。"""
        raise NotImplementedError

    def add(self, frame: LatestFrame) -> None:
        """frame を最新として足し (LATEST_FRAME_COUNT 枚を超えたら最も古いものを外す)、frames/latest/ を書き直す
        (モジュールの docstring の「ファイル」)。

        frames/latest/ が無ければ親を含めて作る。書き込みの失敗 (OSError) は外に出さず、logging で警告を書く
        (一定間隔のフレームの保存と frames.jsonl の記録を止めないため)。書き込みに失敗しても、メモリーの画像は足したままにする。
        """
        raise NotImplementedError

    def snapshot(self) -> tuple[LatestFrame, ...]:
        """今持っている画像を、新しい順のタプルで返す (n 番目が frames/latest/<n>.jpg と同じ画像)。無ければ空のタプル。

        同じ時点の画像をまとめて返す (ロックの中で写す)。ファイルは読まない。
        """
        # 未実装: 画像を持たないものとして空のタプルを返す
        return ()
