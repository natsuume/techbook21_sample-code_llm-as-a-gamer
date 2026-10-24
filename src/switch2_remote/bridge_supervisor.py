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
状態は state (ok / recovering / failed) と restarts (復旧に成功した回数) で読める (commands の status が使う)。
"""

import threading
import time
from collections.abc import Callable
from typing import Literal, Protocol

REOPEN_ATTEMPTS = 3  # 無線をオフ・オンする前に、ブリッジを起動し直す回数
RADIO_REOPEN_ATTEMPTS = 12  # 無線をオフ・オンした後に、ブリッジを起動し直す回数
REOPEN_INTERVAL = 5.0  # ブリッジを起動し直す前の待ち (秒)
WATCH_INTERVAL = 0.5  # 監視スレッドがブリッジの生存を確かめる間隔 (秒)

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


def log_with_time(message: str) -> None:
    """`HH:MM:SS <message>` を標準出力に書き、すぐに flush する (デーモンのログ)。"""
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def run_in_daemon_thread(recover: Callable[[], None]) -> None:
    """recover を daemon スレッドで起動し、終わるのを待たずに戻る。"""
    threading.Thread(target=recover, daemon=True).start()


class BridgeSupervisor:
    """ブリッジを保持し、使えなくなったら裏で起動し直す。Controller の bridge として使う。

    start_bridge   ブリッジを起動して返す関数。失敗は RuntimeError か OSError
                   (controller_bridge.BridgeStartError は RuntimeError の派生)
    reset_radio    Bluetooth 無線をオフ・オンする関数。失敗は RuntimeError
    sleep          秒数を受け取って待つ関数。起動し直す前の待ちに使う
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
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = log_with_time,
        start_watcher: bool = True,
        run_recovery: Callable[[Callable[[], None]], None] = run_in_daemon_thread,
    ):
        self.start_bridge = start_bridge
        self.reset_radio = reset_radio
        self.sleep = sleep
        self.log = log
        self.run_recovery = run_recovery
        self.on_recovered: Callable[[], None] | None = None
        # state と現在のブリッジ (と restarts・last_recovery_error) を守るロック
        self.lock = threading.Lock()
        self.bridge = start_bridge()
        self._state: BridgeState = "ok"
        self._restarts = 0
        self._last_recovery_error: str | None = None
        # close() で立て、監視スレッドと復旧の待ちを止める
        self.stop = threading.Event()
        self.watcher_thread: threading.Thread | None = None
        if start_watcher:
            self.watcher_thread = threading.Thread(
                target=self._run_watcher, daemon=True
            )
            self.watcher_thread.start()

    @property
    def state(self) -> BridgeState:
        """ "ok" (現在のブリッジを使える)、"recovering" (復旧中)、"failed" (復旧できなかった) のいずれかを返す。"""
        raise NotImplementedError

    @property
    def restarts(self) -> int:
        """復旧に成功した回数を返す。"""
        raise NotImplementedError

    @property
    def last_recovery_error(self) -> str | None:
        """最後に復旧で失敗した理由 (例外の error_message.describe_error の説明文) を返す。無ければ None。

        reset_radio の失敗と、無線のオフ・オンの後の起動し直しが全部失敗したときの最後の失敗で更新する。
        復旧に成功しても消さない。
        """
        raise NotImplementedError

    def is_alive(self) -> bool:
        """state が "ok" で、かつ現在のブリッジが is_alive() なら True を返す (commands の status の bridge_alive)。"""
        raise NotImplementedError

    def set_report(self, report: bytes) -> str:
        """state が "ok" なら現在のブリッジの set_report に渡し、その ack の行を返す。

        - state が "recovering" なら送らずに RuntimeError(RECONNECTING_MESSAGE) にする
        - state が "failed" なら送らずに RuntimeError(FAILED_MESSAGE) にする
        - ブリッジの set_report の例外はそのまま外に出す。例外の後に現在のブリッジが is_alive() でなければ、
          外に出す前に復旧を開始する (run_recovery に渡すだけで、復旧の終わりは待たない)
        ブリッジの set_report はロックの外で呼ぶ。
        """
        raise NotImplementedError

    def check(self) -> None:
        """state が "ok" で現在のブリッジが is_alive() でなければ、復旧を開始する。

        監視スレッドが WATCH_INTERVAL 秒ごとに呼び、入力の無い間にブリッジのプロセスが終わった場合を拾う。
        テストから直接呼べるよう公開している。
        """
        raise NotImplementedError

    def close(self) -> None:
        """監視スレッドを止め、stop を立てて復旧を止め、現在のブリッジの close() を呼ぶ。"""
        raise NotImplementedError

    def _start_recovery(self, failed_bridge: ManagedBridge) -> None:
        """state が "ok" のまま failed_bridge が現在のブリッジなら、state を "recovering" にして run_recovery を呼ぶ。

        state の確認と変更はロックの中で行い、"recovering" にした呼び出しだけが run_recovery を呼ぶ
        (復旧は同時に 1 つだけ)。run_recovery はロックの外で呼ぶ。
        """
        raise NotImplementedError

    def _recover(self) -> None:
        """復旧の本体。run_recovery から呼ばれる。

        1. log で開始を記録し、古いブリッジの close() を呼ぶ (例外は無視する)
        2. REOPEN_ATTEMPTS 回まで、sleep(REOPEN_INTERVAL) してから start_bridge() を呼ぶ。成功したら 5 へ
        3. reset_radio() を呼ぶ。RuntimeError なら last_recovery_error に記録して log に出し、4 へ進む
           (無線のオフ・オンに失敗しても、Windows が自分でつなぎ直す可能性があるため)
        4. RADIO_REOPEN_ATTEMPTS 回まで、sleep(REOPEN_INTERVAL) してから start_bridge() を呼ぶ。成功したら 5 へ。
           全部失敗したら state を "failed" にし、last_recovery_error に最後の失敗を記録して log に出す
        5. on_recovered があれば呼ぶ → ロックの中で現在のブリッジを差し替え、state を "ok"、restarts を 1 増やす → log に記録する
        start_bridge の失敗として扱う例外は RuntimeError と OSError で、失敗のたびに log に出す。
        各 sleep の前と後で stop が立っていたら、それ以上何もせずに戻る (close() 中の復旧を止める)。
        """
        raise NotImplementedError

    def _run_watcher(self) -> None:
        """close() まで、WATCH_INTERVAL 秒ごとに check() を呼ぶ (監視スレッドの本体)。"""
        raise NotImplementedError
