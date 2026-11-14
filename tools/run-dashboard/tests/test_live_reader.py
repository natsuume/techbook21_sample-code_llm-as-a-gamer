"""接続ごとの読み取り (live_reader の open_live・poll_live・stream_live)。

合成の実行 (synthetic_runs) を書き、live_replay で空の実行のディレクトリーに 1 行ずつ書き足しながら読む。
最後の期待値は、書き終えた実行のディレクトリーに対する区間 API・summary・advisor の詳細の応答 (TestClient)。
イベントの data は encode_live_event_data で JSON にしてから読んだ値で比べる (応答の直列化と同じく NaN は null)。
"""

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anyio
import anyio.lowlevel
import pytest
from fastapi.testclient import TestClient
from live_replay import (
    ReplayStep,
    apply_step,
    copy_static_files,
    replay_steps,
    shift_decision_times,
)
from synthetic_runs import (
    CONTROLS_RUN_NAME,
    DASHBOARD_ORIGIN_MONO,
    STALE_RUN_LOG_MTIME,
    STRATEGY_RUN_NAME,
)

from run_dashboard import jsonl_lines, live_reader
from run_dashboard.app import create_app
from run_dashboard.live_events import LiveEvent, encode_live_event_data
from run_dashboard.live_reader import (
    LIVE_POLL_INTERVAL_S,
    LiveCursors,
    LivePoll,
    LiveState,
    open_live,
    poll_live,
    stream_live,
)

# TestClient の要求の宛先 (Host の検査を通すため、待ち受けるアドレスにする)
BASE_URL = "http://127.0.0.1:8765"
# 区間 API の種類 (応答の cursor を LiveCursors のどのフィールドに渡すか)
RANGE_KIND_OF_CURSOR = {
    "run": "events",
    "requests": "requests",
    "frames": "frames",
    "pad": "pad",
}
# 行のイベントの種類 → 同じ形の項目を返す区間 API の種類
LINE_EVENT_KINDS = {
    "request": "requests",
    "run": "events",
    "frame": "frames",
    "pad": "pad",
}
# 1 回の読み取りの events の種類の順
EVENT_ORDER = (
    "request",
    "run",
    "frame",
    "pad",
    "advisor_step",
    "advisor_run",
    "note_version",
    "stats",
    "end",
)
# 1 行ずつ書き足して集めたイベントを確かめる実行 (古い形は 1 つ。古い形の 2 つは
# test_old_format_run_sends_only_run_and_stats で確かめる)
REPLAYED_RUNS = (
    "dashboard",
    "broken",
    "save_requests",
    "no_dashboard",
    "no_answer_type",
)
# decision の行の壁時計をずらす秒 (起点の decision の行を取りこぼすと、その後の行の t がこの秒だけずれる)
DECISION_SKEW_S = 2.0
# stream_live のテストで受け取るイベントと、poll_live を呼ぶ回数の上限 (超えたら終わらない読み方として失敗にする)
MAX_STREAM_EVENTS = 10_000
MAX_POLLS = 10_000


# --- 補助 ---


def now_for(run_dir: Path) -> float:
    """読み取りに渡す今の時刻。run.jsonl があればその更新時刻の 1 秒後 (実行中のまま)、無ければ time.time()。"""
    path = run_dir / "run.jsonl"
    return os.stat(path).st_mtime + 1.0 if path.exists() else time.time()


def decoded(event: LiveEvent) -> object:
    """event の data を、SSE の data の JSON にしてから読んだ値。"""
    return json.loads(encode_live_event_data(event.data))


class LiveFollower:
    """1 つの接続の読み取り (open_live の後に poll_live を繰り返す) を行い、届いたイベントを集める。"""

    def __init__(self, run_dir: Path, cursors: LiveCursors):
        self.run_dir = run_dir
        self.polls: list[LivePoll] = [open_live(run_dir, cursors, now_for(run_dir))]

    @property
    def state(self) -> LiveState:
        return self.polls[-1].state

    @property
    def finished(self) -> bool:
        return self.polls[-1].finished

    def poll(self, now: float | None = None) -> list[LiveEvent]:
        """poll_live で読み、届いたイベントを返す。now を省くと now_for(run_dir)。"""
        poll = poll_live(self.state, now_for(self.run_dir) if now is None else now)
        self.polls.append(poll)
        return poll.events

    @property
    def events(self) -> list[LiveEvent]:
        return [event for poll in self.polls for event in poll.events]

    def data_of(self, kind: str) -> list:
        return [decoded(event) for event in self.events if event.kind == kind]


def data_of(events: list[LiveEvent], kind: str) -> list:
    return [decoded(event) for event in events if event.kind == kind]


@dataclass
class Replay:
    """元の実行の歩 steps を、実行のディレクトリー target に先頭から 1 歩ずつ書く。written は書いた歩の数。"""

    target: Path
    steps: list[ReplayStep]
    written: int = 0

    def write_next(self) -> ReplayStep:
        step = self.steps[self.written]
        apply_step(self.target, step)
        self.written += 1
        return step

    def write_until(self, end: int) -> None:
        """steps[end] の手前まで書く。"""
        while self.written < end:
            self.write_next()


def start_replay(source_run_dir: Path, tmp_path: Path) -> Replay:
    """source_run_dir と同じ名前の空の実行のディレクトリーを tmp_path / "live" に作り、書き足す準備をする。

    画像 (thumbs/・frames/) を先に写し、空の run.jsonl を置く。run.jsonl の無い実行は実行中でない
    (最初の読み取りで end になる) ため、接続した後に全部の行を書き足すテストでも、run.jsonl を先に置く
    (書き終えたファイルは元と同じバイト列になる)。
    """
    target = tmp_path / "live" / source_run_dir.name
    copy_static_files(source_run_dir, target)
    (target / "run.jsonl").touch()
    return Replay(target, replay_steps(source_run_dir))


def follow(
    replay: Replay, follower: LiveFollower, every: int = 1, until: int | None = None
) -> None:
    """replay の残りの歩 (until があれば steps[until] の手前まで) を書き、every 歩ごとと最後に poll する。"""
    end = len(replay.steps) if until is None else until
    while replay.written < end:
        replay.write_next()
        if replay.written % every == 0:
            follower.poll()
    follower.poll()


def step_record(step: ReplayStep) -> dict | None:
    """追記する歩の行を JSON の object として読んだ値 (読めなければ None)。"""
    if step.whole_file:
        return None
    try:
        value = json.loads(step.data)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def find_step(steps: list[ReplayStep], relative_path: str, **fields: object) -> int:
    """relative_path に追記する歩のうち、行の fields のキーの値がすべて同じ最初の歩の位置を返す。"""
    for index, step in enumerate(steps):
        record = step_record(step) if step.relative_path == relative_path else None
        if record is not None and all(
            record.get(key) == value for key, value in fields.items()
        ):
            return index
    raise AssertionError(f"no step for {relative_path} with {fields}")


def get_json(client: TestClient, url: str):
    response = client.get(url)
    assert response.status_code == 200, response.text
    return response.json()


def api_client(run_dir: Path) -> TestClient:
    """run_dir の実行を読む、新しいアプリ (キャッシュの無い状態) の TestClient を返す。"""
    return TestClient(create_app(run_dir.parent, None), base_url=BASE_URL)


@dataclass(frozen=True)
class ApiSnapshot:
    """ある時点の、実行の区間 API (4 つ。種類 → 応答) と summary の応答。"""

    ranges: dict[str, dict]
    summary: dict

    def items(self, range_kind: str) -> list:
        return self.ranges[range_kind]["items"]

    def cursor(self, field: str) -> int:
        return self.ranges[RANGE_KIND_OF_CURSOR[field]]["cursor"]

    def cursors(self) -> LiveCursors:
        """区間 API の cursor を、そのまま接続のクエリーにした値。"""
        return LiveCursors(
            **{field: self.cursor(field) for field in RANGE_KIND_OF_CURSOR}
        )


def api_snapshot(run_dir: Path) -> ApiSnapshot:
    client = api_client(run_dir)
    name = run_dir.name
    return ApiSnapshot(
        ranges={
            kind: get_json(client, f"/api/runs/{name}/{kind}")
            for kind in RANGE_KIND_OF_CURSOR.values()
        },
        summary=get_json(client, f"/api/runs/{name}"),
    )


def advisor_detail_steps(run_dir: Path, name: str) -> list[dict]:
    """advisor の実行 name の詳細の steps (実行のディレクトリーが無ければ空のリスト)。"""
    if not (run_dir / name).is_dir():
        return []
    return get_json(api_client(run_dir), f"/api/runs/{run_dir.name}/advisor/{name}")[
        "steps"
    ]


def approx_or_none(value: float | None) -> object:
    return value if value is None else pytest.approx(value)


def assert_stats_match(stats: dict, summary: dict) -> None:
    """stats の data が summary の t_end・counts・decide_s_median と同じ値であることを確かめる。"""
    assert set(stats) == {"t_end", "counts", "decide_s_median"}
    assert stats["counts"] == summary["counts"]
    assert stats["t_end"] == approx_or_none(summary["t_end"])
    assert stats["decide_s_median"] == approx_or_none(summary["decide_s_median"])


def assert_line_events_match(
    follower: LiveFollower, final: ApiSnapshot, start: ApiSnapshot | None = None
) -> None:
    """接続した時点の区間 API の項目 (start。無ければ空) + 届いた行のイベントが、最後の区間 API の項目と同じ。"""
    for event_kind, range_kind in LINE_EVENT_KINDS.items():
        before = start.items(range_kind) if start is not None else []
        assert before + follower.data_of(event_kind) == final.items(range_kind), (
            event_kind
        )


def assert_snapshot_values_match(follower: LiveFollower, summary: dict) -> None:
    """advisor_run を名前、note_version を番号で置き換えた最後の値と、最後の stats が summary と同じ。"""
    advisor_runs: dict[str, dict] = {}
    for data in follower.data_of("advisor_run"):
        advisor_runs[data["name"]] = data
    assert advisor_runs == {run["name"]: run for run in summary["advisor_runs"]}
    versions: dict[int, dict] = {}
    for data in follower.data_of("note_version"):
        versions[data["version"]] = data
    assert versions == {
        version["version"]: version for version in summary["note_versions"]
    }
    assert_stats_match(follower.data_of("stats")[-1], summary)


def assert_steps_match(sent: list[dict], expected: list[dict]) -> None:
    """届いた advisor_step が、詳細の steps と (t, tool, summary) で同じで、kind も同じ (詳細で
    permission_denied の step は、送った時点の tool_result で届いてよい) ことを確かめる。
    """
    assert all(set(step) == {"name", "t", "kind", "tool", "summary"} for step in sent)
    assert [(step["t"], step["tool"], step["summary"]) for step in sent] == [
        (step["t"], step["tool"], step["summary"]) for step in expected
    ]
    for got, want in zip(sent, expected, strict=True):
        assert got["kind"] == want["kind"] or (
            want["kind"] == "permission_denied" and got["kind"] == "tool_result"
        )


def append_lines(run_dir: Path, relative_path: str, lines: list[bytes]) -> None:
    for line in lines:
        apply_step(run_dir, ReplayStep(relative_path, line, False))


def file_lines(path: Path) -> list[bytes]:
    """改行で終わる JSON Lines のファイル path の各行 (改行つき) を返す。"""
    return [line + b"\n" for line in path.read_bytes().split(b"\n")[:-1]]


def remove_origin_mono(run_dir: Path) -> None:
    """run_dir の run.jsonl の start の行から origin_mono を除いて書き直す (ほかの行はそのまま)。"""
    path = run_dir / "run.jsonl"
    lines = []
    for line in path.read_bytes().split(b"\n"):
        try:
            record = json.loads(line) if line else None
        except ValueError:
            record = None
        if isinstance(record, dict) and record.get("event") == "start":
            record.pop("origin_mono", None)
            line = json.dumps(record, ensure_ascii=False).encode("utf-8")
        lines.append(line)
    path.write_bytes(b"\n".join(lines))


def tree_signature(root: Path) -> dict[str, tuple[int, int]]:
    """root と、その下のすべてのパスの、相対パス → (大きさ, 更新時刻 st_mtime_ns)。"""
    signature = {}
    for path in [root, *sorted(root.rglob("*"))]:
        stat = path.lstat()
        signature[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return signature


# --- 定数 ---


def test_live_poll_interval_is_quarter_second():
    assert LIVE_POLL_INTERVAL_S == 0.25


# --- 1 行ずつ書き足して集めたイベントが、最後の区間 API・summary と同じ ---


@pytest.mark.parametrize("every", [1, 4])
@pytest.mark.parametrize("attribute", REPLAYED_RUNS)
def test_events_collected_while_lines_are_appended_match_final_api(
    runs, tmp_path, attribute, every
):
    # 空の実行のディレクトリーに接続 (cursor なし) し、1 歩ずつ (または 4 歩ずつ) 書き足して読む
    replay = start_replay(getattr(runs, attribute).run_dir, tmp_path)
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower, every)
    final = api_snapshot(replay.target)
    assert_line_events_match(follower, final)
    assert_snapshot_values_match(follower, final.summary)
    # end は record_end のある実行だけ (書いている途中は、今の時刻を run.jsonl の更新の 1 秒後にするため)
    has_record_end = attribute in ("dashboard", "broken")
    assert follower.data_of("end") == ([{"record_end": True}] if has_record_end else [])
    assert follower.finished is has_record_end


@pytest.mark.parametrize("every", [1, 4])
def test_advisor_steps_collected_match_advisor_detail(runs, tmp_path, every):
    # 名前ごとに集めた advisor_step が、advisor の詳細の steps と同じ
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower, every)
    sent = follower.data_of("advisor_step")
    assert {step["name"] for step in sent} == {CONTROLS_RUN_NAME, STRATEGY_RUN_NAME}
    for name in (CONTROLS_RUN_NAME, STRATEGY_RUN_NAME):
        expected = advisor_detail_steps(replay.target, name)
        assert_steps_match([step for step in sent if step["name"] == name], expected)
    if every == 1:
        # 拒否された Bash の結果は、result の行が届く前に送るため tool_result で届く
        strategy = [step for step in sent if step["name"] == STRATEGY_RUN_NAME]
        detail = advisor_detail_steps(replay.target, STRATEGY_RUN_NAME)
        assert [step["kind"] for step in strategy] == [
            "tool_result" if step["kind"] == "permission_denied" else step["kind"]
            for step in detail
        ]


@pytest.mark.parametrize("fraction", [0.25, 0.5, 0.75])
@pytest.mark.parametrize("attribute", ["dashboard", "broken"])
def test_connecting_midway_with_range_cursors(runs, tmp_path, attribute, fraction):
    # 途中まで書いた時点の区間 API の cursor で接続すると、区間 API の項目 + 届いたイベントが最後の区間 API
    replay = start_replay(getattr(runs, attribute).run_dir, tmp_path)
    replay.write_until(int(len(replay.steps) * fraction))
    start = api_snapshot(replay.target)
    follower = LiveFollower(replay.target, start.cursors())
    follow(replay, follower)
    final = api_snapshot(replay.target)
    assert_line_events_match(follower, final, start)
    assert_snapshot_values_match(follower, final.summary)


@pytest.mark.parametrize("older", ["requests", "run"])
def test_cursors_taken_at_different_times(runs, tmp_path, older):
    # requests の cursor を古い時点、run の cursor を新しい時点で取る (と、その逆)。decision の行の壁時計を
    # 2 秒ずらした実行で、最初の起点 (要求 1 の decision の行) を取りこぼすと、その後の行の t が 2 秒ずれる
    source = runs.dashboard.run_dir
    shift_decision_times(source, DECISION_SKEW_S)
    shifted_events = api_snapshot(source).items("events")
    # 要求 1 の decision の行を起点にした decision_error の t (2.125 - 2.0)。start の行を起点にすると 2.125 になる
    assert shifted_events[3]["event"] == "decision_error"
    assert shifted_events[3]["t"] == pytest.approx(2.125 - DECISION_SKEW_S)
    replay = start_replay(source, tmp_path)
    early_end = find_step(replay.steps, "requests.jsonl", event="request", number=1)
    late_end = find_step(replay.steps, "run.jsonl", event="decision", number=3) + 1
    replay.write_until(early_end)
    early = api_snapshot(replay.target)
    replay.write_until(late_end)
    late = api_snapshot(replay.target)
    taken = {
        field: (early if field == older else late) for field in RANGE_KIND_OF_CURSOR
    }
    cursors = LiveCursors(
        **{field: snapshot.cursor(field) for field, snapshot in taken.items()}
    )
    follower = LiveFollower(replay.target, cursors)
    follow(replay, follower)
    final = api_snapshot(replay.target)
    for field, range_kind in RANGE_KIND_OF_CURSOR.items():
        event_kind = next(
            kind
            for kind, range_of in LINE_EVENT_KINDS.items()
            if range_of == range_kind
        )
        assert taken[field].items(range_kind) + follower.data_of(
            event_kind
        ) == final.items(range_kind), field
    assert_snapshot_values_match(follower, final.summary)


@pytest.mark.parametrize("omitted", ["run", "requests", "frames", "pad"])
def test_omitted_cursor_sends_only_lines_written_after_connecting(
    runs, tmp_path, omitted
):
    # cursor を省いたファイルは、接続時にあった行を送らず、その後に書いた行を送る (ほかは 0 から送る)
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(len(replay.steps) // 2)
    start = api_snapshot(replay.target)
    assert start.items(RANGE_KIND_OF_CURSOR[omitted]) != []
    cursors = LiveCursors(
        **{field: None if field == omitted else 0 for field in RANGE_KIND_OF_CURSOR}
    )
    follower = LiveFollower(replay.target, cursors)
    follow(replay, follower)
    final = api_snapshot(replay.target)
    for field, range_kind in RANGE_KIND_OF_CURSOR.items():
        event_kind = next(
            kind
            for kind, range_of in LINE_EVENT_KINDS.items()
            if range_of == range_kind
        )
        skipped = len(start.items(range_kind)) if field == omitted else 0
        assert follower.data_of(event_kind) == final.items(range_kind)[skipped:], field


def test_connecting_sends_all_advisor_runs_note_versions_and_stats(runs, tmp_path):
    # 書き足さなくても、接続直後に advisor_run (summary の順)・note_version (番号の昇順) のすべてと stats。
    # 何も書かずに読むと、何も届かない
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(find_step(replay.steps, "run.jsonl", event="record_end"))
    snapshot = api_snapshot(replay.target)
    advisor_runs = snapshot.summary["advisor_runs"]
    note_versions = snapshot.summary["note_versions"]
    assert (len(advisor_runs), len(note_versions)) == (2, 3)
    follower = LiveFollower(replay.target, LiveCursors())
    assert [event.kind for event in follower.events] == (
        ["advisor_run"] * 2 + ["note_version"] * 3 + ["stats"]
    )
    assert follower.data_of("advisor_run") == advisor_runs
    assert follower.data_of("note_version") == note_versions
    assert_stats_match(follower.data_of("stats")[0], snapshot.summary)
    assert follower.finished is False
    assert follower.poll() == []
    assert follower.finished is False


def test_events_of_one_read_are_in_kind_order(runs, tmp_path):
    # 1 回の読み取りに全種類が入るとき、request → run → frame → pad → advisor_step → advisor_run →
    # note_version → stats の順
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    follower = LiveFollower(replay.target, LiveCursors())
    replay.write_until(find_step(replay.steps, "run.jsonl", event="record_end"))
    kinds = [event.kind for event in follower.poll()]
    assert set(kinds) == set(EVENT_ORDER) - {"end"}
    assert kinds == sorted(kinds, key=EVENT_ORDER.index)


def test_advisor_run_is_sent_again_only_for_run_whose_result_json_is_written(
    runs, tmp_path
):
    # result.json を書いた読み取りで、その実行の 1 件だけを送り直す (t_end・returncode・denials・result が変わる)
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    result_index = next(
        index
        for index, step in enumerate(replay.steps)
        if step.relative_path == f"{STRATEGY_RUN_NAME}/result.json"
    )
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower, until=result_index)
    before = [
        data
        for data in follower.data_of("advisor_run")
        if data["name"] == STRATEGY_RUN_NAME
    ][-1]
    replay.write_next()
    sent = data_of(follower.poll(), "advisor_run")
    follow(replay, follower)
    final = api_snapshot(replay.target)
    strategy = next(
        run for run in final.summary["advisor_runs"] if run["name"] == STRATEGY_RUN_NAME
    )
    assert sent == [strategy]
    for key in ("t_end", "returncode", "denials", "result"):
        assert before[key] is None
        assert strategy[key] is not None


def test_note_version_is_sent_again_when_advisor_dir_is_written_later(runs, tmp_path):
    # 版を変えた要求の行を、受け持つ advisor の実行のディレクトリーより先に書くと、by が "external" で届き、
    # ディレクトリーを書いた読み取りで、by が実行の名前の同じ番号の版が届く
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    strategy_steps = [
        step
        for step in replay.steps
        if step.relative_path.startswith(f"{STRATEGY_RUN_NAME}/")
    ]
    others = [step for step in replay.steps if step not in strategy_steps]
    request_4 = find_step(others, "requests.jsonl", event="request", number=4)
    assert step_record(others[request_4 + 1]) is not None
    assert step_record(others[request_4 + 1])["event"] == "decision"
    replay.steps = others[: request_4 + 1] + strategy_steps + others[request_4 + 1 :]
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower, until=request_4 + 1)
    version_1 = [
        data for data in follower.data_of("note_version") if data["version"] == 1
    ]
    assert version_1[-1]["changed"] == [{"kind": "strategy", "by": "external"}]
    replay.write_until(request_4 + 1 + len(strategy_steps) + 1)
    resent = [
        data
        for data in data_of(follower.poll(), "note_version")
        if data["version"] == 1
    ]
    assert [data["changed"] for data in resent] == [
        [{"kind": "strategy", "by": STRATEGY_RUN_NAME}]
    ]
    follow(replay, follower)
    final = api_snapshot(replay.target)
    assert resent[0] == final.summary["note_versions"][1]
    assert_snapshot_values_match(follower, final.summary)


def test_stats_are_sent_only_when_changed(runs, tmp_path):
    # frames の行だけを書くと counts.frames と t_end が変わって届く。何も書かなければ届かない
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(find_step(replay.steps, "run.jsonl", event="record_end"))
    follower = LiveFollower(replay.target, LiveCursors())
    at_connect = follower.data_of("stats")[0]
    assert follower.poll() == []
    frame_t = 20.0
    append_lines(
        replay.target,
        "frames.jsonl",
        [
            json.dumps(
                {"mono": DASHBOARD_ORIGIN_MONO + frame_t, "sharpness": 1.0}
            ).encode("utf-8")
            + b"\n"
        ],
    )
    stats = data_of(follower.poll(), "stats")
    assert stats == [
        {
            "t_end": frame_t,
            "counts": {
                **at_connect["counts"],
                "frames": at_connect["counts"]["frames"] + 1,
            },
            "decide_s_median": at_connect["decide_s_median"],
        }
    ]
    assert follower.poll() == []


def test_lines_before_start_line_are_sent_when_start_line_is_read(runs, tmp_path):
    # start の行より前に書いた header・要求・frames・pad・record_error は、start の行を読むまでイベントに
    # しない (stats の件数は数える)。start の行を読んだ読み取りで、ためた行がファイルの順に届く
    source = runs.dashboard.run_dir
    target = tmp_path / "live" / source.name
    copy_static_files(source, target)
    (target / "run.jsonl").touch()
    run_lines = file_lines(source / "run.jsonl")
    assert json.loads(run_lines[0])["event"] == "record_error"
    assert json.loads(run_lines[1])["event"] == "start"
    follower = LiveFollower(target, LiveCursors())
    append_lines(target, "requests.jsonl", file_lines(source / "requests.jsonl")[:2])
    append_lines(target, "frames.jsonl", file_lines(source / "frames.jsonl")[:2])
    append_lines(target, "pad.jsonl", file_lines(source / "pad.jsonl")[:1])
    append_lines(target, "run.jsonl", run_lines[:1])
    before_start = follower.poll()
    assert [
        event.kind for event in before_start if event.kind in LINE_EVENT_KINDS
    ] == []
    assert data_of(before_start, "stats")[-1]["counts"] == {
        "requests": 1,
        "frames": 2,
        "pad": 1,
    }
    append_lines(target, "run.jsonl", run_lines[1:2])
    at_start = follower.poll()
    final = api_snapshot(target)
    assert [event.kind for event in at_start if event.kind in LINE_EVENT_KINDS] == (
        ["request", "run", "run", "frame", "frame", "pad"]
    )
    for event_kind, range_kind in LINE_EVENT_KINDS.items():
        assert data_of(at_start, event_kind) == final.items(range_kind), event_kind


def test_connecting_with_range_cursors_before_start_line_sends_earlier_lines(
    runs, tmp_path
):
    # start の行を書く前に、区間 API の cursor で接続する。frames・pad・events は start の行 (origin_mono) が無く
    # 使えないため cursor が 0 で、start の行を書いた読み取りで、start の行より前に書いた frames・pad・run の行が届く
    source = runs.dashboard.run_dir
    target = tmp_path / "live" / source.name
    copy_static_files(source, target)
    run_lines = file_lines(source / "run.jsonl")
    assert json.loads(run_lines[0])["event"] == "record_error"
    assert json.loads(run_lines[1])["event"] == "start"
    append_lines(target, "requests.jsonl", file_lines(source / "requests.jsonl")[:1])
    append_lines(target, "frames.jsonl", file_lines(source / "frames.jsonl")[:2])
    append_lines(target, "pad.jsonl", file_lines(source / "pad.jsonl")[:1])
    append_lines(target, "run.jsonl", run_lines[:1])
    start = api_snapshot(target)
    for range_kind in ("frames", "pad", "events"):
        assert start.ranges[range_kind]["available"] is False, range_kind
        assert start.ranges[range_kind]["cursor"] == 0, range_kind
    follower = LiveFollower(target, start.cursors())
    assert [
        event.kind for event in follower.events if event.kind in LINE_EVENT_KINDS
    ] == []
    append_lines(target, "run.jsonl", run_lines[1:2])
    at_start = follower.poll()
    final = api_snapshot(target)
    assert [event.kind for event in at_start if event.kind in LINE_EVENT_KINDS] == (
        ["run", "run", "frame", "frame", "pad"]
    )
    assert_line_events_match(follower, final, start)
    assert [data["event"] for data in follower.data_of("run")] == [
        "record_error",
        "start",
    ]


def test_frames_and_pad_are_not_sent_without_origin_mono(runs, tmp_path):
    # start の行に origin_mono が無ければ frame・pad を送らない (件数は数える)
    source = runs.dashboard.run_dir
    remove_origin_mono(source)
    source_snapshot = api_snapshot(source)
    assert source_snapshot.ranges["frames"]["available"] is False
    assert source_snapshot.summary["counts"] == {"requests": 6, "frames": 7, "pad": 5}
    replay = start_replay(source, tmp_path)
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower)
    final = api_snapshot(replay.target)
    assert follower.data_of("frame") == []
    assert follower.data_of("pad") == []
    assert_line_events_match(follower, final)
    assert_stats_match(follower.data_of("stats")[-1], final.summary)


@pytest.mark.parametrize("connect_at", ["strategy_tool_result", "controls_first_line"])
def test_advisor_steps_are_sent_for_stream_lines_written_after_connecting(
    runs, tmp_path, connect_at
):
    # 接続時にあった stream.jsonl は接続後の行だけ、接続後にできたディレクトリーはすべて、接続時に
    # ディレクトリーがあって stream.jsonl が無ければ最初の行から送る。接続前の tool_use の名前が、接続後の
    # tool_result の tool になる
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    if connect_at == "strategy_tool_result":
        index = find_step(
            replay.steps,
            f"{STRATEGY_RUN_NAME}/stream.jsonl",
            mono=DASHBOARD_ORIGIN_MONO + 2.25,
        )
    else:
        index = find_step(
            replay.steps,
            f"{CONTROLS_RUN_NAME}/stream.jsonl",
            mono=DASHBOARD_ORIGIN_MONO + 3.75,
        )
    replay.write_until(index)
    controls_dir = replay.target / CONTROLS_RUN_NAME
    if connect_at == "strategy_tool_result":
        assert not controls_dir.exists()
    else:
        assert controls_dir.is_dir()
        assert not (controls_dir / "stream.jsonl").exists()
    steps_at_connect = {
        name: len(advisor_detail_steps(replay.target, name))
        for name in (STRATEGY_RUN_NAME, CONTROLS_RUN_NAME)
    }
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower)
    sent = follower.data_of("advisor_step")
    for name in (STRATEGY_RUN_NAME, CONTROLS_RUN_NAME):
        expected = advisor_detail_steps(replay.target, name)[steps_at_connect[name] :]
        assert_steps_match([step for step in sent if step["name"] == name], expected)
    if connect_at == "strategy_tool_result":
        first = next(step for step in sent if step["name"] == STRATEGY_RUN_NAME)
        assert (first["kind"], first["tool"]) == ("tool_result", "Read")


@pytest.mark.parametrize("attribute", ["no_answer_type", "single_advisor_question"])
def test_old_format_run_sends_only_run_and_stats(runs, tmp_path, attribute):
    # 古い形の実行では request・advisor_run・note_version・advisor_step を送らない
    replay = start_replay(getattr(runs, attribute).run_dir, tmp_path)
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower)
    final = api_snapshot(replay.target)
    assert {event.kind for event in follower.events} == {"run", "stats"}
    assert follower.data_of("run") == final.items("events")
    assert_stats_match(follower.data_of("stats")[-1], final.summary)


def test_end_is_sent_last_in_read_with_record_end(runs, tmp_path):
    # record_end を書いた読み取りで、ほかのイベントの後に end {"record_end": true}。終わった state の
    # poll_live は何も読まずに finished
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    record_end = find_step(replay.steps, "run.jsonl", event="record_end")
    assert record_end == len(replay.steps) - 1
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower, until=record_end)
    assert follower.finished is False
    replay.write_next()
    poll = poll_live(follower.state, now_for(replay.target))
    kinds = [event.kind for event in poll.events]
    assert kinds[-1] == "end"
    assert kinds.count("end") == 1
    assert "run" in kinds
    assert decoded(poll.events[-1]) == {"record_end": True}
    assert poll.finished is True
    final = api_snapshot(replay.target)
    assert data_of(poll.events, "run") == [final.items("events")[-1]]
    after = poll_live(poll.state, now_for(replay.target))
    assert (after.events, after.finished) == ([], True)


def test_end_is_sent_when_run_log_stops_for_more_than_ten_seconds(runs, tmp_path):
    # record_end が無くても、run.jsonl の更新から 10 秒を超えたら end {"record_end": false}
    replay = start_replay(runs.save_requests.run_dir, tmp_path)
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower)
    os.utime(replay.target / "run.jsonl", (STALE_RUN_LOG_MTIME, STALE_RUN_LOG_MTIME))
    follower.poll(now=STALE_RUN_LOG_MTIME + 10.0)
    assert "end" not in [event.kind for event in follower.polls[-1].events]
    assert follower.finished is False
    poll = poll_live(follower.state, STALE_RUN_LOG_MTIME + 10.001)
    assert poll.events[-1].kind == "end"
    assert decoded(poll.events[-1]) == {"record_end": False}
    assert poll.finished is True


def test_open_live_of_finished_run_sends_all_lines_snapshot_and_end(runs):
    # 接続した時点で実行中でない実行 (cursor すべて 0) は、open_live の 1 回で全部の行・snapshot・end
    run_dir = runs.dashboard.run_dir
    snapshot = api_snapshot(run_dir)
    poll = open_live(
        run_dir, LiveCursors(run=0, requests=0, frames=0, pad=0), time.time()
    )
    assert poll.finished is True
    assert [event.kind for event in poll.events] == (
        ["request"] * 6
        + ["run"] * 8
        + ["frame"] * 7
        + ["pad"] * 5
        + ["advisor_run"] * 2
        + ["note_version"] * 3
        + ["stats", "end"]
    )
    for event_kind, range_kind in LINE_EVENT_KINDS.items():
        assert data_of(poll.events, event_kind) == snapshot.items(range_kind)
    assert data_of(poll.events, "advisor_run") == snapshot.summary["advisor_runs"]
    assert data_of(poll.events, "note_version") == snapshot.summary["note_versions"]
    assert_stats_match(data_of(poll.events, "stats")[0], snapshot.summary)
    assert data_of(poll.events, "end") == [{"record_end": True}]


class RaceWriter:
    """jsonl_lines.read_complete_lines の代わり。armed の間に requests.jsonl を読んだら、読んだ直後に
    race_steps を書き足す (1 回だけ)。ほかは元の関数で読む。
    """

    def __init__(self, original: Callable, target: Path, race_steps: list[ReplayStep]):
        self.original = original
        self.target = target
        self.race_steps = race_steps
        self.armed = False
        self.done = False
        # 読んだファイルの名前 (呼ばれた順)
        self.read_names: list[str] = []

    def __call__(self, path: Path, offset: int = 0, end: int | None = None):
        self.read_names.append(path.name)
        chunk = self.original(path, offset, end)
        if self.armed and path.name == "requests.jsonl":
            self.armed = False
            for step in self.race_steps:
                apply_step(self.target, step)
            self.done = True
        return chunk


def test_decision_line_written_while_reading_requests_gets_its_request(
    runs, tmp_path, monkeypatch
):
    # requests.jsonl を読んだ直後に「要求 1 の行」「壁時計を 2 秒ずらした decision の行」「その後の run の行」
    # が書かれても、次の読み取りまでに届く run の行の t は、最後の区間 API と同じ。1 回の読み取りでは
    # requests.jsonl を run.jsonl より先に読む
    source = runs.dashboard.run_dir
    shift_decision_times(source, DECISION_SKEW_S)
    replay = start_replay(source, tmp_path)
    request_1 = find_step(replay.steps, "requests.jsonl", event="request", number=1)
    decision_1 = find_step(replay.steps, "run.jsonl", event="decision", number=1)
    decision_error = find_step(replay.steps, "run.jsonl", event="decision_error")
    race_indexes = (request_1, decision_1, decision_error)
    assert request_1 < decision_1 < decision_error
    race_steps = [replay.steps[index] for index in race_indexes]
    rest = [
        step
        for index, step in enumerate(replay.steps)
        if index >= request_1 and index not in race_indexes
    ]
    replay.write_until(request_1)
    replay.steps = replay.steps[:request_1] + race_steps + rest
    race = RaceWriter(jsonl_lines.read_complete_lines, replay.target, race_steps)
    monkeypatch.setattr(jsonl_lines, "read_complete_lines", race)
    follower = LiveFollower(
        replay.target, LiveCursors(run=0, requests=0, frames=0, pad=0)
    )
    race.armed = True
    raced = follower.poll()
    assert race.done is True
    # この読み取りの run.jsonl は、読み始めた時点の大きさまでしか読まない
    assert [data["event"] for data in data_of(raced, "run")] == []
    replay.written = request_1 + len(race_steps)
    # 次の読み取りは、要求 1 の行と decision の行のどちらも新しく読む
    race.read_names.clear()
    follower.poll()
    assert "requests.jsonl" in race.read_names
    assert "run.jsonl" in race.read_names
    assert race.read_names.index("requests.jsonl") < race.read_names.index("run.jsonl")
    follow(replay, follower)
    final = api_snapshot(replay.target)
    decision_error_item = next(
        item for item in final.items("events") if item["event"] == "decision_error"
    )
    assert decision_error_item["t"] == pytest.approx(2.125 - DECISION_SKEW_S)
    assert_line_events_match(follower, final)


@pytest.mark.parametrize("shift, skipped_after_connect", [(-5, 0), (1, 1)])
def test_frames_cursor_inside_line_or_beyond_end_skips_that_line(
    runs, tmp_path, shift, skipped_after_connect
):
    # cursor が行の途中なら、その行は届かない。cursor がファイルの大きさより大きければ、その位置より後に
    # 始まる行だけが届く (その位置をまたぐ行は届かない)
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(len(replay.steps) // 2)
    start = api_snapshot(replay.target)
    connected = len(start.items("frames"))
    remaining_frames = sum(
        1
        for step in replay.steps[replay.written :]
        if step.relative_path == "frames.jsonl"
    )
    assert connected >= 1
    assert remaining_frames >= 2
    follower = LiveFollower(
        replay.target, LiveCursors(frames=start.cursor("frames") + shift)
    )
    follow(replay, follower)
    final = api_snapshot(replay.target)
    assert (
        follower.data_of("frame")
        == final.items("frames")[connected + skipped_after_connect :]
    )


def test_symlinked_run_log_is_treated_as_missing(tmp_path):
    # run.jsonl がシンボリックリンクなら無いものとして扱う (実行中でないので、最初の読み取りで end)
    outside = tmp_path / "outside.jsonl"
    outside.write_text(
        '{"time": "10:00:00.000", "event": "start", "game": "secret"}\n'
        '{"time": "10:00:01.000", "event": "bridge_wait"}\n',
        encoding="utf-8",
    )
    run_dir = tmp_path / "runs" / "20261101-000000"
    run_dir.mkdir(parents=True)
    (run_dir / "run.jsonl").symlink_to(outside)
    poll = open_live(run_dir, LiveCursors(run=0), time.time())
    assert data_of(poll.events, "run") == []
    assert data_of(poll.events, "end") == [{"record_end": False}]
    assert poll.finished is True


def test_frames_file_removed_between_reads_does_not_raise(runs, tmp_path):
    # 読み取りの間に frames.jsonl を消しても例外を出さない
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(len(replay.steps) // 2)
    follower = LiveFollower(replay.target, LiveCursors())
    (replay.target / "frames.jsonl").unlink()
    assert data_of(follower.poll(), "frame") == []
    assert follower.finished is False


def test_frames_file_removed_while_reading_does_not_raise(runs, tmp_path, monkeypatch):
    # 読み取りの途中で (大きさを取った後、開く前に) frames.jsonl が消えても例外を出さない
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(len(replay.steps) // 2)
    follower = LiveFollower(replay.target, LiveCursors())
    original = jsonl_lines.read_complete_lines

    def removing_frames(path: Path, offset: int = 0, end: int | None = None):
        if path.name == "frames.jsonl" and path.exists():
            path.unlink()
        return original(path, offset, end)

    monkeypatch.setattr(jsonl_lines, "read_complete_lines", removing_frames)
    replay.write_next()
    assert data_of(follower.poll(), "frame") == []
    assert follower.finished is False


def test_poll_live_does_not_change_given_state(runs, tmp_path):
    # 同じ state で 2 回呼ぶと同じイベント
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(len(replay.steps) // 4)
    follower = LiveFollower(
        replay.target, LiveCursors(run=0, requests=0, frames=0, pad=0)
    )
    replay.write_until(len(replay.steps) * 3 // 4)
    now = now_for(replay.target)
    first = poll_live(follower.state, now)
    second = poll_live(follower.state, now)
    assert first.events != []
    assert second.events == first.events
    assert second.finished == first.finished
    assert poll_live(first.state, now).events == []


def test_decision_lines_and_lines_without_time_are_not_sent(runs, tmp_path):
    # decision の行と、t が None の行 (time が読めない・無い行) は run のイベントにしない。data に time は無い
    replay = start_replay(runs.save_requests.run_dir, tmp_path)
    follower = LiveFollower(replay.target, LiveCursors())
    follow(replay, follower)
    append_lines(
        replay.target,
        "run.jsonl",
        [
            json.dumps(line).encode("utf-8") + b"\n"
            for line in (
                {"time": "25:00:00.000", "event": "bridge_wait"},
                {"event": "interrupted"},
                {"time": "11:00:05.000", "event": "bridge_wait_end", "waited_s": 1.0},
            )
        ],
    )
    follower.poll()
    final = api_snapshot(replay.target)
    sent = follower.data_of("run")
    assert sent == final.items("events")
    assert [data["event"] for data in sent] == ["start", "bridge_wait_end"]
    assert all("time" not in data for data in sent)


def test_surrogate_and_nan_are_sent_as_range_api_values(runs, tmp_path):
    # 対になっていないサロゲートと NaN を含む要求の行・stream の行。data を JSON にして読んだ値が区間 API・
    # 詳細と同じ (NaN は null)
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(find_step(replay.steps, "run.jsonl", event="record_end"))
    follower = LiveFollower(replay.target, LiveCursors())
    request = {
        "event": "request",
        "number": 7,
        "t_sent": 14.5,
        "t_received": 14.75,
        "result": math.nan,
        "action": "a\ud800b",
    }
    stream = {
        "mono": DASHBOARD_ORIGIN_MONO + 6.0,
        "data": {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_surrogate",
                        "content": "a\ud800b",
                    }
                ]
            },
        },
    }
    append_lines(
        replay.target, "requests.jsonl", [json.dumps(request).encode() + b"\n"]
    )
    append_lines(
        replay.target,
        f"{STRATEGY_RUN_NAME}/stream.jsonl",
        [json.dumps(stream).encode() + b"\n"],
    )
    events = follower.poll()
    for event in events:
        encode_live_event_data(event.data).encode("ascii")
    final = api_snapshot(replay.target)
    requests = data_of(events, "request")
    assert requests == [final.items("requests")[-1]]
    assert (requests[0]["result"], requests[0]["action"]) == (None, "a\ud800b")
    steps = advisor_detail_steps(replay.target, STRATEGY_RUN_NAME)
    assert data_of(events, "advisor_step") == [{"name": STRATEGY_RUN_NAME, **steps[-1]}]
    assert steps[-1]["summary"] == "a\ud800b"


@pytest.mark.parametrize("field", ["run", "requests", "frames", "pad"])
def test_open_live_rejects_negative_cursor(runs, field):
    with pytest.raises(ValueError):
        open_live(runs.dashboard.run_dir, LiveCursors(**{field: -1}), time.time())


def test_reading_does_not_write_to_run_dir(runs, tmp_path):
    # open_live と poll_live の呼び出しの前後で、実行のディレクトリーの下のパス・大きさ・更新時刻が同じ
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(len(replay.steps) // 2)
    before = tree_signature(replay.target)
    follower = LiveFollower(
        replay.target, LiveCursors(run=0, requests=0, frames=0, pad=0)
    )
    follower.poll()
    assert tree_signature(replay.target) == before
    finished_run = runs.dashboard.run_dir
    before_finished = tree_signature(finished_run)
    open_live(
        finished_run, LiveCursors(run=0, requests=0, frames=0, pad=0), time.time()
    )
    assert tree_signature(finished_run) == before_finished


# --- stream_live ---


class LimitedPolls:
    """poll_live の代わり。呼ばれた回数を count に数え、limit 回を超えたら失敗にしてから元の関数で読む。"""

    def __init__(self, original: Callable, limit: int):
        self.original = original
        self.limit = limit
        self.count = 0

    def __call__(self, state: LiveState, now: float) -> LivePoll:
        self.count += 1
        if self.count > self.limit:
            raise AssertionError(f"poll_live was called more than {self.limit} times")
        return self.original(state, now)


def test_stream_live_follows_appended_lines_until_end(runs, tmp_path, monkeypatch):
    # 待ちのたびに次の歩を書き足す偽の sleep と、run.jsonl の更新の 1 秒後を返す偽の時計で、record_end を
    # 書いた読み取りの end まで受け取って終わる
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    assert (
        find_step(replay.steps, "run.jsonl", event="record_end")
        == len(replay.steps) - 1
    )
    replay.write_until(len(replay.steps) // 2)
    start = api_snapshot(replay.target)
    remaining = len(replay.steps) - replay.written
    monkeypatch.setattr(
        live_reader, "poll_live", LimitedPolls(live_reader.poll_live, MAX_POLLS)
    )
    sleeps: list[float] = []

    async def sleep_and_write(seconds: float) -> None:
        sleeps.append(seconds)
        if replay.written >= len(replay.steps):
            raise AssertionError(
                "stream_live kept reading after all steps were written"
            )
        replay.write_next()
        await anyio.lowlevel.checkpoint()

    async def collect() -> list[LiveEvent]:
        events: list[LiveEvent] = []
        async for event in stream_live(
            replay.target,
            start.cursors(),
            clock=lambda: now_for(replay.target),
            sleep=sleep_and_write,
            interval_s=0.01,
        ):
            events.append(event)
            if len(events) > MAX_STREAM_EVENTS:
                raise AssertionError("too many events")
        return events

    events = anyio.run(collect)
    assert events[-1].kind == "end"
    assert decoded(events[-1]) == {"record_end": True}
    assert [event.kind for event in events].count("end") == 1
    # 書き足す歩ごとに 1 回待ち、record_end を書いた後の読み取りで終わる
    assert sleeps == [0.01] * remaining
    final = api_snapshot(replay.target)
    for event_kind, range_kind in LINE_EVENT_KINDS.items():
        assert start.items(range_kind) + data_of(events, event_kind) == final.items(
            range_kind
        ), event_kind


def test_stream_live_does_not_read_after_close(runs, tmp_path, monkeypatch):
    # ジェネレーターを途中で閉じたら、それ以上 poll_live を呼ばない
    replay = start_replay(runs.dashboard.run_dir, tmp_path)
    replay.write_until(len(replay.steps) // 2)
    polls = LimitedPolls(live_reader.poll_live, MAX_POLLS)
    monkeypatch.setattr(live_reader, "poll_live", polls)

    async def sleep_and_write(seconds: float) -> None:
        if replay.written < len(replay.steps):
            replay.write_next()
        await anyio.lowlevel.checkpoint()

    async def read_then_close() -> tuple[int, int]:
        stream = stream_live(
            replay.target,
            LiveCursors(),
            clock=lambda: now_for(replay.target),
            sleep=sleep_and_write,
            interval_s=0.01,
        )
        # 接続直後のイベント (最後が stats) と、その後の読み取りで届く最初のイベントまで読んで閉じる
        while (await anext(stream)).kind != "stats":
            pass
        await anext(stream)
        await stream.aclose()
        closed_at = polls.count
        await anyio.sleep(0.2)
        return closed_at, polls.count

    closed_at, later = anyio.run(read_then_close)
    assert closed_at >= 1
    assert later == closed_at
