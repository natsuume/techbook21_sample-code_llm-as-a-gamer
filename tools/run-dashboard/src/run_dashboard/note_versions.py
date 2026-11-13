"""requests.jsonl の state_text (要求に入れたゲームの情報の 4 ファイルの内容) から、ゲームの情報の版を復元する。

state_text は第4章の switch2_agent.game_notes.format_game_notes の書式で、NOTE_KINDS の順に
`## <NOTE_TITLES の見出し>\\n<中身>` を空行 1 つ (\\n\\n) でつないだもの。state_text が前の要求と変わった要求ごとに
1 つの版とし、変わったファイルの種類と、それを書き換えた advisor の実行を求める。I/O を持たない。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from run_dashboard.advisor_runs import AdvisorRun

# advisor の実行が終わった後も、その実行の書き換えとみなす時間 (秒)。advisor がファイルを書いてから
# 実行の終了が記録されるまでの間や、書いた直後の要求で、変わった内容が読まれることがあるため
NOTE_CHANGE_GRACE_S = 1.0
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
    raise NotImplementedError


def build_note_versions(
    requests: Sequence[dict], advisor_runs: Sequence[AdvisorRun]
) -> list[NoteVersion]:
    """要求の行 (ファイルの順) から、ゲームの情報の版の一覧を返す。

    state_text が文字列で t_sent が数 (bool を除く) の行だけを使う。その最初の行が版 0 (initial は True、
    changed は空のリスト)。以後、state_text が直前の版と違う行ごとに次の版を作る (t はその行の t_sent、
    request はその行の number、files はその行の state_text の split_state_text)。
    changed は、NOTE_KINDS の順に、分けた中身が直前の版と違う種類ごとに 1 件。どちらかの版を分けられなければ
    (split_state_text が None)、changed は空のリストにする。
    各件の by は、その種類を受け持つ役割 (advisor_roles.ADVISOR_ROLES の note_kind がその種類の役割) の
    advisor_runs の実行のうち、t_start が数で、t_start <= t_sent かつ (t_end が None か
    t_sent <= t_end + NOTE_CHANGE_GRACE_S) の実行の名前。当てはまる実行が複数あれば advisor_runs の順で最初の実行。
    無ければ EXTERNAL_CHANGE ("external")。
    """
    raise NotImplementedError


def note_version_of_request(
    versions: Sequence[NoteVersion], number: int, requests: Sequence[dict]
) -> int | None:
    """number の要求を送った時点のゲームの情報の版の番号を返す。

    requests (ファイルの順) の中で number の要求の行より前 (その行を含む) に始まった版のうち、最後の版の version。
    number の要求の行が requests に無いか、当てはまる版が無ければ None を返す。
    """
    raise NotImplementedError
