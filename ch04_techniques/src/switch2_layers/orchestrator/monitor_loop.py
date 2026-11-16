"""Orchestrator 型の監視: Jev への要求 1 回 (ask_monitor) と、今の監視の状態 (ActiveMonitor)。

監視は同時に 1 つだけ動かす。OrchestratorController の run のループが、要求を出せるとき (主ループの操作の残りが無い、
監視の操作を実行していない、返っていない要求が無い、など) に、別のスレッドで ask_monitor を 1 回呼ぶ。応答が返ったら、
ループが ActiveMonitor に答えか失敗を渡し、当たった条件の操作を始める。
監視の世代:
    OrchestratorController は、MonitorCreate を当てるたびに、1 から始まる連番の世代を振る (同じ名前・同じ条件の監視を
    作り直したときも、新しい番号にする)。要求の結果 (MonitorRequestResult.generation) は、要求を送ったときの監視の
    世代を持ち、今の監視 (ActiveMonitor.generation) と番号が同じときだけ、当たりと失敗の回数に使う。監視を
    MonitorCreate の値の等しさでは見分けない。
ActiveMonitor は I/O・スレッド・時刻を扱わない (ループのロックの中で使う)。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.orchestrator.action_executor import PrimitiveAction
from switch2_layers.orchestrator.decision_types import MonitorCreate
from switch2_layers.orchestrator.orchestrator_events import MonitorEnd
from switch2_layers.run_clock import RunClock
from switch2_layers.surface.jev_client import JevAnswers, JevImage
from switch2_layers.surface.surface_questions import SurfaceQuestion


class MonitorAsker(Protocol):
    """監視の要求の送り先。run では surface.jev_client.JevClient、テストでは偽物を渡す。"""

    def ask(
        self,
        state: Mapping[str, object],
        questions: Sequence[SurfaceQuestion],
        images: Sequence[JevImage],
    ) -> JevAnswers:
        """要求を 1 回送り、答えを返す。失敗したら例外 (JevClient では jev_client.JevError) を出す。"""
        ...


@dataclass(frozen=True)
class MonitorRequestResult:
    """監視の要求 1 回の結果。

    monitor         要求を送った監視
    generation      要求を送った監視の世代 (モジュールの docstring の「監視の世代」)
    frame_t         送ったフレームの時刻
    t_sent          ask を呼んだ時刻
    t_received      ask が戻った (または例外を出した) 時刻
    probabilities   選択肢の名前 → 確率 (monitor_questions.monitor_option_names の順)。失敗したら None
    usage           Jev の応答の usage。無いか、失敗したら None
    error           失敗の説明。答えがあれば None
    """

    monitor: MonitorCreate
    generation: int
    frame_t: float
    t_sent: float
    t_received: float
    probabilities: Mapping[str, float] | None
    usage: object | None
    error: str | None


def ask_monitor(
    asker: MonitorAsker,
    monitor: MonitorCreate,
    generation: int,
    frame: CapturedFrame,
    clock: RunClock,
    image_width: int,
    jpeg_quality: int,
    make_reference: Callable[[], str],
) -> MonitorRequestResult:
    """世代 generation の監視 monitor の要求を 1 回送り、結果を返す (結果の generation は引数の値)。

    1. jpeg = safety.safety_monitor.build_safety_image(frame, image_width, jpeg_quality)、reference = make_reference()
    2. t_sent = clock.now() で asker.ask(monitor_questions.build_monitor_state(reference),
       [monitor_questions.build_monitor_question(monitor)], [JevImage(reference, jpeg)]) を呼び、戻ったら (例外でも)
       t_received = clock.now()
    3. 答えの choices[MONITOR_QUESTION_NAME] から、選択肢の順の確率を取り出す
    1〜3 のどこで出た例外 (Exception。画像の作成と参照文字列の作成の失敗、jev_client.JevError、それ以外の ask の例外を
    含む) も、答えに問いか選択肢の確率が無いことも失敗とし、例外を出さずに、probabilities と usage を None、error を
    error_text.describe_exception(例外) にした結果を返す。ask を呼ぶ前に失敗したときは、t_sent と t_received を、
    失敗したときの clock.now() にする。
    """
    raise NotImplementedError


def monitor_failure_detail(count: int, last_error: str) -> str:
    """Jev の失敗が count 回続いて監視を止めたときの MonitorEnd.detail `jev failed <count> times in a row: <last_error>`。"""
    raise NotImplementedError


def monitor_completed_detail(case_index: int, condition: str) -> str:
    """once の条件 case_index の操作を実行し終えて監視を終えたときの MonitorEnd.detail
    `case C<case_index> ran once: <condition>`。"""
    raise NotImplementedError


class ActiveMonitor:
    """今の監視 1 つの状態 (失敗が続いた回数と、条件ごとの操作の列)。

    monitor           監視の指示
    generation        監視の世代 (モジュールの docstring の「監視の世代」)
    case_operations   条件ごとの、展開した操作の列 (action_executor.expand_actions の値。監視を作ったときのマクロで展開する)
    threshold         当たりのしきい値 (monitor_questions.find_hit)
    max_errors        Jev の失敗がこの回数続いたら監視を止める
    """

    def __init__(
        self,
        monitor: MonitorCreate,
        generation: int,
        case_operations: Sequence[tuple[PrimitiveAction, ...]],
        threshold: float,
        max_errors: int,
    ) -> None:
        """失敗の回数 0 で作る。case_operations の数が条件の数と違えば ValueError。"""
        raise NotImplementedError

    @property
    def monitor(self) -> MonitorCreate:
        """監視の指示。"""
        raise NotImplementedError

    @property
    def generation(self) -> int:
        """監視の世代。"""
        raise NotImplementedError

    def record_answer(self, probabilities: Mapping[str, float]) -> int | None:
        """答えのあった要求を記録する。失敗の回数を 0 に戻し、当たった条件の位置 (monitor_questions.find_hit) を返す。"""
        raise NotImplementedError

    def record_failure(self, error: str) -> MonitorEnd | None:
        """失敗した要求を記録する。失敗の回数を 1 足し、max_errors 回になったら
        MonitorEnd(name, "failed", monitor_failure_detail(回数, error)) を返す。ならなければ None。"""
        raise NotImplementedError

    def operations_of(self, case_index: int) -> tuple[PrimitiveAction, ...]:
        """条件 case_index の展開した操作の列を返す。

        空のこともある (条件の actions が空のときと、展開すると空になるマクロのとき)。空なら、呼び出し側は列を始めずに、
        その場で case_finished を呼ぶ。
        """
        raise NotImplementedError

    def case_finished(self, case_index: int) -> MonitorEnd | None:
        """条件 case_index の操作の列を最後まで実行し終えたことを記録する。

        その条件が once なら MonitorEnd(name, "completed", monitor_completed_detail(case_index, 条件の文)) を返し、
        every なら None を返す。
        """
        raise NotImplementedError
