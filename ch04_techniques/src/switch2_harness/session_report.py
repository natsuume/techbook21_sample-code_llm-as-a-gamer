"""コマンドの記録 (events.jsonl) と claude -p の出力 (agent.jsonl) から、実験の集計を作る。

集計 (summarize_session) と表示用のテキスト (format_summary) は純粋関数で、
look の画像は引数の関数 (load_frame) で受け取る。

用語:
    成功した look: command が "look" で ok が True、frame が None でないイベント
    成功した press: command が "press" で ok が True のイベント
    look の組: 成功した look を時刻順に並べたときの、隣り合う 2 つ
    変化なしの組: 2 枚の画像の screen_change.change_ratio が screen_change.is_static に当たる組
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from switch2_harness.event_log import CommandEvent

# look の frame (作業ディレクトリーからの相対パス) を受け取り、グレースケールの画像を返す関数の型
LoadFrame = Callable[[str], np.ndarray]

# agent.jsonl の result メッセージ (type が "result" の JSON の行) の型
ResultMessage = Mapping[str, object]


@dataclass(frozen=True)
class IntervalStats:
    """成功した look の時刻の差 (秒) の中央値・平均・最大。"""

    median_s: float
    mean_s: float
    max_s: float


@dataclass(frozen=True)
class AgentSummary:
    """claude -p の result メッセージの集計。

    launches: result メッセージの数
    total_cost_usd: session_id ごとに最後の result の total_cost_usd を取り、合計したもの
        (--resume で続けたセッションの total_cost_usd は累計のため、すべてを単純には足さない)
    num_turns: すべての result の num_turns の合計
    duration_ms: すべての result の duration_ms の合計
    """

    launches: int
    total_cost_usd: float
    num_turns: int
    duration_ms: int


@dataclass(frozen=True)
class SessionSummary:
    """1 つの作業ディレクトリーの実験の集計。

    duration_s: 最初と最後のイベントの時刻の差 (イベントが 2 つ未満なら 0.0)
    command_counts: command ごとのイベントの数 (時刻順で最初に現れた順)
    error_count: ok が False のイベントの数
    look_interval: 成功した look の時刻の差の統計。成功した look が 2 回未満なら None
    static_pairs: 変化なしの look の組の数
    look_pairs: look の組の数
    longest_static_s: 変化なしの組が連続した区間の最長の時間 (区間の最初の look から最後の look までの秒数)。無ければ 0.0
    unchanged_after_press: 間に成功した press が 1 つ以上ある look の組のうち、変化なしの組の数
    pressed_pairs: 間に成功した press が 1 つ以上ある look の組の数
    agent: result メッセージの集計。result メッセージが無ければ None
    """

    duration_s: float
    command_counts: dict[str, int]
    error_count: int
    look_interval: IntervalStats | None
    static_pairs: int
    look_pairs: int
    longest_static_s: float
    unchanged_after_press: int
    pressed_pairs: int
    agent: AgentSummary | None


def result_messages_from_lines(lines: Iterable[str]) -> list[dict[str, object]]:
    """agent.jsonl の行から result メッセージ (JSON のオブジェクトで "type" が "result" のもの) を順に取り出す。

    JSON として読めない行 (止めたときに途中で切れた行など)、空行、JSON のオブジェクトでない行は読み飛ばす。
    """
    raise NotImplementedError


def summarize_agent(result_messages: Sequence[ResultMessage]) -> AgentSummary | None:
    """result メッセージの列を AgentSummary にする。列が空なら None。

    total_cost_usd は、session_id ごとに列の中で最後の result の値を取って合計する
    (session_id が無い result は、session_id が無いもの同士を 1 つのセッションとして扱う)。
    num_turns と duration_ms は、すべての result の値を int にして合計する。
    値が無い、または数値でない (bool も数値とみなさない) 項目は 0 として扱う。
    """
    raise NotImplementedError


def summarize_session(
    events: Sequence[CommandEvent],
    load_frame: LoadFrame,
    result_messages: Sequence[ResultMessage],
) -> SessionSummary:
    """イベントの列と result メッセージの列から SessionSummary を作る。

    events は time の昇順に並べ替えてから (同じ時刻は元の順のまま) 集計する。
    look の組ごとに、2 つの look の frame を load_frame で読み、screen_change.change_ratio と
    screen_change.is_static で変化なしかを決める。
    「間に成功した press がある」は、並べ替えた列で組の 2 つの look の間に成功した press があること。
    look の間隔の中央値は statistics.median、平均は statistics.fmean で求める。
    agent は summarize_agent(result_messages) の結果。
    """
    raise NotImplementedError


def summary_to_dict(summary: SessionSummary) -> dict[str, object]:
    """summary を JSON にできる辞書にする (dataclasses.asdict。キーはフィールド名、None は None のまま)。"""
    raise NotImplementedError


def format_summary(summary: SessionSummary) -> str:
    """summary を日本語の複数行のテキストにする (末尾に改行は付けない)。

    次の 8 行を返す。秒は小数 1 桁、費用は小数 4 桁で書く:
        記録の時間: <duration_s> 秒
        コマンドの回数: <command=回数 を command_counts の順に空白でつないだもの。無ければ なし>
        error の数: <error_count>
        look の間隔: 中央値 <median_s> 秒 / 平均 <mean_s> 秒 / 最大 <max_s> 秒
        変化なしの look の組: <static_pairs> / <look_pairs>
        変化なしが続いた最長の時間: <longest_static_s> 秒
        press の後も変化なしの組: <unchanged_after_press> / <pressed_pairs>
        エージェント: 起動 <launches> 回 / 費用 <total_cost_usd> USD / ターン <num_turns> / 時間 <duration_ms を秒にしたもの> 秒
    look_interval が None なら 4 行目は `look の間隔: なし (成功した look が 2 回未満)`、
    agent が None なら 8 行目は `エージェント: 記録なし`。
    例: `コマンドの回数: look=6 press=3`、`エージェント: 起動 3 回 / 費用 1.5000 USD / ターン 6 / 時間 3.0 秒`。
    """
    raise NotImplementedError
