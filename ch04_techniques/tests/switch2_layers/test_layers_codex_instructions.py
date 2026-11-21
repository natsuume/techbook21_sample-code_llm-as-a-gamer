import hashlib

import pytest

from switch2_layers.orchestrator import skill_copy, turn_input
from switch2_layers.orchestrator.codex_instructions import (
    CODEX_FRAMES_GUIDE_TEXT,
    CODEX_REPLACEMENTS,
    CODEX_TOOLS_SECTION,
    codex_delegate_instructions,
    codex_orchestrator_instructions,
    replace_exactly_once,
)
from switch2_layers.orchestrator.orchestrator_events import OrchestratorEvents
from switch2_layers.orchestrator.orchestrator_instructions import (
    delegate_instructions,
    handoff_request_text,
    orchestrator_instructions,
)
from switch2_layers.orchestrator.turn_input import (
    FRAMES_GUIDE_TEXT,
    SessionOpening,
    TurnContext,
    build_session_opening_text,
    build_turn_text,
)

# Claude 版の文の sha256 (Codex 版の文や、入力の文を変えられるようにした部品が、Claude 版の文を変えないことを確かめる。
# Claude 版の文を直すときは、この値も直す)
CLAUDE_TEXT_DIGESTS = {
    "orchestrator_instructions": "491e7e38ee6d33e23d700ca1ae71552ab5392af02015baa071a02c73c04c8fd1",
    "delegate_instructions": "7d293348929f31a3f984c23b7eaaa8df349742eaca4d7ad39fc5fe3fca789874",
    "handoff_request_text": "bf960eb129a95ed47aefa7ff5d7256477b45d7bf825abe571e11ba231c5e16e1",
    "FRAMES_GUIDE_TEXT": "952200646abcb646e44348cc39e275663f0b0b73c1a0844ee7212189070b03bb",
    "NONE_TEXT": "cf1211da3395d196a83a4f766b81009a7cc17ad65160fcc9dfe6569f57a9fc7b",
    # 決まった材料の build_turn_text と build_session_opening_text の出力
    "build_turn_text": "7ba1eeebc0bc8d45392b2b0ed84abdb682f3678d6b79d4880d5d00b6db7dc3cb",
    "build_session_opening_text": "6d87c072d20ec8506513dea8f28043df2509f0ab47487b4fa2691062f311f952",
    # skill_copy の写さなかった理由の文 (名前の順に改行でつないだもの)
    "skill_copy reasons": "a8a6133d6e9d371ba3b6492139e177eec28d6255ea879896d6faafe1246e913a",
}
SKILL_REASON_NAMES = [
    "DUPLICATED_KEY_REASON",
    "EXTRA_KEYS_REASON",
    "INVALID_DESCRIPTION_REASON",
    "INVALID_NAME_REASON",
    "MALFORMED_FRONTMATTER_REASON",
    "MISSING_KEYS_REASON",
    "NAME_MISMATCH_REASON",
    "NO_FRONTMATTER_REASON",
    "SHELL_COMMAND_REASON",
    "UNREADABLE_REASON",
    "UNUSUAL_LINE_BREAK_REASON",
]
# Codex 版の文に残ってはいけない、Claude Code のツールの名前と場所
CLAUDE_TOOL_WORDS = ("Write", "Edit", "Read", "Skill のツール", ".claude")
EVENTS = OrchestratorEvents(
    case_hits=(),
    monitor_ends=(),
    discarded_action_count=0,
    blocked_buttons=(),
    running=False,
    remaining_action_count=0,
    macros={},
    monitor=None,
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sample_turn_text(**changes: object) -> str:
    return build_turn_text(
        TurnContext(
            now_t=12.5, events=EVENTS, progress=None, previous_error=None, **changes
        )
    )


# --- Claude 版の文は変わらない ---


def test_claude_instructions_are_unchanged():
    assert (
        digest(orchestrator_instructions())
        == CLAUDE_TEXT_DIGESTS["orchestrator_instructions"]
    )
    assert (
        digest(delegate_instructions()) == CLAUDE_TEXT_DIGESTS["delegate_instructions"]
    )
    assert digest(handoff_request_text()) == CLAUDE_TEXT_DIGESTS["handoff_request_text"]


def test_claude_turn_texts_are_unchanged():
    assert (
        digest(turn_input.FRAMES_GUIDE_TEXT) == CLAUDE_TEXT_DIGESTS["FRAMES_GUIDE_TEXT"]
    )
    assert digest(turn_input.NONE_TEXT) == CLAUDE_TEXT_DIGESTS["NONE_TEXT"]
    assert digest(sample_turn_text()) == CLAUDE_TEXT_DIGESTS["build_turn_text"]
    opening = build_session_opening_text(
        SessionOpening(
            initial_instruction=None, note_files=("strategy.md",), handoff_text=""
        )
    )
    assert digest(opening) == CLAUDE_TEXT_DIGESTS["build_session_opening_text"]


def test_skill_copy_reasons_are_unchanged():
    names = sorted(name for name in dir(skill_copy) if name.endswith("_REASON"))
    assert names == SKILL_REASON_NAMES
    joined = "\n".join(getattr(skill_copy, name) for name in names)
    assert digest(joined) == CLAUDE_TEXT_DIGESTS["skill_copy reasons"]


# --- Codex 版の文 ---


@pytest.mark.parametrize(
    "codex, claude",
    [
        (codex_orchestrator_instructions, orchestrator_instructions),
        (codex_delegate_instructions, delegate_instructions),
    ],
)
def test_codex_instructions_name_the_codex_tools_and_not_the_claude_ones(codex, claude):
    text = codex()
    for word in CLAUDE_TOOL_WORDS:
        assert word not in text, word
    for word in ("apply_patch", "view_image", "シェル", ".agents/skills/"):
        assert word in text
    assert CODEX_TOOLS_SECTION + "## 答えの形\n" in text
    assert "書けるのは notes/ の下だけです。それ以外の場所への書き込みは" in text
    # 置き換えたところのほかは、Claude 版と同じ
    restored = text
    for old, new in reversed(CODEX_REPLACEMENTS):
        restored = restored.replace(new, old)
    assert restored == claude()


def test_codex_delegate_instructions_keep_the_delegate_sections():
    text = codex_delegate_instructions()
    assert "## 委任" in text
    assert '"delegate"' in text
    assert "## 監視\n" not in text


def test_codex_frames_guide_names_view_image():
    assert "Read" not in CODEX_FRAMES_GUIDE_TEXT
    assert "view_image" in CODEX_FRAMES_GUIDE_TEXT
    assert CODEX_FRAMES_GUIDE_TEXT.startswith("frames/latest/1.jpg〜5.jpg")
    assert CODEX_FRAMES_GUIDE_TEXT != FRAMES_GUIDE_TEXT


def test_codex_turn_text_uses_the_codex_frames_guide():
    text = sample_turn_text(frames_guide=CODEX_FRAMES_GUIDE_TEXT)
    assert text.endswith(f"## 古いフレーム\n{CODEX_FRAMES_GUIDE_TEXT}")
    for word in CLAUDE_TOOL_WORDS:
        assert word not in text, word


@pytest.mark.parametrize("text", ["置き換える文が無い", "前 X 中 X 後"])
def test_replace_exactly_once_rejects_missing_or_repeated_text(text):
    with pytest.raises(ValueError):
        replace_exactly_once(text, [("X", "Y")] if "X" in text else [("Z", "Y")])


def test_replace_exactly_once_replaces_in_order():
    assert replace_exactly_once("a b", [("a", "c"), ("c b", "d")]) == "d"
