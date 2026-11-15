"""前回の middle の実行以降の surface.jsonl と operations.jsonl の行を、gpt-6-luna に渡す短い文にまとめる。I/O を扱わない。

行は JSON Lines から読んだ値で、形が崩れていることがある。まとめる処理は例外を出さず、読めない行や値は読み飛ばす
(例外を出すと、middle/state.json の読んだ位置が進まず、同じ行を次の回にも読むため、middle が続けて失敗する)。
時刻は、フレームのラベルと同じ「t=<小数第 3 位まで>」の形 (frames.surface_frames.format_frame_label) で書く。
自由な文字列 (失敗の説明・ボタン名・情報ラベルの名前など) は MAX_TEXT_CHARS 文字までにし、超えた分は「…」に置き換える。
まとめの行が MAX_SUMMARY_LINES 行を超えるときは、新しい方の MAX_SUMMARY_LINES 行だけを残し、
その前に「(古い <省いた行数> 行を省いた)」の行を置く (middle-once が保存済みの実行を先頭から読むときにも、要求を短く保つ)。
まとめる行が 1 つも無いときは NO_RECORDS を返す。まとめの文は行を改行 1 つでつなぎ、末尾に改行を付けない。

operations.jsonl のまとめ (summarize_operation_rows):
    t が数 (bool を除く) で有限の行だけを使い、1 行を次の説明にする:
        source が "neutral"   NEUTRAL_RESET_TEXT
        source が "stop"      STOP_RESET_TEXT
        それ以外              describe_operation(行)
        ok が false の行は、説明の後ろに「 (送信に失敗: <error>)」(error が文字列でなければ「 (送信に失敗)」) を足す
    説明が同じ行が続いた区間を 1 行にまとめる:
        1 行だけ       「- t=12.300: <説明>」
        2 行以上       「- t=12.300〜t=13.500 (4 回): <説明>」(区間の最初と最後の行の t と行数)
surface.jsonl のまとめ (summarize_surface_rows):
    t_received が数 (bool を除く) で有限の行だけを使う。1 行目は「surface の要求 <行数> 回 (失敗 <error が空でない
    文字列の行の数> 回)」。続けて、次のどれかがある行ごとに「- t=<t_received>: <項目> / <項目> ...」を書く
    (どれも無い行は書かない)。項目はこの順:
        scene_changed が true                 「場面転換を検知」
        info_labels (リスト) の要素のうち、object で name が文字列、probability が有限の数 (bool を除く) のもの
                                              「情報ラベル: <name> <probability を小数第 2 位まで>, ...」
        deep_requests (リスト) の文字列の要素    「deep を求めた: <種類>, ...」
        blocked_buttons (リスト) の文字列の要素  「押せないボタンを外した: <ボタン名>, ...」
        error が空でない文字列                「失敗: <error>」
    MAX_SUMMARY_LINES は 2 行目以降 (- で始まる行) に当てはめ、省いた行数の行は 1 行目の直後に置く。
    使える行があって「- 」の行が 1 つも無いときは、1 行目だけを返す。
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from switch2_layers.frames.surface_frames import format_frame_label

MAX_SUMMARY_LINES = 30  # まとめの行 (- で始まる行) の最大の数
MAX_TEXT_CHARS = 200  # 自由な文字列の最大の文字数
NO_RECORDS = "(記録なし)"
NEUTRAL_RESET_TEXT = "中立に戻した"
STOP_RESET_TEXT = "止めるために中立にした"
NEUTRAL_OPERATION_TEXT = "何も押さない"


def describe_operation(row: Mapping[str, object]) -> str:
    """operations.jsonl の 1 行 (または surface.jsonl の action) の held・taps・sticks を説明の文にする。

    次の項目を、あるものだけこの順に「 / 」でつなぐ:
        held がリストなら、その文字列の要素    「押し続け: A, B」(書かれた順)
        taps がリストなら、その文字列の要素    「短押し: X」
        sticks が object で、その LS が 2 つの有限の数 (bool を除く) のリストで (0, 0) でない
                                               「左スティック: (0.00, 1.00)」(小数第 2 位まで)
        sticks の RS が同じ形で (0, 0) でない  「右スティック: (-0.50, 0.00)」
    どの項目も無ければ NEUTRAL_OPERATION_TEXT を返す。形が崩れた値は無いものとして扱い、例外は出さない。
    ボタン名は MAX_TEXT_CHARS 文字までにする。
    """
    parts: list[str] = []
    held = _strings(row.get("held"))
    if held:
        parts.append("押し続け: " + ", ".join(held))
    taps = _strings(row.get("taps"))
    if taps:
        parts.append("短押し: " + ", ".join(taps))
    sticks = row.get("sticks")
    if isinstance(sticks, Mapping):
        for key, name in (("LS", "左スティック"), ("RS", "右スティック")):
            position = _stick_position(sticks.get(key))
            if position is not None and position != (0.0, 0.0):
                parts.append(f"{name}: ({position[0]:.2f}, {position[1]:.2f})")
    return " / ".join(parts) if parts else NEUTRAL_OPERATION_TEXT


def summarize_operation_rows(rows: Sequence[Mapping[str, object]]) -> str:
    """operations.jsonl の行 rows (書かれた順) を、モジュールの docstring の形の複数行の文にまとめて返す。"""
    groups: list[_OperationGroup] = []
    for row in rows:
        t = row.get("t")
        if not _is_finite_number(t):
            continue
        text = _describe_operation_row(row)
        if groups and groups[-1].text == text:
            groups[-1].last_t = float(t)
            groups[-1].count += 1
        else:
            groups.append(_OperationGroup(float(t), float(t), 1, text))
    if not groups:
        return NO_RECORDS
    return "\n".join(_keep_newest_lines([group.line() for group in groups]))


def summarize_surface_rows(rows: Sequence[Mapping[str, object]]) -> str:
    """surface.jsonl の行 rows (書かれた順) を、モジュールの docstring の形の複数行の文にまとめて返す。"""
    usable = [row for row in rows if _is_finite_number(row.get("t_received"))]
    if not usable:
        return NO_RECORDS
    failures = sum(1 for row in usable if _error_text(row) is not None)
    header = f"surface の要求 {len(usable)} 回 (失敗 {failures} 回)"
    lines: list[str] = []
    for row in usable:
        items = _surface_row_items(row)
        if items:
            t = format_frame_label(float(row["t_received"]))
            lines.append(f"- {t}: " + " / ".join(items))
    return "\n".join([header, *_keep_newest_lines(lines)])


def _describe_operation_row(row: Mapping[str, object]) -> str:
    """operations.jsonl の 1 行の説明 (きっかけで決まる説明か操作の説明。送信に失敗した行は失敗の説明を足す)。"""
    source = row.get("source")
    if source == "neutral":
        text = NEUTRAL_RESET_TEXT
    elif source == "stop":
        text = STOP_RESET_TEXT
    else:
        text = describe_operation(row)
    if row.get("ok") is False:
        error = row.get("error")
        if isinstance(error, str):
            text += f" (送信に失敗: {_clip(error)})"
        else:
            text += " (送信に失敗)"
    return text


@dataclass
class _OperationGroup:
    """operations.jsonl の、説明が同じ行が続いた区間。

    first_t   区間の最初の行の t
    last_t    区間の最後の行の t
    count     区間の行数
    text      行の説明
    """

    first_t: float
    last_t: float
    count: int
    text: str

    def line(self) -> str:
        """まとめの 1 行にする (1 行だけの区間は時刻を 1 つだけ書く)。"""
        first = format_frame_label(self.first_t)
        if self.count == 1:
            return f"- {first}: {self.text}"
        last = format_frame_label(self.last_t)
        return f"- {first}〜{last} ({self.count} 回): {self.text}"


def _surface_row_items(row: Mapping[str, object]) -> list[str]:
    """surface.jsonl の 1 行から、まとめに書く項目 (場面転換・情報ラベル・deep・外したボタン・失敗) をこの順に返す。"""
    items: list[str] = []
    if row.get("scene_changed") is True:
        items.append("場面転換を検知")
    labels = _info_labels(row.get("info_labels"))
    if labels:
        items.append("情報ラベル: " + ", ".join(labels))
    deep_requests = _strings(row.get("deep_requests"))
    if deep_requests:
        items.append("deep を求めた: " + ", ".join(deep_requests))
    blocked = _strings(row.get("blocked_buttons"))
    if blocked:
        items.append("押せないボタンを外した: " + ", ".join(blocked))
    error = _error_text(row)
    if error is not None:
        items.append(f"失敗: {_clip(error)}")
    return items


def _info_labels(value: object) -> list[str]:
    """info_labels の要素のうち、name が文字列で probability が有限の数のものを「<name> <確率>」の文にして返す。"""
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        probability = item.get("probability")
        if isinstance(name, str) and _is_finite_number(probability):
            labels.append(f"{_clip(name)} {float(probability):.2f}")
    return labels


def _error_text(row: Mapping[str, object]) -> str | None:
    """row の error が空でない文字列ならその値、それ以外は None を返す。"""
    error = row.get("error")
    return error if isinstance(error, str) and error else None


def _strings(value: object) -> list[str]:
    """value がリストなら、その文字列の要素を MAX_TEXT_CHARS 文字までにして返す。リストでなければ空のリスト。"""
    if not isinstance(value, list):
        return []
    return [_clip(item) for item in value if isinstance(item, str)]


def _stick_position(value: object) -> tuple[float, float] | None:
    """スティックの値 value が 2 つの有限の数 (bool を除く) のリストなら (x, y) を返す。それ以外は None。"""
    if not isinstance(value, list) or len(value) != 2:
        return None
    x, y = value
    if not _is_finite_number(x) or not _is_finite_number(y):
        return None
    return float(x), float(y)


def _is_finite_number(value: object) -> bool:
    """value が有限の数 (int・float。bool を除く) なら True。"""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _clip(text: str) -> str:
    """text が MAX_TEXT_CHARS 文字を超えたら、超えた分を「…」に置き換える。"""
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS] + "…"


def _keep_newest_lines(lines: list[str]) -> list[str]:
    """lines が MAX_SUMMARY_LINES 行を超えたら、新しい方の MAX_SUMMARY_LINES 行の前に省いた行数の行を置いて返す。"""
    if len(lines) <= MAX_SUMMARY_LINES:
        return lines
    omitted = len(lines) - MAX_SUMMARY_LINES
    return [f"(古い {omitted} 行を省いた)", *lines[-MAX_SUMMARY_LINES:]]
