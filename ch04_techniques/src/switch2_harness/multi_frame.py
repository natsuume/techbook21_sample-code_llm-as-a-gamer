"""要素「複数フレーム」(multi-frame)。look のときに画面を続けて撮り、縮小して 1 枚に並べる。

look は SHOT_INTERVAL_S 秒おきに SHOT_COUNT 回画面を撮る。保存先は shot_paths で決め、
1〜5 回目は作業ディレクトリーの multi-frames/ に置く一時ファイル、6 回目は複数フレームを使わない look と同じ
frames/NNNNNN.png にする。
撮った 6 枚は write_tile で TILE_WIDTH x TILE_HEIGHT に縮め、TILE_COLUMNS 列 × TILE_ROWS 段の 1 枚に並べて
multi-frames/NNNNNN.png (tile_path_for) に書く。look はそのパスを multi_frame_line の行で表示する。

デーモンへの送信 (send)・時計 (clock)・待機 (sleep) は引数で受け取る。
commands はこのモジュールを import するため、このモジュールは commands を import しない (循環 import を避ける)。
"""

from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from switch2_harness.daemon_client import SendCommand

# look 1 回で画面を撮る回数
SHOT_COUNT = 6

# 撮影を始めてから次の撮影を始めるまでの秒数
SHOT_INTERVAL_S = 0.2

# 並べるときに縮める、1 コマの幅 (画素)
TILE_WIDTH = 640

# 並べるときに縮める、1 コマの高さ (画素)
TILE_HEIGHT = 360

# 並べた画像の列の数 (横に並べるコマの数)
TILE_COLUMNS = 3

# 並べた画像の段の数 (縦に並べるコマの数)
TILE_ROWS = 2

# 作業ディレクトリーの直下の、1〜5 回目の一時ファイルと並べた画像を置くディレクトリーの名前
MULTI_FRAMES_DIR_NAME = "multi-frames"

# look が表示する、並べた画像のパスの行の前半 (後半は並べた画像の絶対パス)
LINE_PREFIX = "複数フレーム: "


def multi_frame_line(tile_path: Path) -> str:
    """look が表示する、並べた画像のパスの行を返す。

    例: tile_path が /w/multi-frames/000012.png なら "複数フレーム: /w/multi-frames/000012.png"。
    """
    return f"{LINE_PREFIX}{tile_path}"


def shot_paths(frame_path: Path, multi_frames_dir: Path) -> tuple[Path, ...]:
    """SHOT_COUNT 回の撮影の保存先を、撮る順に返す。

    1〜5 回目は multi_frames_dir / f"{frame_path.stem}-{i}.png" (i は 1〜5)、6 回目は frame_path。
    例: frame_path が frames/000012.png なら
    multi-frames/000012-1.png、…、multi-frames/000012-5.png、frames/000012.png の 6 つ。
    """
    temporary = tuple(
        multi_frames_dir / f"{frame_path.stem}-{i}.png" for i in range(1, SHOT_COUNT)
    )
    return (*temporary, frame_path)


def tile_path_for(frame_path: Path, multi_frames_dir: Path) -> Path:
    """並べた画像の保存先 multi_frames_dir / frame_path.name を返す。

    例: frame_path が frames/000012.png なら multi-frames/000012.png。
    """
    return multi_frames_dir / frame_path.name


def shoot_burst(
    shot_paths: Sequence[Path],
    *,
    send: SendCommand,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    interval_s: float = SHOT_INTERVAL_S,
) -> str:
    """shot_paths の順に画面を撮り、最後に受け取った応答を返す。

    各パスについて、次をこの順に行う:
        1. started = clock()
        2. reply = send(["shot", str(<パス>)]) を送る (timeout_s は渡さない。look の送信には待ち時間の上限を付けない)
        3. reply が "error" で始まれば、残りのパスは送らずに reply を返す。
           判定は reply.startswith("error") で行う (commands.is_error_reply は、commands がこのモジュールを
           import するため、使うと循環 import になる)
        4. 最後のパスでなければ、wait = interval_s - (clock() - started) を求め、wait > 0 のときだけ sleep(wait) を呼ぶ
           (撮影を始めてから interval_s 秒たつまで待つ。撮影に interval_s 秒以上かかったら待たない)。
           最後のパスの後は待たない
    すべて成功したら、最後のパスの応答を返す。
    例: 送信に 0.05 秒かかるなら、6 つのパスで send を 6 回、sleep(0.15) を 5 回呼び、各送信の始まりは 0.2 秒ずつずれる。
    """
    raise NotImplementedError


def tile_frames(images: Sequence[np.ndarray]) -> np.ndarray:
    """SHOT_COUNT 枚の BGR 画像を縮めて並べた、形が (720, 1920, 3) の画像を返す。

    images は撮った順の 6 枚 (uint8 の BGR 画像。大きさは問わない) を渡す。
    1. 各画像を cv2.resize(<画像>, (TILE_WIDTH, TILE_HEIGHT), interpolation=cv2.INTER_AREA) で 640x360 に縮める
    2. k 枚目 (1 から数える) を、段 (k - 1) // TILE_COLUMNS、列 (k - 1) % TILE_COLUMNS の位置に置く。
       上の段の左から 1・2・3、下の段の左から 4・5・6 の順になる
    3. 各コマの左上に、番号 k の数字を cv2.putText で描く。縁取りして (太い線で描いた上に別の色で描くなど)、
       コマの背景の色によらず読めるようにする
    """
    raise NotImplementedError


def write_tile(image_paths: Sequence[Path], tile_path: Path) -> None:
    """image_paths の画像を読み、tile_frames で並べて tile_path に PNG で書く。

    image_paths は撮った順の 6 つのパス (shot_paths の戻り値) を渡す。
    各画像を cv2.imread(str(<パス>)) で BGR 画像として読み、tile_frames(<読んだ画像の列>) の結果を
    cv2.imwrite(str(tile_path), <並べた画像>) で書く。tile_path の親ディレクトリーはあるものとして呼ぶ。
    """
    raise NotImplementedError
