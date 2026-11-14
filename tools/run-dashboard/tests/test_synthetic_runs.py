"""合成の実行を書く補助 (synthetic_runs) そのものの確認。run_dashboard の関数は呼ばない。

ほかのテストの期待値が前提にしている記録 (相反する十字キー、スティックの方向の同点、しきい値ちょうどの
advisor の問い、日付をまたぐ行、壊れた行) が、実際に書かれていることを第4章の関数で確かめる。
"""

import json
from pathlib import Path

from switch2_agent.action_decision import Thresholds, decide_action
from switch2_agent.questions import build_questions
from synthetic_runs import (
    DASHBOARD_START_TIME,
    RUN_NAMES_NEWEST_FIRST,
    UNTERMINATED_FRAME_LINE,
)


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_write_all_runs_writes_six_runs_with_expected_records(runs):
    assert sorted(path.name for path in runs.runs_dir.iterdir()) == sorted(
        RUN_NAMES_NEWEST_FIRST
    )

    dashboard = runs.dashboard.run_dir
    for relative in [
        "run.jsonl",
        "requests.jsonl",
        "frames.jsonl",
        "pad.jsonl",
        "thumbs/1000750000.jpg",
        "advisor-strategy-001/context.md",
        "advisor-strategy-001/frame-01.jpg",
        "advisor-strategy-001/stream.jsonl",
        "advisor-strategy-001/result.json",
        "advisor-controls-001/stream.jsonl",
    ]:
        assert (dashboard / relative).is_file(), relative
    # 実行中の controls の実行には result.json が無い。--save-requests が無いので frames/ も無い
    assert not (dashboard / "advisor-controls-001" / "result.json").exists()
    assert not (dashboard / "frames").exists()

    # run.jsonl: start より前の record_error、start、start のすぐ後に書いた時刻がわずかに前の record_error、
    # 日付をまたいだ 00:00 台の行
    run_lines = read_lines(dashboard / "run.jsonl")
    assert [line["event"] for line in run_lines[:3]] == [
        "record_error",
        "start",
        "record_error",
    ]
    assert run_lines[0]["time"] == "23:59:49.500"
    assert run_lines[1]["time"] == DASHBOARD_START_TIME
    assert run_lines[2]["time"] == "23:59:49.997"
    assert run_lines[2]["kind"] == "frames"
    assert run_lines[-1]["event"] == "record_end"
    assert run_lines[-1]["time"] == "00:00:05.000"
    # decision の行は number と advisor_accepted を持ち、strategy と controls を受け付けた要求がある
    decisions = [line for line in run_lines if line["event"] == "decision"]
    assert [(line["number"], line["advisor_accepted"]) for line in decisions] == [
        (1, ["strategy"]),
        (3, ["controls"]),
        (4, []),
        (5, []),
    ]
    # strategy が変わった要求は、strategy の実行の終了 (1.25 + 4.0 秒) から 1 秒を超えて後に送り、
    # その直前の要求は終了より前に送った
    strategy_end = 1.25 + runs.dashboard.strategy_duration_s
    t_sent = runs.dashboard.t_sent
    assert t_sent[runs.dashboard.strategy_changed_request] > strategy_end + 1.0
    assert t_sent[runs.dashboard.controls_request] < strategy_end
    # 記録の無い実行も、tips の advisor を受け付けた decision の行を持つ
    no_dashboard_decisions = [
        line
        for line in read_lines(runs.no_dashboard.run_dir / "run.jsonl")
        if line["event"] == "decision"
    ]
    assert [line["advisor_accepted"] for line in no_dashboard_decisions] == [
        [],
        [],
        ["tips"],
    ]
    assert no_dashboard_decisions[2]["number"] == runs.no_dashboard.tips_request

    # frames.jsonl: 第4章の SharpnessValleyDetector で、1.0 秒に谷に向かい始め (fall)、1.25 秒に 0.75 秒の
    # 山を残して谷を確定する (close)
    frame_lines = read_lines(dashboard / "frames.jsonl")
    assert [line["phase"] for line in frame_lines] == [
        "rising",
        "rising",
        "falling",
        "rising",
        "rising",
        "rising",
        "rising",
    ]
    assert [line["diff"] for line in frame_lines] == [
        None,
        0.0,
        30.0,
        0.0,
        40.0,
        0.0,
        40.0,
    ]
    assert [line["ref"] for line in frame_lines] == [
        25.0,
        50.0,
        160.0,
        45.0,
        47.5,
        50.0,
        52.5,
    ]
    assert [line["event"] for line in frame_lines] == [
        None,
        None,
        {"kind": "fall"},
        {
            "kind": "close",
            "candidate_mono": 1000.75,
            "candidate_sharpness": 200.0,
            "kept": True,
            "similar_diff": None,
        },
        None,
        None,
        None,
    ]
    pad_lines = read_lines(dashboard / "pad.jsonl")
    assert [line["state"] for line in pad_lines] == [
        "A+B+UP+LS:0.4375,0",
        "A+UP+LS:0.4375,0",
        "none",
        "RIGHT+RS:0,-1",
        "none",
    ]

    # requests.jsonl の押した要求: 第4章の decide_action で、DOWN が UP に負けて外れ、LS_y は同点で倒さず、
    # しきい値ちょうどの tips は求めない
    request_lines = read_lines(dashboard / "requests.jsonl")
    assert request_lines[0]["event"] == "header"
    pressed = request_lines[runs.dashboard.pressed_request]
    assert pressed["number"] == runs.dashboard.pressed_request
    action = decide_action(
        pressed["probabilities"],
        pressed["choices"],
        build_questions(),
        Thresholds(advisor=runs.dashboard.threshold_advisor),
    )
    assert action.held.buttons == frozenset({"A", "UP"})
    assert action.taps == frozenset({"B"})
    down = pressed["choices"]["button_DOWN"]
    assert down["hold"] + down["tap"] > down["none"]
    ls_y = pressed["choices"]["stick_LS_y"]
    assert ls_y["none"] == ls_y["up"]
    assert action.held.sticks == {"LS": (0.4375, 0.0)}
    assert action.advisor_requested == frozenset({"strategy"})
    assert pressed["probabilities"]["advisor_tips"] == runs.dashboard.threshold_advisor
    assert pressed["images"][0]["thumb"] == "thumbs/1001000000.jpg"
    assert pressed["images"][0]["file"] is None
    error = request_lines[runs.dashboard.error_request]
    assert (error["result"], error["probabilities"], error["choices"]) == (
        "error",
        None,
        None,
    )

    # 壊れた行のある実行: frames.jsonl は改行で終わっていない行で終わる
    broken_frames = (runs.broken.run_dir / "frames.jsonl").read_text(encoding="utf-8")
    assert broken_frames.endswith(UNTERMINATED_FRAME_LINE)

    # --save-requests だけの実行は frames/ に要求の画像がある
    for relative in runs.save_requests.request_files.values():
        assert (runs.save_requests.run_dir / relative).is_file()

    # 古い形の 2 つの実行は、役割を持たない形の advisor のディレクトリーを持つ
    for old in (runs.no_answer_type, runs.single_advisor_question):
        assert (old.run_dir / "advisor-001" / "result.json").is_file()
    assert runs.single_advisor_question.question_count == 34
