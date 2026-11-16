from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.progress_text import (
    NO_PROGRESS_TEXT,
    describe_progress_values,
)


def test_values_are_two_sentences_like_the_issue():
    state = ProgressState(t=85.2, static_s=12.4, no_novel_s=85.0, window_s=300.0)
    assert describe_progress_values(state) == (
        "画面が変わっていない: 12.4 秒\n直近 300 秒に見た画面だけが出ている: 85.0 秒"
    )


def test_seconds_have_one_decimal_and_window_is_compact():
    state = ProgressState(t=3.0, static_s=0.0, no_novel_s=61.26, window_s=12.5)
    assert describe_progress_values(state) == (
        "画面が変わっていない: 0.0 秒\n直近 12.5 秒に見た画面だけが出ている: 61.3 秒"
    )


def test_no_progress_text_says_there_is_no_value():
    assert "まだ無い" in NO_PROGRESS_TEXT
