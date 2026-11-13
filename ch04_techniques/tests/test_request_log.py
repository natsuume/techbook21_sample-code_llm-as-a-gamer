import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from switch2_remote.controller_state import PadState

from switch2_agent.action_decision import PadAction
from switch2_agent.decision_models.base import Decision
from switch2_agent.decision_request import DecisionRequest, LabeledImage
from switch2_agent.operation_log import format_action
from switch2_agent.questions import Question
from switch2_agent.request_log import (
    FRAMES_DIR_NAME,
    REQUEST_LOG_FILE_NAME,
    RequestLog,
    RequestLogEntry,
    frame_file_name,
)

TIME_PATTERN = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}")
ORIGIN = 100.0
QUESTIONS = [
    Question("press_A", "今、A ボタンを押した状態にするべきか。", "press", "A"),
    Question("advisor", "今、advisor に戦略の更新を求めるべきか。", "advisor", ""),
]
HISTORY_TEXT = (
    "## 直近の応答と送った操作 (古い順、最大 5 回)\n"
    "- t=2.00 秒 (1.00 秒前): 押下 none / 短押し A (前回の操作)"
)
PREVIOUS_OPERATION_TEXT = "── t=2.00 秒 (1.00 秒前) に、前回の応答の操作を送った ──"
RAW_RESPONSE = {
    "model": "jev-1.14.0",
    "answers": {
        "press_A": {"type": "noul", "noul": 0.9},
        "advisor": {"type": "noul", "noul": 0.7},
    },
}


def sample_request() -> DecisionRequest:
    # 画像の経過秒は 1.5 秒 (101.5 - ORIGIN) と 2.25 秒 (102.25 - ORIGIN)
    return DecisionRequest(
        instructions="あなたはコントローラーの判断をする。",
        state_text="## 戦略\n右へ進む",
        images=[
            LabeledImage("過去の場面 1/1 (t=1.50 秒、1.50 秒前)", b"jpeg-one", 101.5),
            LabeledImage(
                "直近 1/1 (t=2.25 秒、0.75 秒前、前回の操作の後)",
                b"jpeg-two!",
                102.25,
                True,
            ),
        ],
        questions=QUESTIONS,
        history_text=HISTORY_TEXT,
        previous_operation_text=PREVIOUS_OPERATION_TEXT,
    )


def sample_action() -> PadAction:
    return PadAction(
        held=PadState(frozenset({"A"}), {}),
        taps=frozenset({"B"}),
        advisor_requested=True,
        advisor_probability=0.7,
    )


def sample_decision() -> Decision:
    return Decision(
        probabilities={"press_A": 0.9, "advisor": 0.7},
        refusals=1,
        raw_response=RAW_RESPONSE,
    )


def ok_entry(**overrides) -> RequestLogEntry:
    """操作を送った (result が ok の) 要求の記録を返す。overrides のフィールドだけを置き換える。"""
    entry = RequestLogEntry(
        number=3,
        origin=ORIGIN,
        sent_at=103.0,
        encoded_at=103.25,
        received_at=103.5,
        request=sample_request(),
        decision=sample_decision(),
        action=sample_action(),
        advisor_accepted=True,
        result="ok",
        error=None,
    )
    return replace(entry, **overrides)


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def request_lines(run_dir: Path) -> list[dict]:
    return [
        line
        for line in read_lines(run_dir / REQUEST_LOG_FILE_NAME)
        if line["event"] == "request"
    ]


# --- 定数とファイル名 ---


def test_constants():
    assert REQUEST_LOG_FILE_NAME == "requests.jsonl"
    assert FRAMES_DIR_NAME == "frames"


@pytest.mark.parametrize(
    "t, expected",
    [
        (0.0, "000000000000.jpg"),
        (1.5, "000001500000.jpg"),
        (128.1, "000128100000.jpg"),
        # マイクロ秒は切り捨てではなく丸める
        (0.0000016, "000000000002.jpg"),
        # 1 ミリ秒以内の 2 枚も別の名前になる
        (1.5004, "000001500400.jpg"),
    ],
)
def test_frame_file_name_is_microseconds_with_twelve_digits(t, expected):
    assert frame_file_name(t) == expected


# --- 作成と header ---


def test_init_creates_frames_dir(tmp_path):
    RequestLog(tmp_path)
    assert (tmp_path / FRAMES_DIR_NAME).is_dir()


def test_init_accepts_existing_frames_dir(tmp_path):
    (tmp_path / FRAMES_DIR_NAME).mkdir()
    RequestLog(tmp_path)
    assert (tmp_path / FRAMES_DIR_NAME).is_dir()


def test_write_header_writes_model_instructions_and_questions(tmp_path):
    log = RequestLog(tmp_path)
    log.write_header("jev", "あなたはコントローラーの判断をする。", QUESTIONS)
    lines = read_lines(tmp_path / REQUEST_LOG_FILE_NAME)
    assert len(lines) == 1
    header = lines[0]
    assert TIME_PATTERN.fullmatch(header.pop("time"))
    assert header == {
        "event": "header",
        "model": "jev",
        "instructions": "あなたはコントローラーの判断をする。",
        "questions": [
            {
                "name": "press_A",
                "text": "今、A ボタンを押した状態にするべきか。",
                "kind": "press",
            },
            {
                "name": "advisor",
                "text": "今、advisor に戦略の更新を求めるべきか。",
                "kind": "advisor",
            },
        ],
    }


def test_write_appends_to_existing_lines(tmp_path):
    (tmp_path / REQUEST_LOG_FILE_NAME).write_text(
        '{"event": "earlier"}\n', encoding="utf-8"
    )
    log = RequestLog(tmp_path)
    log.write_header("jev", "指示", QUESTIONS)
    log.write(ok_entry())
    events = [line["event"] for line in read_lines(tmp_path / REQUEST_LOG_FILE_NAME)]
    assert events == ["earlier", "header", "request"]


def test_each_line_is_readable_right_after_writing(tmp_path):
    # 書くたびに flush するので、閉じる前でも書いた行を読める
    log = RequestLog(tmp_path)
    log.write_header("jev", "指示", QUESTIONS)
    assert len(read_lines(tmp_path / REQUEST_LOG_FILE_NAME)) == 1
    log.write(ok_entry())
    assert len(read_lines(tmp_path / REQUEST_LOG_FILE_NAME)) == 2


# --- request の行 ---


def test_write_ok_entry_writes_request_line(tmp_path):
    log = RequestLog(tmp_path)
    entry = ok_entry()
    log.write(entry)
    lines = request_lines(tmp_path)
    assert len(lines) == 1
    line = lines[0]
    assert TIME_PATTERN.fullmatch(line["time"])
    assert line["number"] == 3
    # t_sent は要求の時刻 (sent_at) の経過秒、decide_s は model.decide を呼ぶ直前 (encoded_at) から
    # 応答を受け取るまで (received_at) の時間で、選択と符号化の時間 (sent_at から encoded_at まで) を含まない
    assert line["t_sent"] == pytest.approx(3.0)
    assert line["t_received"] == pytest.approx(3.5)
    assert line["decide_s"] == pytest.approx(0.25)
    assert line["result"] == "ok"
    assert line["error"] is None
    assert line["state_text"] == "## 戦略\n右へ進む"
    assert line["history_text"] == HISTORY_TEXT
    assert line["previous_operation_text"] == PREVIOUS_OPERATION_TEXT
    assert line["probabilities"] == {"press_A": 0.9, "advisor": 0.7}
    assert line["refusals"] == 1
    assert line["raw_response"] == RAW_RESPONSE
    assert line["action"] == format_action(entry.action)
    assert line["advisor_requested"] is True
    assert line["advisor_probability"] == 0.7
    assert line["advisor_accepted"] is True


def test_write_lists_images_with_elapsed_time_and_file(tmp_path):
    log = RequestLog(tmp_path)
    log.write(ok_entry())
    images = request_lines(tmp_path)[0]["images"]
    assert len(images) == 2
    first, second = images
    assert first["label"] == "過去の場面 1/1 (t=1.50 秒、1.50 秒前)"
    assert first["t"] == pytest.approx(1.5)
    assert first["file"] == "frames/000001500000.jpg"
    assert first["bytes"] == len(b"jpeg-one")
    assert first["after_previous_operation"] is False
    assert second["label"] == "直近 1/1 (t=2.25 秒、0.75 秒前、前回の操作の後)"
    assert second["t"] == pytest.approx(2.25)
    assert second["file"] == "frames/000002250000.jpg"
    assert second["bytes"] == len(b"jpeg-two!")
    assert second["after_previous_operation"] is True


def test_write_saves_jpeg_of_each_image_under_frames(tmp_path):
    RequestLog(tmp_path).write(ok_entry())
    frames_dir = tmp_path / FRAMES_DIR_NAME
    assert sorted(path.name for path in frames_dir.iterdir()) == [
        "000001500000.jpg",
        "000002250000.jpg",
    ]
    assert (frames_dir / "000001500000.jpg").read_bytes() == b"jpeg-one"
    assert (frames_dir / "000002250000.jpg").read_bytes() == b"jpeg-two!"


def test_write_saves_jpeg_of_same_frame_only_once(tmp_path):
    log = RequestLog(tmp_path)
    log.write(ok_entry(number=1))
    # 1 回目に書いたファイルを書き換えておき、2 回目の write で書き直されないことを確かめる
    first_file = tmp_path / FRAMES_DIR_NAME / "000001500000.jpg"
    first_file.write_bytes(b"kept")
    request = replace(
        sample_request(),
        images=[
            sample_request().images[0],
            LabeledImage("直近 1/1 (t=3.00 秒、0.50 秒前)", b"jpeg-three", 103.0),
        ],
    )
    log.write(ok_entry(number=2, request=request))
    assert first_file.read_bytes() == b"kept"
    assert (
        tmp_path / FRAMES_DIR_NAME / "000003000000.jpg"
    ).read_bytes() == b"jpeg-three"
    # 行からは同じファイルを参照する
    lines = request_lines(tmp_path)
    assert lines[0]["images"][0]["file"] == "frames/000001500000.jpg"
    assert lines[1]["images"][0]["file"] == "frames/000001500000.jpg"


def test_write_error_entry_without_response(tmp_path):
    # model.decide が失敗した要求: 応答・操作・受け取った時刻が無い
    RequestLog(tmp_path).write(
        ok_entry(
            received_at=None,
            decision=None,
            action=None,
            advisor_accepted=False,
            result="error",
            error="HTTP 500",
        )
    )
    line = request_lines(tmp_path)[0]
    assert line["result"] == "error"
    assert line["error"] == "HTTP 500"
    assert line["t_sent"] == pytest.approx(3.0)
    assert line["t_received"] is None
    assert line["decide_s"] is None
    assert line["action"] is None
    for key in ("probabilities", "refusals", "raw_response"):
        assert line.get(key) is None
    assert line["advisor_accepted"] is False
    # 応答が無くても、送った画像と要求の文は残す
    assert [image["file"] for image in line["images"]] == [
        "frames/000001500000.jpg",
        "frames/000002250000.jpg",
    ]
    assert line["history_text"] == HISTORY_TEXT


def test_write_discarded_entry_with_decision_but_without_action(tmp_path):
    # 応答を待つ間にキャプチャーが止まった要求: 応答はあるが、操作は決めていない
    RequestLog(tmp_path).write(
        ok_entry(
            action=None,
            advisor_accepted=False,
            result="discarded",
            error="no new frame for 2.0 seconds",
        )
    )
    line = request_lines(tmp_path)[0]
    assert line["result"] == "discarded"
    assert line["error"] == "no new frame for 2.0 seconds"
    assert line["t_received"] == pytest.approx(3.5)
    assert line["probabilities"] == {"press_A": 0.9, "advisor": 0.7}
    assert line["raw_response"] == RAW_RESPONSE
    assert line["action"] is None


def test_write_send_error_entry_with_decision_and_action(tmp_path):
    entry = ok_entry(
        advisor_accepted=False,
        result="error",
        error="bridge did not acknowledge the report",
    )
    RequestLog(tmp_path).write(entry)
    line = request_lines(tmp_path)[0]
    assert line["result"] == "error"
    assert line["error"] == "bridge did not acknowledge the report"
    assert line["probabilities"] == {"press_A": 0.9, "advisor": 0.7}
    assert line["action"] == format_action(entry.action)
    assert line["advisor_accepted"] is False


def test_write_previous_operation_text_is_null_for_first_request(tmp_path):
    request = replace(sample_request(), previous_operation_text=None)
    RequestLog(tmp_path).write(ok_entry(request=request))
    assert request_lines(tmp_path)[0]["previous_operation_text"] is None


def test_write_keeps_non_ascii_text_readable(tmp_path):
    RequestLog(tmp_path).write(ok_entry())
    text = (tmp_path / REQUEST_LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "右へ進む" in text
    assert "前回の操作の後" in text
