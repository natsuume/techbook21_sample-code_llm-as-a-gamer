"""advisor の実行 (advisor-<役割>-<連番>/) と、それを呼んだ要求の対応づけと、実行の途中経過 (steps)。

対応づけ: 役割ごとに、連番 k の実行 (advisor-<役割>-<k>/) は、run.jsonl の decision の行のうち
advisor_accepted にその役割を含む k 番目の行の要求に対応する (第4章の advisor は、受け付けた要求ごとに連番を
1 つ進め、Pipeline はその要求の decision の行に受け付けた役割を書くため)。
途中経過: stream.jsonl の行 (claude -p の stream-json の出力) から、ツールの呼び出し・ツールの結果・
拒否された操作・最後の結果を、時刻の順に並べる。
result.json を読む関数は外から受け取る (build_advisor_runs の read_result_json)。ほかに I/O を持たない。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from run_dashboard.run_directory import AdvisorRunDir
from run_dashboard.run_records import RunEvent

# summarize_tool_input が、ツールの入力から 1 行の要約に使うキー (この順に探す)
SUMMARY_INPUT_KEYS = ("file_path", "path", "skill", "pattern", "command", "url")
# 要約の 1 行の文字数の上限
SUMMARY_MAX_CHARS = 120

AdvisorRunStatus = Literal["done", "running"]


@dataclass(frozen=True)
class AdvisorRun:
    """advisor の実行 1 件。

    name                  ディレクトリー名 (例: advisor-strategy-001)
    role                  役割の name (例: strategy)
    request               対応する要求の number。対応する要求が無ければ None
    t_start               開始の経過秒。求められなければ None
    t_end                 終了の経過秒。求められなければ (実行中など) None
    returncode            claude の終了コード。分からなければ None
    denials               拒否された操作 (permission_denials) の数。分からなければ None
    result                claude -p の答えの文。分からなければ None
    status                result.json があれば "done"、無ければ "running"
    result_json           result.json の中身。無ければ None
    denied_tool_use_ids   result.json の permission_denials の tool_use_id の集合 (無ければ空)
    """

    name: str
    role: str
    request: int | None
    t_start: float | None
    t_end: float | None
    returncode: int | None
    denials: int | None
    result: str | None
    status: AdvisorRunStatus
    result_json: dict | None
    denied_tool_use_ids: frozenset[str]


def build_advisor_runs(
    advisor_dirs: Sequence[AdvisorRunDir],
    requests: Sequence[dict],
    run_events: Sequence[RunEvent],
    read_result_json: Callable[[Path], dict | None],
) -> list[AdvisorRun]:
    """advisor の実行の一覧を、advisor_dirs の順 (役割の順、同じ役割の中は連番の昇順) で返す。

    read_result_json(path) は result.json のファイルのパス
    (advisor_dir.path / switch2_agent.advisors.claude_code_headless.RESULT_FILE_NAME) を受け取り、その中身の辞書か
    None (無い、読めない) を返す関数 (run_records.read_result_json。テストでは偽物を渡す)。

    連番 k (AdvisorRunDir.index。advisor_dirs の中の位置ではない) の実行を、run_events (ファイルの順) のうち
    record の event が "decision" で、advisor_accepted (リスト) にその役割の name を含む k 番目の行に対応づける
    (requests.jsonl の書き込みの失敗や、advisor のディレクトリーの欠けで、対応がずれないようにするため。
    advisor_accepted がリストでない行は数えない)。requests は対応づけには使わず、t_start を求めるためだけに使う。
    各実行の値:
        - request: 対応する decision の行の number (int のとき)。対応する行が無ければ None
        - duration_s・returncode・denials・result: result.json があれば、その duration_s、returncode、
          permission_denials の件数、output が object ならその result から。result.json が無ければ、run_events の
          advisor の行 (下) の duration_s、returncode、permission_denials の件数、result から。
          どちらも無ければ None。duration_s は数 (bool を除く)、returncode は int (bool を除く)、denials は
          permission_denials がリストのときの件数、result は文字列のときだけ使い、それ以外は None
        - requests (ファイルの順) に number が request の要求の行があるとき: t_start はその t_received
          (数でなければ None)、t_end は t_start + duration_s (どちらかが None なら None)
        - その要求の行が無いとき (request が None のときを含む): run_events のうち record の event が "advisor" で、
          Path(record の run_dir).name が実行のディレクトリー名と同じ最初の行の t を t_end にし、
          t_start は t_end - duration_s (どちらかが None なら None)。その行が無ければ t_end も t_start も None
        - status: result.json があれば "done"、無ければ "running"。result_json はその中身 (無ければ None)
        - denied_tool_use_ids: result.json の permission_denials の各要素の tool_use_id (文字列のもの)
    """
    raise NotImplementedError


def summarize_tool_input(tool_input: object) -> str:
    """ツールの入力 (tool_use の input) から、1 行で読める部分 (パスや Skill の名前など) を返す。

    tool_input が object (辞書) なら、SUMMARY_INPUT_KEYS の順に探し、値が文字列の最初のキーの値の 1 行目
    (str.splitlines の最初の行。空文字なら空文字) を、先頭から SUMMARY_MAX_CHARS 文字までにして返す。
    当てはまるキーが無いか、object でなければ空文字を返す。
    """
    raise NotImplementedError


def first_line(text: object, limit: int = SUMMARY_MAX_CHARS) -> str:
    """ツールの結果や最後の結果の文から、1 行目を返す。

    text が文字列なら、その 1 行目 (str.splitlines の最初の行。空文字なら空文字) を先頭から limit 文字まで。
    {"type": "text", "text": <文字列>} の項目を含むリストなら、最初のその項目の text の 1 行目を limit 文字まで。
    それ以外は空文字を返す。
    """
    raise NotImplementedError


def build_steps(
    stream_records: Sequence[dict],
    origin_mono: float | None,
    denied_from_result_json: frozenset[str],
) -> list[dict]:
    """stream.jsonl の行 (run_records.read_advisor_stream の結果) から、途中経過の項目のリストを作る (ファイルの順)。

    各項目は {"t", "kind", "tool", "summary"}。t はその行の elapsed_time.mono_elapsed(mono, origin_mono)。
    data を持たない行 (raw の行) と、下に挙げない行 (system・rate_limit_event・assistant の text や thinking など) は
    入れない。
        - data.type が "assistant": data.message.content のリストの、type が "tool_use" の項目ごとに
          {"kind": "tool_use", "tool": 項目の name, "summary": summarize_tool_input(項目の input)}
        - data.type が "user": data.message.content のリストの、type が "tool_result" の項目ごとに
          {"kind": "tool_result" か "permission_denied", "tool": それより前の、id が項目の tool_use_id と同じ
          tool_use の name (無ければ None), "summary": first_line(項目の content)}
        - data.type が "result": {"kind": "result", "tool": None, "summary": first_line(data.result)}
    拒否の id: stream_records に result の行があれば、その最後の行の permission_denials の各要素の tool_use_id。
    result の行が無ければ denied_from_result_json (result.json の permission_denials の tool_use_id)。
    tool_use_id が拒否の id に入っている tool_result を "permission_denied" にし、それ以外は "tool_result" にする
    (result の行も result.json も無い実行中の実行では、すべて "tool_result" のまま)。
    """
    raise NotImplementedError
