"""OpenAI Decisions API (POST /v1/decisions) を Decision Model として使う。

要求と応答の形は公式ドキュメント (https://developers.openai.com/api/docs/guides/decisions と
https://developers.openai.com/api/reference/resources/decisions/methods/create、2026-11-12 に確認。
選択肢の問い (choice) の形は 2026-11-13 に確認) に合わせる。

要求の形:
    {
      "model": "gpt-6-luna",
      "input": [{"role": "user", "content": [
        {"type": "input_text", "text": <instructions>},
        {"type": "input_text", "text": <state_text>},
        {"type": "input_text", "text": <history_with_timeline(request)>},
        {"type": "input_text", "text": <画像 1 のラベル>},
        {"type": "input_image", "image_url": "data:image/jpeg;base64,<画像 1 の base64>"},
        ...
        {"type": "input_text", "text": <previous_operation_text>},
        {"type": "input_text", "text": <前回の操作の後の最初の画像のラベル>},
        {"type": "input_image", "image_url": "data:image/jpeg;base64,<その画像の base64>"},
        ...
      ]}],
      "questions": [
        {"type": "predicate", "name": <確率の問いの Question.name>, "instructions": <Question.text>},
        {"type": "choice", "name": <選択肢の問いの Question.name>, "instructions": <Question.text>,
         "choices": [{"value": <Choice.name>, "description": <Choice.description>}, ...]},
        ...
      ]
    }
questions は request.questions の順で、choices は Question.choices の順。
直近の応答と送った操作の履歴 (history_text) の後ろに、空行をはさんで系列情報 (timeline_text) を足した文
(decision_request.history_with_timeline) は、空文字なら置かない。previous_operation_text
(前回の操作を送った時点の区切りの文) は、前回の操作の後 (after_previous_operation が True) の最初の画像の
ラベルの直前に 1 回だけ置き、previous_operation_text が None か、前回の操作の後の画像が無ければ置かない。
応答の形: {"answers": [<answer>, ...], "model": ..., "usage": ...}。answer は次のどれか:
    {"type": "predicate", "name": ..., "probability": <0〜1>}                         確率の問いの答え
    {"type": "choice", "name": ..., "choice": <確率が最も高い選択肢の value>,
     "probabilities": [{"value": <選択肢の value>, "probability": <0〜1>}, ...], "confidence": ...}   選択肢の問いの答え
    {"type": "refusal", "name": ...}                                                  回答の拒否 (確率が無い)
選択肢の問いの答えは probabilities だけを読み、choice と confidence は使わない。

未確認 (公式ドキュメントに記載なし): Decisions API のエラーの本文の形、画像の枚数・大きさの上限、レート制限、タイムアウト。
2xx 以外の応答はすべて DecisionModelError にする。JPEG の data URL の MIME 型 (image/jpeg) は、
ドキュメントの PNG の例 (data:image/png;base64,...) からの類推で、JPEG の例は未確認。
"""

import base64
from collections.abc import Mapping, Sequence
from dataclasses import replace

import httpx

from switch2_agent.decision_models.base import (
    Decision,
    DecisionModelError,
    post_json,
    read_choice_probabilities,
    read_probability,
)
from switch2_agent.decision_request import DecisionRequest, history_with_timeline
from switch2_agent.questions import NONE_CHOICE, Question

DECISIONS_URL = "https://api.openai.com/v1/decisions"
DEFAULT_DECISIONS_MODEL = "gpt-6-luna"  # Decisions API で使える唯一のモデル
API_KEY_ENV = "OPENAI_API_KEY"  # API キーを読む環境変数の名前
DEFAULT_TIMEOUT_S = 10.0  # 1 回の要求の HTTP のタイムアウト (秒)


def jpeg_data_url(jpeg: bytes) -> str:
    """JPEG のバイト列を `data:image/jpeg;base64,<base64>` の data URL にする。

    base64 は標準のアルファベットで、= のパディングを付け、改行を入れない (base64.b64encode)。
    """
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def build_decisions_headers(api_key: str) -> dict[str, str]:
    """要求のヘッダー {"Authorization": "Bearer <api_key>", "Content-Type": "application/json"} を返す。"""
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def build_decisions_payload(
    request: DecisionRequest, model: str = DEFAULT_DECISIONS_MODEL
) -> dict[str, object]:
    """request を Decisions API の要求の JSON (モジュールの docstring の形) にする。

    input は user のメッセージ 1 つ。content には、instructions のテキスト、state_text のテキスト、
    履歴の欄のテキスト (decision_request.history_with_timeline(request)。history_text の後ろに、空行をはさんで
    系列情報 timeline_text を足した文。空文字なら置かない) の順に置き、続けて request.images の順に、ラベルのテキスト
    (input_text) と画像 (input_image、jpeg_data_url) を 1 組ずつ置く。after_previous_operation が True の
    最初の画像の組の直前 (ラベルのテキストの前) には、request.previous_operation_text が None でなければ、
    その文の input_text を 1 回だけ置く。各画像の本体は要求の中に 1 回だけ入る。
    questions は request.questions の順に、確率の問い (answer_type が "probability") なら
    {"type": "predicate", "name": name, "instructions": text}、選択肢の問い (answer_type が "choice") なら
    {"type": "choice", "name": name, "instructions": text,
     "choices": [{"value": Choice.name, "description": Choice.description}, ...]} (choices は Question.choices の順)。
    """
    content: list[dict[str, str]] = [
        {"type": "input_text", "text": request.instructions},
        {"type": "input_text", "text": request.state_text},
    ]
    history = history_with_timeline(request)
    if history:
        content.append({"type": "input_text", "text": history})
    separator_placed = False
    for image in request.images:
        if (
            image.after_previous_operation
            and not separator_placed
            and request.previous_operation_text is not None
        ):
            # 前回の操作を送った時点の区切りを、送った後の最初の画像の組の前に 1 回だけ置く
            content.append(
                {"type": "input_text", "text": request.previous_operation_text}
            )
            separator_placed = True
        # 画像の説明の専用のフィールドは無いため、画像の直前にラベルのテキストを置く
        content.append({"type": "input_text", "text": image.label})
        content.append({"type": "input_image", "image_url": jpeg_data_url(image.jpeg)})
    return {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "questions": [
            build_decisions_question(question) for question in request.questions
        ],
    }


def build_decisions_question(question: Question) -> dict[str, object]:
    """問い 1 つを、Decisions API の要求の questions の要素 (build_decisions_payload の docstring の形) にする。"""
    if question.answer_type == "choice":
        return {
            "type": "choice",
            "name": question.name,
            "instructions": question.text,
            "choices": [
                {"value": choice.name, "description": choice.description}
                for choice in question.choices
            ],
        }
    return {"type": "predicate", "name": question.name, "instructions": question.text}


def parse_decisions_response(
    body: Mapping[str, object], questions: Sequence[Question]
) -> Decision:
    """応答の本文 body から、questions (要求した問い) それぞれの答えを読み、Decision にする。

    body["answers"] の各 answer を name で照合する (answers の並びには頼らない)。
    - type が "predicate" の answer は、確率の問いの答え。probability を確率にし (base.read_probability で検証する)、
      Decision.probabilities に入れる
    - type が "choice" の answer は、選択肢の問いの答え。probabilities (value と probability を持つ object のリスト) を
      value → probability の対応にして base.read_choice_probabilities で読み、Decision.choices に入れる。
      choice と confidence は読まない
    - type が "refusal" の answer は、確率の問いなら確率 0.0、選択肢の問いなら NONE_CHOICE の確率を 1.0、ほかの
      選択肢を 0.0 とし (何もしない側に倒す)、refusals に数える
    questions のうち answer が無い問い、answers がリストでない応答、answer が object でないもの、name が文字列でない
    (name が無いものを含む) か type が predicate / choice / refusal 以外の answer、type が問いの答えの型と合わない
    answer (確率の問いに choice、選択肢の問いに predicate)、choice の answer で probabilities がリストでないか、
    その要素が object でないか value が文字列でないものは DecisionModelError にする。
    questions に無い name の answer は無視する。raw_response は None (DecisionsApiModel.decide が応答の本文を入れる)。
    """
    answers = body.get("answers")
    if not isinstance(answers, list):
        raise DecisionModelError("decisions response has no answers list")
    requested = {question.name: question for question in questions}
    probabilities: dict[str, float] = {}
    choices: dict[str, dict[str, float]] = {}
    refusals = 0
    for answer in answers:
        if not isinstance(answer, dict):
            raise DecisionModelError(f"decisions answer is not an object: {answer!r}")
        name = answer.get("name")
        if not isinstance(name, str):
            raise DecisionModelError(
                f"decisions answer has a name that is not a string: {name!r}"
            )
        answer_type = answer.get("type")
        if answer_type not in ("predicate", "choice", "refusal"):
            raise DecisionModelError(
                f"decisions answer of {name} has unexpected type: {answer_type!r}"
            )
        question = requested.get(name)
        if question is None:
            continue
        if answer_type == "refusal":
            refusals += 1
            if question.answer_type == "choice":
                choices[name] = refused_choice_probabilities(question)
            else:
                probabilities[name] = 0.0
        elif question.answer_type == "choice":
            if answer_type != "choice":
                raise DecisionModelError(
                    f"decisions answer of choice question {name} is {answer_type!r}"
                )
            choices[name] = read_choice_probabilities(
                choice_values(answer, name), question
            )
        else:
            if answer_type != "predicate":
                raise DecisionModelError(
                    f"decisions answer of probability question {name} is {answer_type!r}"
                )
            probabilities[name] = read_probability(answer.get("probability"), name)
    missing = [
        question.name
        for question in questions
        if question.name not in probabilities and question.name not in choices
    ]
    if missing:
        raise DecisionModelError(
            f"decisions response has no answer for: {' '.join(missing)}"
        )
    return Decision(probabilities=probabilities, choices=choices, refusals=refusals)


def choice_values(answer: Mapping[str, object], name: str) -> dict[str, object]:
    """choice の answer の probabilities (value と probability を持つ object のリスト) を、value → probability の辞書にする。

    probabilities がリストでないか、その要素が object でないか value が文字列でなければ DecisionModelError にする。
    name は例外のメッセージに使う問いの name。
    """
    items = answer.get("probabilities")
    if not isinstance(items, list):
        raise DecisionModelError(
            f"decisions answer of {name} has no probabilities list: {items!r}"
        )
    values: dict[str, object] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("value"), str):
            raise DecisionModelError(
                f"decisions answer of {name} has a malformed probability: {item!r}"
            )
        values[item["value"]] = item.get("probability")
    return values


def refused_choice_probabilities(question: Question) -> dict[str, float]:
    """回答を拒否された選択肢の問いの答えとして、NONE_CHOICE を 1.0、ほかの選択肢を 0.0 にした辞書を返す。"""
    return {
        choice.name: 1.0 if choice.name == NONE_CHOICE else 0.0
        for choice in question.choices
    }


class DecisionsApiModel:
    """decision_models.base.DecisionModel の実装。OpenAI Decisions API に httpx で要求を送る。

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
        model: str = DEFAULT_DECISIONS_MODEL,
    ):
        """属性を入れる。client が None なら httpx.Client() を作る。"""
        self.api_key = api_key
        self.client = client if client is not None else httpx.Client()
        self.timeout_s = timeout_s
        self.model = model

    def decide(self, request: DecisionRequest) -> Decision:
        """request を DECISIONS_URL に POST し、parse_decisions_response の結果の raw_response に応答の本文を入れて返す。

        応答の本文は post_json の結果の JSON の object。
        ヘッダーは build_decisions_headers、本文は build_decisions_payload(request, model)。
        送信と応答の検証は base.post_json で行う (httpx の例外と 2xx 以外の応答は DecisionModelError)。
        """
        body = post_json(
            self.client,
            DECISIONS_URL,
            build_decisions_headers(self.api_key),
            build_decisions_payload(request, self.model),
            self.timeout_s,
        )
        decision = parse_decisions_response(body, request.questions)
        return replace(decision, raw_response=body)
