"""OpenAI Decisions API (POST /v1/decisions) を Decision Model として使う。

要求と応答の形は公式ドキュメント (https://developers.openai.com/api/docs/guides/decisions と
https://developers.openai.com/api/reference/resources/decisions/methods/create、2026-11-12 に確認) に合わせる。

要求の形:
    {
      "model": "gpt-6-luna",
      "input": [{"role": "user", "content": [
        {"type": "input_text", "text": <instructions>},
        {"type": "input_text", "text": <state_text>},
        {"type": "input_text", "text": <画像 1 のラベル>},
        {"type": "input_image", "image_url": "data:image/jpeg;base64,<画像 1 の base64>"},
        ...
      ]}],
      "questions": [{"type": "predicate", "name": <Question.name>, "instructions": <Question.text>}, ...]
    }
応答の形: {"answers": [{"type": "predicate", "name": ..., "probability": ...}, ...], "model": ..., "usage": ...}。
answer は {"type": "refusal", "name": ...} (probability が無い) のこともある。

未確認 (公式ドキュメントに記載なし): Decisions API のエラーの本文の形、画像の枚数・大きさの上限、レート制限、タイムアウト。
2xx 以外の応答はすべて DecisionModelError にする。JPEG の data URL の MIME 型 (image/jpeg) は、
ドキュメントの PNG の例 (data:image/png;base64,...) からの類推で、JPEG の例は未確認。
"""

from collections.abc import Mapping, Sequence

import httpx

from switch2_agent.decision_models.base import Decision
from switch2_agent.decision_request import DecisionRequest

DECISIONS_URL = "https://api.openai.com/v1/decisions"
DEFAULT_DECISIONS_MODEL = "gpt-6-luna"  # Decisions API で使える唯一のモデル
API_KEY_ENV = "OPENAI_API_KEY"  # API キーを読む環境変数の名前
DEFAULT_TIMEOUT_S = 10.0  # 1 回の要求の HTTP のタイムアウト (秒)


def jpeg_data_url(jpeg: bytes) -> str:
    """JPEG のバイト列を `data:image/jpeg;base64,<base64>` の data URL にする。

    base64 は標準のアルファベットで、= のパディングを付け、改行を入れない (base64.b64encode)。
    """
    raise NotImplementedError


def build_decisions_headers(api_key: str) -> dict[str, str]:
    """要求のヘッダー {"Authorization": "Bearer <api_key>", "Content-Type": "application/json"} を返す。"""
    raise NotImplementedError


def build_decisions_payload(
    request: DecisionRequest, model: str = DEFAULT_DECISIONS_MODEL
) -> dict[str, object]:
    """request を Decisions API の要求の JSON (モジュールの docstring の形) にする。

    input は user のメッセージ 1 つ。content には、instructions のテキスト、state_text のテキストを置き、
    続けて request.images の順に、ラベルのテキスト (input_text) と画像 (input_image、jpeg_data_url) を
    1 組ずつ置く。各画像の本体は要求の中に 1 回だけ入る。
    questions は request.questions の順に、{"type": "predicate", "name": name, "instructions": text}。
    """
    raise NotImplementedError


def parse_decisions_response(
    body: Mapping[str, object], names: Sequence[str]
) -> Decision:
    """応答の本文 body から、names (要求した問いの name) それぞれの確率を読み、Decision にする。

    body["answers"] の各 answer を name で照合する (answers の並びには頼らない)。
    - type が "predicate" の answer は probability を確率にする (base.read_probability で検証する)
    - type が "refusal" の answer は確率 0.0 とし、refusals に数える
    names のうち answer が無い問い、answers がリストでない応答、answer が object でないか type が
    predicate / refusal 以外のものは DecisionModelError にする。names に無い name の answer は無視する。
    """
    raise NotImplementedError


class DecisionsApiModel:
    """decision_models.base.DecisionModel の実装。OpenAI Decisions API に httpx で要求を送る。

    属性:
        api_key     API キー (run は環境変数 API_KEY_ENV から読む)
        client      要求に使う httpx.Client。None を渡したら新しく作る (テストでは MockTransport の Client を渡す)
        timeout_s   1 回の要求の HTTP のタイムアウト (秒)
        model       要求の model
    """

    api_key: str
    client: httpx.Client
    timeout_s: float
    model: str

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        model: str = DEFAULT_DECISIONS_MODEL,
    ):
        """属性を入れる。client が None なら httpx.Client() を作る。"""
        raise NotImplementedError

    def decide(self, request: DecisionRequest) -> Decision:
        """request を DECISIONS_URL に POST し、parse_decisions_response の結果を返す。

        ヘッダーは build_decisions_headers、本文は build_decisions_payload(request, model)。
        送信と応答の検証は base.post_json で行う (httpx の例外と 2xx 以外の応答は DecisionModelError)。
        """
        raise NotImplementedError
