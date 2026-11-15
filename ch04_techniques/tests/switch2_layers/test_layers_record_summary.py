import math

import pytest

from switch2_layers.middle.record_summary import (
    MAX_SUMMARY_LINES,
    MAX_TEXT_CHARS,
    NO_RECORDS,
    describe_operation,
    summarize_operation_rows,
    summarize_surface_rows,
)

ZERO_STICKS = {"LS": [0.0, 0.0], "RS": [0.0, 0.0]}


def lines_of(lines: list[str]) -> str:
    """行を改行 1 つでつないだ文 (末尾に改行を付けない) を返す。"""
    return "\n".join(lines)


def operation(
    t: object,
    source: str = "apply",
    held: list | None = None,
    taps: list | None = None,
    sticks: dict | None = None,
    ok: bool = True,
    error: str | None = None,
) -> dict:
    """operations.jsonl の 1 行 (records.operation_log.operation_row の形)。"""
    return {
        "t": t,
        "source": source,
        "held": held or [],
        "taps": taps or [],
        "sticks": sticks or ZERO_STICKS,
        "ok": ok,
        "error": error,
    }


def surface(
    t_received: object,
    scene_changed: bool = False,
    info_labels: list | None = None,
    deep_requests: list | None = None,
    blocked_buttons: list | None = None,
    error: str | None = None,
) -> dict:
    """surface.jsonl の 1 行 (surface.surface_record.surface_row の形のうち、まとめに使う部分と t_sent)。"""
    return {
        "t_sent": 0.0 if not isinstance(t_received, float) else t_received - 0.1,
        "t_received": t_received,
        "action": None,
        "scene_changed": scene_changed,
        "info_labels": info_labels or [],
        "deep_requests": deep_requests or [],
        "blocked_buttons": blocked_buttons or [],
        "boxes": [],
        "wait_s": 0.2,
        "error": error,
    }


# --- 操作の説明 ---


def test_describe_operation_lists_all_parts():
    row = operation(
        1.0,
        held=["A", "B"],
        taps=["X"],
        sticks={"LS": [0.5, -1.0], "RS": [-0.25, 0.0]},
    )
    assert describe_operation(row) == (
        "押し続け: A, B / 短押し: X / 左スティック: (0.50, -1.00) / 右スティック: (-0.25, 0.00)"
    )


def test_describe_neutral_operation():
    assert describe_operation(operation(1.0)) == "何も押さない"
    assert describe_operation({}) == "何も押さない"


def test_describe_operation_skips_malformed_values():
    row = {
        "held": "A",
        "taps": [1, "Y", None],
        "sticks": {"LS": [True, 1.0], "RS": "x"},
    }
    assert describe_operation(row) == "短押し: Y"
    assert describe_operation({"sticks": None, "held": None, "taps": {}}) == (
        "何も押さない"
    )
    assert describe_operation({"sticks": {"LS": [math.nan, 1.0]}}) == "何も押さない"


# --- operations.jsonl のまとめ ---


def test_operation_summary_groups_same_consecutive_operations():
    up_with_a = {"held": ["A"], "sticks": {"LS": [0.0, 1.0], "RS": [0.0, 0.0]}}
    rows = [
        operation(10.0, **up_with_a),
        operation(10.5, **up_with_a),
        operation(11.0, **up_with_a),
        operation(11.5, taps=["B"]),
        operation(12.0, ok=False, error="OSError: bridge closed"),
        operation(12.5, source="neutral"),
        operation(13.0, source="stop"),
    ]
    assert summarize_operation_rows(rows) == lines_of(
        [
            "- t=10.000〜t=11.000 (3 回): 押し続け: A / 左スティック: (0.00, 1.00)",
            "- t=11.500: 短押し: B",
            "- t=12.000: 何も押さない (送信に失敗: OSError: bridge closed)",
            "- t=12.500: 中立に戻した",
            "- t=13.000: 止めるために中立にした",
        ]
    )


def test_failed_and_successful_same_operation_are_not_grouped():
    rows = [
        operation(1.0, taps=["A"]),
        operation(2.0, taps=["A"], ok=False, error=None),
        operation(3.0, taps=["A"]),
    ]
    assert summarize_operation_rows(rows) == lines_of(
        [
            "- t=1.000: 短押し: A",
            "- t=2.000: 短押し: A (送信に失敗)",
            "- t=3.000: 短押し: A",
        ]
    )


def test_same_operation_after_a_different_one_starts_a_new_group():
    rows = [
        operation(1.0, taps=["A"]),
        operation(2.0, taps=["B"]),
        operation(3.0, taps=["A"]),
        operation(4.0, taps=["A"]),
    ]
    assert summarize_operation_rows(rows) == lines_of(
        [
            "- t=1.000: 短押し: A",
            "- t=2.000: 短押し: B",
            "- t=3.000〜t=4.000 (2 回): 短押し: A",
        ]
    )


def test_operation_rows_with_invalid_time_are_skipped():
    rows = [
        operation(None, taps=["A"]),
        operation("1.0", taps=["A"]),
        operation(True, taps=["A"]),
        operation(math.nan, taps=["A"]),
        operation(math.inf, taps=["A"]),
        {"source": "apply"},
        operation(5.0, taps=["B"]),
    ]
    assert summarize_operation_rows(rows) == "- t=5.000: 短押し: B"


@pytest.mark.parametrize(
    "rows", [[], [operation(None)], [{"unexpected": 1}]], ids=["empty", "no-t", "junk"]
)
def test_operation_summary_without_usable_rows(rows):
    assert NO_RECORDS == "(記録なし)"
    assert summarize_operation_rows(rows) == NO_RECORDS


def test_operation_summary_keeps_only_newest_lines():
    assert MAX_SUMMARY_LINES == 30
    # A と B を交互に押す (まとめられない) 35 行
    rows = [operation(float(i), taps=["A" if i % 2 == 0 else "B"]) for i in range(35)]
    lines = summarize_operation_rows(rows).split("\n")
    assert lines[0] == "(古い 5 行を省いた)"
    assert len(lines) == 1 + 30
    assert lines[1] == "- t=5.000: 短押し: B"
    assert lines[-1] == "- t=34.000: 短押し: A"


def test_long_error_is_clipped():
    assert MAX_TEXT_CHARS == 200
    rows = [operation(1.0, ok=False, error="e" * 300)]
    assert summarize_operation_rows(rows) == (
        "- t=1.000: 何も押さない (送信に失敗: " + "e" * 200 + "…)"
    )


# --- surface.jsonl のまとめ ---


def test_surface_summary_counts_requests_and_lists_notable_rows():
    rows = [
        surface(10.2),
        surface(
            10.8,
            scene_changed=True,
            info_labels=[
                {"name": "menu_open", "probability": 0.923},
                {"name": "dialog", "probability": 0.4},
            ],
            deep_requests=["tips"],
            blocked_buttons=["HOME"],
        ),
        surface(11.5, error="RuntimeError: model failed"),
    ]
    assert summarize_surface_rows(rows) == lines_of(
        [
            "surface の要求 3 回 (失敗 1 回)",
            (
                "- t=10.800: 場面転換を検知 / 情報ラベル: menu_open 0.92, dialog 0.40"
                " / deep を求めた: tips / 押せないボタンを外した: HOME"
            ),
            "- t=11.500: 失敗: RuntimeError: model failed",
        ]
    )


def test_surface_summary_without_notable_rows_is_only_the_count():
    assert summarize_surface_rows([surface(1.0), surface(2.0)]) == (
        "surface の要求 2 回 (失敗 0 回)"
    )


def test_surface_summary_skips_malformed_values():
    rows = [
        surface(
            3.0,
            info_labels=[
                {"name": "ok_label", "probability": 1},
                {"name": 1, "probability": 0.5},
                {"name": "no_probability"},
                {"name": "bool_probability", "probability": True},
                "not an object",
            ],
            deep_requests=["advice", 3],
            blocked_buttons=[None],
        ),
        # 空文字列の error は失敗に数えない
        surface(4.0, error=""),
        {"t_received": 5.0, "scene_changed": "yes", "info_labels": "x", "error": 7},
    ]
    assert summarize_surface_rows(rows) == lines_of(
        [
            "surface の要求 3 回 (失敗 0 回)",
            "- t=3.000: 情報ラベル: ok_label 1.00 / deep を求めた: advice",
        ]
    )


@pytest.mark.parametrize(
    "rows",
    [[], [surface(None)], [surface("1.0")], [{"t_sent": 1.0}]],
    ids=["empty", "none", "string", "no-t_received"],
)
def test_surface_summary_without_usable_rows(rows):
    assert summarize_surface_rows(rows) == NO_RECORDS


def test_surface_summary_keeps_only_newest_lines():
    rows = [surface(float(i), error=f"E{i}") for i in range(35)]
    lines = summarize_surface_rows(rows).split("\n")
    assert lines[0] == "surface の要求 35 回 (失敗 35 回)"
    assert lines[1] == "(古い 5 行を省いた)"
    assert len(lines) == 2 + 30
    assert lines[2] == "- t=5.000: 失敗: E5"
    assert lines[-1] == "- t=34.000: 失敗: E34"
