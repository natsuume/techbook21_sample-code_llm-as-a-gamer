"""Orchestrator 型の主ループに読ませる、Switch 2 の本体の操作のスキル (SKILL.md) の形。

スキルは src/switch2_layers/orchestrator/skills/<名前>/SKILL.md に置き、テストはそのファイルを直接読む。
frontmatter は、先頭の --- の行で囲まれた key: value の行として読む (依存を足さないため、YAML のライブラリーは
使わない)。本文の操作の例は ```json のコードブロックとして読む。
"""

import json
from pathlib import Path

import pytest
from switch2_remote.controller_state import BUTTON_ORDER

import switch2_layers
from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.orchestrator.decision_parser import parse_decision
from switch2_layers.orchestrator.decision_types import DecisionError

# スキルを置くディレクトリー (パッケージのディレクトリーからのパスで指す)
SKILLS_DIR = Path(switch2_layers.__file__).parent / "orchestrator" / "skills"
SKILL_FILE_NAME = "SKILL.md"
# 本体の操作のスキルの名前 (ディレクトリーの名前と、frontmatter の name を兼ねる)
SKILL_NAMES = (
    "switch-return-to-game",
    "switch-launch-software",
    "switch-select-user",
    "switch-controller-order",
    "switch-system-dialog",
)
# 本体の操作のスキルの名前の頭
SKILL_NAME_PREFIX = "switch-"
# 本文の節の見出し (## の見出し。5 つのスキルで同じ並びにする)
SECTION_HEADINGS = ("画面の見た目", "操作", "してはいけない操作")
# 本文の操作の例に書く操作の種類 (本体の操作は、押す操作と待つ操作だけで書く)
EXAMPLE_ACTION_TYPES = frozenset({"press", "wait"})
FRONTMATTER_DELIMITER = "---"
CODE_FENCE = "```"


def read_skill(name: str) -> str:
    """スキル name の SKILL.md の文を返す。"""
    return (SKILLS_DIR / name / SKILL_FILE_NAME).read_text(encoding="utf-8")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """SKILL.md の文を、frontmatter の欄 (key → value) と本文に分ける。

    frontmatter は、1 行目の --- の行から次の --- の行までの間の、key: value の行。key と value は、行の最初の
    : の前と後ろを、前後の空白を除いて読む。1 行目が --- でない、閉じる --- の行が無い、: の無い行がある、同じ
    key が 2 回出るときは AssertionError にする。
    """
    lines = text.splitlines()
    assert lines and lines[0] == FRONTMATTER_DELIMITER, "1 行目が --- の行ではない"
    assert FRONTMATTER_DELIMITER in lines[1:], "frontmatter を閉じる --- の行が無い"
    end = lines.index(FRONTMATTER_DELIMITER, 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        assert separator, f"frontmatter に key: value の形でない行がある: {line!r}"
        key = key.strip()
        assert key not in fields, f"frontmatter に同じ key が 2 回ある: {key}"
        fields[key] = value.strip()
    return fields, "\n".join(lines[end + 1 :])


def json_blocks(body: str) -> list[str]:
    """本文の ```json のコードブロックの中身を、出てくる順に返す。

    コードブロックは、``` で始まる行から、``` だけの行までとする。行の前後の空白は除いて比べるため、箇条書きの
    中で字下げしたコードブロックも読む。``` の後ろの言語名が json のコードブロックだけを返す。閉じていない
    コードブロックがあれば AssertionError にする。
    """
    blocks: list[str] = []
    # 今いるコードブロックの言語名 (コードブロックの外なら None)
    language: str | None = None
    content: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if language is None:
            if stripped.startswith(CODE_FENCE):
                language = stripped.removeprefix(CODE_FENCE).strip()
                content = []
        elif stripped == CODE_FENCE:
            if language == "json":
                blocks.append("\n".join(content))
            language = None
        else:
            content.append(line)
    assert language is None, "閉じていないコードブロックがある"
    return blocks


def section_headings(body: str) -> list[str]:
    """本文の ## の見出し (### 以下は含めない) の文を、出てくる順に返す。"""
    return [
        line.removeprefix("## ").strip()
        for line in body.splitlines()
        if line.startswith("## ")
    ]


def example_actions(name: str) -> list[tuple[str, dict[str, object]]]:
    """スキル name の本文の JSON の例を読み、actions の各要素を (場所の説明, 要素) で、出てくる順に返す。

    各例が JSON のオブジェクトとして読め、最上位のキーが actions だけで、actions が JSON のオブジェクトの
    配列であることを確かめる。合わなければ、スキルの名前と何番目の例かを書いて AssertionError にする。
    actions が空の例 (何もしない JSON) も受け付ける。
    """
    _, body = split_frontmatter(read_skill(name))
    actions: list[tuple[str, dict[str, object]]] = []
    for block_index, block in enumerate(json_blocks(body), start=1):
        where = f"{name} の {block_index} 番目の JSON の例"
        try:
            example = json.loads(block)
        except json.JSONDecodeError as error:
            pytest.fail(f"{where} を JSON として読めない: {error}")
        assert isinstance(example, dict), f"{where} が JSON のオブジェクトではない"
        assert set(example) == {"actions"}, (
            f"{where} の最上位のキーが actions だけではない: {sorted(example)}"
        )
        assert isinstance(example["actions"], list), (
            f"{where} の actions が配列ではない"
        )
        for action_index, action in enumerate(example["actions"], start=1):
            assert isinstance(action, dict), (
                f"{where} の {action_index} 番目の操作が JSON のオブジェクトではない"
            )
            actions.append((f"{where} の {action_index} 番目の操作", action))
    return actions


def assert_seconds(where: str, action: dict[str, object]) -> None:
    """action に seconds があり、0 より大きく MAX_HOLD_SECONDS 以下の数であることを確かめる。"""
    assert "seconds" in action, f"{where} に seconds が無い"
    seconds = action["seconds"]
    # JSON の true・false は Python の bool (int の派生型) になるため、数として扱わない
    assert isinstance(seconds, int | float) and not isinstance(seconds, bool), (
        f"{where} の seconds が数ではない: {seconds!r}"
    )
    assert 0 < seconds <= MAX_HOLD_SECONDS, (
        f"{where} の seconds が 0 より大きく {MAX_HOLD_SECONDS} 以下ではない: {seconds}"
    )


# --- スキルのディレクトリー ---


def test_skill_directories_are_the_five_switch_skills():
    directories = {path.name for path in SKILLS_DIR.iterdir() if path.is_dir()}
    assert directories == set(SKILL_NAMES)
    for name in SKILL_NAMES:
        assert (SKILLS_DIR / name / SKILL_FILE_NAME).is_file(), name


# --- frontmatter ---


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_frontmatter_has_name_and_description(name):
    fields, _ = split_frontmatter(read_skill(name))
    # name はディレクトリーの名前と同じで、本体の操作のスキルの頭 switch- で始まる
    assert fields.get("name") == name
    assert fields["name"].startswith(SKILL_NAME_PREFIX)
    # description (どの画面で使うか) が空でない
    assert fields.get("description"), f"{name} の description が無いか空"


# --- 本文の節 ---


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_body_has_the_same_sections(name):
    _, body = split_frontmatter(read_skill(name))
    assert section_headings(body) == list(SECTION_HEADINGS)


# --- 本文の JSON の例 ---


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_body_has_json_examples(name):
    _, body = split_frontmatter(read_skill(name))
    assert json_blocks(body), f"{name} の本文に ```json のコードブロックが無い"


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_json_examples_have_only_press_and_wait_actions(name):
    # 例を読む関数が、例が actions だけを持つ JSON のオブジェクトであることを確かめる
    for where, action in example_actions(name):
        assert action.get("type") in EXAMPLE_ACTION_TYPES, (
            f"{where} の type が press・wait のどちらでもない: {action.get('type')!r}"
        )


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_json_example_presses_use_known_buttons_and_seconds(name):
    for where, action in example_actions(name):
        if action.get("type") != "press":
            continue
        # press には、押すボタン (buttons) か倒すスティック (sticks) の少なくとも一方がある
        assert "buttons" in action or "sticks" in action, (
            f"{where} の press に buttons も sticks も無い"
        )
        buttons = action.get("buttons", [])
        assert isinstance(buttons, list), f"{where} の buttons が配列ではない"
        unknown = [button for button in buttons if button not in BUTTON_ORDER]
        assert not unknown, f"{where} に BUTTON_ORDER に無いボタン名がある: {unknown}"
        # 押す秒数も書く (seconds を省いた短押しで書かない)
        assert_seconds(where, action)


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_json_example_waits_have_seconds(name):
    for where, action in example_actions(name):
        if action.get("type") == "wait":
            assert_seconds(where, action)


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_json_examples_are_read_by_parse_decision(name):
    # 主ループの応答の本文に、例のブロックを 1 つだけ書いたものとして読む
    _, body = split_frontmatter(read_skill(name))
    for block_index, block in enumerate(json_blocks(body), start=1):
        response = f"{CODE_FENCE}json\n{block}\n{CODE_FENCE}\n"
        result = parse_decision(response)
        assert not isinstance(result, DecisionError), (
            f"{name} の {block_index} 番目の JSON の例が誤りになる: {result.messages}"
        )
