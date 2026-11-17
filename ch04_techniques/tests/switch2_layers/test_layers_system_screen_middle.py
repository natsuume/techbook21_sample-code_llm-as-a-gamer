import base64
import dataclasses
import json

import httpx
import pytest

from switch2_layers.middle.luna_client import ERROR_BODY_HEAD_CHARS, LunaCallError
from switch2_layers.middle.luna_middle import CALL_DETAIL_KEYS
from switch2_layers.middle.middle_call_record import MiddleCallDetailsSource
from switch2_layers.middle.system_screen_instructions import (
    SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
    goal_text,
)
from switch2_layers.middle.system_screen_middle import (
    FAKE_DONE_OUTPUT,
    FakeSystemScreenMiddle,
    LunaSystemScreenMiddle,
    SystemScreenMiddleError,
)
from switch2_layers.middle.system_screen_types import (
    SystemCutReason,
    SystemGoal,
    SystemOperation,
    SystemScreenInput,
    SystemScreenOutput,
    SystemStepRecord,
)
from switch2_layers.safety.safety_rules import SAFETY_RULES

SCREEN_NAMES = ("home_menu", "controllers", "controller_order", "user_select")
JPEG = b"\xff\xd8\xff\xe0system-screen"
SOFTWARE_NAME = "テストのソフト名"
USER_NAME = "テストのユーザー名"
PRESS_DOWN = SystemOperation(
    screen="home_menu", kind="press", buttons=("DOWN",), seconds=None
)
SCREEN_INPUT = SystemScreenInput(
    t=123.456,
    goal=SystemGoal.PREPARE,
    software_name=SOFTWARE_NAME,
    user_name=USER_NAME,
    frame_jpeg=JPEG,
    frame_t=122.5,
    judged_screen="controllers",
    judged_frame_t=121.25,
    history=(),
)
OUTPUT = {
    "observation": "HOME メニュー。カーソルは上の段のソフトにある",
    "operations": [
        {"screen": "home_menu", "type": "press", "buttons": ["DOWN"], "seconds": None},
        {"screen": "home_menu", "type": "wait", "buttons": [], "seconds": 0.5},
    ],
    "done": False,
}
USAGE = {
    "input_tokens": 3000,
    "input_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 0},
    "output_tokens": 400,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 3400,
}
# (3000 - 1024) × 0.10 + 1024 × 0.01 + 400 × 0.50 = 407.84 (USD / 100 万トークン)
USAGE_COST = 0.00040784
USAGE_JSON = {
    "input_tokens": 3000,
    "cached_tokens": 1024,
    "cache_write_tokens": 0,
    "output_tokens": 400,
    "reasoning_tokens": 0,
}


def response_body(
    output: object = None,
    status: str = "completed",
    content: list | None = None,
    usage: dict | None = USAGE,
    **extra,
) -> dict:
    """Responses API の応答の本文。content を渡さなければ output の JSON の output_text 1 つ。"""
    text = json.dumps(OUTPUT if output is None else output, ensure_ascii=False)
    return {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "incomplete_details": None,
        "model": "gpt-6-luna",
        "output": [
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}]
                if content is None
                else content,
            }
        ],
        "usage": usage,
        **extra,
    }


class RecordingHandler:
    """MockTransport の handler。受け取った要求を記録し、response を返す (例外なら出す)。"""

    def __init__(self, response: httpx.Response | Exception):
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def payload(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def make_middle(
    response: httpx.Response | Exception,
) -> tuple[LunaSystemScreenMiddle, RecordingHandler]:
    handler = RecordingHandler(response)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LunaSystemScreenMiddle("sk-test", SCREEN_NAMES, client=client), handler


def sent_payload(screen_input: SystemScreenInput = SCREEN_INPUT) -> dict:
    """screen_input で decide を 1 回呼び、送られた要求の本文を返す。"""
    middle, handler = make_middle(httpx.Response(200, json=response_body()))
    middle.decide(screen_input)
    return handler.payload()


def developer_text(payload: dict) -> str:
    developer = payload["input"][0]
    assert developer["role"] == "developer"
    return "".join(part["text"] for part in developer["content"])


def user_texts(payload: dict) -> list[str]:
    user = payload["input"][1]
    assert user["role"] == "user"
    return [part["text"] for part in user["content"] if part["type"] == "input_text"]


def history_record(t: float, **changes: object) -> SystemStepRecord:
    values: dict[str, object] = {
        "t": t,
        "judged_screen": "home_menu",
        "operations": (PRESS_DOWN,),
        "sent_count": 1,
        "cut_reason": None,
    }
    values.update(changes)
    return SystemStepRecord(**values)


# --- 成功 ---


def test_decide_returns_parsed_output():
    middle, handler = make_middle(httpx.Response(200, json=response_body()))
    assert middle.decide(SCREEN_INPUT) == SystemScreenOutput(
        observation="HOME メニュー。カーソルは上の段のソフトにある",
        operations=(
            PRESS_DOWN,
            SystemOperation(screen="home_menu", kind="wait", buttons=(), seconds=0.5),
        ),
        done=False,
    )
    # 再試行しない
    assert len(handler.requests) == 1


def test_decide_sends_model_reasoning_and_strict_schema():
    middle, handler = make_middle(httpx.Response(200, json=response_body()))
    middle.decide(SCREEN_INPUT)
    request = handler.requests[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["Authorization"] == "Bearer sk-test"
    payload = handler.payload()
    # ゲームの画面の middle と同じモデルと effort
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning"] == {"effort": "none"}
    text_format = payload["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True
    schema = text_format["schema"]
    assert schema["required"] == ["observation", "operations", "done"]
    assert schema["additionalProperties"] is False
    screen = schema["properties"]["operations"]["items"]["properties"]["screen"]
    assert screen["enum"] == ["none", *SCREEN_NAMES]


def test_developer_message_is_the_fixed_instructions():
    payload = sent_payload()
    developer = payload["input"][0]
    assert developer["content"] == [
        {
            "type": "input_text",
            "text": SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
            "prompt_cache_breakpoint": {"mode": "explicit"},
        }
    ]
    assert payload["prompt_cache_options"] == {"mode": "explicit"}


def test_developer_message_does_not_change_with_input():
    other_input = dataclasses.replace(
        SCREEN_INPUT,
        goal=SystemGoal.CLEAN_UP,
        software_name="別のソフト名",
        user_name="別のユーザー名",
        judged_screen=None,
        history=(history_record(100.0),),
    )
    assert developer_text(sent_payload()) == developer_text(sent_payload(other_input))


def test_developer_message_has_no_software_or_user_name():
    text = developer_text(sent_payload())
    assert SOFTWARE_NAME not in text
    assert USER_NAME not in text


def test_developer_message_includes_safety_rules():
    assert SAFETY_RULES in developer_text(sent_payload())


def test_developer_message_describes_each_reference_screen():
    text = developer_text(sent_payload())
    for name in SCREEN_NAMES:
        assert name in text


def test_developer_message_has_the_procedure_of_every_goal():
    # 目的ごとの手順は、その回の目的によらず 3 つすべてを変わらない指示に載せる
    text = developer_text(sent_payload())
    for goal in SystemGoal:
        assert goal.value in text
        assert goal_text(goal) in text


def test_user_message_has_goal_and_its_procedure():
    for goal in SystemGoal:
        joined = "\n".join(
            user_texts(sent_payload(dataclasses.replace(SCREEN_INPUT, goal=goal)))
        )
        assert goal.value in joined
        assert goal_text(goal) in joined
        for other in SystemGoal:
            if other is not goal:
                assert goal_text(other) not in joined


def test_user_message_has_time_names_and_judgment():
    joined = "\n".join(user_texts(sent_payload()))
    assert "t=123.456" in joined
    assert SOFTWARE_NAME in joined
    assert USER_NAME in joined
    assert "controllers" in joined
    assert "t=121.250" in joined


def test_user_message_shows_none_for_no_reference_screen():
    screen_input = dataclasses.replace(SCREEN_INPUT, judged_screen=None)
    joined = "\n".join(user_texts(sent_payload(screen_input)))
    assert "none" in joined
    assert "t=121.250" in joined


def test_user_message_has_the_frame_with_its_time_label():
    payload = sent_payload()
    user = payload["input"][1]
    images = [part for part in user["content"] if part["type"] == "input_image"]
    expected_url = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode("ascii")
    assert [part["image_url"] for part in images] == [expected_url]
    assert "フレーム t=122.500" in user_texts(payload)
    # 画像の直前に時刻のラベルがある
    content = user["content"]
    image_index = next(
        index for index, part in enumerate(content) if part["type"] == "input_image"
    )
    assert content[image_index - 1] == {
        "type": "input_text",
        "text": "フレーム t=122.500",
    }
    assert all("prompt_cache_breakpoint" not in part for part in content)


def test_user_message_history_keeps_newest_ten_in_order():
    # t=50.000〜t=60.000 の 11 件。古い t=50.000 は入らない
    history = tuple(history_record(50.0 + index) for index in range(11))
    screen_input = dataclasses.replace(SCREEN_INPUT, history=history)
    joined = "\n".join(user_texts(sent_payload(screen_input)))
    assert "t=50.000" not in joined
    positions = [joined.index(f"t={50 + index}.000") for index in range(1, 11)]
    assert positions == sorted(positions)


def test_user_message_history_has_operations_counts_and_cut_reason():
    record = history_record(
        70.0,
        judged_screen="user_select",
        operations=(
            SystemOperation(
                screen="user_select", kind="press", buttons=("RIGHT",), seconds=None
            ),
            SystemOperation(
                screen="user_select", kind="press", buttons=("RIGHT",), seconds=None
            ),
        ),
        sent_count=1,
        cut_reason=SystemCutReason.SCREEN_MISMATCH,
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=(record,))
    joined = "\n".join(user_texts(sent_payload(screen_input)))
    assert "t=70.000" in joined
    assert "user_select" in joined
    assert "RIGHT" in joined
    assert "screen_mismatch" in joined


def test_user_message_history_counts_the_operation_whose_send_failed():
    # 送信に失敗した press は送った操作の数に入る。列の最後の操作で失敗しても、最後まで送ったとは書かない
    record = history_record(
        75.0,
        operations=(
            PRESS_DOWN,
            SystemOperation(
                screen="home_menu", kind="press", buttons=("RIGHT",), seconds=None
            ),
        ),
        sent_count=2,
        cut_reason=SystemCutReason.SEND_FAILED,
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=(record,))
    joined = "\n".join(user_texts(sent_payload(screen_input)))
    assert "t=75.000" in joined
    assert "送った操作 2/2、打ち切った理由 send_failed" in joined
    assert "最後まで送った" not in joined


# この段階で送った A・B・X の節の見出し
SENT_BUTTONS_TITLE = "この段階で送った A・B・X"
HISTORY_TITLE = "この段階のこれまでの呼び出し"


def user_section(payload: dict, title: str) -> str:
    """user メッセージのうち、見出し title の節の中身 (見出しの行を除く) を返す。"""
    for text in user_texts(payload):
        if text.startswith(f"## {title}\n"):
            return text.removeprefix(f"## {title}\n")
    raise AssertionError(f"no section {title!r}")


def press_a(screen: str) -> SystemOperation:
    return SystemOperation(screen=screen, kind="press", buttons=("A",), seconds=None)


def press(screen: str, *buttons: str) -> SystemOperation:
    return SystemOperation(screen=screen, kind="press", buttons=buttons, seconds=None)


def test_sent_buttons_section_lists_a_b_and_x_in_sent_order():
    # 準備は controllers での B の前後の A で、片付けは home_menu での X で次の操作を決めるため、1 本の並びに置く
    history = (
        history_record(
            50.0, judged_screen="controllers", operations=(press("controllers", "B"),)
        ),
        history_record(
            51.0,
            operations=(press("home_menu", "UP"), press("home_menu", "RIGHT")),
            sent_count=2,
        ),
        history_record(52.0, operations=(press("home_menu", "X"),)),
        history_record(
            53.0,
            judged_screen="software_close",
            operations=(press("software_close", "A"),),
        ),
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=history)
    section = user_section(sent_payload(screen_input), SENT_BUTTONS_TITLE)
    assert section.split("\n") == [
        "t=50.000 controllers で B",
        "t=52.000 home_menu で X",
        "t=53.000 software_close で A",
    ]


def test_sent_buttons_section_includes_b_older_than_ten():
    # 準備が長引くと、controllers で B を送った回は履歴の節の 10 件から外れる
    history = (
        history_record(
            40.0, judged_screen="controllers", operations=(press("controllers", "B"),)
        ),
        *(history_record(41.0 + index) for index in range(11)),
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=history)
    payload = sent_payload(screen_input)
    assert "t=40.000" not in user_section(payload, HISTORY_TITLE)
    assert user_section(payload, SENT_BUTTONS_TITLE) == "t=40.000 controllers で B"


def test_sent_buttons_section_skips_b_that_was_not_sent():
    record = history_record(
        60.0,
        judged_screen="controllers",
        operations=(PRESS_DOWN, press("controllers", "B")),
        sent_count=1,
        cut_reason=SystemCutReason.SCREEN_MISMATCH,
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=(record,))
    section = user_section(sent_payload(screen_input), SENT_BUTTONS_TITLE)
    assert section == "まだ送っていません"


def test_sent_decisions_section_follows_the_history_section():
    titles = [text.split("\n", 1)[0] for text in user_texts(sent_payload())]
    assert titles.index(f"## {SENT_BUTTONS_TITLE}") == (
        titles.index(f"## {HISTORY_TITLE}") + 1
    )


@pytest.mark.parametrize(
    "history",
    [(), (history_record(50.0),)],
    ids=["no-history", "no-a-in-history"],
)
def test_sent_decisions_section_without_a(history):
    screen_input = dataclasses.replace(SCREEN_INPUT, history=history)
    section = user_section(sent_payload(screen_input), SENT_BUTTONS_TITLE)
    assert section == "まだ送っていません"


def test_sent_decisions_section_lists_each_sent_a_in_order():
    history = (
        history_record(
            50.0,
            judged_screen="controller_order",
            operations=(press_a("controller_order"),),
        ),
        history_record(51.0),
        history_record(
            52.0,
            judged_screen="home_menu",
            operations=(
                SystemOperation(
                    screen="home_menu", kind="press", buttons=("L", "A"), seconds=0.5
                ),
            ),
        ),
        history_record(
            53.0, judged_screen="user_select", operations=(press_a("user_select"),)
        ),
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=history)
    section = user_section(sent_payload(screen_input), SENT_BUTTONS_TITLE)
    assert section.split("\n") == [
        "t=50.000 controller_order で A",
        "t=52.000 home_menu で A",
        "t=53.000 user_select で A",
    ]


def test_sent_decisions_section_includes_history_older_than_ten():
    history = (
        history_record(
            40.0,
            judged_screen="controller_order",
            operations=(press_a("controller_order"),),
        ),
        *(history_record(41.0 + index) for index in range(11)),
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=history)
    payload = sent_payload(screen_input)
    # 履歴の節には新しい方から 10 件だけが入り、t=40.000 の呼び出しは入らない
    assert "t=40.000" not in user_section(payload, HISTORY_TITLE)
    assert user_section(payload, SENT_BUTTONS_TITLE) == "t=40.000 controller_order で A"


def test_sent_decisions_section_skips_a_that_was_not_sent():
    record = history_record(
        60.0,
        operations=(PRESS_DOWN, press_a("home_menu")),
        sent_count=1,
        cut_reason=SystemCutReason.DECISION_NOT_FIRST,
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=(record,))
    section = user_section(sent_payload(screen_input), SENT_BUTTONS_TITLE)
    assert section == "まだ送っていません"


def test_sent_decisions_section_includes_a_whose_send_failed():
    # 送信に失敗した A は送った操作の数に入るので、届いたかもしれないものとして入る
    record = history_record(
        61.0,
        judged_screen="user_select",
        operations=(
            press_a("user_select"),
            SystemOperation(
                screen="user_select", kind="press", buttons=("RIGHT",), seconds=None
            ),
        ),
        sent_count=1,
        cut_reason=SystemCutReason.SEND_FAILED,
    )
    screen_input = dataclasses.replace(SCREEN_INPUT, history=(record,))
    section = user_section(sent_payload(screen_input), SENT_BUTTONS_TITLE)
    assert section == "t=61.000 user_select で A"


def test_developer_message_describes_the_sent_decisions_section():
    assert SENT_BUTTONS_TITLE in developer_text(sent_payload())


def test_developer_message_describes_the_send_failed_reason():
    text = developer_text(sent_payload())
    assert "- send_failed: 操作の送信に失敗した。" in text
    assert (
        "送信に失敗した操作 (send_failed) は、届いたかもしれないものとして入ります。"
        in text
    )


def test_details_after_success_have_usage_cost_and_response():
    body = response_body()
    middle, _ = make_middle(httpx.Response(200, json=body))
    middle.decide(SCREEN_INPUT)
    details = middle.take_call_details()
    assert list(details) == list(CALL_DETAIL_KEYS)
    assert details["usage"] == USAGE_JSON
    assert details["cost_usd"] == pytest.approx(USAGE_COST)
    assert details["response"] == body
    assert details["error_body_head"] is None
    # 渡した記録は消える
    assert middle.take_call_details() == {}


def test_details_before_any_call_are_empty():
    middle, _ = make_middle(httpx.Response(200, json=response_body()))
    assert middle.take_call_details() == {}


def test_middle_is_a_call_details_source():
    middle, _ = make_middle(httpx.Response(200, json=response_body()))
    assert isinstance(middle, MiddleCallDetailsSource)


def test_middle_keeps_settings_as_attributes():
    middle, _ = make_middle(httpx.Response(200, json=response_body()))
    assert middle.api_key == "sk-test"
    assert middle.screen_names == SCREEN_NAMES
    assert middle.timeout_s == 30.0


def test_middle_rejects_invalid_screen_names():
    with pytest.raises(ValueError):
        LunaSystemScreenMiddle("sk-test", ("home_menu", "none"))


# --- 失敗 ---


def test_http_error_raises_system_screen_middle_error():
    error_text = json.dumps({"error": {"message": "Invalid schema", "type": "x"}})
    middle, handler = make_middle(httpx.Response(400, text=error_text))
    with pytest.raises(SystemScreenMiddleError) as raised:
        middle.decide(SCREEN_INPUT)
    assert isinstance(raised.value.__cause__, LunaCallError)
    assert len(handler.requests) == 1
    assert middle.take_call_details() == {
        "usage": None,
        "cost_usd": None,
        "response": None,
        "error_body_head": error_text,
    }


def test_long_error_body_is_recorded_as_head():
    middle, _ = make_middle(
        httpx.Response(500, text="e" * (ERROR_BODY_HEAD_CHARS + 10))
    )
    with pytest.raises(SystemScreenMiddleError):
        middle.decide(SCREEN_INPUT)
    assert middle.take_call_details()["error_body_head"] == "e" * ERROR_BODY_HEAD_CHARS


def test_timeout_raises_system_screen_middle_error():
    middle, handler = make_middle(httpx.ReadTimeout("timed out"))
    with pytest.raises(SystemScreenMiddleError):
        middle.decide(SCREEN_INPUT)
    assert len(handler.requests) == 1
    assert middle.take_call_details() == {
        "usage": None,
        "cost_usd": None,
        "response": None,
        "error_body_head": None,
    }


def test_body_that_is_not_json_raises_system_screen_middle_error():
    middle, _ = make_middle(httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(SystemScreenMiddleError):
        middle.decide(SCREEN_INPUT)
    assert middle.take_call_details()["error_body_head"] == "<html>oops</html>"


def test_incomplete_response_raises_and_records_usage():
    body = response_body(
        status="incomplete", incomplete_details={"reason": "max_output_tokens"}
    )
    middle, _ = make_middle(httpx.Response(200, json=body))
    with pytest.raises(SystemScreenMiddleError):
        middle.decide(SCREEN_INPUT)
    details = middle.take_call_details()
    assert details["usage"] == USAGE_JSON
    assert details["cost_usd"] == pytest.approx(USAGE_COST)
    assert details["response"] == body


def test_refusal_raises_system_screen_middle_error():
    body = response_body(content=[{"type": "refusal", "refusal": "Sorry, I can't."}])
    middle, _ = make_middle(httpx.Response(200, json=body))
    with pytest.raises(SystemScreenMiddleError):
        middle.decide(SCREEN_INPUT)
    assert middle.take_call_details()["response"] == body


@pytest.mark.parametrize(
    "output",
    [
        {
            **OUTPUT,
            "operations": [{**OUTPUT["operations"][0], "screen": "settings"}],
        },
        {**OUTPUT, "operations": [OUTPUT["operations"][0]] * 11},
        {
            **OUTPUT,
            "operations": [{**OUTPUT["operations"][0], "buttons": ["UP", "RIGHT"]}],
        },
        {key: value for key, value in OUTPUT.items() if key != "done"},
        {**OUTPUT, "extra": 1},
    ],
    ids=["unknown-screen", "too-many", "two-directions", "missing-key", "extra-key"],
)
def test_output_that_breaks_the_rules_raises_and_records_response(output):
    # OUTPUT そのものは読める (誤りの出力が、別の理由で失敗して通らないように)
    valid, _ = make_middle(httpx.Response(200, json=response_body()))
    assert len(valid.decide(SCREEN_INPUT).operations) == 2
    body = response_body(output=output)
    middle, _ = make_middle(httpx.Response(200, json=body))
    with pytest.raises(SystemScreenMiddleError) as raised:
        middle.decide(SCREEN_INPUT)
    assert isinstance(raised.value.__cause__, ValueError)
    details = middle.take_call_details()
    assert details["response"] == body
    assert details["cost_usd"] == pytest.approx(USAGE_COST)


def test_each_decide_replaces_previous_details():
    handler = RecordingHandler(httpx.Response(200, json=response_body()))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    middle = LunaSystemScreenMiddle("sk-test", SCREEN_NAMES, client=client)
    middle.decide(SCREEN_INPUT)
    # 前の回の記録を渡さないまま、次の回が失敗する
    handler.response = httpx.ReadTimeout("timed out")
    with pytest.raises(SystemScreenMiddleError):
        middle.decide(SCREEN_INPUT)
    details = middle.take_call_details()
    assert details["response"] is None
    assert details["usage"] is None


# --- フェイク ---


def fake_output(observation: str, done: bool = False) -> SystemScreenOutput:
    return SystemScreenOutput(
        observation=observation, operations=(PRESS_DOWN,), done=done
    )


def test_fake_returns_given_outputs_in_order():
    first = fake_output("1 回目")
    second = fake_output("2 回目", done=True)
    middle = FakeSystemScreenMiddle([first, second])
    assert middle.decide(SCREEN_INPUT) == first
    assert middle.decide(SCREEN_INPUT) == second


def test_fake_returns_empty_operations_after_outputs_run_out():
    middle = FakeSystemScreenMiddle([fake_output("1 回目", done=True)])
    middle.decide(SCREEN_INPUT)
    for _ in range(2):
        result = middle.decide(SCREEN_INPUT)
        assert result.operations == ()
        assert result.done is False


def test_fake_without_outputs_returns_empty_operations():
    result = FakeSystemScreenMiddle().decide(SCREEN_INPUT)
    assert result.operations == ()
    assert result.done is False


def test_fake_reads_the_given_iterable_once():
    outputs = iter([fake_output("1 回目")])
    middle = FakeSystemScreenMiddle(outputs)
    assert middle.decide(SCREEN_INPUT).observation == "1 回目"
    assert middle.decide(SCREEN_INPUT).operations == ()


def test_fake_for_run_returns_done_without_operations_forever():
    # run の --middle fake の本体の画面の middle: 操作なしで done を真で返し続ける
    assert FAKE_DONE_OUTPUT == SystemScreenOutput(
        observation="", operations=(), done=True
    )
    middle = FakeSystemScreenMiddle(exhausted_output=FAKE_DONE_OUTPUT)
    for _ in range(3):
        assert middle.decide(SCREEN_INPUT) == FAKE_DONE_OUTPUT


def test_fake_returns_the_given_outputs_before_the_exhausted_output():
    first = fake_output("1 回目")
    middle = FakeSystemScreenMiddle([first], exhausted_output=FAKE_DONE_OUTPUT)
    assert middle.decide(SCREEN_INPUT) == first
    assert middle.decide(SCREEN_INPUT) == FAKE_DONE_OUTPUT
