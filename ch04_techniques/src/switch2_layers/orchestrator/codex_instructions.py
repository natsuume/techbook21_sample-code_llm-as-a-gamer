"""主ループの Codex (--agent codex) に、スレッドの developerInstructions で渡す変わらない指示の文と、入力の
「古いフレーム」の節の文。

Claude 版の文 (orchestrator_instructions の orchestrator_instructions・delegate_instructions と、
turn_input.FRAMES_GUIDE_TEXT) から、ツールに関わる箇所だけを置き換えて作る。ほかの箇所 (答えの JSON の形・
監視と委任の決まり・安全の決まりなど) は Claude 版と同じ文にする。置き換えは次のとおり (CODEX_REPLACEMENTS):
    - 「## 答えの形」の節の前に「## ツール」の節を足す: シェル・apply_patch・view_image を使うこと、読める範囲と
      読めない範囲、書けるのは notes/ の下だけで、それ以外への書き込みは拒まれること
    - notes/ に書くツール: Write と Edit → apply_patch
    - スキル: .claude/skills/ と Skill のツール → .agents/skills/ と、一覧に載った SKILL.md をシェルで読むこと。
      写したスキルが同じセッションの一覧に載る時期は確かめていないため、一覧に無くても SKILL.md を読めば使えると書く
    - フレームを見るツール: Read → view_image
置き換える元の文が Claude 版の文にちょうど 1 回現れなければ、ValueError にする (Claude 版の文が変わったときに、
置き換えの漏れに気づくため)。引き継ぎの依頼の文 (handoff_request_text) は、ツールの名前を含まないため Claude 版を使う。
"""

from collections.abc import Sequence

from switch2_layers.orchestrator.orchestrator_instructions import (
    delegate_instructions,
    orchestrator_instructions,
)
from switch2_layers.orchestrator.turn_input import FRAMES_GUIDE_TEXT

# 「## 答えの形」の節の前に足す「## ツール」の節
CODEX_TOOLS_SECTION = """\
## ツール
使えるツールは、シェル (コマンドの実行)、apply_patch (ファイルの編集)、view_image (画像を見る) です。
シェルのコマンドと apply_patch は、サンドボックスの中で動きます。
作業ディレクトリーの frames/・notes/・.agents/skills/ は読めます。作業ディレクトリーの直下の記録のファイル (run.jsonl など) と、orchestrator/ などの記録のディレクトリーは読めません。
書けるのは notes/ の下だけです。それ以外の場所への書き込みは、apply_patch でもシェルのコマンドでも拒まれます。

"""

# (Claude 版の文, Codex 版の文) の組。指示の文の置き換えに、この順で使う
CODEX_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("\n## 答えの形\n", "\n" + CODEX_TOOLS_SECTION + "## 答えの形\n"),
    (
        "notes/ に、分かったこと (ボタンなどの操作の割り当て、攻略の情報、方針) を Write と Edit で書きます。",
        (
            "notes/ に、分かったこと (ボタンなどの操作の割り当て、攻略の情報、方針) を apply_patch で書きます。"
            "書けるのは notes/ の下だけです。"
        ),
    ),
    (
        ".claude/skills/ のスキルは、Skill のツールで使えます。",
        (
            ".agents/skills/ のスキルは、指示の中のスキルの一覧に、名前と説明と SKILL.md のパスが載ります。"
            "使うときは、その SKILL.md をシェルで読み (cat など)、書かれた手順に従います。"
        ),
    ),
    (
        "コードが SKILL.md を .claude/skills/ に写して読ませるため、書いたそのターンには使えないことに注意します。",
        (
            "コードが SKILL.md を .agents/skills/ に写すため、書いたそのターンには使えないことに注意します。"
            "写したスキルがスキルの一覧に載るのは、後のセッションになることがあります。"
            "一覧に無くても、.agents/skills/game-<名前>/SKILL.md をシェルで読めば使えます。"
        ),
    ),
    (
        "いつ Read しても、その時点の最新の画面が読めます。",
        "いつ view_image で開いても、その時点の最新の画面が見えます。",
    ),
)
# 入力の「古いフレーム」の節の置き換え
CODEX_FRAMES_GUIDE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "いつ Read してもその時点の最新の画面が読める",
        "いつ view_image で開いてもその時点の最新の画面が見える",
    ),
)


def replace_exactly_once(text: str, replacements: Sequence[tuple[str, str]]) -> str:
    """text の中の、replacements の (元の文, 新しい文) の元の文を、順に新しい文に置き換えて返す (I/O なし)。

    それぞれの元の文は、その時点の text にちょうど 1 回現れなければならない。0 回か 2 回以上なら ValueError にする。
    """
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise ValueError(f"expected exactly one {old!r} in the text, found {count}")
        text = text.replace(old, new)
    return text


def codex_orchestrator_instructions() -> str:
    """--mode orchestrator の Codex 版の変わらない指示の文を返す (orchestrator_instructions() に CODEX_REPLACEMENTS)。"""
    return replace_exactly_once(orchestrator_instructions(), CODEX_REPLACEMENTS)


def codex_delegate_instructions() -> str:
    """--mode delegate の Codex 版の変わらない指示の文を返す (delegate_instructions() に CODEX_REPLACEMENTS)。"""
    return replace_exactly_once(delegate_instructions(), CODEX_REPLACEMENTS)


# 入力の「## 古いフレーム」の節の Codex 版の文 (FRAMES_GUIDE_TEXT に CODEX_FRAMES_GUIDE_REPLACEMENTS)
CODEX_FRAMES_GUIDE_TEXT = replace_exactly_once(
    FRAMES_GUIDE_TEXT, CODEX_FRAMES_GUIDE_REPLACEMENTS
)
