import base64
import json
from pathlib import Path

import httpx
import pytest
from switch2_remote.controller_bridge import BridgeStartError

from switch2_layers import cli
from switch2_layers.app import REASON_INTERRUPTED
from switch2_layers.cli import (
    build_parser,
    create_middle_analyzer,
    main,
    middle_once_settings_from_args,
    run_settings_from_args,
)
from switch2_layers.middle.fake_middle import FakeMiddleAnalyzer
from switch2_layers.middle.luna_middle import LunaMiddleAnalyzer
from switch2_layers.notes.publish_note import EXIT_CONFLICT, EXIT_INVALID
from switch2_layers.settings import (
    DEFAULT_DEEP_AUTO_SECONDS,
    DEFAULT_DEEP_MIN_START_INTERVAL_S,
    DEFAULT_DEEP_TIMEOUT_S,
    DEFAULT_INTERVAL_FPS,
    DEFAULT_INTERVAL_WIDTH,
    DEFAULT_MAX_MIDDLE_ERRORS,
    DEFAULT_MAX_SURFACE_ERRORS,
    DEFAULT_MIDDLE_INTERVAL_S,
    DEFAULT_MIDDLE_WINDOW_S,
    MiddleKind,
)

COMMANDS = ["run", "middle-once", "deep-once", "publish-note"]
API_KEY_ENV = "OPENAI_API_KEY"


@pytest.fixture(autouse=True)
def without_api_key(monkeypatch):
    """どのテストも、開発者の環境の API キーで実際の API を呼ばないよう、OPENAI_API_KEY を消してから動かす。"""
    monkeypatch.delenv(API_KEY_ENV, raising=False)


# --- 使い方の表示と引数の定義 ---


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for command in COMMANDS:
        assert command in out


@pytest.mark.parametrize("command", COMMANDS)
def test_help_of_each_command_exits_with_zero(command, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"])
    assert exit_info.value.code == 0
    assert command in capsys.readouterr().out


def test_command_is_required():
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2


def test_run_defaults_follow_the_issue():
    args = build_parser().parse_args(["run"])
    assert args.runs_dir == Path("runs")
    assert args.interval_fps == DEFAULT_INTERVAL_FPS == 5
    assert args.interval_width == DEFAULT_INTERVAL_WIDTH == 640
    assert args.max_surface_errors == DEFAULT_MAX_SURFACE_ERRORS == 5
    assert args.middle_interval == DEFAULT_MIDDLE_INTERVAL_S == 5.0
    assert args.middle_window == DEFAULT_MIDDLE_WINDOW_S == 10
    assert args.max_middle_errors == DEFAULT_MAX_MIDDLE_ERRORS == 5
    assert args.deep_min_start_interval == DEFAULT_DEEP_MIN_START_INTERVAL_S == 15
    assert args.deep_auto_seconds == DEFAULT_DEEP_AUTO_SECONDS == 60
    assert args.deep_timeout == DEFAULT_DEEP_TIMEOUT_S == 300
    assert args.allow_home is False


def test_run_accepts_all_options():
    args = build_parser().parse_args(
        [
            "run",
            "--runs-dir",
            "elsewhere",
            "--interval-fps",
            "2.5",
            "--interval-width",
            "320",
            "--max-surface-errors",
            "3",
            "--middle-interval",
            "1",
            "--middle-window",
            "20",
            "--max-middle-errors",
            "2",
            "--deep-min-start-interval",
            "0",
            "--deep-auto-seconds",
            "30",
            "--deep-timeout",
            "60",
            "--allow-home",
        ]
    )
    assert args.runs_dir == Path("elsewhere")
    assert args.interval_fps == 2.5
    assert args.interval_width == 320
    assert args.max_surface_errors == 3
    assert args.middle_interval == 1.0
    assert args.middle_window == 20.0
    assert args.max_middle_errors == 2
    assert args.deep_min_start_interval == 0.0
    assert args.deep_auto_seconds == 30.0
    assert args.deep_timeout == 60.0
    assert args.allow_home is True


def test_run_rejects_auto_seconds_shorter_than_min_interval_without_hardware(
    monkeypatch, capsys
):
    def must_not_start_bridge(**kwargs):
        raise AssertionError("the bridge must not be started")

    # 範囲の誤りでは、実機 (ブリッジ) に触れずに終了する
    monkeypatch.setattr(cli, "BridgeSupervisor", must_not_start_bridge)
    # 既定の --deep-min-start-interval は 15
    code = main(["run", "--deep-auto-seconds", "5"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--deep-auto-seconds" in captured.err


def test_deep_once_rejects_unknown_document_kind():
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(
            ["deep-once", "--run-dir", "runs/x", "--doc", "unknown"]
        )
    assert exit_info.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["middle-once"],
        ["deep-once", "--run-dir", "runs/x"],
        ["publish-note", "--run-dir", "runs/x", "--note", "advice.md"],
    ],
)
def test_required_options(argv):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_publish_note_from_option_is_stored_as_source():
    args = build_parser().parse_args(
        [
            "publish-note",
            "--run-dir",
            "runs/x",
            "--note",
            "advice.md",
            "--base-sha256",
            "none",
            "--from",
            "edited.md",
        ]
    )
    assert args.source == Path("edited.md")
    assert args.base_sha256 == "none"
    assert args.note == "advice.md"


# --- サブコマンドの処理 (保存済みの実行ディレクトリーに対して) ---


def make_saved_run(tmp_path: Path) -> Path:
    """middle-once・deep-once・publish-note が対象にする、最小の実行ディレクトリーを作って返す (パッケージのコードは使わない)。"""
    run_dir = tmp_path / "runs" / "20261115-120000"
    (run_dir / "notes" / "common").mkdir(parents=True)
    (run_dir / "notes" / "map").mkdir()
    (run_dir / "middle").mkdir()
    (run_dir / "deep").mkdir()
    (run_dir / "frames" / "interval").mkdir(parents=True)
    (run_dir / "frames" / "surface").mkdir()
    (run_dir / "notes" / "strategy.md").write_text("# 大方針\n", encoding="utf-8")
    (run_dir / "notes" / "advice.md").write_text("# 助言\n", encoding="utf-8")
    (run_dir / "notes" / "common" / "controls.md").write_text(
        "# 操作説明\n", encoding="utf-8"
    )
    (run_dir / "notes" / "common" / "tips.md").write_text(
        "# 攻略情報\n", encoding="utf-8"
    )
    (run_dir / "notes" / "scene_labels.json").write_text(
        '{"scenes": []}', encoding="utf-8"
    )
    return run_dir


def job_work_file(run_dir: Path) -> Path:
    """deep のジョブの work/ の中のファイルのパスを返す (publish-note の --from に使える置き場所。親は作る)。"""
    path = run_dir / "deep" / "tips-001" / "work" / "edited.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_publish_note_command_publishes_and_prints_json(tmp_path, capsys):
    run_dir = make_saved_run(tmp_path)
    edited = job_work_file(run_dir)
    edited.write_text("# 助言\n\n新しい助言\n", encoding="utf-8")
    code = main(
        [
            "publish-note",
            "--run-dir",
            str(run_dir),
            "--note",
            "scenes/scene-001/tips.md",
            "--base-sha256",
            "none",
            "--from",
            str(edited),
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "published"
    assert len(out["sha256"]) == 64
    assert (run_dir / "notes" / "scenes" / "scene-001" / "tips.md").read_text(
        encoding="utf-8"
    ) == "# 助言\n\n新しい助言\n"


def test_publish_note_command_conflict_exit_code(tmp_path, capsys):
    run_dir = make_saved_run(tmp_path)
    edited = job_work_file(run_dir)
    edited.write_text("x", encoding="utf-8")
    code = main(
        [
            "publish-note",
            "--run-dir",
            str(run_dir),
            "--note",
            "advice.md",
            "--base-sha256",
            "none",
            "--from",
            str(edited),
        ]
    )
    assert code == EXIT_CONFLICT == 3
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "conflict"
    assert len(out["current_sha256"]) == 64


def test_publish_note_command_outside_path_exit_code(tmp_path, capsys):
    run_dir = make_saved_run(tmp_path)
    edited = job_work_file(run_dir)
    edited.write_text("x", encoding="utf-8")
    code = main(
        [
            "publish-note",
            "--run-dir",
            str(run_dir),
            "--note",
            "../run.jsonl",
            "--base-sha256",
            "none",
            "--from",
            str(edited),
        ]
    )
    assert code == EXIT_INVALID == 2
    assert capsys.readouterr().out == ""
    assert not (run_dir / "run.jsonl").exists()


def test_publish_note_command_rejects_source_outside_job_work(tmp_path, capsys):
    run_dir = make_saved_run(tmp_path)
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=secret\n", encoding="utf-8")
    code = main(
        [
            "publish-note",
            "--run-dir",
            str(run_dir),
            "--note",
            "scenes/scene-001/tips.md",
            "--base-sha256",
            "none",
            "--from",
            str(secret),
        ]
    )
    assert code == EXIT_INVALID == 2
    assert capsys.readouterr().out == ""
    assert not (run_dir / "notes" / "scenes").exists()


def test_middle_once_command_writes_middle_outputs(tmp_path, capsys):
    run_dir = make_saved_run(tmp_path)
    # フェイクの middle は API キーが無くても動く
    code = main(["middle-once", "--run-dir", str(run_dir), "--middle", "fake"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["new_scene"] is True
    assert (run_dir / "middle" / "state.json").is_file()
    assert (run_dir / "middle" / "calls.jsonl").is_file()
    # middle-once では deep を起動しない
    assert list((run_dir / "deep").iterdir()) == []


def test_middle_once_command_missing_run_dir(tmp_path, capsys):
    code = main(["middle-once", "--run-dir", str(tmp_path / "missing")])
    assert code == 2
    assert capsys.readouterr().out == ""


def test_deep_once_command_creates_job_directory(tmp_path, capsys):
    run_dir = make_saved_run(tmp_path)
    code = main(["deep-once", "--run-dir", str(run_dir), "--doc", "strategy"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["job"] == "deep/strategy-001"
    assert out["status"] == "ok"
    job = json.loads(
        (run_dir / "deep" / "strategy-001" / "job.json").read_text(encoding="utf-8")
    )
    assert job["trigger"] == "manual"
    assert job["kind"] == "strategy"


@pytest.mark.parametrize(
    "extra",
    [
        # 場面 ID の形でない
        ["--doc", "controls", "--scene", "../x"],
        # controls・tips 以外には場面を付けられない
        ["--doc", "strategy", "--scene", "scene-001"],
    ],
)
def test_deep_once_command_rejects_invalid_scene(tmp_path, capsys, extra):
    run_dir = make_saved_run(tmp_path)
    code = main(["deep-once", "--run-dir", str(run_dir), *extra])
    assert code == 2
    assert capsys.readouterr().out == ""
    assert list((run_dir / "deep").iterdir()) == []


# --- --middle (middle の実装の選択) ---


@pytest.mark.parametrize(
    "argv", [["run"], ["middle-once", "--run-dir", "runs/x"]], ids=["run", "once"]
)
def test_middle_defaults_to_luna(argv):
    args = build_parser().parse_args(argv)
    assert args.middle == "luna"


@pytest.mark.parametrize(
    "argv", [["run"], ["middle-once", "--run-dir", "runs/x"]], ids=["run", "once"]
)
def test_middle_accepts_fake_and_rejects_unknown_values(argv):
    assert build_parser().parse_args([*argv, "--middle", "fake"]).middle == "fake"
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*argv, "--middle", "gpt"])
    assert exit_info.value.code == 2


def test_settings_from_args_convert_middle_to_kind():
    run_args = build_parser().parse_args(["run", "--middle", "fake"])
    assert run_settings_from_args(run_args).middle is MiddleKind.FAKE
    once_args = build_parser().parse_args(["middle-once", "--run-dir", "runs/x"])
    assert middle_once_settings_from_args(once_args).middle is MiddleKind.LUNA


def test_create_middle_analyzer_for_fake_does_not_need_api_key():
    assert isinstance(create_middle_analyzer(MiddleKind.FAKE, {}), FakeMiddleAnalyzer)


def test_create_middle_analyzer_for_luna_uses_api_key_from_environ():
    analyzer = create_middle_analyzer(MiddleKind.LUNA, {API_KEY_ENV: " sk-test\n"})
    assert isinstance(analyzer, LunaMiddleAnalyzer)
    assert analyzer.api_key == "sk-test"


@pytest.mark.parametrize("environ", [{}, {API_KEY_ENV: ""}, {API_KEY_ENV: "  "}])
def test_create_middle_analyzer_for_luna_without_api_key_is_an_error(environ):
    with pytest.raises(ValueError):
        create_middle_analyzer(MiddleKind.LUNA, environ)


def must_not_start_bridge(**kwargs):
    raise AssertionError("the bridge must not be started")


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_run_without_api_key_exits_before_hardware(
    tmp_path, monkeypatch, capsys, value
):
    if value is not None:
        monkeypatch.setenv(API_KEY_ENV, value)
    monkeypatch.setattr(cli, "BridgeSupervisor", must_not_start_bridge)
    runs_dir = tmp_path / "runs"
    code = main(["run", "--runs-dir", str(runs_dir)])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert API_KEY_ENV in captured.err
    assert "--middle fake" in captured.err
    # 実行ディレクトリーも作らない
    assert not runs_dir.exists()


def test_run_range_errors_are_reported_before_api_key(monkeypatch, capsys):
    monkeypatch.setattr(cli, "BridgeSupervisor", must_not_start_bridge)
    code = main(["run", "--middle-interval", "0"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--middle-interval" in err
    assert API_KEY_ENV not in err


def test_run_with_fake_middle_does_not_need_api_key(monkeypatch, capsys):
    def failing_bridge(**kwargs):
        raise BridgeStartError("controller bridge failed: no bridge in test")

    # API キーの確かめを通り、ブリッジの起動 (ここでは失敗させる) まで進む
    monkeypatch.setattr(cli, "BridgeSupervisor", failing_bridge)
    code = main(["run", "--middle", "fake"])
    assert code == 1
    assert "no bridge in test" in capsys.readouterr().err


class StubSupervisor:
    state = "ok"

    def close(self) -> None:
        pass


class StubCapture:
    def close(self) -> None:
        pass


def stub_hardware_and_app(monkeypatch) -> list:
    """run の実機の部品と LayersApp を偽物にし、LayersApp に渡された (settings, parts) を記録するリストを返す。"""
    created: list = []

    class StubApp:
        def __init__(self, settings, parts):
            created.append((settings, parts))

        def run(self) -> str:
            return REASON_INTERRUPTED

    monkeypatch.setattr(cli, "BridgeSupervisor", lambda **kwargs: StubSupervisor())
    monkeypatch.setattr(cli, "Capture", lambda *args: StubCapture())
    monkeypatch.setattr(cli, "SwitchCaptureReader", lambda capture: object())
    monkeypatch.setattr(cli, "LayersApp", StubApp)
    return created


def test_run_passes_luna_middle_to_app_by_default(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    created = stub_hardware_and_app(monkeypatch)
    assert main(["run"]) == 0
    [(settings, parts)] = created
    assert settings.middle is MiddleKind.LUNA
    assert isinstance(parts.middle, LunaMiddleAnalyzer)
    assert parts.middle.api_key == "sk-test"


def test_run_passes_fake_middle_to_app(monkeypatch):
    created = stub_hardware_and_app(monkeypatch)
    assert main(["run", "--middle", "fake"]) == 0
    [(settings, parts)] = created
    assert settings.middle is MiddleKind.FAKE
    assert isinstance(parts.middle, FakeMiddleAnalyzer)


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_middle_once_without_api_key_exits_and_writes_nothing(
    tmp_path, monkeypatch, capsys, value
):
    if value is not None:
        monkeypatch.setenv(API_KEY_ENV, value)
    run_dir = make_saved_run(tmp_path)
    code = main(["middle-once", "--run-dir", str(run_dir)])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert API_KEY_ENV in captured.err
    assert not (run_dir / "middle" / "state.json").exists()
    assert not (run_dir / "middle" / "calls.jsonl").exists()
    assert json.loads(
        (run_dir / "notes" / "scene_labels.json").read_text(encoding="utf-8")
    ) == {"scenes": []}


LUNA_OUTPUT = {
    "scene_name": "タイトル画面",
    "is_stuck": False,
    "stuck_description": "",
    "surface_wait_s": 1.5,
    "timeline": "## 操作のまとめ\n- まだ操作していない",
    "tasks": "- A でゲームを始める\n",
}
LUNA_USAGE = {
    "input_tokens": 3000,
    "input_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 0},
    "output_tokens": 400,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 3400,
}


def luna_response_body() -> dict:
    return {
        "id": "resp_test",
        "object": "response",
        "status": "completed",
        "incomplete_details": None,
        "model": "gpt-6-luna",
        "output": [
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(LUNA_OUTPUT, ensure_ascii=False),
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": LUNA_USAGE,
    }


def test_middle_once_with_luna_records_usage_and_cost(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    run_dir = make_saved_run(tmp_path)
    jpeg = b"\xff\xd8\xff\xe0saved-frame"
    (run_dir / "frames" / "interval" / "000005000.jpg").write_bytes(jpeg)
    (run_dir / "frames.jsonl").write_text(
        json.dumps(
            {
                "t": 5.0,
                "kind": "interval",
                "file": "frames/interval/000005000.jpg",
                "width": 640,
                "height": 360,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=luna_response_body())

    kinds: list[MiddleKind] = []

    def create_with_mock_transport(kind, environ):
        # 実際の API を呼ばないよう、MockTransport の Client を持つ LunaMiddleAnalyzer を渡す
        kinds.append(kind)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return LunaMiddleAnalyzer(environ[API_KEY_ENV], client=client)

    monkeypatch.setattr(cli, "create_middle_analyzer", create_with_mock_transport)
    code = main(["middle-once", "--run-dir", str(run_dir)])
    assert code == 0
    assert kinds == [MiddleKind.LUNA]
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["scene_id"] == "scene-001"
    assert out["new_scene"] is True
    # 保存済みの画像を送る
    [request] = requests
    payload = json.loads(request.content)
    images = [
        part["image_url"]
        for part in payload["input"][1]["content"]
        if part["type"] == "input_image"
    ]
    assert images == ["data:image/jpeg;base64," + base64.b64encode(jpeg).decode()]
    # calls.jsonl の行に usage と概算の費用と応答を足す
    [row] = [
        json.loads(line)
        for line in (run_dir / "middle" / "calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert row["error"] is None
    assert row["scene_name"] == "タイトル画面"
    assert row["usage"] == {
        "input_tokens": 3000,
        "cached_tokens": 1024,
        "cache_write_tokens": 0,
        "output_tokens": 400,
        "reasoning_tokens": 0,
    }
    assert row["cost_usd"] == pytest.approx(0.00040784)
    assert row["response"] == luna_response_body()
    assert row["error_body_head"] is None
    state = json.loads((run_dir / "middle" / "state.json").read_text(encoding="utf-8"))
    assert state["surface_wait_s"] == 1.5
