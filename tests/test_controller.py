import time

import pytest

from switch2_remote.controller import Controller
from switch2_remote.controller_bridge import UnconfirmedInputError
from switch2_remote.controller_state import PadState

NEUTRAL = bytes.fromhex("00000f8080808000")


def pad(*buttons: str, **sticks: tuple[float, float]) -> PadState:
    """ボタン名とスティック (LS=(x, y) など) から PadState を作る。"""
    return PadState(frozenset(buttons), dict(sticks))


def report(b1=0, b2=0, hat=15, lx=128, ly=128, rx=128, ry=128) -> bytes:
    return bytes([b1, b2, hat, lx, ly, rx, ry, 0])


A = report(b1=0x04)
B = report(b1=0x02)


class FakeBridge:
    """controller_bridge.Bridge の代わりに、送られたレポートを記録する偽物。

    fail_at 回目 (0 始まり) 以降の set_report で fail_with の例外を送出する (壊れたブリッジは以後も失敗し続ける)。
    """

    def __init__(self, fail_at: int | None = None, fail_with: Exception | None = None):
        self.reports: list[bytes] = []
        self.calls = 0
        self.fail_at = fail_at
        self.fail_with = fail_with

    def set_report(self, report: bytes) -> str:
        index = self.calls
        self.calls += 1
        if self.fail_at is not None and index >= self.fail_at:
            raise self.fail_with
        self.reports.append(bytes(report))
        return f"ack {bytes(report).hex()} {index}"


class FakeClock:
    """time.monotonic の代わりに、テストが進めた時刻を返す偽物。"""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_controller(bridge: FakeBridge | None = None, now: float = 0.0):
    """解放スレッドを起動しない Controller と、その偽の bridge・clock・sleep の記録を返す。"""
    bridge = bridge if bridge is not None else FakeBridge()
    clock = FakeClock(now)
    sleeps: list[float] = []
    controller = Controller(bridge, clock=clock, sleep=sleeps.append, start_timer=False)
    return controller, bridge, clock, sleeps


# --- tap ---


def test_tap_presses_and_releases_each_chord_in_order():
    controller, bridge, _, sleeps = make_controller()
    reply = controller.tap([pad("A"), pad("RIGHT", "B")])
    assert reply == "ok tap A B+RIGHT"
    assert bridge.reports == [A, NEUTRAL, report(b1=0x02, hat=2), NEUTRAL]
    # 押下 120ms → 離して 100ms → 次の CHORD の前に 150ms → 押下 120ms → 離して 100ms
    assert sleeps == pytest.approx([0.12, 0.1, 0.15, 0.12, 0.1])


def test_tap_does_not_wait_after_last_chord():
    controller, _, _, sleeps = make_controller()
    controller.tap([pad("A")])
    assert sleeps == pytest.approx([0.12, 0.1])


def test_tap_keeps_held_inputs_while_pressing():
    controller, bridge, _, _ = make_controller()
    controller.down([pad("L", LS=(0.0, 1.0))])
    controller.tap([pad("A"), pad("A")])
    held = report(b1=0x10, ly=0)
    held_and_a = report(b1=0x14, ly=0)
    assert bridge.reports == [held, held_and_a, held, held_and_a, held]


def test_tap_during_hold_keeps_hold():
    controller, bridge, _, _ = make_controller()
    controller.hold(pad("B"), 2000)
    controller.tap([pad("A")])
    assert bridge.reports == [B, report(b1=0x06), B]


@pytest.mark.parametrize(
    "setup, pressed, chords",
    [
        ("down", pad("A"), [pad("B"), pad("A")]),  # held にあるボタン (2 つ目の CHORD)
        ("hold", pad("B"), [pad("B")]),  # holds にあるボタン
        ("down", pad(LS=(0.0, 1.0)), [pad(LS=(1.0, 0.0))]),  # 倒しているスティック
        ("down", pad("UP"), [pad("DOWN")]),  # 合成後の十字キーが相反する
        ("hold", pad("LEFT"), [pad("A"), pad("RIGHT")]),
    ],
)
def test_tap_rejects_collision_without_sending(setup, pressed, chords):
    controller, bridge, _, sleeps = make_controller()
    if setup == "down":
        controller.down([pressed])
    else:
        controller.hold(pressed, 1000)
    sent_before = list(bridge.reports)
    with pytest.raises(ValueError):
        controller.tap(chords)
    assert bridge.reports == sent_before
    assert sleeps == []


def test_tap_reports_sent_and_unconfirmed_chords_on_unconfirmed_error():
    # 0: A を押す、1: A を離す、2: A+B を押す (失敗)
    bridge = FakeBridge(fail_at=2, fail_with=UnconfirmedInputError("no ack"))
    controller, _, _, _ = make_controller(bridge)
    reply = controller.tap([pad("A"), pad("B", "A"), pad("X")])
    assert reply == "error no ack (already sent: A; unconfirmed: A+B)"
    assert bridge.reports == [A, NEUTRAL]


def test_tap_reports_no_unconfirmed_chord_on_runtime_error():
    bridge = FakeBridge(fail_at=0, fail_with=RuntimeError("bridge exited"))
    controller, _, _, _ = make_controller(bridge)
    reply = controller.tap([pad("A"), pad("B")])
    assert reply == "error bridge exited (already sent: none; unconfirmed: none)"


def test_tap_treats_chord_as_unconfirmed_when_release_fails():
    # 押下は送れたが、離すレポートの ack を確認できなかった
    bridge = FakeBridge(fail_at=1, fail_with=UnconfirmedInputError("no ack"))
    controller, _, _, _ = make_controller(bridge)
    reply = controller.tap([pad("A"), pad("B")])
    assert reply == "error no ack (already sent: none; unconfirmed: A)"


def test_tap_treats_chord_as_unconfirmed_when_release_fails_after_press_ack():
    # 押下の ack は受け取ったが、離すレポートを送る前にブリッジが終了した
    bridge = FakeBridge(fail_at=1, fail_with=RuntimeError("bridge exited"))
    controller, _, _, _ = make_controller(bridge)
    reply = controller.tap([pad("A"), pad("B")])
    assert reply == "error bridge exited (already sent: none; unconfirmed: A)"


def test_tap_clears_tapping_when_it_fails():
    bridge = FakeBridge(fail_at=1, fail_with=RuntimeError("bridge exited"))
    controller, _, _, _ = make_controller(bridge)
    controller.tap([pad("A")])
    assert controller.tapping.is_empty()


# --- hold ---


def test_hold_can_press_again_right_after_previous_hold_expires():
    # 解放スレッドが期限切れの hold を離す前に、同じボタンを押し直す
    controller, bridge, clock, _ = make_controller()
    controller.hold(pad("A"), 1000)
    clock.now = 1.0
    assert controller.hold(pad("A"), 500) == "ok hold A 500ms"
    assert bridge.reports == [A, NEUTRAL, A]


def test_command_reports_bridge_failure_when_expired_hold_cannot_be_released():
    # 期限切れの hold を離す送信が失敗したら、衝突のエラーではなく送信の失敗を返す
    bridge = FakeBridge(fail_at=1, fail_with=RuntimeError("bridge exited"))
    controller, _, clock, _ = make_controller(bridge)
    controller.hold(pad("A"), 1000)
    clock.now = 1.0
    with pytest.raises(RuntimeError, match="bridge exited"):
        controller.hold(pad("A"), 500)


def test_hold_counts_duration_from_press_ack():
    # 押下のレポートの ack までに 0.6 秒かかるブリッジ
    clock = FakeClock()

    class SlowBridge(FakeBridge):
        def set_report(self, report: bytes) -> str:
            clock.now += 0.6
            return super().set_report(report)

    bridge = SlowBridge()
    controller = Controller(
        bridge, clock=clock, sleep=lambda s: None, start_timer=False
    )
    controller.hold(pad("A"), 500)
    assert controller.status_text() == "held=none holds=A:500ms last_error=none"


def test_hold_replies_immediately_and_releases_after_ms():
    controller, bridge, clock, sleeps = make_controller(now=10.0)
    reply = controller.hold(pad("RIGHT", "B"), 2000)
    assert reply == "ok hold B+RIGHT 2000ms"
    assert bridge.reports == [report(b1=0x02, hat=2)]
    assert sleeps == []
    clock.now = 11.5
    controller.release_expired()
    assert bridge.reports == [report(b1=0x02, hat=2)]
    clock.now = 12.0
    controller.release_expired()
    assert bridge.reports == [report(b1=0x02, hat=2), NEUTRAL]
    controller.release_expired()
    assert bridge.reports == [report(b1=0x02, hat=2), NEUTRAL]


def test_hold_can_overlap_another_hold():
    controller, bridge, clock, _ = make_controller()
    controller.hold(pad("A"), 1000)
    controller.hold(pad("B"), 3000)
    assert bridge.reports == [A, report(b1=0x06)]
    clock.now = 1.0
    controller.release_expired()
    assert bridge.reports[-1] == B
    clock.now = 3.0
    controller.release_expired()
    assert bridge.reports[-1] == NEUTRAL


def test_hold_keeps_held_inputs_after_release():
    controller, bridge, clock, _ = make_controller()
    controller.down([pad("L")])
    controller.hold(pad("A"), 500)
    clock.now = 0.5
    controller.release_expired()
    assert bridge.reports == [report(b1=0x10), report(b1=0x14), report(b1=0x10)]


@pytest.mark.parametrize(
    "pressed, chord",
    [
        (pad("A"), pad("A", "B")),  # holds にあるボタン
        (pad(RS=(0.0, 1.0)), pad(RS=(0.0, -1.0))),  # holds で倒しているスティック
        (pad("UP"), pad("DOWN")),  # 十字キーが相反する
    ],
)
def test_hold_rejects_collision_without_sending(pressed, chord):
    controller, bridge, _, _ = make_controller()
    controller.hold(pressed, 1000)
    sent_before = list(bridge.reports)
    with pytest.raises(ValueError):
        controller.hold(chord, 500)
    assert bridge.reports == sent_before


def test_hold_rejects_button_held_by_down():
    controller, bridge, _, _ = make_controller()
    controller.down([pad("A")])
    with pytest.raises(ValueError):
        controller.hold(pad("A"), 500)
    assert bridge.reports == [A]


# --- down ---


def test_down_replaces_stick_value():
    controller, bridge, _, _ = make_controller()
    assert controller.down([pad(LS=(1.0, 0.0))]) == "ok held=LS:1,0"
    assert controller.down([pad(LS=(0.0, 1.0))]) == "ok held=LS:0,1"
    assert bridge.reports == [report(lx=255), report(ly=0)]


def test_down_is_idempotent_for_buttons():
    controller, _, _, _ = make_controller()
    controller.down([pad("A")])
    assert controller.down([pad("A")]) == "ok held=A"


def test_down_joins_multiple_chords():
    controller, bridge, _, _ = make_controller()
    reply = controller.down([pad("A"), pad(LS=(0.5, -1.0))])
    assert reply == "ok held=A+LS:0.5,-1"
    assert bridge.reports == [report(b1=0x04, lx=192, ly=255)]


def test_down_rejects_input_pressed_by_hold():
    controller, bridge, _, _ = make_controller()
    controller.hold(pad("B"), 1000)
    with pytest.raises(ValueError):
        controller.down([pad("B")])
    assert bridge.reports == [B]


@pytest.mark.parametrize(
    "first, second",
    [
        ([pad("UP")], [pad("DOWN")]),  # held と相反する
        ([], [pad("LEFT"), pad("RIGHT")]),  # 1 回の down の中で相反する
    ],
)
def test_down_rejects_conflicting_dpad(first, second):
    controller, bridge, _, _ = make_controller()
    if first:
        controller.down(first)
    sent_before = list(bridge.reports)
    with pytest.raises(ValueError):
        controller.down(second)
    assert bridge.reports == sent_before


# --- up / up_all ---


def test_up_releases_target_from_hold():
    controller, bridge, clock, _ = make_controller()
    controller.hold(pad("B", "RIGHT"), 2000)
    assert controller.up(frozenset({"RIGHT"})) == "ok held=none"
    assert bridge.reports[-1] == B
    clock.now = 0.5
    assert controller.status_text() == "held=none holds=B:1500ms last_error=none"
    clock.now = 2.0
    controller.release_expired()
    assert bridge.reports[-1] == NEUTRAL


def test_up_removes_hold_that_becomes_empty():
    controller, bridge, clock, _ = make_controller()
    controller.hold(pad("A"), 1000)
    controller.up(frozenset({"A"}))
    assert bridge.reports == [A, NEUTRAL]
    assert controller.status_text() == "held=none holds=none last_error=none"
    clock.now = 1.0
    controller.release_expired()
    assert bridge.reports == [A, NEUTRAL]


def test_up_releases_from_held_and_holds_together():
    controller, bridge, _, _ = make_controller()
    controller.down([pad("A", LS=(0.0, 1.0))])
    controller.hold(pad("B"), 1000)
    assert controller.up(frozenset({"LS", "B"})) == "ok held=A"
    assert bridge.reports[-1] == A


def test_up_rejects_target_not_pressed():
    controller, bridge, _, _ = make_controller()
    controller.down([pad("A")])
    with pytest.raises(ValueError):
        controller.up(frozenset({"A", "B"}))
    assert bridge.reports == [A]
    assert controller.status_text() == "held=A holds=none last_error=none"


def test_up_all_releases_held_and_holds():
    controller, bridge, clock, _ = make_controller()
    controller.down([pad("A")])
    controller.hold(pad("B"), 1000)
    assert controller.up_all() == "ok held=none"
    assert bridge.reports[-1] == NEUTRAL
    assert controller.status_text() == "held=none holds=none last_error=none"
    sent_before = list(bridge.reports)
    clock.now = 1.0
    controller.release_expired()
    assert bridge.reports == sent_before


def test_up_all_without_pressed_inputs_is_not_error():
    controller, _, _, _ = make_controller()
    assert controller.up_all() == "ok held=none"


# --- status ---


def test_status_text_without_inputs():
    controller, _, _, _ = make_controller()
    assert controller.status_text() == "held=none holds=none last_error=none"


def test_status_text_shows_held_and_remaining_hold_time():
    controller, _, clock, _ = make_controller(now=10.0)
    controller.down([pad("A", LS=(0.5, -1.0))])
    controller.hold(pad("RIGHT", "B"), 2000)
    controller.hold(pad("X"), 500)
    clock.now = 10.25
    assert controller.status_text() == (
        "held=A+LS:0.5,-1 holds=B+RIGHT:1750ms,X:250ms last_error=none"
    )


def test_release_failure_is_recorded_in_last_error():
    # 0: A の hold を押す、1: 期限切れの解放 (失敗)
    bridge = FakeBridge(fail_at=1, fail_with=RuntimeError("bridge exited"))
    controller, _, clock, _ = make_controller(bridge)
    controller.hold(pad("A"), 1000)
    clock.now = 1.0
    controller.release_expired()
    assert controller.status_text() == (
        "held=none holds=A:0ms last_error=bridge exited"
    )


def test_last_error_keeps_first_release_failure():
    bridge = FakeBridge(fail_at=1, fail_with=RuntimeError("bridge exited"))
    controller, _, clock, _ = make_controller(bridge)
    controller.hold(pad("A"), 1000)
    clock.now = 1.0
    controller.release_expired()
    bridge.fail_with = RuntimeError("controller bridge is not usable")
    controller.release_expired()
    assert controller.status_text() == (
        "held=none holds=A:0ms last_error=bridge exited"
    )


# --- reset_state ---


def test_reset_state_clears_all_layers_without_sending():
    controller, bridge, clock, _ = make_controller()
    controller.down([pad("L", LS=(0.0, 1.0))])
    controller.hold(pad("B"), 2000)
    controller.tapping = pad("A")
    sent_before = list(bridge.reports)
    controller.reset_state()
    assert bridge.reports == sent_before
    assert controller.held.is_empty()
    assert controller.holds == []
    assert controller.tapping.is_empty()
    assert controller.status_text() == "held=none holds=none last_error=none"
    # 空にした後は、以前に押していた入力を衝突とみなさず、期限が来ても何も送らない
    clock.now = 2.0
    controller.release_expired()
    assert bridge.reports == sent_before
    assert controller.down([pad("L")]) == "ok held=L"


def test_reset_state_clears_last_error_so_next_failure_is_recorded():
    # 0: A の hold を押す、1: 復旧中の解放 (失敗)
    bridge = FakeBridge(
        fail_at=1, fail_with=RuntimeError("controller bridge is reconnecting")
    )
    controller, _, clock, _ = make_controller(bridge)
    controller.hold(pad("A"), 1000)
    clock.now = 1.0
    controller.release_expired()
    assert controller.status_text().endswith(
        "last_error=controller bridge is reconnecting"
    )
    # 復旧に成功した (新しいブリッジは中立から始まる)
    controller.reset_state()
    assert controller.status_text() == "held=none holds=none last_error=none"
    # 新しいブリッジで押せた後の、解放の失敗は新しく記録される
    bridge.fail_at = None
    controller.hold(pad("B"), 500)
    bridge.fail_at = bridge.calls
    bridge.fail_with = RuntimeError("bridge exited")
    clock.now = 2.0
    controller.release_expired()
    assert controller.status_text().endswith("last_error=bridge exited")


# --- 解放スレッド ---


def test_release_timer_releases_expired_hold_until_close():
    bridge = FakeBridge()
    clock = FakeClock()
    controller = Controller(bridge, clock=clock, start_timer=True)
    timer_thread = controller.timer_thread
    try:
        controller.hold(pad("A"), 1000)
        clock.now = 1.0
        # 解放スレッドが RELEASE_CHECK_INTERVAL ごとに確かめるため、実時間で少し待つ
        deadline = time.monotonic() + 2.0
        while bridge.reports[-1] != NEUTRAL and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bridge.reports == [A, NEUTRAL]
    finally:
        controller.close()
    assert not timer_thread.is_alive()
