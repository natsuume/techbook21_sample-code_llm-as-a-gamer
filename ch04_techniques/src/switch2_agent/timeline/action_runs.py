"""系列情報の「操作のまとめ」(actions の節)。操作の記録を、同じ操作が続いた区間ごとにまとめる純粋な関数。

速い層の要求に入る直近の履歴は 5 回分 (約 2 秒) しかなく、同じ操作を何十秒も続けていることが分からない。
スティックの傾きは応答ごとに少しずつ違う (例: LS:0,-0.55 と LS:0,-0.5725) ため、傾きの大きさを除いて向きだけに
してから比べる。中立に戻した記録は、`中立に戻した (<理由>)` の文で 1 つの区間にする (前後の操作の区間を区切る)。
I/O・スレッド・時刻を扱わない。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from switch2_remote.controller_state import BUTTON_ORDER, PadState

from switch2_agent.action_decision import PadAction
from switch2_agent.operation_log import (
    OperationLogRecord,
    OperationRecord,
    format_record,
    record_time,
)
from switch2_agent.questions import STICK_LABELS

NOTHING_TEXT = "none"  # 押すものが無いときの表記 (operation_log.format_action と同じ)
NO_RECORD_TEXT = "(記録なし)"


@dataclass(frozen=True)
class ActionRun:
    """同じ操作が続いた区間 1 つ。

    start   区間の最初の記録の時刻 (operation_log.record_time)
    end     区間の最後の記録の時刻
    count   区間の記録の数
    text    区間の操作の文 (normalize_action の結果か、中立に戻した記録の文)
    """

    start: float
    end: float
    count: int
    text: str


def stick_direction_text(stick: str, x: float, y: float) -> str:
    """スティックの傾き (x, y) を、向きだけの文 (例: `左スティック下`、`右スティック上右`) にする。

    呼び名は questions.STICK_LABELS。上下 (y が正なら上、負なら下) を先に、左右 (x が正なら右、負なら左) を後に書く。
    どちらも 0 なら呼び名だけを返す。
    """
    parts = []
    if y > 0:
        parts.append("上")
    elif y < 0:
        parts.append("下")
    if x > 0:
        parts.append("右")
    elif x < 0:
        parts.append("左")
    return STICK_LABELS[stick] + "".join(parts)


def format_held(held: PadState) -> str:
    """押した状態のボタン (BUTTON_ORDER の順) と、倒したスティックの向き (STICK_LABELS の順) を + でつなぐ。

    何も無ければ NOTHING_TEXT を返す。例: `DOWN+左スティック下`。
    """
    parts = [button for button in BUTTON_ORDER if button in held.buttons]
    for stick in STICK_LABELS:
        if stick in held.sticks:
            x, y = held.sticks[stick]
            parts.append(stick_direction_text(stick, x, y))
    return "+".join(parts) if parts else NOTHING_TEXT


def normalize_action(action: PadAction) -> str:
    """操作を、スティックの傾きの大きさを除いた `押下 <押した状態> / 短押し <短押し>` の文にする。

    押した状態は format_held、短押しはボタン名を BUTTON_ORDER の順に + でつないだもの (無ければ none)。
    例: `押下 左スティック下 / 短押し DOWN`。傾きの大きさだけが違う操作は、同じ文になる。
    """
    taps = [button for button in BUTTON_ORDER if button in action.taps]
    return f"押下 {format_held(action.held)} / 短押し {'+'.join(taps) or NOTHING_TEXT}"


def record_text(record: OperationLogRecord) -> str:
    """記録の区間の文を返す。OperationRecord は normalize_action、中立に戻した記録は operation_log.format_record。"""
    if isinstance(record, OperationRecord):
        return normalize_action(record.action)
    return format_record(record)


def summarize_action_runs(records: Sequence[OperationLogRecord]) -> list[ActionRun]:
    """records (古い順) の、record_text が同じ記録が続いた区間を古い順に返す。records が空なら空のリストを返す。"""
    runs: list[ActionRun] = []
    for record in records:
        time = record_time(record)
        text = record_text(record)
        if runs and runs[-1].text == text:
            last = runs[-1]
            runs[-1] = ActionRun(last.start, time, last.count + 1, text)
        else:
            runs.append(ActionRun(time, time, 1, text))
    return runs


def format_action_runs(
    runs: Sequence[ActionRun],
    origin: float,
    window_s: float,
    max_runs: int,
    truncated: bool = False,
) -> str:
    """操作のまとめの節の文を返す。

    1 行目は見出し `## もっと前からの操作のまとめ (直近 <window_s> 秒。同じ操作が続いた区間ごと、古い順。スティックの強さは省く)`。
    truncated が真 (記録の上限で、直近 window_s 秒より前の記録が残っていない) なら、続けて
    `- (これより前の記録は残っていない)` を書く。区間が max_runs より多ければ、古い区間を省き、
    `- (それより前の <省いた数> 区間は省略)` を書く。区間は 1 行ずつ
    `- t=<start - origin>〜<end - origin> 秒 (<end - start> 秒、<count> 回): <text>` (秒は小数第 1 位まで) で書き、
    最後の区間の末尾に ` (今も続いている)` を付ける。runs が空なら、見出しの次に `(記録なし)` を書く。
    """
    lines = [
        (
            f"## もっと前からの操作のまとめ (直近 {window_s:g} 秒。"
            "同じ操作が続いた区間ごと、古い順。スティックの強さは省く)"
        )
    ]
    if not runs:
        lines.append(NO_RECORD_TEXT)
        return "\n".join(lines)
    if truncated:
        lines.append("- (これより前の記録は残っていない)")
    if len(runs) > max_runs:
        lines.append(f"- (それより前の {len(runs) - max_runs} 区間は省略)")
        runs = runs[-max_runs:]
    for index, run in enumerate(runs):
        tail = " (今も続いている)" if index == len(runs) - 1 else ""
        lines.append(
            f"- t={run.start - origin:.1f}〜{run.end - origin:.1f} 秒 "
            f"({run.end - run.start:.1f} 秒、{run.count} 回): {run.text}{tail}"
        )
    return "\n".join(lines)
