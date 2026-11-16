"""deep のジョブの起動の判断と実行 (DeepScheduler)、deep-once (run_deep_once)。

ジョブは文書 (種類と場面の組。DocumentKey) を単位に扱う。
- 同じ文書への要求は、きっかけに依らず、次の順に確かめて扱う (request)
    1. close の後なら捨てる (run.jsonl に deep_skipped)
    2. 前回の起動から min_start_interval_s 秒たっていなければ捨てる (deep_skipped)。実行中のジョブの有無より先に確かめる
    3. 実行中のジョブがあれば、1 件だけ保留する (deep_pending)。保留中の要求があれば、古い要求を捨てて (deep_skipped)
       新しい要求に置き換える
    4. どれにも当たらなければ起動する
- 同じ文書の実行中のジョブは 1 件まで。保留中の要求は、その文書の実行中のジョブが終わったとき (run が戻った・kill した・
  ジョブのスレッドを起動できなかった) に、すぐ起動する。ジョブの終わりの記録 (deep_end) と、保留中の要求の起動の用意
  (ジョブのディレクトリーと deep_start) は、同じ 1 つのロックの区間で行う。そのため、その間にほかのスレッドの要求が
  入り、実行中のジョブが無いと見て直接起動することは無い (start_job_thread だけをロックの外で呼ぶ)。
  close では保留中の要求を捨てる
- controls と tips の対象は、要求した側が渡した現在の場面の文書 (場面が無ければ共通の文書)
- 自動の呼び出し (check_auto) は、種類ごとに現在の対象の文書について判定し、次の 2 つを満たしたら、きっかけ auto で起動する
    - その文書のジョブが実行中でない (実行中の文書は対象にせず、保留も作らない)
    - その文書の前回のジョブの終了 (まだ無ければ、その文書が対象になった時刻) から auto_seconds 秒たった
- 画面の停滞の計測による呼び出し (check_progress) は、新しい画面が出ていない秒数が stuck_advice_seconds を超えたら、
  advice の文書をきっかけ progress で要求する (progress.stuck_advice_trigger の規則。最小の起動間隔は request が当てる)
- 自動の呼び出しと画面の停滞の計測による呼び出しは、run の段階が play のときだけ行う (部品の is_play で確かめる。
  タイムアウトの判定 check_timeouts は段階に依らず続ける)
- ジョブを始めるときに deep/<種類>-<連番3桁>/ と job.json を用意し (deep_job_directory)、
  別スレッドで DeepRunner.run を呼ぶ。開始と終了を run.jsonl に deep_start・deep_end で書く
- ジョブを止める手順は、タイムアウト (起動から timeout_s 秒) でも終了時 (close) でも同じ:
  DeepRunner.stop を呼び (run.jsonl に deep_stop)、stop_grace_s 秒 (既定 5 秒) たっても run が戻らなければ
  DeepRunner.kill を呼ぶ (deep_end の status は killed)。stop の後に run が戻れば kill は呼ばない
- 文書ごとの呼び出しの状況 (実行中のジョブ・保留中の要求・前回の終了・最小間隔の残り・surface が最後に求めた結果) を
  document_status で読める (surface が advisor 判定の state に入れる)。読み取りはジョブを起動せず、run.jsonl にも書かない
時計 (RunClock)・スレッドの起動 (start_job_thread)・終了時の待ち (sleep) を注入でき、テストでは時間を進めて
request・check_auto・check_timeouts・close・document_status を直接呼ぶ。
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path

from switch2_layers.deep.deep_job_directory import (
    create_job_directory,
    find_previous_latest_frame_t,
    read_job_map_layout,
)
from switch2_layers.deep.deep_types import (
    DOCUMENT_KINDS,
    SCENE_DOCUMENT_KINDS,
    DeepJob,
    DeepRequestResult,
    DeepRunner,
    DeepTrigger,
    DocumentKey,
    DocumentKind,
)
from switch2_layers.error_text import describe_exception
from switch2_layers.frames.frame_store import latest_frame_t
from switch2_layers.notes.note_paths import document_key, is_scene_id
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.stuck_advice_trigger import (
    DEFAULT_STUCK_ADVICE_SECONDS,
    StuckAdviceTrigger,
)
from switch2_layers.records.run_log import (
    EVENT_DEEP_END,
    EVENT_DEEP_PENDING,
    EVENT_DEEP_REQUEST_ERROR,
    EVENT_DEEP_SKIPPED,
    EVENT_DEEP_START,
    EVENT_DEEP_STOP,
    RunLog,
)
from switch2_layers.run_clock import RunClock, round_to_milliseconds
from switch2_layers.run_directory import RunPaths, open_run_directory
from switch2_layers.surface.middle_state_reader import read_surface_context

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# タイムアウトと自動の呼び出しを判定する間隔 (秒)
CHECK_INTERVAL_S = 0.5
# stop を呼んでから、run が戻らなければ kill を呼ぶまでの秒数
STOP_GRACE_S = 5.0
# close が、止めたジョブの run が戻ったかを確かめる間隔 (秒)
CLOSE_POLL_INTERVAL_S = 0.05
# deep_skipped の reason: 同じ文書の前回の起動から min_start_interval_s 秒たっていない
SKIP_MIN_START_INTERVAL = "min_start_interval"
# deep_skipped の reason: close の後に来た要求、または close で捨てた保留中の要求
SKIP_CLOSED = "closed"
# deep_skipped の reason: 保留中の要求を、後から来た要求で置き換えた (捨てたのは古い要求)
SKIP_REPLACED = "replaced"
# DeepRequestResult と DeepRequestOutcome の reason: 同じ文書のジョブが実行中のため、要求を保留した
REQUEST_PENDING = "pending"
# check_progress は、progress/state.json の t がこの秒数より前の値では判定を見送る。
# フレームの保存か値の書き込みが止まったときに、しきい値を超えた古い値で advice を求め続けないため
PROGRESS_STATE_MAX_AGE_S = 10.0


class DeepStopReason(StrEnum):
    """ジョブを止めた理由。run.jsonl の deep_stop の reason と deep_end の stop_reason。"""

    TIMEOUT = "timeout"  # 起動から timeout_s 秒たった
    CLOSE = "close"  # 終了処理 (DeepScheduler.close) で実行中だった


class DeepJobStatus(StrEnum):
    """ジョブの終わり方。run.jsonl の deep_end の status。"""

    OK = "ok"  # DeepRunner.run が ok=True の結果を返した
    FAILED = "failed"  # DeepRunner.run が ok=False で stopped=False の結果を返した
    ERROR = "error"  # DeepRunner.run が例外を出した、またはジョブのスレッドを起動できなかった
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


@dataclass(frozen=True)
class DeepJobEnd:
    """文書の前回の終了 (DeepDocumentStatus.last_end)。

    t        終わった t (run が戻った・kill した・スレッドを起動できなかった・ジョブのディレクトリーを用意できなかった時刻)
    status   終わり方。ジョブのディレクトリーを用意できなかった失敗 (request から起動するときと、保留から起動するとき) は
             DeepJobStatus.ERROR
    """

    t: float
    status: DeepJobStatus


@dataclass(frozen=True)
class DeepRequestOutcome:
    """要求 1 回の扱い (DeepDocumentStatus.last_surface_request)。

    t         要求の t (request を呼んだときの clock.now())
    started   ジョブを起動したら True、捨てたか保留したら False
    reason    捨てた理由 (SKIP_MIN_START_INTERVAL か SKIP_CLOSED)、または保留したときの REQUEST_PENDING。起動したら None
    保留した要求の扱いは、保留したときのまま変えない (後で保留から起動しても、ほかの要求に置き換えられても、close で
    捨てられても REQUEST_PENDING のまま)。
    """

    t: float
    started: bool
    reason: str | None = None


@dataclass(frozen=True)
class DeepDocumentStatus:
    """文書 1 つの deep の呼び出しの状況 (DeepScheduler.document_status の結果)。

    running_count              実行中のジョブの数 (起動してから、run が戻るか kill するまで。stop を呼んで
                               戻るのを待っているジョブも数える)
    latest_running_start_t     実行中のジョブのうち、最も新しい起動の t。実行中が無ければ None
    pending_request_t          保留中の要求の t (その要求で request を呼んだときの clock.now())。保留中の要求が無ければ None
    last_end                   前回の終了 (このスケジューラーで終わったジョブ、または起動の失敗)。まだ無ければ None
    min_interval_remaining_s   最小間隔の残りの秒数。前回の起動から min_start_interval_s 秒たっていなければその残り
                               (0 より大きい)、たっていれば (前回の起動が無いときを含む) 0.0
    last_surface_request       きっかけが surface の request の、最後の扱い (起動したか、保留したか、捨てた理由)。
                               まだ無ければ None
    """

    running_count: int
    latest_running_start_t: float | None
    pending_request_t: float | None
    last_end: DeepJobEnd | None
    min_interval_remaining_s: float
    last_surface_request: DeepRequestOutcome | None


def execute_deep_job(runner: DeepRunner, job: DeepJob, job_dir: Path) -> DeepJobOutcome:
    """runner.run(job, job_dir) を呼び、終わり方を返す (KILLED は返さない)。

    結果の stopped が True なら STOPPED、そうでなく ok が True なら OK、False なら FAILED (message は結果の message)。
    例外 (Exception) なら ERROR (message は例外の説明。例外は外に出さない)。
    """
    try:
        result = runner.run(job, job_dir)
    except Exception as error:
        logger.warning("deep job %s raised an exception", job_dir, exc_info=True)
        return DeepJobOutcome(DeepJobStatus.ERROR, describe_exception(error))
    if result.stopped:
        return DeepJobOutcome(DeepJobStatus.STOPPED, result.message)
    if result.ok:
        return DeepJobOutcome(DeepJobStatus.OK, result.message)
    return DeepJobOutcome(DeepJobStatus.FAILED, result.message)


def start_daemon_thread(body: Callable[[], None]) -> None:
    """body を daemon スレッドで起動し、終わるのを待たずに戻る (start_job_thread の既定値)。"""
    threading.Thread(target=body, name="deep-job", daemon=True).start()


def no_progress_state() -> ProgressState | None:
    """いつも None (画面の停滞の計測の値が無い) を返す (DeepSchedulerParts.progress_state の既定値)。"""
    return None


def always_play() -> bool:
    """いつも True (段階が play) を返す (DeepSchedulerParts.is_play の既定値)。"""
    return True


@dataclass(frozen=True)
class DeepSchedulerSettings:
    """deep のスケジューラーの設定。

    min_start_interval_s   同じ文書のジョブを、前回の起動からこの秒数たつまで、起動も保留もしない
                           (--deep-min-start-interval)
    auto_seconds           自動の呼び出しの秒数 (--deep-auto-seconds)
    timeout_s              ジョブの実行時間の上限 (--deep-timeout)。起動からこの秒数たったジョブを止める
    stop_grace_s           stop を呼んでから、run が戻らなければ kill を呼ぶまでの秒数 (既定 STOP_GRACE_S)
    stuck_advice_seconds   新しい画面が出ていない秒数がこれを超えたら、advice の deep をきっかけ progress で要求する
                           (--stuck-advice-seconds。既定 progress.stuck_advice_trigger.DEFAULT_STUCK_ADVICE_SECONDS)
    """

    min_start_interval_s: float
    auto_seconds: float
    timeout_s: float
    stop_grace_s: float = STOP_GRACE_S
    stuck_advice_seconds: float = DEFAULT_STUCK_ADVICE_SECONDS


@dataclass(frozen=True)
class DeepSchedulerParts:
    """deep のスケジューラーが使う部品。

    paths              実行ディレクトリー
    runner             ジョブを実行する DeepRunner
    clock              実行の経過秒の時計。起動・終了・タイムアウト・stop からの秒数は、すべてこの時計で測る
    run_log            run.jsonl (deep_start・deep_stop・deep_end・deep_skipped・deep_pending・deep_request_error を書く)
    current_scene_id   現在の場面 ID を返す関数 (run では middle/state.json の scene_id)。自動の呼び出しの判定に使う
    latest_frame_t     最新のフレームの時刻を返す関数 (run では frames.jsonl の最後の t)。DeepJob.latest_frame_t に使う
    start_job_thread   ジョブの本体 (引数なしの関数) を受け取って別スレッドで起動する関数。既定は start_daemon_thread。
                       テストでは本体を溜めておき、好きな時点で呼ぶ関数を渡す
    sleep              close が止めたジョブの終わりを待つ間に、秒数を受け取って待つ関数 (既定 time.sleep)。
                       テストでは偽の時計を進める関数を渡す
    prepare_job_directory
                       ジョブのディレクトリーを用意する関数 (既定 deep_job_directory.create_job_directory)。
                       テストでは、ある種類の用意に失敗する関数を渡す
    progress_state     画面の停滞の計測の最新の値を返す関数 (run では progress/state.json を
                       progress.progress_state.read_progress_state で読む)。値が無ければ None。
                       既定は、いつも None を返す関数 (きっかけ progress で要求しない)
    is_play            run の段階が play なら True を返す関数 (run では app の段階を読む)。False の間は、自動の呼び出し
                       (check_auto) と画面の停滞の計測による呼び出し (check_progress) をしない。既定は、いつも True を
                       返す関数
    """

    paths: RunPaths
    runner: DeepRunner
    clock: RunClock
    run_log: RunLog
    current_scene_id: Callable[[], str | None]
    latest_frame_t: Callable[[], float | None]
    start_job_thread: Callable[[Callable[[], None]], None] = start_daemon_thread
    sleep: Callable[[float], None] = time.sleep
    prepare_job_directory: Callable[[RunPaths, DeepJob], Path] = create_job_directory
    progress_state: Callable[[], ProgressState | None] = no_progress_state
    is_play: Callable[[], bool] = always_play


@dataclass
class _RunningJob:
    """スケジューラーが起動したジョブ 1 件の状態。ロックの中で読み書きする。

    job           ジョブの依頼
    job_dir       ジョブのディレクトリー
    relative      ジョブのディレクトリーの実行ディレクトリーからの相対パス (run.jsonl の job)
    started_t     起動した t
    pending_t     保留した要求の t (deep_start の pending_t)。保留を経ずに起動したジョブでは None
    stop_t        stop を呼んだ t。まだ止めていなければ None
    stop_reason   止めた理由。まだ止めていなければ None
    ended         deep_end を書いた (run が戻った・kill した・スレッドを起動できなかった) なら True
    """

    job: DeepJob
    job_dir: Path
    relative: str
    started_t: float
    pending_t: float | None = None
    stop_t: float | None = None
    stop_reason: DeepStopReason | None = None
    ended: bool = False

    def fields(self) -> dict[str, object]:
        """run.jsonl の deep_start・deep_stop・deep_end に共通の項目 {"kind", "scene_id", "trigger", "job"} を返す。"""
        return {
            "kind": self.job.kind.value,
            "scene_id": self.job.scene_id,
            "trigger": self.job.trigger.value,
            "job": self.relative,
        }


@dataclass(frozen=True)
class _PendingRequest:
    """文書 1 つの保留中の要求 (その文書の実行中のジョブが終わったら起動する)。ロックの中で読み書きする。

    trigger       要求のきっかけ (起動するジョブの trigger)
    requested_t   要求の t (起動するジョブの requested_t と、deep_start の pending_t)
    """

    trigger: DeepTrigger
    requested_t: float


class DeepScheduler:
    """deep のジョブを起動し、止める。

    request・check_auto・check_timeouts・close・document_status は複数のスレッドから呼んでよい (判断と記録と読み取りは
    1 つのロックの中で行う。DeepRunner の stop・kill・run と、close の待ち (sleep) はロックの外で呼ぶ)。
    check_progress は 1 つのスレッドからだけ呼ぶ。
    DeepRequestSink と surface.surface_types.DeepStatusReader の実装。作ったときの t を、最初の対象の文書が対象になった
    時刻とする。
    ジョブは起動 (request から、または保留から) してから、run が戻るか kill を呼ぶまで「実行中」に数える。
    同じ文書の実行中のジョブは 1 件まで、保留中の要求も 1 件までにする。
    """

    def __init__(
        self, parts: DeepSchedulerParts, settings: DeepSchedulerSettings
    ) -> None:
        """ジョブの記録が空の状態で作る。自動の呼び出しのスレッドは start で起動する。"""
        self.parts = parts
        self.settings = settings
        self._lock = threading.Lock()
        self._created_t = parts.clock.now()
        # 実行中のジョブ (起動した順)
        self._running: list[_RunningJob] = []
        # 文書ごとの前回の起動の t と、前回の終了 (request の失敗を含む)
        self._last_start_t: dict[DocumentKey, float] = {}
        self._last_end: dict[DocumentKey, DeepJobEnd] = {}
        # 文書ごとの保留中の要求 (その文書のジョブが実行中の間だけ持つ)
        self._pending: dict[DocumentKey, _PendingRequest] = {}
        # 文書ごとの、きっかけが surface の要求の最後の扱い
        self._last_surface_request: dict[DocumentKey, DeepRequestOutcome] = {}
        # 種類ごとに、前回の check_auto で見た対象の文書と、それが対象になった t
        self._auto_targets: dict[DocumentKind, tuple[DocumentKey, float]] = {}
        self._closed = False
        self._stop_checks = threading.Event()
        self._thread: threading.Thread | None = None
        # きっかけ progress で要求するかの判定 (check_progress だけが使う)
        self._stuck_advice = StuckAdviceTrigger(settings.stuck_advice_seconds)

    def request(
        self, kind: DocumentKind, trigger: DeepTrigger, scene_id: str | None
    ) -> DeepRequestResult:
        """種類 kind の文書の deep を、きっかけ trigger で要求する。

        文書のキー key = notes.note_paths.document_key(kind, scene_id) (controls・tips 以外は場面 None) について、
        きっかけに依らず、次の順に確かめる。now は clock.now():
        1. close の後なら捨てる: run_log に deep_skipped {"kind", "scene_id", "trigger", "reason": SKIP_CLOSED} を書き、
           DeepRequestResult(started=False, reason=SKIP_CLOSED) を返す
        2. 同じ key の前回の起動から min_start_interval_s 秒たっていなければ (now - 前回の起動の t < min_start_interval_s)
           捨てる: deep_skipped {"kind", "scene_id", "trigger", "reason": SKIP_MIN_START_INTERVAL,
           "since_last_start_s": 前回の起動からの秒数 (ミリ秒に丸める)} を書き、
           DeepRequestResult(started=False, reason=SKIP_MIN_START_INTERVAL) を返す。
           key のジョブが実行中かどうかに依らず、3 より先にこの判定を行う
        3. key のジョブが実行中なら (stop を呼んで戻るのを待っているジョブも数える)、保留する (_hold_request_locked):
           key に保留中の要求があれば、それを捨てて deep_skipped {"kind", "scene_id", "trigger": 古い要求のきっかけ,
           "reason": SKIP_REPLACED} を書く。この要求 (trigger と now) を key の保留中の要求にし、deep_pending {"kind",
           "scene_id", "trigger", "since_last_start_s": 前回の起動からの秒数 (ミリ秒に丸める)} を書き、
           DeepRequestResult(started=False, reason=REQUEST_PENDING) を返す。ジョブのディレクトリーはまだ作らない
        4. 起動する: map_layout = deep_job_directory.read_job_map_layout(notes/, kind) (map ならゲームの性質の文書から
           決めた型。判定が付かなければ map.json の型、それも無ければ free。map 以外は None) を 1 回だけ求め、DeepJob(kind, key.scene_id, trigger, requested_t=now,
           previous_latest_frame_t=deep_job_directory.find_previous_latest_frame_t(paths, key, map_layout) (同じ文書の、
           前回の正常に終わったジョブの latest_frame_t。失敗・stop・kill のジョブは飛ばす。map の文書で、map.json の
           layout が map_layout と違えば None), latest_frame_t=latest_frame_t(), map_layout=map_layout) を作り、
           prepare_job_directory(paths, job) でディレクトリーを用意し、key の前回の起動の t を now にし、実行中に数え、
           run_log に deep_start {"kind", "scene_id", "trigger", "job": 実行ディレクトリーからの相対パス,
           "pending_t": null} を書いてから、start_job_thread(ジョブの本体) を呼ぶ。
           DeepRequestResult(started=True, job_dir=ディレクトリー) を返す
        ジョブの本体 (_run_job): execute_deep_job(runner, job, job_dir) を呼ぶ。戻ったときに、そのジョブがまだ kill されて
        いなければ、1 つのロックの区間で、実行中から外し、key の前回の終了を (戻った時刻, 終わり方) にして、run_log に
        deep_end {"kind", "scene_id", "trigger", "job", "status", "message", "stop_reason": 止めた理由 (DeepStopReason の値。
        止めていなければ null)} を書き、key の保留中の要求の起動を用意する (_finish_locked)。ロックの外で、用意したジョブの
        スレッドを起動する (_start_pending)。既に kill したジョブなら、戻り値を使わず何も書かない (deep_end は kill の
        ときに書いてあり、保留中の要求も kill のときに起動してある)。
        trigger が DeepTrigger.SURFACE なら、1 と 2 で捨てたとき (deep_skipped を書いたとき)、3 で保留したとき
        (deep_pending を書いたとき)、4 で起動したとき (deep_start を書いたとき) に、key の surface の最後の要求を
        DeepRequestOutcome(now, started, 理由) にする (document_status の last_surface_request。理由は、捨てたらその理由、
        保留したら REQUEST_PENDING、起動したら None)。ほかのきっかけの要求では変えない。保留した要求を後で起動したとき・
        ほかの要求で置き換えたとき・close で捨てたときも変えない。
        4 で起動するときの失敗の扱い (どちらも、失敗を「key の前回のジョブの終了」と同じに扱い、key の前回の終了を
        (now, DeepJobStatus.ERROR) にする。そのため自動の呼び出しは、失敗した時刻から auto_seconds の間、この文書を
        対象にしない):
            - prepare_job_directory が例外 (Exception) を出したら (作りかけのディレクトリーは
              deep_job_directory.create_job_directory が消す)、前回の終了を (now, ERROR) にしてから、例外をそのまま出す。
              前回の起動の t は変えず (最小間隔の判定に数えない)、run_log にも書かない (呼んだ側が記録する)。
              起動も捨ても保留もしていないため、surface の最後の要求も変えない
            - start_job_thread が例外を出したら、1 つのロックの区間で、key を実行中から外し、前回の終了を (now, ERROR)
              にし、run_log に deep_end {..., "status": "error", "message": 説明, "stop_reason": null} を書き、key の
              保留中の要求 (start_job_thread を呼ぶまでの間に、ほかのスレッドが保留したもの) の起動を用意する
              (_finish_locked)。ロックの外で用意したジョブのスレッドを起動してから (_start_pending)、例外をそのまま出す
              (deep_start と対応させるため、ジョブのディレクトリーは残す。surface の最後の要求は、4 で起動したときのまま)
        保留中の要求の起動の用意は _take_pending_job_locked、スレッドの起動とその失敗の扱い (例外を外に出さない) は
        _start_pending に書く。
        """
        key = document_key(kind, scene_id)
        parts = self.parts
        with self._lock:
            now = parts.clock.now()
            if self._closed:
                self._write_skipped_locked(key, trigger, SKIP_CLOSED)
                self._note_surface_request_locked(
                    key, trigger, DeepRequestOutcome(now, False, SKIP_CLOSED)
                )
                return DeepRequestResult(started=False, reason=SKIP_CLOSED)
            last_start = self._last_start_t.get(key)
            if (
                last_start is not None
                and now - last_start < self.settings.min_start_interval_s
            ):
                self._write_skipped_locked(
                    key,
                    trigger,
                    SKIP_MIN_START_INTERVAL,
                    since_last_start_s=round_to_milliseconds(now - last_start),
                )
                self._note_surface_request_locked(
                    key,
                    trigger,
                    DeepRequestOutcome(now, False, SKIP_MIN_START_INTERVAL),
                )
                return DeepRequestResult(started=False, reason=SKIP_MIN_START_INTERVAL)
            if self._is_running_locked(key):
                return self._hold_request_locked(key, trigger, now)
            map_layout = read_job_map_layout(parts.paths.notes_dir, kind)
            job = DeepJob(
                kind=kind,
                scene_id=key.scene_id,
                trigger=trigger,
                requested_t=now,
                previous_latest_frame_t=find_previous_latest_frame_t(
                    parts.paths, key, map_layout
                ),
                latest_frame_t=parts.latest_frame_t(),
                map_layout=map_layout,
            )
            try:
                job_dir = parts.prepare_job_directory(parts.paths, job)
            except Exception:
                # 失敗を前回の終了と同じに扱い、自動の呼び出しが同じ失敗をすぐ繰り返さないようにする
                self._last_end[key] = DeepJobEnd(now, DeepJobStatus.ERROR)
                raise
            running = _RunningJob(job, job_dir, parts.paths.relative(job_dir), now)
            self._add_running_locked(running)
            self._note_surface_request_locked(
                key, trigger, DeepRequestOutcome(now, True)
            )
        try:
            parts.start_job_thread(partial(self._run_job, running))
        except Exception as error:
            with self._lock:
                pending_job = self._finish_locked(
                    running,
                    DeepJobStatus.ERROR,
                    describe_exception(error),
                    parts.clock.now(),
                )
            self._start_pending(pending_job)
            raise
        return DeepRequestResult(started=True, job_dir=job_dir)

    def check_auto(self) -> list[DeepRequestResult]:
        """自動の呼び出しを 1 回判定し、request を呼んだ結果 (起動したものと、最小間隔で捨てたもの) を
        DOCUMENT_KINDS の順に返す。request を呼ばなければ空のリスト。request が例外を出した種類は結果に含めない。

        scene_id = current_scene_id() として、DOCUMENT_KINDS の種類ごとに:
            1. 対象の文書 key = document_key(種類, scene_id)
            2. 種類ごとに前回の check_auto で見た対象と key が違えば、key が対象になった時刻を今の t にする
               (最初の check_auto で見た対象は、スケジューラーを作ったときの t に対象になったものとする)
            3. key のジョブが実行中なら request を呼ばない (起動せず、保留も作らない)
            4. 基準の時刻 = key の前回のジョブの終了の t (このスケジューラーで終わったもの、または request の失敗。
               無ければ、key が対象になった時刻)。now - 基準の時刻 >= auto_seconds なら request(種類, DeepTrigger.AUTO,
               scene_id) を呼ぶ
            5. request が例外 (Exception) を出したら、外に出さずに run_log に deep_request_error {"source": "auto",
               "kind", "trigger": "auto", "scene_id", "error": 説明} を書き、残りの種類の判定を続ける。
               request が失敗を前回の終了として記録するため、その文書は失敗した時刻から auto_seconds の間、再び要求しない
        close の後と、is_play() が False (段階が play でない) の間は、何もせず空のリストを返す (対象の文書が対象に
        なった時刻も変えない)。
        """
        with self._lock:
            if self._closed:
                return []
        scene_id = self.parts.current_scene_id()
        results: list[DeepRequestResult] = []
        for kind in DOCUMENT_KINDS:
            try:
                key = document_key(kind, scene_id)
                with self._lock:
                    if self._closed:
                        break
                    due = self._auto_due_locked(kind, key, self.parts.clock.now())
                if due:
                    results.append(self.request(kind, DeepTrigger.AUTO, scene_id))
            except Exception as error:
                logger.debug("automatic deep request failed", exc_info=True)
                self.parts.run_log.write(
                    EVENT_DEEP_REQUEST_ERROR,
                    source="auto",
                    kind=kind.value,
                    trigger=DeepTrigger.AUTO.value,
                    scene_id=scene_id,
                    error=describe_exception(error),
                )
        return results

    def check_progress(self) -> DeepRequestResult | None:
        """画面の停滞の計測から、advice の deep をきっかけ progress で要求するかを 1 回判定する。

        判定は progress.stuck_advice_trigger.StuckAdviceTrigger (しきい値は settings.stuck_advice_seconds) で行う:
            1. state = parts.progress_state() (値が無ければ None)。now = clock.now() とし、now - state.t が
               PROGRESS_STATE_MAX_AGE_S を超える state なら、StuckAdviceTrigger を呼ばずに None を返す
               (ちょうどその秒数なら使う)。古い値は停滞が解けたことを示さないため、前回の要求の時刻を消さない
            2. advice の文書 (場面に依らない) のジョブが 1 つ以上実行中か (きっかけに依らない) を確かめる
            3. StuckAdviceTrigger.check(now, state の no_novel_s (state が None なら None), 実行中か) が
               True なら、request(DocumentKind.ADVICE, DeepTrigger.PROGRESS, None) を呼んで、その結果を返す。
               最小の起動間隔と保留などの規則は request がそのまま当てる (捨てたか保留したら
               DeepRequestResult(started=False, ...))
            4. request が例外 (Exception) を出したら、外に出さずに run_log に deep_request_error {"source": "progress",
               "kind": "advice", "trigger": "progress", "scene_id": null, "error": 説明} を書き、None を返す
        要求しなければ None を返す。close の後と、is_play() が False (段階が play でない) の間は、何もせず None を
        返す (StuckAdviceTrigger も呼ばない)。
        判定の状態 (前回の要求の時刻) を持つため、1 つのスレッド (start で起動する判定のスレッド) からだけ呼ぶ。
        """
        with self._lock:
            if self._closed:
                return None
        state = self.parts.progress_state()
        now = self.parts.clock.now()
        if state is not None and now - state.t > PROGRESS_STATE_MAX_AGE_S:
            return None
        advice_key = document_key(DocumentKind.ADVICE, None)
        advice_running = self.is_running(advice_key)
        no_novel_s = None if state is None else state.no_novel_s
        if not self._stuck_advice.check(now, no_novel_s, advice_running):
            return None
        try:
            return self.request(DocumentKind.ADVICE, DeepTrigger.PROGRESS, None)
        except Exception as error:
            logger.debug("progress deep request failed", exc_info=True)
            self.parts.run_log.write(
                EVENT_DEEP_REQUEST_ERROR,
                source="progress",
                kind=DocumentKind.ADVICE.value,
                trigger=DeepTrigger.PROGRESS.value,
                scene_id=None,
                error=describe_exception(error),
            )
            return None

    def check_timeouts(self) -> None:
        """実行中のジョブのタイムアウトと、止めたジョブの kill を 1 回判定する。

        now = clock.now() として、実行中のジョブごとに (起動した順に):

        1. まだ止めておらず、now - 起動の t >= timeout_s なら、止める (stop_job の手順。理由 DeepStopReason.TIMEOUT)
        2. 止めていて (理由を問わない)、now - stop を呼んだ t >= stop_grace_s なら、kill する (kill_job の手順)
        1 と 2 は同じ呼び出しの中で続けて行わない (止めたばかりのジョブは、次の呼び出しから kill の判定をする)。
        止める手順 (stop_job): stop を呼んだ t と理由を記録し、run_log に deep_stop {"kind", "scene_id", "trigger",
        "job", "reason"} を書いてから、ロックの外で runner.stop(job_dir) を呼ぶ。
        kill の手順 (kill_job): 1 つのロックの区間で、ジョブを kill 済みにして実行中から外し、key の前回の終了の t を
        now にし、run_log に deep_end {"kind", "scene_id", "trigger", "job", "status": "killed", "message": stop からの
        秒数の説明, "stop_reason"} を書き、key の保留中の要求の起動を用意する (_mark_killed_locked)。ロックの外で
        runner.kill(job_dir) を呼んでから、用意したジョブのスレッドを起動する (_start_pending。close の後は保留中の
        要求が無く、起動の用意もしない)。
        runner.stop と runner.kill の例外 (Exception) は外に出さず、logging で警告を書く。
        """
        actions: list[tuple[Callable[[Path], None], _RunningJob]] = []
        pending_jobs: list[_RunningJob] = []
        with self._lock:
            if self._closed:
                return
            now = self.parts.clock.now()
            for running in list(self._running):
                if running.stop_t is None:
                    if now - running.started_t >= self.settings.timeout_s:
                        self._mark_stopped_locked(running, DeepStopReason.TIMEOUT, now)
                        actions.append((self.parts.runner.stop, running))
                elif now - running.stop_t >= self.settings.stop_grace_s:
                    pending_job = self._mark_killed_locked(running, now)
                    actions.append((self.parts.runner.kill, running))
                    if pending_job is not None:
                        pending_jobs.append(pending_job)
        for action, running in actions:
            _call_runner(action, running.job_dir)
        for pending_job in pending_jobs:
            self._start_pending(pending_job)

    def is_running(self, key: DocumentKey) -> bool:
        """文書 key のジョブが 1 つ以上実行中なら True を返す。"""
        with self._lock:
            return any(running.job.key == key for running in self._running)

    def document_status(
        self, kind: DocumentKind, scene_id: str | None
    ) -> DeepDocumentStatus:
        """種類 kind の文書の deep の呼び出しの状況を返す (読み取りだけ)。

        対象の文書は request と同じく key = notes.note_paths.document_key(kind, scene_id) (controls・tips は scene_id の
        場面の文書、scene_id が None なら共通の文書。ほかの種類では scene_id を無視する)。now = clock.now() として、
        ロックの中で次を読み、DeepDocumentStatus にする:
            - running_count: key の実行中のジョブの数。latest_running_start_t: そのうち最も新しい起動の t
              (実行中が無ければ running_count は 0、latest_running_start_t は None)
            - pending_request_t: key の保留中の要求の t (request で保留したときの now)。保留中の要求が無ければ None
            - last_end: key の前回の終了 DeepJobEnd(t, 終わり方) (request の docstring。ジョブの終わりでは、その時刻と
              終わり方。起動の失敗 (request から起動するときと保留から起動するときのディレクトリーの用意の失敗) では、
              その時刻と DeepJobStatus.ERROR)。まだ無ければ None
            - min_interval_remaining_s: key の前回の起動の t があり、remaining = min_start_interval_s - (now - 前回の起動の t)
              が 0 より大きければ remaining、そうでなければ 0.0 (前回の起動が無いときも 0.0)。remaining が 0 より大きいのは、
              今 request を呼べば最小間隔で捨てられるときと同じ
            - last_surface_request: key の surface の最後の要求 (request の docstring)。まだ無ければ None
        ジョブを起動せず、run.jsonl にも書かない。close の後も読める。
        controls・tips で scene_id が場面 ID の形でなければ ValueError (document_key と同じ)。
        """
        key = document_key(kind, scene_id)
        with self._lock:
            now = self.parts.clock.now()
            running_starts = [
                running.started_t for running in self._running if running.job.key == key
            ]
            remaining = 0.0
            last_start = self._last_start_t.get(key)
            if last_start is not None:
                # request の最小間隔の判定 (now - last_start < min_start_interval_s) と同じ境界にする
                left = self.settings.min_start_interval_s - (now - last_start)
                if left > 0:
                    remaining = left
            pending = self._pending.get(key)
            return DeepDocumentStatus(
                running_count=len(running_starts),
                latest_running_start_t=max(running_starts) if running_starts else None,
                pending_request_t=None if pending is None else pending.requested_t,
                last_end=self._last_end.get(key),
                min_interval_remaining_s=remaining,
                last_surface_request=self._last_surface_request.get(key),
            )

    def start(self) -> None:
        """CHECK_INTERVAL_S ごとに check_timeouts・check_auto・check_progress をこの順に呼ぶスレッド (daemon) を起動する。

        2 回目以降は何もしない。check_timeouts・check_auto・check_progress の想定外の例外 (Exception) は、スレッドを
        止めずに logging で警告を書く。
        """
        with self._lock:
            if self._thread is not None or self._closed:
                return
            self._thread = threading.Thread(
                target=self._run_checks, name="deep-scheduler", daemon=True
            )
            self._thread.start()

    def close(self) -> None:
        """以後の要求を捨て、実行中のジョブを止める。2 回目以降の呼び出しは何もしない。

        1. 以後の request を SKIP_CLOSED で捨てるようにし、check_auto と check_timeouts は何もしないようにする。
           同じロックの中で、保留中の要求をすべて捨て、それぞれ run_log に deep_skipped {"kind", "scene_id",
           "trigger": 保留中の要求のきっかけ, "reason": SKIP_CLOSED} を書く (_discard_all_pending_locked)。
           以後、実行中のジョブが終わっても (run が戻っても、kill しても)、保留から起動しない
        2. start で起動したスレッドがあれば止めて、終わるのを待つ
        3. 実行中で、まだ止めていないジョブを止める (check_timeouts の stop_job の手順。理由 DeepStopReason.CLOSE)。
           タイムアウトで既に止めたジョブには stop を呼び直さない
        4. deadline = clock.now() + stop_grace_s として、実行中のジョブが無くなるか clock.now() >= deadline になるまで、
           sleep(CLOSE_POLL_INTERVAL_S) で待つ (ロックは持たない。実行中のジョブが無ければ待たない)
        5. まだ実行中のジョブを kill する (check_timeouts の kill_job の手順)
        3 と 5 は、ジョブを起動した順に行う。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._discard_all_pending_locked()
            thread = self._thread
        # 1. 判定のスレッドを止める (ロックを持たずに待つ)
        self._stop_checks.set()
        if thread is not None:
            thread.join()
        # 2. まだ止めていないジョブを止める
        clock = self.parts.clock
        with self._lock:
            now = clock.now()
            to_stop = [running for running in self._running if running.stop_t is None]
            for running in to_stop:
                self._mark_stopped_locked(running, DeepStopReason.CLOSE, now)
        for running in to_stop:
            _call_runner(self.parts.runner.stop, running.job_dir)
        # 3. run が戻るのを最大 stop_grace_s 秒待つ
        deadline = clock.now() + self.settings.stop_grace_s
        while self._has_running_jobs() and clock.now() < deadline:
            self.parts.sleep(CLOSE_POLL_INTERVAL_S)
        # 4. まだ戻らないジョブを kill する (close の後なので、保留から起動するジョブは無い)
        with self._lock:
            now = clock.now()
            to_kill = list(self._running)
            for running in to_kill:
                self._mark_killed_locked(running, now)
        for running in to_kill:
            _call_runner(self.parts.runner.kill, running.job_dir)

    def _run_job(self, running: _RunningJob) -> None:
        """ジョブの本体。run を呼び、戻ったときにまだ kill していなければ、1 つのロックの区間で deep_end を書いて同じ文書の
        保留中の要求の起動を用意し (_finish_locked)、ロックの外で用意したジョブのスレッドを起動する (_start_pending)。"""
        outcome = execute_deep_job(self.parts.runner, running.job, running.job_dir)
        with self._lock:
            if running.ended:
                # kill した後に戻った。deep_end と保留中の要求の起動の用意は、kill のときに済ませてある
                return
            pending_job = self._finish_locked(
                running, outcome.status, outcome.message, self.parts.clock.now()
            )
        self._start_pending(pending_job)

    def _finish_locked(
        self, running: _RunningJob, status: DeepJobStatus, message: str, now: float
    ) -> _RunningJob | None:
        """ジョブを終わったものにし、同じ文書の保留中の要求の起動を用意して、そのジョブを返す (ロックの中で呼ぶ)。

        実行中から外し、前回の終了を (now, status) にして、deep_end を書いてから、同じロックの区間のまま
        _take_pending_job_locked(key, now) を呼び、その結果 (起動を用意したジョブ。用意しなければ None) を返す。
        返したジョブのスレッドは、呼び出し側がロックの外で _start_pending で起動する。
        """
        running.ended = True
        if running in self._running:
            self._running.remove(running)
        key = running.job.key
        self._last_end[key] = DeepJobEnd(now, status)
        self.parts.run_log.write(
            EVENT_DEEP_END,
            **running.fields(),
            status=status.value,
            message=message,
            stop_reason=None
            if running.stop_reason is None
            else running.stop_reason.value,
        )
        return self._take_pending_job_locked(key, now)

    def _mark_stopped_locked(
        self, running: _RunningJob, reason: DeepStopReason, now: float
    ) -> None:
        """stop を呼んだ t と理由を記録し、deep_stop を書く (runner.stop は呼び出し側がロックの外で呼ぶ)。"""
        running.stop_t = now
        running.stop_reason = reason
        self.parts.run_log.write(
            EVENT_DEEP_STOP, **running.fields(), reason=reason.value
        )

    def _mark_killed_locked(
        self, running: _RunningJob, now: float
    ) -> _RunningJob | None:
        """ジョブを kill 済みにして deep_end (status killed) を書き、同じ文書の保留中の要求の起動を用意して、そのジョブを
        返す (_finish_locked)。runner.kill と、返したジョブのスレッドの起動は、呼び出し側がロックの外で行う。"""
        since_stop = 0.0 if running.stop_t is None else now - running.stop_t
        return self._finish_locked(
            running,
            DeepJobStatus.KILLED,
            f"killed {since_stop:.1f} seconds after stop",
            now,
        )

    def _write_skipped_locked(
        self, key: DocumentKey, trigger: DeepTrigger, reason: str, **extra: object
    ) -> None:
        """deep_skipped {"kind", "scene_id", "trigger", "reason", ...extra} を書く。"""
        self.parts.run_log.write(
            EVENT_DEEP_SKIPPED,
            kind=key.kind.value,
            scene_id=key.scene_id,
            trigger=trigger.value,
            reason=reason,
            **extra,
        )

    def _note_surface_request_locked(
        self, key: DocumentKey, trigger: DeepTrigger, outcome: DeepRequestOutcome
    ) -> None:
        """trigger が surface なら、key の surface の最後の要求を outcome にする。ほかのきっかけでは何もしない。"""
        if trigger is DeepTrigger.SURFACE:
            self._last_surface_request[key] = outcome

    def _hold_request_locked(
        self, key: DocumentKey, trigger: DeepTrigger, now: float
    ) -> DeepRequestResult:
        """key のジョブが実行中のときに来た要求を保留する (request の 3)。

        1. key に保留中の要求があれば、それを捨てて、run_log に deep_skipped {"kind", "scene_id", "trigger": 古い要求の
           きっかけ, "reason": SKIP_REPLACED} を書く
        2. key の保留中の要求を _PendingRequest(trigger, now) にし、run_log に deep_pending {"kind", "scene_id",
           "trigger", "since_last_start_s": round_to_milliseconds(now - key の前回の起動の t)} を書く
        3. trigger が surface なら、key の surface の最後の要求を DeepRequestOutcome(now, False, REQUEST_PENDING) にする
        DeepRequestResult(started=False, reason=REQUEST_PENDING) を返す。ジョブのディレクトリーは作らない。
        key のジョブが実行中のときに呼ぶため、key の前回の起動の t はいつもある。
        """
        replaced = self._pending.get(key)
        if replaced is not None:
            self._write_skipped_locked(key, replaced.trigger, SKIP_REPLACED)
        self._pending[key] = _PendingRequest(trigger, now)
        self.parts.run_log.write(
            EVENT_DEEP_PENDING,
            kind=key.kind.value,
            scene_id=key.scene_id,
            trigger=trigger.value,
            since_last_start_s=round_to_milliseconds(now - self._last_start_t[key]),
        )
        self._note_surface_request_locked(
            key, trigger, DeepRequestOutcome(now, False, REQUEST_PENDING)
        )
        return DeepRequestResult(started=False, reason=REQUEST_PENDING)

    def _start_pending(self, pending_job: _RunningJob | None) -> None:
        """保留中の要求から起動を用意したジョブ pending_job (_finish_locked の結果) のスレッドを起動する。

        ロックを持たずに呼ぶ。pending_job が None なら何もしない。次を繰り返す:
        1. ロックの外で start_job_thread(ジョブの本体) を呼ぶ。起動できたら戻る
        2. 例外 (Exception) を出したら、1 つのロックの区間で、そのジョブを実行中から外し、前回の終了を
           (その時刻, DeepJobStatus.ERROR) にし、run_log に deep_end {"kind", "scene_id", "trigger", "job",
           "status": "error", "message": 説明, "stop_reason": null} を書き、同じ文書の保留中の要求 (その間にほかの
           スレッドが保留したもの) の起動を用意する (_finish_locked)。用意したジョブがあれば 1 に戻り、無ければ戻る
        保留からの起動には例外を受け取る呼び出し元が無いため、スレッドを起動できなくても例外を外に出さず、run.jsonl に書く。
        """
        while pending_job is not None:
            try:
                self.parts.start_job_thread(partial(self._run_job, pending_job))
            except Exception as error:
                logger.warning(
                    "starting pending deep job %s failed",
                    pending_job.job_dir,
                    exc_info=True,
                )
                with self._lock:
                    pending_job = self._finish_locked(
                        pending_job,
                        DeepJobStatus.ERROR,
                        describe_exception(error),
                        self.parts.clock.now(),
                    )
            else:
                return

    def _take_pending_job_locked(
        self, key: DocumentKey, now: float
    ) -> _RunningJob | None:
        """key の保留中の要求を取り出して起動の用意をし、起動するジョブを返す (start_job_thread は呼び出し側が呼ぶ)。

        ジョブの終わりを記録したのと同じロックの区間で、_finish_locked から呼ぶ。
        close の後、key のジョブが実行中、または key に保留中の要求が無ければ、何もせずに None を返す。
        そうでなければ、保留中の要求 (trigger, requested_t) を取り出し (key の保留中の要求は無くなる)、次を行う:
        1. map_layout = deep_job_directory.read_job_map_layout(notes/, key.kind) を 1 回だけ求め (このときの
           ゲームの性質の文書から決める。判定が付かなければ map.json の型、それも無ければ free。map 以外は None)、DeepJob(key.kind, key.scene_id, trigger,
           requested_t=保留した要求の t, previous_latest_frame_t=deep_job_directory.find_previous_latest_frame_t(
           paths, key, map_layout) (終わったばかりのジョブが正常に終わっていれば、その latest_frame_t。map の文書で、
           map.json の layout が map_layout と違えば None), latest_frame_t=latest_frame_t(), map_layout=map_layout) を
           作り、prepare_job_directory(paths, job) でディレクトリーを用意する。最小の起動間隔は確かめ直さない (保留した要求は、前回の起動から
           min_start_interval_s 秒たった後に来たものであるため)
        2. 1 が例外 (Exception) を出したら、key の前回の終了を (now, DeepJobStatus.ERROR) にし (前回の起動の t は
           変えない)、run_log に deep_request_error {"source": "pending", "kind", "trigger": 保留した要求のきっかけ,
           "scene_id", "error": 説明} を書いて、None を返す (例外は外に出さない)
        3. key の前回の起動の t を now にし、実行中に数え、run_log に deep_start {"kind", "scene_id", "trigger": 保留した
           要求のきっかけ, "job", "pending_t": 保留した要求の t} を書いて、そのジョブ (_RunningJob。pending_t は保留した
           要求の t) を返す
        surface の最後の要求は変えない。
        """
        if self._closed or self._is_running_locked(key):
            return None
        pending = self._pending.pop(key, None)
        if pending is None:
            return None
        parts = self.parts
        try:
            map_layout = read_job_map_layout(parts.paths.notes_dir, key.kind)
            job = DeepJob(
                kind=key.kind,
                scene_id=key.scene_id,
                trigger=pending.trigger,
                requested_t=pending.requested_t,
                previous_latest_frame_t=find_previous_latest_frame_t(
                    parts.paths, key, map_layout
                ),
                latest_frame_t=parts.latest_frame_t(),
                map_layout=map_layout,
            )
            job_dir = parts.prepare_job_directory(parts.paths, job)
        except Exception as error:
            logger.debug("preparing pending deep job failed", exc_info=True)
            # request の失敗と同じく前回の終了として扱い、自動の呼び出しが同じ失敗をすぐ繰り返さないようにする
            self._last_end[key] = DeepJobEnd(now, DeepJobStatus.ERROR)
            parts.run_log.write(
                EVENT_DEEP_REQUEST_ERROR,
                source="pending",
                kind=key.kind.value,
                trigger=pending.trigger.value,
                scene_id=key.scene_id,
                error=describe_exception(error),
            )
            return None
        running = _RunningJob(
            job,
            job_dir,
            parts.paths.relative(job_dir),
            now,
            pending_t=pending.requested_t,
        )
        self._add_running_locked(running)
        return running

    def _discard_all_pending_locked(self) -> None:
        """保留中の要求をすべて捨てる (close の 1)。

        捨てた要求ごとに、run_log に deep_skipped {"kind", "scene_id", "trigger": 保留中の要求のきっかけ,
        "reason": SKIP_CLOSED} を書く。surface の最後の要求は変えない。
        """
        for key, pending in self._pending.items():
            self._write_skipped_locked(key, pending.trigger, SKIP_CLOSED)
        self._pending.clear()

    def _is_running_locked(self, key: DocumentKey) -> bool:
        """文書 key のジョブが実行中なら True を返す (ロックの中で呼ぶ)。"""
        return any(running.job.key == key for running in self._running)

    def _add_running_locked(self, running: _RunningJob) -> None:
        """起動を用意したジョブを実行中に数える (ロックの中で呼ぶ)。

        文書の前回の起動の t を running.started_t にし、実行中に足し、run_log に deep_start {"kind", "scene_id",
        "trigger", "job", "pending_t": running.pending_t} を書く。
        """
        self._last_start_t[running.job.key] = running.started_t
        self._running.append(running)
        self.parts.run_log.write(
            EVENT_DEEP_START, **running.fields(), pending_t=running.pending_t
        )

    def _auto_due_locked(
        self, kind: DocumentKind, key: DocumentKey, now: float
    ) -> bool:
        """種類 kind の対象 key を自動で呼ぶ時刻になっていれば True を返す (check_auto の 2〜4)。"""
        target = self._auto_targets.get(kind)
        if target is None:
            since = self._created_t
        elif target[0] != key:
            since = now
        else:
            since = target[1]
        self._auto_targets[kind] = (key, since)
        if any(running.job.key == key for running in self._running):
            return False
        last_end = self._last_end.get(key)
        base_t = since if last_end is None else last_end.t
        return now - base_t >= self.settings.auto_seconds

    def _has_running_jobs(self) -> bool:
        """実行中のジョブが 1 つ以上あれば True を返す。"""
        with self._lock:
            return bool(self._running)

    def _run_checks(self) -> None:
        """start で起動するスレッドの本体。close まで CHECK_INTERVAL_S ごとに check_timeouts・check_auto・
        check_progress を呼ぶ。"""
        while not self._stop_checks.wait(CHECK_INTERVAL_S):
            try:
                self.check_timeouts()
            except Exception:
                logger.warning("checking deep job timeouts failed", exc_info=True)
            try:
                self.check_auto()
            except Exception:
                logger.warning("checking automatic deep requests failed", exc_info=True)
            try:
                self.check_progress()
            except Exception:
                logger.warning("checking progress deep requests failed", exc_info=True)


def _call_runner(action: Callable[[Path], None], job_dir: Path) -> None:
    """runner.stop か runner.kill を呼ぶ。例外は外に出さず、logging で警告を書く。"""
    try:
        action(job_dir)
    except Exception:
        logger.warning("stopping deep job %s failed", job_dir, exc_info=True)


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
    3. map_layout = deep_job_directory.read_job_map_layout(notes/, kind) (map ならゲームの性質の文書から決めた型。
       判定が付かなければ map.json の型、それも無ければ free。map 以外は None) を 1 回だけ求め、DeepJob(kind, document_key(kind, scene_id).scene_id, MANUAL,
       requested_t=now, previous_latest_frame_t=find_previous_latest_frame_t(paths, key, map_layout) (同じ文書の、
       前回の正常に終わったジョブの latest_frame_t。map の文書で、map.json の layout が map_layout と違えば None),
       latest_frame_t=frames.jsonl の最新の t (無ければ None), map_layout=map_layout) で create_job_directory →
       execute_deep_job を呼び、結果を返す
    最小の起動間隔とタイムアウトは確かめない (run が戻るまで待つ)。run.jsonl には書かない。
    run_dir がディレクトリーでなければ FileNotFoundError、scene_id が場面 ID の形でなければ ValueError にする。
    """
    paths = open_run_directory(run_dir)
    if scene_id is not None and not is_scene_id(scene_id):
        raise ValueError(f"invalid scene id: {scene_id!r}")
    if scene_id is None and kind in SCENE_DOCUMENT_KINDS:
        scene_id = read_surface_context(paths.middle_state).scene_id
    key = document_key(kind, scene_id)
    latest = latest_frame_t(paths)
    map_layout = read_job_map_layout(paths.notes_dir, kind)
    job = DeepJob(
        kind=kind,
        scene_id=key.scene_id,
        trigger=DeepTrigger.MANUAL,
        requested_t=latest if latest is not None else 0.0,
        previous_latest_frame_t=find_previous_latest_frame_t(paths, key, map_layout),
        latest_frame_t=latest,
        map_layout=map_layout,
    )
    job_dir = create_job_directory(paths, job)
    outcome = execute_deep_job(runner, job, job_dir)
    return DeepOnceResult(job_dir=job_dir, job=paths.relative(job_dir), outcome=outcome)
