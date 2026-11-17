"""委任型の委任のループ: Jev への要求 1 回 (ask_delegate) と、今の委任の状態 (ActiveDelegation)。

委任は、主ループがリアルタイムの場面の操作を Jev に任せること。同時に 1 つだけ動かす。
OrchestratorController (OrchestratorSettings.delegate があるとき) の run のループが委任のループで、委任中に、返っていない
要求が無く、前の要求の応答 (または失敗) を受け取った時刻 (DelegateRequestResult.t_received) から
DelegateSettings.request_interval_s 秒たったら、別のスレッドで ask_delegate を 1 回呼ぶ (委任を始めた後の最初の要求は
待たずに送る)。応答が返ったら、ループが ActiveDelegation に答えか失敗を渡し、選んだ行動のマクロを始めるか、実行中の
行動を続けるか、操作権を主ループに返す。
Jev の送り先は、監視と同じく monitor_loop.MonitorAsker の形 (run では surface.jev_client.JevClient、テストでは偽物)。
画像 (select_delegate_frames):
    frames/latest/ の最新の一定間隔のフレーム (frames.latest_frames.LatestFrames.snapshot。最大 5 枚) と同じ時刻の、
    キャプチャーの元のフレーム (frames.frame_buffer.FrameBuffer.frames) を古い順に選ぶ。元のフレームが FrameBuffer に
    残っていない時刻は飛ばす (送る画像はある分だけ)。1 枚も無ければ要求を送らない (ループが次の回りで確かめ直す)。
    選んだフレームは safety.safety_monitor.build_safety_image(フレーム, DelegateSettings.image_width,
    DelegateSettings.jpeg_quality) で JPEG にする (時刻や枠は書き込まない。幅が image_width より大きければ縮小する)。
    時刻は state の frames のラベルで伝える (orchestrator.delegate_questions)。
委任の通し番号 (serial):
    OrchestratorController は、委任の指示 (start・update・stop) を当てるたびと、委任を終えるたびに通し番号を 1 進める。
    要求の結果 (DelegateRequestResult.serial) は要求を送ったときの番号を持ち、今の番号と同じときだけ答えと失敗を使う
    (違えば、要求を送った後に委任を始め直した・置き換えた・終えたので、応答を使わない)。
ActiveDelegation は I/O・スレッド・時刻を扱わない (ループのロックの中で使う)。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY
from switch2_layers.frames.latest_frames import LatestFrame
from switch2_layers.orchestrator.decision_types import DelegateStart, DelegateUpdate
from switch2_layers.orchestrator.monitor_loop import MonitorAsker
from switch2_layers.orchestrator.orchestrator_events import (
    DelegateEnd,
    DelegatePick,
    DelegationStatus,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.surface.jev_client import JevAnswers

# Jev に送る画像の幅の既定値と範囲 (--delegate-image-width)。下限は、Jev が受け付ける画像の各辺の下限 280 ピクセルを、
# 16:9 の高さで満たす幅
DEFAULT_DELEGATE_IMAGE_WIDTH = 1280
DELEGATE_IMAGE_WIDTH_MIN = 498
DELEGATE_IMAGE_WIDTH_MAX = 1280
# Jev の失敗がこの回数続いたら操作権を主ループに返す回数の既定値 (--max-delegate-errors)
DEFAULT_MAX_DELEGATE_ERRORS = 5
# 応答 (または失敗) を受け取ってから、次の要求を送るまでの秒数
DELEGATE_REQUEST_INTERVAL_S = 0.2
# Jev が「操作を返す」をこの回数続けて選んだら、操作権を主ループに返す
RETURN_CONTROL_STREAK = 2
# 主ループが delegate の stop を書いて委任を終えたときの DelegateEnd.detail
MAIN_STOP_DETAIL = "the main loop wrote the delegate stop"
# 実行全体を止めて委任を終えたときの、run.jsonl の delegate_end の detail (理由は stop)
RUN_STOP_DETAIL = "the run stopped"

# delegate.jsonl に書く、要求の応答 (または失敗) を受けて行った処理:
#   started    選んだ行動のマクロを始めた (実行中の行動の残りは捨てた)
#   continued  実行中の行動を続けた (none・1 回目の return_control・数に満たない失敗)
#   returned   操作権を主ループに返した (return_control が続いた・失敗が続いた)
#   ignored    応答を使わなかった (要求を送った後に委任を始め直した・置き換えた・終えた)
DelegateHandling = Literal["started", "continued", "returned", "ignored"]


@dataclass(frozen=True)
class DelegateSettings:
    """委任のループの設定 (OrchestratorSettings.delegate)。

    image_width          Jev に送る画像の幅 (--delegate-image-width。DELEGATE_IMAGE_WIDTH_MIN〜DELEGATE_IMAGE_WIDTH_MAX)
    max_errors           Jev の失敗がこの回数続いたら操作権を主ループに返す (--max-delegate-errors。1 以上)
    request_interval_s   応答 (または失敗) を受け取ってから、次の要求を送るまでの秒数
    jpeg_quality         Jev に送る画像の JPEG の品質
    """

    image_width: int = DEFAULT_DELEGATE_IMAGE_WIDTH
    max_errors: int = DEFAULT_MAX_DELEGATE_ERRORS
    request_interval_s: float = DELEGATE_REQUEST_INTERVAL_S
    jpeg_quality: int = DEFAULT_JPEG_QUALITY


@dataclass(frozen=True)
class DelegateRequestResult:
    """委任の要求 1 回の結果。

    serial          要求を送ったときの委任の通し番号 (モジュールの docstring の「委任の通し番号」)
    frame_ts        送った画像の元のフレームの時刻 (古い順)
    option_names    その要求で送った (送ろうとした) 選択肢の名前 (delegate_questions.delegate_option_names(choices) の
                    順)。失敗した要求でも入れる。delegate.jsonl の chosen は、この名前と probabilities で決める
                    (要求を送った後に update で choices を置き換えても、応答の確率と食い違わないため)
    t_sent          ask を呼んだ時刻
    t_received      ask が戻った (または例外を出した) 時刻
    probabilities   選択肢の名前 → 確率 (option_names の順)。失敗したら None
    usage           Jev の応答の usage。無いか、失敗したら None
    error           失敗の説明。答えがあれば None
    """

    serial: int
    frame_ts: tuple[float, ...]
    option_names: tuple[str, ...]
    t_sent: float
    t_received: float
    probabilities: Mapping[str, float] | None
    usage: object | None
    error: str | None


@dataclass(frozen=True)
class DelegateAnswer:
    """答えのあった要求を ActiveDelegation.record_answer に渡した結果。

    chosen         選んだ選択肢の名前 (delegate_questions.choose_option)
    start_action   始める行動の名前 (マクロの名前)。行動を選んでいなければ None
    end            操作権を主ループに返すなら、その DelegateEnd (理由 jev_return)。返さなければ None
    """

    chosen: str
    start_action: str | None
    end: DelegateEnd | None


def select_delegate_frames(
    latest: Sequence[LatestFrame], buffered: Sequence[CapturedFrame]
) -> list[CapturedFrame]:
    """Jev に送る元のフレームを、古い順に返す (モジュールの docstring の「画像」。I/O なし)。

    latest は frames/latest/ の最新のフレーム (新しい順。LatestFrames.snapshot の値)、buffered はキャプチャーの元の
    フレーム (古い順。FrameBuffer.frames の値)。latest の各フレームと時刻 (t) がちょうど同じフレームを buffered から
    選ぶ。buffered に無い時刻は飛ばす。latest か buffered が空なら空のリスト。
    """
    raise NotImplementedError("select_delegate_frames")


def ask_delegate(
    asker: MonitorAsker,
    values: DelegateStart,
    recent_picks: Sequence[DelegatePick],
    frames: Sequence[CapturedFrame],
    serial: int,
    clock: RunClock,
    image_width: int,
    jpeg_quality: int,
    make_reference: Callable[[], str],
) -> DelegateRequestResult:
    """委任の通し番号 serial の要求を 1 回送り、結果を返す (結果の serial は引数の値、frame_ts は frames の時刻、
    option_names は delegate_questions.delegate_option_names(values.choices)。失敗した結果でも同じ)。

    1. frames (古い順) の各フレームについて、jpeg = safety.safety_monitor.build_safety_image(フレーム, image_width,
       jpeg_quality)、reference = make_reference() とする
    2. t_sent = clock.now() で asker.ask(delegate_questions.build_delegate_state(values.tasks, values.cautions,
       recent_picks, [DelegateStateFrame(フレームの t, reference), ...]),
       [delegate_questions.build_delegate_question(values.choices)], [JevImage(reference, jpeg), ...]) を呼び
       (画像は frames の順)、戻ったら (例外でも) t_received = clock.now()
    3. 答えから、read_delegate_probabilities(答え, option_names) で選択肢の順の確率を取り出す
    1〜3 のどこで出た例外 (Exception。画像の作成と参照文字列の作成の失敗、jev_client.JevError、それ以外の ask の例外を
    含む) も、答えに問いか選択肢の確率が無いことも失敗とし、例外を出さずに、probabilities と usage を None、error を
    error_text.describe_exception(例外) にした結果を返す。ask を呼ぶ前に失敗したときは、t_sent と t_received を、
    失敗したときの clock.now() にする。frames が空なら ValueError を出す (呼び出し側は空なら要求を送らない)。
    """
    raise NotImplementedError("ask_delegate")


def read_delegate_probabilities(
    answers: JevAnswers, option_names: Sequence[str]
) -> dict[str, float]:
    """答え answers から、委任の問いの選択肢の確率を、option_names の順の辞書で返す。

    問いの答え (choices[delegate_questions.DELEGATE_QUESTION_NAME]) が無いか、どれかの選択肢の確率が無ければ ValueError。
    """
    raise NotImplementedError("read_delegate_probabilities")


def delegate_return_detail(count: int) -> str:
    """Jev が「操作を返す」を count 回続けて選んで委任を終えたときの DelegateEnd.detail
    `jev chose return_control <count> times in a row` を返す。"""
    raise NotImplementedError("delegate_return_detail")


def delegate_failure_detail(count: int, last_error: str) -> str:
    """Jev の失敗が count 回続いて委任を終えたときの DelegateEnd.detail `jev failed <count> times in a row: <last_error>`
    を返す。"""
    raise NotImplementedError("delegate_failure_detail")


class ActiveDelegation:
    """今の委任 1 つの状態 (委任の値、直近に選んで始めた行動、「操作を返す」と失敗の続いた回数)。

    values       委任の値 (タスク・注意事項・行動)
    started_t    委任を始めた時刻
    max_errors   Jev の失敗がこの回数続いたら操作権を返す (DelegateSettings.max_errors)
    """

    def __init__(
        self, values: DelegateStart, started_t: float, max_errors: int
    ) -> None:
        """直近の行動なし、「操作を返す」と失敗の回数 0 で作る。max_errors が 1 未満なら ValueError。"""
        raise NotImplementedError("ActiveDelegation")

    @property
    def values(self) -> DelegateStart:
        """今の委任の値 (update を当てた後の値)。"""
        raise NotImplementedError("ActiveDelegation.values")

    @property
    def started_t(self) -> float:
        """委任を始めた時刻。"""
        raise NotImplementedError("ActiveDelegation.started_t")

    def update(self, update: DelegateUpdate) -> None:
        """update の None でない欄で、委任の値を置き換える。直近の行動と回数は変えない (実行中の行動も止めない)。"""
        raise NotImplementedError("ActiveDelegation.update")

    def recent_picks(self) -> tuple[DelegatePick, ...]:
        """今の委任で直近に選んで始めた行動を、古い順に返す (最大 delegate_questions.MAX_RECENT_PICKS 件)。"""
        raise NotImplementedError("ActiveDelegation.recent_picks")

    def record_answer(
        self, probabilities: Mapping[str, float], t: float
    ) -> DelegateAnswer:
        """答えのあった要求を記録し、どうするかを返す。t は行動を始める時刻 (今の時刻)。

        1. chosen = delegate_questions.choose_option(probabilities, delegate_option_names(values.choices))
        2. 失敗の回数を 0 に戻す
        3. chosen が
             行動の名前              「操作を返す」の回数を 0 に戻し、DelegatePick(chosen, t) を直近の行動に足して、
                                    DelegateAnswer(chosen, start_action=chosen, end=None) を返す
             NONE_OPTION            「操作を返す」の回数を 0 に戻し、DelegateAnswer(chosen, None, None) を返す
             RETURN_CONTROL_OPTION  「操作を返す」の回数を 1 足す。RETURN_CONTROL_STREAK 回になったら
                                    DelegateAnswer(chosen, None, DelegateEnd("jev_return", delegate_return_detail(回数)))、
                                    ならなければ DelegateAnswer(chosen, None, None) を返す
        probabilities にどれかの選択肢の名前が無ければ KeyError。呼び出し側は、今の委任と同じ通し番号で送った要求の
        確率だけを渡す (そのとき、要求で送った選択肢の名前は、今の値の選択肢の名前と同じ)。
        """
        raise NotImplementedError("ActiveDelegation.record_answer")

    def record_failure(self, error: str) -> DelegateEnd | None:
        """失敗した要求を記録する。失敗の回数を 1 足し、max_errors 回になったら
        DelegateEnd("errors", delegate_failure_detail(回数, error)) を返す。ならなければ None。

        「操作を返す」の回数は変えない (失敗は選択肢を選んだことにしない)。
        """
        raise NotImplementedError("ActiveDelegation.record_failure")

    def status(self) -> DelegationStatus:
        """DelegationStatus(active=True, started_t, values, last_pick=直近の行動の最も新しいもの (無ければ None),
        return_streak=「操作を返す」の続いた回数) を返す。"""
        raise NotImplementedError("ActiveDelegation.status")
