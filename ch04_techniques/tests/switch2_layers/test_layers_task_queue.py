from typing import get_args

import pytest

from switch2_layers.tasks.task_queue import (
    MAX_ADDED_PER_CALL,
    MAX_OPEN_TASKS_PER_SCENE,
    AddResult,
    DroppedText,
    DropReason,
    SceneTaskQueues,
    TaskView,
    is_same_task_text,
    normalize_task_text,
    task_id,
    task_id_number,
)

SCENE = "scene-001"
OTHER = "scene-002"

# 作り直しに使う、保存されていたタスク (SCENE に 2 つ、OTHER に 1 つ)
RESTORED = (
    TaskView(
        id="task-003",
        text="宿屋に入る",
        scene_id=SCENE,
        added_t=10.0,
        active_s=12.5,
        cancel_requested_t=None,
    ),
    TaskView(
        id="task-005",
        text="主人に話しかける",
        scene_id=SCENE,
        added_t=11.0,
        active_s=4.0,
        cancel_requested_t=30.0,
    ),
    TaskView(
        id="task-004",
        text="町を出る",
        scene_id=OTHER,
        added_t=12.0,
        active_s=2.0,
        cancel_requested_t=None,
    ),
)


def ids_of(views: tuple[TaskView, ...]) -> list[str]:
    return [view.id for view in views]


def texts_of(views: tuple[TaskView, ...]) -> list[str]:
    return [view.text for view in views]


def full_queues() -> SceneTaskQueues:
    """場面 SCENE に未完了のタスクを 5 個 (A〜E。task-001〜task-005) 足したキューを返す。現在の場面は場面なし。"""
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A", "B", "C"], 1.0)
    queues.add(SCENE, ["D", "E"], 2.0)
    return queues


# --- 上限と理由 ---


def test_limits_are_three_per_call_and_five_per_scene():
    assert MAX_ADDED_PER_CALL == 3
    assert MAX_OPEN_TASKS_PER_SCENE == 5


def test_drop_reasons():
    assert get_args(DropReason) == ("empty", "duplicate", "limit")


# --- タスクの ID ---


@pytest.mark.parametrize(
    ("number", "expected"),
    [(1, "task-001"), (12, "task-012"), (999, "task-999"), (1000, "task-1000")],
)
def test_task_id_has_at_least_three_digits(number, expected):
    assert task_id(number) == expected


@pytest.mark.parametrize("number", [0, -1])
def test_task_id_rejects_numbers_below_one(number):
    with pytest.raises(ValueError):
        task_id(number)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("task-001", 1), ("task-012", 12), ("task-1000", 1000)],
)
def test_task_id_number_reads_the_number(value, expected):
    assert task_id_number(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "task-01",
        "task-abc",
        "Task-001",
        "task-001 ",
        "scene-001",
        "",
        None,
        1,
        ["task-001"],
    ],
)
def test_task_id_number_of_other_values_is_none(value):
    assert task_id_number(value) is None


# --- 文の整え方と比べ方 ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  宿屋に入る\t\n", "宿屋に入る"),
        ("　宿屋に入る　", "宿屋に入る"),
        ("宿屋に\n入る", "宿屋に 入る"),
        ("宿屋に\r\n入る", "宿屋に 入る"),
        ("宿屋に\r入る", "宿屋に 入る"),
        # 前後の空白を除いてから、内側の改行を置き換える
        ("\n宿屋に\n入る\n", "宿屋に 入る"),
        ("", ""),
        (" \n　", ""),
    ],
)
def test_normalize_task_text(text, expected):
    assert normalize_task_text(text) == expected


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("A で話しかける", "Aで話しかける"),
        ("右の 扉に\t入る", "右の扉に入る"),
        ("右の　扉に入る", "右の扉に入る"),
        ("宿屋に入る", "宿屋に入る"),
    ],
)
def test_texts_differing_only_in_whitespace_are_the_same(a, b):
    assert is_same_task_text(a, b) is True
    assert is_same_task_text(b, a) is True


@pytest.mark.parametrize(
    ("a", "b"),
    [("右の扉に入る", "左の扉に入る"), ("宿屋に入る", "宿屋に入る。")],
)
def test_texts_with_different_characters_are_not_the_same(a, b):
    assert is_same_task_text(a, b) is False
    assert is_same_task_text(b, a) is False


# --- 追加 ---


def test_add_appends_tasks_to_the_end_in_input_order():
    queues = SceneTaskQueues()
    result = queues.add(SCENE, ["宿屋に入る", "主人に話しかける"], 1.0)
    assert result == AddResult(
        outcomes=(
            TaskView(
                id="task-001",
                text="宿屋に入る",
                scene_id=SCENE,
                added_t=1.0,
                active_s=0.0,
                cancel_requested_t=None,
            ),
            TaskView(
                id="task-002",
                text="主人に話しかける",
                scene_id=SCENE,
                added_t=1.0,
                active_s=0.0,
                cancel_requested_t=None,
            ),
        )
    )
    queues.add(SCENE, ["部屋に泊まる"], 2.0)
    views = queues.tasks(SCENE, 2.0)
    assert texts_of(views) == ["宿屋に入る", "主人に話しかける", "部屋に泊まる"]
    assert ids_of(views) == ["task-001", "task-002", "task-003"]


def test_add_keeps_normalized_text():
    queues = SceneTaskQueues()
    result = queues.add(SCENE, ["  宿屋に\r\n入る "], 1.0)
    assert texts_of(result.added) == ["宿屋に 入る"]
    assert texts_of(queues.tasks(SCENE, 1.0)) == ["宿屋に 入る"]


def test_ids_are_serial_across_scenes():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A"], 1.0)
    queues.add(OTHER, ["B", "C"], 2.0)
    queues.add(SCENE, ["D"], 3.0)
    assert ids_of(queues.tasks(SCENE, 3.0)) == ["task-001", "task-004"]
    assert ids_of(queues.tasks(OTHER, 3.0)) == ["task-002", "task-003"]


def test_dropped_texts_do_not_use_ids():
    queues = SceneTaskQueues()
    result = queues.add(SCENE, ["A", " ", "A", "B"], 1.0)
    assert ids_of(result.added) == ["task-001", "task-002"]
    assert texts_of(result.added) == ["A", "B"]
    assert result.dropped == (
        DroppedText(text="", reason="empty"),
        DroppedText(text="A", reason="duplicate"),
    )
    assert ids_of(queues.add(OTHER, ["C"], 2.0).added) == ["task-003"]


def test_add_result_keeps_the_input_order():
    queues = SceneTaskQueues()
    result = queues.add(SCENE, ["A", " ", "B", "A"], 1.0)
    assert [
        item.id if isinstance(item, TaskView) else item.reason
        for item in result.outcomes
    ] == ["task-001", "empty", "task-002", "duplicate"]


@pytest.mark.parametrize("text", ["", "   ", "\n", "　\t\r\n"])
def test_empty_text_is_dropped(text):
    queues = SceneTaskQueues()
    result = queues.add(SCENE, [text], 1.0)
    assert result == AddResult(outcomes=(DroppedText(text="", reason="empty"),))
    assert queues.tasks(SCENE, 1.0) == ()


def test_text_differing_only_in_whitespace_is_duplicate():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A で話しかける"], 1.0)
    result = queues.add(SCENE, ["Aで 話しかける"], 2.0)
    assert result == AddResult(
        outcomes=(DroppedText(text="Aで 話しかける", reason="duplicate"),)
    )
    assert texts_of(queues.tasks(SCENE, 2.0)) == ["A で話しかける"]


def test_text_same_as_one_added_earlier_in_the_call_is_duplicate():
    queues = SceneTaskQueues()
    result = queues.add(SCENE, ["宿屋に入る", "宿屋に 入る"], 1.0)
    assert texts_of(result.added) == ["宿屋に入る"]
    assert result.dropped == (DroppedText(text="宿屋に 入る", reason="duplicate"),)


def test_same_text_in_another_scene_is_not_duplicate():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["宿屋に入る"], 1.0)
    result = queues.add(OTHER, ["宿屋に入る"], 2.0)
    assert ids_of(result.added) == ["task-002"]
    assert result.dropped == ()


def test_text_of_a_finished_task_can_be_added_again():
    # 重複は未完了のタスクとだけ比べる
    queues = SceneTaskQueues()
    queues.add(SCENE, ["宿屋に入る", "主人に話しかける"], 1.0)
    queues.complete(SCENE, "task-001", 2.0)
    queues.cancel(["task-002"], 3.0)
    result = queues.add(SCENE, ["宿屋に入る", "主人に話しかける"], 4.0)
    assert ids_of(result.added) == ["task-003", "task-004"]
    assert result.dropped == ()


def test_fourth_text_in_one_call_is_limit():
    queues = SceneTaskQueues()
    result = queues.add(SCENE, ["A", "B", "C", "D"], 1.0)
    assert texts_of(result.added) == ["A", "B", "C"]
    assert result.dropped == (DroppedText(text="D", reason="limit"),)


def test_per_call_limit_counts_only_added_texts():
    # 足せなかった文は、1 回で足した数に数えない
    queues = SceneTaskQueues()
    result = queues.add(SCENE, ["", "A", "A", "B", "C", "D"], 1.0)
    assert texts_of(result.added) == ["A", "B", "C"]
    assert result.dropped == (
        DroppedText(text="", reason="empty"),
        DroppedText(text="A", reason="duplicate"),
        DroppedText(text="D", reason="limit"),
    )


def test_texts_after_five_open_tasks_are_limit():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A", "B", "C"], 1.0)
    result = queues.add(SCENE, ["D", "E", "F"], 2.0)
    assert texts_of(result.added) == ["D", "E"]
    assert result.dropped == (DroppedText(text="F", reason="limit"),)
    assert queues.add(SCENE, ["G"], 3.0) == AddResult(
        outcomes=(DroppedText(text="G", reason="limit"),)
    )
    assert texts_of(queues.tasks(SCENE, 3.0)) == ["A", "B", "C", "D", "E"]


def test_open_task_limit_is_per_scene():
    queues = full_queues()
    result = queues.add(OTHER, ["A"], 3.0)
    assert ids_of(result.added) == ["task-006"]
    assert result.dropped == ()


def test_completing_or_canceling_makes_room_again():
    queues = full_queues()
    queues.complete(SCENE, "task-001", 3.0)
    assert texts_of(queues.add(SCENE, ["F"], 4.0).added) == ["F"]
    queues.cancel(["task-003"], 5.0)
    result = queues.add(SCENE, ["G", "H"], 6.0)
    assert texts_of(result.added) == ["G"]
    assert result.dropped == (DroppedText(text="H", reason="limit"),)
    assert texts_of(queues.tasks(SCENE, 6.0)) == ["B", "D", "E", "F", "G"]


def test_overlapping_reasons_use_the_first_one():
    # 満杯の場面に、空の文・重複の文・新しい文 (理由は empty → duplicate → limit の順で決める)
    queues = full_queues()
    result = queues.add(SCENE, ["  ", "A", "F"], 3.0)
    assert result.added == ()
    assert result.dropped == (
        DroppedText(text="", reason="empty"),
        DroppedText(text="A", reason="duplicate"),
        DroppedText(text="F", reason="limit"),
    )
    # 1 回で 3 個足した後の、空の文と重複の文
    queues = SceneTaskQueues()
    result = queues.add(SCENE, ["A", "B", "C", "", "A"], 1.0)
    assert result.dropped == (
        DroppedText(text="", reason="empty"),
        DroppedText(text="A", reason="duplicate"),
    )


# --- 完了 ---


def test_complete_removes_the_head():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A", "B"], 1.0)
    assert queues.complete(SCENE, "task-001", 4.0) == TaskView(
        id="task-001",
        text="A",
        scene_id=SCENE,
        added_t=1.0,
        active_s=3.0,
        cancel_requested_t=None,
    )
    assert ids_of(queues.tasks(SCENE, 4.0)) == ["task-002"]
    # 完了にしたタスクは、もう完了にできない
    assert queues.complete(SCENE, "task-001", 5.0) is None
    # 外した後は、次のタスクが先頭
    done = queues.complete(SCENE, "task-002", 5.0)
    assert done is not None
    assert done.id == "task-002"
    assert queues.tasks(SCENE, 5.0) == ()


@pytest.mark.parametrize(
    ("scene_id", "target"),
    [
        # 先頭でない
        (SCENE, "task-002"),
        # ほかの場面のタスク
        (OTHER, "task-001"),
        # キューの無い場面
        ("scene-003", "task-001"),
        # 未知の ID
        (SCENE, "task-099"),
    ],
)
def test_complete_does_nothing_unless_the_head_of_the_scene(scene_id, target):
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A", "B"], 1.0)
    queues.add(OTHER, ["C"], 1.0)
    assert queues.complete(scene_id, target, 2.0) is None
    assert ids_of(queues.tasks(SCENE, 2.0)) == ["task-001", "task-002"]
    assert ids_of(queues.tasks(OTHER, 2.0)) == ["task-003"]


# --- 取り消しの要求 ---


def test_request_cancel_marks_the_head():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A", "B"], 1.0)
    assert queues.request_cancel(SCENE, "task-001", 3.0) == TaskView(
        id="task-001",
        text="A",
        scene_id=SCENE,
        added_t=1.0,
        active_s=2.0,
        cancel_requested_t=3.0,
    )
    views = queues.tasks(SCENE, 4.0)
    assert [view.cancel_requested_t for view in views] == [3.0, None]


def test_second_request_does_nothing():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A", "B"], 1.0)
    queues.request_cancel(SCENE, "task-001", 3.0)
    assert queues.request_cancel(SCENE, "task-001", 5.0) is None
    assert queues.tasks(SCENE, 5.0)[0].cancel_requested_t == 3.0


@pytest.mark.parametrize(
    ("scene_id", "target"),
    [
        (SCENE, "task-002"),
        (OTHER, "task-001"),
        ("scene-003", "task-001"),
        (SCENE, "task-099"),
    ],
)
def test_request_cancel_does_nothing_unless_the_head_of_the_scene(scene_id, target):
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A", "B"], 1.0)
    queues.add(OTHER, ["C"], 1.0)
    assert queues.request_cancel(scene_id, target, 2.0) is None
    views = queues.tasks(SCENE, 2.0) + queues.tasks(OTHER, 2.0)
    assert [view.cancel_requested_t for view in views] == [None, None, None]


def test_request_stays_until_the_task_is_completed():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A", "B"], 1.0)
    queues.request_cancel(SCENE, "task-001", 2.0)
    queues.add(SCENE, ["C"], 3.0)
    assert queues.tasks(SCENE, 4.0)[0].cancel_requested_t == 2.0
    # 要求が付いたままでも完了にできる
    done = queues.complete(SCENE, "task-001", 5.0)
    assert done is not None
    assert done.cancel_requested_t == 2.0
    views = queues.tasks(SCENE, 5.0)
    assert ids_of(views) == ["task-002", "task-003"]
    # 次の先頭には要求が無く、新しく付けられる
    assert views[0].cancel_requested_t is None
    assert queues.request_cancel(SCENE, "task-002", 6.0) is not None


def test_requested_task_can_be_canceled():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A", "B"], 1.0)
    queues.request_cancel(SCENE, "task-001", 2.0)
    canceled = queues.cancel(["task-001"], 3.0)
    assert ids_of(canceled) == ["task-001"]
    assert canceled[0].cancel_requested_t == 2.0
    assert ids_of(queues.tasks(SCENE, 3.0)) == ["task-002"]


# --- 取り消し ---


def test_cancel_removes_tasks_of_any_scene_and_position():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A", "B", "C"], 1.0)
    queues.add(OTHER, ["D"], 2.0)
    # 戻り値は外した順 (task_ids の順)。先頭でないタスクも外せる
    assert queues.cancel(["task-004", "task-002"], 5.0) == (
        TaskView(
            id="task-004",
            text="D",
            scene_id=OTHER,
            added_t=2.0,
            active_s=0.0,
            cancel_requested_t=None,
        ),
        TaskView(
            id="task-002",
            text="B",
            scene_id=SCENE,
            added_t=1.0,
            active_s=4.0,
            cancel_requested_t=None,
        ),
    )
    assert ids_of(queues.tasks(SCENE, 5.0)) == ["task-001", "task-003"]
    assert queues.tasks(OTHER, 5.0) == ()


def test_cancel_ignores_ids_not_in_open_tasks():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A", "B"], 1.0)
    queues.complete(SCENE, "task-001", 2.0)
    # 未知の ID、完了にしたタスクの ID、同じ ID の 2 回目は無視する
    canceled = queues.cancel(["task-099", "task-001", "task-002", "task-002"], 3.0)
    assert ids_of(canceled) == ["task-002"]
    assert queues.tasks(SCENE, 3.0) == ()
    assert queues.cancel(["task-002"], 4.0) == ()


# --- 続いた秒数 ---


def test_current_scene_starts_without_scene():
    queues = SceneTaskQueues()
    assert queues.current_scene_id is None
    queues.set_current_scene(SCENE, 1.0)
    assert queues.current_scene_id == SCENE
    queues.set_current_scene(None, 2.0)
    assert queues.current_scene_id is None


def test_active_seconds_grow_only_while_the_scene_is_current():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A"], 1.0)
    assert queues.tasks(SCENE, 5.0)[0].active_s == 4.0
    # 別の場面にいる間は止まる
    queues.set_current_scene(OTHER, 5.0)
    assert queues.tasks(SCENE, 9.0)[0].active_s == 4.0
    # 戻ると、止まる前の秒数に足していく
    queues.set_current_scene(SCENE, 9.0)
    assert queues.tasks(SCENE, 12.0)[0].active_s == 7.0


def test_active_seconds_stop_without_scene():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A"], 0.0)
    queues.set_current_scene(None, 3.0)
    assert queues.tasks(SCENE, 10.0)[0].active_s == 3.0
    queues.set_current_scene(SCENE, 10.0)
    assert queues.tasks(SCENE, 11.0)[0].active_s == 4.0


def test_task_added_outside_the_current_scene_stays_at_zero():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(OTHER, ["A"], 2.0)
    assert queues.tasks(OTHER, 8.0)[0].active_s == 0.0
    queues.set_current_scene(OTHER, 8.0)
    assert queues.tasks(OTHER, 10.0)[0].active_s == 2.0


def test_task_added_without_scene_stays_at_zero():
    queues = SceneTaskQueues()
    queues.add(SCENE, ["A"], 1.0)
    assert queues.tasks(SCENE, 5.0)[0].active_s == 0.0


def test_each_task_counts_from_its_own_added_time():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A"], 1.0)
    queues.add(SCENE, ["B"], 3.0)
    assert [view.active_s for view in queues.tasks(SCENE, 6.0)] == [5.0, 3.0]


def test_setting_the_same_scene_again_does_not_reset():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A"], 0.0)
    queues.set_current_scene(SCENE, 5.0)
    assert queues.tasks(SCENE, 10.0)[0].active_s == 10.0


def test_active_seconds_are_rounded_to_milliseconds():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A"], 0.1)
    # 0.3 - 0.1 は浮動小数点で 0.19999999999999998 になる
    assert queues.tasks(SCENE, 0.3)[0].active_s == 0.2
    queues.set_current_scene(OTHER, 0.3)
    queues.set_current_scene(SCENE, 0.7)
    assert queues.tasks(SCENE, 1.0)[0].active_s == 0.5


# --- 最も古いタスク ---


def test_oldest_active_s_without_open_tasks_is_none():
    queues = SceneTaskQueues()
    assert queues.oldest_active_s(SCENE, 1.0) is None
    queues.add(SCENE, ["A"], 1.0)
    queues.complete(SCENE, "task-001", 2.0)
    assert queues.oldest_active_s(SCENE, 3.0) is None


def test_oldest_active_s_is_of_the_earliest_added_task():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A"], 1.0)
    queues.add(SCENE, ["B"], 3.0)
    assert queues.oldest_active_s(SCENE, 6.0) == 5.0
    queues.complete(SCENE, "task-001", 6.0)
    assert queues.oldest_active_s(SCENE, 8.0) == 5.0
    assert queues.oldest_active_s(OTHER, 8.0) is None


def test_oldest_active_s_uses_added_t_rather_than_queue_order():
    # 先頭のタスクより added_t が小さいタスクが、キューの後ろにある
    restored = (
        TaskView(
            id="task-002",
            text="B",
            scene_id=SCENE,
            added_t=20.0,
            active_s=1.0,
            cancel_requested_t=None,
        ),
        TaskView(
            id="task-001",
            text="A",
            scene_id=SCENE,
            added_t=10.0,
            active_s=7.0,
            cancel_requested_t=None,
        ),
    )
    queues = SceneTaskQueues(restored, next_number=3)
    assert ids_of(queues.tasks(SCENE, 30.0)) == ["task-002", "task-001"]
    assert queues.oldest_active_s(SCENE, 30.0) == 7.0


def test_oldest_active_s_with_the_same_added_t_uses_the_front_task():
    restored = (
        TaskView(
            id="task-001",
            text="A",
            scene_id=SCENE,
            added_t=10.0,
            active_s=4.0,
            cancel_requested_t=None,
        ),
        TaskView(
            id="task-002",
            text="B",
            scene_id=SCENE,
            added_t=10.0,
            active_s=6.0,
            cancel_requested_t=None,
        ),
    )
    queues = SceneTaskQueues(restored, next_number=3)
    assert queues.oldest_active_s(SCENE, 30.0) == 4.0


# --- キューの状態 ---


def test_tasks_lists_open_tasks_in_queue_order():
    queues = SceneTaskQueues()
    queues.set_current_scene(SCENE, 0.0)
    queues.add(SCENE, ["A", "B", "C"], 1.0)
    queues.request_cancel(SCENE, "task-001", 2.0)
    assert queues.tasks(SCENE, 4.0) == (
        TaskView(
            id="task-001",
            text="A",
            scene_id=SCENE,
            added_t=1.0,
            active_s=3.0,
            cancel_requested_t=2.0,
        ),
        TaskView(
            id="task-002",
            text="B",
            scene_id=SCENE,
            added_t=1.0,
            active_s=3.0,
            cancel_requested_t=None,
        ),
        TaskView(
            id="task-003",
            text="C",
            scene_id=SCENE,
            added_t=1.0,
            active_s=3.0,
            cancel_requested_t=None,
        ),
    )


def test_tasks_of_a_scene_without_tasks_is_empty():
    assert SceneTaskQueues().tasks(SCENE, 1.0) == ()


# --- 作り直し ---


def test_restored_tasks_keep_the_saved_values():
    queues = SceneTaskQueues(RESTORED, next_number=6)
    assert queues.current_scene_id is None
    # 場面なしで始めるため、続いた秒数は進まない
    assert queues.tasks(SCENE, 100.0) == RESTORED[:2]
    assert queues.tasks(OTHER, 100.0) == RESTORED[2:]
    assert queues.oldest_active_s(SCENE, 100.0) == 12.5


def test_restored_tasks_grow_from_the_saved_active_seconds():
    queues = SceneTaskQueues(RESTORED, next_number=6)
    queues.set_current_scene(SCENE, 100.0)
    views = queues.tasks(SCENE, 103.0)
    assert [view.active_s for view in views] == [15.5, 7.0]
    assert [view.added_t for view in views] == [10.0, 11.0]
    assert [view.cancel_requested_t for view in views] == [None, 30.0]
    assert queues.tasks(OTHER, 103.0)[0].active_s == 2.0


def test_restored_queue_uses_next_number_and_the_saved_order():
    queues = SceneTaskQueues(RESTORED, next_number=6)
    assert ids_of(queues.add(OTHER, ["宿屋に戻る"], 100.0).added) == ["task-006"]
    # restored の順がキューの順 (SCENE の先頭は task-003)
    assert queues.complete(SCENE, "task-005", 100.0) is None
    assert queues.complete(SCENE, "task-003", 100.0) is not None
    # 作り直したタスクも、重複の判定に入る
    assert queues.add(SCENE, ["主人に 話しかける"], 101.0).dropped == (
        DroppedText(text="主人に 話しかける", reason="duplicate"),
    )
