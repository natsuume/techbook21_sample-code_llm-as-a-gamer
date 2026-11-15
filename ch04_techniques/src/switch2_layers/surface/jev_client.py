"""TypeSafe Jev (System One、POST /v1/systemone) の HTTP の呼び出しと、応答の読み取り。

要求と応答の形は公式ドキュメント (https://docs.typesafe.ai/api/system-one.md、https://docs.typesafe.ai/images.md、
https://docs.typesafe.ai/primitives/box.md) に合わせる。

要求 (JSON の本文。画像は base64 で入れ、multipart は使わない):
    {
      "model": "jev-1.14.0",
      "state": <state (surface.jev_surface が作る)>,
      "questions": {<問いの ID>: <問い>, ...},
      "assets": {<参照文字列>: {"type": "image", "format": "jpg", "data_base64": <base64>}, ...}
    }
    問いは、choice なら {"type": "choice", "instructions": 文, "criteria": {選択肢の名前: 説明, ...}}、
    noul なら {"type": "noul", "instructions": 文}、box なら {"type": "box", "instructions": 文}。
    画像は assets に 1 回だけ登録し、state の中に参照文字列を 1 回だけ置く (参照文字列は書くたびに同じ画像が
    足され、どこからも参照しない asset は 400 になるため)。画像が無ければ assets を入れない。
ヘッダー: Authorization: Bearer <API キー>、Content-Type: application/json。画像か box の問いを含む要求には
    TypeSafe-Danger-Zone-Images: true を足す。
応答: {"model": ..., "answers": {<問いの ID>: <答え>, ...}, "usage": ...}。答えは
    noul    {"type": "noul", "noul": 0〜1}
    choice  {"type": "choice", "choice": ..., "confidence": ..., "probabilities": {選択肢の名前: 0〜1, ...}}
            (probabilities だけを読み、choice と confidence は使わない)
    box     {"type": "box", "box": {"top", "bottom", "left", "right"}, "confidence": 0〜1 か null}
            (座標は画像の高さ・幅に対する割合。公式ドキュメントでは left < right、top < bottom)
失敗 (どれも JevError): 接続できない・タイムアウト・2xx 以外の応答、JSON として読めない本文、形の違う応答、
要求した問いの答えが無い・型が違う、確率が数でない・有限でない・0〜1 の外、box の座標が数でない、確信度が null でも
数でもない。
box の座標と確信度が数でも、座標か確信度が 0〜1 の外 (有限でない値を含む) か、left >= right か top >= bottom
(幅か高さが 0 以下) なら、失敗にせず「使える box が無い」(None) とする。Jev は対象が画像に無くても box を返すため、
使えない box 1 つで要求全体を失敗にしない (ほかの問いの答えは使う)。
1 回の ask で送る要求は 1 回だけ (再試行しない。surface のループが次の要求で送り直す)。
"""

import base64
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard

import httpx
from switch2_remote.error_message import describe_error

from switch2_layers.surface.surface_questions import QuestionType, SurfaceQuestion

JEV_URL = "https://api.typesafe.ai/v1/systemone"
# 再現性のため、別名 (jev-latest) ではなくバージョン付きの ID に固定する
DEFAULT_JEV_MODEL = "jev-1.14.0"
API_KEY_ENV = "TYPESAFE_API_KEY"  # API キーを読む環境変数の名前
IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"  # 画像か box の問いを含む要求に付けるヘッダー (値は "true")
JPEG_ASSET_FORMAT = "jpg"
DEFAULT_TIMEOUT_S = 10.0  # 1 回の要求の HTTP のタイムアウト (秒)
# 2xx 以外の応答の例外のメッセージに含める、応答の本文の先頭の文字数
ERROR_BODY_PREVIEW_CHARS = 500
# box の答えの座標のキー
BOX_EDGES = ("top", "bottom", "left", "right")


class JevError(RuntimeError):
    """Jev の呼び出しの失敗 (モジュールの docstring の「失敗」)。surface のループは surface の失敗として数える。"""


@dataclass(frozen=True)
class JevImage:
    """要求に入れる画像 1 枚。

    reference   参照文字列 (assets のキー。state の中に 1 回だけ置く)
    jpeg        JPEG のバイト列
    """

    reference: str
    jpeg: bytes


@dataclass(frozen=True)
class JevBox:
    """box の問いの答え。座標は画像の幅・高さに対する割合 (0〜1)。

    top          上端
    bottom       下端
    left         左端
    right        右端
    confidence   確信度 (0〜1)。Jev が確信度を返さないとき None
    """

    top: float
    bottom: float
    left: float
    right: float
    confidence: float | None


@dataclass(frozen=True)
class JevAnswers:
    """1 回の要求の答え。要求したすべての問いの答えを、問いの型ごとに持つ。

    choices   choice の問いの ID → (選択肢の名前 → 確率)。問いのすべての選択肢を含む
    nouls     noul の問いの ID → 真である確率
    boxes     box の問いの ID → JevBox。使えない box (座標か確信度が 0〜1 の外、幅か高さが 0 以下) は None
    """

    choices: Mapping[str, Mapping[str, float]]
    nouls: Mapping[str, float]
    boxes: Mapping[str, JevBox | None]


def new_asset_reference() -> str:
    """asset の参照文字列として、新しい UUID (uuid.uuid4) の文字列を返す (公式ドキュメントは UUID を勧めている)。"""
    return str(uuid.uuid4())


def needs_images_header(
    questions: Sequence[SurfaceQuestion], images: Sequence[JevImage]
) -> bool:
    """images が 1 枚以上あるか、questions に box の問いがあれば True を返す。"""
    if images:
        return True
    return any(question.question_type is QuestionType.BOX for question in questions)


def build_headers(api_key: str, with_images: bool) -> dict[str, str]:
    """要求のヘッダーを返す。

    {"Authorization": "Bearer <api_key>", "Content-Type": "application/json"} に、with_images が True なら
    IMAGES_HEADER: "true" を足す。
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if with_images:
        headers[IMAGES_HEADER] = "true"
    return headers


def build_question_json(question: SurfaceQuestion) -> dict[str, object]:
    """問い 1 つを、要求の questions の値 (モジュールの docstring の形) にする。criteria は question.choices の順。"""
    value: dict[str, object] = {
        "type": question.question_type.value,
        "instructions": question.instructions,
    }
    if question.question_type is QuestionType.CHOICE:
        value["criteria"] = {
            choice.name: choice.description for choice in question.choices
        }
    return value


def build_assets(images: Sequence[JevImage]) -> dict[str, dict[str, str]]:
    """images を assets の map にする。

    キーは参照文字列、値は {"type": "image", "format": JPEG_ASSET_FORMAT, "data_base64": <base64>}。
    base64 は標準のアルファベットで = のパディングを付け、改行も data: の接頭辞も付けない (base64.b64encode)。
    同じ参照文字列が 2 回あれば ValueError。
    """
    assets: dict[str, dict[str, str]] = {}
    for image in images:
        if image.reference in assets:
            raise ValueError(f"duplicate asset reference: {image.reference}")
        assets[image.reference] = {
            "type": "image",
            "format": JPEG_ASSET_FORMAT,
            "data_base64": base64.b64encode(image.jpeg).decode("ascii"),
        }
    return assets


def build_payload(
    state: Mapping[str, object],
    questions: Sequence[SurfaceQuestion],
    images: Sequence[JevImage],
    model: str = DEFAULT_JEV_MODEL,
) -> dict[str, object]:
    """要求の本文を、キー model・state・questions・assets の順で返す (画像が無ければ assets を入れない)。

    questions は questions の順に、ID をキーにして build_question_json の値を置く。同じ ID が 2 回あれば ValueError。
    state はそのまま入れる (画像の参照文字列を state の中に置くのは呼び出し側)。
    """
    questions_json: dict[str, object] = {}
    for question in questions:
        if question.name in questions_json:
            raise ValueError(f"duplicate question id: {question.name}")
        questions_json[question.name] = build_question_json(question)
    payload: dict[str, object] = {
        "model": model,
        "state": state,
        "questions": questions_json,
    }
    if images:
        payload["assets"] = build_assets(images)
    return payload


def _is_number(value: object) -> TypeGuard[int | float]:
    """value が数 (int か float。bool は除く) なら True。"""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_fraction(value: float) -> bool:
    """value が 0〜1 の有限の値なら True (nan と inf は False)。"""
    return math.isfinite(value) and 0.0 <= value <= 1.0


def read_probability(value: object, name: str) -> float:
    """応答の確率の値 value を float にして返す。name は例外のメッセージに使う問い (か選択肢) の ID。

    value が数 (int か float。bool は除く) でないか、有限でないか、0〜1 の範囲の外なら JevError。
    """
    if not _is_number(value):
        raise JevError(f"probability of {name} is not a number: {value!r}")
    probability = float(value)
    if not _is_fraction(probability):
        raise JevError(f"probability of {name} is out of 0..1: {value!r}")
    return probability


def read_box_answer(answer: Mapping[str, object], name: str) -> JevBox | None:
    """box の答え answer を JevBox にする。

    answer["box"] が object で、top・bottom・left・right がどれも数 (bool は除く) であること。confidence は null
    (None か、キーが無い) か数であること。どれかに合わなければ JevError。
    これらに合っていても、座標か確信度が 0〜1 の外 (有限でない値を含む) か、left >= right か top >= bottom なら
    None (使える box が無い) を返す。
    """
    edges = answer.get("box")
    if not isinstance(edges, dict):
        raise JevError(f"box answer of {name} has no box object: {answer!r}")
    values: dict[str, float] = {}
    for edge in BOX_EDGES:
        value = edges.get(edge)
        if not _is_number(value):
            raise JevError(f"box answer of {name} has no number for {edge}: {value!r}")
        values[edge] = float(value)
    confidence_value = answer.get("confidence")
    confidence: float | None = None
    if confidence_value is not None:
        if not _is_number(confidence_value):
            raise JevError(
                f"box confidence of {name} is not a number: {confidence_value!r}"
            )
        confidence = float(confidence_value)
    if not all(_is_fraction(value) for value in values.values()):
        return None
    if confidence is not None and not _is_fraction(confidence):
        return None
    if values["left"] >= values["right"] or values["top"] >= values["bottom"]:
        return None
    return JevBox(
        top=values["top"],
        bottom=values["bottom"],
        left=values["left"],
        right=values["right"],
        confidence=confidence,
    )


def parse_answers(
    body: Mapping[str, object], questions: Sequence[SurfaceQuestion]
) -> JevAnswers:
    """応答の本文 body から、questions それぞれの答えを読んで JevAnswers にする。

    body["answers"] が object でない、questions の ID の答えが無い、答えが object でない、答えの type が問いの型と
    合わない、choice の probabilities が object でないかどれかの選択肢の確率が無い、確率や box が不正 (read_probability・
    read_box_answer) なら JevError。questions に無い ID の答えと、問いに無い選択肢の名前は無視する。
    box の問いの答えは read_box_answer の値 (使える box が無ければ None) を boxes に入れる。
    選択肢の確率の合計は確かめない (公式ドキュメントは「およそ 1」としか書いていない)。
    """
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise JevError("jev response has no answers object")
    choices: dict[str, dict[str, float]] = {}
    nouls: dict[str, float] = {}
    boxes: dict[str, JevBox | None] = {}
    for question in questions:
        name = question.name
        if name not in answers:
            raise JevError(f"jev response has no answer for: {name}")
        answer = answers[name]
        if not isinstance(answer, dict):
            raise JevError(f"jev answer of {name} is not an object: {answer!r}")
        if answer.get("type") != question.question_type.value:
            raise JevError(
                f"jev answer of {name} is not a {question.question_type.value}: "
                f"{answer!r}"
            )
        if question.question_type is QuestionType.CHOICE:
            choices[name] = _read_choice_probabilities(answer, question)
        elif question.question_type is QuestionType.NOUL:
            nouls[name] = read_probability(answer.get("noul"), name)
        else:
            boxes[name] = read_box_answer(answer, name)
    return JevAnswers(choices=choices, nouls=nouls, boxes=boxes)


def _read_choice_probabilities(
    answer: Mapping[str, object], question: SurfaceQuestion
) -> dict[str, float]:
    """choice の答え answer の probabilities から、question の選択肢の順の (選択肢の名前 → 確率) を返す。

    probabilities が object でないか、どれかの選択肢の確率が無ければ JevError。確率は read_probability で読む
    (例外のメッセージの名前は `<問いの ID>.<選択肢の名前>`)。
    """
    values = answer.get("probabilities")
    if not isinstance(values, dict):
        raise JevError(
            f"jev answer of {question.name} has no probabilities object: {answer!r}"
        )
    probabilities: dict[str, float] = {}
    for choice in question.choices:
        if choice.name not in values:
            raise JevError(
                f"jev answer of {question.name} has no probability for: {choice.name}"
            )
        probabilities[choice.name] = read_probability(
            values[choice.name], f"{question.name}.{choice.name}"
        )
    return probabilities


class JevClient:
    """TypeSafe Jev に httpx で要求を送る。

    api_key     API キー (run は環境変数 API_KEY_ENV から読む)
    client      要求に使う httpx.Client。None なら新しく作る (テストでは MockTransport の Client を渡す)
    timeout_s   1 回の要求の HTTP のタイムアウト (秒)
    model       要求の model
    url         要求の送り先
    """

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        model: str = DEFAULT_JEV_MODEL,
        url: str = JEV_URL,
    ) -> None:
        """属性を持つ。client が None なら httpx.Client() を作る (このときは close で閉じる)。"""
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.model = model
        self.url = url
        # 自分で作った Client だけを close で閉じる
        self._owns_client = client is None
        self.client = client if client is not None else httpx.Client()
        self._closed = False

    def post(
        self, payload: Mapping[str, object], with_images: bool
    ) -> dict[str, object]:
        """payload を url に JSON で 1 回だけ POST し、応答の本文の JSON の object を返す。

        ヘッダーは build_headers(api_key, with_images)、タイムアウトは timeout_s。
        httpx の例外 (httpx.HTTPError)、2xx 以外の応答 (ステータスコードと本文の先頭 ERROR_BODY_PREVIEW_CHARS 文字を
        メッセージに含める)、JSON でない本文、JSON の object でない本文は JevError にする。
        """
        try:
            response = self.client.post(
                self.url,
                headers=build_headers(self.api_key, with_images),
                json=payload,
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as error:
            raise JevError(
                f"request to {self.url} failed: {describe_error(error)}"
            ) from error
        if not response.is_success:
            preview = response.text[:ERROR_BODY_PREVIEW_CHARS]
            raise JevError(
                f"{self.url} returned HTTP {response.status_code}: {preview}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise JevError(f"{self.url} returned a body that is not JSON") from error
        if not isinstance(body, dict):
            raise JevError(f"{self.url} returned JSON that is not an object")
        return body

    def ask(
        self,
        state: Mapping[str, object],
        questions: Sequence[SurfaceQuestion],
        images: Sequence[JevImage],
    ) -> JevAnswers:
        """build_payload(state, questions, images, model) を post し、parse_answers(本文, questions) を返す。

        with_images は needs_images_header(questions, images)。
        """
        payload = build_payload(state, questions, images, self.model)
        body = self.post(payload, needs_images_header(questions, images))
        return parse_answers(body, questions)

    def close(self) -> None:
        """__init__ で作った httpx.Client を閉じる。渡された client は閉じない。2 回目以降は何もしない。"""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            self.client.close()
