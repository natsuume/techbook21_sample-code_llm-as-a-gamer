from switch2_layers.middle.system_screen_instructions import (
    SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
    goal_text,
)
from switch2_layers.middle.system_screen_types import SystemGoal


def test_developer_instructions_is_a_non_empty_string():
    assert isinstance(SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS, str)
    assert SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS.strip()


def test_goal_text_is_non_empty_for_each_goal():
    for goal in SystemGoal:
        text = goal_text(goal)
        assert isinstance(text, str)
        assert text.strip()


def test_goal_texts_differ_by_goal():
    texts = [goal_text(goal) for goal in SystemGoal]
    assert len(set(texts)) == len(texts)


def test_goal_text_mentions_done():
    # どの目的の手順にも、done を真にする条件を書く
    for goal in SystemGoal:
        assert "done" in goal_text(goal)


def test_prepare_text_decides_by_the_sent_decisions():
    text = goal_text(SystemGoal.PREPARE)
    condition = (
        "「この段階で送った決定」に、controller_order での A より後の home_menu か "
        "user_select での A が"
    )
    # 手順 1 (HOME を押す) と手順 8 (done) は、送った決定の一覧で見分ける
    assert (
        f"ゲームの画面 (none) が映っていて、{condition}無ければ、HOME を短押し" in text
    )
    assert (
        "ゲームの画面 (ソフトの起動の演出やタイトル画面を含む) が映っていて、"
        f"{condition}あれば、done を true にします"
    ) in text
    # 最初の呼び出しかどうかと、最後に送った操作による見分けは使わない
    assert "最初の呼び出し" not in text
    assert "最後に送った操作" not in text


def test_developer_instructions_describe_the_sent_decisions_section():
    assert "この段階で送った決定 (A)" in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS
    assert "まだ送っていません" in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS


def test_developer_instructions_keep_the_rule_for_transitions():
    # 画面が切り替わる途中なら、空の列を返す
    assert "画面が切り替わる途中" in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS
    assert "operations を空にして done を false にし" in (
        SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS
    )
