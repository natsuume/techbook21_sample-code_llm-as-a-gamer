"""要素「停滞の知らせ」(stagnation-alert) のうち、新しい画面の判定と、look に出す知らせの組み立て。

新しい画面:
    run の間に裏で撮った撮影 (160x90 のグレースケールの縮小画像。screen_recorder) のうち、その run でそれまでに撮った
    どの撮影とも、screen_change.change_ratio が screen_change.STATIC_RATIO (1%) 以上のもの。run の最初の撮影も新しい画面とする。
    位置合わせはしない。比べるのはその run の撮影だけで、前の run の撮影とは比べない。
候補の絞り方:
    すべての撮影と比べると撮影の数に比例して遅くなるため、撮影ごとに強くぼかした小さな画像 (特徴。screen_feature) を作り、
    特徴の差が小さい順に CANDIDATE_NEAREST_COUNT 枚と、直前の CANDIDATE_RECENT_COUNT 枚を候補にして (candidate_positions)、
    候補とだけ 160x90 で比べる (is_new_screen)。記録した 3 つの run (最大 18838 枚) で、この絞り方の判定が
    すべての撮影と比べる判定と一致することを確かめてある。
状態ファイル:
    新しい画面が出るたびに、run のディレクトリー (records/screen-samples/run-<NNN>/) の LAST_NEW_SCREEN_FILE_NAME に
    {"index": <撮影の番号>, "time": <撮影の時刻>, "file": <縮小画像のファイル名>} を書く (write_last_new_screen)。
    look は別のプロセスでこれを読む (read_last_new_screen_time)。
知らせの条件:
    look の時刻が、最後の新しい画面の時刻から STAGNATION_SECONDS 秒以上たっていたら (alert_seconds)、新しい画面が出るまで、
    look のたびに知らせの行 (alert_line) を出し、records/events.jsonl の look の行の直後に、command が ALERT_COMMAND の
    イベント (build_alert_event) を足す。

screen_recorder がこのモジュールを import するため、このモジュールは screen_recorder と commands を import しない
(循環 import を避ける)。
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from switch2_harness.event_log import CommandEvent

# 最後の新しい画面からこの秒数以上たったら知らせる
STAGNATION_SECONDS = 120.0

# 特徴の差が小さい順に候補にする過去の撮影の数
CANDIDATE_NEAREST_COUNT = 20

# 候補に必ず入れる直前の撮影の数
CANDIDATE_RECENT_COUNT = 5

# 特徴を作るときのガウスぼかしの sigma (画素)
FEATURE_BLUR_SIGMA = 4.0

# 特徴の大きさ (幅, 高さ)
FEATURE_SIZE: tuple[int, int] = (20, 12)

# run のディレクトリーに置く状態ファイル
LAST_NEW_SCREEN_FILE_NAME = "last-new-screen.json"

# 知らせを記録するイベントの command
ALERT_COMMAND = "stagnation-alert"


def screen_feature(sample: np.ndarray) -> np.ndarray:
    """縮小画像 sample (160x90 の uint8 の 2 次元配列) から、候補を絞るための特徴を作って返す。

    cv2.GaussianBlur(sample.astype(np.float32), (0, 0), sigmaX=FEATURE_BLUR_SIGMA) でぼかした後、
    cv2.resize(..., FEATURE_SIZE, interpolation=cv2.INTER_AREA) で縮小し、.ravel() した
    1 次元の float32 の配列 (長さ 20 * 12 = 240) を返す。
    """
    raise NotImplementedError


def candidate_positions(features: np.ndarray, feature: np.ndarray) -> np.ndarray:
    """今の撮影の特徴 feature と比べる候補の、過去の撮影の位置 (0 始まりの行番号) を、昇順・重複なしの整数の配列で返す。

    features は過去の撮影の特徴を行に並べた (n, 240) の float32 の配列。n が 0 なら空の整数の配列を返す。
    1. distance = np.abs(features - feature).mean(axis=1) で、行ごとの特徴の差を求める
    2. n > CANDIDATE_NEAREST_COUNT なら np.argpartition(distance, CANDIDATE_NEAREST_COUNT)[:CANDIDATE_NEAREST_COUNT]
       (特徴の差が小さい CANDIDATE_NEAREST_COUNT 行)、そうでなければ np.arange(n) (すべての行) を候補にする
    3. 直前の撮影 np.arange(max(0, n - CANDIDATE_RECENT_COUNT), n) を足し、np.unique で昇順・重複なしにする
    """
    raise NotImplementedError


def is_new_screen(sample: np.ndarray, candidates: Sequence[np.ndarray]) -> bool:
    """縮小画像 sample が、候補の縮小画像 candidates のどれとも違う (新しい画面) なら True を返す。

    candidates が空なら True (run の最初の撮影)。そうでなければ、すべての候補 c で
    not screen_change.is_static(screen_change.change_ratio(c, sample)) (変化の割合が 1% 以上) なら True、
    1 つでも 1% 未満の候補があれば False。
    例: 160x90 の画像で、変わった画素が 144 (= 160 * 90 の 1%) なら新しい画面、143 なら新しい画面でない。
    """
    raise NotImplementedError


def alert_seconds(now: float, last_new_time: float) -> int | None:
    """時刻 now が、最後の新しい画面の時刻 last_new_time から STAGNATION_SECONDS 秒以上たっていれば、その秒数を返す。

    elapsed = now - last_new_time が STAGNATION_SECONDS 以上なら int(elapsed) (小数点以下を切り捨てた秒数)、
    そうでなければ (負のときも) None。
    例: 135.7 秒たっていれば 135。119.9 秒なら None。
    """
    raise NotImplementedError


def alert_line(seconds: int) -> str:
    """look に出す知らせの行を返す。例: 135 なら `停滞の知らせ: 新しい画面が 135 秒出ていません`。"""
    raise NotImplementedError


def build_alert_event(look_time: float, frame: str, seconds: int) -> CommandEvent:
    """知らせを出したことの記録を返す。records/events.jsonl の、知らせを出した look の行の直後に足す。

    CommandEvent(time=look_time, command=ALERT_COMMAND, args=(str(seconds),), reply=alert_line(seconds), ok=True,
    elapsed_s=0.0, frame=frame) を返す。look_time と frame は、知らせを出した look のイベントの time と frame。
    """
    raise NotImplementedError


def write_last_new_screen(run_dir: Path, index: int, time: float, file: str) -> None:
    """新しい画面の撮影 (番号 index、時刻 time、縮小画像のファイル名 file) を、run_dir の状態ファイルに書く。

    {"index": index, "time": time, "file": file} を json.dumps(..., ensure_ascii=False) で
    run_dir / (LAST_NEW_SCREEN_FILE_NAME + ".tmp") に UTF-8 で書き、os.replace で run_dir / LAST_NEW_SCREEN_FILE_NAME に
    置き換える (look は別のプロセスで同時に読むので、書きかけのファイルを読ませない)。
    """
    raise NotImplementedError


def read_last_new_screen_time(run_dir: Path) -> float | None:
    """run_dir の状態ファイル LAST_NEW_SCREEN_FILE_NAME を読み、最後の新しい画面の時刻 (time) を float で返す。

    次のどれかなら None を返す: ファイルが無い (FileNotFoundError)、JSON として読めない (ValueError)、
    JSON のオブジェクトでない、time が数値 (int か float。bool は除く) でない。
    """
    raise NotImplementedError


class NewScreenTracker:
    """run の間の撮影を順に受け取って新しい画面かを判定し、新しい画面なら状態ファイルを書く。

    screen_recorder.run_recording が裏のスレッドで作り、撮影に成功するたびに add_sample を呼ぶ。
    過去の撮影は、特徴 (screen_feature) とファイル名だけをメモリーに持ち、縮小画像は候補になったときにディスクから読む
    (縮小画像をすべてメモリーに持つと、2 時間の run で約 1 GB になるため)。
    """

    def __init__(self, run_dir: Path) -> None:
        """今回の run の記録のディレクトリー run_dir の撮影を判定する、過去の撮影が無い状態で作る。

        run_dir は self.run_dir に持つ。
        過去の撮影の特徴は (容量, 240) の float32 の配列に行で持ち、最初の容量は 1024 行にする。
        撮影の数が容量に達したら配列を 2 倍の容量に広げる (撮影ごとに配列全体を作り直さない)。
        過去の撮影のファイル名は、特徴と同じ順に list[str] で持つ。
        """
        raise NotImplementedError

    def add_sample(self, file_name: str, index: int, time: float) -> bool:
        """run_dir の縮小画像 file_name (撮影の番号 index、時刻 time) を受け取り、新しい画面なら True を返す。

        1. sample = screen_change.load_gray(self.run_dir / file_name) で縮小画像を読む
        2. feature = screen_feature(sample)
        3. positions = candidate_positions(<これまでの特徴 (行数はこれまでの撮影の数)>, feature)
        4. 候補の位置のファイル名の縮小画像を、screen_change.load_gray(self.run_dir / <そのファイル名>) でディスクから読む
        5. new = is_new_screen(sample, <候補の縮小画像>)
        6. feature と file_name を、過去の撮影に足す
        7. new なら write_last_new_screen(self.run_dir, index, time, file_name) で状態ファイルを書く
        8. new を返す
        """
        raise NotImplementedError
