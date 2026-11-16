import pytest

from switch2_layers.middle.middle_types import MiddleState
from switch2_layers.middle.task_progress import (
    first_task_of,
    first_task_since,
    is_same_task_text,
)


def previous_state(first_task: str, first_task_since_t: float | None) -> MiddleState:
    """最初のタスクの文と、その文になった時刻だけを変えた前回の middle/state.json の値を返す。"""
    return MiddleState(
        generated_t=7.5,
        based_on_t=7.0,
        scene_id="scene-001",
        scene_name="町",
        is_stuck=False,
        stuck_description="",
        surface_wait_s=0.5,
        timeline="",
        surface_rows_offset=0,
        operations_rows_offset=0,
        first_task=first_task,
        first_task_since_t=first_task_since_t,
    )


# --- 最初のタスクの文 ---


def test_first_task_is_the_first_bullet():
    assert first_task_of("- 宿屋に入る\n- 主人に話しかける\n") == "宿屋に入る"


def test_lines_before_the_first_bullet_are_skipped():
    # 空行・見出し・「- 」で始まらない行は飛ばす
    tasks = "\n# タスク\n\n説明の行\n* 別の記号の行\n-記号の後に空白が無い行\n- 右の扉に入る\n- 次"
    assert first_task_of(tasks) == "右の扉に入る"


def test_indented_bullet_counts_and_its_text_is_stripped():
    assert first_task_of("  - 右の扉に入る  \n- 次") == "右の扉に入る"
    # 「- 」より後ろの前後の空白も除く
    assert first_task_of("-   宿屋に入る\t\n") == "宿屋に入る"


def test_first_bullet_wins_over_nested_bullets():
    assert first_task_of("- 町へ出る\n  - 北の出口を探す\n") == "町へ出る"


@pytest.mark.parametrize("tasks", ["", "   \n", "# タスク\n説明だけの行\n"])
def test_no_bullet_gives_empty_text(tasks):
    assert first_task_of(tasks) == ""


# --- 文が同じか ---


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("A で話しかける", "Aで話しかける"),
        ("右の 扉に\t入る\n", "右の扉に入る"),
        # 全角の空白も除く
        ("右の　扉に入る", "右の扉に入る"),
        ("宿屋に入る", "宿屋に入る"),
    ],
)
def test_texts_differing_only_in_whitespace_are_the_same(a, b):
    assert is_same_task_text(a, b) is True
    assert is_same_task_text(b, a) is True


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("右の扉に入る", "左の扉に入る"),
        ("宿屋に入る", "宿屋に入る。"),
        ("宿屋に入る", ""),
    ],
)
def test_texts_with_different_characters_are_not_the_same(a, b):
    assert is_same_task_text(a, b) is False
    assert is_same_task_text(b, a) is False


def test_two_empty_texts_are_the_same():
    assert is_same_task_text("", "") is True
    assert is_same_task_text("", " 　\t\n") is True


# --- 最初のタスクが今の文になった時刻 ---


def test_without_previous_state_the_time_is_written_t():
    assert first_task_since(None, "宿屋に入る", 12.8) == 12.8


def test_same_first_task_keeps_the_previous_time():
    assert (
        first_task_since(previous_state("宿屋に入る", 3.0), "宿屋に入る", 12.8) == 3.0
    )


def test_first_task_differing_only_in_whitespace_keeps_the_previous_time():
    assert (
        first_task_since(previous_state("A で話しかける", 3.0), "Aで話しかける", 12.8)
        == 3.0
    )


def test_different_first_task_starts_at_written_t():
    assert (
        first_task_since(previous_state("宿屋に入る", 3.0), "主人に話しかける", 12.8)
        == 12.8
    )


def test_previous_state_without_time_starts_at_written_t():
    assert (
        first_task_since(previous_state("宿屋に入る", None), "宿屋に入る", 12.8) == 12.8
    )


def test_empty_first_task_that_stays_empty_keeps_the_previous_time():
    # どちらも空 (タスクの行が無い) なら同じ文とみなす
    assert first_task_since(previous_state("", 3.0), "", 12.8) == 3.0
