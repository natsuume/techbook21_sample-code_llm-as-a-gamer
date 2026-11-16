import re
from collections.abc import Sequence

import pytest
from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.orchestrator.orchestrator_instructions import (
    HANDOFF_NOTE,
    PROCEDURE_MAX_COUNT,
    handoff_request_text,
    orchestrator_instructions,
)
from switch2_layers.safety.safety_rules import SAFETY_RULES


def sentences(text: str) -> list[str]:
    """text を文 (。か改行で区切った部分) に分ける。"""
    return [part for part in re.split(r"[。\n]", text) if part.strip()]


def sentences_with(
    text: str, required: Sequence[str], any_of: Sequence[str] = ()
) -> list[str]:
    """required の語をすべて含み、any_of が空でなければそのどれかも含む文を返す。

    指示の項目ごとに、その項目の手がかりの語が 1 つの文の中にそろっているかを確かめる (言い回しには結合させない)。
    """
    return [
        sentence
        for sentence in sentences(text)
        if all(word in sentence for word in required)
        and (not any_of or any(word in sentence for word in any_of))
    ]


def test_instructions_contain_the_safety_rules_verbatim():
    assert SAFETY_RULES in orchestrator_instructions()


def test_instructions_can_be_passed_as_the_appended_system_prompt():
    text = orchestrator_instructions()
    assert text
    assert not text.startswith("-")


@pytest.mark.parametrize(
    "word",
    [
        "```json",
        '"actions"',
        '"macros"',
        '"monitor"',
        '"press"',
        '"wait"',
        '"procedure"',
        '"macro"',
        '"buttons"',
        '"sticks"',
        '"seconds"',
        '"left"',
        '"right"',
        '"set"',
        '"delete"',
        '"create"',
        '"stop"',
        '"cases"',
        '"condition"',
        '"repeat"',
        '"every"',
        '"once"',
    ],
)
def test_instructions_describe_the_json_form(word):
    assert word in orchestrator_instructions()


@pytest.mark.parametrize(
    "word",
    ["tap_repeat", "walk", "button", "count", "interval_seconds", "direction"],
)
def test_instructions_describe_the_code_procedures(word):
    assert word in orchestrator_instructions()


def test_instructions_list_every_button_name_and_the_hold_limit():
    text = orchestrator_instructions()
    for button in BUTTON_ORDER:
        assert button in text
    assert f"{MAX_HOLD_SECONDS:g}" in text


def test_instructions_describe_skills_notes_and_old_frames():
    text = orchestrator_instructions()
    assert "notes/skills/game-" in text
    assert "SKILL.md" in text
    assert "name" in text
    assert "description" in text
    assert ".claude/skills/" in text
    assert "notes/" in text
    assert "frames/interval/" in text


def test_instructions_state_the_role_of_playing_from_the_screen_only():
    assert sentences_with(orchestrator_instructions(), ("画面", "ゲームを進め"))


def test_instructions_say_the_monitor_pauses_while_claude_actions_run():
    assert sentences_with(
        orchestrator_instructions(),
        ("監視", "実行中"),
        any_of=("止ま", "止め", "休", "判定しない"),
    )


def test_instructions_say_new_actions_replace_the_rest_of_the_previous_actions():
    assert sentences_with(
        orchestrator_instructions(), ("actions", "残り"), any_of=("置き換", "捨て")
    )


def test_instructions_say_empty_actions_let_the_previous_actions_continue():
    assert sentences_with(orchestrator_instructions(), ("空", "actions", "続"))


def test_instructions_say_when_to_answer_with_empty_actions():
    text = orchestrator_instructions()
    # 操作の実行中で続けさせたいとき
    assert sentences_with(text, ("空", "actions", "実行中"))
    # 監視に任せて待つとき
    assert sentences_with(text, ("空", "actions", "監視", "待"))


def test_instructions_say_monitor_conditions_describe_only_the_screen():
    text = orchestrator_instructions()
    assert sentences_with(text, ("条件", "見た目"))
    assert sentences_with(text, ("条件", "ゲーム名", "固有"))


def test_instructions_say_to_write_findings_in_notes():
    found = [
        sentence
        for sentence in sentences_with(
            orchestrator_instructions(), ("notes/", "分かったこと", "攻略", "方針")
        )
        # notes/skills/ のスキルの文ではなく、分かったことを書く場所としての notes/ を確かめる
        if "notes/" in sentence.replace("notes/skills/", "")
    ]
    assert found


def test_instructions_say_game_skills_go_to_notes_and_wait_a_turn():
    text = orchestrator_instructions()
    assert sentences_with(text, ("notes/skills/game-", "SKILL.md"))
    assert sentences_with(text, ("ターン", "使えない"), any_of=("書いた", "写"))


def test_procedure_count_limit_is_the_one_of_the_action_executor():
    # 操作の実行の部品は、tap_repeat・walk の count を 1 以上 100 以下の整数にし、超えると JSON の誤りにする
    assert PROCEDURE_MAX_COUNT == 100


def test_instructions_state_the_upper_limit_of_procedure_counts():
    assert sentences_with(
        orchestrator_instructions(), ("count", str(PROCEDURE_MAX_COUNT))
    )


def test_instructions_state_the_rules_for_copying_game_skills():
    text = orchestrator_instructions()
    # frontmatter は name と description だけ
    assert sentences_with(
        text, ("frontmatter", "name", "description"), any_of=("だけ",)
    )
    # シェルのコマンドを実行する書き方を書かない
    assert sentences_with(text, ("シェル",))
    # 守らないスキルは写されず、理由が次の入力の節で届く
    assert sentences_with(text, ("写さなかったスキル",))


def test_handoff_request_asks_for_the_handoff_note_and_json_without_actions():
    text = handoff_request_text()
    assert f"notes/{HANDOFF_NOTE}" in text
    assert HANDOFF_NOTE == "handoff.md"
    assert "```json" in text
    assert "actions" in text
    for item in ("目標", "分かったこと", "効かなかったこと", "監視", "マクロ"):
        assert item in text
