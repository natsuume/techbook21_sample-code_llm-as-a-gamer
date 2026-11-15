import json

import pytest

from switch2_layers.middle.luna_usage import (
    CACHE_WRITE_30M_USD_PER_MILLION,
    CACHED_INPUT_USD_PER_MILLION,
    INPUT_USD_PER_MILLION,
    OUTPUT_USD_PER_MILLION,
    LunaUsage,
    estimate_cost_usd,
    read_usage,
    usage_to_json,
)

# 公式のリファレンスの usage の形 (cache_write_tokens_details も付く)
FULL_USAGE = {
    "input_tokens": 3000,
    "input_tokens_details": {
        "cached_tokens": 1024,
        "cache_write_tokens": 256,
        "cache_write_tokens_details": {"ttl_30m_tokens": 256, "ttl_12h_tokens": 0},
    },
    "output_tokens": 400,
    "output_tokens_details": {"reasoning_tokens": 12},
    "total_tokens": 3400,
}


# --- 単価 ---


def test_prices_follow_the_official_pricing():
    # gpt-6-luna の Standard の単価 (USD / 100 万トークン)
    assert INPUT_USD_PER_MILLION == 0.10
    assert CACHED_INPUT_USD_PER_MILLION == 0.01
    assert CACHE_WRITE_30M_USD_PER_MILLION == 0.125
    assert OUTPUT_USD_PER_MILLION == 0.50


# --- usage の読み取り ---


def test_read_usage_reads_nested_details():
    usage = read_usage({"status": "completed", "usage": FULL_USAGE})
    assert usage == LunaUsage(
        input_tokens=3000,
        cached_tokens=1024,
        cache_write_tokens=256,
        output_tokens=400,
        reasoning_tokens=12,
    )


@pytest.mark.parametrize("body", [{}, {"usage": None}, {"usage": "x"}, {"usage": []}])
def test_read_usage_without_usage_object_is_none(body):
    assert read_usage(body) is None


def test_read_usage_fills_missing_or_malformed_values_with_zero():
    usage = read_usage(
        {
            "usage": {
                "input_tokens": 500,
                # details が object でない
                "input_tokens_details": None,
                "output_tokens": True,  # bool は数として扱わない
                "output_tokens_details": {"reasoning_tokens": -3},
            }
        }
    )
    assert usage == LunaUsage(
        input_tokens=500,
        cached_tokens=0,
        cache_write_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
    )


def test_usage_to_json_keys_and_order():
    usage = LunaUsage(
        input_tokens=3000,
        cached_tokens=1024,
        cache_write_tokens=256,
        output_tokens=400,
        reasoning_tokens=12,
    )
    value = usage_to_json(usage)
    assert list(value) == [
        "input_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_tokens",
    ]
    assert value == {
        "input_tokens": 3000,
        "cached_tokens": 1024,
        "cache_write_tokens": 256,
        "output_tokens": 400,
        "reasoning_tokens": 12,
    }
    assert json.loads(json.dumps(value)) == value


# --- 概算の費用 ---


def usage(
    input_tokens: int,
    cached: int = 0,
    write: int = 0,
    output: int = 0,
    reasoning: int = 0,
) -> LunaUsage:
    return LunaUsage(
        input_tokens=input_tokens,
        cached_tokens=cached,
        cache_write_tokens=write,
        output_tokens=output,
        reasoning_tokens=reasoning,
    )


def test_cost_of_uncached_input_and_output():
    # 1,000,000 入力 × $0.10 + 1,000,000 出力 × $0.50
    assert estimate_cost_usd(usage(1_000_000, output=1_000_000)) == pytest.approx(0.60)


def test_cost_counts_cached_and_written_tokens_at_their_rates():
    # キャッシュに関わらない入力 = 3000 - 1024 - 256 = 1720
    # (1720 × 0.10 + 1024 × 0.01 + 256 × 0.125 + 400 × 0.50) / 1e6
    expected = (1720 * 0.10 + 1024 * 0.01 + 256 * 0.125 + 400 * 0.50) / 1_000_000
    assert estimate_cost_usd(
        usage(3000, cached=1024, write=256, output=400)
    ) == pytest.approx(expected)
    assert expected == pytest.approx(0.00041424)


def test_reasoning_tokens_are_not_added_again():
    # reasoning_tokens は output_tokens に含まれる
    without = estimate_cost_usd(usage(1000, output=500, reasoning=0))
    with_reasoning = estimate_cost_usd(usage(1000, output=500, reasoning=300))
    assert with_reasoning == pytest.approx(without)
    assert without == pytest.approx((1000 * 0.10 + 500 * 0.50) / 1_000_000)


def test_cost_of_zero_usage_is_zero():
    assert estimate_cost_usd(usage(0)) == 0.0


def test_inconsistent_usage_does_not_make_negative_input():
    # cached + write が input_tokens を超える (値どうしが食い違う) ときは、キャッシュに関わらない入力を 0 にする
    cost = estimate_cost_usd(usage(100, cached=80, write=40))
    assert cost == pytest.approx((80 * 0.01 + 40 * 0.125) / 1_000_000)


def test_cost_is_rounded_to_nine_decimals():
    cost = estimate_cost_usd(usage(1, output=1))
    # (0.10 + 0.50) / 1e6 = 6e-7
    assert cost == round(cost, 9)
    assert cost == pytest.approx(6e-7)
