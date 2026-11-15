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

from collections.abc import Mapping, Sequence

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
    raise NotImplementedError


def summarize_operation_rows(rows: Sequence[Mapping[str, object]]) -> str:
    """operations.jsonl の行 rows (書かれた順) を、モジュールの docstring の形の複数行の文にまとめて返す。"""
    raise NotImplementedError


def summarize_surface_rows(rows: Sequence[Mapping[str, object]]) -> str:
    """surface.jsonl の行 rows (書かれた順) を、モジュールの docstring の形の複数行の文にまとめて返す。"""
    raise NotImplementedError
