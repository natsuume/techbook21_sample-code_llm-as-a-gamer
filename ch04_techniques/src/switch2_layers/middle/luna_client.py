"""gpt-6-luna を Responses API (POST https://api.openai.com/v1/responses) で呼ぶ HTTP の処理と、応答の読み取り。

応答の本文の形 (使う部分):
    {"status": "completed" | "incomplete" | "failed" | "cancelled" | "queued" | "in_progress",
     "incomplete_details": {"reason": ...} | null,
     "output": [{"type": "message", "content": [{"type": "output_text", "text": ...}
                                                | {"type": "refusal", "refusal": ...}, ...]}, ...],
     "usage": {...}}
構造化出力の JSON の文は、output の type が "message" の要素の content の output_text にある。最上位の output_text は
SDK が作る値で、HTTP の応答の本文には無い。output の並びには頼らず、type が "message" の要素を探す。
status が "completed" でも、content が拒否 (refusal) のことがある。

失敗はすべて LunaCallError にする。再試行はしない (middle のループが次の間隔で呼び直す)。
"""

from collections.abc import Mapping

import httpx

RESPONSES_URL = "https://api.openai.com/v1/responses"
# httpx のタイムアウト (秒)。接続・送信・受信・接続の空きの待ちのそれぞれに同じ値を使う
LUNA_TIMEOUT_S = 30.0
# 記録に残すエラーの本文の先頭の文字数
ERROR_BODY_HEAD_CHARS = 1000


class LunaCallError(Exception):
    """gpt-6-luna の呼び出しの失敗。

    response_body     2xx の応答の本文の JSON の object (status が completed でない・拒否・出力の文が無いとき)。
                      応答の本文を読めなかったときは None
    error_body_head   2xx 以外の応答の本文か、JSON の object として読めない本文の先頭 ERROR_BODY_HEAD_CHARS 文字。
                      無ければ None (接続できない・タイムアウトなど、応答が無いときを含む)
    """

    def __init__(
        self,
        message: str,
        *,
        response_body: dict[str, object] | None = None,
        error_body_head: str | None = None,
    ) -> None:
        """message を例外の説明にし、response_body と error_body_head を属性に持つ。"""
        super().__init__(message)
        self.response_body = response_body
        self.error_body_head = error_body_head


def build_headers(api_key: str) -> dict[str, str]:
    """要求のヘッダー {"Authorization": "Bearer <api_key>", "Content-Type": "application/json"} を返す。"""
    raise NotImplementedError


def post_responses(
    client: httpx.Client,
    api_key: str,
    payload: Mapping[str, object],
    timeout_s: float = LUNA_TIMEOUT_S,
) -> dict[str, object]:
    """payload を RESPONSES_URL に JSON で 1 回だけ POST し、応答の本文の JSON の object を返す。

    ヘッダーは build_headers(api_key)、タイムアウトは timeout_s 秒 (httpx の timeout)。
    - httpx の例外 (httpx.HTTPError。接続できない、タイムアウトなど) は LunaCallError にする
      (response_body と error_body_head は None。説明には例外の説明を含める)
    - 2xx 以外の応答は、ステータスコードと本文の先頭を説明に含め、error_body_head に本文の先頭
      ERROR_BODY_HEAD_CHARS 文字を入れた LunaCallError にする
    - 本文が JSON でないか、JSON の object でなければ、error_body_head に本文の先頭を入れた LunaCallError にする
    API キーは説明にも error_body_head にも入れない。
    """
    raise NotImplementedError


def read_output_text(body: Mapping[str, object]) -> str:
    """応答の本文 body から、構造化出力の JSON の文を取り出して返す。

    1. body["status"] が "completed" でなければ LunaCallError にする。説明には status と、
       incomplete_details が object なら その reason を含める
    2. output のうち type が "message" の要素の content を、output と content の順に見る。
       type が "refusal" の要素があれば、その refusal の文を説明に含めた LunaCallError にする
    3. type が "output_text" の要素の text を順につないだ文を返す。1 つも無ければ LunaCallError にする
    output がリストでないとき、要素や content が期待の形 (object・リスト・文字列) でないものは読み飛ばす
    (その結果 output_text が無ければ 3 の LunaCallError)。
    LunaCallError の response_body には body (dict にしたもの) を入れる。
    """
    raise NotImplementedError
