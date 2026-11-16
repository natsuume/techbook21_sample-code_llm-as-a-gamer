import base64
import json
import os
from pathlib import Path

import pytest

from switch2_layers.file_io import write_bytes_atomic
from switch2_layers.frames.frame_store import FrameRecord
from switch2_layers.orchestrator.decision_types import (
    DecisionError,
    MacroAction,
    MonitorCase,
    MonitorCreate,
    PressAction,
    ProcedureAction,
    WaitAction,
)
from switch2_layers.orchestrator.orchestrator_events import (
    CaseHit,
    MonitorEnd,
    OrchestratorEvents,
)
from switch2_layers.orchestrator.turn_input import (
    FRAMES_GUIDE_TEXT,
    NONE_TEXT,
    TURN_MAX_IMAGES,
    SessionOpening,
    TurnContext,
    TurnImage,
    action_to_json,
    build_session_opening_text,
    build_turn_content,
    build_turn_text,
    describe_decision_error,
    describe_events,
    describe_macros,
    describe_monitor,
    describe_progress_for_turn,
    describe_running,
    image_label,
    list_note_files,
    read_turn_images,
    select_turn_frames,
)
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.progress_text import (
    NO_PROGRESS_TEXT,
    describe_progress_values,
)
from switch2_layers.run_directory import RunPaths

PRESS_A = PressAction(buttons=("A",), sticks={}, seconds=None)
HOLD_LEFT = PressAction(buttons=("B", "LEFT"), sticks={}, seconds=0.5)
STICK = PressAction(buttons=(), sticks={"left": (0.0, 1.0)}, seconds=1.2)
WAIT = WaitAction(seconds=0.3)
WALK = ProcedureAction(
    name="walk",
    args={"direction": "UP", "count": 3, "seconds": 0.2, "interval_seconds": 0.1},
)
NEXT_MESSAGE = MacroAction(name="next_message")
DIALOG_MONITOR = MonitorCreate(
    name="dialog",
    cases=(
        MonitorCase(
            condition="画面の下に会話の枠があり、右下に▼が出ている",
            actions=(PRESS_A,),
            repeat="every",
        ),
        MonitorCase(
            condition="はい・いいえの選択肢が出ている",
            actions=(PressAction(buttons=("B",), sticks={}, seconds=None),),
            repeat="once",
        ),
    ),
)


def events(**changes: object) -> OrchestratorEvents:
    values: dict[str, object] = {
        "case_hits": (),
        "monitor_ends": (),
        "discarded_action_count": 0,
        "blocked_buttons": (),
        "running": False,
        "remaining_action_count": 0,
        "macros": {},
        "monitor": None,
    }
    values.update(changes)
    return OrchestratorEvents(**values)  # type: ignore[arg-type]


def interval(t: float) -> FrameRecord:
    milliseconds = round(t * 1000)
    return FrameRecord(
        t=t,
        kind="interval",
        file=f"frames/interval/{milliseconds:09d}.jpg",
        width=640,
        height=360,
    )


def surface(t: float) -> FrameRecord:
    milliseconds = round(t * 1000)
    return FrameRecord(
        t=t,
        kind="surface",
        file=f"frames/surface/{milliseconds:09d}.jpg",
        width=1280,
        height=720,
    )


# --- 画像の選び方 ---


def test_first_turn_of_a_session_gets_the_latest_frame_only():
    records = [interval(1.0), interval(2.0), surface(2.5), interval(3.0), interval(9.0)]
    assert select_turn_frames(records, None, now_t=5.0) == [interval(3.0)]


def test_frames_since_the_previous_input_are_picked_evenly_up_to_three():
    records = [interval(float(t)) for t in range(1, 11)]
    # 前の入力は t=5 に送った。範囲は 5 < t <= 10 の 5 枚
    assert select_turn_frames(records, 5.0, now_t=10.0) == [
        interval(6.0),
        interval(8.0),
        interval(10.0),
    ]
    assert TURN_MAX_IMAGES == 3


def test_frame_at_the_previous_send_time_is_not_sent_again():
    records = [interval(5.0), interval(6.0), interval(7.0)]
    assert select_turn_frames(records, 5.0, now_t=7.0) == [interval(6.0), interval(7.0)]


def test_frames_after_now_and_surface_frames_are_not_picked():
    records = [interval(6.0), surface(6.5), interval(7.0), interval(12.0)]
    assert select_turn_frames(records, 5.0, now_t=7.0) == [interval(6.0), interval(7.0)]


def test_without_new_frames_the_latest_frame_is_sent_again():
    records = [interval(1.0), interval(4.0)]
    assert select_turn_frames(records, 5.0, now_t=6.0) == [interval(4.0)]


def test_no_frames_gives_no_images():
    assert select_turn_frames([], None, now_t=5.0) == []
    assert select_turn_frames([surface(1.0)], 0.5, now_t=5.0) == []


def test_records_out_of_order_are_sorted_by_time():
    records = [interval(8.0), interval(6.0), interval(7.0)]
    assert select_turn_frames(records, 5.0, now_t=8.0) == [
        interval(6.0),
        interval(7.0),
        interval(8.0),
    ]


# --- 画像を読む ---


def make_run(tmp_path: Path) -> RunPaths:
    paths = RunPaths(tmp_path / "runs" / "20261116-120000")
    paths.interval_frames_dir.mkdir(parents=True)
    return paths


def test_images_are_read_as_saved(tmp_path):
    paths = make_run(tmp_path)
    write_bytes_atomic(paths.interval_frame(6.0), b"jpeg-6")
    write_bytes_atomic(paths.interval_frame(7.0), b"jpeg-7")
    images = read_turn_images(paths, [interval(6.0), interval(7.0)])
    assert images == [
        TurnImage(t=6.0, file="frames/interval/000006000.jpg", jpeg=b"jpeg-6"),
        TurnImage(t=7.0, file="frames/interval/000007000.jpg", jpeg=b"jpeg-7"),
    ]


def test_missing_images_and_links_outside_interval_frames_are_left_out(tmp_path):
    paths = make_run(tmp_path)
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"secret")
    os.symlink(secret, paths.interval_frame(6.0))
    write_bytes_atomic(paths.interval_frame(8.0), b"jpeg-8")
    # frames.jsonl の行が実行ディレクトリーの外を指していても読まない
    (paths.root.parent / "secret.jpg").write_bytes(b"secret")
    outside = FrameRecord(
        t=9.0, kind="interval", file="../secret.jpg", width=1, height=1
    )
    images = read_turn_images(
        paths, [interval(6.0), interval(7.0), interval(8.0), outside]
    )
    assert images == [
        TurnImage(t=8.0, file="frames/interval/000008000.jpg", jpeg=b"jpeg-8")
    ]


# --- 操作の JSON ---


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (PRESS_A, {"type": "press", "buttons": ["A"]}),
        (HOLD_LEFT, {"type": "press", "buttons": ["B", "LEFT"], "seconds": 0.5}),
        (
            STICK,
            {
                "type": "press",
                "buttons": [],
                "sticks": {"left": {"x": 0.0, "y": 1.0}},
                "seconds": 1.2,
            },
        ),
        (WAIT, {"type": "wait", "seconds": 0.3}),
        (
            WALK,
            {
                "type": "procedure",
                "name": "walk",
                "args": {
                    "direction": "UP",
                    "count": 3,
                    "seconds": 0.2,
                    "interval_seconds": 0.1,
                },
            },
        ),
        (NEXT_MESSAGE, {"type": "macro", "name": "next_message"}),
    ],
)
def test_actions_are_written_in_the_json_form_of_the_decision(action, expected):
    assert action_to_json(action) == expected


# --- 出来事 ---


def test_events_describe_hits_ends_discarded_actions_and_blocked_buttons():
    text = describe_events(
        events(
            case_hits=(
                CaseHit(
                    monitor="dialog",
                    case_index=0,
                    condition="画面の下に会話の枠があり、右下に▼が出ている",
                    count=4,
                    actions=(PRESS_A,),
                ),
            ),
            monitor_ends=(
                MonitorEnd(name="dialog", reason="completed", detail="C1 を実行した"),
                MonitorEnd(name="menu", reason="failed", detail="Jev failed 5 times"),
            ),
            discarded_action_count=2,
            blocked_buttons=("HOME", "CAPTURE"),
        )
    )
    lines = text.splitlines()
    assert lines == [
        (
            "監視 dialog の条件 C0 (画面の下に会話の枠があり、右下に▼が出ている) に 4 回当たり、"
            '操作 [{"type": "press", "buttons": ["A"]}] を実行した'
        ),
        "監視 dialog が終わった (completed): C1 を実行した",
        "監視 menu が終わった (failed): Jev failed 5 times",
        "新しい actions で捨てた前の操作: 2 個",
        "押せずに外したボタン: HOME, CAPTURE",
    ]


def test_no_events_is_written_as_none():
    assert describe_events(events()) == NONE_TEXT


def test_running_state_and_remaining_actions():
    assert describe_running(events(running=True, remaining_action_count=5)) == (
        "実行中: はい\n残りの操作: 5 個"
    )
    assert describe_running(events()) == "実行中: いいえ\n残りの操作: 0 個"


# --- 画面の停滞 ---


def progress(t: float) -> ProgressState:
    return ProgressState(t=t, static_s=12.4, no_novel_s=85.0, window_s=300.0)


def test_fresh_progress_is_written_with_its_time():
    state = progress(97.0)
    assert describe_progress_for_turn(state, now_t=100.0) == (
        f"計測した時刻: t=97.000\n{describe_progress_values(state)}"
    )


def test_progress_exactly_at_the_age_limit_is_used():
    state = progress(90.0)
    assert describe_progress_for_turn(state, now_t=100.0).startswith("計測した時刻")


def test_old_or_missing_progress_is_written_as_no_value():
    assert describe_progress_for_turn(progress(89.999), now_t=100.0) == NO_PROGRESS_TEXT
    assert describe_progress_for_turn(None, now_t=100.0) == NO_PROGRESS_TEXT


# --- 前の JSON の誤り・監視・マクロ ---


def test_previous_error_is_listed():
    error = DecisionError(messages=("unknown key 'action'", "actions[0].type: 'jump'"))
    assert describe_decision_error(error) == (
        "- unknown key 'action'\n- actions[0].type: 'jump'"
    )
    assert describe_decision_error(None) == NONE_TEXT


def test_monitor_is_written_as_json():
    assert json.loads(describe_monitor(DIALOG_MONITOR)) == {
        "name": "dialog",
        "cases": [
            {
                "condition": "画面の下に会話の枠があり、右下に▼が出ている",
                "actions": [{"type": "press", "buttons": ["A"]}],
                "repeat": "every",
            },
            {
                "condition": "はい・いいえの選択肢が出ている",
                "actions": [{"type": "press", "buttons": ["B"]}],
                "repeat": "once",
            },
        ],
    }
    assert "会話の枠" in describe_monitor(DIALOG_MONITOR)
    assert describe_monitor(None) == NONE_TEXT


def test_macros_are_written_as_json_in_name_order():
    text = describe_macros({"next_message": (PRESS_A, WAIT), "back": (HOLD_LEFT,)})
    assert json.loads(text) == {
        "back": [{"type": "press", "buttons": ["B", "LEFT"], "seconds": 0.5}],
        "next_message": [
            {"type": "press", "buttons": ["A"]},
            {"type": "wait", "seconds": 0.3},
        ],
    }
    assert list(json.loads(text)) == ["back", "next_message"]
    assert describe_macros({}) == NONE_TEXT


# --- ふつうのターンのテキスト ---


def test_turn_text_has_every_section_in_order():
    error = DecisionError(messages=("no json block",))
    context = TurnContext(
        now_t=12.345,
        events=events(
            blocked_buttons=("HOME",),
            running=True,
            remaining_action_count=3,
            macros={"next_message": (PRESS_A,)},
            monitor=DIALOG_MONITOR,
        ),
        progress=progress(10.0),
        previous_error=error,
    )
    text = build_turn_text(context)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 時刻",
        "## 前の判断の後の出来事",
        "## 主ループの操作",
        "## 画面の停滞 (コードの計測)",
        "## 前の JSON の誤り",
        "## 今の監視",
        "## 今のマクロ",
        "## 古いフレーム",
    ]
    assert "t=12.345" in text
    assert describe_events(context.events) in text
    assert describe_running(context.events) in text
    assert describe_progress_for_turn(context.progress, 12.345) in text
    assert describe_decision_error(error) in text
    assert describe_monitor(DIALOG_MONITOR) in text
    assert describe_macros({"next_message": (PRESS_A,)}) in text
    assert FRAMES_GUIDE_TEXT in text
    assert "frames/interval/" in text


def test_turn_text_without_anything_to_report():
    text = build_turn_text(
        TurnContext(now_t=1.0, events=events(), progress=None, previous_error=None)
    )
    assert NO_PROGRESS_TEXT in text
    assert text.count(NONE_TEXT) >= 4


# --- セッションの最初の入力 ---


def test_session_opening_has_instruction_note_files_and_handoff():
    text = build_session_opening_text(
        SessionOpening(
            initial_instruction="RPG の主人公を名付けて冒険を始めてください",
            note_files=("common/controls.md", "handoff.md", "skills/game-x/SKILL.md"),
            handoff_text="# 引き継ぎ\n今の目標: 町に入る\n",
        )
    )
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 人の初期指示",
        "## notes/ のファイル",
        "## 引き継ぎ (notes/handoff.md)",
    ]
    assert "RPG の主人公を名付けて冒険を始めてください" in text
    assert "- common/controls.md\n- handoff.md\n- skills/game-x/SKILL.md" in text
    assert "今の目標: 町に入る" in text


def test_session_opening_without_instruction_or_handoff():
    text = build_session_opening_text(
        SessionOpening(initial_instruction=None, note_files=(), handoff_text="  \n")
    )
    assert "## 人の初期指示" not in text
    assert text.splitlines() == [
        "## notes/ のファイル",
        NONE_TEXT,
        "## 引き継ぎ (notes/handoff.md)",
        NONE_TEXT,
    ]


def test_note_files_are_listed_without_links_and_temporary_files(tmp_path):
    notes = tmp_path / "notes"
    (notes / "common").mkdir(parents=True)
    (notes / "common" / "controls.md").write_text("x\n", encoding="utf-8")
    (notes / "strategy.md").write_text("x\n", encoding="utf-8")
    (notes / "skills" / "game-x").mkdir(parents=True)
    (notes / "skills" / "game-x" / "SKILL.md").write_text("x\n", encoding="utf-8")
    (notes / ".strategy.md.abc123.tmp").write_text("x\n", encoding="utf-8")
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "deep" / "secret.md").write_text("x\n", encoding="utf-8")
    os.symlink(outside / "deep" / "secret.md", notes / "linked.md")
    os.symlink(outside, notes / "linked-dir")
    assert list_note_files(notes) == (
        "common/controls.md",
        "skills/game-x/SKILL.md",
        "strategy.md",
    )


def test_missing_notes_directory_lists_nothing(tmp_path):
    assert list_note_files(tmp_path / "missing") == ()


# --- メッセージの内容 ---


def test_content_has_text_then_a_label_and_image_for_each_frame():
    images = [
        TurnImage(t=6.0, file="frames/interval/000006000.jpg", jpeg=b"jpeg-6"),
        TurnImage(t=8.5, file="frames/interval/000008500.jpg", jpeg=b"jpeg-8"),
    ]
    content = build_turn_content("本文", images)
    assert content == [
        {"type": "text", "text": "本文"},
        {"type": "text", "text": "t=6.000 の画面 (frames/interval/000006000.jpg)"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(b"jpeg-6").decode("ascii"),
            },
        },
        {"type": "text", "text": "t=8.500 の画面 (frames/interval/000008500.jpg)"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(b"jpeg-8").decode("ascii"),
            },
        },
    ]
    assert image_label(images[0]) == "t=6.000 の画面 (frames/interval/000006000.jpg)"


def test_content_without_images_is_the_text_only():
    assert build_turn_content("引き継ぎ", []) == [{"type": "text", "text": "引き継ぎ"}]
