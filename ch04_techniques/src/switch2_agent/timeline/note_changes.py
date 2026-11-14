"""系列情報の「ゲームの情報の更新時刻」(notes の節)。4 ファイルの中身が、それぞれ最後に変わった時刻を追う。

Pipeline が要求のたびに読んだゲームの情報を NoteChangeTracker.observe に渡す。中身 (前後の空白を除いたもの) が
前回と違えば、その要求の時刻を「書き換わった時刻」にする。advisor がファイルを書き換えてから、次の要求で読むまでの
差は含まない。Pipeline のスレッドだけから呼ぶため、ロックは持たない。
"""

from collections.abc import Mapping

from switch2_agent.game_notes import NOTE_KINDS, NOTE_TITLES
from switch2_agent.timeline.moments import format_moment


class NoteChangeTracker:
    """ゲームの情報の種別ごとに、中身が最後に変わった時刻を持つ。

    属性:
        first_observed_at   最初に observe した時刻 (実行の開始)。まだなら None
        contents            種別 → 前回 observe した中身 (前後の空白を除いたもの)
        changed_at          種別 → 中身が最後に変わった時刻。最初に observe してから変わっていない種別は含まない
    """

    def __init__(self):
        """まだ何も observe していない状態で作る。"""
        self.first_observed_at: float | None = None
        self.contents: dict[str, str] = {}
        self.changed_at: dict[str, float] = {}

    def observe(self, notes: Mapping[str, str], now: float) -> None:
        """要求の時刻 now に読んだゲームの情報 notes (種別 → 中身) を受け取る。

        最初の呼び出しでは、first_observed_at を now にし、中身を覚えるだけにする。2 回目からは、
        game_notes.NOTE_KINDS の種別ごとに、中身 (前後の空白を除いたもの。notes に無い種別は空文字) が前回と
        違えば changed_at[種別] を now にする。
        """
        current = {kind: notes.get(kind, "").strip() for kind in NOTE_KINDS}
        if self.first_observed_at is None:
            self.first_observed_at = now
        else:
            for kind, content in current.items():
                if content != self.contents.get(kind, ""):
                    self.changed_at[kind] = now
        self.contents = current


def format_note_changes(tracker: NoteChangeTracker, now: float, origin: float) -> str:
    """ゲームの情報の更新時刻の節の文を返す。まだ何も observe していなければ空文字を返す。

    1 行目は見出し `## ゲームの情報の更新 (最後に書き換わった時刻)`。続けて NOTE_KINDS の順に、種別ごとに 1 行:
        書き換わった種別:     `- <NOTE_TITLES の見出し>: <format_moment(書き換わった時刻)> に書き換わった`
        書き換わっていない種別: `- <見出し>: この実行の間は書き換わっていない (実行の開始 <format_moment(first_observed_at)> から同じ)`
    """
    if tracker.first_observed_at is None:
        return ""
    lines = ["## ゲームの情報の更新 (最後に書き換わった時刻)"]
    for kind in NOTE_KINDS:
        title = NOTE_TITLES[kind]
        changed = tracker.changed_at.get(kind)
        if changed is None:
            start = format_moment(tracker.first_observed_at, now, origin)
            lines.append(
                f"- {title}: この実行の間は書き換わっていない (実行の開始 {start} から同じ)"
            )
        else:
            lines.append(
                f"- {title}: {format_moment(changed, now, origin)} に書き換わった"
            )
    return "\n".join(lines)
