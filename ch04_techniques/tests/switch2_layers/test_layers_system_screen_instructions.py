import pytest

from switch2_layers.middle.system_screen_instructions import (
    CLEAN_UP_TEXT,
    SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
    goal_text,
)
from switch2_layers.middle.system_screen_types import SystemGoal
from switch2_layers.system_screen.system_screen_references import SYSTEM_SCREEN_NAMES


def top_section(title: str) -> str:
    """指示のうち「# <title>」の節の本文 (次の「# 」の見出しの前まで) を返す。"""
    return SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS.split(f"\n# {title}\n")[1].split(
        "\n# "
    )[0]


def screen_section(heading: str) -> str:
    """「# 本体の画面」の節のうち、見出しが「## <heading>」で始まる節の本文 (次の「## 」の見出しの前まで) を返す。"""
    return top_section("本体の画面").split(f"\n## {heading}")[1].split("\n## ")[0]


def step_line(text: str, number: int) -> str:
    """手順の文 text のうち「<number>. 」で始まる行を返す。"""
    [line] = [line for line in text.splitlines() if line.startswith(f"{number}. ")]
    return line


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


def test_prepare_text_decides_by_the_sent_buttons():
    text = goal_text(SystemGoal.PREPARE)
    condition = (
        "登録を確かめた後の home_menu か user_select での A が"
        "「この段階で送った A・B・X」に"
    )
    # 手順 1 (HOME を押す) と手順 8 (done) は、登録を確かめた後に送った決定で見分ける
    first = step_line(text, 1)
    assert first.startswith("1. ゲームの画面 (none) が映っていて、")
    assert f"{condition}無ければ、HOME を短押し" in first
    last = step_line(text, 8)
    assert last.startswith(
        "8. ゲームの画面 (ソフトの起動の演出やタイトル画面を含む) が映っていて、"
    )
    assert f"{condition}あれば、done を true にします" in last
    # 登録を確かめたかは、送った A・B・X の節に controllers での B があるかで決める
    assert "「この段階で送った A・B・X」に「controllers で B」の行がある" in text
    # HOME メニューでの手順 2 (コントローラーを開く) と手順 6 (ソフトを起動する) にも、同じ条件を書く
    assert "まだ登録を確かめていなければ (「この段階で送った A・B・X」に" in text
    assert "登録を確かめた後なら (「この段階で送った A・B・X」に" in text
    # 最初の呼び出しかどうかと、最後に送った操作による見分けは使わない
    assert "最初の呼び出し" not in text
    assert "最後に送った操作" not in text


def test_prepare_text_judges_registration_by_the_usb_controller_on_the_left():
    # 登録が済んでいるかは、コントローラーの画面のいちばん左に USB のコントローラーが映っているかで決める
    text = goal_text(SystemGoal.PREPARE)
    assert "いちばん左に USB のコントローラーが映っていれば、登録は済んでいます" in text
    assert "A を押さずに、B を短押しして HOME メニューに戻ります" in text


def test_developer_instructions_describe_the_sent_buttons_section():
    assert "この段階で送った A・B・X" in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS
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


def test_clean_up_text_decides_the_close_confirmation_by_the_sent_x():
    # 終了の確認で A を押すか B で閉じるかは、送った A・B・X の節に home_menu での X があるかで決める
    assert "「この段階で送った A・B・X」に「home_menu で X」の行があれば" in (
        CLEAN_UP_TEXT
    )
    assert "「この段階で送った A・B・X」に「home_menu で X」の行が無ければ" in (
        CLEAN_UP_TEXT
    )
    assert "この段階のこれまでの呼び出し" not in CLEAN_UP_TEXT


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


# --- HOME メニューの上の段と「もっとみる」、ソフトの一覧 ---


def test_home_menu_section_describes_the_top_row_and_more_software():
    home = screen_section("home_menu (")
    # 上の段には最近のソフトの絵が最大 12 枚並び、右端に「もっとみる」がある
    for phrase in ["最大 12 枚", "「もっとみる」", "四角が 4 つ"]:
        assert phrase in home, phrase


def test_home_menu_section_tells_where_the_cursor_is():
    home = screen_section("home_menu (")
    # ONLINE のアイコンは、選ばれていなくても赤い丸である
    assert "選ばれていなくても赤い丸" in home
    # 絵の上にソフトの名前が出ている間は、カーソルは上の段にある
    assert "カーソルは上の段にあり" in home


def test_home_menu_section_describes_the_parental_controls_button():
    home = screen_section("home_menu (")
    for phrase in ["みまもり設定", "みまもり設定を一時OFFにする", "DOWN で上の段に戻"]:
        assert phrase in home, phrase


def test_instructions_describe_the_software_list_screen():
    software_list = screen_section("ソフトの一覧 (もっとみる)")
    for phrase in [
        # 照合の結果は none だが、ゲームの画面ではない
        "照合の結果は none",
        "ゲームの画面では",
        # 目印
        "「ソフト」「グループ」のタブ",
        "じょうご",
        "並べ替え",
        "「+ オプション」「B もどる」「A 決定」",
        # 並び方と選んでいる絵の名前
        "6 列",
        "本体の設定で変わ",
        "吹き出し",
        "流れて表示",
        # 使う操作
        "DOWN で 1 行ずつ",
        "L・R・ZL・ZR・PLUS・MINUS・X・Y",
        "RIGHT で戻",
        "HOME メニューに戻",
    ]:
        assert phrase in software_list, phrase


def test_prepare_text_does_not_treat_the_software_list_as_the_game_screen():
    text = goal_text(SystemGoal.PREPARE)
    marker = (
        "ソフトの一覧の目印 (「ソフト」「グループ」のタブ、右下の「B もどる」) が映って"
    )
    # 手順 1 (HOME を押す) と手順 8 (done) の条件文そのものに、一覧の目印が映っていないことを入れる
    for number, action in [(1, "HOME を短押し"), (8, "done を true にします")]:
        line = step_line(text, number)
        assert marker in line, number
        assert line.index(marker) < line.index(action), number


def test_prepare_text_opens_more_software_when_the_software_is_not_on_the_top_row():
    line = step_line(goal_text(SystemGoal.PREPARE), 6)
    for phrase in [
        "「もっとみる」まで確かめても無ければ",
        "「もっとみる」を選",
        "一覧で指定のソフトを探",
    ]:
        assert phrase in line, phrase


@pytest.mark.parametrize("goal", [SystemGoal.RECOVER, SystemGoal.CLEAN_UP])
def test_recover_and_clean_up_do_not_open_more_software(goal):
    text = goal_text(goal)
    assert "「もっとみる」を開きません" in text
    # ソフトの一覧が映っていたら、B で HOME メニューに戻る
    assert any(
        "ソフトの一覧" in line and "B を短押し" in line and "HOME メニューに戻" in line
        for line in text.splitlines()
    )


def test_recover_text_returns_empty_operations_without_the_software_on_the_top_row():
    assert any(
        "上の段に無ければ" in line and "操作の列を空に" in line
        for line in goal_text(SystemGoal.RECOVER).splitlines()
    )


# --- 名前の確かめ方 ---


def test_name_check_allows_differences_and_uses_the_logo():
    assert "名前の全体が指定のソフトと同じ" not in SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS
    prohibited = top_section("してはいけない操作")
    # 表示の名前は、指定のソフトの名前と完全に同じでなくてよい
    for phrase in [
        "完全に同じでなくて",
        "空白",
        "記号",
        "全角半角",
        "途中で切れ",
        "流れて表示",
    ]:
        assert phrase in prohibited, phrase
    # 表示の名前と絵の中のロゴの文字を合わせて確かめ、見分けられないときは押さない
    for phrase in ["ロゴ", "見分けられない"]:
        assert phrase in prohibited, phrase


@pytest.mark.parametrize("goal", [SystemGoal.RECOVER, SystemGoal.CLEAN_UP])
def test_recover_and_clean_up_check_the_name_with_the_logo(goal):
    text = goal_text(goal)
    assert "絵の上の名前が指定のソフトであること" not in text
    assert "ロゴ" in text


# --- カーソルの移動・してはいけない操作・入力 ---


def test_cursor_section_resends_moves_that_were_not_reflected():
    cursor = top_section("カーソルの移動")
    for phrase in [
        "反映されない",
        "最初の入力",
        "送り直",
        # 下の段で数えて動かすときは、DOWN の後の画像で下の段が選ばれたことを確かめてから RIGHT を数える
        "DOWN の後の画像",
        "輪が付き",
        "RIGHT を数え",
    ]:
        assert phrase in cursor, phrase


def test_prohibited_section_covers_parental_controls_more_software_and_the_list():
    prohibited = top_section("してはいけない操作")
    assert "みまもり設定のボタンで A を押しません" in prohibited
    # 「もっとみる」を開くのは、準備で指定のソフトが上の段に無いときだけ
    assert any(
        "「もっとみる」を開くのは" in line and "準備" in line and "上の段に無い" in line
        for line in prohibited.splitlines()
    )
    assert "L・R・ZL・ZR・PLUS・MINUS・X・Y を押しません" in prohibited


def test_input_section_describes_the_deep_advice():
    lines = top_section("入力").splitlines()
    [line] = [line for line in lines if line.startswith("- deep の助言:")]
    # deep 層が本体の画面の記録とフレームから書いた助言。参考にするが、規則が優先する
    for phrase in ["deep 層", "参考", "優先"]:
        assert phrase in line, phrase
    # 節の順は user メッセージと同じ (送った A・B・X の後、今の画像の前)
    titles = [line.split(":")[0] for line in lines if line.startswith("- ")]
    index = titles.index("- deep の助言")
    assert titles[index - 1] == "- この段階で送った A・B・X"
    assert titles[index + 1] == "- 今の画像"
