"""ブリッジ (controller_bridge.Bridge) の失敗を検知し、裏で起動し直して入力の経路を復旧させる。

BridgeSupervisor は Controller に bridge として渡す (set_report を持つ)。
送信の失敗の後にブリッジが使えなくなっていたら、または監視スレッドがブリッジのプロセスの終了を見つけたら、
別のスレッドで復旧を始める。復旧の手順:
    1. 古いブリッジを閉じる
    2. ブリッジを起動し直す (COM ポートを開き直す)。REOPEN_INTERVAL 秒待ってから試し、REOPEN_ATTEMPTS 回まで繰り返す
    3. それでも戻らなければ、Windows の Bluetooth 無線をオフ・オンする (bluetooth_radio.reset_bluetooth_radio)
    4. もう一度、REOPEN_INTERVAL 秒待ってから起動し直すことを RADIO_REOPEN_ATTEMPTS 回まで繰り返す
    5. 起動できたら新しいブリッジに差し替える。全部失敗したら failed にし、自動では戻らない
復旧中と failed の間の set_report は送らずに RuntimeError にする。
close() は復旧の待ちを中断し、実行中の復旧の終了を RECOVERY_JOIN_TIMEOUT 秒まで待ってからブリッジを閉じる。
状態は state (ok / recovering / failed) と restarts (復旧に成功した回数) で読める (commands の status が使う)。
"""

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Literal, Protocol

from switch2_remote.error_message import describe_error

REOPEN_ATTEMPTS = 3  # 無線をオフ・オンする前に、ブリッジを起動し直す回数
RADIO_REOPEN_ATTEMPTS = 12  # 無線をオフ・オンした後に、ブリッジを起動し直す回数
REOPEN_INTERVAL = 5.0  # ブリッジを起動し直す前の待ち (秒)
WATCH_INTERVAL = 0.5  # 監視スレッドがブリッジの生存を確かめる間隔 (秒)
# close() が実行中の復旧の終了を待つ上限 (秒)。
# 復旧はブリッジの起動の待ちの途中にもありうるため、その上限 (controller_bridge.BRIDGE_READY_TIMEOUT の 30 秒) より長くとる
RECOVERY_JOIN_TIMEOUT = 35.0

RECONNECTING_MESSAGE = (
    "controller bridge is reconnecting; wait until status shows bridge_state=ok"
)
FAILED_MESSAGE = (
    "controller bridge could not be reconnected; "
    "turn Bluetooth off and on in Windows, then restart the daemon"
)

BridgeState = Literal["ok", "recovering", "failed"]


class ManagedBridge(Protocol):
    """BridgeSupervisor が起動・監視・差し替えをするブリッジ。実機では controller_bridge.Bridge を使う。"""

    def set_report(self, report: bytes) -> str:
        """8 バイトのレポートを送り、ブリッジの ack の行を返す。"""
        ...

    def is_alive(self) -> bool:
        """ブリッジのプロセスが動いていて、まだ使えるなら True を返す。"""
        ...

    def close(self) -> None:
        """ブリッジを終了させる。"""
        ...


class BridgeReconnectingError(RuntimeError):
    """復旧中のため送らなかった。復旧が終われば送れるようになる一時的な拒否で、失敗の原因ではない。

    Controller は、この例外を status の last_error に記録しない (復旧が failed で終わったときに、
    復旧を待つよう案内するメッセージが残らないようにするため)。
    """


class RecoveryStopped(Exception):
    """close() で復旧を中止したことを、復旧の手順の途中から _recover に伝える。"""


def close_quietly(bridge: ManagedBridge) -> None:
    """bridge.close() を呼び、例外は無視する (閉じられないブリッジのために復旧を止めない)。"""
    with contextlib.suppress(Exception):
        bridge.close()


def log_with_time(message: str) -> None:
    """`HH:MM:SS <message>` を標準出力に書き、すぐに flush する (デーモンのログ)。"""
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def run_in_daemon_thread(recover: Callable[[], None]) -> None:
    """recover を daemon スレッドで起動し、終わるのを待たずに戻る。"""
    threading.Thread(target=recover, daemon=True).start()


class BridgeSupervisor:
    """ブリッジを保持し、使えなくなったら裏で起動し直す。Controller の bridge として使う。

    start_bridge   ブリッジを起動して返す関数。復旧の中では、送出した Exception はどれも起動の失敗として扱う
                   (controller_bridge.BridgeStartError、wslpath の subprocess.CalledProcessError 等)
    reset_radio    Bluetooth 無線をオフ・オンする関数。送出した Exception はどれも無線のオフ・オンの失敗として扱う
    sleep          秒数を受け取って待つ関数。起動し直す前の待ちに使う。None (既定) なら stop.wait(秒数) を使い、
                   close() で待ちを中断できるようにする
    log            復旧の開始・失敗・成功を 1 行ずつ記録する関数
    start_watcher  False なら監視スレッドを起動しない (テストでは check を直接呼ぶ)
    run_recovery   復旧の本体 (引数なしの関数) を受け取って実行する関数。既定では daemon スレッドで起動して待たない
                   (テストではその場で呼ぶ関数や、呼ばずに保留する関数を渡す)

    コンストラクターで start_bridge() を 1 回呼ぶ。失敗の例外はそのまま外に出す (デーモンの起動の失敗)。
    on_recovered は復旧に成功したとき、新しいブリッジを使い始める前に引数なしで呼ぶ関数 (既定 None)。
    デーモンは controller.reset_state を設定する。

    ロック (self.lock) は state と現在のブリッジの読み書きにだけ使い、ブリッジの set_report・on_recovered・
    start_bridge・reset_radio・run_recovery の呼び出し中は持たない。
    Controller は自分のロックを持ったまま set_report を呼び、on_recovered (reset_state) は Controller のロックを取るため、
    両方のロックを持ち合ってデッドロックしないようにする。
    """

    def __init__(
        self,
        start_bridge: Callable[[], ManagedBridge],
        reset_radio: Callable[[], None],
        sleep: Callable[[float], None] | None = None,
        log: Callable[[str], None] = log_with_time,
        start_watcher: bool = True,
        run_recovery: Callable[[Callable[[], None]], None] = run_in_daemon_thread,
    ):
        self.start_bridge = start_bridge
        self.reset_radio = reset_radio
        # close() で立て、監視スレッドと復旧の待ちを止める
        self.stop = threading.Event()
        # 既定の待ちは stop.wait にし、close() で待ちの途中でも復旧が stop に気づけるようにする (戻り値は使わない)
        self.sleep = (
            sleep if sleep is not None else lambda seconds: self.stop.wait(seconds)
        )
        self.log = log
        self.run_recovery = run_recovery
        self.on_recovered: Callable[[], None] | None = None
        # state と現在のブリッジ (と restarts・last_recovery_error) を守るロック
        self.lock = threading.Lock()
        self.bridge = start_bridge()
        self._state: BridgeState = "ok"
        self._restarts = 0
        self._last_recovery_error: str | None = None
        # 実行中の復旧が無ければ set。recovering にしたときに clear し、最新の世代の _recover の終わりに set する
        self.recovery_idle = threading.Event()
        self.recovery_idle.set()
        # 復旧の世代番号 (ロックで守る)。recovering にするたびに 1 増やし、古い復旧の終わりで
        # 新しい復旧の recovery_idle と recovery_thread を消さないために使う
        self._recovery_generation = 0
        # 復旧を実行しているスレッド。close() が復旧の中 (on_recovered 等) から呼ばれたときに、自分の終了を待たないために使う
        self.recovery_thread: threading.Thread | None = None
        self.watcher_thread: threading.Thread | None = None
        if start_watcher:
            self.watcher_thread = threading.Thread(
                target=self._run_watcher, daemon=True
            )
            self.watcher_thread.start()

    @property
    def state(self) -> BridgeState:
        """ "ok" (現在のブリッジを使える)、"recovering" (復旧中)、"failed" (復旧できなかった) のいずれかを返す。"""
        with self.lock:
            return self._state

    @property
    def restarts(self) -> int:
        """復旧に成功した回数を返す。"""
        with self.lock:
            return self._restarts

    @property
    def last_recovery_error(self) -> str | None:
        """最後に復旧で失敗した理由 (例外の error_message.describe_error の説明文) を返す。無ければ None。

        reset_radio の失敗、無線のオフ・オンの後の起動し直しが全部失敗したときの最後の失敗、
        復旧の中の想定外の例外 (on_recovered の例外等) で更新する。復旧に成功しても消さない。
        """
        with self.lock:
            return self._last_recovery_error

    def is_alive(self) -> bool:
        """state が "ok" で、かつ現在のブリッジが is_alive() なら True を返す (commands の status の bridge_alive)。"""
        with self.lock:
            state, bridge = self._state, self.bridge
        return state == "ok" and bridge.is_alive()

    def set_report(self, report: bytes) -> str:
        """state が "ok" なら現在のブリッジの set_report に渡し、その ack の行を返す。

        - state が "recovering" なら送らずに BridgeReconnectingError(RECONNECTING_MESSAGE) にする
        - state が "failed" なら送らずに RuntimeError(FAILED_MESSAGE) にする
        - state が "ok" でも現在のブリッジが is_alive() でなければ (監視の次の確認より前に止まっていた)、
          送らずに復旧を開始して BridgeReconnectingError(RECONNECTING_MESSAGE) にする。何も送っていないため、
          UnconfirmedInputError にはしない
        - ブリッジの set_report の例外は、型を変えずにそのまま外に出す (tap の unconfirmed の判定が
          UnconfirmedInputError の型を使うため)。例外の後に現在のブリッジが is_alive() でなければ、
          外に出す前に復旧を開始する (run_recovery に渡すだけで、復旧の終わりは待たない)
        ブリッジの set_report はロックの外で呼ぶ。
        """
        with self.lock:
            state, bridge = self._state, self.bridge
        if state == "recovering":
            raise BridgeReconnectingError(RECONNECTING_MESSAGE)
        if state == "failed":
            raise RuntimeError(FAILED_MESSAGE)
        if not bridge.is_alive():
            self._start_recovery(bridge)
            raise BridgeReconnectingError(RECONNECTING_MESSAGE)
        try:
            return bridge.set_report(report)
        except Exception:
            if not bridge.is_alive():
                self._start_recovery(bridge)
            raise

    def check(self) -> None:
        """state が "ok" で現在のブリッジが is_alive() でなければ、復旧を開始する。

        監視スレッドが WATCH_INTERVAL 秒ごとに呼び、入力の無い間にブリッジのプロセスが終わった場合を拾う。
        テストから直接呼べるよう公開している。
        """
        with self.lock:
            state, bridge = self._state, self.bridge
        if state == "ok" and not bridge.is_alive():
            self._start_recovery(bridge)

    def close(self) -> None:
        """復旧と監視スレッドを止め、現在のブリッジを閉じる。

        1. stop を立てる (監視スレッドと、復旧の待ちが止まる。既定の sleep は stop.wait なので待ちの途中でも戻る)
        2. 監視スレッドがあれば終了を待つ
        3. recovery_idle.wait(RECOVERY_JOIN_TIMEOUT) で、実行中の復旧の終了を待つ。期限までに終わらなければ、
           log に 1 行出してそのまま続ける。復旧の中 (on_recovered 等) から呼ばれたときは、自分の終了を待たない
        4. ロックの中で現在のブリッジを読み、そのブリッジの close() を呼ぶ
        保証の範囲: 復旧が RECOVERY_JOIN_TIMEOUT 秒以内に終われば、起動に成功したブリッジは必ず閉じられる
        (差し替えの前に stop に気づいた復旧は新しいブリッジを自分で閉じ、差し替えた後なら 4 で閉じる)。
        無線のオフ・オンの途中などで期限を過ぎた場合は、復旧の終わりを待たずに終える。
        """
        self.stop.set()
        if self.watcher_thread is not None:
            self.watcher_thread.join()
            self.watcher_thread = None
        called_from_recovery = threading.current_thread() is self.recovery_thread
        if not called_from_recovery and not self.recovery_idle.wait(
            RECOVERY_JOIN_TIMEOUT
        ):
            self.log("controller bridge recovery did not stop in time")
        with self.lock:
            bridge = self.bridge
        bridge.close()

    def _start_recovery(self, failed_bridge: ManagedBridge) -> None:
        """state が "ok" のまま failed_bridge が現在のブリッジなら、state を "recovering" にして run_recovery を呼ぶ。

        state の確認と変更はロックの中で行い、"recovering" にした呼び出しだけが run_recovery を呼ぶ
        (復旧は同時に 1 つだけ)。recovering にするときに、同じロックの中で復旧の世代を 1 増やし、recovery_idle を clear する
        (復旧のスレッドが動き出す前に close() されても、その復旧の終わりを待てるように)。run_recovery はロックの外で呼ぶ。
        """
        with self.lock:
            if self._state != "ok" or self.bridge is not failed_bridge:
                return
            self._state = "recovering"
            self._recovery_generation += 1
            self.recovery_idle.clear()
        self.run_recovery(self._recover)

    def _recover(self) -> None:
        """復旧の本体。run_recovery から呼ばれる。

        1. log で開始を記録し、古いブリッジの close() を呼ぶ (例外は無視する)
        2. REOPEN_ATTEMPTS 回まで、sleep(REOPEN_INTERVAL) してから start_bridge() を呼ぶ。成功したら 5 へ
        3. reset_radio() を呼ぶ。例外 (Exception) なら last_recovery_error に記録して log に出し、4 へ進む
           (無線のオフ・オンに失敗しても、Windows が自分でつなぎ直す可能性があるため)
        4. RADIO_REOPEN_ATTEMPTS 回まで、sleep(REOPEN_INTERVAL) してから start_bridge() を呼ぶ。成功したら 5 へ。
           全部失敗したら state を "failed" にし、last_recovery_error に最後の失敗を記録して log に出す
        5. on_recovered があれば呼ぶ → ロックの中で stop を確かめてから現在のブリッジを差し替え、state を "ok"、
           restarts を 1 増やす → log に記録する
        start_bridge が送出した Exception はどれも起動の失敗として扱い、失敗のたびに log に出して次の試行に進む
        (queue.Empty、subprocess.CalledProcessError 等の RuntimeError / OSError 以外も含む)。

        close() による中止: 次のいずれかで stop が立っていたら、それ以上何もせずに戻る (state は変えない)。
            - 各 sleep の前と後
            - start_bridge() が成功した直後。新しいブリッジの close() を呼び (例外は無視する)、
              on_recovered を呼ばず、差し替えもしない
            - 5 の差し替えの直前 (ロックの中で確かめる)。新しいブリッジの close() を呼び (例外は無視する)、差し替えない

        想定外の例外: 上の扱い以外で例外 (Exception) が出そうになったら (on_recovered の例外等)、外に出さずに
        state を "failed" にし、last_recovery_error にその説明 (error_message.describe_error) を記録して log に出す。
        起動に成功して差し替えなかった新しいブリッジがあれば、その close() を呼ぶ (例外は無視する)。

        不変条件: close() で中止した場合を除き、state を "recovering" にした復旧は必ず "ok" か "failed" で終わる。
        どの終わり方 (成功・failed・中止・想定外の例外) でも、最後に、ロックの中で現在の世代が開始時の自分の世代と
        同じときだけ recovery_idle を set し、recovery_thread を None に戻す。差し替えた新しいブリッジがすぐ止まって
        次の復旧が始まっていたら (世代が進んでいたら)、その復旧の recovery_idle と recovery_thread には触れない。
        """
        with self.lock:
            generation = self._recovery_generation
            self.recovery_thread = threading.current_thread()
        try:
            self._recover_steps()
        finally:
            with self.lock:
                if self._recovery_generation == generation:
                    self.recovery_thread = None
                    self.recovery_idle.set()

    def _recover_steps(self) -> None:
        """_recover の手順の本体。手順と例外の扱いは _recover の docstring のとおり。"""
        new_bridge: ManagedBridge | None = None
        try:
            with self.lock:
                old_bridge = self.bridge
            self.log("controller bridge is not usable; reconnecting")
            close_quietly(old_bridge)
            new_bridge, _ = self._reopen(REOPEN_ATTEMPTS)
            if new_bridge is None:
                self._reset_radio_and_record()
                new_bridge, last_error = self._reopen(RADIO_REOPEN_ATTEMPTS)
                if new_bridge is None:
                    self._fail(last_error)
                    return
            if self.on_recovered is not None:
                self.on_recovered()
            with self.lock:
                adopted = not self.stop.is_set()
                if adopted:
                    self.bridge = new_bridge
                    self._state = "ok"
                    self._restarts += 1
                    restarts = self._restarts
            if not adopted:
                close_quietly(new_bridge)
                return
            # 差し替えた後は現在のブリッジなので、下の想定外の例外の扱いで閉じない
            new_bridge = None
            self.log(f"controller bridge reconnected (restarts={restarts})")
        except RecoveryStopped:
            return
        # 復旧のスレッドを例外で終わらせず、recovering のまま残さないため、想定外の例外もすべて受ける
        except Exception as e:  # noqa: BLE001
            if new_bridge is not None:
                close_quietly(new_bridge)
            self._fail(e)

    def _sleep_unless_stopped(self) -> None:
        """sleep(REOPEN_INTERVAL) する。前と後で stop が立っていたら RecoveryStopped を送出する。"""
        if self.stop.is_set():
            raise RecoveryStopped
        self.sleep(REOPEN_INTERVAL)
        if self.stop.is_set():
            raise RecoveryStopped

    def _reopen(self, attempts: int) -> tuple[ManagedBridge | None, Exception | None]:
        """attempts 回まで、待ってから start_bridge() を呼ぶ。

        成功したら (新しいブリッジ, None)、全部失敗したら (None, 最後の失敗の例外) を返す。
        起動に成功した直後に stop が立っていたら、新しいブリッジを閉じて RecoveryStopped を送出する。
        全部失敗した後に stop が立っていたら、RecoveryStopped を送出する
        (終了の途中で無線をオフ・オンしたり、failed にしたりしないため)。
        """
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._sleep_unless_stopped()
            try:
                bridge = self.start_bridge()
            # 起動の失敗の型は起動の方法によって様々なため (queue.Empty、CalledProcessError 等)、すべて失敗として扱う
            except Exception as e:  # noqa: BLE001
                last_error = e
                self.log(
                    f"controller bridge reopen failed ({attempt}/{attempts}): "
                    f"{describe_error(e)}"
                )
                continue
            if self.stop.is_set():
                close_quietly(bridge)
                raise RecoveryStopped
            return bridge, None
        if self.stop.is_set():
            raise RecoveryStopped
        return None, last_error

    def _reset_radio_and_record(self) -> None:
        """reset_radio() を呼ぶ。失敗 (Exception) は last_recovery_error に記録して log に出し、外に出さない。"""
        self.log("turning bluetooth radio off and on")
        try:
            self.reset_radio()
        # 無線のオフ・オンに失敗しても開き直しを続けるため、失敗の型を問わない
        except Exception as e:  # noqa: BLE001
            reason = describe_error(e)
            with self.lock:
                self._last_recovery_error = reason
            self.log(f"bluetooth radio could not be turned off and on: {reason}")
            return
        self.log("bluetooth radio was turned off and on")

    def _fail(self, error: Exception | None) -> None:
        """state を "failed" にし、error の説明を last_recovery_error に記録して log に出す。"""
        reason = describe_error(error) if error is not None else "unknown error"
        with self.lock:
            self._state = "failed"
            self._last_recovery_error = reason
        self.log(f"controller bridge could not be reconnected: {reason}")

    def _run_watcher(self) -> None:
        """close() まで、WATCH_INTERVAL 秒ごとに check() を呼ぶ (監視スレッドの本体)。"""
        while not self.stop.wait(WATCH_INTERVAL):
            self.check()
