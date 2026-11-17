"""詰まったときの助言の文書 (notes/advice.md) の「本体の画面」の節の本文を読む。

文書の形は deep の advice の種類のジョブの指示 (deep/prompts/advice.md) のとおりで、本体の画面の段階 (prepare・recover)
のための助言を、見出し SYSTEM_SCREEN_ADVICE_HEADING (`## 本体の画面`) の節に書く。読み方
(parse_system_screen_advice):
    - 前後の空白を除いて SYSTEM_SCREEN_ADVICE_HEADING と一致する最初の行を、節の見出しとする
    - 見出しの次の行から、次の `## ` で始まる見出しの行より前までを節の本文とし、前後の空白を除いて返す
    - 見出しの行が無い、または本文が空なら、空文字列を返す
本体の画面のループ (system_screen.system_screen_loop) が、本体の画面の middle を呼ぶたびに read_system_screen_advice で
読み、advice_for_phase で今の目的 (prepare・recover・clean_up) に合う助言だけを選んで、本体の画面の middle の入力
(middle.system_screen_types.SystemScreenInput.deep_advice) に入れる。deep は節の最初の行に、書いた段階を
「- 段階: <prepare か recover> (…)」の形で書く。
"""

import re
from pathlib import Path

from switch2_layers.notes.note_paths import ADVICE_NOTE
from switch2_layers.notes.note_reader import read_note_text

# 「本体の画面」の節の見出しの行
SYSTEM_SCREEN_ADVICE_HEADING = "## 本体の画面"


def parse_system_screen_advice(text: str) -> str:
    """助言の文書の本文 text から、「本体の画面」の節の本文を返す (読み方はモジュールの docstring)。I/O を扱わない。

    見出しの行が複数あれば、最初のものを使う。見出しの行が無い・本文が空 (空白だけを含む) なら空文字列を返す。
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != SYSTEM_SCREEN_ADVICE_HEADING:
            continue
        body: list[str] = []
        for section_line in lines[index + 1 :]:
            if section_line.startswith("## "):
                break
            body.append(section_line)
        return "\n".join(body).strip()
    return ""


def advice_for_phase(section: str, phase_name: str) -> str:
    """節の本文 section が段階 phase_name (目的の値) のために書かれたものなら section を、そうでなければ空文字列を返す。

    「段階」を含む最初の行で決める。その行から空白と強調の記号 (`*`・`` ` ``) を除き、全角のコロンを半角にした文に
    「段階:<phase_name>」があれば section を返す。「段階」を含む行が無ければ空文字列を返す。I/O を扱わない。
    準備のときに書かれた助言を、復帰や片付けの middle に渡さないために使う (deep は prepare・recover のときだけ書く
    ため、clean_up ではいつも空になる)。
    """
    phase_line = next((line for line in section.splitlines() if "段階" in line), "")
    compact = re.sub(r"[\s*`]", "", phase_line).replace("：", ":")
    return section if f"段階:{phase_name}" in compact else ""


def read_system_screen_advice(notes_dir: Path) -> str:
    """notes_dir の助言の文書 (note_paths.ADVICE_NOTE) を読み、parse_system_screen_advice の結果を返す。

    文書は notes.note_reader.read_note_text で読む。読めない文書 (無い・シンボリックリンクを解決した先が notes/ の中の
    通常のファイルでない・OSError・UTF-8 として読めない) は空文字列になり、空文字列を返す。例外は出さない。
    """
    return parse_system_screen_advice(read_note_text(notes_dir, ADVICE_NOTE))
