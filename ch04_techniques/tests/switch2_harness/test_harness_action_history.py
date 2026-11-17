from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np
import pytest

from switch2_harness import screen_change
from switch2_harness.action_history import (
    HISTORY_COMMANDS,
    HISTORY_LIMIT,
    build_history_lines,
    frames_changed_on_disk,
)
from switch2_harness.event_log import CommandEvent

# 今回の look の時刻と frame
NOW = 1000.0
CURRENT_FRAME = "frames/000099.png"

HEADER = "直近の行動 (古い順、最大 20 件。秒数はこの撮影の何秒前か):"
NO_ACTIONS = ("直近の行動: なし",)
HOME_ERROR = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)


def frame(number: int) -> str:
    """番号の撮影の、作業ディレクトリーからの相対パスを返す (例: 1 は "frames/000001.png")。"""
    return f"frames/{number:06d}.png"


def look(seconds_ago: float, number: int) -> CommandEvent:
    """NOW の seconds_ago 秒前の、frame(number) を保存した成功した look。"""
    return CommandEvent(
        NOW - seconds_ago,
        "look",
        (),
        f"ok /play/{frame(number)}",
        True,
        0.2,
        frame(number),
    )


def failed_look(seconds_ago: float, reply: str = "error no frame yet") -> CommandEvent:
    """NOW の seconds_ago 秒前の、失敗した look。"""
    return CommandEvent(NOW - seconds_ago, "look", (), reply, False, 0.2)


def press(seconds_ago: float, *args: str) -> CommandEvent:
    """NOW の seconds_ago 秒前の、成功した press。"""
    return CommandEvent(
        NOW - seconds_ago, "press", args, "ok " + " ".join(args), True, 0.4
    )


def failed_press(seconds_ago: float, args: tuple[str, ...], reply: str) -> CommandEvent:
    """NOW の seconds_ago 秒前の、失敗した press。"""
    return CommandEvent(NOW - seconds_ago, "press", args, reply, False, 0.0)


def other(seconds_ago: float, command: str) -> CommandEvent:
    """NOW の seconds_ago 秒前の、look と press 以外 (status・finish・run) のイベント。"""
    args = {"status": (), "finish": ("up", "ALL"), "run": ("start",)}[command]
    return CommandEvent(NOW - seconds_ago, command, args, "ok", True, 0.1)


class FakeFramesChanged:
    """frames_changed の fake。呼ばれた (前, 後) の組を calls に記録し、changed にある組なら True を返す。"""

    def __init__(self, changed: Iterable[tuple[str, str]] = ()) -> None:
        self.changed = set(changed)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, before: str, after: str) -> bool:
        self.calls.append((before, after))
        return (before, after) in self.changed


def test_constants():
    assert HISTORY_LIMIT == 20
    assert HISTORY_COMMANDS == ("look", "press")


# --- 表示する行動 ---


@pytest.mark.parametrize(
    "previous",
    [
        [],
        [other(30, "run"), other(20, "status"), other(10, "finish")],
    ],
)
def test_no_actions(previous):
    frames_changed = FakeFramesChanged()
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines == NO_ACTIONS
    assert frames_changed.calls == []


def test_shows_the_last_20_looks_and_presses_in_time_order():
    # 25 秒前から 1 秒前まで 1 秒ごとに、look (奇数秒前) と press (偶数秒前) を交互に 25 件。
    # それぞれの 0.5 秒後に status・finish・run のどれかを挟む
    others = ("status", "finish", "run")
    previous: list[CommandEvent] = []
    look_number = 0
    for seconds_ago in range(25, 0, -1):
        if seconds_ago % 2 == 1:
            look_number += 1
            previous.append(look(seconds_ago, look_number))
        else:
            previous.append(press(seconds_ago, "tap", "A"))
        previous.append(other(seconds_ago - 0.5, others[seconds_ago % 3]))
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, FakeFramesChanged())
    assert lines == (
        HEADER,
        "- 20 秒前 press tap A: 画面の変化 なし",
        "- 19 秒前 look 000004.png",
        "- 18 秒前 press tap A: 画面の変化 なし",
        "- 17 秒前 look 000005.png",
        "- 16 秒前 press tap A: 画面の変化 なし",
        "- 15 秒前 look 000006.png",
        "- 14 秒前 press tap A: 画面の変化 なし",
        "- 13 秒前 look 000007.png",
        "- 12 秒前 press tap A: 画面の変化 なし",
        "- 11 秒前 look 000008.png",
        "- 10 秒前 press tap A: 画面の変化 なし",
        "- 9 秒前 look 000009.png",
        "- 8 秒前 press tap A: 画面の変化 なし",
        "- 7 秒前 look 000010.png",
        "- 6 秒前 press tap A: 画面の変化 なし",
        "- 5 秒前 look 000011.png",
        "- 4 秒前 press tap A: 画面の変化 なし",
        "- 3 秒前 look 000012.png",
        "- 2 秒前 press tap A: 画面の変化 なし",
        "- 1 秒前 look 000013.png",
    )


def test_limit_sets_the_number_of_actions_and_the_header():
    previous = [look(30, 1), look(20, 2), look(10, 3)]
    lines = build_history_lines(
        previous, NOW, CURRENT_FRAME, FakeFramesChanged(), limit=2
    )
    assert lines == (
        "直近の行動 (古い順、最大 2 件。秒数はこの撮影の何秒前か):",
        "- 20 秒前 look 000002.png",
        "- 10 秒前 look 000003.png",
    )


def test_previous_is_sorted_by_time():
    previous = [look(10, 2), press(20, "tap", "A"), look(30, 1)]
    frames_changed = FakeFramesChanged([(frame(1), frame(2))])
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines == (
        HEADER,
        "- 30 秒前 look 000001.png",
        "- 20 秒前 press tap A: 画面の変化 あり",
        "- 10 秒前 look 000002.png",
    )


def test_seconds_are_rounded():
    previous = [look(12.6, 1), look(12.4, 2)]
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, FakeFramesChanged())
    assert lines == (
        HEADER,
        "- 13 秒前 look 000001.png",
        "- 12 秒前 look 000002.png",
    )


def test_line_of_each_kind_of_action():
    previous = [
        look(95, 41),
        failed_look(30, "error no frame yet"),
        press(12, "hold", "LS:-1,0", "500"),
        failed_press(5, ("tap", "HOME"), HOME_ERROR),
    ]
    frames_changed = FakeFramesChanged()
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines == (
        HEADER,
        "- 95 秒前 look 000041.png",
        "- 30 秒前 look error no frame yet",
        "- 12 秒前 press hold LS:-1,0 500: 画面の変化 なし",
        f"- 5 秒前 press tap HOME: {HOME_ERROR}",
    )
    # 比べるのは成功した press だけ (失敗した press の分は呼ばない)
    assert frames_changed.calls == [(frame(41), CURRENT_FRAME)]


# --- press の画面の変化 ---


@pytest.mark.parametrize(("changed", "word"), [(True, "あり"), (False, "なし")])
def test_press_compares_the_looks_before_and_after_it(changed, word):
    # press の後の失敗した look は飛ばし、その次の成功した look と比べる
    previous = [look(30, 1), press(20, "tap", "A"), failed_look(15), look(10, 2)]
    frames_changed = FakeFramesChanged([(frame(1), frame(2))] if changed else [])
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines[2] == f"- 20 秒前 press tap A: 画面の変化 {word}"
    assert frames_changed.calls == [(frame(1), frame(2))]


def test_press_without_a_look_before_it_is_unknown():
    # press より前には失敗した look しかない
    previous = [failed_look(30), press(20, "tap", "A"), look(10, 1)]
    frames_changed = FakeFramesChanged()
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines == (
        HEADER,
        "- 30 秒前 look error no frame yet",
        "- 20 秒前 press tap A: 画面の変化 不明",
        "- 10 秒前 look 000001.png",
    )
    assert frames_changed.calls == []


def test_press_after_the_last_look_is_compared_with_the_current_frame():
    previous = [look(30, 1), press(10, "tap", "A")]
    frames_changed = FakeFramesChanged([(frame(1), CURRENT_FRAME)])
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines[-1] == "- 10 秒前 press tap A: 画面の変化 あり"
    assert frames_changed.calls == [(frame(1), CURRENT_FRAME)]


def test_press_uses_a_look_before_the_shown_actions():
    # 40 秒前の look の後に press が 21 件あり、表示する 20 件にその look は入らない
    presses = [press(seconds_ago, "tap", "A") for seconds_ago in range(21, 0, -1)]
    previous = [look(40, 1), *presses]
    frames_changed = FakeFramesChanged([(frame(1), CURRENT_FRAME)])
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines == (
        HEADER,
        *(
            f"- {seconds_ago} 秒前 press tap A: 画面の変化 あり"
            for seconds_ago in range(20, 0, -1)
        ),
    )
    assert frames_changed.calls == [(frame(1), CURRENT_FRAME)]


def test_presses_between_the_same_two_looks_compare_them_once():
    previous = [
        look(40, 1),
        press(30, "tap", "A"),
        press(20, "tap", "B"),
        press(10, "tap", "X"),
        look(5, 2),
    ]
    frames_changed = FakeFramesChanged([(frame(1), frame(2))])
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines == (
        HEADER,
        "- 40 秒前 look 000001.png",
        "- 30 秒前 press tap A: 画面の変化 あり",
        "- 20 秒前 press tap B: 画面の変化 あり",
        "- 10 秒前 press tap X: 画面の変化 あり",
        "- 5 秒前 look 000002.png",
    )
    assert frames_changed.calls == [(frame(1), frame(2))]


def test_each_press_compares_its_own_looks():
    previous = [look(40, 1), press(30, "tap", "A"), look(20, 2), press(10, "tap", "B")]
    frames_changed = FakeFramesChanged([(frame(1), frame(2))])
    lines = build_history_lines(previous, NOW, CURRENT_FRAME, frames_changed)
    assert lines == (
        HEADER,
        "- 40 秒前 look 000001.png",
        "- 30 秒前 press tap A: 画面の変化 あり",
        "- 20 秒前 look 000002.png",
        "- 10 秒前 press tap B: 画面の変化 なし",
    )
    assert sorted(frames_changed.calls) == [
        (frame(1), frame(2)),
        (frame(2), CURRENT_FRAME),
    ]


# --- frames_changed_on_disk ---


def write_gray_png(path: Path, value: int) -> None:
    """path に、すべての画素が value のグレースケールの PNG を書く (親ディレクトリーも作る)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), np.full((90, 160), value, dtype=np.uint8))


@pytest.mark.parametrize(("second_value", "changed"), [(0, False), (255, True)])
def test_frames_changed_on_disk_compares_the_images(tmp_path, second_value, changed):
    write_gray_png(tmp_path / frame(1), 0)
    write_gray_png(tmp_path / frame(2), second_value)
    frames_changed = frames_changed_on_disk(tmp_path)
    assert frames_changed(frame(1), frame(2)) is changed


def test_frames_changed_on_disk_reads_each_frame_once(tmp_path, monkeypatch):
    write_gray_png(tmp_path / frame(1), 0)
    write_gray_png(tmp_path / frame(2), 0)
    write_gray_png(tmp_path / frame(3), 255)
    read_paths: list[Path] = []
    original_load_gray = screen_change.load_gray

    def counting_load_gray(path: Path) -> np.ndarray:
        read_paths.append(path)
        return original_load_gray(path)

    # screen_change.load_sample は load_gray で読むため、load_gray の呼び出しを数える
    monkeypatch.setattr(screen_change, "load_gray", counting_load_gray)
    frames_changed = frames_changed_on_disk(tmp_path)
    assert frames_changed(frame(1), frame(2)) is False
    assert frames_changed(frame(2), frame(3)) is True
    assert frames_changed(frame(1), frame(3)) is True
    assert sorted(read_paths) == [
        tmp_path / frame(1),
        tmp_path / frame(2),
        tmp_path / frame(3),
    ]
