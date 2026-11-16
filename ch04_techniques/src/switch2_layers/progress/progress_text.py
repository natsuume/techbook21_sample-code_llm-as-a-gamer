"""画面の停滞の値 (ProgressState) を、surface と middle に渡す文にする。I/O を扱わない。

2 つの値を、次の 2 行の文にする (秒数は小数第 1 位まで、窓の秒数は書式 g):
    画面が変わっていない: <static_s> 秒
    直近 <window_s> 秒に見た画面だけが出ている: <no_novel_s> 秒
例: 「画面が変わっていない: 12.4 秒」「直近 300 秒に見た画面だけが出ている: 85.0 秒」。
surface (要求の state の progress) と middle (user メッセージの「画面の停滞 (コードの計測)」の節) は、同じ文を使い、
値の時刻はそれぞれの層の時刻の書き方で添える。値が無いときは NO_PROGRESS_TEXT を書く。
"""

from switch2_layers.progress.progress_state import ProgressState

# 値が無い (progress/state.json がまだ無い・読めない) ときに書く文
NO_PROGRESS_TEXT = "画面の停滞の計測の値はまだ無い"


def describe_progress_values(state: ProgressState) -> str:
    """state の 2 つの値を、モジュールの docstring の 2 行の文 (改行 1 つでつなぎ、末尾に改行を付けない) にする。"""
    raise NotImplementedError
