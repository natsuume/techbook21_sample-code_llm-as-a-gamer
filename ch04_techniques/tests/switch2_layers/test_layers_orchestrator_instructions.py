import re
from collections.abc import Sequence

import pytest
from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.orchestrator.decision_parser import (
    MAX_DELEGATE_CAUTIONS,
    MAX_DELEGATE_CHOICES,
    MAX_DELEGATE_TASKS,
)
from switch2_layers.orchestrator.orchestrator_instructions import (
    HANDOFF_NOTE,
    PROCEDURE_MAX_COUNT,
    delegate_instructions,
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


def test_instructions_describe_the_latest_frames():
    text = orchestrator_instructions()
    # frames/latest/1.jpg〜5.jpg は最新の 5 枚で、1.jpg が最新
    assert sentences_with(text, ("frames/latest/1.jpg", "5.jpg"))
    assert sentences_with(text, ("1.jpg", "最新"))
    # いつ Read しても、その時点の最新の画面が読める
    assert sentences_with(text, ("Read", "最新"), any_of=("いつ",))


def test_instructions_say_older_frames_are_in_interval_named_by_milliseconds():
    assert sentences_with(orchestrator_instructions(), ("frames/interval/", "ミリ秒"))


def test_instructions_do_not_say_to_read_frames_only_when_needed():
    assert (
        sentences_with(
            orchestrator_instructions(), ("必要なときだけ",), any_of=("読", "Read")
        )
        == []
    )


def test_instructions_and_handoff_request_have_no_game_title():
    # どちらの文にもゲーム名を書かない (ゲーム名を渡すのは、人の初期指示に書かれている場合だけ)
    for text in (orchestrator_instructions(), handoff_request_text()):
        for title in ("ロックマン", "エグゼ"):
            assert title not in text


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


def test_instructions_say_the_next_input_comes_after_the_actions_end_and_settle():
    # actions の操作の列が終わり、最後にボタンとスティックを離してから 0.3 秒たってから次の画面と出来事が届く
    assert sentences_with(
        orchestrator_instructions(), ("離", "0.3"), any_of=("届", "次")
    )


def test_instructions_say_the_next_input_comes_at_once_only_for_empty_actions():
    # 「答えを返すと、すぐ次が届く」とは書かない (すぐ届くのは、空の actions のときだけ)
    at_once = sentences_with(orchestrator_instructions(), ("すぐ", "届"))
    assert [sentence for sentence in at_once if "空" not in sentence] == []


def test_instructions_say_empty_actions_mean_doing_nothing():
    assert sentences_with(
        orchestrator_instructions(), ("空", "actions"), any_of=("何もしない",)
    )


def test_instructions_do_not_say_empty_actions_let_the_previous_actions_continue():
    assert sentences_with(orchestrator_instructions(), ("空", "actions", "続")) == []


def test_instructions_say_to_answer_with_empty_actions_to_wait_for_the_monitor():
    assert sentences_with(orchestrator_instructions(), ("空", "actions", "監視", "待"))


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


def test_instructions_show_how_to_write_the_skill_frontmatter():
    # frontmatter は name: game-<名前> と description: "<どの場面で使うか>" の 2 行で書く
    assert sentences_with(
        orchestrator_instructions(), ("name: game-", 'description: "')
    )


def test_handoff_request_asks_for_the_handoff_note_and_json_without_actions():
    text = handoff_request_text()
    assert f"notes/{HANDOFF_NOTE}" in text
    assert HANDOFF_NOTE == "handoff.md"
    assert "```json" in text
    assert "actions" in text
    for item in ("目標", "分かったこと", "効かなかったこと", "監視", "マクロ"):
        assert item in text


# --- 委任型の版 (delegate_instructions) ---


def test_delegate_instructions_contain_the_safety_rules_verbatim():
    assert SAFETY_RULES in delegate_instructions()


@pytest.mark.parametrize(
    "word",
    [
        "```json",
        '"actions"',
        '"macros"',
        '"delegate"',
        '"start"',
        '"update"',
        '"stop"',
        '"tasks"',
        '"cautions"',
        '"choices"',
        '"name"',
        '"description"',
        '"press"',
        '"wait"',
        '"procedure"',
        '"macro"',
    ],
)
def test_delegate_instructions_describe_the_json_form(word):
    assert word in delegate_instructions()


def test_delegate_instructions_list_every_button_name():
    # 主ループが押せるボタンは、監視型と同じ 17 個
    text = delegate_instructions()
    for button in BUTTON_ORDER:
        assert button in text


def test_delegate_instructions_state_the_limits_of_the_values():
    text = delegate_instructions()
    assert sentences_with(text, ("choices", f"{MAX_DELEGATE_CHOICES}"))
    assert sentences_with(text, ("tasks", f"{MAX_DELEGATE_TASKS}"))
    assert sentences_with(text, ("cautions", f"{MAX_DELEGATE_CAUTIONS}"))
    assert sentences_with(text, ("HOME", "マクロ"))


def test_delegate_instructions_say_the_first_task_is_the_most_important():
    assert sentences_with(delegate_instructions(), ("tasks", "最初", "最も重要"))


def test_delegate_instructions_say_to_write_avoiding_attacks_in_the_tasks():
    assert sentences_with(delegate_instructions(), ("攻撃", "避け", "tasks"))


def test_delegate_instructions_say_to_write_observed_attack_moves_in_the_cautions():
    assert sentences_with(delegate_instructions(), ("攻撃", "動き", "cautions"))


def test_delegate_instructions_say_not_to_write_proper_names():
    assert sentences_with(
        delegate_instructions(), ("固有", "名前"), any_of=("タスク", "tasks")
    )


def test_delegate_instructions_say_to_name_choices_by_their_intent():
    text = delegate_instructions()
    assert sentences_with(text, ("ボタンの名前", "意図", "名前", "説明"))
    assert sentences_with(text, ("説明", "選ぶ場面"))


def test_delegate_instructions_say_moves_can_be_combined_with_attacks():
    assert sentences_with(delegate_instructions(), ("移動", "攻撃", "1 つ", "マクロ"))


def test_delegate_instructions_say_to_always_delegate_scenes_where_time_runs():
    text = delegate_instructions()
    assert sentences_with(text, ("戦い", "自分で操作せず", "委任"))
    # 時間の止まる画面は、自分で操作する
    assert sentences_with(text, ("時間の止まる画面", "自分で操作"))


def test_delegate_instructions_say_to_fix_the_values_with_update_while_delegating():
    text = delegate_instructions()
    assert sentences_with(text, ("update", "直す"))
    # 文の中の JSON の波かっこは、エスケープせずにそのまま届く
    assert '{"action": "update", ...}' in text


def test_delegate_instructions_say_stop_is_only_for_jev_not_returning_control():
    assert sentences_with(delegate_instructions(), ("stop", "操作を返さない"))


def test_delegate_instructions_say_to_start_again_when_control_returns_mid_battle():
    assert sentences_with(delegate_instructions(), ("戦いの場面が続いている", "start"))


def test_delegate_instructions_say_to_review_each_delegation_with_the_chosen_counts():
    text = delegate_instructions()
    assert sentences_with(text, ("振り返", "選んだ回数"))
    # 振り返りに合わせて、次の委任の値を直す
    assert sentences_with(text, ("振り返", '"choices"', '"tasks"', '"cautions"', "直"))
    assert sentences_with(text, ("振り返", "notes/"))


def test_delegate_instructions_say_failures_are_not_a_reason_to_stop_delegating():
    assert sentences_with(delegate_instructions(), ("委任をやめる理由にしません",))


def test_delegate_instructions_say_actions_are_discarded_while_delegating():
    assert sentences_with(delegate_instructions(), ("委任中", "actions", "捨て"))


def test_delegate_instructions_say_to_write_stop_to_take_control_back():
    assert sentences_with(delegate_instructions(), ("取り返", '"stop"'))


def test_delegate_instructions_have_no_game_title():
    text = delegate_instructions()
    for title in ("ロックマン", "エグゼ"):
        assert title not in text
