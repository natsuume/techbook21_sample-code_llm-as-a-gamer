"""requests.jsonl の state_text (要求に入れたゲームの情報の 4 ファイルの内容) から、ゲームの情報の版を復元する。

state_text は第4章の switch2_agent.game_notes.format_game_notes の書式で、NOTE_KINDS の順に
`## <NOTE_TITLES の見出し>\\n<中身>` を空行 1 つ (\\n\\n) でつないだもの。state_text が前の要求と変わった要求ごとに
1 つの版とし、変わったファイルの種類と、それを書き換えた advisor の実行を求める。I/O を持たない。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.game_notes import NOTE_KINDS, NOTE_TITLES

from run_dashboard.advisor_runs import AdvisorRun
from run_dashboard.jsonl_lines import is_json_number

# 書き換えた advisor の実行が見つからない変更の by
EXTERNAL_CHANGE = "external"


@dataclass(frozen=True)
class NoteVersion:
    """ゲームの情報の版 1 つ。

    version   版の番号 (0 始まり。最初の要求の版が 0)
    t         その版を最初に送った要求の送信時刻 (t_sent の経過秒)
    request   その版を最初に送った要求の number
    initial   最初の版なら True
    changed   前の版から変わった種類ごとの {"kind": 種類, "by": 書き換えた advisor の実行の名前か "external"}
              (NOTE_KINDS の順。最初の版は空のリスト)
    files     state_text を split_state_text で分けた、種類 → 中身。分けられなければ None
    """

    version: int
    t: float
    request: int
    initial: bool
    changed: list[dict]
    files: dict[str, str] | None


def split_state_text(state_text: str) -> dict[str, str] | None:
    """state_text を、format_game_notes の書式に従って種類 (NOTE_KINDS) → 中身の辞書に分ける。

    NOTE_KINDS の順に見出し `## <NOTE_TITLES の見出し>` を探す。最初の種類は、state_text が `## <見出し>\\n` で
    始まること。2 つ目以降の種類は、前の種類の見出しの後で最初に現れる `\\n\\n## <見出し>\\n` を区切りにする。
    各種類の中身は、その見出しの行の後から次の区切りの前まで (最後の種類は文字列の終わりまで)。
    中身の EMPTY_NOTE_TEXT (`(まだ書かれていない)`) は変えずにそのまま入れる。
    中身の行に `## ` の見出しがあっても (strategy.md の節など)、4 つの見出しの文字列と一致しなければ区切りにしない。
    見出しが順に見つからなければ None を返す。
    """
    first_heading = f"## {NOTE_TITLES[NOTE_KINDS[0]]}\n"
    if not state_text.startswith(first_heading):
        return None
    files: dict[str, str] = {}
    content_start = len(first_heading)
    for kind, next_kind in pairwise(NOTE_KINDS):
        separator = f"\n\n## {NOTE_TITLES[next_kind]}\n"
        separator_start = state_text.find(separator, content_start)
        if separator_start < 0:
            return None
        files[kind] = state_text[content_start:separator_start]
        content_start = separator_start + len(separator)
    files[NOTE_KINDS[-1]] = state_text[content_start:]
    return files


def build_note_versions(
    requests: Sequence[dict], advisor_runs: Sequence[AdvisorRun]
) -> list[NoteVersion]:
    """要求の行 (ファイルの順) から、ゲームの情報の版の一覧を返す。

    state_text が文字列で t_sent が数 (bool を除く) の行だけを使う。その最初の行が版 0 (initial は True、
    changed は空のリスト)。以後、state_text が直前の版と違う行ごとに次の版を作る (t はその行の t_sent、
    request はその行の number、files はその行の state_text の split_state_text)。
    changed は、NOTE_KINDS の順に、分けた中身が直前の版と違う種類ごとに 1 件。どちらかの版を分けられなければ
    (split_state_text が None)、changed は空のリストにする。
    各件の by は次のように決める。変わった行の t_sent を ts、その直前に使った行 (前の版の内容を読んだ最後の行。
    state_text が文字列で t_sent が数の行) の t_sent を tp とする。ファイルは tp から ts までの間に書き換えられた。
    その種類を受け持つ役割 (advisor_roles.ADVISOR_ROLES の note_kind がその種類の役割) の advisor_runs の実行のうち、
    t_start が数で t_start <= ts、かつ (t_end が None か t_end >= tp) の実行 (その間に動いていた実行) の名前。
    当てはまる実行が複数あれば advisor_runs の順で最初の実行。無ければ EXTERNAL_CHANGE ("external")。
    終了の後に何秒まで書き換えとみなすか、という固定の猶予は使わない (応答に時間のかかった要求の後でも、
    その間に終わった advisor の書き換えとみなせるようにするため)。
    """
    versions: list[NoteVersion] = []
    previous_text: str | None = None
    previous_t_sent = 0.0
    for request in requests:
        state_text = request.get("state_text")
        t_sent = request.get("t_sent")
        if not isinstance(state_text, str) or not is_json_number(t_sent):
            continue
        if previous_text is None or state_text != previous_text:
            files = split_state_text(state_text)
            changed = (
                []
                if previous_text is None
                else _changed_kinds(
                    versions[-1].files,
                    files,
                    previous_t_sent,
                    float(t_sent),
                    advisor_runs,
                )
            )
            versions.append(
                NoteVersion(
                    version=len(versions),
                    t=float(t_sent),
                    request=request.get("number"),
                    initial=previous_text is None,
                    changed=changed,
                    files=files,
                )
            )
            previous_text = state_text
        previous_t_sent = float(t_sent)
    return versions


def _changed_kinds(
    previous_files: Mapping[str, str] | None,
    files: Mapping[str, str] | None,
    previous_t_sent: float,
    t_sent: float,
    advisor_runs: Sequence[AdvisorRun],
) -> list[dict]:
    """前の版の files と新しい版の files で中身が違う種類ごとの {"kind", "by"} を、NOTE_KINDS の順に返す。

    previous_t_sent は前の版の内容を読んだ最後の行の t_sent (tp)、t_sent は変わった行の t_sent (ts)。
    どちらかの files が None (分けられなかった) なら空のリストを返す。
    """
    if previous_files is None or files is None:
        return []
    return [
        {"kind": kind, "by": _changed_by(kind, previous_t_sent, t_sent, advisor_runs)}
        for kind in NOTE_KINDS
        if files[kind] != previous_files[kind]
    ]


def _changed_by(
    kind: str,
    previous_t_sent: float,
    t_sent: float,
    advisor_runs: Sequence[AdvisorRun],
) -> str:
    """種類 kind のファイルを、previous_t_sent (tp) から t_sent (ts) までの間に書き換えた advisor の実行の名前を返す。

    build_note_versions の docstring の by の決め方。当てはまる実行が無ければ EXTERNAL_CHANGE を返す。
    """
    role_names = {role.name for role in ADVISOR_ROLES if role.note_kind == kind}
    for run in advisor_runs:
        if run.role not in role_names or not is_json_number(run.t_start):
            continue
        if run.t_start <= t_sent and (
            run.t_end is None or run.t_end >= previous_t_sent
        ):
            return run.name
    return EXTERNAL_CHANGE


def note_version_of_request(
    versions: Sequence[NoteVersion], number: int, requests: Sequence[dict]
) -> int | None:
    """number の要求を送った時点のゲームの情報の版の番号を返す。

    requests (ファイルの順) の中で number の要求の行より前 (その行を含む) に始まった版のうち、最後の版の version。
    number の要求の行が requests に無いか、当てはまる版が無ければ None を返す。
    """
    # 要求の number → requests の中の位置 (同じ number の行が複数あれば最初の行)
    positions: dict[object, int] = {}
    for position, request in enumerate(requests):
        positions.setdefault(request.get("number"), position)
    request_position = positions.get(number)
    if request_position is None:
        return None
    version_number = None
    for version in versions:
        start_position = positions.get(version.request)
        if start_position is not None and start_position <= request_position:
            version_number = version.version
    return version_number
