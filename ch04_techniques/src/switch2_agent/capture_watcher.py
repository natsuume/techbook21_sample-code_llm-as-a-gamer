"""キャプチャーが止まったことを、判断のループ (Pipeline) とは別のスレッドで見張る。

Pipeline は Decision Model の応答 (model.decide) を同期で待つため、その間はキャプチャーを確かめられない。
応答を待つ間もキャプチャーが止まったことに気づいて押下状態を中立に戻せるよう、別のスレッドで interval_s ごとに
確かめ、止まったら on_lost (run では PadOutput.stop_output。以後の apply も送らない) を呼ぶ。
止まったという判定は取り消さない (Pipeline は capture_lost で判定を読み、次の確認で止まる)。
判定は純粋な関数 capture_lost_reason に分け、テストはこの関数と、スレッドを起動しない CaptureWatcher で行う。
"""

import logging
import threading
import time
from collections.abc import Callable

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

DEFAULT_WATCH_INTERVAL_S = 0.1  # 見張りの確認の間隔 (秒)


def capture_lost_reason(
    ended: bool, latest_frame_time: float | None, now: float, stale_timeout_s: float
) -> str | None:
    """キャプチャーが止まったとみなす理由を返す (純粋な関数)。止まっていなければ None を返す。

    - ended が真なら "capture ended"
    - latest_frame_time が None でなく、now - latest_frame_time >= stale_timeout_s なら
      f"no new frame for {stale_timeout_s} seconds" (pipeline の CaptureStoppedError のメッセージと同じ形)
    - どちらでもなければ None。最初のフレームが届く前 (latest_frame_time が None) は、古さで判定しない
      (起動直後の最初のフレームの待ちは、Pipeline の first_frame_timeout_s が扱う)
    """
    if ended:
        return "capture ended"
    if latest_frame_time is not None and now - latest_frame_time >= stale_timeout_s:
        return f"no new frame for {stale_timeout_s} seconds"
    return None


class CaptureWatcher:
    """キャプチャーを見張り、止まったら on_lost を 1 回だけ呼ぶ。

    属性:
        capture_ended       キャプチャーが終わったら True を返す関数
        latest_frame_time   最新のフレームの time を返す関数。まだフレームが無ければ None
        on_lost             止まったと判定したときに 1 回だけ呼ぶ関数 (run では PadOutput.stop_output)
        stale_timeout_s     最新のフレームがこの秒数以上古ければ、止まったとみなす
        clock               現在時刻 (秒) を返す関数 (フレームの time と同じ時計)
        interval_s          スレッドが check_once を呼ぶ間隔 (秒)
        lock                lost_reason の読み書きを守るロック
        lost_reason         止まったと判定した理由。まだなら None
        stop_watching       close() で立て、スレッドを止める
        watch_thread        見張りのスレッド。start_thread が False なら None
    """

    def __init__(
        self,
        capture_ended: Callable[[], bool],
        latest_frame_time: Callable[[], float | None],
        on_lost: Callable[[], None],
        stale_timeout_s: float,
        clock: Callable[[], float] = time.monotonic,
        interval_s: float = DEFAULT_WATCH_INTERVAL_S,
        start_thread: bool = True,
    ):
        """属性を入れる。start_thread が真なら、interval_s ごとに check_once を呼ぶ daemon スレッドを起動する。

        False なら起動しない (テストでは check_once を直接呼ぶ)。
        """
        self.capture_ended = capture_ended
        self.latest_frame_time = latest_frame_time
        self.on_lost = on_lost
        self.stale_timeout_s = stale_timeout_s
        self.clock = clock
        self.interval_s = interval_s
        self.lock = threading.Lock()
        self.lost_reason: str | None = None
        self.stop_watching = threading.Event()
        self.watch_thread: threading.Thread | None = None
        if start_thread:
            self.watch_thread = threading.Thread(target=self._run, daemon=True)
            self.watch_thread.start()

    def check_once(self) -> str | None:
        """キャプチャーを 1 回確かめ、止まったと判定した理由 (まだなら None) を返す。

        まだ止まったと判定していなければ、ロックの中で capture_lost_reason(capture_ended(), latest_frame_time(),
        clock(), stale_timeout_s) を計算し、理由があれば lost_reason に入れる。入れたときだけ、ロックの外で
        on_lost() を呼ぶ (1 回だけ)。on_lost の例外 (Exception) は外に出さず、logging で標準エラー出力に書く
        (`on_lost failed` の 1 行の後に、例外のトレースバックが続く)。
        すでに判定していれば再計算せず、記録した理由を返す (判定は取り消さない)。
        """
        with self.lock:
            if self.lost_reason is not None:
                return self.lost_reason
            reason = capture_lost_reason(
                self.capture_ended(),
                self.latest_frame_time(),
                self.clock(),
                self.stale_timeout_s,
            )
            if reason is None:
                return None
            self.lost_reason = reason
        # 中立に戻す処理の失敗で見張りのスレッドを止めないため、失敗の型を問わずに捕まえる
        try:
            self.on_lost()
        except Exception:
            logger.exception("on_lost failed")
        return reason

    def lost(self) -> str | None:
        """止まったと判定した理由を返す (ロックの中で読む)。まだなら None を返す。"""
        with self.lock:
            return self.lost_reason

    def close(self) -> None:
        """見張りのスレッドを止め、終わるのを待つ。スレッドが無ければ何もしない。"""
        self.stop_watching.set()
        if self.watch_thread is not None:
            self.watch_thread.join()
            self.watch_thread = None

    def _run(self) -> None:
        """close() まで、interval_s ごとに check_once を呼ぶ (見張りのスレッドの本体)。"""
        while not self.stop_watching.wait(self.interval_s):
            self.check_once()
