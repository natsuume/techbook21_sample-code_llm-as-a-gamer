"""要素「停滞の知らせ」(stagnation-alert) のうち、run の間に裏で画面を撮って記録し、撮影ごとに新しい画面かを判定する部分。

記録は records/screen-samples/run-<NNN>/ に、run ごとに分けて置く。run ごとのディレクトリーの中身:
    samples.jsonl  撮影ごとに 1 行の JSON。成功は {"index": <番号>, "time": <時刻>, "file": <縮小画像のファイル名>}、
                   失敗は {"time": <時刻>, "error": <error 行>}
    000001.png ... 撮影した画面を 160x90 のグレースケールに縮小した PNG (成功した撮影ごとに 1 から連番)
    last-new-screen.json
                   最後の新しい画面の {"index": <番号>, "time": <時刻>, "file": <縮小画像のファイル名>}
                   (stagnation_alert.LAST_NEW_SCREEN_FILE_NAME。新しい画面が出るたびに書き換える)
    shot.png       デーモンに元の大きさで保存させる一時ファイル (撮影のたびに上書きし、記録を止めたら消す)
撮影に成功するたびに、stagnation_alert.NewScreenTracker でその run の撮影と比べて新しい画面かを判定し、
新しい画面なら状態ファイル last-new-screen.json を書く。look は read_latest_last_new_screen_time で
番号が最大の run の状態ファイルを読み、停滞の知らせを出すかを決める。

commands.handle_run が recording_screens で裏のスレッドを動かし、撮影はエージェントの操作と並行に進む。
デーモンへの送信 (send) と時計 (clock) は引数で受け取る。
commands はこのモジュールを import するため、このモジュールは commands を import しない (循環 import を避ける)。
"""

import json
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import cast

import cv2

from switch2_harness.daemon_client import SendCommand
from switch2_harness.screen_change import load_sample
from switch2_harness.stagnation_alert import (
    NewScreenTracker,
    read_last_new_screen_time,
)
from switch2_harness.workspace import WorkspacePaths

# 撮影を始めてから次の撮影を始めるまでの秒数
SAMPLE_INTERVAL_S = 0.1

# 裏の撮影でデーモンの応答を待つ上限の秒数
SHOT_TIMEOUT_S = 5.0

# 記録を止めるときに、裏の撮影のスレッドの終わりを待つ上限の秒数
STOP_JOIN_TIMEOUT_S = 10.0

# records/ の下の、記録を置くディレクトリーの名前
SAMPLES_DIR_NAME = "screen-samples"

# run ごとのディレクトリーの名前の前半 (後半は 3 桁以上の番号。例: run-001)
RUN_DIR_PREFIX = "run-"

# run ごとのディレクトリーの名前に合う正規表現。グループ 1 が番号
_RUN_DIR_PATTERN = re.compile(re.escape(RUN_DIR_PREFIX) + r"([0-9]{3,})")

# run ごとのディレクトリーの、撮影ごとの 1 行を書くファイル
INDEX_FILE_NAME = "samples.jsonl"

# デーモンに元の大きさで保存させる一時ファイルの名前 (毎回上書きし、記録を止めたら消す)
SHOT_FILE_NAME = "shot.png"

# 作業ディレクトリーを受け取り、記録を始めて終えるコンテキストマネージャーを返す関数の型。commands.handle_run が受け取る
ScreenRecording = Callable[[WorkspacePaths], AbstractContextManager[object]]


def _numbered_run_dirs(samples_dir: Path) -> list[tuple[int, Path]]:
    """samples_dir の直下の run のディレクトリーを、(番号, パス) の組のリストで返す (順は決めない)。

    名前が RUN_DIR_PREFIX ("run-") と 3 桁以上の数字でできたディレクトリーだけを返す (例: run-001、run-1234)。
    名前の規則に合わないものと、ディレクトリーでないものは除く。samples_dir はあるものとして呼ぶ。
    """
    return [
        (int(match.group(1)), entry)
        for entry in samples_dir.iterdir()
        if entry.is_dir() and (match := _RUN_DIR_PATTERN.fullmatch(entry.name))
    ]


def next_run_dir(samples_dir: Path) -> Path:
    """今回の run の記録を置くディレクトリーを作って返す。

    samples_dir の直下にある、名前が RUN_DIR_PREFIX ("run-") と 3 桁以上の数字でできたディレクトリー
    (例: run-001、run-1234) の番号の最大値 + 1 (1 つも無ければ 1) を今回の番号にし、
    samples_dir / "run-<番号を 3 桁にゼロ埋めしたもの>" のディレクトリーを作って返す。
    samples_dir が無ければ、親のディレクトリーごと作る。
    例: samples_dir が空なら run-001。run-001 と run-007 があれば run-008。
    """
    samples_dir.mkdir(parents=True, exist_ok=True)
    numbers = [number for number, _ in _numbered_run_dirs(samples_dir)]
    run_dir = samples_dir / f"{RUN_DIR_PREFIX}{max(numbers, default=0) + 1:03d}"
    run_dir.mkdir()
    return run_dir


def latest_run_dir(samples_dir: Path) -> Path | None:
    """samples_dir の直下の、番号が最大の run のディレクトリーを返す。ディレクトリーは作らない。

    next_run_dir と同じ名前の規則 (RUN_DIR_PREFIX ("run-") と 3 桁以上の数字) のディレクトリーのうち、
    番号が最大のものを返す。samples_dir が無いとき、または規則に合うディレクトリーが無いときは None。
    例: run-001 と run-007 があれば samples_dir / "run-007"。
    """
    if not samples_dir.is_dir():
        return None
    run_dirs = _numbered_run_dirs(samples_dir)
    if not run_dirs:
        return None
    _, largest = max(run_dirs, key=lambda numbered: numbered[0])
    return largest


def read_latest_last_new_screen_time(paths: WorkspacePaths) -> float | None:
    """作業ディレクトリーの最新の run の、最後の新しい画面の時刻を返す。look の停滞の知らせが使う。

    latest_run_dir(paths.records_dir / SAMPLES_DIR_NAME) が None なら None。
    そうでなければ stagnation_alert.read_last_new_screen_time(<そのディレクトリー>) の結果を返す
    (状態ファイルがまだ無いときなどは None)。
    """
    run_dir = latest_run_dir(paths.records_dir / SAMPLES_DIR_NAME)
    if run_dir is None:
        return None
    return read_last_new_screen_time(run_dir)


def sample_file_name(index: int) -> str:
    """撮影の番号 index (1 から) の縮小画像のファイル名を返す。番号は 6 桁にゼロ埋めする。例: 1 は "000001.png"。"""
    return f"{index:06d}.png"


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
    shot_path = run_dir / SHOT_FILE_NAME
    reply = send(["shot", str(shot_path)], timeout_s=SHOT_TIMEOUT_S)
    shot_time = clock()
    if reply.startswith("error"):
        return {"time": shot_time, "error": reply}
    try:
        sample = load_sample(shot_path)
    except ValueError as e:
        return {"time": shot_time, "error": f"error {e}"}
    file_name = sample_file_name(index)
    cv2.imwrite(str(run_dir / file_name), sample)
    return {"index": index, "time": shot_time, "file": file_name}


def append_sample_line(index_file: Path, entry: Mapping[str, object]) -> None:
    """entry を json.dumps(entry, ensure_ascii=False) で 1 行の JSON にし、改行を付けて index_file に UTF-8 で追記する。"""
    with open(index_file, "a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_recording(
    run_dir: Path,
    stop: threading.Event,
    *,
    send: SendCommand,
    clock: Callable[[], float],
    interval_s: float = SAMPLE_INTERVAL_S,
) -> None:
    """stop が set されるまで、interval_s 秒ごとに画面を撮って run_dir に記録し、撮影ごとに新しい画面かを判定する。

    recording_screens が裏のスレッドで動かす。
    最初に tracker = stagnation_alert.NewScreenTracker(run_dir) を作る。
    撮影の番号 index を 1 から始め、stop.is_set() になるまで次を繰り返す:
        1. started = clock()
        2. entry = record_sample(run_dir, index, send=send, clock=clock)
        3. append_sample_line(run_dir / INDEX_FILE_NAME, entry)
        4. entry に "index" があれば (撮影に成功したら)、tracker.add_sample(sample_file_name(index), index, entry["time"])
           で新しい画面かを判定し (新しい画面なら状態ファイルを書く)、index を 1 進める。
           entry["time"] は record_sample が clock() で取った撮影の時刻。
           error の行では判定せず、index も進めないので、画像の番号は飛ばない
        5. stop.wait(min(interval_s, max(0.0, interval_s - (clock() - started)))) で、撮影を始めてから interval_s 秒たつまで待つ
           (撮影に interval_s 秒以上かかったら待たない。待っている間に stop が set されたら、すぐに次の確認に進む)。
           時計 (本番は壁時計) が戻って経過時間が負になっても、待ちは interval_s を超えない
    繰り返しを抜けたら、一時ファイル run_dir / SHOT_FILE_NAME を unlink(missing_ok=True) で消す。
    """
    tracker = NewScreenTracker(run_dir)
    index = 1
    while not stop.is_set():
        started = clock()
        entry = record_sample(run_dir, index, send=send, clock=clock)
        append_sample_line(run_dir / INDEX_FILE_NAME, entry)
        if "index" in entry:
            # 成功した撮影の entry の "time" は、record_sample が clock() で取った float
            shot_time = cast(float, entry["time"])
            tracker.add_sample(sample_file_name(index), index, shot_time)
            index += 1
        stop.wait(min(interval_s, max(0.0, interval_s - (clock() - started))))
    (run_dir / SHOT_FILE_NAME).unlink(missing_ok=True)


@contextmanager
def recording_screens(
    paths: WorkspacePaths, *, send: SendCommand, clock: Callable[[], float]
) -> Iterator[Path]:
    """with の中にいる間、裏のスレッドで画面を撮って記録し、撮影ごとに新しい画面かを判定して状態ファイルを書く。

    with ... as で今回の run の記録のディレクトリーを受け取れる。

    1. run_dir = next_run_dir(paths.records_dir / SAMPLES_DIR_NAME) で、今回の run のディレクトリーを作る
    2. stop = threading.Event() と、threading.Thread(target=run_recording, args=(run_dir, stop),
       kwargs={"send": send, "clock": clock}, daemon=True) を作って start する
       (daemon=True にして、スレッドが止まらないときも Python の終了を妨げないようにする)
    3. run_dir を yield する
    4. with を抜けるとき (例外で抜けるときも finally で)、stop.set() で止める合図をし、
       thread.join(timeout=STOP_JOIN_TIMEOUT_S) でスレッドの終わりを待つ (撮影の途中なら、その撮影が終わるまで待つ)

    commands.handle_run は、screen_recording を受け取らなかったときに、これを send と clock を渡して使う。
    """
    run_dir = next_run_dir(paths.records_dir / SAMPLES_DIR_NAME)
    stop = threading.Event()
    thread = threading.Thread(
        target=run_recording,
        args=(run_dir, stop),
        kwargs={"send": send, "clock": clock},
        daemon=True,
    )
    thread.start()
    try:
        yield run_dir
    finally:
        stop.set()
        thread.join(timeout=STOP_JOIN_TIMEOUT_S)
