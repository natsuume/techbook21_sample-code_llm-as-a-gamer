"""委任のループの Jev への要求 1 回 (ask_delegate)・送る元のフレームの選び方・今の委任の状態 (ActiveDelegation) を、
偽の Jev の送り先と偽の時計で確かめる (orchestrator.delegate_loop)。"""

from collections.abc import Mapping, Sequence

import cv2
import numpy as np
import pytest

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY
from switch2_layers.frames.latest_frames import LatestFrame
from switch2_layers.orchestrator.decision_types import (
    DelegateChoice,
    DelegateStart,
    DelegateUpdate,
)
from switch2_layers.orchestrator.delegate_loop import (
    DEFAULT_DELEGATE_IMAGE_WIDTH,
    DEFAULT_MAX_DELEGATE_ERRORS,
    DELEGATE_IMAGE_WIDTH_MAX,
    DELEGATE_IMAGE_WIDTH_MIN,
    DELEGATE_REQUEST_INTERVAL_S,
    RETURN_CONTROL_STREAK,
    ActiveDelegation,
    DelegateAnswer,
    ask_delegate,
    delegate_failure_detail,
    delegate_return_detail,
    order_chosen_counts,
    select_delegate_frames,
)
from switch2_layers.orchestrator.delegate_questions import (
    DELEGATE_QUESTION_NAME,
    DelegateStateFrame,
    build_delegate_question,
    build_delegate_state,
)
from switch2_layers.orchestrator.orchestrator_events import (
    DelegateEnd,
    DelegatePick,
    DelegationStatus,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.safety.safety_monitor import build_safety_image
from switch2_layers.surface.jev_client import JevAnswers, JevError, JevImage
from switch2_layers.surface.surface_questions import SurfaceQuestion

ATTACK = DelegateChoice(name="attack", description="前の敵を攻撃する")
MOVE_UP = DelegateChoice(name="move_up", description="上に 1 マス移動する")
VALUES = DelegateStart(
    tasks=("最重要: HP を 0 にせずに敵をすべて倒す",),
    cautions=("衝撃波は横一列に進む",),
    choices=(ATTACK, MOVE_UP),
)
NV12_640 = np.full((360 * 3 // 2, 640), 128, dtype=np.uint8)
IMAGE_WIDTH = 498


def frame_at(t: float) -> CapturedFrame:
    return CapturedFrame(t=t, nv12=NV12_640)


def latest_at(t: float) -> LatestFrame:
    """frames/latest/ の最新のフレーム (時刻を書き込んだ JPEG の代わりのバイト列)。"""
    return LatestFrame(
        t=t, source_file=f"frames/interval/{round(t * 1000):09d}.jpg", jpeg=b"stamped"
    )


def probabilities(**values: float) -> dict[str, float]:
    """委任の問いの確率 (無い選択肢は 0)。選択肢は VALUES の行動。"""
    names = ("none", "attack", "move_up", "return_control")
    return {name: values.get(name, 0.0) for name in names}


class FakeMonotonic:
    """偽の time.monotonic。now を書き換えて時間を進める。"""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class RecordingAsker:
    """ask の引数を記録し、result を返す (例外なら出す) Jev の送り先の偽物。ask の間に時計を進める。"""

    def __init__(
        self, result: JevAnswers | Exception, monotonic: FakeMonotonic, seconds: float
    ) -> None:
        self.result = result
        self.monotonic = monotonic
        self.seconds = seconds
        self.calls: list[
            tuple[dict[str, object], tuple[SurfaceQuestion, ...], tuple[JevImage, ...]]
        ] = []

    def ask(
        self,
        state: Mapping[str, object],
        questions: Sequence[SurfaceQuestion],
        images: Sequence[JevImage],
    ) -> JevAnswers:
        self.calls.append((dict(state), tuple(questions), tuple(images)))
        self.monotonic.now += self.seconds
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class References:
    """make_reference の偽物。ref-0, ref-1, ... を順に返す。"""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> str:
        reference = f"ref-{self.count}"
        self.count += 1
        return reference


def answers(usage: object = None, **values: float) -> JevAnswers:
    return JevAnswers(
        choices={DELEGATE_QUESTION_NAME: probabilities(**values)},
        nouls={},
        boxes={},
        usage=usage,
    )


def ask(
    result: JevAnswers | Exception,
    frames: Sequence[CapturedFrame],
    picks: Sequence[DelegatePick] = (),
):
    """t=20.0 の偽の時計で ask_delegate を 1 回呼び、(結果, 送り先の偽物) を返す。ask は 0.4 秒かかる。"""
    monotonic = FakeMonotonic(20.0)
    asker = RecordingAsker(result, monotonic, seconds=0.4)
    result_of_ask = ask_delegate(
        asker,
        VALUES,
        picks,
        frames,
        7,
        RunClock(monotonic, origin=0.0),
        IMAGE_WIDTH,
        DEFAULT_JPEG_QUALITY,
        References(),
    )
    return result_of_ask, asker


# --- 設定の値 ---


def test_delegate_constants_have_the_default_values_and_the_range():
    assert DEFAULT_DELEGATE_IMAGE_WIDTH == 1280
    assert DELEGATE_IMAGE_WIDTH_MIN == 498
    assert DELEGATE_IMAGE_WIDTH_MAX == 1280
    assert DEFAULT_MAX_DELEGATE_ERRORS == 5
    assert DELEGATE_REQUEST_INTERVAL_S == 0.2
    assert RETURN_CONTROL_STREAK == 2


# --- 送る元のフレーム ---


def test_delegate_frames_are_the_originals_at_the_times_of_the_latest_frames_oldest_first():
    # 最新のフレームは新しい順 (frames/latest/1.jpg が先頭)、元のフレームは古い順で受け取る
    latest = [latest_at(t) for t in (11.0, 10.8, 10.6, 10.4, 10.2)]
    buffered = [frame_at(round(10.0 + 0.1 * index, 1)) for index in range(11)]
    selected = select_delegate_frames(latest, buffered)
    assert [frame.t for frame in selected] == [10.2, 10.4, 10.6, 10.8, 11.0]


# --- 要求 1 回 ---


def test_ask_delegate_sends_the_state_the_question_and_the_images_oldest_first():
    frames = [frame_at(t) for t in (10.2, 10.4, 10.6)]
    picks = [DelegatePick("attack", 9.5)]
    _, asker = ask(answers(none=1.0), frames, picks)
    [(state, questions, images)] = asker.calls
    assert questions == (build_delegate_question(VALUES.choices),)
    assert [image.reference for image in images] == ["ref-0", "ref-1", "ref-2"]
    assert state == build_delegate_state(
        VALUES.tasks,
        VALUES.cautions,
        picks,
        [
            DelegateStateFrame(10.2, "ref-0"),
            DelegateStateFrame(10.4, "ref-1"),
            DelegateStateFrame(10.6, "ref-2"),
        ],
    )


def test_ask_delegate_images_are_the_original_frames_at_the_delegate_image_width():
    frames = [frame_at(10.2), frame_at(10.4)]
    _, asker = ask(answers(none=1.0), frames)
    [(_, _, images)] = asker.calls
    # 時刻や枠を書き込まず、元のフレームを幅 IMAGE_WIDTH にした JPEG
    assert [image.jpeg for image in images] == [
        build_safety_image(frame, IMAGE_WIDTH, DEFAULT_JPEG_QUALITY) for frame in frames
    ]
    decoded = cv2.imdecode(np.frombuffer(images[0].jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[1] == IMAGE_WIDTH


def test_ask_delegate_returns_the_probabilities_usage_and_times():
    result, _ = ask(
        answers(usage={"tokens": 3}, attack=0.7, none=0.3), [frame_at(10.2)]
    )
    assert result.serial == 7
    assert result.frame_ts == (10.2,)
    assert result.t_sent == 20.0
    assert result.t_received == 20.4
    assert result.probabilities == probabilities(attack=0.7, none=0.3)
    assert list(result.probabilities) == ["none", "attack", "move_up", "return_control"]
    assert result.usage == {"tokens": 3}
    assert result.error is None


def test_ask_delegate_failure_of_jev_is_a_result_with_the_error():
    result, _ = ask(JevError("HTTP 500"), [frame_at(10.2)])
    assert result.probabilities is None
    assert result.usage is None
    assert result.error is not None
    assert "HTTP 500" in result.error
    assert result.t_received == 20.4


def test_ask_delegate_answer_without_the_question_is_a_failure():
    wrong = JevAnswers(choices={"other": {"none": 1.0}}, nouls={}, boxes={})
    result, _ = ask(wrong, [frame_at(10.2)])
    assert result.probabilities is None
    assert result.error is not None


@pytest.mark.parametrize(
    "jev_result",
    [answers(none=1.0), JevError("HTTP 500")],
    ids=["answer", "failure"],
)
def test_ask_delegate_result_has_the_option_names_it_sent(jev_result):
    # 答えでも失敗でも、その要求で送った選択肢の名前を結果に入れる (delegate.jsonl の chosen を決めるため)
    result, _ = ask(jev_result, [frame_at(10.2)])
    assert result.option_names == ("none", "attack", "move_up", "return_control")


# --- 今の委任の状態 ---


def test_delegate_chosen_action_is_started_and_kept_as_a_recent_pick():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    answer = delegation.record_answer(probabilities(attack=0.8, none=0.2), 6.5)
    assert answer == DelegateAnswer(chosen="attack", start_action="attack", end=None)
    assert delegation.recent_picks() == (DelegatePick("attack", 6.5),)


def test_delegate_none_continues_without_starting_an_action():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    answer = delegation.record_answer(probabilities(none=0.9), 6.5)
    assert answer == DelegateAnswer(chosen="none", start_action=None, end=None)
    assert delegation.recent_picks() == ()


def test_delegate_first_return_control_continues():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    answer = delegation.record_answer(probabilities(return_control=0.9), 6.5)
    assert answer == DelegateAnswer(
        chosen="return_control", start_action=None, end=None
    )


def test_delegate_second_return_control_in_a_row_returns_control():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    delegation.record_answer(probabilities(return_control=0.9), 6.5)
    answer = delegation.record_answer(probabilities(return_control=0.9), 6.9)
    assert answer.chosen == "return_control"
    assert answer.start_action is None
    assert answer.end is not None
    assert (answer.end.reason, answer.end.detail) == (
        "jev_return",
        delegate_return_detail(2),
    )


def test_delegate_another_choice_resets_the_return_control_count():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    delegation.record_answer(probabilities(return_control=0.9), 6.5)
    delegation.record_answer(probabilities(none=0.9), 6.9)
    answer = delegation.record_answer(probabilities(return_control=0.9), 7.3)
    assert answer.end is None


def test_delegate_failures_up_to_the_limit_return_control():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=3)
    assert delegation.record_failure("JevError: HTTP 500") is None
    assert delegation.record_failure("JevError: HTTP 500") is None
    end = delegation.record_failure("JevError: timeout")
    assert end is not None
    assert (end.reason, end.detail) == (
        "errors",
        delegate_failure_detail(3, "JevError: timeout"),
    )


def test_delegate_answer_resets_the_failure_count():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=2)
    assert delegation.record_failure("JevError: HTTP 500") is None
    delegation.record_answer(probabilities(none=0.9), 6.5)
    assert delegation.record_failure("JevError: HTTP 500") is None


def test_delegate_update_replaces_only_the_given_values():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    delegation.update(DelegateUpdate(tasks=("敵をすべて倒す", "前に出すぎない")))
    assert delegation.values == DelegateStart(
        tasks=("敵をすべて倒す", "前に出すぎない"),
        cautions=VALUES.cautions,
        choices=VALUES.choices,
    )


def test_delegate_status_has_the_start_time_values_last_pick_and_return_streak():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    delegation.record_answer(probabilities(attack=0.9), 6.5)
    delegation.record_answer(probabilities(return_control=0.9), 6.9)
    assert delegation.status() == DelegationStatus(
        active=True,
        started_t=5.0,
        values=VALUES,
        last_pick=DelegatePick("attack", 6.5),
        return_streak=1,
    )


def test_delegate_recent_picks_keep_the_newest_five():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    for index in range(7):
        name = "attack" if index % 2 == 0 else "move_up"
        delegation.record_answer(probabilities(**{name: 0.9}), 6.0 + index)
    assert [pick.t for pick in delegation.recent_picks()] == [
        8.0,
        9.0,
        10.0,
        11.0,
        12.0,
    ]


def test_delegate_end_details_name_the_count_and_the_last_error():
    assert delegate_return_detail(2) == "jev chose return_control 2 times in a row"
    assert (
        delegate_failure_detail(5, "JevError: HTTP 500")
        == "jev failed 5 times in a row: JevError: HTTP 500"
    )


# --- 委任で Jev が選んだ回数 ---

# update で置き換える行動 (置き換えた後の選択肢は none・dodge・return_control)
DODGE = DelegateChoice(name="dodge", description="上に動いて衝撃波をよける")


def dodge_probabilities(**values: float) -> dict[str, float]:
    """行動を DODGE だけに置き換えた後の、委任の問いの確率 (無い選択肢は 0)。"""
    names = ("none", "dodge", "return_control")
    return {name: values.get(name, 0.0) for name in names}


def test_order_chosen_counts_lists_every_option_then_the_names_no_longer_in_the_options():
    counts = {"move_up": 2, "none": 3, "dodge": 1, "old_attack": 4}
    assert order_chosen_counts(
        counts, ("none", "attack", "dodge", "return_control")
    ) == (
        ("none", 3),
        ("attack", 0),
        ("dodge", 1),
        ("return_control", 0),
        # 選択肢に無い名前は、counts の順 (最初に選んだ順)
        ("move_up", 2),
        ("old_attack", 4),
    )


def test_order_chosen_counts_without_any_choice_is_every_option_with_zero():
    assert order_chosen_counts({}, ("none", "attack", "return_control")) == (
        ("none", 0),
        ("attack", 0),
        ("return_control", 0),
    )


def test_delegate_chosen_counts_start_at_zero_for_every_option():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    assert delegation.chosen_counts() == (
        ("none", 0),
        ("attack", 0),
        ("move_up", 0),
        ("return_control", 0),
    )
    assert delegation.failure_count == 0


def test_delegate_chosen_counts_count_each_answer_including_the_first_return_control():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    delegation.record_answer(probabilities(attack=0.9), 6.0)
    delegation.record_answer(probabilities(none=0.9), 6.5)
    delegation.record_answer(probabilities(none=0.9), 7.0)
    # 1 回目の「操作を返す」(実行中の行動を続ける) も数える
    delegation.record_answer(probabilities(return_control=0.9), 7.5)
    delegation.record_answer(probabilities(move_up=0.9), 8.0)
    assert delegation.chosen_counts() == (
        ("none", 2),
        ("attack", 1),
        ("move_up", 1),
        ("return_control", 1),
    )
    assert delegation.failure_count == 0


def test_delegate_failures_are_counted_in_total_apart_from_the_chosen_counts():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    delegation.record_failure("JevError: HTTP 500")
    delegation.record_answer(probabilities(none=0.9), 6.5)
    delegation.record_failure("JevError: HTTP 500")
    # 答えで続いた回数は 0 に戻るが、失敗の合計は戻さない。失敗は選択肢の回数に入れない
    assert delegation.failure_count == 2
    assert delegation.chosen_counts() == (
        ("none", 1),
        ("attack", 0),
        ("move_up", 0),
        ("return_control", 0),
    )


def test_delegate_chosen_counts_put_names_replaced_by_an_update_after_the_options():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    delegation.record_answer(probabilities(move_up=0.9), 6.0)
    delegation.record_answer(probabilities(attack=0.9), 6.5)
    delegation.update(DelegateUpdate(choices=(DODGE,)))
    delegation.record_answer(dodge_probabilities(dodge=0.9), 7.0)
    assert delegation.chosen_counts() == (
        ("none", 0),
        ("dodge", 1),
        ("return_control", 0),
        # 置き換えて選択肢に無くなった名前は、最初に選んだ順に後ろへ
        ("move_up", 1),
        ("attack", 1),
    )


def test_delegate_jev_return_end_has_the_counts_of_the_delegation():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=5)
    delegation.record_answer(probabilities(none=0.9), 6.0)
    delegation.record_failure("JevError: HTTP 500")
    delegation.record_answer(probabilities(return_control=0.9), 6.5)
    answer = delegation.record_answer(probabilities(return_control=0.9), 7.0)
    # 委任を終える答え (2 回目の「操作を返す」) も数える
    assert answer.end == DelegateEnd(
        reason="jev_return",
        detail=delegate_return_detail(2),
        chosen_counts=(
            ("none", 1),
            ("attack", 0),
            ("move_up", 0),
            ("return_control", 2),
        ),
        failure_count=1,
    )


def test_delegate_errors_end_has_the_counts_of_the_delegation():
    delegation = ActiveDelegation(VALUES, started_t=5.0, max_errors=2)
    delegation.record_answer(probabilities(attack=0.9), 6.0)
    delegation.record_failure("JevError: HTTP 500")
    end = delegation.record_failure("JevError: timeout")
    # 委任を終える失敗も数える
    assert end == DelegateEnd(
        reason="errors",
        detail=delegate_failure_detail(2, "JevError: timeout"),
        chosen_counts=(
            ("none", 0),
            ("attack", 1),
            ("move_up", 0),
            ("return_control", 0),
        ),
        failure_count=2,
    )
