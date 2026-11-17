from switch2_layers.middle.system_screen_instructions import (
    CLEAN_UP_TEXT,
    SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
    goal_text,
)
from switch2_layers.middle.system_screen_types import SystemGoal
from switch2_layers.system_screen.system_screen_references import SYSTEM_SCREEN_NAMES


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
        "登録を確かめた後の home_menu か user_select での A が"
        "「この段階で送った決定」に"
    )
    # 手順 1 (HOME を押す) と手順 8 (done) は、登録を確かめた後に送った決定で見分ける
    assert (
        f"ゲームの画面 (none) が映っていて、{condition}無ければ、HOME を短押し" in text
    )
    assert (
        "ゲームの画面 (ソフトの起動の演出やタイトル画面を含む) が映っていて、"
        f"{condition}あれば、done を true にします"
    ) in text
    # 登録を確かめたかは、コントローラーの画面で B を送ったかで決める
    assert "コントローラーの画面 (controllers) で B を送った回" in text
    # 最初の呼び出しかどうかと、最後に送った操作による見分けは使わない
    assert "最初の呼び出し" not in text
    assert "最後に送った操作" not in text


def test_prepare_text_judges_registration_by_the_usb_controller_on_the_left():
    # 登録が済んでいるかは、コントローラーの画面のいちばん左に USB のコントローラーが映っているかで決める
    text = goal_text(SystemGoal.PREPARE)
    assert "いちばん左に USB のコントローラーが映っていれば、登録は済んでいます" in text
    assert "A を押さずに、B を短押しして HOME メニューに戻ります" in text


def test_developer_instructions_describe_the_sent_decisions_section():
    assert "この段階で送った決定 (A)" in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS
    assert "まだ送っていません" in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS


def test_developer_instructions_keep_the_rule_for_transitions():
    # 画面が切り替わる途中なら、空の列を返す
    assert "画面が切り替わる途中" in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS
    assert "operations を空にして done を false にし" in (
        SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS
    )


# --- 片付け (clean_up) の手順: HOME メニューに戻り、指定のソフトを終了する ---


def test_clean_up_text_closes_the_specified_software():
    assert goal_text(SystemGoal.CLEAN_UP) == CLEAN_UP_TEXT
    assert "HOME メニュー" in CLEAN_UP_TEXT
    assert "指定のソフト" in CLEAN_UP_TEXT
    assert "終了" in CLEAN_UP_TEXT


def test_clean_up_text_uses_x_and_the_close_confirmation():
    # HOME メニューで「プレイ中」の絵を選んで X を押すと、終了の確認 (software_close) が出る
    assert "プレイ中" in CLEAN_UP_TEXT
    assert "X" in CLEAN_UP_TEXT
    assert "software_close" in CLEAN_UP_TEXT
    # 終了の確認では、最初に選ばれている「終了する」で A を押す
    assert "終了する" in CLEAN_UP_TEXT
    assert "A" in CLEAN_UP_TEXT


def test_clean_up_text_is_done_on_the_home_menu():
    assert "done" in CLEAN_UP_TEXT
    assert "home_menu" in CLEAN_UP_TEXT


def test_instructions_describe_every_reference_screen():
    # 見本の画面の説明に、照合の見本と同じ名前の節を置く (software_close を含む)
    for name in SYSTEM_SCREEN_NAMES:
        assert f"## {name} (" in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS, name


def test_x_is_allowed_only_when_closing_the_software_in_clean_up():
    prohibited = SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS.split("# してはいけない操作")[1]
    prohibited = prohibited.split("\n# ")[0]
    # してはいけない操作の節で、X (ソフトをおわる) は、片付けで指定のソフトを終了するときだけ許す
    assert "X" in prohibited
    assert "片付け" in prohibited
    assert "指定のソフトを終了" in prohibited
