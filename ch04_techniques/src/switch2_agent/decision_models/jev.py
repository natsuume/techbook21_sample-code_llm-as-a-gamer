"""TypeSafe Jev (System One、POST /v1/systemone) を Decision Model として使う。

要求と応答の形は公式ドキュメント (https://docs.typesafe.ai/api/system-one、https://docs.typesafe.ai/images、
https://docs.typesafe.ai/models、2026-11-12 に確認) に合わせる。

要求の形 (画像は base64 の JSON で送り、multipart は使わない):
    {
      "model": "jev-1.14.0",
      "state": {
        "instructions": <instructions>,
        "game_notes": <state_text>,
        "operation_history": <history_text>,
        "frames": [{"label": <画像 1 のラベル>, "image": <画像 1 の参照文字列>}, ...]
      },
      "assets": {<画像 1 の参照文字列>: {"type": "image", "format": "jpg", "data_base64": <base64>}, ...},
      "questions": {<Question.name>: {"type": "noul", "instructions": <Question.text>}, ...}
    }
state のキーは instructions、game_notes、operation_history、frames の順。operation_history (直近の応答と送った
操作の履歴) は、history_text が空文字なら入れない。frames のラベルは DecisionRequest の画像のラベルをそのまま使う。
前回の操作の前か後かはラベルに書いてあるため、前回の操作を送った時点の区切りの文 (previous_operation_text) は入れない。
画像はトップレベルの assets に 1 回だけ登録し、state の frames の各要素の image に参照文字列を 1 回だけ置く。
参照文字列は書くたびに同じ画像が足されるため、問いの文には書かない。どこからも参照しない asset は 400 になるため、
登録した asset はすべて state から参照する。画像が無い要求では assets を入れない (assets は 1 個以上の map)。
画像を含む要求には TypeSafe-Danger-Zone-Images: true のヘッダーを付ける。
応答の形: {"model": ..., "answers": {<問いの ID>: {"type": "noul", "noul": <0〜1>}, ...}, "usage": ...}。

画像の制約: PNG か JPEG の静止画、各辺 280〜1568 px。1280x720 は範囲に収まる。
未確認 (公式ドキュメントに記載なし): 画像 1 枚が何トークンになるか。文脈長は要求全体で 64k トークン、
問い 1 つあたり 32k トークンで、画像 10 枚と 43 問が収まるかは未確認。超えると 400 (max_tokens_exceeded)。
"""

import base64
import uuid
from collections.abc import Callable, Mapping, Sequence

import httpx

from switch2_agent.decision_models.base import (
    Decision,
    DecisionModelError,
    post_json,
    read_probability,
)
from switch2_agent.decision_request import DecisionRequest

JEV_URL = "https://api.typesafe.ai/v1/systemone"
# 再現性のため、別名 (jev-latest) ではなくバージョン付きの ID に固定する
DEFAULT_JEV_MODEL = "jev-1.14.0"
API_KEY_ENV = "TYPESAFE_API_KEY"  # API キーを読む環境変数の名前
# 画像を含む要求に付けるヘッダー (値は "true")
IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
JPEG_ASSET_FORMAT = "jpg"  # asset の format。"jpeg" は "jpg" の別名
DEFAULT_TIMEOUT_S = 10.0  # 1 回の要求の HTTP のタイムアウト (秒)


def new_asset_reference() -> str:
    """asset の参照文字列として、新しい UUID (uuid.uuid4) の文字列を返す (公式ドキュメントは UUID を推奨している)。"""
    return str(uuid.uuid4())


def build_jev_headers(api_key: str, has_images: bool) -> dict[str, str]:
    """要求のヘッダーを返す。

    {"Authorization": "Bearer <api_key>", "Content-Type": "application/json"} に、
    has_images が真なら IMAGES_HEADER: "true" を足す。
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if has_images:
        headers[IMAGES_HEADER] = "true"
    return headers


def build_jev_payload(
    request: DecisionRequest,
    model: str = DEFAULT_JEV_MODEL,
    make_reference: Callable[[], str] = new_asset_reference,
) -> dict[str, object]:
    """request を Jev の要求の JSON (モジュールの docstring の形) にする。

    state は {"instructions": request.instructions, "game_notes": request.state_text,
    "operation_history": request.history_text, "frames": [...]} の順のキーを持つ。request.history_text が
    空文字なら operation_history を入れない。request.previous_operation_text は入れない。
    request.images の各画像に make_reference() で参照文字列を 1 つずつ作り (make_reference は呼ぶたびに
    異なる文字列を返す)、assets に {"type": "image", "format": JPEG_ASSET_FORMAT, "data_base64": <base64>}
    を 1 回だけ登録し、state の frames に request.images の順で {"label": ラベル (画像の label のまま),
    "image": 参照文字列} を置く。
    base64 は標準のアルファベットで、= のパディングを付け、改行も data: の接頭辞も付けない (base64.b64encode)。
    questions は request.questions の name をキーにし、値を {"type": "noul", "instructions": text} にする。
    画像が無ければ assets を入れない。
    """
    frames: list[dict[str, str]] = []
    assets: dict[str, dict[str, str]] = {}
    for image in request.images:
        reference = make_reference()
        frames.append({"label": image.label, "image": reference})
        assets[reference] = {
            "type": "image",
            "format": JPEG_ASSET_FORMAT,
            "data_base64": base64.b64encode(image.jpeg).decode("ascii"),
        }
    payload: dict[str, object] = {
        "model": model,
        "state": {
            "instructions": request.instructions,
            "game_notes": request.state_text,
            "frames": frames,
        },
        "questions": {
            question.name: {"type": "noul", "instructions": question.text}
            for question in request.questions
        },
    }
    if assets:
        payload["assets"] = assets
    return payload


def parse_jev_response(
    body: Mapping[str, object], names: Sequence[str]
) -> dict[str, float]:
    """応答の本文 body から、names (要求した問いの ID) それぞれの確率 (Noul の値 noul) を読む。

    body["answers"] は問いの ID → answer の map。各 answer の type が "noul" で、noul を確率にする
    (base.read_probability で検証する)。names のうち answer が無い問い、answers が object でない応答、
    answer が object でないか type が noul でないものは DecisionModelError にする。
    names に無い ID の answer は無視する。
    """
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise DecisionModelError("jev response has no answers object")
    probabilities: dict[str, float] = {}
    for name in names:
        if name not in answers:
            raise DecisionModelError(f"jev response has no answer for: {name}")
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise DecisionModelError(f"jev answer of {name} is not a noul: {answer!r}")
        probabilities[name] = read_probability(answer.get("noul"), name)
    return probabilities


class JevModel:
    """decision_models.base.DecisionModel の実装。TypeSafe Jev に httpx で要求を送る。

    属性:
        api_key     API キー (run は環境変数 API_KEY_ENV から読む)
        client      要求に使う httpx.Client。None を渡したら新しく作る (テストでは MockTransport の Client を渡す)
        timeout_s   1 回の要求の HTTP のタイムアウト (秒)
        model       要求の model
    """

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        model: str = DEFAULT_JEV_MODEL,
    ):
        """属性を入れる。client が None なら httpx.Client() を作る。"""
        self.api_key = api_key
        self.client = client if client is not None else httpx.Client()
        self.timeout_s = timeout_s
        self.model = model

    def decide(self, request: DecisionRequest) -> Decision:
        """request を JEV_URL に POST し、Decision(parse_jev_response の結果, refusals=0, raw_response=応答の本文) を返す。

        応答の本文は post_json の結果の JSON の object。
        ヘッダーは build_jev_headers(api_key, 画像があるか)、本文は build_jev_payload(request, model)。
        送信と応答の検証は base.post_json で行う (httpx の例外と 2xx 以外の応答は DecisionModelError)。
        """
        body = post_json(
            self.client,
            JEV_URL,
            build_jev_headers(self.api_key, bool(request.images)),
            build_jev_payload(request, self.model),
            self.timeout_s,
        )
        probabilities = parse_jev_response(
            body, [question.name for question in request.questions]
        )
        return Decision(probabilities=probabilities, refusals=0)
