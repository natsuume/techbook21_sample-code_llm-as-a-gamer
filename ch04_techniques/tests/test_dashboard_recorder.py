import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from switch2_remote.capture import DEVICE_HEIGHT, DEVICE_WIDTH
from switch2_remote.controller_state import PadState

from switch2_agent import dashboard_recorder
from switch2_agent.dashboard_recorder import (
    FRAMES_QUEUE_CAPACITY,
    PAD_QUEUE_CAPACITY,
    RECORDER_CLOSE_WAIT_S,
    DashboardRecorder,
    feed_detector,
)
from switch2_agent.dashboard_records import (
    DetectorSnapshot,
    PadReport,
    snapshot_detector,
)
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.meaningful_frames import (
    MeaningfulFrameStore,
    SharpnessValleyDetector,
)
from switch2_agent.record_writer import BoundedRecordWriter

WAIT_TIMEOUT = 2.0  # テストがスレッドの進みを待つ上限 (秒)
SHORT_WAIT = 0.05  # 書き出しスレッドが (誤って) 進むのを待つ短い時間 (秒)
POLL_INTERVAL = 0.01
FRAMES_FILE = "frames.jsonl"
PAD_FILE = "pad.jsonl"
# 1280x720 の NV12 (中間の灰色)。フレームどうしで同じ配列を共有する
NV12 = np.full((DEVICE_HEIGHT * 3 // 2, DEVICE_WIDTH), 128, np.uint8)


def frame(time: float, sharpness: float = 100.0, gray: int = 0) -> TimedFrame:
    """時刻 time・鮮明度 sharpness の TimedFrame を返す。gray_small は全画素が gray の 180x320 の画像。"""
    return TimedFrame(
        time=time,
        nv12=NV12,
        gray_small=np.full((180, 320), gray, np.uint8),
        sharpness=sharpness,
    )


def report(
    mono: float,
    source: str = "apply",
    state: PadState | None = None,
    ok: bool = True,
    error: str | None = None,
) -> PadReport:
    return PadReport(
        mono=mono,
        source=source,
        state=state if state is not None else PadState(),
        ok=ok,
        error=error,
    )


class LogRecorder:
    """run.jsonl の log の代わり。受け取った辞書を events に入れる。fail_with があれば、入れた後にその例外を出す。"""

    def __init__(self, fail_with: Exception | None = None):
        self.events: list[dict] = []
        self.fail_with = fail_with

    def __call__(self, event: dict) -> None:
        self.events.append(event)
        if self.fail_with is not None:
            raise self.fail_with

    def named(self, name: str) -> list[dict]:
        return [event for event in self.events if event["event"] == name]


def record(
    recorder: DashboardRecorder, detector: SharpnessValleyDetector, frame: TimedFrame
) -> None:
    """frame を detector に入れ、その前後の状態を recorder.record_frame に渡す。"""
    before = snapshot_detector(detector)
    kept = detector.feed(frame)
    recorder.record_frame(
        frame, before, snapshot_detector(detector), kept, detector.valley_ratio
    )


def read_lines(path: Path) -> list[dict]:
    """JSON Lines のファイルの行を読む。ファイルが無ければ空のリストを返す。"""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def wait_lines(path: Path, count: int) -> None:
    """path に count 行以上が書かれるまで待つ (最大 WAIT_TIMEOUT 秒)。"""
    deadline = time.monotonic() + WAIT_TIMEOUT
    while len(read_lines(path)) < count:
        if time.monotonic() >= deadline:
            pytest.fail(f"{path.name} did not have {count} lines in {WAIT_TIMEOUT} s")
        time.sleep(POLL_INTERVAL)


def wait_for(event: threading.Event, what: str) -> None:
    if not event.wait(WAIT_TIMEOUT):
        pytest.fail(f"{what} did not happen within {WAIT_TIMEOUT} seconds")


def wait_until(condition, what: str) -> None:
    """condition() が真になるまで待つ (最大 WAIT_TIMEOUT 秒)。"""
    deadline = time.monotonic() + WAIT_TIMEOUT
    while not condition():
        if time.monotonic() >= deadline:
            pytest.fail(f"{what} did not happen within {WAIT_TIMEOUT} seconds")
        time.sleep(POLL_INTERVAL)


# このプロセスが開いているファイルの一覧 (Linux の /proc)。無い環境では、ファイルを閉じたかのテストを飛ばす
PROC_FD_DIR = Path("/proc/self/fd")
needs_proc_fd = pytest.mark.skipif(
    not PROC_FD_DIR.is_dir(),
    reason="開いているファイルを /proc/self/fd で調べるため",
)


def open_paths() -> set[Path]:
    """このプロセスが今開いているファイルのパスの集合を返す。"""
    paths = set()
    for fd in PROC_FD_DIR.iterdir():
        # 一覧を読んでいる間に閉じたファイルは飛ばす
        try:
            paths.add(fd.readlink())
        except OSError:
            pass
    return paths


@pytest.fixture
def thread_errors(monkeypatch) -> list[threading.ExceptHookArgs]:
    """スレッドの外に出た例外 (threading.excepthook に渡されたもの) を集める。"""
    errors: list[threading.ExceptHookArgs] = []
    monkeypatch.setattr(threading, "excepthook", errors.append)
    return errors


class WriterGates:
    """DashboardRecorder が作る BoundedRecordWriter の、種類ごとの書き出しを止める仕掛け。

    capacities      種類 ("frames" / "pad") → 作るときに渡された capacity
    capacity        種類 → 渡された capacity の代わりに使う上限 (テストで満杯にしやすくするため)
    started         種類 → 最初の 1 件の書き出しに入ったら立つ
    gates           種類 → 開いている (set) 間は書き出しを止めない。最初は開いている
    finish_given    種類 → 作るときに on_finish が渡されたか
    finish_calls    種類 → 渡された on_finish を書き出しスレッドが呼んだ回数
    """

    def __init__(self):
        self.capacities: dict[str, int] = {}
        self.capacity: dict[str, int] = {}
        self.started = {"frames": threading.Event(), "pad": threading.Event()}
        self.gates = {"frames": threading.Event(), "pad": threading.Event()}
        for gate in self.gates.values():
            gate.set()
        self.finish_given: dict[str, bool] = {}
        self.finish_calls = {"frames": 0, "pad": 0}


@pytest.fixture
def writer_gates(monkeypatch) -> WriterGates:
    """dashboard_recorder.BoundedRecordWriter を、書き出しを止められる子クラスに差し替える。

    書き出し自体は本物の BoundedRecordWriter と、DashboardRecorder が渡した write_item・on_finish で行う。
    """
    gates = WriterGates()

    class GatedWriter(BoundedRecordWriter):
        def __init__(
            self, kind, capacity, write_item, on_error, *, on_finish=None, **options
        ):
            gates.capacities[kind] = capacity
            gates.finish_given[kind] = on_finish is not None

            def gated_write(item):
                gates.started[kind].set()
                gates.gates[kind].wait(WAIT_TIMEOUT)
                write_item(item)

            def counted_finish():
                gates.finish_calls[kind] += 1
                if on_finish is not None:
                    on_finish()

            super().__init__(
                kind,
                gates.capacity.get(kind, capacity),
                gated_write,
                on_error,
                on_finish=counted_finish,
                **options,
            )

    monkeypatch.setattr(dashboard_recorder, "BoundedRecordWriter", GatedWriter)
    return gates


# --- 定数と作成 ---


def test_constants():
    assert FRAMES_QUEUE_CAPACITY == 600
    assert PAD_QUEUE_CAPACITY == 2000
    assert RECORDER_CLOSE_WAIT_S == 3.0


def test_init_creates_thumbs_dir(tmp_path):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    assert (tmp_path / "thumbs").is_dir()
    recorder.close()


def test_init_accepts_existing_thumbs_dir(tmp_path):
    (tmp_path / "thumbs").mkdir()
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    assert (tmp_path / "thumbs").is_dir()
    recorder.close()


def test_writers_use_queue_capacities(tmp_path, writer_gates):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    assert writer_gates.capacities == {"frames": 600, "pad": 2000}
    recorder.close()


# --- frames.jsonl と thumbs/ ---


def test_frames_jsonl_has_one_line_per_frame(tmp_path):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    detector = SharpnessValleyDetector()
    record(recorder, detector, frame(1.5, 100.0, gray=10))
    # 40 は山 (100) * 0.5 以下なので、谷に向かい始める
    record(recorder, detector, frame(1.6, 40.0, gray=30))
    recorder.close()
    assert read_lines(tmp_path / FRAMES_FILE) == [
        {
            "mono": 1.5,
            "sharpness": 100.0,
            # 最初のフレームは比べる相手が無い
            "diff": None,
            "phase": "rising",
            "peak_mono": 1.5,
            "peak_sharpness": 100.0,
            "ref": 50.0,
            "event": None,
            "thumb": "thumbs/1500000.jpg",
        },
        {
            "mono": 1.6,
            "sharpness": 40.0,
            # 直前に書き出したフレーム (gray 10) との差分
            "diff": 20.0,
            "phase": "falling",
            "peak_mono": 1.5,
            "peak_sharpness": 100.0,
            "ref": 80.0,
            "event": {"kind": "fall"},
            "thumb": "thumbs/1600000.jpg",
        },
    ]


def test_frame_line_is_readable_before_close(tmp_path):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    record(recorder, SharpnessValleyDetector(), frame(1.5))
    # 1 行ごとに書き出すので、close の前でもほかの読み手が読める
    wait_lines(tmp_path / FRAMES_FILE, 1)
    recorder.close()


def test_thumb_is_320x180_jpeg_named_after_frame_time(tmp_path):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    record(recorder, SharpnessValleyDetector(), frame(12345.678901))
    recorder.close()
    [line] = read_lines(tmp_path / FRAMES_FILE)
    assert line["thumb"] == "thumbs/12345678901.jpg"
    data = (tmp_path / "thumbs" / "12345678901.jpg").read_bytes()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape == (180, 320, 3)


def test_frames_are_written_in_time_order(tmp_path):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    detector = SharpnessValleyDetector()
    times = [round(1.0 + i * 0.1, 1) for i in range(20)]
    for t in times:
        record(recorder, detector, frame(t))
    recorder.close()
    lines = read_lines(tmp_path / FRAMES_FILE)
    assert [line["mono"] for line in lines] == times
    # 1 行ごとに、その行の thumb の縮小画像がある
    assert len({line["thumb"] for line in lines}) == len(times)
    assert all((tmp_path / line["thumb"]).is_file() for line in lines)
    assert len(list((tmp_path / "thumbs").iterdir())) == len(times)


def test_diff_is_none_after_dropped_frame(tmp_path, writer_gates):
    writer_gates.capacity["frames"] = 1
    writer_gates.gates["frames"].clear()
    log = LogRecorder()
    recorder = DashboardRecorder(tmp_path, log)
    detector = SharpnessValleyDetector()
    record(recorder, detector, frame(1.0, gray=10))
    wait_for(writer_gates.started["frames"], "first frame write")
    started_at = time.monotonic()
    # 書き出しが止まっている間、1 枚はキューに入り、次の 1 枚は満杯で捨てる
    record(recorder, detector, frame(1.1, gray=20))
    record(recorder, detector, frame(1.2, gray=30))
    # 記録する側は、書き出しを待たずに戻る
    assert time.monotonic() - started_at < WAIT_TIMEOUT / 4
    writer_gates.gates["frames"].set()
    # キューの上限が 1 件なので、1 枚ずつ書き出されるのを待ってから次のフレームを渡す
    wait_lines(tmp_path / FRAMES_FILE, 2)
    record(recorder, detector, frame(1.3, gray=40))
    wait_lines(tmp_path / FRAMES_FILE, 3)
    record(recorder, detector, frame(1.4, gray=60))
    recorder.close()
    lines = read_lines(tmp_path / FRAMES_FILE)
    assert [line["mono"] for line in lines] == [1.0, 1.1, 1.3, 1.4]
    # 捨てたフレームの次は、比べる相手が直前のフレームではないため差分を求めない
    assert [line["diff"] for line in lines] == [None, 10.0, None, 20.0]
    assert not (tmp_path / "thumbs" / "1200000.jpg").exists()
    assert log.named("record_end") == [
        {
            "event": "record_end",
            "written": {"frames": 4, "pad": 0},
            "dropped": {"frames": 1, "pad": 0},
        }
    ]


# --- pad.jsonl ---


def test_pad_jsonl_has_one_line_per_report(tmp_path):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    recorder.record_pad(
        report(1.5, "apply", PadState(frozenset({"A"}), {"LS": (0.8, 0.0)}))
    )
    recorder.record_pad(report(1.62, "tap_release"))
    recorder.record_pad(report(1.7, "neutral", ok=False, error="pipe closed"))
    recorder.close()
    assert read_lines(tmp_path / PAD_FILE) == [
        {
            "mono": 1.5,
            "source": "apply",
            "state": "A+LS:0.8,0",
            "ok": True,
            "error": None,
        },
        {
            "mono": 1.62,
            "source": "tap_release",
            "state": "none",
            "ok": True,
            "error": None,
        },
        {
            "mono": 1.7,
            "source": "neutral",
            "state": "none",
            "ok": False,
            "error": "pipe closed",
        },
    ]


def test_pad_line_is_readable_before_close(tmp_path):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    recorder.record_pad(report(1.5))
    wait_lines(tmp_path / PAD_FILE, 1)
    recorder.close()


def test_pad_line_keeps_non_ascii_and_lone_surrogate_readable(tmp_path):
    error = "ブリッジが応答しない\ud800"
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    recorder.record_pad(report(1.5, ok=False, error=error))
    recorder.close()
    assert "ブリッジが応答しない" in (tmp_path / PAD_FILE).read_text(encoding="utf-8")
    # 単独のサロゲートは escape として書くので、読み直すと同じ値になる
    assert read_lines(tmp_path / PAD_FILE)[0]["error"] == error


# --- close ---


def test_close_logs_record_end_with_counts(tmp_path):
    log = LogRecorder()
    recorder = DashboardRecorder(tmp_path, log)
    detector = SharpnessValleyDetector()
    record(recorder, detector, frame(1.0))
    record(recorder, detector, frame(1.1))
    for mono in (1.0, 1.1, 1.2):
        recorder.record_pad(report(mono))
    recorder.close()
    assert log.events == [
        {
            "event": "record_end",
            "written": {"frames": 2, "pad": 3},
            "dropped": {"frames": 0, "pad": 0},
        }
    ]
    assert len(read_lines(tmp_path / FRAMES_FILE)) == 2
    assert len(read_lines(tmp_path / PAD_FILE)) == 3


def test_close_twice_logs_record_end_once(tmp_path):
    log = LogRecorder()
    recorder = DashboardRecorder(tmp_path, log)
    recorder.close()
    recorder.close()
    assert len(log.named("record_end")) == 1


def test_records_after_close_are_not_written_and_do_not_raise(tmp_path):
    log = LogRecorder()
    recorder = DashboardRecorder(tmp_path, log)
    recorder.close()
    record(recorder, SharpnessValleyDetector(), frame(1.0))
    recorder.record_pad(report(1.0))
    time.sleep(SHORT_WAIT)
    assert read_lines(tmp_path / FRAMES_FILE) == []
    assert read_lines(tmp_path / PAD_FILE) == []
    assert len(log.named("record_end")) == 1


def test_close_waits_both_queues_within_one_limit(tmp_path, writer_gates, monkeypatch):
    close_wait_s = 0.3
    monkeypatch.setattr(dashboard_recorder, "RECORDER_CLOSE_WAIT_S", close_wait_s)
    for gate in writer_gates.gates.values():
        gate.clear()
    log = LogRecorder()
    recorder = DashboardRecorder(tmp_path, log)
    detector = SharpnessValleyDetector()
    record(recorder, detector, frame(1.0))
    recorder.record_pad(report(1.0))
    wait_for(writer_gates.started["frames"], "first frame write")
    wait_for(writer_gates.started["pad"], "first pad write")
    record(recorder, detector, frame(1.1))
    recorder.record_pad(report(1.1))
    started_at = time.monotonic()
    recorder.close()
    elapsed = time.monotonic() - started_at
    # 2 つのキューを合わせて最大 close_wait_s 秒待つ (1 つずつ close_wait_s 秒なら 2 倍かかる)
    assert close_wait_s * 0.8 <= elapsed < close_wait_s * 1.7
    # 書いている途中の 1 件とキューに残っていた 1 件を、種類ごとに捨てた件数に足す
    assert log.named("record_end") == [
        {
            "event": "record_end",
            "written": {"frames": 0, "pad": 0},
            "dropped": {"frames": 2, "pad": 2},
        }
    ]
    # 止まっていた書き出しが戻っても、キューに残っていた分は書かない
    for gate in writer_gates.gates.values():
        gate.set()
    time.sleep(SHORT_WAIT * 2)
    assert 1.1 not in [line["mono"] for line in read_lines(tmp_path / FRAMES_FILE)]
    assert 1.1 not in [line["mono"] for line in read_lines(tmp_path / PAD_FILE)]
    assert not (tmp_path / "thumbs" / "1100000.jpg").exists()


def test_close_finishes_both_writers_with_on_finish(tmp_path, writer_gates):
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    record(recorder, SharpnessValleyDetector(), frame(1.0))
    recorder.record_pad(report(1.0))
    recorder.close()
    # どちらの writer にも、ファイルを閉じる on_finish を渡し、close までに 1 回ずつ呼ばれる
    assert writer_gates.finish_given == {"frames": True, "pad": True}
    assert writer_gates.finish_calls == {"frames": 1, "pad": 1}


@needs_proc_fd
def test_close_closes_jsonl_files(tmp_path):
    frames_path = (tmp_path / FRAMES_FILE).resolve()
    pad_path = (tmp_path / PAD_FILE).resolve()
    recorder = DashboardRecorder(tmp_path, LogRecorder())
    record(recorder, SharpnessValleyDetector(), frame(1.0))
    recorder.record_pad(report(1.0))
    wait_lines(tmp_path / FRAMES_FILE, 1)
    wait_lines(tmp_path / PAD_FILE, 1)
    # 最初の 1 件で開いたファイルは、書き出しスレッドが持ち続ける
    assert {frames_path, pad_path} <= open_paths()
    recorder.close()
    assert frames_path not in open_paths()
    assert pad_path not in open_paths()


@needs_proc_fd
def test_write_failure_closes_frames_file_before_close(tmp_path):
    frames_path = (tmp_path / FRAMES_FILE).resolve()
    # 2 枚目の縮小画像のファイルの名前にディレクトリーがあるので、2 枚目で書けなくなる
    (tmp_path / "thumbs" / "1600000.jpg").mkdir(parents=True)
    log = LogRecorder()
    recorder = DashboardRecorder(tmp_path, log)
    detector = SharpnessValleyDetector()
    record(recorder, detector, frame(1.5))
    wait_lines(tmp_path / FRAMES_FILE, 1)
    record(recorder, detector, frame(1.6))
    wait_until(lambda: log.named("record_error"), "record_error")
    # 失敗で止めたら、close を待たずに書き出しスレッドが終わり、ファイルを閉じる
    wait_until(lambda: frames_path not in open_paths(), "closing frames.jsonl")
    recorder.close()
    assert len(read_lines(tmp_path / FRAMES_FILE)) == 1


# --- 書き込みの失敗 ---


def test_thumb_write_failure_stops_frames_and_logs_record_error(tmp_path):
    # 縮小画像のファイルの名前にディレクトリーがあるので、書けない
    (tmp_path / "thumbs" / "1500000.jpg").mkdir(parents=True)
    log = LogRecorder()
    recorder = DashboardRecorder(tmp_path, log)
    detector = SharpnessValleyDetector()
    record(recorder, detector, frame(1.5))
    record(recorder, detector, frame(1.6))
    recorder.record_pad(report(1.5))
    recorder.close()
    [record_error] = log.named("record_error")
    assert record_error["kind"] == "frames"
    assert isinstance(record_error["error"], str) and record_error["error"]
    assert set(record_error) == {"event", "kind", "error"}
    assert read_lines(tmp_path / FRAMES_FILE) == []
    # pad の記録は止めない
    assert len(read_lines(tmp_path / PAD_FILE)) == 1
    assert log.named("record_end") == [
        {
            "event": "record_end",
            "written": {"frames": 0, "pad": 1},
            "dropped": {"frames": 2, "pad": 0},
        }
    ]


def test_pad_write_failure_stops_pad_and_logs_record_error(tmp_path):
    # pad.jsonl の名前にディレクトリーがあるので、書けない
    (tmp_path / PAD_FILE).mkdir()
    log = LogRecorder()
    recorder = DashboardRecorder(tmp_path, log)
    recorder.record_pad(report(1.5))
    recorder.record_pad(report(1.6))
    record(recorder, SharpnessValleyDetector(), frame(1.5))
    recorder.close()
    [record_error] = log.named("record_error")
    assert record_error["kind"] == "pad"
    # frames の記録は止めない
    assert len(read_lines(tmp_path / FRAMES_FILE)) == 1
    assert log.named("record_end") == [
        {
            "event": "record_end",
            "written": {"frames": 1, "pad": 0},
            "dropped": {"frames": 0, "pad": 2},
        }
    ]


def test_log_failure_does_not_escape(tmp_path, thread_errors):
    (tmp_path / "thumbs" / "1500000.jpg").mkdir(parents=True)
    log = LogRecorder(fail_with=RuntimeError("run.jsonl is not writable"))
    recorder = DashboardRecorder(tmp_path, log)
    record(recorder, SharpnessValleyDetector(), frame(1.5))
    recorder.record_pad(report(1.5))
    # record_error と record_end の log が失敗しても、書き出しスレッドと close は例外を出さない
    recorder.close()
    assert [event["event"] for event in log.events] == ["record_error", "record_end"]
    assert thread_errors == []
    assert len(read_lines(tmp_path / PAD_FILE)) == 1


# --- feed_detector ---


class RecordingRecorder:
    """DashboardRecorder の代わり。record_frame に渡された引数を calls に入れる。"""

    def __init__(self):
        self.calls: list[tuple] = []

    def record_frame(self, frame, before, after, kept, valley_ratio):
        self.calls.append((frame, before, after, kept, valley_ratio))


def test_feed_detector_without_recorder_keeps_meaningful_frames():
    detector = SharpnessValleyDetector()
    store = MeaningfulFrameStore()
    # 鮮明度 200 の山の後に谷 (50) が来て回復 (150) すると、山のフレームが意味のあるフレームになる
    frames = [frame(float(t), s) for t, s in enumerate([100, 200, 50, 150])]
    for f in frames:
        feed_detector(f, detector, store, None)
    assert store.latest_before(10.0, 5) == [frames[1]]


def test_feed_detector_passes_states_before_and_after_feed_to_recorder():
    detector = SharpnessValleyDetector(valley_ratio=0.4)
    store = MeaningfulFrameStore()
    recorder = RecordingRecorder()
    frames = [frame(float(t), s) for t, s in enumerate([100, 200, 80, 50, 150])]
    for f in frames:
        feed_detector(f, detector, store, recorder)
    assert [call[0] for call in recorder.calls] == frames
    assert all(call[4] == 0.4 for call in recorder.calls)
    # 最初のフレーム: 判定の前は何も無い
    _, before, after, kept, _ = recorder.calls[0]
    assert before == DetectorSnapshot("rising", None, None, None)
    assert after.peak is frames[0]
    assert kept is None
    # 80 (山 200 * 0.4 以下) で谷に向かい始める
    _, before, after, kept, _ = recorder.calls[2]
    assert (before.phase, after.phase) == ("rising", "falling")
    assert after.valley is frames[2]
    assert kept is None
    # 150 (谷 50 / 0.4 以上) で谷を確定し、前の区間の山 (200) を残す
    _, before, after, kept, _ = recorder.calls[4]
    assert (before.phase, before.peak, before.valley) == (
        "falling",
        frames[1],
        frames[3],
    )
    assert (after.phase, after.peak, after.last_kept) == (
        "rising",
        frames[4],
        frames[1],
    )
    assert kept is frames[1]
    assert store.latest_before(10.0, 5) == [frames[1]]
