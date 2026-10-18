"""コントローラーの押下状態を保持し、変化のたびに合成したレポートをブリッジに送る。

押下状態は 3 層からなり、送るレポートは 3 層を合成したもの (controller_state.merge_all → build_report):
    held     down で押したままにしている状態
    holds    hold の時限押下 (ActiveHold の並び)。複数同時に存在しうる
    tapping  tap で押している最中の 1 CHORD

各操作:
    tap(chords)        CHORD を順に押す。各 CHORD について「現在の合成状態 + CHORD」を送る → TAP_PRESS_MS 待つ →
                       CHORD を除いた合成状態を送る → TAP_RELEASE_MS 待つ → 次の CHORD の前に TAP_INTERVAL_MS 待つ
                       (最後の CHORD の後は待たない)。全 CHORD を送り終えてから応答を返す
    hold(chord, ms)    「現在の合成状態 + CHORD」を送り、送れたらすぐ応答を返す。ms 経過後、
                       その hold の分を除いた合成状態をバックグラウンドの解放スレッド (release_expired) が送る
    down(chords)       CHORD を held に加える (controller_state.add_to_held の規則)
    up(targets)        held と holds の両方から targets を離す
    up_all()           held と holds をすべて空にする

並行して動くのは、デーモンのコマンド処理と解放スレッドの 2 つ。状態の変更と送信は 1 つのロックの中で行い、
tap の待ち (sleep) はロックの外で行う (tap の待ち中も hold の解放を送れるようにするため)。
送信 (bridge.set_report) に成功してから内部状態を確定するため、送信に失敗した操作は押下状態を変えない。
"""

import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from switch2_remote.controller_bridge import UnconfirmedInputError
from switch2_remote.controller_state import (
    PadState,
    add_to_held,
    build_report,
    format_holds,
    format_state,
    join_chords,
    merge,
    merge_all,
    remove_targets,
)

TAP_PRESS_MS = 120  # tap で CHORD を押している時間
TAP_RELEASE_MS = 100  # tap で CHORD を離した後の待ち
TAP_INTERVAL_MS = 150  # tap で次の CHORD を押すまでの追加の待ち
RELEASE_CHECK_INTERVAL = 0.01  # 解放スレッドが期限切れの hold を確かめる間隔 (秒)


class ReportSender(Protocol):
    """Controller がレポートを送る先。実機では controller_bridge.Bridge を使う。"""

    def set_report(self, report: bytes) -> str:
        """8 バイトのレポートを送り、ブリッジの ack の行を返す。"""
        ...


@dataclass(frozen=True)
class ActiveHold:
    """hold の時限押下 1 つ分。release_at は clock の値で表した解放の期限 (秒)。"""

    chord: PadState
    release_at: float


class Controller:
    """押下状態 (held / holds / tapping) を保持し、変化のたびに合成したレポートを bridge.set_report で送る。

    bridge      set_report(bytes) -> str を持つもの。commands の status が bridge.is_alive() を読むため、公開の属性として持つ
    clock       現在時刻 (秒) を返す関数。hold の期限と status の残り時間に使う
    sleep       秒数を受け取って待つ関数。tap の待ちに使う
    start_timer False なら解放スレッドを起動しない (テストでは release_expired を直接呼ぶ)
    """

    def __init__(
        self,
        bridge: ReportSender,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        start_timer: bool = True,
    ):
        self.bridge = bridge
        self.clock = clock
        self.sleep = sleep
        # 状態の変更と送信をまとめて守るロック。デーモンのコマンド処理と解放スレッドが共有する
        self.lock = threading.Lock()
        self.held = PadState()
        self.holds: list[ActiveHold] = []
        self.tapping = PadState()
        self.last_error: str | None = None
        self.stop_timer = threading.Event()
        self.timer_thread: threading.Thread | None = None
        if start_timer:
            self.timer_thread = threading.Thread(
                target=self._run_release_timer, daemon=True
            )
            self.timer_thread.start()

    def tap(self, chords: Sequence[PadState]) -> str:
        """CHORD を順に押して離し、全 CHORD を送り終えてから応答を返す。

        送る前に、全 CHORD をそれぞれ現在の held + holds と合成できるか (controller_state.merge) 検証し、
        1 つでも衝突すれば何も送らずに ValueError にする。
        各 CHORD は、押す間だけ tapping に置き、held と holds を保ったまま押して戻す。
        待ちは sleep(TAP_PRESS_MS / 1000)、sleep(TAP_RELEASE_MS / 1000)、次の CHORD があれば sleep(TAP_INTERVAL_MS / 1000) の順に呼ぶ。

        応答: `ok tap RIGHT RIGHT A+B` (各 CHORD の正規化表記を空白区切り)。
        途中で bridge の送信が失敗 (RuntimeError / OSError) したら例外を外に出さず、
        `error <例外の repr> (already sent: <押して離すまで送り終えた CHORD を空白区切り。無ければ none>; unconfirmed: <例外が UnconfirmedInputError なら失敗した CHORD、それ以外は none>)`
        を返す。
        """
        with self.lock:
            pressed = merge_all([self.held, *self._hold_chords(self.holds)])
            for chord in chords:
                merge(pressed, chord)
        sent: list[str] = []
        for index, chord in enumerate(chords):
            try:
                with self.lock:
                    self._send(self.held, self.holds, chord)
                    self.tapping = chord
                self.sleep(TAP_PRESS_MS / 1000)
                with self.lock:
                    self._send(self.held, self.holds, PadState())
                    self.tapping = PadState()
            except (RuntimeError, OSError) as e:
                already_sent = " ".join(sent) if sent else "none"
                # ack を確認できなかった CHORD は、届いた可能性があるため未送信と区別して報告する
                unconfirmed = (
                    format_state(chord)
                    if isinstance(e, UnconfirmedInputError)
                    else "none"
                )
                return (
                    f"error {e!r} (already sent: {already_sent}; "
                    f"unconfirmed: {unconfirmed})"
                )
            sent.append(format_state(chord))
            self.sleep(TAP_RELEASE_MS / 1000)
            if index < len(chords) - 1:
                self.sleep(TAP_INTERVAL_MS / 1000)
        return f"ok tap {' '.join(sent)}"

    def hold(self, chord: PadState, ms: int) -> str:
        """「現在の合成状態 + chord」を送り、送れたらすぐ `ok hold B+RIGHT 2000ms` を返す。

        ms は commands.parse_hold_ms で検証済み (1〜60000) の値。解放の期限は clock() + ms / 1000。
        衝突の検証は tap と同じで、衝突すれば何も送らずに ValueError にする。
        """
        with self.lock:
            pressed = merge_all([self.held, *self._hold_chords(self.holds)])
            merge(pressed, chord)
            new_holds = [*self.holds, ActiveHold(chord, self.clock() + ms / 1000)]
            self._send(self.held, new_holds, self.tapping)
            self.holds = new_holds
        return f"ok hold {format_state(chord)} {ms}ms"

    def down(self, chords: Sequence[PadState]) -> str:
        """全 CHORD を 1 つにつないだもの (controller_state.join_chords) を held に加え、合成状態を送る。

        held に既にあるボタンは何もせず、held に既にあるスティックは新しい値で置き換える。
        holds にあるボタン・スティックとの重複と、十字キーの相反は何も送らずに ValueError にする。
        応答: `ok held=<held の正規化表記>`
        """
        with self.lock:
            holds_state = merge_all(self._hold_chords(self.holds))
            new_held = add_to_held(self.held, holds_state, join_chords(chords))
            self._send(new_held, self.holds, self.tapping)
            self.held = new_held
            return f"ok held={format_state(self.held)}"

    def up(self, targets: frozenset[str]) -> str:
        """targets (ボタン名とスティック名 LS / RS) を held と holds の両方から離し、合成状態を送る。

        holds の各 CHORD から該当分を除き、空になった hold は削除する。
        targets のうち 1 つでも held にも holds にも無ければ、何も送らずに ValueError にする。
        応答: `ok held=<held の正規化表記>`
        """
        with self.lock:
            pressed = self.held.names()
            for hold in self.holds:
                pressed |= hold.chord.names()
            not_pressed = targets - pressed
            if not_pressed:
                raise ValueError(f"not pressed: {' '.join(sorted(not_pressed))}")
            new_held = remove_targets(self.held, targets)
            new_holds = []
            for hold in self.holds:
                rest = remove_targets(hold.chord, targets)
                if not rest.is_empty():
                    new_holds.append(ActiveHold(rest, hold.release_at))
            self._send(new_held, new_holds, self.tapping)
            self.held, self.holds = new_held, new_holds
            return f"ok held={format_state(self.held)}"

    def up_all(self) -> str:
        """held と holds をすべて空にして合成状態を送る。何も押されていなくてもエラーにしない。

        応答: `ok held=none`
        """
        with self.lock:
            self._send(PadState(), [], self.tapping)
            self.held, self.holds = PadState(), []
        return "ok held=none"

    def status_text(self) -> str:
        """`held=<held の表記> holds=<holds の表記> last_error=<最後のバックグラウンド送信の失敗の repr。無ければ none>` を返す。

        holds の表記は controller_state.format_holds の形式で、残り時間は (release_at - clock()) を ms にして切り捨てた整数。
        """
        with self.lock:
            now = self.clock()
            holds = format_holds(
                [
                    (hold.chord, max(0, math.floor((hold.release_at - now) * 1000)))
                    for hold in self.holds
                ]
            )
            last_error = self.last_error if self.last_error is not None else "none"
            return (
                f"held={format_state(self.held)} holds={holds} last_error={last_error}"
            )

    def release_expired(self) -> None:
        """期限 (release_at <= clock()) を過ぎた hold を外し、外した後の合成状態を送る。

        解放スレッドから RELEASE_CHECK_INTERVAL ごとに呼ぶ。テストから直接呼べるよう公開している。
        期限切れの hold が無ければ何も送らない。送信が失敗 (RuntimeError / OSError) したら例外を外に出さず、
        その repr を last_error に記録して holds を変えない。
        """
        with self.lock:
            now = self.clock()
            remaining = [hold for hold in self.holds if hold.release_at > now]
            if len(remaining) == len(self.holds):
                return
            try:
                self._send(self.held, remaining, self.tapping)
            except (RuntimeError, OSError) as e:
                # 解放スレッドには応答を返す相手がいないため、status で読めるように記録する
                self.last_error = repr(e)
                return
            self.holds = remaining

    def close(self) -> None:
        """解放スレッドを止める。押下状態の中立化は、ブリッジの終了処理 (中立を 200ms 送る) に任せる。"""
        self.stop_timer.set()
        if self.timer_thread is not None:
            self.timer_thread.join()
            self.timer_thread = None

    def _run_release_timer(self) -> None:
        """close() まで、RELEASE_CHECK_INTERVAL ごとに期限切れの hold を外す (解放スレッドの本体)。"""
        while not self.stop_timer.wait(RELEASE_CHECK_INTERVAL):
            self.release_expired()

    @staticmethod
    def _hold_chords(holds: Sequence[ActiveHold]) -> list[PadState]:
        return [hold.chord for hold in holds]

    def _send(
        self, held: PadState, holds: Sequence[ActiveHold], tapping: PadState
    ) -> None:
        """3 層を合成したレポートを送る。呼び出し側がロックを持つ。失敗したら例外をそのまま出す。"""
        layers = [held, *self._hold_chords(holds), tapping]
        self.bridge.set_report(build_report(merge_all(layers)))
