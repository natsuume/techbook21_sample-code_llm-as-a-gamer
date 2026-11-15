"""gpt-6-luna の 1 回の呼び出しの usage と、概算の費用 (USD) の計算。I/O を扱わない。

応答の本文の usage の形 (Responses API。使う部分):
    {"input_tokens", "input_tokens_details": {"cached_tokens", "cache_write_tokens"},
     "output_tokens", "output_tokens_details": {"reasoning_tokens"}}
input_tokens は cached_tokens (キャッシュから読んだ分) と cache_write_tokens (キャッシュに書いた分) を含む。
output_tokens は reasoning_tokens を含む (推論のトークンは出力の単価で数える)。

概算の費用 (USD):
    (キャッシュに関わらない入力 × INPUT_USD_PER_MILLION
     + cached_tokens × CACHED_INPUT_USD_PER_MILLION
     + cache_write_tokens × CACHE_WRITE_30M_USD_PER_MILLION
     + output_tokens × OUTPUT_USD_PER_MILLION) / 1,000,000
    キャッシュに関わらない入力 = input_tokens - cached_tokens - cache_write_tokens
要求ではキャッシュの寿命 (ttl) を指定しない (既定の 30 分) ため、書き込みはすべて 30 分の書き込みの単価で数える。
入力が 272K トークンを超える要求の割増は数えない (middle の要求はそれより十分に短い)。
"""

from collections.abc import Mapping
from dataclasses import dataclass

# gpt-6-luna の Standard の単価 (USD / 100 万トークン)。
# 2026-11-15 に公式の料金表 (https://developers.openai.com/api/docs/pricing) で確認
INPUT_USD_PER_MILLION = 0.10
CACHED_INPUT_USD_PER_MILLION = 0.01
CACHE_WRITE_30M_USD_PER_MILLION = 0.125
OUTPUT_USD_PER_MILLION = 0.50
TOKENS_PER_MILLION = 1_000_000
COST_DECIMALS = 9  # 概算の費用を丸める小数の桁数


@dataclass(frozen=True)
class LunaUsage:
    """gpt-6-luna の 1 回の呼び出しのトークン数。

    input_tokens         入力のトークン数 (cached_tokens と cache_write_tokens を含む)
    cached_tokens        キャッシュから読んだ入力のトークン数 (usage.input_tokens_details.cached_tokens)
    cache_write_tokens   キャッシュに書いた入力のトークン数 (usage.input_tokens_details.cache_write_tokens)
    output_tokens        出力のトークン数 (reasoning_tokens を含む)
    reasoning_tokens     推論のトークン数 (usage.output_tokens_details.reasoning_tokens)
    """

    input_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int


def read_usage(body: Mapping[str, object]) -> LunaUsage | None:
    """応答の本文 body の usage を LunaUsage にする。

    body["usage"] が object でなければ (無い・null を含む) None を返す。
    各値は 0 以上の整数 (bool を除く) ならその値、無いか型が合わなければ 0 にする。
    input_tokens_details・output_tokens_details が object でなければ、その中の値をすべて 0 にする。
    例外は出さない (usage は記録のためだけに読み、応答の読み取りの成否に関わらせない)。
    """
    raise NotImplementedError


def estimate_cost_usd(usage: LunaUsage) -> float:
    """usage の概算の費用 (USD) を、モジュールの docstring の式で計算し、小数第 COST_DECIMALS 位に丸めて返す。

    キャッシュに関わらない入力が負になる (usage の値どうしが食い違う) ときは、0 として数える。
    """
    raise NotImplementedError


def usage_to_json(usage: LunaUsage) -> dict[str, int]:
    """usage を middle/calls.jsonl の行の usage の形
    {"input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens"} (この順) にする。
    """
    raise NotImplementedError
