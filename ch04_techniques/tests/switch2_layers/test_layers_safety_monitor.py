"""安全の監視 (SafetyMonitor) を、偽物の SafetyChecker と偽物の時計で 1 step ずつ動かす。"""

import dataclasses
import json
import math
import threading
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import (
    DEFAULT_JPEG_QUALITY,
    encode_jpeg,
    nv12_to_bgr,
    shrink_to_width,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.safety.safety_monitor import (
    FIRST_FRAME_POLL_S,
    SAFETY_IMAGE_WIDTH,
    SafetyMonitor,
    SafetyMonitorParts,
    SafetyMonitorSettings,
    SafetyStepOutcome,
    build_safety_image,
    safety_monitor_failure_reason,
)
from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.safety.safety_types import SafetyAnswer
from switch2_layers.system_screen.screen_judgment import SystemScreenJudgment
from switch2_layers.system_screen.system_screen_references import (
    SYSTEM_SCREEN_CHOICE_NAMES,
    SYSTEM_SCREEN_NAMES,
)

THRESHOLD = 0.7
ALL_ZERO = {name: 0.0 for name in SAFETY_QUESTION_NAMES}
RNG = np.random.default_rng(0)
FRAME = CapturedFrame(
    t=1.5, nv12=RNG.integers(0, 256, size=(360 * 3 // 2, 640), dtype=np.uint8)
)


def screen_choices(screen: str | None = None, probability: float = 0.97) -> dict:
    """照合の確率 (選択肢の順)。screen が None なら none を 1.0、そうでなければ screen を probability にする。"""
    values = dict.fromkeys(SYSTEM_SCREEN_CHOICE_NAMES, 0.0)
    if screen is None:
        values["none"] = 1.0
    else:
        values["none"] = round(1.0 - probability, 6)
        values[screen] = probability
    return values


NO_SCREEN = screen_choices()


def answer(
    usage: object = None, screen: dict[str, float] | None = None, **values: float
) -> SafetyAnswer:
    """すべての問いを 0 にし、values の問いだけをその確率にした答え。照合の確率は screen (既定はどの見本でもない)。"""
    return SafetyAnswer(
        probabilities={**ALL_ZERO, **values},
        system_screen=NO_SCREEN if screen is None else screen,
        usage=usage,
    )


class FakeTime:
    """偽物の time.monotonic。now を書き換えて時間を進める。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedChecker:
    """SafetyChecker の偽物。results を順に返す (例外なら出す。最後の結果は繰り返す)。

    check のたびに、受け取った JPEG を記録し、偽物の時計を duration 秒進め、during_check を呼ぶ。
    """

    def __init__(
        self,
        results: list[SafetyAnswer | Exception],
        time: FakeTime | None = None,
        duration: float = 0.0,
        during_check: Callable[[int], None] | None = None,
    ) -> None:
        self.results = list(results)
        self.time = time
        self.duration = duration
        self.during_check = during_check
        self.jpegs: list[bytes] = []

    def check(self, jpeg: bytes) -> SafetyAnswer:
        self.jpegs.append(jpeg)
        if self.time is not None:
            self.time.now += self.duration
        if self.during_check is not None:
            self.during_check(len(self.jpegs))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


class Calls:
    """on_stop・on_start_passed・on_system_screen の呼ばれ方を記録する。"""

    def __init__(self) -> None:
        self.stops: list[str] = []
        self.start_passed = 0
        self.system_screens: list[SystemScreenJudgment] = []

    def on_stop(self, reason: str) -> None:
        self.stops.append(reason)

    def on_start_passed(self) -> None:
        self.start_passed += 1

    def on_system_screen(self, judgment: SystemScreenJudgment) -> None:
        self.system_screens.append(judgment)


def make_monitor(
    tmp_path: Path,
    checker: ScriptedChecker,
    time: FakeTime | None = None,
    latest_frame: Callable[[], CapturedFrame | None] = lambda: FRAME,
    max_errors: int = 3,
    interval_s: float = 1.0,
    on_start_passed: Callable[[], None] | None = None,
    on_system_screen: Callable[[SystemScreenJudgment], None] | None = None,
) -> tuple[SafetyMonitor, Calls]:
    calls = Calls()
    parts = SafetyMonitorParts(
        checker=checker,
        latest_frame=latest_frame,
        clock=RunClock(time or FakeTime(), origin=0.0),
        safety_log=tmp_path / "safety.jsonl",
        on_stop=calls.on_stop,
        on_start_passed=on_start_passed or calls.on_start_passed,
        on_system_screen=on_system_screen or calls.on_system_screen,
    )
    settings = SafetyMonitorSettings(
        interval_s=interval_s,
        threshold=THRESHOLD,
        max_errors=max_errors,
    )
    return SafetyMonitor(parts, settings), calls


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- 設定 ---


def test_settings_have_no_allow_home():
    # --allow-home は押せるボタンだけに使い、安全の監視には渡さない
    names = {field.name for field in dataclasses.fields(SafetyMonitorSettings)}
    assert "allow_home" not in names


# --- 画像 ---


def test_image_is_the_plain_frame_without_time_or_boxes():
    expected = encode_jpeg(
        shrink_to_width(nv12_to_bgr(FRAME.nv12), SAFETY_IMAGE_WIDTH),
        DEFAULT_JPEG_QUALITY,
    ).jpeg
    assert (
        build_safety_image(FRAME, SAFETY_IMAGE_WIDTH, DEFAULT_JPEG_QUALITY) == expected
    )


def test_image_is_shrunk_to_the_width():
    # 1920x1080 のフレームは、縦横比を保って 1280x720 にする
    wide = CapturedFrame(t=0.0, nv12=np.full((1080 * 3 // 2, 1920), 128, np.uint8))
    jpeg = build_safety_image(wide, 1280, DEFAULT_JPEG_QUALITY)
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (720, 1280)


def test_checker_receives_the_plain_latest_frame(tmp_path):
    checker = ScriptedChecker([answer()])
    monitor, _ = make_monitor(tmp_path, checker)
    monitor.step()
    assert checker.jpegs == [
        build_safety_image(FRAME, SAFETY_IMAGE_WIDTH, DEFAULT_JPEG_QUALITY)
    ]


# --- 最初のフレームを待つ ---


def test_no_frame_sends_nothing(tmp_path):
    checker = ScriptedChecker([answer()])
    monitor, calls = make_monitor(tmp_path, checker, latest_frame=lambda: None)
    result = monitor.step()
    assert result.outcome is SafetyStepOutcome.NO_FRAME
    assert result.wait_s == FIRST_FRAME_POLL_S
    assert result.row is None
    assert checker.jpegs == []
    assert not (tmp_path / "safety.jsonl").exists()
    assert calls.stops == []
    assert calls.start_passed == 0
    assert calls.system_screens == []
    assert monitor.start_passed is False


# --- 実行を始めるときの確かめ ---


def test_start_check_passes_and_is_recorded(tmp_path):
    time = FakeTime()
    time.now = 2.0
    checker = ScriptedChecker([answer(usage={"input_tokens": 10})], time, duration=0.5)
    monitor, calls = make_monitor(tmp_path, checker, time)
    result = monitor.step()
    assert result.outcome is SafetyStepOutcome.PASSED
    assert result.stop_reason is None
    assert calls.start_passed == 1
    assert calls.stops == []
    assert monitor.start_passed is True
    [row] = read_rows(tmp_path / "safety.jsonl")
    assert row == result.row
    assert list(row) == [
        "t_sent",
        "t_received",
        "frame_t",
        "phase",
        "probabilities",
        "system_screen",
        "system_screen_result",
        "usage",
        "error",
    ]
    assert row["phase"] == "start"
    assert row["t_sent"] == 2.0
    assert row["t_received"] == 2.5
    assert row["frame_t"] == FRAME.t
    assert row["probabilities"] == ALL_ZERO
    assert list(row["probabilities"]) == list(SAFETY_QUESTION_NAMES)
    assert row["system_screen"] == NO_SCREEN
    assert list(row["system_screen"]) == list(SYSTEM_SCREEN_CHOICE_NAMES)
    assert row["system_screen_result"] is None
    assert row["usage"] == {"input_tokens": 10}
    assert row["error"] is None


def test_start_check_passes_the_system_screen_judgment(tmp_path):
    time = FakeTime()
    time.now = 2.0
    checker = ScriptedChecker([answer()], time, duration=0.5)
    monitor, calls = make_monitor(tmp_path, checker, time)
    monitor.step()
    assert calls.system_screens == [
        SystemScreenJudgment(
            frame_t=FRAME.t, t_received=2.5, screen=None, probabilities=NO_SCREEN
        )
    ]


@pytest.mark.parametrize("name", SYSTEM_SCREEN_NAMES)
def test_start_check_on_a_system_screen_passes_when_the_three_questions_are_low(
    tmp_path, name
):
    # HOME メニューやユーザー選択が映っていても、3 つの問いが超えなければ止めない
    screen = screen_choices(name, 0.98)
    checker = ScriptedChecker([answer(screen=screen)])
    monitor, calls = make_monitor(tmp_path, checker)
    result = monitor.step()
    assert result.outcome is SafetyStepOutcome.PASSED
    assert calls.stops == []
    assert calls.start_passed == 1
    [row] = read_rows(tmp_path / "safety.jsonl")
    assert row["system_screen_result"] == name
    assert row["system_screen"] == screen
    [judgment] = calls.system_screens
    assert judgment.screen == name
    assert judgment.probabilities == screen


def test_start_check_at_exactly_the_system_screen_threshold_has_no_result(tmp_path):
    # いちばん高い user_select がちょうど 0.5 なので、照合の結果は None
    screen = {**dict.fromkeys(SYSTEM_SCREEN_CHOICE_NAMES, 0.0), "none": 0.3}
    screen["user_select"] = 0.5
    screen["controllers"] = 0.2
    checker = ScriptedChecker([answer(screen=screen)])
    monitor, calls = make_monitor(tmp_path, checker)
    assert monitor.step().outcome is SafetyStepOutcome.PASSED
    [row] = read_rows(tmp_path / "safety.jsonl")
    assert row["system_screen_result"] is None
    assert [judgment.screen for judgment in calls.system_screens] == [None]


def test_start_check_over_the_threshold_stops_at_once(tmp_path):
    checker = ScriptedChecker([answer(online=0.71)])
    monitor, calls = make_monitor(tmp_path, checker)
    result = monitor.step()
    assert result.outcome is SafetyStepOutcome.STOP
    assert result.stop_reason == "unsafe screen at start: online"
    assert result.wait_s == 0.0
    assert calls.stops == ["unsafe screen at start: online"]
    assert calls.start_passed == 0
    assert monitor.start_passed is False
    [row] = read_rows(tmp_path / "safety.jsonl")
    assert row["phase"] == "start"
    assert row["probabilities"]["online"] == 0.71


def test_start_check_that_stops_still_passes_the_system_screen_judgment(tmp_path):
    # 止まることを決めた確かめも、答えのあった確かめなので照合の結果を渡す
    screen = screen_choices("home_menu", 0.9)
    checker = ScriptedChecker([answer(screen=screen, purchase=0.9)])
    monitor, calls = make_monitor(tmp_path, checker)
    assert monitor.step().outcome is SafetyStepOutcome.STOP
    assert [judgment.screen for judgment in calls.system_screens] == ["home_menu"]
    [row] = read_rows(tmp_path / "safety.jsonl")
    assert row["system_screen_result"] == "home_menu"


def test_start_check_at_exactly_the_threshold_passes(tmp_path):
    monitor, calls = make_monitor(tmp_path, ScriptedChecker([answer(online=0.7)]))
    assert monitor.step().outcome is SafetyStepOutcome.PASSED
    assert calls.stops == []
    assert calls.start_passed == 1


def test_start_check_failure_stops_at_once(tmp_path):
    checker = ScriptedChecker([RuntimeError("jev is down")])
    monitor, calls = make_monitor(tmp_path, checker)
    result = monitor.step()
    assert result.outcome is SafetyStepOutcome.STOP
    assert calls.stops == ["safety check failed at start"]
    assert calls.start_passed == 0
    assert calls.system_screens == []
    [row] = read_rows(tmp_path / "safety.jsonl")
    assert row["phase"] == "start"
    assert row["probabilities"] is None
    assert row["system_screen"] is None
    assert row["system_screen_result"] is None
    assert row["usage"] is None
    assert "RuntimeError" in row["error"]
    assert "jev is down" in row["error"]


def test_start_check_with_broken_probabilities_is_a_failure(tmp_path):
    broken = SafetyAnswer(
        probabilities={**ALL_ZERO, "online": 1.5}, system_screen=NO_SCREEN
    )
    monitor, calls = make_monitor(tmp_path, ScriptedChecker([broken]))
    assert monitor.step().outcome is SafetyStepOutcome.STOP
    assert calls.stops == ["safety check failed at start"]
    assert calls.system_screens == []
    [row] = read_rows(tmp_path / "safety.jsonl")
    assert row["probabilities"] is None
    assert row["system_screen"] is None
    assert row["error"] is not None


def without(values: dict[str, float], name: str) -> dict[str, float]:
    return {key: value for key, value in values.items() if key != name}


BROKEN_SYSTEM_SCREENS = {
    "no_answer": {},
    "missing_none": without(NO_SCREEN, "none"),
    "missing_reference": without(NO_SCREEN, "software_close"),
    "not_a_number": {**NO_SCREEN, "home_menu": "0.5"},
    "bool": {**NO_SCREEN, "home_menu": True},
    "over_one": {**NO_SCREEN, "controllers": 1.01},
    "below_zero": {**NO_SCREEN, "none": -0.01},
    "nan": {**NO_SCREEN, "user_select": math.nan},
}


@pytest.mark.parametrize(
    "screen", BROKEN_SYSTEM_SCREENS.values(), ids=BROKEN_SYSTEM_SCREENS.keys()
)
def test_start_check_with_a_broken_system_screen_is_a_failure(tmp_path, screen):
    broken = SafetyAnswer(probabilities=ALL_ZERO, system_screen=screen)
    monitor, calls = make_monitor(tmp_path, ScriptedChecker([broken]))
    result = monitor.step()
    assert result.outcome is SafetyStepOutcome.STOP
    assert calls.stops == ["safety check failed at start"]
    assert calls.start_passed == 0
    assert calls.system_screens == []
    [row] = read_rows(tmp_path / "safety.jsonl")
    assert row["probabilities"] is None
    assert row["system_screen"] is None
    assert row["system_screen_result"] is None
    assert row["usage"] is None
    assert row["error"] is not None


# --- 監視の確かめ ---


def test_monitor_stops_when_the_same_question_is_over_twice_in_a_row(tmp_path):
    checker = ScriptedChecker([answer(), answer(online=0.9), answer(online=0.9)])
    monitor, calls = make_monitor(tmp_path, checker)
    assert monitor.step().outcome is SafetyStepOutcome.PASSED
    first = monitor.step()
    assert first.outcome is SafetyStepOutcome.PASSED
    assert calls.stops == []
    second = monitor.step()
    assert second.outcome is SafetyStepOutcome.STOP
    assert second.stop_reason == "unsafe screen: online"
    assert calls.stops == ["unsafe screen: online"]
    # 実行を始めるときの確かめを終えた合図は 1 回だけ
    assert calls.start_passed == 1
    rows = read_rows(tmp_path / "safety.jsonl")
    assert [row["phase"] for row in rows] == ["start", "monitor", "monitor"]
    # 答えのあった確かめのたびに、照合の結果を渡す (止まることを決めた確かめも)
    assert len(calls.system_screens) == 3


def test_monitor_does_not_stop_on_system_screens(tmp_path):
    # 照合の結果では止めない
    screens = [screen_choices(name, 0.99) for name in SYSTEM_SCREEN_NAMES]
    checker = ScriptedChecker([answer(), *[answer(screen=s) for s in screens]])
    monitor, calls = make_monitor(tmp_path, checker)
    outcomes = [monitor.step().outcome for _ in range(1 + len(screens))]
    assert outcomes == [SafetyStepOutcome.PASSED] * (1 + len(screens))
    assert calls.stops == []
    assert [judgment.screen for judgment in calls.system_screens] == [
        None,
        *SYSTEM_SCREEN_NAMES,
    ]
    rows = read_rows(tmp_path / "safety.jsonl")
    assert [row["system_screen_result"] for row in rows] == [
        None,
        *SYSTEM_SCREEN_NAMES,
    ]


def test_monitor_keeps_going_on_the_same_system_screen(tmp_path):
    checker = ScriptedChecker([answer(screen=screen_choices("home_menu", 0.99))])
    monitor, calls = make_monitor(tmp_path, checker)
    for _ in range(5):
        assert monitor.step().outcome is SafetyStepOutcome.PASSED
    assert calls.stops == []
    assert [judgment.screen for judgment in calls.system_screens] == ["home_menu"] * 5


def test_monitor_stops_after_max_errors_failures_in_a_row(tmp_path):
    checker = ScriptedChecker([answer(), RuntimeError("timeout")])
    monitor, calls = make_monitor(tmp_path, checker, max_errors=3)
    assert monitor.step().outcome is SafetyStepOutcome.PASSED
    assert monitor.step().outcome is SafetyStepOutcome.FAILED
    assert monitor.step().outcome is SafetyStepOutcome.FAILED
    assert calls.stops == []
    last = monitor.step()
    assert last.outcome is SafetyStepOutcome.STOP
    assert calls.stops == ["safety check failed 3 times in a row"]
    rows = read_rows(tmp_path / "safety.jsonl")
    assert [row["probabilities"] is None for row in rows] == [False, True, True, True]
    assert [row["system_screen"] is None for row in rows] == [False, True, True, True]
    assert all(row["system_screen_result"] is None for row in rows)
    assert all("timeout" in row["error"] for row in rows[1:])
    # 失敗した確かめでは、照合の結果を渡さない
    assert len(calls.system_screens) == 1


@pytest.mark.parametrize(
    "screen", BROKEN_SYSTEM_SCREENS.values(), ids=BROKEN_SYSTEM_SCREENS.keys()
)
def test_monitor_counts_a_broken_system_screen_as_a_failure(tmp_path, screen):
    broken = SafetyAnswer(probabilities=ALL_ZERO, system_screen=screen)
    checker = ScriptedChecker([answer(), broken])
    monitor, calls = make_monitor(tmp_path, checker, max_errors=2)
    assert monitor.step().outcome is SafetyStepOutcome.PASSED
    assert monitor.step().outcome is SafetyStepOutcome.FAILED
    assert monitor.step().outcome is SafetyStepOutcome.STOP
    assert calls.stops == ["safety check failed 2 times in a row"]
    assert len(calls.system_screens) == 1
    rows = read_rows(tmp_path / "safety.jsonl")
    assert [row["system_screen"] is None for row in rows] == [False, True, True]


def test_monitor_success_between_failures_starts_counting_again(tmp_path):
    error = RuntimeError("timeout")
    checker = ScriptedChecker(
        [answer(), error, error, answer(), error, error, answer()]
    )
    monitor, calls = make_monitor(tmp_path, checker, max_errors=3)
    outcomes = [monitor.step().outcome for _ in range(7)]
    assert SafetyStepOutcome.STOP not in outcomes
    assert calls.stops == []
    assert len(calls.system_screens) == 3


def test_monitor_stops_when_over_then_failure_then_over(tmp_path):
    # 失敗した確かめをはさんでも、答えのあった確かめを続けて数える
    checker = ScriptedChecker(
        [answer(), answer(online=0.9), RuntimeError("timeout"), answer(online=0.9)]
    )
    monitor, calls = make_monitor(tmp_path, checker, max_errors=3)
    outcomes = [monitor.step().outcome for _ in range(4)]
    assert outcomes == [
        SafetyStepOutcome.PASSED,
        SafetyStepOutcome.PASSED,
        SafetyStepOutcome.FAILED,
        SafetyStepOutcome.STOP,
    ]
    assert calls.stops == ["unsafe screen: online"]


def test_monitor_stops_when_over_and_failure_alternate(tmp_path):
    error = RuntimeError("timeout")
    over = answer(purchase=0.9)
    checker = ScriptedChecker([answer(), over, error, over, error, over, error])
    monitor, calls = make_monitor(tmp_path, checker, max_errors=2)
    outcomes: list[SafetyStepOutcome] = []
    for _ in range(7):
        outcomes.append(monitor.step().outcome)
        if outcomes[-1] is SafetyStepOutcome.STOP:
            break
    assert outcomes[-1] is SafetyStepOutcome.STOP
    assert calls.stops == ["unsafe screen: purchase"]
    rows = read_rows(tmp_path / "safety.jsonl")
    assert [row["probabilities"] is None for row in rows] == [
        False,
        False,
        True,
        False,
    ]


@pytest.mark.parametrize(
    "duration, expected_wait",
    [(0.3, 0.7), (1.0, 0.0), (1.5, 0.0)],
)
def test_wait_keeps_the_interval_from_the_previous_send(
    tmp_path, duration, expected_wait
):
    time = FakeTime()
    checker = ScriptedChecker([answer()], time, duration=duration)
    monitor, _ = make_monitor(tmp_path, checker, time, interval_s=1.0)
    assert monitor.step().wait_s == pytest.approx(expected_wait)
    assert monitor.step().wait_s == pytest.approx(expected_wait)


def test_failed_monitor_check_also_keeps_the_interval(tmp_path):
    time = FakeTime()
    checker = ScriptedChecker([answer(), RuntimeError("x")], time, duration=0.25)
    monitor, _ = make_monitor(tmp_path, checker, time, interval_s=1.0)
    monitor.step()
    result = monitor.step()
    assert result.outcome is SafetyStepOutcome.FAILED
    assert result.wait_s == pytest.approx(0.75)


def test_monitor_judgments_carry_the_frame_time_and_the_receive_time(tmp_path):
    time = FakeTime()
    frames = [CapturedFrame(t=t, nv12=FRAME.nv12) for t in (1.5, 2.6, 3.7)]

    def latest_frame() -> CapturedFrame:
        return frames.pop(0) if len(frames) > 1 else frames[0]

    screen = screen_choices("controllers", 0.8)
    checker = ScriptedChecker([answer(screen=screen)], time, duration=0.25)
    monitor, calls = make_monitor(tmp_path, checker, time, latest_frame=latest_frame)
    for _ in range(3):
        monitor.step()
    assert [judgment.frame_t for judgment in calls.system_screens] == [1.5, 2.6, 3.7]
    assert [judgment.t_received for judgment in calls.system_screens] == [
        pytest.approx(0.25),
        pytest.approx(0.5),
        pytest.approx(0.75),
    ]
    assert all(judgment.screen == "controllers" for judgment in calls.system_screens)


# --- 止める合図 ---


@pytest.mark.parametrize("unsafe", [False, True], ids=["safe", "unsafe"])
def test_stop_during_the_start_check_writes_nothing(tmp_path, unsafe):
    stop = threading.Event()
    result_answer = answer(online=0.99) if unsafe else answer()
    checker = ScriptedChecker([result_answer], during_check=lambda _: stop.set())
    monitor, calls = make_monitor(tmp_path, checker)
    result = monitor.step(stop)
    assert result.outcome is SafetyStepOutcome.STOPPED
    assert result.row is None
    assert not (tmp_path / "safety.jsonl").exists()
    assert calls.stops == []
    assert calls.start_passed == 0
    # 止まることが決まった後に受け取った応答は、照合の結果も渡さない
    assert calls.system_screens == []


def test_stop_during_a_monitor_check_writes_nothing(tmp_path):
    stop = threading.Event()

    def stop_on_second_check(count: int) -> None:
        if count == 2:
            stop.set()

    checker = ScriptedChecker(
        [answer(), answer(screen=screen_choices("user_select"))],
        during_check=stop_on_second_check,
    )
    monitor, calls = make_monitor(tmp_path, checker, max_errors=1)
    assert monitor.step(stop).outcome is SafetyStepOutcome.PASSED
    assert monitor.step(stop).outcome is SafetyStepOutcome.STOPPED
    assert len(read_rows(tmp_path / "safety.jsonl")) == 1
    assert calls.stops == []
    assert [judgment.screen for judgment in calls.system_screens] == [None]


def test_stop_during_a_failed_monitor_check_writes_nothing(tmp_path):
    stop = threading.Event()

    def stop_on_second_check(count: int) -> None:
        if count == 2:
            stop.set()

    checker = ScriptedChecker(
        [answer(), RuntimeError("x")], during_check=stop_on_second_check
    )
    monitor, calls = make_monitor(tmp_path, checker, max_errors=1)
    assert monitor.step(stop).outcome is SafetyStepOutcome.PASSED
    assert monitor.step(stop).outcome is SafetyStepOutcome.STOPPED
    assert len(read_rows(tmp_path / "safety.jsonl")) == 1
    assert calls.stops == []


# --- スレッドの本体 ---


def test_run_waits_for_the_first_frame_and_checks_until_stopped(tmp_path):
    stop = threading.Event()
    frames: list[CapturedFrame | None] = [None, None, FRAME]

    def latest_frame() -> CapturedFrame | None:
        return frames.pop(0) if len(frames) > 1 else frames[0]

    def stop_on_third_check(count: int) -> None:
        if count == 3:
            stop.set()

    checker = ScriptedChecker([answer()], during_check=stop_on_third_check)
    monitor, calls = make_monitor(
        tmp_path, checker, latest_frame=latest_frame, interval_s=0.01
    )
    monitor.run(stop)
    assert len(checker.jpegs) == 3
    assert calls.start_passed == 1
    assert calls.stops == []
    # 3 回目は止める合図を見て、行を書かずに抜けた
    rows = read_rows(tmp_path / "safety.jsonl")
    assert [row["phase"] for row in rows] == ["start", "monitor"]
    assert len(calls.system_screens) == 2


def test_run_returns_after_an_unsafe_start(tmp_path):
    stop = threading.Event()
    checker = ScriptedChecker([answer(purchase=0.9)])
    monitor, calls = make_monitor(tmp_path, checker, interval_s=0.01)
    monitor.run(stop)
    assert calls.stops == ["unsafe screen at start: purchase"]
    assert len(checker.jpegs) == 1


def test_run_returns_after_a_monitor_stop(tmp_path):
    stop = threading.Event()
    checker = ScriptedChecker([answer(), answer(system_settings=0.9)])
    monitor, calls = make_monitor(tmp_path, checker, interval_s=0.01)
    monitor.run(stop)
    assert calls.stops == ["unsafe screen: system_settings"]
    assert len(checker.jpegs) == 3


def test_run_stops_everything_on_an_unexpected_error(tmp_path):
    def broken_on_start_passed() -> None:
        raise RuntimeError("boom")

    stop = threading.Event()
    checker = ScriptedChecker([answer()])
    monitor, calls = make_monitor(
        tmp_path, checker, interval_s=0.01, on_start_passed=broken_on_start_passed
    )
    monitor.run(stop)
    assert calls.stops == [safety_monitor_failure_reason(RuntimeError("boom"))]
    assert calls.stops == ["safety monitor failed: RuntimeError: boom"]


def test_run_stops_everything_when_passing_the_judgment_fails(tmp_path):
    def broken_on_system_screen(judgment: SystemScreenJudgment) -> None:
        raise RuntimeError("judgment sink is broken")

    stop = threading.Event()
    checker = ScriptedChecker([answer()])
    monitor, calls = make_monitor(
        tmp_path, checker, interval_s=0.01, on_system_screen=broken_on_system_screen
    )
    monitor.run(stop)
    assert calls.stops == [
        "safety monitor failed: RuntimeError: judgment sink is broken"
    ]
    assert len(checker.jpegs) == 1
