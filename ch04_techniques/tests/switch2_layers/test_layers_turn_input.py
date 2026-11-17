import base64
import json
import os

import pytest

from switch2_layers.frames.latest_frames import LatestFrame
from switch2_layers.orchestrator.decision_types import (
    DecisionError,
    DelegateChoice,
    DelegateStart,
    MacroAction,
    MonitorCase,
    MonitorCreate,
    PressAction,
    ProcedureAction,
    WaitAction,
)
from switch2_layers.orchestrator.orchestrator_events import (
    CaseHit,
    DelegateEnd,
    DelegatePick,
    DelegationStatus,
    MainActionsRun,
    MonitorEnd,
    OrchestratorEvents,
)
from switch2_layers.orchestrator.skill_copy import SkippedGameSkill
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
    carry_over_events,
    describe_decision_error,
    describe_delegation,
    describe_events,
    describe_macros,
    describe_monitor,
    describe_previous_actions,
    describe_progress_for_turn,
    describe_running,
    describe_skipped_skills,
    image_label,
    list_note_files,
    select_turn_frames,
)
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.progress_text import (
    NO_PROGRESS_TEXT,
    describe_progress_values,
)

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


def latest(t: float) -> LatestFrame:
    """frames/latest/ に置いた時刻 t の画像 (JPEG の代わりに、t の分かるバイト列)。"""
    return LatestFrame(
        t=t,
        source_file=f"frames/interval/{round(t * 1000):09d}.jpg",
        jpeg=f"stamped-{t:.3f}".encode(),
    )


def turn_image(t: float) -> TurnImage:
    """latest(t) から作る入力の画像 (file は元の frames/interval/ のファイル)。"""
    return TurnImage(
        t=t,
        file=f"frames/interval/{round(t * 1000):09d}.jpg",
        jpeg=f"stamped-{t:.3f}".encode(),
    )


# --- 画像の選び方 ---


def test_turn_gets_the_latest_five_frames_oldest_first():
    # 最新のフレームは新しい順 (frames/latest/1.jpg が先頭) に受け取る
    newest_first = [latest(t) for t in (10.0, 9.8, 9.6, 9.4, 9.2)]
    assert select_turn_frames(newest_first) == [
        turn_image(t) for t in (9.2, 9.4, 9.6, 9.8, 10.0)
    ]
    assert TURN_MAX_IMAGES == 5


def test_fewer_than_five_frames_are_all_sent():
    assert select_turn_frames([latest(2.0), latest(1.0)]) == [
        turn_image(1.0),
        turn_image(2.0),
    ]


def test_only_the_newest_five_frames_are_sent():
    newest_first = [latest(float(t)) for t in range(10, 3, -1)]
    assert select_turn_frames(newest_first) == [
        turn_image(float(t)) for t in range(6, 11)
    ]


def test_no_frames_gives_no_images():
    assert select_turn_frames([]) == []


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


# --- 前の操作の列の時刻 ---


def test_previous_actions_are_written_with_the_start_and_the_last_release():
    run = MainActionsRun(started_t=10.0, released_t=11.25, ended_t=11.25)
    assert describe_previous_actions(run) == (
        "始めた時刻: t=10.000\n終えた時刻 (最後に離した時刻): t=11.250"
    )


def test_previous_actions_ending_with_a_wait_use_the_last_release():
    run = MainActionsRun(started_t=10.0, released_t=11.25, ended_t=12.0)
    assert describe_previous_actions(run).endswith(
        "終えた時刻 (最後に離した時刻): t=11.250"
    )


def test_previous_actions_without_a_release_use_the_end_time():
    run = MainActionsRun(started_t=10.0, released_t=None, ended_t=10.5)
    assert describe_previous_actions(run) == (
        "始めた時刻: t=10.000\n終えた時刻 (最後に離した時刻): t=10.500"
    )


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


SKIPPED = (
    SkippedGameSkill(
        name="game-tools",
        reason="frontmatter に name と description のほかの欄がある: allowed-tools",
    ),
    SkippedGameSkill(
        name="game-shell", reason="スキルからシェルのコマンドを実行する書き方がある"
    ),
)


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
        skipped_skills=SKIPPED,
    )
    text = build_turn_text(context)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 時刻",
        "## 前の判断の後の出来事",
        "## 主ループの操作",
        "## 画面の停滞 (コードの計測)",
        "## 前の JSON の誤り",
        "## 写さなかったスキル",
        "## 今の監視",
        "## 今のマクロ",
        "## 古いフレーム",
    ]
    assert "t=12.345" in text
    assert describe_events(context.events) in text
    assert describe_running(context.events) in text
    assert describe_progress_for_turn(context.progress, 12.345) in text
    assert describe_decision_error(error) in text
    assert describe_skipped_skills(SKIPPED) in text
    assert describe_monitor(DIALOG_MONITOR) in text
    assert describe_macros({"next_message": (PRESS_A,)}) in text
    assert FRAMES_GUIDE_TEXT in text
    assert "frames/interval/" in text


def test_turn_text_without_anything_to_report():
    context = TurnContext(
        now_t=1.0, events=events(), progress=None, previous_error=None
    )
    assert context.skipped_skills == ()
    text = build_turn_text(context)
    assert NO_PROGRESS_TEXT in text
    assert text.count(NONE_TEXT) >= 5


def test_turn_text_has_the_previous_actions_after_the_main_loop_actions():
    run = MainActionsRun(started_t=10.0, released_t=11.25, ended_t=11.25)
    context = TurnContext(
        now_t=11.55,
        events=events(),
        progress=None,
        previous_error=None,
        previous_actions=run,
    )
    text = build_turn_text(context)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 時刻",
        "## 前の判断の後の出来事",
        "## 主ループの操作",
        "## 前の操作の列",
        "## 画面の停滞 (コードの計測)",
        "## 前の JSON の誤り",
        "## 写さなかったスキル",
        "## 今の監視",
        "## 今のマクロ",
        "## 古いフレーム",
    ]
    assert f"## 前の操作の列\n{describe_previous_actions(run)}\n" in text


def test_turn_text_without_previous_actions_has_no_action_times():
    # 前のターンに操作が無かった (actions が空・JSON の誤り・最初のターン) ときは、時刻を書かない
    context = TurnContext(
        now_t=1.0, events=events(), progress=None, previous_error=None
    )
    assert context.previous_actions is None
    text = build_turn_text(context)
    assert "## 前の操作の列" not in text
    assert "始めた時刻" not in text
    assert "終えた時刻" not in text


# --- 写さなかったスキル ---


def test_skipped_skills_are_listed_with_their_reasons():
    assert describe_skipped_skills(SKIPPED) == (
        "- game-tools: frontmatter に name と description のほかの欄がある: allowed-tools\n"
        "- game-shell: スキルからシェルのコマンドを実行する書き方がある"
    )
    assert describe_skipped_skills(()) == NONE_TEXT


# --- 出来事の繰り越し ---


HIT_OLD = CaseHit(
    monitor="dialog", case_index=0, condition="会話の枠", count=2, actions=(PRESS_A,)
)
HIT_NEW = CaseHit(
    monitor="dialog", case_index=1, condition="選択肢", count=1, actions=(WAIT,)
)
END_OLD = MonitorEnd(name="dialog", reason="completed", detail="C1 を実行した")
END_NEW = MonitorEnd(name="menu", reason="failed", detail="Jev failed 5 times")


def test_without_carried_events_the_taken_events_are_used():
    taken = events(case_hits=(HIT_NEW,), discarded_action_count=1)
    assert carry_over_events(None, taken) == taken


def test_one_time_events_are_combined_and_current_state_is_taken():
    carried = events(
        case_hits=(HIT_OLD,),
        monitor_ends=(END_OLD,),
        discarded_action_count=2,
        blocked_buttons=("HOME", "CAPTURE"),
        running=False,
        remaining_action_count=0,
        macros={"old": (PRESS_A,)},
        monitor=None,
    )
    taken = events(
        case_hits=(HIT_NEW,),
        monitor_ends=(END_NEW,),
        discarded_action_count=3,
        blocked_buttons=("CAPTURE", "ZL"),
        running=True,
        remaining_action_count=4,
        macros={"new": (WAIT,)},
        monitor=DIALOG_MONITOR,
    )
    assert carry_over_events(carried, taken) == events(
        case_hits=(HIT_OLD, HIT_NEW),
        monitor_ends=(END_OLD, END_NEW),
        discarded_action_count=5,
        blocked_buttons=("HOME", "CAPTURE", "ZL"),
        running=True,
        remaining_action_count=4,
        macros={"new": (WAIT,)},
        monitor=DIALOG_MONITOR,
    )


def test_carried_events_are_kept_when_nothing_new_happened():
    carried = events(case_hits=(HIT_OLD,), blocked_buttons=("HOME",))
    merged = carry_over_events(carried, events())
    assert merged.case_hits == (HIT_OLD,)
    assert merged.blocked_buttons == ("HOME",)


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
    # 初期指示が無いことも主ループに伝えるため、初期指示の節は省かずに本文を NONE_TEXT にする
    text = build_session_opening_text(
        SessionOpening(initial_instruction=None, note_files=(), handoff_text="  \n")
    )
    assert text.splitlines() == [
        "## 人の初期指示",
        NONE_TEXT,
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


# --- 委任型の委任の状況と出来事 ---

DELEGATION_VALUES = DelegateStart(
    tasks=("最重要: HP を 0 にせずに敵をすべて倒す", "敵の攻撃は、可能なら避ける"),
    cautions=("衝撃波は横一列に進む",),
    choices=(DelegateChoice(name="attack", description="前の敵を攻撃する"),),
)
DELEGATING = DelegationStatus(
    active=True,
    started_t=12.5,
    values=DELEGATION_VALUES,
    last_pick=DelegatePick(name="attack", t=13.25),
    return_streak=1,
)
NOT_DELEGATING = DelegationStatus(
    active=False, started_t=None, values=None, last_pick=None, return_streak=0
)
JEV_RETURN = DelegateEnd(
    reason="jev_return", detail="jev chose return_control 2 times in a row"
)
MAIN_STOP = DelegateEnd(
    reason="main_stop", detail="the main loop wrote the delegate stop"
)


def test_delegate_status_while_delegating_has_the_values_the_last_pick_and_the_count():
    assert describe_delegation(DELEGATING).splitlines() == [
        "委任中: はい",
        "委任を始めた時刻: t=12.500",
        'タスク: ["最重要: HP を 0 にせずに敵をすべて倒す", "敵の攻撃は、可能なら避ける"]',
        '注意事項: ["衝撃波は横一列に進む"]',
        '行動: [{"name": "attack", "description": "前の敵を攻撃する"}]',
        "Jev が直近に選んだ行動: attack (t=13.250)",
        "Jev が「操作を返す」を続けて選んだ回数: 1",
    ]


def test_delegate_status_without_any_delegation_says_none():
    assert describe_delegation(NOT_DELEGATING).splitlines() == [
        "委任中: いいえ",
        f"委任を始めた時刻: {NONE_TEXT}",
        f"タスク: {NONE_TEXT}",
        f"注意事項: {NONE_TEXT}",
        f"行動: {NONE_TEXT}",
        f"Jev が直近に選んだ行動: {NONE_TEXT}",
        "Jev が「操作を返す」を続けて選んだ回数: 0",
    ]


def test_delegate_turn_text_has_the_delegation_section_instead_of_the_monitor():
    context = TurnContext(
        now_t=14.0,
        events=events(delegation=DELEGATING),
        progress=None,
        previous_error=None,
    )
    text = build_turn_text(context)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 時刻",
        "## 前の判断の後の出来事",
        "## 主ループの操作",
        "## 画面の停滞 (コードの計測)",
        "## 前の JSON の誤り",
        "## 写さなかったスキル",
        "## 委任",
        "## 今のマクロ",
        "## 古いフレーム",
    ]
    assert f"## 委任\n{describe_delegation(DELEGATING)}\n" in text


def test_delegate_events_tell_the_return_of_control_and_the_discarded_actions():
    lines = describe_events(
        events(delegate_ends=(JEV_RETURN,), delegate_discarded_action_count=3)
    ).splitlines()
    assert (
        "委任が終わり、操作権が主ループに戻った (jev_return): "
        "jev chose return_control 2 times in a row"
    ) in lines
    assert "委任中のため、操作を捨てた: 3 個" in lines


def test_delegate_one_time_events_are_carried_over():
    carried = events(delegate_ends=(MAIN_STOP,), delegate_discarded_action_count=2)
    taken = events(
        delegate_ends=(JEV_RETURN,),
        delegate_discarded_action_count=1,
        delegation=NOT_DELEGATING,
    )
    merged = carry_over_events(carried, taken)
    assert merged.delegate_ends == (MAIN_STOP, JEV_RETURN)
    assert merged.delegate_discarded_action_count == 3
    assert merged.delegation == NOT_DELEGATING
