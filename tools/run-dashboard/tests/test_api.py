"""6 つの合成の実行 (synthetic_runs) に対する各 API の応答。"""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from switch2_agent.questions import build_questions
from synthetic_runs import (
    CONTROLS_NOTE,
    JPEG,
    RUN_NAMES_NEWEST_FIRST,
    STRATEGY_NOTE_FIRST,
    STRATEGY_NOTE_SECOND,
    TIPS_NOTE,
)

from run_dashboard.app import (
    ALLOWED_HOSTS,
    BUILD_INSTRUCTIONS_TEXT,
    DEFAULT_WEB_DIST_DIR,
    create_app,
    file_media_type,
)

QUESTION_NAMES = [question.name for question in build_questions()]
EMPTY = "(まだ書かれていない)"
NO_SKIPPED_LINES = {"run": 0, "requests": 0, "frames": 0, "pad": 0, "advisor_steps": 0}
BUTTON_A_QUESTION = {
    "name": "button_A",
    "text": "今、A ボタンをどうするか。",
    "kind": "button",
    "answer_type": "choice",
    "choices": [
        {"name": "none", "description": "押さない"},
        {
            "name": "hold",
            "description": "押した状態にする。押した状態は次の判断まで続く",
        },
        {"name": "tap", "description": "1 回だけ短く押す"},
    ],
}
# 記録がそろった実行の、押した要求 (number 1) の問いの値 (ほかの問いは 0.0)
PRESSED_VALUES = {
    "button_A": 0.875,
    "button_B": 0.75,
    "button_UP": 0.875,
    "button_DOWN": 0.75,
    "stick_LS_x": 0.75,
    "stick_LS_y": 0.625,
    "stick_LS_x_at_least_50": 0.5,
    "stick_LS_x_at_least_75": 0.25,
    "advisor_strategy": 0.875,
    "advisor_advice": 0.25,
    "advisor_tips": 0.6,
}
PRESSED_ADOPTED = {
    "button_A": "hold",
    "button_B": "tap",
    "button_UP": "hold",
    "button_DOWN": "rejected",
    "stick_LS_x": "right",
    "advisor_strategy": "requested",
}
FIRST_FILES = {
    "controls": CONTROLS_NOTE,
    "tips": EMPTY,
    "strategy": STRATEGY_NOTE_FIRST,
    "advice": EMPTY,
}
HISTORY_HEADING = "## 直近の応答と送った操作 (古い順、最大 5 回)"


def values_of(names_to_values: dict, default: object) -> list:
    """問いの順 (QUESTION_NAMES) に、names_to_values の値 (無ければ default) を並べたリストを返す。"""
    return [names_to_values.get(name, default) for name in QUESTION_NAMES]


# TestClient の要求の宛先。既定の testserver は ALLOWED_HOSTS に無く 400 になるため、待ち受けるアドレスにする
BASE_URL = "http://127.0.0.1:8765"


def make_client(runs_dir: Path, web_dist_dir: Path | None) -> TestClient:
    """create_app(runs_dir, web_dist_dir) のアプリに BASE_URL で要求を送る TestClient を返す。"""
    return TestClient(create_app(runs_dir, web_dist_dir), base_url=BASE_URL)


@pytest.fixture
def client(runs) -> TestClient:
    """6 つの合成の実行を読むアプリ (画面のビルドの出力なし) の TestClient。"""
    return make_client(runs.runs_dir, None)


def get_json(client: TestClient, url: str):
    response = client.get(url)
    assert response.status_code == 200, response.text
    return response.json()


# --- GET /api/runs ---


def test_run_list_is_newest_first_with_recorded_flags(client):
    items = get_json(client, "/api/runs")
    assert [item["run"] for item in items] == RUN_NAMES_NEWEST_FIRST
    by_name = {item["run"]: item for item in items}
    assert by_name["20261113-235950"] == {
        "run": "20261113-235950",
        "game": "smb",
        "model": "jev",
        "started": "23:59:50.000",
        "recorded": {"dashboard": True, "requests": True},
        "in_progress": False,
    }
    assert by_name["20261112-100000"] == {
        "run": "20261112-100000",
        "game": "smb",
        "model": "decisions",
        "started": "10:00:00.000",
        "recorded": {"dashboard": False, "requests": False},
        "in_progress": False,
    }
    assert by_name["20261112-110000"]["recorded"] == {
        "dashboard": False,
        "requests": True,
    }
    assert by_name["20261113-120000"]["recorded"] == {
        "dashboard": True,
        "requests": True,
    }
    for old in ("20261111-090000", "20261110-090000"):
        assert by_name[old]["recorded"] == {"dashboard": False, "requests": True}
        assert by_name[old]["started"] == "09:00:00.000"
    assert all(item["in_progress"] is False for item in items)


# --- GET /api/runs/{run} (summary) ---


def test_summary_of_dashboard_run(client, runs):
    run = runs.dashboard
    summary = get_json(client, f"/api/runs/{run.name}")
    assert summary["run"] == run.name
    assert (summary["game"], summary["model"], summary["started"]) == (
        "smb",
        "jev",
        "23:59:50.000",
    )
    assert summary["args"] == run.args
    assert summary["thresholds"] == {"advisor": 0.6}
    assert summary["thresholds_assumed"] is False
    assert summary["valley_ratio"] == 0.25
    assert summary["similarity_threshold"] == 0.5
    assert [question["name"] for question in summary["questions"]] == QUESTION_NAMES
    assert summary["questions"][0] == BUTTON_A_QUESTION
    assert summary["old_format"] is False
    assert summary["t_end"] == pytest.approx(15.0)
    assert summary["counts"] == {"requests": 6, "frames": 7, "pad": 5}
    # decision の行の decide_s は 0.125、0.25、0.125、0.25
    assert summary["decide_s_median"] == pytest.approx(0.1875)
    assert summary["advisor_runs"] == [
        {
            "name": "advisor-controls-001",
            "role": "controls",
            "request": 3,
            "t_start": 3.5,
            "t_end": None,
            "returncode": None,
            "denials": None,
            "result": None,
        },
        {
            "name": "advisor-strategy-001",
            "role": "strategy",
            "request": 1,
            "t_start": 1.25,
            "t_end": 5.25,
            "returncode": 0,
            "denials": 1,
            "result": "戦略を更新した\n次は穴を越える",
        },
    ]
    # strategy の版 1 は、実行の終了 (5.25 秒) から 1 秒を超えた後 (6.5 秒) に送った要求で変わったが、
    # その直前の要求 (3.0 秒) は終了より前に送ったので、その実行の書き換え
    assert summary["note_versions"] == [
        {"version": 0, "t": 1.0, "request": 1, "initial": True, "changed": []},
        {
            "version": 1,
            "t": 6.5,
            "request": 4,
            "initial": False,
            "changed": [{"kind": "strategy", "by": "advisor-strategy-001"}],
        },
        {
            "version": 2,
            "t": 12.375,
            "request": 5,
            "initial": False,
            "changed": [{"kind": "tips", "by": "external"}],
        },
    ]
    assert summary["recorded"] == {
        "dashboard": True,
        "requests": True,
        "advisor_steps": True,
    }
    assert summary["dropped"] == {"frames": 1, "pad": 0}
    assert summary["skipped_lines"] == NO_SKIPPED_LINES


def test_summary_of_run_without_dashboard_records(client, runs):
    run = runs.no_dashboard
    summary = get_json(client, f"/api/runs/{run.name}")
    assert summary["args"] is None
    assert summary["thresholds"] == {"advisor": 0.5}
    assert summary["thresholds_assumed"] is True
    assert summary["valley_ratio"] == 0.5
    assert summary["similarity_threshold"] == 0.15
    # requests.jsonl が無いので、問いは第4章の build_questions() の一覧
    assert [question["name"] for question in summary["questions"]] == QUESTION_NAMES
    assert summary["questions"][0] == BUTTON_A_QUESTION
    assert summary["old_format"] is False
    assert summary["t_end"] == pytest.approx(21.0)
    assert summary["counts"] == {"requests": 0, "frames": 0, "pad": 0}
    assert summary["decide_s_median"] == pytest.approx(0.5)
    # run.jsonl の decision の行で要求の番号は分かるが、requests.jsonl が無いので、run.jsonl の advisor の行
    # (20.0 秒) と所要時間 (6.5 秒) から時刻を求める
    assert summary["advisor_runs"] == [
        {
            "name": "advisor-tips-001",
            "role": "tips",
            "request": 3,
            "t_start": 13.5,
            "t_end": 20.0,
            "returncode": 0,
            "denials": 0,
            "result": "Tips を更新した",
        }
    ]
    assert summary["note_versions"] == []
    assert summary["recorded"] == {
        "dashboard": False,
        "requests": False,
        "advisor_steps": False,
    }
    assert summary["dropped"] is None
    assert summary["skipped_lines"] == NO_SKIPPED_LINES


def test_summary_of_run_with_save_requests_only(client, runs):
    summary = get_json(client, f"/api/runs/{runs.save_requests.name}")
    assert summary["thresholds_assumed"] is True
    assert summary["recorded"] == {
        "dashboard": False,
        "requests": True,
        "advisor_steps": False,
    }
    assert summary["counts"] == {"requests": 2, "frames": 0, "pad": 0}
    assert summary["t_end"] == pytest.approx(2.5)
    assert summary["decide_s_median"] == pytest.approx(0.25)
    assert summary["advisor_runs"] == []
    assert summary["note_versions"] == [
        {"version": 0, "t": 1.0, "request": 1, "initial": True, "changed": []}
    ]
    assert summary["dropped"] is None


def test_summary_of_run_with_broken_lines(client, runs):
    summary = get_json(client, f"/api/runs/{runs.broken.name}")
    assert summary["skipped_lines"] == {
        "run": 1,
        "requests": 1,
        "frames": 1,
        "pad": 1,
        "advisor_steps": 1,
    }
    assert summary["counts"] == {"requests": 6, "frames": 7, "pad": 5}
    assert summary["t_end"] == pytest.approx(15.0)
    assert summary["decide_s_median"] == pytest.approx(0.1875)


@pytest.mark.parametrize("attribute", ["no_answer_type", "single_advisor_question"])
def test_summary_of_old_format_runs(client, runs, attribute):
    summary = get_json(client, f"/api/runs/{getattr(runs, attribute).name}")
    assert summary["old_format"] is True
    assert summary["questions"] == []
    assert summary["advisor_runs"] == []
    assert summary["note_versions"] == []
    # run.jsonl から求めるものは返す (decision の行の decide_s は 0.5 と 1.0)
    assert summary["decide_s_median"] == pytest.approx(0.75)
    assert summary["counts"] == {"requests": 2, "frames": 0, "pad": 0}
    assert summary["recorded"] == {
        "dashboard": False,
        "requests": True,
        "advisor_steps": False,
    }


# --- GET /api/runs/{run}/requests ---


def test_requests_of_dashboard_run(client, runs):
    run = runs.dashboard
    body = get_json(client, f"/api/runs/{run.name}/requests")
    assert body["available"] is True
    items = body["items"]
    assert [item["n"] for item in items] == [1, 2, 3, 4, 5, 6]
    assert items[0] == {
        "n": 1,
        "t_sent": 1.0,
        "t_received": 1.25,
        "result": "ok",
        "action": "押下 A+UP+LS:0.4375,0 / 短押し B",
        "advisor_requested": ["strategy"],
        "advisor_accepted": ["strategy"],
        "p": pytest.approx(values_of(PRESSED_VALUES, 0.0)),
        "adopted_count": 5,
    }
    # 応答が無い要求は、問いの値と操作に採った数が null、advisor の役割は空のリスト
    assert items[1] == {
        "n": 2,
        "t_sent": 2.0,
        "t_received": None,
        "result": "error",
        "action": None,
        "advisor_requested": [],
        "advisor_accepted": [],
        "p": None,
        "adopted_count": None,
    }
    assert [item["adopted_count"] for item in items] == [5, None, 2, 2, 0, 1]
    assert items[2]["advisor_accepted"] == ["controls"]
    assert items[4]["p"] == values_of({}, 0.0)
    # 応答を捨てた要求は、操作を決めていないが応答の値はある
    assert items[5]["result"] == "discarded"
    assert items[5]["action"] is None
    assert items[5]["advisor_requested"] == []


@pytest.mark.parametrize(
    "query, numbers",
    [
        ("?from=2&to=6.5", [2, 3, 4]),
        ("?from=2&to=6", [2, 3]),
        ("?from=12.375", [5, 6]),
        ("?to=1", [1]),
        ("?from=1&to=1", [1]),
        ("?from=20", []),
        ("?from=-5&to=0.5", []),
    ],
)
def test_requests_in_range_of_sent_time(client, runs, query, numbers):
    body = get_json(client, f"/api/runs/{runs.dashboard.name}/requests{query}")
    assert [item["n"] for item in body["items"]] == numbers


def test_requests_of_run_with_save_requests_only(client, runs):
    body = get_json(client, f"/api/runs/{runs.save_requests.name}/requests")
    assert body["available"] is True
    assert [
        (item["n"], item["t_sent"], item["action"], item["adopted_count"])
        for item in body["items"]
    ] == [
        (1, 1.0, "押下 A / 短押し none", 1),
        (2, 2.0, "押下 none / 短押し B", 1),
    ]


# --- GET /api/runs/{run}/requests/{n} ---


def test_request_detail_of_pressed_request(client, runs):
    run = runs.dashboard
    detail = get_json(client, f"/api/runs/{run.name}/requests/1")
    assert detail["n"] == 1
    assert detail["t_sent"] == 1.0
    assert detail["t_received"] == 1.25
    assert detail["result"] == "ok"
    assert detail["action"] == "押下 A+UP+LS:0.4375,0 / 短押し B"
    assert detail["advisor_requested"] == ["strategy"]
    assert detail["advisor_accepted"] == ["strategy"]
    assert detail["p"] == pytest.approx(values_of(PRESSED_VALUES, 0.0))
    assert detail["adopted_count"] == 5
    assert detail["images"] == [
        {
            "label": "最新 (t=1.00 秒、0.00 秒前)",
            "t": 1.0,
            "bytes": len(JPEG),
            "thumb": "thumbs/1001000000.jpg",
            "file": None,
        }
    ]
    assert detail["timings"] == {
        "select_s": 0.0,
        "encode_s": 0.125,
        "decide_s": 0.125,
        "apply_s": 0.03125,
    }
    assert detail["payload_bytes"] == len(JPEG)
    assert detail["history_text"] == f"{HISTORY_HEADING}\n(まだ操作を送っていない)"
    assert detail["previous_operation_text"] is None
    assert detail["note_version"] == 0
    assert len(detail["probabilities"]) == 16
    assert detail["probabilities"]["advisor_tips"] == 0.6
    assert detail["probabilities"]["stick_LS_x_at_least_50"] == 0.5
    assert len(detail["choices"]) == 21
    assert detail["choices"]["button_DOWN"] == {"none": 0.25, "hold": 0.25, "tap": 0.5}
    assert detail["advisor_probabilities"] == {
        "controls": 0.0,
        "strategy": 0.875,
        "advice": 0.25,
        "tips": 0.6,
    }
    expected_adopted = {name: None for name in QUESTION_NAMES}
    expected_adopted.update(PRESSED_ADOPTED)
    assert detail["adopted"] == expected_adopted


def test_request_detail_without_response(client, runs):
    detail = get_json(client, f"/api/runs/{runs.dashboard.name}/requests/2")
    assert detail["result"] == "error"
    assert detail["p"] is None
    assert detail["adopted_count"] is None
    assert detail["adopted"] is None
    assert detail["probabilities"] is None
    assert detail["choices"] is None
    assert detail["advisor_probabilities"] is None
    # decision の行が無いので、所要時間と画像のバイト数は分からない
    assert detail["timings"] == {
        "select_s": None,
        "encode_s": None,
        "decide_s": None,
        "apply_s": None,
    }
    assert detail["payload_bytes"] is None
    assert detail["images"][0]["thumb"] == "thumbs/1001250000.jpg"
    assert detail["previous_operation_text"] == (
        "── t=1.25 秒 (0.75 秒前) に、前回の応答の操作を送った ──"
    )
    assert detail["note_version"] == 0


def test_request_detail_note_versions_and_adopted_of_later_requests(client, runs):
    name = runs.dashboard.name
    strategy_changed = get_json(client, f"/api/runs/{name}/requests/4")
    assert strategy_changed["note_version"] == 1
    assert strategy_changed["adopted"]["button_RIGHT"] == "hold"
    assert strategy_changed["adopted"]["stick_RS_y"] == "down"
    assert strategy_changed["timings"]["decide_s"] == 0.125
    assert get_json(client, f"/api/runs/{name}/requests/3")["note_version"] == 0
    assert get_json(client, f"/api/runs/{name}/requests/5")["note_version"] == 2
    discarded = get_json(client, f"/api/runs/{name}/requests/6")
    assert discarded["note_version"] == 2
    assert discarded["adopted"]["button_A"] == "hold"
    assert discarded["adopted_count"] == 1


def test_request_detail_of_run_with_save_requests_only(client, runs):
    run = runs.save_requests
    detail = get_json(client, f"/api/runs/{run.name}/requests/1")
    assert detail["images"] == [
        {
            "label": "最新 (t=0.75 秒、0.25 秒前)",
            "t": 0.75,
            "bytes": len(JPEG),
            "thumb": None,
            "file": "frames/000000750000.jpg",
        }
    ]
    assert detail["timings"] == {
        "select_s": 0.0,
        "encode_s": 0.25,
        "decide_s": 0.25,
        "apply_s": 0.03125,
    }
    assert detail["note_version"] == 0
    assert detail["adopted"]["button_A"] == "hold"
    assert detail["adopted_count"] == 1


def test_request_line_with_values_of_other_types_does_not_break_api(runs):
    # number が list で、答えの値が文字列、選択肢の問いの答えが object でない要求の行を足す
    run = runs.dashboard
    line = {
        "event": "request",
        "number": [7],
        "t_sent": 14.5,
        "t_received": 14.75,
        "result": "ok",
        "state_text": "分けられない文",
        "probabilities": {"advisor_tips": "0.75"},
        "choices": {"button_A": [0.0, 1.0, 0.0]},
        "advisor_requested": None,
        "advisor_accepted": None,
    }
    with (run.run_dir / "requests.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(line, ensure_ascii=False) + "\n")
    client = make_client(runs.runs_dir, None)

    items = get_json(client, f"/api/runs/{run.name}/requests")["items"]
    assert [item["n"] for item in items] == [1, 2, 3, 4, 5, 6, [7]]
    assert items[-1]["p"] == values_of({}, None)
    assert items[-1]["adopted_count"] is None

    summary = get_json(client, f"/api/runs/{run.name}")
    assert summary["counts"]["requests"] == 7
    assert summary["note_versions"][-1]["request"] == [7]
    assert summary["note_versions"][-1]["changed"] == []
    # number が list の要求の行で始まった版は、ほかの要求の時点の版にしない
    assert get_json(client, f"/api/runs/{run.name}/requests/6")["note_version"] == 2


# --- GET /api/runs/{run}/frames ---

DASHBOARD_FRAME_ITEMS = [
    {
        "t": 0.5,
        "sharpness": 100.0,
        "diff": None,
        "phase": "rising",
        "peak_t": 0.5,
        "peak_sharpness": 100.0,
        "ref": 25.0,
        "event": None,
        "thumb": "thumbs/1000500000.jpg",
    },
    {
        "t": 0.75,
        "sharpness": 200.0,
        "diff": 0.0,
        "phase": "rising",
        "peak_t": 0.75,
        "peak_sharpness": 200.0,
        "ref": 50.0,
        "event": None,
        "thumb": "thumbs/1000750000.jpg",
    },
    {
        "t": 1.0,
        "sharpness": 40.0,
        "diff": 30.0,
        "phase": "falling",
        "peak_t": 0.75,
        "peak_sharpness": 200.0,
        "ref": 160.0,
        "event": {"kind": "fall"},
        "thumb": "thumbs/1001000000.jpg",
    },
    {
        "t": 1.25,
        "sharpness": 180.0,
        "diff": 0.0,
        "phase": "rising",
        "peak_t": 1.25,
        "peak_sharpness": 180.0,
        "ref": 45.0,
        # 谷を確定した出来事の候補の時刻も経過秒にする
        "event": {
            "kind": "close",
            "candidate_t": 0.75,
            "candidate_sharpness": 200.0,
            "kept": True,
            "similar_diff": None,
        },
        "thumb": "thumbs/1001250000.jpg",
    },
    {
        "t": 5.5,
        "sharpness": 190.0,
        "diff": 40.0,
        "phase": "rising",
        "peak_t": 5.5,
        "peak_sharpness": 190.0,
        "ref": 47.5,
        "event": None,
        "thumb": "thumbs/1005500000.jpg",
    },
    {
        "t": 12.25,
        "sharpness": 200.0,
        "diff": 0.0,
        "phase": "rising",
        "peak_t": 12.25,
        "peak_sharpness": 200.0,
        "ref": 50.0,
        "event": None,
        "thumb": "thumbs/1012250000.jpg",
    },
    {
        "t": 13.75,
        "sharpness": 210.0,
        "diff": 40.0,
        "phase": "rising",
        "peak_t": 13.75,
        "peak_sharpness": 210.0,
        "ref": 52.5,
        "event": None,
        "thumb": "thumbs/1013750000.jpg",
    },
]


def test_frames_of_dashboard_run(client, runs):
    body = get_json(client, f"/api/runs/{runs.dashboard.name}/frames")
    assert body == {"available": True, "items": DASHBOARD_FRAME_ITEMS}


def test_frames_in_range(client, runs):
    body = get_json(client, f"/api/runs/{runs.dashboard.name}/frames?from=1&to=5.5")
    assert [item["t"] for item in body["items"]] == [1.0, 1.25, 5.5]


def test_frames_of_run_with_broken_lines_skip_unreadable_lines(client, runs):
    body = get_json(client, f"/api/runs/{runs.broken.name}/frames")
    assert body == {"available": True, "items": DASHBOARD_FRAME_ITEMS}


# --- GET /api/runs/{run}/pad ---


def test_pad_of_dashboard_run(client, runs):
    body = get_json(client, f"/api/runs/{runs.dashboard.name}/pad")
    assert body == {
        "available": True,
        "items": [
            {
                "t": 1.3125,
                "source": "apply",
                "state": "A+B+UP+LS:0.4375,0",
                "ok": True,
                "error": None,
            },
            {
                "t": 1.4375,
                "source": "tap_release",
                "state": "A+UP+LS:0.4375,0",
                "ok": True,
                "error": None,
            },
            {
                "t": 2.125,
                "source": "neutral",
                "state": "none",
                "ok": False,
                "error": "pipe closed",
            },
            {
                "t": 6.8125,
                "source": "apply",
                "state": "RIGHT+RS:0,-1",
                "ok": True,
                "error": None,
            },
            {
                "t": 12.8125,
                "source": "apply",
                "state": "none",
                "ok": True,
                "error": None,
            },
        ],
    }


def test_pad_in_range(client, runs):
    body = get_json(client, f"/api/runs/{runs.dashboard.name}/pad?from=1.4&to=7")
    assert [item["t"] for item in body["items"]] == [1.4375, 2.125, 6.8125]


# --- GET /api/runs/{run}/events ---


def test_events_of_dashboard_run(client, runs):
    run = runs.dashboard
    body = get_json(client, f"/api/runs/{run.name}/events")
    assert body["available"] is True
    items = body["items"]
    # decision の行は返さない
    assert [item["event"] for item in items] == [
        "record_error",
        "start",
        "record_error",
        "decision_error",
        "advisor",
        "record_error",
        "capture_stopped",
        "record_end",
    ]
    # start の行より前の行は負の秒。start の行より後でも時刻がわずかに前の行は負の秒のまま。
    # 00:00 台の行は日付をまたいだ経過秒
    assert [item["t"] for item in items] == pytest.approx(
        [-0.5, 0.0, -0.003, 2.125, 5.25, 7.0, 14.5, 15.0]
    )
    # 壁時計の time は返さない
    assert all("time" not in item for item in items)
    assert items[0] == {
        "t": -0.5,
        "event": "record_error",
        "kind": "pad",
        "error": "No space left on device",
    }
    assert items[1] == {
        "t": 0.0,
        "event": "start",
        "game": "smb",
        "model": "jev",
        "run_dir": str(run.run_dir),
        "args": run.args,
        "origin_mono": 1000.0,
    }
    assert items[2] == {
        "t": pytest.approx(-0.003),
        "event": "record_error",
        "kind": "frames",
        "error": "No space left on device",
    }
    assert items[3] == {"t": 2.125, "event": "decision_error", "error": "HTTP 500"}
    assert items[4]["role"] == "strategy"
    assert items[4]["duration_s"] == 4.0
    assert items[5] == {
        "t": 7.0,
        "event": "record_error",
        "kind": "advisor_steps",
        "run_dir": str(run.run_dir / "advisor-controls-001"),
        "error": "No space left on device",
    }
    assert items[7] == {
        "t": 15.0,
        "event": "record_end",
        "written": {"frames": 7, "pad": 5},
        "dropped": {"frames": 1, "pad": 0},
    }


@pytest.mark.parametrize(
    "query, events",
    [
        ("?from=-1&to=0", ["record_error", "start", "record_error"]),
        ("?to=-0.5", ["record_error"]),
        ("?from=0&to=3", ["start", "decision_error"]),
        ("?from=12&to=15", ["capture_stopped", "record_end"]),
    ],
)
def test_events_in_range(client, runs, query, events):
    body = get_json(client, f"/api/runs/{runs.dashboard.name}/events{query}")
    assert [item["event"] for item in body["items"]] == events


def test_events_of_run_without_dashboard_records(client, runs):
    body = get_json(client, f"/api/runs/{runs.no_dashboard.name}/events")
    assert body["available"] is True
    assert [(item["t"], item["event"]) for item in body["items"]] == [
        (0.0, "start"),
        (20.0, "advisor"),
        (21.0, "interrupted"),
    ]


def test_events_without_start_line_are_unavailable(runs):
    run_dir = runs.runs_dir / "20261101-000000"
    run_dir.mkdir()
    (run_dir / "run.jsonl").write_text(
        '{"time": "10:00:00.000", "event": "bridge_wait"}\n', encoding="utf-8"
    )
    client = make_client(runs.runs_dir, None)
    body = get_json(client, "/api/runs/20261101-000000/events")
    assert body == {"available": False, "items": []}


# --- 記録の無い種類 ---


def test_kinds_without_records_are_unavailable(client, runs):
    unavailable = {"available": False, "items": []}
    no_dashboard = runs.no_dashboard.name
    assert get_json(client, f"/api/runs/{no_dashboard}/requests") == unavailable
    assert get_json(client, f"/api/runs/{no_dashboard}/frames") == unavailable
    assert get_json(client, f"/api/runs/{no_dashboard}/pad") == unavailable
    # --save-requests だけの実行は、要求の行はあるが、フレームの判定と送ったレポートの記録が無い
    save_requests = runs.save_requests.name
    assert get_json(client, f"/api/runs/{save_requests}/frames") == unavailable
    assert get_json(client, f"/api/runs/{save_requests}/pad") == unavailable


# --- GET /api/runs/{run}/notes/{version} ---


def test_notes_of_each_version(client, runs):
    name = runs.dashboard.name
    assert get_json(client, f"/api/runs/{name}/notes/0") == {
        "version": 0,
        "t": 1.0,
        "changed": [],
        "files": FIRST_FILES,
    }
    assert get_json(client, f"/api/runs/{name}/notes/1") == {
        "version": 1,
        "t": 6.5,
        "changed": [{"kind": "strategy", "by": "advisor-strategy-001"}],
        "files": {**FIRST_FILES, "strategy": STRATEGY_NOTE_SECOND},
    }
    assert get_json(client, f"/api/runs/{name}/notes/2") == {
        "version": 2,
        "t": 12.375,
        "changed": [{"kind": "tips", "by": "external"}],
        "files": {**FIRST_FILES, "strategy": STRATEGY_NOTE_SECOND, "tips": TIPS_NOTE},
    }


# --- GET /api/runs/{run}/advisor/{name} ---

STRATEGY_STEPS = [
    {
        "t": 2.0,
        "kind": "tool_use",
        "tool": "Read",
        "summary": "game_notes/smb/strategy.md",
    },
    {"t": 2.25, "kind": "tool_result", "tool": "Read", "summary": "## 目標"},
    {"t": 3.0, "kind": "tool_use", "tool": "Bash", "summary": "ls game_notes"},
    {
        "t": 3.25,
        "kind": "permission_denied",
        "tool": "Bash",
        "summary": "Permission to use Bash has been denied.",
    },
    {"t": 4.0, "kind": "tool_use", "tool": "Skill", "summary": "record-strategy"},
    {
        "t": 4.25,
        "kind": "tool_result",
        "tool": "Skill",
        "summary": "Launching skill: record-strategy",
    },
    {"t": 5.25, "kind": "result", "tool": None, "summary": "戦略を更新した"},
]


def expected_files(advisor_dir: Path, names: list[str]) -> list[dict]:
    return [
        {
            "path": f"{advisor_dir.name}/{name}",
            "bytes": (advisor_dir / name).stat().st_size,
        }
        for name in names
    ]


def test_advisor_of_done_run_with_steps(client, runs):
    run = runs.dashboard
    advisor_dir = run.run_dir / "advisor-strategy-001"
    body = get_json(client, f"/api/runs/{run.name}/advisor/advisor-strategy-001")
    assert body == {
        "name": "advisor-strategy-001",
        "role": "strategy",
        "request": 1,
        "t_start": 1.25,
        "t_end": 5.25,
        "status": "done",
        "result_json": json.loads(
            (advisor_dir / "result.json").read_text(encoding="utf-8")
        ),
        "steps_available": True,
        "steps": STRATEGY_STEPS,
        "files": expected_files(
            advisor_dir, ["context.md", "frame-01.jpg", "result.json", "stream.jsonl"]
        ),
    }
    assert body["result_json"]["permission_denials"][0]["tool_use_id"] == "toolu_02"


def test_advisor_of_running_run(client, runs):
    run = runs.dashboard
    advisor_dir = run.run_dir / "advisor-controls-001"
    body = get_json(client, f"/api/runs/{run.name}/advisor/advisor-controls-001")
    assert body == {
        "name": "advisor-controls-001",
        "role": "controls",
        "request": 3,
        "t_start": 3.5,
        "t_end": None,
        "status": "running",
        "result_json": None,
        "steps_available": True,
        # result の行も result.json も無いので、エラーの結果も tool_result のまま
        "steps": [
            {
                "t": 4.0,
                "kind": "tool_use",
                "tool": "Read",
                "summary": "game_notes/smb/controls.md",
            },
            {
                "t": 4.5,
                "kind": "tool_result",
                "tool": "Read",
                "summary": "- A: ジャンプ",
            },
            {"t": 5.0, "kind": "tool_use", "tool": "Bash", "summary": "cat README.md"},
            {
                "t": 5.25,
                "kind": "tool_result",
                "tool": "Bash",
                "summary": "Permission to use Bash has been denied.",
            },
        ],
        "files": expected_files(
            advisor_dir, ["context.md", "frame-01.jpg", "stream.jsonl"]
        ),
    }


def test_advisor_without_stream(client, runs):
    run = runs.no_dashboard
    advisor_dir = run.run_dir / run.tips_run
    body = get_json(client, f"/api/runs/{run.name}/advisor/{run.tips_run}")
    assert body["status"] == "done"
    # 要求の番号は run.jsonl の decision の行から、時刻は run.jsonl の advisor の行から求める
    assert (body["request"], body["t_start"], body["t_end"]) == (
        run.tips_request,
        13.5,
        20.0,
    )
    assert run.tips_request == 3
    assert body["steps_available"] is False
    assert body["steps"] == []
    assert body["files"] == expected_files(
        advisor_dir, ["context.md", "frame-01.jpg", "result.json"]
    )


def test_advisor_steps_of_run_with_broken_lines(client, runs):
    body = get_json(
        client, f"/api/runs/{runs.broken.name}/advisor/advisor-strategy-001"
    )
    assert body["steps"] == STRATEGY_STEPS


# --- GET /api/runs/{run}/files/{path} ---


def test_files_returns_thumb_jpeg(client, runs):
    response = client.get(
        f"/api/runs/{runs.dashboard.name}/files/thumbs/1000750000.jpg"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG
    assert int(response.headers["content-length"]) == len(JPEG)


def assert_file_body(response, path: Path) -> None:
    """応答の本文が path のファイルのバイト列と同じで、content-length がその長さであることを確かめる。"""
    content = path.read_bytes()
    assert response.content == content
    assert int(response.headers["content-length"]) == len(content)


def test_files_returns_text_of_advisor_dir(client, runs):
    name = runs.dashboard.name
    advisor_dir = runs.dashboard.run_dir / "advisor-strategy-001"
    context = client.get(f"/api/runs/{name}/files/advisor-strategy-001/context.md")
    assert context.status_code == 200
    assert context.headers["content-type"] == "text/markdown; charset=utf-8"
    assert context.text.startswith("# advisor への文脈")
    assert_file_body(context, advisor_dir / "context.md")
    result = client.get(f"/api/runs/{name}/files/advisor-strategy-001/result.json")
    assert result.status_code == 200
    assert result.headers["content-type"] == "application/json"
    assert result.json()["duration_s"] == 4.0
    assert_file_body(result, advisor_dir / "result.json")
    stream = client.get(f"/api/runs/{name}/files/advisor-strategy-001/stream.jsonl")
    assert stream.status_code == 200
    assert stream.headers["content-type"] == "text/plain; charset=utf-8"
    assert_file_body(stream, advisor_dir / "stream.jsonl")


def test_files_returns_request_image_and_old_advisor_dir(client, runs):
    save_requests = runs.save_requests
    response = client.get(
        f"/api/runs/{save_requests.name}/files/{save_requests.request_files[1]}"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert_file_body(response, save_requests.run_dir / save_requests.request_files[1])
    old = client.get(
        f"/api/runs/{runs.no_answer_type.name}/files/advisor-001/result.json"
    )
    assert old.status_code == 200
    assert old.json()["returncode"] == 0
    assert_file_body(old, runs.no_answer_type.run_dir / "advisor-001" / "result.json")


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root はモードを 000 にしたファイルも読めるため"
)
def test_files_unreadable_file_is_404(client, runs):
    (runs.dashboard.run_dir / "thumbs" / "1000750000.jpg").chmod(0)
    response = client.get(
        f"/api/runs/{runs.dashboard.name}/files/thumbs/1000750000.jpg"
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        # %2e%2e はサーバーで .. に戻る
        "%2e%2e/20261112-100000/run.jsonl",
        "thumbs/%2e%2e/run.jsonl",
        "thumbs/%2E%2E/%2E%2E/20261112-100000/run.jsonl",
        # 許していない最初の部分
        "run.jsonl",
        "requests.jsonl",
        "game_notes/smb/strategy.md",
        # ディレクトリーそのもの、無いファイル
        "thumbs",
        "advisor-strategy-001",
        "thumbs/missing.jpg",
    ],
)
def test_files_rejects_paths_not_allowed(client, runs, path):
    response = client.get(f"/api/runs/{runs.dashboard.name}/files/{path}")
    assert response.status_code == 404


def test_files_rejects_symlink_to_outside(runs, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (runs.dashboard.run_dir / "thumbs" / "link.jpg").symlink_to(secret)
    client = make_client(runs.runs_dir, None)
    response = client.get(f"/api/runs/{runs.dashboard.name}/files/thumbs/link.jpg")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path, expected",
    [
        (Path("thumbs/1.jpg"), "image/jpeg"),
        (Path("frames/1.JPEG"), "image/jpeg"),
        (Path("advisor-tips-001/context.md"), "text/markdown; charset=utf-8"),
        (Path("advisor-tips-001/result.json"), "application/json"),
        (Path("advisor-tips-001/stream.jsonl"), "text/plain; charset=utf-8"),
        (Path("advisor-tips-001/notes.txt"), "application/octet-stream"),
        (Path("advisor-tips-001/README"), "application/octet-stream"),
    ],
)
def test_file_media_type(path, expected):
    assert file_media_type(path) == expected


# --- 古い形の実行 ---


@pytest.mark.parametrize("attribute", ["no_answer_type", "single_advisor_question"])
def test_old_format_runs_do_not_return_question_values_advisor_runs_or_notes(
    client, runs, attribute
):
    run = getattr(runs, attribute)
    assert get_json(client, f"/api/runs/{run.name}/requests") == {
        "available": False,
        "reason": "old_format",
        "items": [],
    }
    assert get_json(client, f"/api/runs/{run.name}/requests/1") == {
        "available": False,
        "reason": "old_format",
    }
    assert client.get(f"/api/runs/{run.name}/notes/0").status_code == 404
    assert client.get(f"/api/runs/{run.name}/advisor/advisor-001").status_code == 404
    # run.jsonl の出来事は返す
    events = get_json(client, f"/api/runs/{run.name}/events")
    assert [(item["t"], item["event"]) for item in events["items"]] == [
        (0.0, "start"),
        (5.0, "advisor"),
    ]


# --- 404 と 422 ---


@pytest.mark.parametrize(
    "url",
    [
        "/api/runs/20991231-000000",
        "/api/runs/%2e%2e",
        "/api/runs/20991231-000000/requests",
        "/api/runs/20991231-000000/requests/1",
        "/api/runs/20991231-000000/frames",
        "/api/runs/20991231-000000/pad",
        "/api/runs/20991231-000000/events",
        "/api/runs/20991231-000000/notes/0",
        "/api/runs/20991231-000000/advisor/advisor-strategy-001",
        "/api/runs/20991231-000000/files/thumbs/1000750000.jpg",
    ],
)
def test_unknown_run_is_404(client, url):
    assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    "suffix",
    [
        "requests/99",
        "requests/0",
        "notes/3",
        "advisor/advisor-tips-001",
        "advisor/advisor-strategy-002",
        "advisor/advisor-001",
    ],
)
def test_unknown_number_version_or_advisor_run_is_404(client, runs, suffix):
    assert client.get(f"/api/runs/{runs.dashboard.name}/{suffix}").status_code == 404


@pytest.mark.parametrize("kind", ["requests", "frames", "pad", "events"])
@pytest.mark.parametrize(
    "query",
    ["?from=5&to=1", "?from=abc", "?to=abc", "?from=nan", "?from=inf", "?to=-inf"],
)
def test_invalid_range_is_422(client, runs, kind, query):
    response = client.get(f"/api/runs/{runs.dashboard.name}/{kind}{query}")
    assert response.status_code == 422


# --- / (画面) ---


def test_root_without_web_dist_returns_build_instructions(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == BUILD_INSTRUCTIONS_TEXT
    assert "tools/run-dashboard/web" in response.text


def test_root_with_missing_web_dist_dir_returns_build_instructions(runs, tmp_path):
    client = make_client(runs.runs_dir, tmp_path / "missing-dist")
    response = client.get("/")
    assert response.status_code == 200
    assert response.text == BUILD_INSTRUCTIONS_TEXT


def test_root_with_web_dist_returns_index_html(runs, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    index = "<!doctype html><title>run-dashboard</title>"
    (dist / "index.html").write_text(index, encoding="utf-8")
    client = make_client(runs.runs_dir, dist)
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == index


def test_api_is_served_with_web_dist(runs, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    client = make_client(runs.runs_dir, dist)
    items = get_json(client, "/api/runs")
    assert [item["run"] for item in items] == RUN_NAMES_NEWEST_FIRST


def test_default_web_dist_dir_is_under_project():
    project_dir = Path(__file__).resolve().parents[1]
    assert DEFAULT_WEB_DIST_DIR == project_dir / "web" / "dist"


# --- Host の検査 ---


def test_allowed_hosts():
    assert ALLOWED_HOSTS == ("127.0.0.1", "localhost")


@pytest.mark.parametrize("url", ["/", "/api/runs"])
def test_request_with_other_host_is_400(client, url):
    response = client.get(url, headers={"host": "evil.example"})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["localhost:5173", "127.0.0.1:8765"])
def test_request_with_allowed_host_is_served(client, host):
    response = client.get("/", headers={"host": host})
    assert response.status_code == 200
    assert response.text == BUILD_INSTRUCTIONS_TEXT
