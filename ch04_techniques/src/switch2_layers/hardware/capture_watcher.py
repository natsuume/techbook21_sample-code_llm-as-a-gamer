"""キャプチャーが止まったことの判定。

最新のフレームが STALE_FRAME_TIMEOUT_S 秒以上古いか、キャプチャーの出力が終わったら止まったとみなす。
起動直後の最初のフレームは、実行の開始 (t = 0) から FIRST_FRAME_TIMEOUT_S 秒まで待つ。
判定は純粋な関数 capture_stop_reason に分け、app.LayersApp の見張りのループが CaptureWatcher.check_once を呼ぶ。
"""

from collections.abc import Callable

from switch2_layers.run_clock import RunClock

STALE_FRAME_TIMEOUT_S = 2.0
FIRST_FRAME_TIMEOUT_S = 10.0
REASON_CAPTURE_ENDED = "capture ended"
REASON_NO_FIRST_FRAME = f"no first frame within {FIRST_FRAME_TIMEOUT_S:g} seconds"
REASON_STALE_FRAME = f"no new frame for {STALE_FRAME_TIMEOUT_S:g} seconds"


def capture_stop_reason(
    ended: bool, latest_frame_t: float | None, now_t: float
) -> str | None:
    """キャプチャーが止まったとみなす理由を返す。止まっていなければ None。

    - ended が True なら REASON_CAPTURE_ENDED
    - latest_frame_t が None (まだフレームが無い) で、now_t >= FIRST_FRAME_TIMEOUT_S なら REASON_NO_FIRST_FRAME
    - latest_frame_t があり、now_t - latest_frame_t >= STALE_FRAME_TIMEOUT_S なら REASON_STALE_FRAME
    - どれでもなければ None
    """
    raise NotImplementedError


class CaptureWatcher:
    """キャプチャーを確かめ、止まったと判定した理由を覚える。判定は取り消さない。

    is_ended         キャプチャーの出力が終わっていれば True を返す関数
    latest_frame_t   最後に受け取ったフレームの t を返す関数 (無ければ None)
    clock            今の t を返す時計
    """

    def __init__(
        self,
        is_ended: Callable[[], bool],
        latest_frame_t: Callable[[], float | None],
        clock: RunClock,
    ) -> None:
        """属性を持つ。スレッドは起動しない。"""
        raise NotImplementedError

    def check_once(self) -> str | None:
        """capture_stop_reason(is_ended(), latest_frame_t(), clock.now()) を計算し、理由があれば覚えて返す。

        一度理由を覚えたら、以後は計算せずにその理由を返す。止まっていなければ None。
        """
        raise NotImplementedError
