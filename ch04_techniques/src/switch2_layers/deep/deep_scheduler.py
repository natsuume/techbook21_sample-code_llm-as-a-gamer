"""deep のジョブの起動の判断と実行 (DeepScheduler)、deep-once (run_deep_once)。

ジョブは文書 (種類と場面の組。DocumentKey) を単位に扱う。
- 同じ文書のジョブが実行中でも、新しいジョブを並行して動かす
- 同じ文書のジョブは、前回の起動から min_start_interval_s 秒たつまで起動しない。間隔内に来た要求は捨て、
  run.jsonl に deep_skipped を書く
- controls と tips の対象は、要求した側が渡した現在の場面の文書 (場面が無ければ共通の文書)
- 自動の呼び出し (check_auto) は、種類ごとに現在の対象の文書について判定し、次の 2 つを満たしたら、きっかけ auto で起動する
    - その文書のジョブが実行中でない
    - その文書の前回のジョブの終了 (まだ無ければ、その文書が対象になった時刻) から auto_seconds 秒たった
- ジョブを始めるときに deep/<種類>-<連番3桁>/ と job.json・work/ を用意し (deep_job_directory)、
  別スレッドで DeepRunner.run を呼ぶ。開始と終了を run.jsonl に deep_start・deep_end で書く
- ジョブを止める手順は、タイムアウト (起動から timeout_s 秒) でも終了時 (close) でも同じ:
  DeepRunner.stop を呼び (run.jsonl に deep_stop)、stop_grace_s 秒 (既定 5 秒) たっても run が戻らなければ
  DeepRunner.kill を呼ぶ (deep_end の status は killed)。stop の後に run が戻れば kill は呼ばない
時計 (RunClock)・スレッドの起動 (start_job_thread)・終了時の待ち (sleep) を注入でき、テストでは時間を進めて
request・check_auto・check_timeouts・close を直接呼ぶ。
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from switch2_layers.deep.deep_types import (
    DeepJob,
    DeepRequestResult,
    DeepRunner,
    DeepTrigger,
    DocumentKey,
    DocumentKind,
)
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

# タイムアウトと自動の呼び出しを判定する間隔 (秒)
CHECK_INTERVAL_S = 0.5
# stop を呼んでから、run が戻らなければ kill を呼ぶまでの秒数
STOP_GRACE_S = 5.0
# close が、止めたジョブの run が戻ったかを確かめる間隔 (秒)
CLOSE_POLL_INTERVAL_S = 0.05
# deep_skipped の reason: 同じ文書の前回の起動から min_start_interval_s 秒たっていない
SKIP_MIN_START_INTERVAL = "min_start_interval"
# deep_skipped の reason: close の後に来た要求
SKIP_CLOSED = "closed"


class DeepStopReason(StrEnum):
    """ジョブを止めた理由。run.jsonl の deep_stop の reason と deep_end の stop_reason。"""

    TIMEOUT = "timeout"  # 起動から timeout_s 秒たった
    CLOSE = "close"  # 終了処理 (DeepScheduler.close) で実行中だった


class DeepJobStatus(StrEnum):
    """ジョブの終わり方。run.jsonl の deep_end の status。"""

    OK = "ok"  # DeepRunner.run が ok=True の結果を返した
    FAILED = "failed"  # DeepRunner.run が ok=False で stopped=False の結果を返した
    ERROR = "error"  # DeepRunner.run が例外を出した
    STOPPED = "stopped"  # DeepRunner.run が stopped=True の結果を返した (stop で止められて戻った)
    KILLED = "killed"  # stop の後 stop_grace_s 秒たっても run が戻らず、kill を呼んだ


@dataclass(frozen=True)
class DeepJobOutcome:
    """1 回のジョブの終わり方。

    status    終わり方
    message   DeepResult.message、または例外の説明
    """

    status: DeepJobStatus
    message: str


def execute_deep_job(runner: DeepRunner, job: DeepJob, job_dir: Path) -> DeepJobOutcome:
    """runner.run(job, job_dir) を呼び、終わり方を返す (KILLED は返さない)。

    結果の stopped が True なら STOPPED、そうでなく ok が True なら OK、False なら FAILED (message は結果の message)。
    例外 (Exception) なら ERROR (message は例外の説明。例外は外に出さない)。
    """
    raise NotImplementedError


def start_daemon_thread(body: Callable[[], None]) -> None:
    """body を daemon スレッドで起動し、終わるのを待たずに戻る (start_job_thread の既定値)。"""
    raise NotImplementedError


@dataclass(frozen=True)
class DeepSchedulerSettings:
    """deep のスケジューラーの設定。

    min_start_interval_s   同じ文書のジョブを、前回の起動からこの秒数たつまで起動しない (--deep-min-start-interval)
    auto_seconds           自動の呼び出しの秒数 (--deep-auto-seconds)
    timeout_s              ジョブの実行時間の上限 (--deep-timeout)。起動からこの秒数たったジョブを止める
    stop_grace_s           stop を呼んでから、run が戻らなければ kill を呼ぶまでの秒数 (既定 STOP_GRACE_S)
    """

    min_start_interval_s: float
    auto_seconds: float
    timeout_s: float
    stop_grace_s: float = STOP_GRACE_S


@dataclass(frozen=True)
class DeepSchedulerParts:
    """deep のスケジューラーが使う部品。

    paths              実行ディレクトリー
    runner             ジョブを実行する DeepRunner
    clock              実行の経過秒の時計。起動・終了・タイムアウト・stop からの秒数は、すべてこの時計で測る
    run_log            run.jsonl (deep_start・deep_stop・deep_end・deep_skipped を書く)
    current_scene_id   現在の場面 ID を返す関数 (run では middle/state.json の scene_id)。自動の呼び出しの判定に使う
    latest_frame_t     最新のフレームの時刻を返す関数 (run では frames.jsonl の最後の t)。DeepJob.latest_frame_t に使う
    start_job_thread   ジョブの本体 (引数なしの関数) を受け取って別スレッドで起動する関数。既定は start_daemon_thread。
                       テストでは本体を溜めておき、好きな時点で呼ぶ関数を渡す
    sleep              close が止めたジョブの終わりを待つ間に、秒数を受け取って待つ関数 (既定 time.sleep)。
                       テストでは偽の時計を進める関数を渡す
    """

    paths: RunPaths
    runner: DeepRunner
    clock: RunClock
    run_log: RunLog
    current_scene_id: Callable[[], str | None]
    latest_frame_t: Callable[[], float | None]
    start_job_thread: Callable[[Callable[[], None]], None] = start_daemon_thread
    sleep: Callable[[float], None] = time.sleep


class DeepScheduler:
    """deep のジョブを起動し、止める。

    request・check_auto・check_timeouts・close は複数のスレッドから呼んでよい (判断と記録は 1 つのロックの中で行う。
    DeepRunner の stop・kill・run と、close の待ち (sleep) はロックの外で呼ぶ)。
    DeepRequestSink の実装。作ったときの t を、最初の対象の文書が対象になった時刻とする。
    ジョブは request で起動してから、run が戻るか kill を呼ぶまで「実行中」に数える。
    """

    def __init__(
        self, parts: DeepSchedulerParts, settings: DeepSchedulerSettings
    ) -> None:
        """ジョブの記録が空の状態で作る。自動の呼び出しのスレッドは start で起動する。"""
        raise NotImplementedError

    def request(
        self, kind: DocumentKind, trigger: DeepTrigger, scene_id: str | None
    ) -> DeepRequestResult:
        """種類 kind の文書の deep を、きっかけ trigger で要求する。

        1. 文書のキー key = notes.note_paths.document_key(kind, scene_id) (controls・tips 以外は場面 None)
        2. close の後なら捨てる: run_log に deep_skipped {"kind", "scene_id", "trigger", "reason": SKIP_CLOSED} を書き、
           DeepRequestResult(started=False, reason=SKIP_CLOSED) を返す
        3. 同じ key の前回の起動から min_start_interval_s 秒たっていなければ (now - 前回の起動の t < min_start_interval_s)
           捨てる: deep_skipped {"kind", "scene_id", "trigger", "reason": SKIP_MIN_START_INTERVAL,
           "since_last_start_s": 前回の起動からの秒数} を書き、started=False の結果を返す
        4. 起動する: DeepJob(kind, key.scene_id, trigger, requested_t=now, previous_latest_frame_t=
           deep_job_directory.find_previous_latest_frame_t(paths, key), latest_frame_t=latest_frame_t()) を作り、
           create_job_directory でディレクトリーを用意し、key の前回の起動の t を now にし、実行中に数え、
           run_log に deep_start {"kind", "scene_id", "trigger", "job": 実行ディレクトリーからの相対パス} を書いてから、
           start_job_thread(ジョブの本体) を呼ぶ。DeepRequestResult(started=True, job_dir=ディレクトリー) を返す
        ジョブの本体: execute_deep_job(runner, job, job_dir) を呼ぶ。戻ったときに、そのジョブがまだ kill されていなければ、
        実行中から外し、key の前回の終了の t を戻った時刻にして、run_log に deep_end {"kind", "scene_id", "trigger",
        "job", "status", "message", "stop_reason": 止めた理由 (DeepStopReason の値。止めていなければ null)} を書く。
        既に kill したジョブなら、戻り値を使わず何も書かない (deep_end は kill のときに書いてある)。
        now は clock.now()。ディレクトリーの用意に失敗したら (OSError) 例外をそのまま出す (起動の記録は変えない)。
        """
        raise NotImplementedError

    def check_auto(self) -> list[DeepRequestResult]:
        """自動の呼び出しを 1 回判定し、request を呼んだ結果 (起動したものと、最小間隔で捨てたもの) を
        DOCUMENT_KINDS の順に返す。request を呼ばなければ空のリスト。

        scene_id = current_scene_id() として、DOCUMENT_KINDS の種類ごとに:
            1. 対象の文書 key = document_key(種類, scene_id)
            2. 種類ごとに前回の check_auto で見た対象と key が違えば、key が対象になった時刻を今の t にする
               (最初の check_auto で見た対象は、スケジューラーを作ったときの t に対象になったものとする)
            3. key のジョブが実行中なら起動しない
            4. 基準の時刻 = key の前回のジョブの終了の t (このスケジューラーで終わったものが無ければ、key が対象になった時刻)。
               now - 基準の時刻 >= auto_seconds なら request(種類, DeepTrigger.AUTO, scene_id) を呼ぶ
        close の後は何もせず空のリストを返す。
        """
        raise NotImplementedError

    def check_timeouts(self) -> None:
        """実行中のジョブのタイムアウトと、止めたジョブの kill を 1 回判定する。

        now = clock.now() として、実行中のジョブごとに (起動した順に):

        1. まだ止めておらず、now - 起動の t >= timeout_s なら、止める (stop_job の手順。理由 DeepStopReason.TIMEOUT)
        2. 止めていて (理由を問わない)、now - stop を呼んだ t >= stop_grace_s なら、kill する (kill_job の手順)
        1 と 2 は同じ呼び出しの中で続けて行わない (止めたばかりのジョブは、次の呼び出しから kill の判定をする)。
        止める手順 (stop_job): stop を呼んだ t と理由を記録し、run_log に deep_stop {"kind", "scene_id", "trigger",
        "job", "reason"} を書いてから、ロックの外で runner.stop(job_dir) を呼ぶ。
        kill の手順 (kill_job): ジョブを kill 済みにして実行中から外し、key の前回の終了の t を now にし、run_log に
        deep_end {"kind", "scene_id", "trigger", "job", "status": "killed", "message": stop からの秒数の説明,
        "stop_reason"} を書いてから、ロックの外で runner.kill(job_dir) を呼ぶ。
        runner.stop と runner.kill の例外 (Exception) は外に出さず、logging で警告を書く。
        """
        raise NotImplementedError

    def is_running(self, key: DocumentKey) -> bool:
        """文書 key のジョブが 1 つ以上実行中なら True を返す。"""
        raise NotImplementedError

    def start(self) -> None:
        """CHECK_INTERVAL_S ごとに check_timeouts を呼んでから check_auto を呼ぶスレッド (daemon) を起動する。

        2 回目以降は何もしない。check_timeouts と check_auto の想定外の例外 (Exception) は、スレッドを止めずに
        logging で警告を書く。
        """
        raise NotImplementedError

    def close(self) -> None:
        """以後の要求を捨て、実行中のジョブを止める。2 回目以降の呼び出しは何もしない。

        1. 以後の request を SKIP_CLOSED で捨てるようにし、check_auto と check_timeouts は何もしないようにする
        2. start で起動したスレッドがあれば止めて、終わるのを待つ
        3. 実行中で、まだ止めていないジョブを止める (check_timeouts の stop_job の手順。理由 DeepStopReason.CLOSE)。
           タイムアウトで既に止めたジョブには stop を呼び直さない
        4. deadline = clock.now() + stop_grace_s として、実行中のジョブが無くなるか clock.now() >= deadline になるまで、
           sleep(CLOSE_POLL_INTERVAL_S) で待つ (ロックは持たない。実行中のジョブが無ければ待たない)
        5. まだ実行中のジョブを kill する (check_timeouts の kill_job の手順)
        3 と 5 は、ジョブを起動した順に行う。
        """
        raise NotImplementedError


@dataclass(frozen=True)
class DeepOnceResult:
    """deep-once の結果。

    job_dir   ジョブのディレクトリー
    job       ジョブのディレクトリーの実行ディレクトリーからの相対パス (例: deep/strategy-001)
    outcome   ジョブの終わり方
    """

    job_dir: Path
    job: str
    outcome: DeepJobOutcome


def run_deep_once(
    run_dir: Path,
    kind: DocumentKind,
    scene_id: str | None,
    runner: DeepRunner,
) -> DeepOnceResult:
    """保存済みの実行ディレクトリー run_dir に対して、deep を 1 回、きっかけ manual でこのスレッドで動かす (deep-once)。

    1. scene_id が None で kind が controls・tips なら、middle/state.json の現在の場面
       (surface.middle_state_reader.read_surface_context の scene_id。無ければ None = 共通の文書) を使う
    2. 時刻 now は frames.jsonl の最新の t (frame_store.latest_frame_t。無ければ 0.0)
    3. DeepJob(kind, document_key(kind, scene_id).scene_id, MANUAL, requested_t=now,
       previous_latest_frame_t=find_previous_latest_frame_t, latest_frame_t=frames.jsonl の最新の t (無ければ None)) で
       create_job_directory → execute_deep_job を呼び、結果を返す
    最小の起動間隔とタイムアウトは確かめない (run が戻るまで待つ)。run.jsonl には書かない。
    run_dir がディレクトリーでなければ FileNotFoundError、scene_id が場面 ID の形でなければ ValueError にする。
    """
    raise NotImplementedError
