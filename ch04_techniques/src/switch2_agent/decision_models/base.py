"""Decision Model の型と、2 つの API に共通の HTTP の送信と応答の検証。

Pipeline は DecisionModel の型だけに依存し、API の違いを知らない。
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx
from switch2_remote.error_message import describe_error

from switch2_agent.decision_request import DecisionRequest

# 2xx 以外の応答の例外のメッセージに含める、応答の本文の先頭の文字数
ERROR_BODY_PREVIEW_CHARS = 500


class DecisionModelError(RuntimeError):
    """Decision Model の呼び出しの失敗。

    HTTP の失敗 (接続できない、タイムアウト、2xx 以外の応答)、形の違う応答、
    要求のすべての問いの答えがそろわない応答、範囲外 (0〜1 の外) の確率を表す。
    """


@dataclass(frozen=True)
class Decision:
    """Decision Model の応答 1 回分。

    probabilities   問いの name → 真である確率 (0〜1)。要求のすべての問いの name を含む
    refusals        回答を拒否された問いの数。拒否された問いの確率は 0.0 として probabilities に入れる
                    (拒否を返さない API では常に 0)
    """

    probabilities: dict[str, float]
    refusals: int


class DecisionModel(Protocol):
    """要求を送り、問いごとに真である確率を受け取るモデル。"""

    def decide(self, request: DecisionRequest) -> Decision:
        """request を送り、すべての問いの確率を持つ Decision を返す。

        HTTP の失敗、形の違う応答、要求のすべての問いの答えがそろわない応答は DecisionModelError にする。
        """
        ...


def post_json(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, object],
    timeout_s: float,
) -> dict[str, object]:
    """url に payload を JSON で POST し、応答の本文の JSON の object を返す。

    - httpx の例外 (httpx.HTTPError。接続できない、タイムアウトなど) は DecisionModelError にする
    - 2xx 以外の応答は、ステータスコードと応答の本文の先頭 ERROR_BODY_PREVIEW_CHARS 文字を含む
      DecisionModelError にする
    - 本文が JSON でないか、JSON の object (dict) でなければ DecisionModelError にする
    """
    try:
        response = client.post(
            url, headers=dict(headers), json=payload, timeout=timeout_s
        )
    except httpx.HTTPError as e:
        raise DecisionModelError(f"request to {url} failed: {describe_error(e)}") from e
    if not response.is_success:
        preview = response.text[:ERROR_BODY_PREVIEW_CHARS]
        raise DecisionModelError(
            f"{url} returned HTTP {response.status_code}: {preview}"
        )
    try:
        body = response.json()
    except ValueError as e:
        raise DecisionModelError(f"{url} returned a body that is not JSON") from e
    if not isinstance(body, dict):
        raise DecisionModelError(f"{url} returned JSON that is not an object")
    return body


def read_probability(value: object, name: str) -> float:
    """応答の確率の値 value を float にして返す。name は例外のメッセージに使う問いの name。

    value が数 (int か float。bool は除く) でないか、有限でないか、0〜1 の範囲の外なら DecisionModelError にする。
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DecisionModelError(f"probability of {name} is not a number: {value!r}")
    probability = float(value)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise DecisionModelError(f"probability of {name} is out of 0..1: {value!r}")
    return probability
