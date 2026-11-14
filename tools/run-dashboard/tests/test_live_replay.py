"""1 行ずつ書き足す補助 (live_replay) そのものの確認。

ほかのテスト (test_live_reader) が前提にしている性質 (書き終えたファイルが元と同じバイト列、要求の行が同じ number の
decision の行より先、advisor の入力が stream.jsonl の最初の行の直前) を確かめる。
"""

import json

import pytest
from live_replay import (
    apply_step,
    copy_static_files,
    replay_steps,
    replayed_record_files,
    shift_decision_times,
)
from synthetic_runs import STRATEGY_RUN_NAME

from run_dashboard.elapsed_time import SECONDS_PER_DAY, parse_wall_time

SYNTHETIC_RUN_ATTRIBUTES = (
    "dashboard",
    "no_dashboard",
    "save_requests",
    "broken",
    "no_answer_type",
    "single_advisor_question",
)


@pytest.mark.parametrize("attribute", SYNTHETIC_RUN_ATTRIBUTES)
def test_replayed_run_dir_has_same_bytes_as_source(runs, tmp_path, attribute):
    source = getattr(runs, attribute).run_dir
    target = tmp_path / "live" / source.name
    copy_static_files(source, target)
    for step in replay_steps(source):
        apply_step(target, step)
    files = replayed_record_files(source)
    assert replayed_record_files(target) == files
    for relative in files:
        assert (target / relative).read_bytes() == (source / relative).read_bytes()
    for name in ("thumbs", "frames"):
        if (source / name).is_dir():
            assert sorted(path.name for path in (target / name).iterdir()) == sorted(
                path.name for path in (source / name).iterdir()
            )


def line_index(steps, relative_path: str, **fields) -> int:
    for index, step in enumerate(steps):
        if step.relative_path != relative_path or step.whole_file:
            continue
        try:
            record = json.loads(step.data)
        except ValueError:
            continue
        if all(record.get(key) == value for key, value in fields.items()):
            return index
    raise AssertionError(f"no line for {relative_path} with {fields}")


def test_replay_steps_order_of_dashboard_run(runs):
    steps = replay_steps(runs.dashboard.run_dir)
    # 要求の行は、同じ number の decision の行より先
    for number in (1, 3, 4, 5):
        assert line_index(
            steps, "requests.jsonl", event="request", number=number
        ) < line_index(steps, "run.jsonl", event="decision", number=number)
    # advisor の入力は、その stream.jsonl の最初の行の直前
    strategy = [
        (step.relative_path, step.whole_file)
        for step in steps
        if step.relative_path.startswith(f"{STRATEGY_RUN_NAME}/")
    ]
    assert strategy[:3] == [
        (f"{STRATEGY_RUN_NAME}/context.md", True),
        (f"{STRATEGY_RUN_NAME}/frame-01.jpg", True),
        (f"{STRATEGY_RUN_NAME}/stream.jsonl", False),
    ]
    # result.json は、その実行の t_end (5.25 秒) に、同じ時刻の stream と run.jsonl の advisor の行より先
    result_index = next(
        index
        for index, step in enumerate(steps)
        if step.relative_path == f"{STRATEGY_RUN_NAME}/result.json"
    )
    assert result_index < line_index(steps, "run.jsonl", event="advisor")
    assert result_index < line_index(
        steps, f"{STRATEGY_RUN_NAME}/stream.jsonl", mono=1005.25
    )
    assert result_index > line_index(
        steps, f"{STRATEGY_RUN_NAME}/stream.jsonl", mono=1004.25
    )
    # requests.jsonl の header が最初
    assert steps[0].relative_path == "requests.jsonl"
    assert json.loads(steps[0].data)["event"] == "header"


def test_unterminated_last_line_is_last_step_of_its_file_without_newline(runs):
    steps = [
        step
        for step in replay_steps(runs.broken.run_dir)
        if step.relative_path == "frames.jsonl"
    ]
    assert not steps[-1].data.endswith(b"\n")
    assert all(step.data.endswith(b"\n") for step in steps[:-1])


def test_shift_decision_times_changes_only_time_of_decision_lines(runs):
    path = runs.dashboard.run_dir / "run.jsonl"
    before = path.read_bytes().split(b"\n")
    shift_decision_times(runs.dashboard.run_dir, 2.0)
    after = path.read_bytes().split(b"\n")
    assert len(after) == len(before)
    decisions = 0
    for old, new in zip(before, after, strict=True):
        record = json.loads(old) if old else None
        if record is None or record["event"] != "decision":
            assert new == old
            continue
        decisions += 1
        shifted = json.loads(new)
        assert {**shifted, "time": record["time"]} == record
        assert parse_wall_time(shifted["time"]) == pytest.approx(
            (parse_wall_time(record["time"]) + 2.0) % SECONDS_PER_DAY
        )
    assert decisions == 4
