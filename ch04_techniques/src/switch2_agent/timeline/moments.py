"""系列情報の節で共通に使う、時刻の書き方。

時刻は「t=<実行を始めてからの経過秒> 秒 (<何秒前>)」で書く。速い層に時刻の引き算をさせないため、何秒前かも書く。
"""


def format_moment(time: float, now: float, origin: float) -> str:
    """時刻 time を `t=<time - origin> 秒 (<now - time> 秒前)` の形 (どちらも小数第 1 位まで) にする。

    origin は経過秒の起点 (Pipeline.started_at)、now は要求の時刻。例: `t=200.4 秒 (99.8 秒前)`。
    """
    return f"t={time - origin:.1f} 秒 ({now - time:.1f} 秒前)"
