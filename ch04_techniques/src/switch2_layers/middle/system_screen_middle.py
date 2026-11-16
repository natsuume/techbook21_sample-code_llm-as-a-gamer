"""本体の画面の middle: Protocol (SystemScreenMiddle) と失敗 (SystemScreenMiddleError)、gpt-6-luna の実装
(LunaSystemScreenMiddle)、フェイク (FakeSystemScreenMiddle)。

LunaSystemScreenMiddle の 1 回の decide の流れ:
    1. system_screen_request.build_system_screen_payload(screen_input, screen_names) で要求の本文を作る
    2. luna_client.post_responses で 1 回だけ送る (タイムアウトは timeout_s。再試行はしない)
    3. luna_client.read_output_text で出力の文を取り出す (status が completed でない・拒否・出力の文が無いときは
       LunaCallError)
    4. system_screen_schema.parse_system_screen_output(出力の文, screen_names) で SystemScreenOutput にする
       (決まりに合わなければ ValueError)
2〜4 の LunaCallError と ValueError は、SystemScreenMiddleError にして出す (元の例外を __cause__ に持つ)。
失敗した回は、操作を 1 つも送らない (呼び出し側が、呼び出しの失敗として扱う)。

middle/system_calls.jsonl の呼び出しの行に足す記録 (take_call_details が返す辞書) は、ゲームの画面の middle と同じく
luna_middle.build_call_details の形 (usage・cost_usd・response・error_body_head)。応答の本文を受け取った後の失敗
(incomplete・拒否・決まりに合わない出力) でも、usage・cost_usd・response を記録する。
"""

from collections import deque
from collections.abc import Iterable, Sequence
from typing import Protocol

import httpx

from switch2_layers.middle.luna_client import (
    LUNA_TIMEOUT_S,
    LunaCallError,
    post_responses,
    read_output_text,
)
from switch2_layers.middle.luna_middle import build_call_details
from switch2_layers.middle.system_screen_request import build_system_screen_payload
from switch2_layers.middle.system_screen_schema import (
    build_system_screen_schema,
    parse_system_screen_output,
)
from switch2_layers.middle.system_screen_types import (
    SystemScreenInput,
    SystemScreenOutput,
)

# FakeSystemScreenMiddle が、渡された出力を使い切った後に返す出力の既定値 (操作が空で done が偽)
FAKE_EXHAUSTED_OUTPUT = SystemScreenOutput(observation="", operations=(), done=False)
# 操作が空で done が真の出力。run の --middle fake の本体の画面の middle は、使い切った後 (最初から) これを返し続ける
FAKE_DONE_OUTPUT = SystemScreenOutput(observation="", operations=(), done=True)


class SystemScreenMiddleError(Exception):
    """本体の画面の middle の呼び出しの失敗 (要求の失敗・応答を読めない・出力が決まりに合わない)。"""


class SystemScreenMiddle(Protocol):
    """本体の画面の middle。目的と今の画面から、送る操作の列を決める。"""

    def decide(self, screen_input: SystemScreenInput) -> SystemScreenOutput:
        """screen_input から操作の列を決めて返す。失敗したら SystemScreenMiddleError を出す。"""
        ...


class LunaSystemScreenMiddle:
    """SystemScreenMiddle の実装。gpt-6-luna を Responses API で呼ぶ。

    middle_call_record.MiddleCallDetailsSource でもあり、decide の 1 回ごとの記録を take_call_details で渡す。
    1 つのスレッドから使う。

    属性:
        api_key        API キー (呼び出し側が環境変数 OPENAI_API_KEY から読む)
        screen_names   見本の画面の名前の一覧 (出力の screen の選択肢は "none" とこの一覧。並びは渡された順)
        client         要求に使う httpx.Client。None を渡したら新しく作る (テストでは MockTransport の Client を渡す)
        timeout_s      1 回の要求のタイムアウト (秒)
    """

    def __init__(
        self,
        api_key: str,
        screen_names: Sequence[str],
        client: httpx.Client | None = None,
        timeout_s: float = LUNA_TIMEOUT_S,
    ) -> None:
        """属性を入れる。screen_names はタプルにして持つ。client が None なら httpx.Client() を作る。

        ここでは要求を送らない。screen_names に "none" があるか、同じ名前が 2 つ以上あれば ValueError にする。
        """
        names = tuple(screen_names)
        # 出力のスキーマを作れるかで、一覧の誤りを確かめる (Client を作る前に)
        build_system_screen_schema(names)
        self.api_key = api_key
        self.screen_names = names
        self.client = httpx.Client() if client is None else client
        self.timeout_s = timeout_s
        # 直前の decide の記録。decide を呼んでいないか、渡した後なら空
        self._call_details: dict[str, object] = {}

    def decide(self, screen_input: SystemScreenInput) -> SystemScreenOutput:
        """screen_input から gpt-6-luna の出力を得て SystemScreenOutput を返す (モジュールの docstring の 1〜4)。

        始めに、持っている記録を build_call_details(None, None) に置き換える (前の回の記録を残さない)。
        応答の本文を受け取ったら build_call_details(本文, None)、LunaCallError なら
        build_call_details(例外の response_body, 例外の error_body_head) に置き換えてから、SystemScreenMiddleError を出す。
        """
        self._call_details = build_call_details(None, None)
        payload = build_system_screen_payload(screen_input, self.screen_names)
        try:
            body = post_responses(self.client, self.api_key, payload, self.timeout_s)
            # 出力を読めなくても (決まりに合わない出力を含む)、費用のかかった応答を記録に残す
            self._call_details = build_call_details(body, None)
            text = read_output_text(body)
        except LunaCallError as error:
            self._call_details = build_call_details(
                error.response_body, error.error_body_head
            )
            raise SystemScreenMiddleError(str(error)) from error
        try:
            return parse_system_screen_output(text, self.screen_names)
        except ValueError as error:
            raise SystemScreenMiddleError(
                f"system screen middle output does not follow the rules: {error}"
            ) from error

    def take_call_details(self) -> dict[str, object]:
        """直前の decide の記録 (luna_middle.build_call_details の形) を返し、持っている記録を消す。

        decide を呼んでいないか、記録を渡した後なら空の辞書を返す。
        """
        details, self._call_details = self._call_details, {}
        return details


class FakeSystemScreenMiddle:
    """SystemScreenMiddle のフェイク。作るときに渡した出力の列を、decide のたびに 1 つずつ順に返す。

    run の --middle fake では FakeSystemScreenMiddle(exhausted_output=FAKE_DONE_OUTPUT) を使う (操作なしで done を
    真で返し続ける)。
    """

    def __init__(
        self,
        outputs: Iterable[SystemScreenOutput] = (),
        exhausted_output: SystemScreenOutput = FAKE_EXHAUSTED_OUTPUT,
    ) -> None:
        """outputs を、返す順の列として持つ (作るときに一度だけ読む)。exhausted_output は使い切った後に返す出力。"""
        self._outputs: deque[SystemScreenOutput] = deque(outputs)

    def decide(self, screen_input: SystemScreenInput) -> SystemScreenOutput:
        """持っている列の次の出力を返す。列を使い切ったら exhausted_output を返す (何度呼んでも同じ)。

        screen_input は使わない。例外は出さない。
        """
        if self._outputs:
            return self._outputs.popleft()
        return FAKE_EXHAUSTED_OUTPUT
