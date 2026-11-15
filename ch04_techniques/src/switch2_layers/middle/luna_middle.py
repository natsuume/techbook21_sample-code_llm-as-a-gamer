"""MiddleAnalyzer の gpt-6-luna の実装 (LunaMiddleAnalyzer) と、middle/calls.jsonl の行に足す記録。

1 回の analyze の流れ:
    1. luna_request.build_luna_payload で要求の本文を作る
    2. luna_client.post_responses で 1 回だけ送る (タイムアウトは timeout_s。再試行はしない)
    3. luna_client.read_output_text で出力の文を取り出す (status が completed でない・拒否・出力の文が無いときは
       LunaCallError)
    4. luna_output_schema.parse_middle_output で MiddleOutput にする (スキーマに合わなければ ValueError)
どの例外も外に出す (middle_loop が middle の失敗として数え、次の間隔で呼び直す)。

middle/calls.jsonl の行に足す記録 (take_call_details が返す辞書。build_call_details が作る。キーは CALL_DETAIL_KEYS の順):
    usage             luna_usage.usage_to_json の形。応答の本文が無いか、本文に usage が無ければ null
    cost_usd          luna_usage.estimate_cost_usd の値。usage が null なら null
    response          2xx の応答の本文の JSON の object (デバッグ用)。無ければ null
    error_body_head   2xx 以外の応答か、JSON の object として読めない本文の先頭 (luna_client.LunaCallError の
                      error_body_head)。無ければ null
応答の本文を受け取った後の失敗 (incomplete・拒否・スキーマに合わない出力) でも、usage・cost_usd・response を記録する。
"""

from collections.abc import Mapping

import httpx

from switch2_layers.middle.luna_client import LUNA_TIMEOUT_S
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput

CALL_DETAIL_KEYS: tuple[str, ...] = ("usage", "cost_usd", "response", "error_body_head")


def build_call_details(
    response_body: Mapping[str, object] | None, error_body_head: str | None
) -> dict[str, object]:
    """middle/calls.jsonl の行に足す記録 (モジュールの docstring の形) を、CALL_DETAIL_KEYS の順のキーで返す。

    response_body が None でなければ luna_usage.read_usage で usage を読み、読めれば usage に usage_to_json、
    cost_usd に estimate_cost_usd を入れる (読めなければ両方 None)。response には response_body を dict にしたもの
    (None なら None)、error_body_head にはそのままの値を入れる。
    """
    raise NotImplementedError


class LunaMiddleAnalyzer:
    """middle_types.MiddleAnalyzer の実装。gpt-6-luna を Responses API で呼ぶ。

    middle_call_record.MiddleCallDetailsSource でもあり、analyze の 1 回ごとの記録を take_call_details で渡す。
    run_once と同じ 1 つのスレッドから使う。

    属性:
        api_key     API キー (cli が環境変数 OPENAI_API_KEY から読む)
        client      要求に使う httpx.Client。None を渡したら新しく作る (テストでは MockTransport の Client を渡す)
        timeout_s   1 回の要求のタイムアウト (秒)
    """

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_s: float = LUNA_TIMEOUT_S,
    ) -> None:
        """属性を入れる。client が None なら httpx.Client() を作る。ここでは要求を送らない。"""
        raise NotImplementedError

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        """middle_input から gpt-6-luna の出力を得て MiddleOutput を返す (モジュールの docstring の 1〜4)。

        始めに、持っている記録を build_call_details(None, None) に置き換える (前の回の記録を残さない)。
        応答の本文を受け取ったら build_call_details(本文, None)、LunaCallError なら
        build_call_details(例外の response_body, 例外の error_body_head) に置き換えてから、例外を外に出す。
        """
        raise NotImplementedError

    def take_call_details(self) -> dict[str, object]:
        """直前の analyze の記録 (モジュールの docstring の形) を返し、持っている記録を消す。

        analyze を呼んでいないか、記録を渡した後なら空の辞書を返す。
        """
        raise NotImplementedError
