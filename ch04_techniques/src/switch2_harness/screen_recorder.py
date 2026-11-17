"""要素「停滞の知らせ」(stagnation-alert) のうち、run の間に裏で画面を撮り、縮小画像と時刻を記録する部分。

記録は records/screen-samples/run-<NNN>/ に、run ごとに分けて置く。run ごとのディレクトリーの中身:
    samples.jsonl  撮影ごとに 1 行の JSON。成功は {"index": <番号>, "time": <時刻>, "file": <縮小画像のファイル名>}、
                   失敗は {"time": <時刻>, "error": <error 行>}
    000001.png ... 撮影した画面を 160x90 のグレースケールに縮小した PNG (成功した撮影ごとに 1 から連番)
    shot.png       デーモンに元の大きさで保存させる一時ファイル (撮影のたびに上書きし、記録を止めたら消す)
記録は、画面が変わらない状態 (停滞) をどう見分けるかを、後で分析して決めるために使う。

commands.handle_run が recording_screens で裏のスレッドを動かし、撮影はエージェントの操作と並行に進む。
デーモンへの送信 (send) と時計 (clock) は引数で受け取る。
commands はこのモジュールを import するため、このモジュールは commands を import しない (循環 import を避ける)。
"""

import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from switch2_harness.daemon_client import SendCommand
from switch2_harness.workspace import WorkspacePaths

# 撮影を始めてから次の撮影を始めるまでの秒数
SAMPLE_INTERVAL_S = 0.5

# 裏の撮影でデーモンの応答を待つ上限の秒数
SHOT_TIMEOUT_S = 5.0

# 記録を止めるときに、裏の撮影のスレッドの終わりを待つ上限の秒数
STOP_JOIN_TIMEOUT_S = 10.0

# records/ の下の、記録を置くディレクトリーの名前
SAMPLES_DIR_NAME = "screen-samples"

# run ごとのディレクトリーの名前の前半 (後半は 3 桁以上の番号。例: run-001)
RUN_DIR_PREFIX = "run-"

# run ごとのディレクトリーの、撮影ごとの 1 行を書くファイル
INDEX_FILE_NAME = "samples.jsonl"

# デーモンに元の大きさで保存させる一時ファイルの名前 (毎回上書きし、記録を止めたら消す)
SHOT_FILE_NAME = "shot.png"

# 作業ディレクトリーを受け取り、記録を始めて終えるコンテキストマネージャーを返す関数の型。commands.handle_run が受け取る
ScreenRecording = Callable[[WorkspacePaths], AbstractContextManager[object]]


def next_run_dir(samples_dir: Path) -> Path:
    """今回の run の記録を置くディレクトリーを作って返す。

    samples_dir の直下にある、名前が RUN_DIR_PREFIX ("run-") と 3 桁以上の数字でできたディレクトリー
    (例: run-001、run-1234) の番号の最大値 + 1 (1 つも無ければ 1) を今回の番号にし、
    samples_dir / "run-<番号を 3 桁にゼロ埋めしたもの>" のディレクトリーを作って返す。
    samples_dir が無ければ、親のディレクトリーごと作る。
    例: samples_dir が空なら run-001。run-001 と run-007 があれば run-008。
    """
    raise NotImplementedError


def sample_file_name(index: int) -> str:
    """撮影の番号 index (1 から) の縮小画像のファイル名を返す。番号は 6 桁にゼロ埋めする。例: 1 は "000001.png"。"""
    raise NotImplementedError


def record_sample(
    run_dir: Path,
    index: int,
    *,
    send: SendCommand,
    clock: Callable[[], float],
) -> dict[str, object]:
    """画面を 1 回撮り、縮小画像を run_dir に書いて、samples.jsonl に書く 1 行の内容を返す。

    1. send(["shot", str(run_dir / SHOT_FILE_NAME)], timeout_s=SHOT_TIMEOUT_S) を送り、デーモンに元の大きさの画面を
       run_dir / SHOT_FILE_NAME に保存させる (デーモンが応答しなくても記録が止まったままにならないよう、待ちに上限を付ける)
    2. 応答を受け取った後に clock() で時刻を取る。これを撮影の時刻とする
    3. 応答が error で始まれば {"time": <時刻>, "error": <応答>} を返す (画像は書かない)。
       判定は reply.startswith("error") で行う (commands.is_error_reply は、commands がこのモジュールを import するため、
       使うと循環 import になる)
    4. screen_change.load_sample(run_dir / SHOT_FILE_NAME) で 160x90 のグレースケールに縮小して読む。
       ValueError (ファイルが無い、画像として読めない) なら {"time": <時刻>, "error": f"error {e}"} を返す
       (例: `error cannot read image: <パス>`)
    5. 縮小画像を cv2.imwrite(str(run_dir / sample_file_name(index)), <縮小画像>) で PNG に書き、
       {"index": index, "time": <時刻>, "file": sample_file_name(index)} を返す
    """
    raise NotImplementedError


def append_sample_line(index_file: Path, entry: Mapping[str, object]) -> None:
    """entry を json.dumps(entry, ensure_ascii=False) で 1 行の JSON にし、改行を付けて index_file に UTF-8 で追記する。"""
    raise NotImplementedError


def run_recording(
    run_dir: Path,
    stop: threading.Event,
    *,
    send: SendCommand,
    clock: Callable[[], float],
    interval_s: float = SAMPLE_INTERVAL_S,
) -> None:
    """stop が set されるまで、interval_s 秒ごとに画面を撮って run_dir に記録する。recording_screens が裏のスレッドで動かす。

    撮影の番号 index を 1 から始め、stop.is_set() になるまで次を繰り返す:
        1. started = clock()
        2. entry = record_sample(run_dir, index, send=send, clock=clock)
        3. append_sample_line(run_dir / INDEX_FILE_NAME, entry)
        4. entry に "index" があれば (撮影に成功したら) index を 1 進める。error の行では進めないので、画像の番号は飛ばない
        5. stop.wait(max(0.0, interval_s - (clock() - started))) で、撮影を始めてから interval_s 秒たつまで待つ
           (撮影に interval_s 秒以上かかったら待たない。待っている間に stop が set されたら、すぐに次の確認に進む)
    繰り返しを抜けたら、一時ファイル run_dir / SHOT_FILE_NAME を unlink(missing_ok=True) で消す。
    """
    raise NotImplementedError


@contextmanager
def recording_screens(
    paths: WorkspacePaths, *, send: SendCommand, clock: Callable[[], float]
) -> Iterator[Path]:
    """with の中にいる間、裏のスレッドで画面を撮って記録する。with ... as で今回の run の記録のディレクトリーを受け取れる。

    1. run_dir = next_run_dir(paths.records_dir / SAMPLES_DIR_NAME) で、今回の run のディレクトリーを作る
    2. stop = threading.Event() と、threading.Thread(target=run_recording, args=(run_dir, stop),
       kwargs={"send": send, "clock": clock}, daemon=True) を作って start する
       (daemon=True にして、スレッドが止まらないときも Python の終了を妨げないようにする)
    3. run_dir を yield する
    4. with を抜けるとき (例外で抜けるときも finally で)、stop.set() で止める合図をし、
       thread.join(timeout=STOP_JOIN_TIMEOUT_S) でスレッドの終わりを待つ (撮影の途中なら、その撮影が終わるまで待つ)

    commands.handle_run は、screen_recording を受け取らなかったときに、これを send と clock を渡して使う。
    """
    raise NotImplementedError
