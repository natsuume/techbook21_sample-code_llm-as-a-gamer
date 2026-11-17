"""gpt-6-luna の変わらない指示 (LUNA_DEVELOPER_INSTRUCTIONS) のうち、タスクのキュー (足すタスクと取り消すタスク) と
場面ラベル (scene_name) に関わる指示を確かめる。

タスクの指示は、特徴的な語句で確かめる。文の言い回しは決めず、指示に含まれるべき語句と、消す指示の語句を確かめる。
タスクの指示は、見出しの行に new_tasks を含む節 (例: 「# タスクの追加と取り消し (new_tasks・cancel_task_ids)」) に
書かれているものとする。
場面ラベルの指示は、見出しの行に scene_name を含む節 (「# 場面ラベル (scene_name)」) の箇条を、決めた文のとおりに
確かめる。
"""

from switch2_layers.middle.luna_instructions import LUNA_DEVELOPER_INSTRUCTIONS
from switch2_layers.middle.luna_request import USER_SECTION_TITLES
from switch2_layers.middle.route_section import ROUTE_SECTION_TITLE


def sections_by_heading() -> dict[str, str]:
    """変わらない指示を「# 」で始まる見出しの行ごとに分け、{見出しの行 (「# 」を除く): 本文} を返す。"""
    sections: dict[str, str] = {}
    heading: str | None = None
    lines: list[str] = []
    for line in LUNA_DEVELOPER_INSTRUCTIONS.split("\n"):
        if line.startswith("# "):
            if heading is not None:
                sections[heading] = "\n".join(lines)
            heading = line[2:]
            lines = []
        else:
            lines.append(line)
    if heading is not None:
        sections[heading] = "\n".join(lines)
    return sections


def section_with(word: str) -> str:
    """見出しの行に word を含む節の本文を返す。そのような節がちょうど 1 つでなければ AssertionError。"""
    found = [body for heading, body in sections_by_heading().items() if word in heading]
    assert len(found) == 1, f"sections with {word!r} in the heading: {len(found)}"
    return found[0]


def task_section() -> str:
    """タスクの指示の節 (見出しの行に new_tasks を含む節) の本文を返す。"""
    return section_with("new_tasks")


def input_lines() -> list[str]:
    """「# 入力」の節の、「- 」で始まる行 (user メッセージの節の説明) を返す。"""
    return [line for line in section_with("入力").split("\n") if line.startswith("- ")]


# --- 入力の節の説明 ---


def test_input_lists_the_task_sections_in_order():
    lines = input_lines()
    titles = [
        "大方針",
        "現在の場面のタスクのキュー",
        "前回以降のタスクの出来事",
        "呼ばれた理由",
        "現在の場面の操作説明",
    ]
    starts: list[int] = []
    for title in titles:
        found = [i for i, line in enumerate(lines) if line.startswith(f"- {title}: ")]
        assert len(found) == 1, title
        starts.append(found[0])
    # user メッセージの節の順どおりに並べる
    assert starts == sorted(starts)
    assert not any(line.startswith("- 現在の場面の短期タスクリスト") for line in lines)


def test_input_describes_every_user_section():
    lines = input_lines()
    for title in USER_SECTION_TITLES:
        assert any(line.startswith(f"- {title}: ") for line in lines), title


def test_previous_output_in_the_input_has_no_first_task():
    [line] = [
        line for line in input_lines() if line.startswith("- 前回の middle の出力: ")
    ]
    assert "最初のタスク" not in line


# --- タスクの書き方 ---


def test_tasks_are_states_that_can_be_confirmed_on_screen():
    assert "達成したときに画面で確かめられる状態" in task_section()


def test_tasks_do_not_carry_operations():
    section = task_section()
    # ボタンの名前・押す向き・マス数のような操作は書かせない
    for word in ["ボタンの名前", "向き", "マス数"]:
        assert word in section, word
    # 経路の手順から向きとマス数をタスクに書かせる指示を消す
    assert "向きとマス数を書" not in LUNA_DEVELOPER_INSTRUCTIONS
    assert "上に 3 マス進む" not in LUNA_DEVELOPER_INSTRUCTIONS


def test_tasks_trying_unknown_buttons_may_name_the_buttons():
    # 例外として、操作説明で unknown のボタンと、画面に案内が出ている複合ボタンを試すタスクには、ボタンの名前を書いてよい
    section = task_section()
    for word in ["unknown", "複合ボタン", "ボタンの名前"]:
        assert word in section, word


def test_tasks_keep_damage_and_game_over_out():
    assert "ダメージとゲームオーバーの条件" in task_section()


def test_tasks_are_not_carried_over_word_for_word():
    # 続いているタスクを一字一句そのまま引き継がせる指示を消す (場面名・場所の名前の「一字一句」は残る)
    assert "前回の文を一字一句" not in LUNA_DEVELOPER_INSTRUCTIONS
    assert "最初のタスクの文が変わると" not in LUNA_DEVELOPER_INSTRUCTIONS


# --- 行き先 ---


def test_tasks_about_places_have_destinations():
    section = task_section()
    # 行き先の場所はマップの場所の名前から、マスは出口・つながりの出たマス・目印から選ぶ。場所だけなら null
    for word in ["destination", "行き先", "出口", "つながりの出たマス", "目印", "null"]:
        assert word in section, word


def test_routes_are_used_to_choose_reachable_targets():
    section = task_section()
    assert ROUTE_SECTION_TITLE in section or "経路の節" in section
    # 「経路なし (未確認のマス (?) を通ってもたどり着けない)」の目標は足させない
    assert "通ってもたどり着けない" in section
    # 未確認のマスを通る経路の文の扱いも残す
    assert "未確認のマス (?) も通れるとすると" in section


def test_route_to_the_strategy_destination_is_written_as_states():
    section = task_section()
    assert "大方針の行き先" in section
    assert "場所をまたぐ経路" in section
    # 操作の形の例を消す
    assert "マットから外に出る」" not in LUNA_DEVELOPER_INSTRUCTIONS


# --- 足す数と取り消し ---


def test_tasks_are_added_only_as_needed():
    section = task_section()
    # 次に middle が呼ばれるまで surface が進められる分だけ足す。足りるときは空の配列
    assert "呼ばれるまで" in section
    assert "空の配列" in section
    # キューに既にある目標と同じものは足さない
    assert "既にある" in section


def test_cancel_is_decided_for_requested_and_old_tasks():
    section = task_section()
    for word in ["cancel_task_ids", "取り消しの要求", "古いタスク", "確かめ"]:
        assert word in section, word


def test_done_but_not_achieved_task_is_added_again():
    section = task_section()
    assert "完了" in section
    assert "足し直" in section


# --- 場面ラベル (scene_name) ---

# 場面を決める手順の箇条 (この順に続けて書く。番号の行は 2 字下げる)
SCENE_NAME_STEPS = [
    "- 毎回、最新のフレームの画面の種類を見て、次の順で場面を決めます",
    "  1. 最新のフレームが、前回の場面と同じ種類の画面なら、前回の場面名をそのまま書きます",
    (
        "  2. 違う種類の画面なら、場面ラベルの一覧から、その画面に当てはまる名前を探します。"
        "あれば、その名前を一字一句そのまま書きます"
    ),
    (
        "  3. 一覧に当てはまる名前が無ければ、新しい名前を作ります。新しい名前は 20 文字以内の短い日本語の"
        "名詞句にし、一覧のどの名前とも区別できるようにします"
    ),
    "- 一覧に当てはまる名前が無いことを理由に、前回の場面名を保ちません",
]


def scene_section_lines() -> list[str]:
    """場面ラベルの節 (見出しの行に scene_name を含む節) の、空でない行を返す。"""
    return [line for line in section_with("scene_name").split("\n") if line]


def test_scene_name_is_decided_by_the_steps():
    # 場面の例の後ろに手順を置き、ほかの箇条 (場面の例・フィールドの中の場所・暗転やロード・場面転換の印) は変えない
    assert scene_section_lines() == [
        (
            "- 場面は、ゲームの画面の種類や遊び方のまとまりです。例: タイトル画面、メインメニュー、フィールド、"
            "戦闘、会話、ショップ、ロード画面"
        ),
        *SCENE_NAME_STEPS,
        (
            "- フィールドの中の場所 (町の名前、家の 1 階、ダンジョンの階など) は場面ラベルにしません。同じ"
            "フィールドの中を移動しても、場面は変えません。場所は place_name に書きます"
        ),
        (
            "- 暗転やロードの途中のような一時的な画面では、前後のフレームを見て決めます。どうしても決められない"
            "ときだけ空文字列にします"
        ),
        (
            "- 場面転換の印があるときや、フレームの見た目が大きく変わったときは、特に注意して場面を確かめます"
        ),
    ]


def test_scene_section_has_no_old_rules():
    # 「一覧に当てはまる名前が無ければ前回の場面名を保つ」と読める箇条を置かない
    lines = scene_section_lines()
    for prefix in [
        "- 毎回、最新のフレームを見て場面を確かめます",
        "- 場面ラベルの一覧に当てはまる場面があれば",
        "- 当てはまるものが無いときだけ、新しい名前を作ります",
    ]:
        assert not any(line.startswith(prefix) for line in lines), prefix
    assert not any("前回の場面のままだと決めつけません" in line for line in lines)


# --- 詰まり ---


def test_stuck_uses_the_seconds_of_the_oldest_task():
    section = section_with("is_stuck")
    assert "最も古いタスク" in section
    # 目安の 30 秒は変えない
    assert "30 秒" in section
    assert "最初のタスクが同じ文のまま" not in section
