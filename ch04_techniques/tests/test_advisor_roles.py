from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from switch2_agent.advisor_roles import ADVISOR_ROLES, AdvisorRole
from switch2_agent.game_notes import NOTE_KINDS

SKILLS_DIR = Path(__file__).resolve().parents[1] / ".claude" / "skills"
EXPECTED_QUESTION_TEXTS = {
    "controls": (
        "今、advisor (操作方法をまとめる上位のモデル) に、操作方法のファイルの更新を求めるべきか。"
        "操作方法がまだ書かれていないとき、「(一般)」の行のように、このゲームでまだ確かめていない操作を試して"
        "画面の反応が分かったとき、書かれている操作と画面の反応が合わないとき、"
        "新しい種類の操作 (メニュー、会話、乗り物など) が画面に出たときに真にする。"
    ),
    "strategy": (
        "今、advisor (目標と戦略を考える上位のモデル) に、目標と戦略の更新を求めるべきか。"
        "目標や戦略がまだ書かれていないとき、今の目標を達成したとき、今の目標が達成できないと分かったとき、"
        "新しい場面に入って今の戦略が画面の状況に合わないときに真にする。"
    ),
    "advice": (
        "今、advisor (問題の解決を助ける上位のモデル) に、助言を求めるべきか。"
        "同じ操作や同じ失敗を繰り返して先に進めないとき、何をすればよいか分からないときに真にする。"
    ),
    "tips": (
        "今、advisor (ゲームの情報をまとめる上位のモデル) に、攻略情報・Tips のファイルの更新を求めるべきか。"
        "敵、アイテム、ゲームの仕組み、注意点など、まだ書かれていないゲームの情報が画面に出たときに真にする。"
    ),
}
EXPECTED_TASKS = {
    "controls": (
        "ここまでの画面と操作の記録から、ゲームの操作方法 (ボタンの割り当て、会話の送り方、"
        "メニューの開き方など) を確かめ、操作方法のファイルを更新する。"
    ),
    "strategy": (
        "今の目標 (大目標・中目標・小目標) が達成されたか、達成できないと分かったかを判断して記録し、"
        "次の目標と、目標に向けた進め方 (戦略) と進行状況を更新する。"
    ),
    "advice": (
        "速い層が先に進めなくなっている原因を画面と操作の記録から考え、"
        "問題を解決するための具体的な手順を助言のファイルに書く。"
    ),
    "tips": (
        "画面に出たゲームの情報 (敵の特徴、アイテム、ゲームの仕組み、注意点など) を、"
        "攻略情報・Tips のファイルに記録する。"
    ),
}


def roles_by_name() -> dict[str, AdvisorRole]:
    return {role.name: role for role in ADVISOR_ROLES}


def test_roles_are_controls_strategy_advice_tips_in_order():
    assert [role.name for role in ADVISOR_ROLES] == [
        "controls",
        "strategy",
        "advice",
        "tips",
    ]


def test_role_question_names():
    assert [role.question_name for role in ADVISOR_ROLES] == [
        "advisor_controls",
        "advisor_strategy",
        "advisor_advice",
        "advisor_tips",
    ]


def test_role_note_kinds_skills_and_titles():
    assert [(role.note_kind, role.skill, role.title) for role in ADVISOR_ROLES] == [
        ("controls", "record-controls", "操作方法の更新"),
        ("strategy", "record-strategy", "目標・戦略の更新"),
        ("advice", "record-advice", "問題の解決の助言"),
        ("tips", "record-tips", "攻略情報・Tips の更新"),
    ]


def test_each_note_kind_has_exactly_one_role():
    assert sorted(role.note_kind for role in ADVISOR_ROLES) == sorted(NOTE_KINDS)


@pytest.mark.parametrize("name", list(EXPECTED_QUESTION_TEXTS))
def test_role_question_text(name):
    assert roles_by_name()[name].question_text == EXPECTED_QUESTION_TEXTS[name]


@pytest.mark.parametrize("name", list(EXPECTED_TASKS))
def test_role_task(name):
    assert roles_by_name()[name].task == EXPECTED_TASKS[name]


def test_role_is_frozen():
    with pytest.raises(FrozenInstanceError):
        ADVISOR_ROLES[0].name = "other"  # type: ignore[misc]


# --- Skill のファイル ---


@pytest.mark.parametrize("role", ADVISOR_ROLES, ids=lambda role: role.name)
def test_each_role_skill_has_skill_file_with_same_name(role):
    skill_file = SKILLS_DIR / role.skill / "SKILL.md"
    assert skill_file.is_file()
    lines = skill_file.read_text(encoding="utf-8").splitlines()
    assert f"name: {role.skill}" in lines


@pytest.mark.parametrize("role", ADVISOR_ROLES, ids=lambda role: role.name)
def test_each_role_skill_allows_writing_only_its_note_file(role):
    text = (SKILLS_DIR / role.skill / "SKILL.md").read_text(encoding="utf-8")
    assert (
        f"書き換えてよいのは {role.note_kind}.md だけ。ほかの 3 ファイルは読むだけにする"
        in text
    )


def test_skills_are_only_those_of_roles():
    # 進行状況は strategy.md に統合したため、record-progress の Skill は無い
    assert sorted(path.name for path in SKILLS_DIR.iterdir()) == sorted(
        role.skill for role in ADVISOR_ROLES
    )
