import base64
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from switch2_remote.controller_bridge import BridgeStartError

from switch2_layers import cli
from switch2_layers.app import REASON_INTERRUPTED, LayersParts
from switch2_layers.cli import (
    build_parser,
    create_middle_analyzer,
    create_system_screen_middle,
    main,
    middle_once_settings_from_args,
    run_settings_from_args,
)
from switch2_layers.deep.claude_code_runner import ClaudeCodeDeepRunner
from switch2_layers.deep.deep_scheduler import (
    DeepJobOutcome,
    DeepJobStatus,
    DeepOnceResult,
)
from switch2_layers.deep.fake_deep import FakeDeepRunner
from switch2_layers.middle.fake_middle import FakeMiddleAnalyzer
from switch2_layers.middle.luna_middle import LunaMiddleAnalyzer
from switch2_layers.middle.system_screen_middle import (
    FAKE_DONE_OUTPUT,
    LunaSystemScreenMiddle,
)
from switch2_layers.middle.system_screen_types import (
    SystemGoal,
    SystemScreenInput,
)
from switch2_layers.progress.screen_progress import DEFAULT_PROGRESS_WINDOW_S
from switch2_layers.progress.stuck_advice_trigger import (
    DEFAULT_STUCK_ADVICE_SECONDS,
    DEFAULT_SYSTEM_ADVICE_SECONDS,
)
from switch2_layers.safety.fake_safety import FakeSafetyChecker
from switch2_layers.safety.jev_safety import JevSafetyChecker
from switch2_layers.safety.safety_assembly import SafetyAssembly
from switch2_layers.settings import (
    DEFAULT_DEEP_AUTO_SECONDS,
    DEFAULT_DEEP_MIN_START_INTERVAL_S,
    DEFAULT_DEEP_TIMEOUT_S,
    DEFAULT_HOLD_MARGIN,
    DEFAULT_INTERVAL_FPS,
    DEFAULT_INTERVAL_WIDTH,
    DEFAULT_MAX_MIDDLE_ERRORS,
    DEFAULT_MAX_SURFACE_ERRORS,
    DEFAULT_MIDDLE_INTERVAL_S,
    DEFAULT_MIDDLE_WINDOW_S,
    DeepRunnerChoice,
    MiddleKind,
    RunSettings,
    SafetyKind,
    SurfaceKind,
    run_settings_to_json,
)
from switch2_layers.surface.fake_surface import FakeSurfaceDecider
from switch2_layers.surface.jev_surface import JevSurfaceDecider
from switch2_layers.surface.surface_assembly import SurfaceAssembly
from switch2_layers.system_screen.system_screen_references import SYSTEM_SCREEN_NAMES

COMMANDS = ["run", "middle-once", "deep-once"]
# run の必須の引数 (指定のソフトとユーザーの名前)
SOFTWARE = "テストのソフト"
USER = "テストのユーザー"
RUN = ["run", "--software", SOFTWARE, "--user", USER]
# deep が文書の本体を直接編集するため、文書を入れ替えるサブコマンドは無い
REMOVED_COMMAND = "publish-note"
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


def test_publish_note_command_does_not_exist(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert REMOVED_COMMAND not in capsys.readouterr().out
    # 知らないサブコマンドとして、argparse が使い方の誤り (終了コード 2) にする
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([REMOVED_COMMAND, "--help"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("command", COMMANDS)
def test_help_of_each_command_exits_with_zero(command, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"])
    assert exit_info.value.code == 0
    assert command in capsys.readouterr().out


def test_run_help_says_min_start_interval_neither_starts_nor_holds(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "--help"])
    assert exit_info.value.code == 0
    # argparse が折り返した行を、空白を除いてつなぎ直してから確かめる
    help_text = "".join(capsys.readouterr().out.split())
    assert "起動も保留もしない" in help_text


def test_command_is_required():
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2


def test_run_defaults_follow_the_issue():
    args = build_parser().parse_args(RUN)
    assert args.runs_dir == Path("runs")
    assert args.interval_fps == DEFAULT_INTERVAL_FPS == 5
    assert args.interval_width == DEFAULT_INTERVAL_WIDTH == 640
    assert args.max_surface_errors == DEFAULT_MAX_SURFACE_ERRORS == 5
    assert args.middle_interval == DEFAULT_MIDDLE_INTERVAL_S == 5.0
    assert args.middle_window == DEFAULT_MIDDLE_WINDOW_S == 10
    assert args.max_middle_errors == DEFAULT_MAX_MIDDLE_ERRORS == 5
    assert args.deep_min_start_interval == DEFAULT_DEEP_MIN_START_INTERVAL_S == 30
    assert args.deep_auto_seconds == DEFAULT_DEEP_AUTO_SECONDS == 60
    assert args.deep_timeout == DEFAULT_DEEP_TIMEOUT_S == 300
    assert args.software == SOFTWARE
    assert args.user == USER


def test_run_accepts_all_options():
    args = build_parser().parse_args(
        [
            *RUN,
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


# --- run の指定のソフトとユーザー (--software・--user) ---


@pytest.mark.parametrize(
    "argv",
    [
        ["run"],
        ["run", "--software", SOFTWARE],
        ["run", "--user", USER],
    ],
    ids=["neither", "without_user", "without_software"],
)
def test_run_requires_software_and_user(argv, capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(argv)
    assert exit_info.value.code == 2
    assert "required" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["", "   ", "\t"], ids=["empty", "spaces", "tab"])
@pytest.mark.parametrize("option", ["--software", "--user"])
def test_run_rejects_blank_software_or_user(no_hardware, option, name, capsys):
    values = {"--software": SOFTWARE, "--user": USER, option: name}
    argv = ["run", "--software", values["--software"], "--user", values["--user"]]
    with pytest.raises(SystemExit) as exit_info:
        main([*argv, *ALL_FAKE])
    assert exit_info.value.code == 2
    assert option in capsys.readouterr().err


def test_run_keeps_the_names_as_written_in_settings():
    args = build_parser().parse_args(
        ["run", "--software", "ソフト A", "--user", "ユーザー 1"]
    )
    settings = run_settings_from_args(args)
    assert settings.software_name == "ソフト A"
    assert settings.user_name == "ユーザー 1"


def test_run_rejects_allow_home(capsys):
    # HOME は surface に押させず、本体の画面の操作だけが押す (--allow-home は無い)
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*RUN, "--allow-home"])
    assert exit_info.value.code == 2
    assert "--allow-home" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["middle-once", "--run-dir", "runs/x"],
        ["deep-once", "--run-dir", "runs/x", "--doc", "advice"],
    ],
    ids=["middle_once", "deep_once"],
)
def test_once_commands_do_not_take_software_or_user(argv):
    # middle-once と deep-once は変えない (名前は要らず、渡すと知らない引数)
    args = build_parser().parse_args(argv)
    assert not hasattr(args, "software")
    assert not hasattr(args, "user")
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*argv, "--software", SOFTWARE])
    assert exit_info.value.code == 2


def test_run_rejects_auto_seconds_shorter_than_min_interval_without_hardware(
    monkeypatch, capsys
):
    def must_not_start_bridge(**kwargs):
        raise AssertionError("the bridge must not be started")

    # 範囲の誤りでは、実機 (ブリッジ) に触れずに終了する
    monkeypatch.setattr(cli, "BridgeSupervisor", must_not_start_bridge)
    # 既定の --deep-min-start-interval は 15
    code = main([*RUN, "--deep-auto-seconds", "5"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--deep-auto-seconds" in captured.err


# --- run の surface の引数と API キー ---


@pytest.fixture
def no_hardware(monkeypatch):
    """run が実機 (ブリッジとキャプチャー) に触れたらテストを失敗させ、TYPESAFE_API_KEY を消す。"""

    def must_not_start_bridge(**kwargs):
        raise AssertionError("the bridge must not be started")

    def must_not_start_capture(*args, **kwargs):
        raise AssertionError("the capture must not be started")

    monkeypatch.setattr(cli, "BridgeSupervisor", must_not_start_bridge)
    monkeypatch.setattr(cli, "Capture", must_not_start_capture)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


def failing_bridge(**kwargs):
    """最初のブリッジの起動に失敗するブリッジの偽物 (実機に触れる手前で止める)。"""
    raise BridgeStartError("stub bridge failed")


def test_run_surface_defaults_follow_the_issue():
    args = build_parser().parse_args(RUN)
    assert args.surface == "jev"
    assert args.threshold_advisor == 0.5
    assert args.box_requery_every == 5


def test_run_accepts_surface_options():
    args = build_parser().parse_args(
        [
            *RUN,
            "--surface",
            "fake",
            "--threshold-advisor",
            "0.7",
            "--box-requery-every",
            "3",
        ]
    )
    settings = cli.run_settings_from_args(args)
    assert settings.surface is SurfaceKind.FAKE
    assert settings.threshold_advisor == 0.7
    assert settings.box_requery_every == 3


def test_run_rejects_unknown_surface():
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*RUN, "--surface", "luna"])
    assert exit_info.value.code == 2


@pytest.mark.parametrize("key", [None, ""])
def test_run_without_typesafe_api_key_exits_before_hardware(
    no_hardware, monkeypatch, capsys, key
):
    if key is not None:
        monkeypatch.setenv("TYPESAFE_API_KEY", key)
    code = main([*RUN, "--middle", "fake"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "TYPESAFE_API_KEY" in captured.err


def test_run_range_error_is_reported_before_api_key(no_hardware, capsys):
    code = main([*RUN, "--threshold-advisor", "1.5"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--threshold-advisor" in err
    assert "TYPESAFE_API_KEY" not in err


def test_run_box_requery_every_range_error(no_hardware, capsys):
    code = main([*RUN, "--surface", "fake", "--box-requery-every", "0"])
    assert code == 2
    assert "--box-requery-every" in capsys.readouterr().err


def test_run_with_fake_surface_does_not_need_api_key(no_hardware, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BridgeSupervisor", failing_bridge)
    code = main([*RUN, "--surface", "fake", "--middle", "fake", "--safety", "fake"])
    # API キーの確かめを通り、ブリッジの起動 (偽物) まで進んだ
    assert code == 1
    assert "stub bridge failed" in capsys.readouterr().err


def test_run_builds_surface_with_api_key_and_closes_it(
    no_hardware, monkeypatch, capsys
):
    built: list[tuple[RunSettings, str | None]] = []
    closed: list[bool] = []

    def fake_build(settings: RunSettings, api_key: str | None) -> SurfaceAssembly:
        built.append((settings, api_key))
        return SurfaceAssembly(
            decider=FakeSurfaceDecider(),
            on_frame=None,
            boxes_at=None,
            on_run_directory=None,
            close=lambda: closed.append(True),
        )

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(cli, "build_surface_assembly", fake_build)
    monkeypatch.setattr(cli, "BridgeSupervisor", failing_bridge)
    code = main([*RUN, "--threshold-advisor", "0.6", "--middle", "fake"])
    assert code == 1
    assert len(built) == 1
    settings, api_key = built[0]
    assert api_key == "test-key"
    assert settings.surface is SurfaceKind.JEV
    assert settings.threshold_advisor == 0.6
    # ブリッジの起動に失敗しても、surface の部品は閉じる
    assert closed == [True]


def test_deep_once_rejects_unknown_document_kind():
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(
            ["deep-once", "--run-dir", "runs/x", "--doc", "unknown"]
        )
    assert exit_info.value.code == 2


def test_deep_once_accepts_the_game_document_kind():
    args = build_parser().parse_args(
        ["deep-once", "--run-dir", "runs/x", "--doc", "game"]
    )
    assert args.doc == "game"


@pytest.mark.parametrize(
    "argv",
    [
        ["middle-once"],
        ["deep-once", "--run-dir", "runs/x"],
    ],
)
def test_required_options(argv):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(argv)
    assert exit_info.value.code == 2


# --- サブコマンドの処理 (保存済みの実行ディレクトリーに対して) ---


def make_saved_run(tmp_path: Path) -> Path:
    """middle-once・deep-once が対象にする、最小の実行ディレクトリーを作って返す (パッケージのコードは使わない)。"""
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
    # 既定の --deep は claude (Claude Code を起動する) なので、フェイクを明示する
    code = main(
        ["deep-once", "--run-dir", str(run_dir), "--doc", "strategy", "--deep", "fake"]
    )
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
        ["--doc", "game", "--scene", "scene-001"],
    ],
)
def test_deep_once_command_rejects_invalid_scene(tmp_path, capsys, extra):
    run_dir = make_saved_run(tmp_path)
    code = main(["deep-once", "--run-dir", str(run_dir), *extra])
    assert code == 2
    assert capsys.readouterr().out == ""
    assert list((run_dir / "deep").iterdir()) == []


def test_deep_once_command_creates_game_job_directory(tmp_path, capsys):
    run_dir = make_saved_run(tmp_path)
    (run_dir / "middle" / "state.json").write_text(
        json.dumps({"scene_id": "scene-001", "surface_wait_s": 0.2}), encoding="utf-8"
    )
    code = main(
        ["deep-once", "--run-dir", str(run_dir), "--doc", "game", "--deep", "fake"]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["job"] == "deep/game-001"
    assert out["status"] == "ok"
    job = json.loads(
        (run_dir / "deep" / "game-001" / "job.json").read_text(encoding="utf-8")
    )
    assert job["kind"] == "game"
    assert job["trigger"] == "manual"
    # 現在の場面があっても、場面に依らない notes/game.md だけを対象にする
    assert job["scene_id"] is None
    assert job["documents"] == ["game.md"]


# --- --middle (middle の実装の選択) ---
# run を動かすテストは、既定の --deep claude (Claude Code を起動する) を避けて --deep fake を付ける


@pytest.mark.parametrize(
    "argv", [RUN, ["middle-once", "--run-dir", "runs/x"]], ids=["run", "once"]
)
def test_middle_defaults_to_luna(argv):
    args = build_parser().parse_args(argv)
    assert args.middle == "luna"


@pytest.mark.parametrize(
    "argv", [RUN, ["middle-once", "--run-dir", "runs/x"]], ids=["run", "once"]
)
def test_middle_accepts_fake_and_rejects_unknown_values(argv):
    assert build_parser().parse_args([*argv, "--middle", "fake"]).middle == "fake"
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*argv, "--middle", "gpt"])
    assert exit_info.value.code == 2


def test_settings_from_args_convert_middle_to_kind():
    run_args = build_parser().parse_args([*RUN, "--middle", "fake"])
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


# 本体の画面の middle に渡す入力 (フェイクは入力を見ない)
SYSTEM_SCREEN_INPUT = SystemScreenInput(
    t=1.0,
    goal=SystemGoal.PREPARE,
    software_name=SOFTWARE,
    user_name=USER,
    frame_jpeg=b"",
    frame_t=1.0,
    judged_screen=None,
    judged_frame_t=None,
    history=(),
)


def test_create_system_screen_middle_for_fake_returns_done_forever():
    middle = create_system_screen_middle(MiddleKind.FAKE, {})
    for _ in range(3):
        assert middle.decide(SYSTEM_SCREEN_INPUT) == FAKE_DONE_OUTPUT


def test_create_system_screen_middle_for_luna_uses_api_key_and_reference_names():
    middle = create_system_screen_middle(MiddleKind.LUNA, {API_KEY_ENV: " sk-test\n"})
    assert isinstance(middle, LunaSystemScreenMiddle)
    assert middle.api_key == "sk-test"
    # 照合と同じ見本の名前
    assert middle.screen_names == SYSTEM_SCREEN_NAMES


@pytest.mark.parametrize("environ", [{}, {API_KEY_ENV: ""}, {API_KEY_ENV: "  "}])
def test_create_system_screen_middle_for_luna_without_api_key_is_an_error(environ):
    with pytest.raises(ValueError):
        create_system_screen_middle(MiddleKind.LUNA, environ)


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
    code = main([*RUN, "--runs-dir", str(runs_dir), "--deep", "fake"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert API_KEY_ENV in captured.err
    assert "--middle fake" in captured.err
    # 実行ディレクトリーも作らない
    assert not runs_dir.exists()


def test_run_range_errors_are_reported_before_api_key(monkeypatch, capsys):
    monkeypatch.setattr(cli, "BridgeSupervisor", must_not_start_bridge)
    code = main([*RUN, "--middle-interval", "0", "--deep", "fake"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--middle-interval" in err
    assert API_KEY_ENV not in err


def test_run_with_fake_middle_does_not_need_api_key(monkeypatch, capsys):
    def failing_bridge(**kwargs):
        raise BridgeStartError("controller bridge failed: no bridge in test")

    # API キーの確かめを通り、ブリッジの起動 (ここでは失敗させる) まで進む
    monkeypatch.setattr(cli, "BridgeSupervisor", failing_bridge)
    code = main(
        [
            *RUN,
            "--middle",
            "fake",
            "--surface",
            "fake",
            "--deep",
            "fake",
            "--safety",
            "fake",
        ]
    )
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
    monkeypatch.setattr(cli, "Capture", lambda *args, **kwargs: StubCapture())
    monkeypatch.setattr(cli, "SwitchCaptureReader", lambda capture: object())
    monkeypatch.setattr(cli, "LayersApp", StubApp)
    return created


def test_run_passes_luna_middle_to_app_by_default(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, "--surface", "fake", "--deep", "fake", "--safety", "fake"]) == 0
    [(settings, parts)] = created
    assert settings.middle is MiddleKind.LUNA
    assert isinstance(parts.middle, LunaMiddleAnalyzer)
    assert parts.middle.api_key == "sk-test"
    # 本体の画面の middle も --middle で選ぶ
    assert isinstance(parts.system_middle, LunaSystemScreenMiddle)
    assert parts.system_middle.api_key == "sk-test"
    # 指定のソフトとユーザーの名前は、起動引数として app に渡す
    assert settings.software_name == SOFTWARE
    assert settings.user_name == USER


def test_run_passes_fake_middle_to_app(monkeypatch):
    created = stub_hardware_and_app(monkeypatch)
    assert (
        main(
            [
                *RUN,
                "--middle",
                "fake",
                "--surface",
                "fake",
                "--deep",
                "fake",
                "--safety",
                "fake",
            ]
        )
        == 0
    )
    [(settings, parts)] = created
    assert settings.middle is MiddleKind.FAKE
    assert isinstance(parts.middle, FakeMiddleAnalyzer)
    assert parts.system_middle.decide(SYSTEM_SCREEN_INPUT) == FAKE_DONE_OUTPUT


def test_run_passes_fake_surface_to_app_without_hooks(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    created = stub_hardware_and_app(monkeypatch)
    assert (
        main([*RUN, "--middle", "fake", "--surface", "fake", "--safety", "fake"]) == 0
    )
    [(settings, parts)] = created
    assert settings.surface is SurfaceKind.FAKE
    assert isinstance(parts.surface, FakeSurfaceDecider)
    assert parts.on_frame is None
    assert parts.surface_boxes_at is None
    assert parts.surface_plan_boxes is None
    assert parts.on_run_directory is None


def test_run_passes_jev_surface_parts_to_app_by_default(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "  ts-test  ")
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, "--middle", "fake", "--box-requery-every", "2"]) == 0
    [(settings, parts)] = created
    # run.jsonl の start に書く起動引数 (settings) と、動かす surface が一致する
    assert settings.surface is SurfaceKind.JEV
    decider = parts.surface
    assert isinstance(decider, JevSurfaceDecider)
    assert decider.client.api_key == "ts-test"
    assert decider.settings.box_requery_every == 2
    assert parts.on_frame == decider.tracking.on_frame
    assert parts.surface_boxes_at == decider.tracking.boxes_at
    assert parts.surface_plan_boxes == decider.plan_boxes
    assert parts.on_run_directory == decider.use_run_directory


def test_run_closes_surface_parts_after_app_stops(monkeypatch):
    closed: list[bool] = []

    def fake_build(settings: RunSettings, api_key: str | None) -> SurfaceAssembly:
        return SurfaceAssembly(
            decider=FakeSurfaceDecider(),
            on_frame=None,
            boxes_at=None,
            on_run_directory=None,
            close=lambda: closed.append(True),
        )

    monkeypatch.setattr(cli, "build_surface_assembly", fake_build)
    stub_hardware_and_app(monkeypatch)
    assert (
        main([*RUN, "--middle", "fake", "--surface", "fake", "--safety", "fake"]) == 0
    )
    assert closed == [True]


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
    # タイトル画面にはゲームの世界の中の場所が映っていない
    "place_name": "",
    "current_tile": None,
    "new_connections": [],
    "new_observations": [],
    "is_stuck": False,
    "stuck_description": "",
    "press_home": False,
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
    # 場所が無く、新しいつながりも観察も無い回は、マップの記録を書かない
    assert not (run_dir / "middle" / "map_records.jsonl").exists()


def test_middle_once_with_luna_reads_and_records_the_map(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    run_dir = make_saved_run(tmp_path)
    (run_dir / "frames" / "interval" / "000005000.jpg").write_bytes(b"jpeg")
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
    map_json = {
        "places": [
            {
                "name": "家の 1 階",
                "aliases": ["1 階の部屋"],
                "description": "",
                "exits": [],
                "blocked": [],
                "state": "",
                "changes_on_entry": False,
            }
        ],
        "connections": [],
    }
    (run_dir / "notes" / "map" / "map.json").write_text(
        json.dumps(map_json, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "middle" / "map_records.jsonl").write_text(
        json.dumps({"t": 1.0, "scene_id": None, "place_name": "自分の部屋"}) + "\n",
        encoding="utf-8",
    )
    output = {
        **LUNA_OUTPUT,
        "scene_name": "フィールド",
        "place_name": "家の 1 階",
        "new_connections": [
            {
                "from": "自分の部屋",
                "to": "家の 1 階",
                "how": "階段",
                "from_tile": None,
                "to_tile": None,
            }
        ],
    }
    body = luna_response_body()
    body["output"][0]["content"][0]["text"] = json.dumps(output, ensure_ascii=False)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=body)

    def create_with_mock_transport(kind, environ):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return LunaMiddleAnalyzer(environ[API_KEY_ENV], client=client)

    monkeypatch.setattr(cli, "create_middle_analyzer", create_with_mock_transport)
    assert main(["middle-once", "--run-dir", str(run_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    # 場所の名前の一覧 (map.json の name と aliases、記録の place_name) と、マップの全体を送る
    [request] = requests
    texts = [
        part["text"]
        for part in json.loads(request.content)["input"][1]["content"]
        if part["type"] == "input_text"
    ]
    [places_section] = [
        text for text in texts if text.startswith("## 場所の名前の一覧\n")
    ]
    assert (
        places_section == "## 場所の名前の一覧\n- 家の 1 階\n- 1 階の部屋\n- 自分の部屋"
    )
    [map_section] = [text for text in texts if text.startswith("## マップ\n")]
    # layout の無い map.json は free として読み、layout を最初のキーにして渡す
    assert json.loads(map_section.split("\n", 1)[1]) == {"layout": "free", **map_json}
    [route_section] = [
        text
        for text in texts
        if text.startswith("## 現在地からの経路 (コードの計算)\n")
    ]
    assert route_section.split("\n", 1)[1] == (
        "マップが自由型 (layout が free) のため、経路を計算しない"
    )
    # 送った画像の時刻で、マップの記録を 1 行足す
    rows = [
        json.loads(line)
        for line in (run_dir / "middle" / "map_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[1:] == [
        {
            "t": 5.0,
            "scene_id": "scene-001",
            "place_name": "家の 1 階",
            "current_tile": None,
            "connections": [
                {
                    "from": "自分の部屋",
                    "to": "家の 1 階",
                    "how": "階段",
                    "from_tile": None,
                    "to_tile": None,
                }
            ],
            "observations": [],
        }
    ]


# --- --deep (deep のジョブを実行するもの) ---


@pytest.mark.parametrize(
    "argv",
    [RUN, ["deep-once", "--run-dir", "runs/x", "--doc", "advice"]],
)
def test_deep_runner_option_defaults_to_claude(argv):
    assert build_parser().parse_args(argv).deep == "claude"
    assert build_parser().parse_args([*argv, "--deep", "fake"]).deep == "fake"
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*argv, "--deep", "openai"])
    assert exit_info.value.code == 2


def test_build_deep_runner():
    assert isinstance(
        cli.build_deep_runner(DeepRunnerChoice.CLAUDE), ClaudeCodeDeepRunner
    )
    assert isinstance(cli.build_deep_runner(DeepRunnerChoice.FAKE), FakeDeepRunner)


def test_run_settings_from_args_convert_deep_to_choice():
    default_args = build_parser().parse_args(RUN)
    assert run_settings_from_args(default_args).deep is DeepRunnerChoice.CLAUDE
    fake_args = build_parser().parse_args([*RUN, "--deep", "fake"])
    assert run_settings_from_args(fake_args).deep is DeepRunnerChoice.FAKE


class CapturedDeepOnce:
    """run_deep_once の偽物。渡された runner を記録し、成功の結果を返す (ジョブは作らない)。"""

    def __init__(self):
        self.runners: list[object] = []

    def __call__(self, run_dir, kind, scene_id, runner) -> DeepOnceResult:
        self.runners.append(runner)
        return DeepOnceResult(
            job_dir=run_dir / "deep" / "advice-001",
            job="deep/advice-001",
            outcome=DeepJobOutcome(DeepJobStatus.OK, ""),
        )


@pytest.mark.parametrize(
    "extra, runner_type",
    [
        ([], ClaudeCodeDeepRunner),
        (["--deep", "claude"], ClaudeCodeDeepRunner),
        (["--deep", "fake"], FakeDeepRunner),
    ],
)
def test_deep_once_uses_the_selected_runner(
    tmp_path, monkeypatch, capsys, extra, runner_type
):
    captured = CapturedDeepOnce()
    monkeypatch.setattr(cli, "run_deep_once", captured)
    run_dir = make_saved_run(tmp_path)
    code = main(["deep-once", "--run-dir", str(run_dir), "--doc", "advice", *extra])
    assert code == 0
    assert len(captured.runners) == 1
    assert isinstance(captured.runners[0], runner_type)


def must_not_start_process(*args, **kwargs):
    raise AssertionError("claude must not be started")


class RefusingPopen:
    """subprocess.Popen の偽物。渡された引数を記録し、OSError を出す (実際の claude は起動しない)。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        raise OSError("claude is not started in tests")


def test_deep_once_map_with_claude_starts_claude_for_the_map_documents(
    tmp_path, monkeypatch, capsys
):
    # map のジョブも Claude Code を起動する。対象の文書は map/map.json と map/places.md (まだ無くてよい)
    popen = RefusingPopen()
    monkeypatch.setattr(subprocess, "Popen", popen)
    run_dir = make_saved_run(tmp_path)
    code = main(["deep-once", "--run-dir", str(run_dir), "--doc", "map"])
    out = json.loads(capsys.readouterr().out)
    assert out["job"] == "deep/map-001"
    assert len(popen.calls) == 1
    # 起動できなかった (偽物の OSError) ので失敗として終わる
    assert out["status"] == "failed"
    assert "claude is not started in tests" in out["message"]
    assert code == 1
    job = json.loads(
        (run_dir / "deep" / "map-001" / "job.json").read_text(encoding="utf-8")
    )
    assert job["documents"] == ["map/map.json", "map/places.md"]
    args = popen.calls[0]
    notes = (run_dir.resolve() / "notes").as_posix()
    allowed = args[args.index("--allowedTools") + 1].split(",")
    assert allowed[1:] == [
        f"Edit(/{notes}/map/map.json)",
        f"Edit(/{notes}/map/places.md)",
    ]


class FakeBridgeSupervisor:
    """BridgeSupervisor の偽物 (実機に触れない)。"""

    state = "ok"

    def __init__(self, **kwargs):
        pass

    def close(self) -> None:
        pass


class FakeCapture:
    """switch2_remote.capture.Capture の偽物 (実機に触れない)。"""

    def __init__(self, *args, **kwargs):
        pass

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "extra, choice, runner_type",
    [
        ([], DeepRunnerChoice.CLAUDE, ClaudeCodeDeepRunner),
        (["--deep", "fake"], DeepRunnerChoice.FAKE, FakeDeepRunner),
    ],
)
def test_run_passes_the_deep_choice_in_settings(
    monkeypatch, extra, choice, runner_type
):
    created = stub_hardware_and_app(monkeypatch)
    assert (
        main(
            [*RUN, "--middle", "fake", "--surface", "fake", "--safety", "fake", *extra]
        )
        == 0
    )
    [(settings, parts)] = created
    # 起動引数 (run.jsonl の start の args) の deep と、実際に渡す DeepRunner がそろう
    assert settings.deep is choice
    assert isinstance(parts.deep, runner_type)


@pytest.mark.parametrize(
    "extra, runner_type",
    [([], ClaudeCodeDeepRunner), (["--deep", "fake"], FakeDeepRunner)],
)
def test_run_uses_the_selected_runner(monkeypatch, capsys, extra, runner_type):
    parts_seen: list[LayersParts] = []

    class FakeLayersApp:
        def __init__(self, settings, parts):
            parts_seen.append(parts)

        def run(self) -> str:
            return REASON_INTERRUPTED

    monkeypatch.setattr(subprocess, "Popen", must_not_start_process)
    monkeypatch.setattr(cli, "BridgeSupervisor", FakeBridgeSupervisor)
    monkeypatch.setattr(cli, "Capture", FakeCapture)
    monkeypatch.setattr(cli, "LayersApp", FakeLayersApp)
    # middle・surface・安全の監視は API キーの要らないフェイクにする
    assert (
        main(
            [*RUN, "--middle", "fake", "--surface", "fake", "--safety", "fake", *extra]
        )
        == 0
    )
    assert len(parts_seen) == 1
    assert isinstance(parts_seen[0].deep, runner_type)


def test_run_starts_the_bridge_and_capture_in_new_sessions(monkeypatch):
    # 最初の Ctrl+C の SIGINT がブリッジと ffmpeg に届かないよう、どちらも新しいセッションで起動する
    supervisor_kwargs: list[dict] = []
    capture_kwargs: list[dict] = []

    class RecordingSupervisor(FakeBridgeSupervisor):
        def __init__(self, **kwargs):
            supervisor_kwargs.append(kwargs)

    class RecordingCapture(FakeCapture):
        def __init__(self, *args, **kwargs):
            capture_kwargs.append(kwargs)

    class FakeLayersApp:
        def __init__(self, settings, parts):
            pass

        def run(self) -> str:
            return REASON_INTERRUPTED

    monkeypatch.setattr(subprocess, "Popen", must_not_start_process)
    monkeypatch.setattr(cli, "BridgeSupervisor", RecordingSupervisor)
    monkeypatch.setattr(cli, "Capture", RecordingCapture)
    monkeypatch.setattr(cli, "LayersApp", FakeLayersApp)
    assert (
        main([*RUN, "--middle", "fake", "--surface", "fake", "--safety", "fake"]) == 0
    )
    assert [kwargs["start_bridge"] for kwargs in supervisor_kwargs] == [
        cli.start_isolated_bridge
    ]
    assert capture_kwargs == [{"new_session": True}]


def test_start_isolated_bridge_starts_the_bridge_in_a_new_session(monkeypatch):
    bridge_kwargs: list[dict] = []

    class RecordingBridge:
        def __init__(self, **kwargs):
            bridge_kwargs.append(kwargs)

    monkeypatch.setattr(cli, "Bridge", RecordingBridge)
    assert isinstance(cli.start_isolated_bridge(), RecordingBridge)
    assert bridge_kwargs == [{"new_session": True}]


# --- --safety (安全の監視) ---


def test_run_safety_defaults_follow_the_issue():
    args = build_parser().parse_args(RUN)
    assert args.safety == "jev"
    assert args.safety_interval == 1.0
    assert args.threshold_safety == 0.7
    assert args.max_safety_errors == 3
    assert run_settings_from_args(args).safety is SafetyKind.JEV


def test_run_accepts_safety_options():
    args = build_parser().parse_args(
        [
            *RUN,
            "--safety",
            "fake",
            "--safety-interval",
            "0.5",
            "--threshold-safety",
            "0.8",
            "--max-safety-errors",
            "2",
        ]
    )
    settings = run_settings_from_args(args)
    assert settings.safety is SafetyKind.FAKE
    assert settings.safety_interval_s == 0.5
    assert settings.threshold_safety == 0.8
    assert settings.max_safety_errors == 2


def test_run_rejects_unknown_safety():
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*RUN, "--safety", "luna"])
    assert exit_info.value.code == 2


ALL_FAKE = [
    "--middle",
    "fake",
    "--surface",
    "fake",
    "--safety",
    "fake",
    "--deep",
    "fake",
]


@pytest.mark.parametrize(
    "extra, option",
    [
        (["--safety-interval", "0.19"], "--safety-interval"),
        (["--safety-interval", "10.01"], "--safety-interval"),
        (["--threshold-safety", "0"], "--threshold-safety"),
        (["--threshold-safety", "1"], "--threshold-safety"),
        (["--max-safety-errors", "0"], "--max-safety-errors"),
    ],
)
def test_run_safety_range_errors_exit_before_hardware(
    no_hardware, tmp_path, capsys, extra, option
):
    runs_dir = tmp_path / "runs"
    code = main([*RUN, "--runs-dir", str(runs_dir), *ALL_FAKE, *extra])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert option in captured.err
    assert not runs_dir.exists()


@pytest.mark.parametrize("key", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_run_with_jev_safety_without_api_key_exits_before_hardware(
    no_hardware, monkeypatch, tmp_path, capsys, key
):
    # surface をフェイクにしても、既定の --safety jev には TYPESAFE_API_KEY が要る
    if key is not None:
        monkeypatch.setenv("TYPESAFE_API_KEY", key)
    runs_dir = tmp_path / "runs"
    code = main(
        [
            *RUN,
            "--runs-dir",
            str(runs_dir),
            "--middle",
            "fake",
            "--surface",
            "fake",
            "--deep",
            "fake",
        ]
    )
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "TYPESAFE_API_KEY" in captured.err
    assert "--safety fake" in captured.err
    assert not runs_dir.exists()


def test_run_without_typesafe_api_key_reports_both_surface_and_safety(
    no_hardware, capsys
):
    code = main([*RUN, "--middle", "fake", "--deep", "fake"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--surface fake" in err
    assert "--safety fake" in err


def test_run_passes_fake_safety_to_app(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, *ALL_FAKE, "--threshold-safety", "0.9"]) == 0
    [(settings, parts)] = created
    # run.jsonl の start に書く起動引数 (settings) と、動かす安全の監視が一致する
    assert settings.safety is SafetyKind.FAKE
    assert settings.threshold_safety == 0.9
    assert isinstance(parts.safety, FakeSafetyChecker)


def test_run_passes_jev_safety_to_app_by_default(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "  ts-test  ")
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, "--middle", "fake", "--surface", "fake", "--deep", "fake"]) == 0
    [(settings, parts)] = created
    assert settings.safety is SafetyKind.JEV
    assert isinstance(parts.safety, JevSafetyChecker)
    assert parts.safety.client.api_key == "ts-test"


def test_run_uses_separate_jev_clients_for_surface_and_safety(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, "--middle", "fake", "--deep", "fake"]) == 0
    [(_, parts)] = created
    assert isinstance(parts.surface, JevSurfaceDecider)
    assert isinstance(parts.safety, JevSafetyChecker)
    assert parts.safety.client is not parts.surface.client


def test_run_builds_safety_with_api_key_and_closes_it_after_app_stops(monkeypatch):
    built: list[tuple[SafetyKind, str | None]] = []
    closed: list[bool] = []
    checker = FakeSafetyChecker()

    def fake_build(kind: SafetyKind, api_key: str | None) -> SafetyAssembly:
        built.append((kind, api_key))
        return SafetyAssembly(checker=checker, close=lambda: closed.append(True))

    monkeypatch.setenv("TYPESAFE_API_KEY", " ts-test ")
    monkeypatch.setattr(cli, "build_safety_assembly", fake_build)
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, "--middle", "fake", "--surface", "fake", "--deep", "fake"]) == 0
    assert built == [(SafetyKind.JEV, "ts-test")]
    [(_, parts)] = created
    assert parts.safety is checker
    assert closed == [True]


def test_run_closes_safety_parts_when_the_bridge_fails(no_hardware, monkeypatch):
    closed: list[bool] = []

    def fake_build(kind: SafetyKind, api_key: str | None) -> SafetyAssembly:
        return SafetyAssembly(
            checker=FakeSafetyChecker(), close=lambda: closed.append(True)
        )

    monkeypatch.setattr(cli, "build_safety_assembly", fake_build)
    monkeypatch.setattr(cli, "BridgeSupervisor", failing_bridge)
    assert main([*RUN, *ALL_FAKE]) == 1
    assert closed == [True]


# --- 画面の停滞の計測の引数 (--progress-window・--stuck-advice-seconds) ---


def test_run_progress_defaults_follow_the_issue():
    args = build_parser().parse_args(RUN)
    assert args.progress_window == DEFAULT_PROGRESS_WINDOW_S == 300
    assert args.stuck_advice_seconds == DEFAULT_STUCK_ADVICE_SECONDS == 60
    settings = run_settings_from_args(args)
    assert settings.progress_window_s == 300.0
    assert settings.stuck_advice_seconds == 60.0


def test_run_accepts_progress_options():
    args = build_parser().parse_args(
        [*RUN, "--progress-window", "120.5", "--stuck-advice-seconds", "30"]
    )
    settings = run_settings_from_args(args)
    assert settings.progress_window_s == 120.5
    assert settings.stuck_advice_seconds == 30.0


@pytest.mark.parametrize(
    "argv, option",
    [
        (["--progress-window", "0"], "--progress-window"),
        (["--progress-window", "-5"], "--progress-window"),
        (["--progress-window", "inf"], "--progress-window"),
        (["--progress-window", "nan"], "--progress-window"),
        (["--stuck-advice-seconds", "0"], "--stuck-advice-seconds"),
        (["--stuck-advice-seconds", "-1"], "--stuck-advice-seconds"),
        (["--stuck-advice-seconds", "inf"], "--stuck-advice-seconds"),
        (["--stuck-advice-seconds", "nan"], "--stuck-advice-seconds"),
    ],
)
def test_run_progress_range_errors_exit_without_hardware(
    no_hardware, capsys, argv, option
):
    code = main([*RUN, "--middle", "fake", "--surface", "fake", *argv])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert option in captured.err


def test_run_passes_the_progress_arguments_in_settings(monkeypatch):
    created = stub_hardware_and_app(monkeypatch)
    argv = [*RUN, *ALL_FAKE, "--progress-window", "90", "--stuck-advice-seconds", "45"]
    assert main(argv) == 0
    [(settings, _)] = created
    # 起動引数は run.jsonl の start の args に入る (settings.run_settings_to_json)
    assert settings.progress_window_s == 90.0
    assert settings.stuck_advice_seconds == 45.0


# --- 本体の画面の段階による助言の引数 (--system-advice-seconds) ---


def test_run_system_advice_seconds_default():
    args = build_parser().parse_args(RUN)
    assert args.system_advice_seconds == DEFAULT_SYSTEM_ADVICE_SECONDS == 90
    assert run_settings_from_args(args).system_advice_seconds == 90.0


def test_run_accepts_system_advice_seconds():
    args = build_parser().parse_args([*RUN, "--system-advice-seconds", "30.5"])
    assert run_settings_from_args(args).system_advice_seconds == 30.5


@pytest.mark.parametrize("value", ["0", "-1", "inf", "nan"])
def test_run_system_advice_seconds_range_errors_exit_without_hardware(
    no_hardware, capsys, value
):
    argv = [*RUN, "--middle", "fake", "--surface", "fake"]
    code = main([*argv, "--system-advice-seconds", value])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    # --stuck-advice-seconds と同じ形の範囲の誤り (argparse の使い方の誤りではない)
    assert "--system-advice-seconds must be a finite number more than 0" in (
        captured.err
    )


def test_run_passes_the_system_advice_seconds_in_settings(monkeypatch):
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, *ALL_FAKE, "--system-advice-seconds", "45"]) == 0
    [(settings, _)] = created
    # 起動引数は run.jsonl の start の args に入る (settings.run_settings_to_json)
    assert settings.system_advice_seconds == 45.0


# --- 押し方の選び方の差 (--hold-margin) ---


def test_run_hold_margin_default_follows_the_issue():
    args = build_parser().parse_args(RUN)
    assert args.hold_margin == DEFAULT_HOLD_MARGIN == 0.1
    assert run_settings_from_args(args).hold_margin == 0.1


def test_run_accepts_hold_margin():
    args = build_parser().parse_args([*RUN, "--hold-margin", "0.25"])
    assert args.hold_margin == 0.25
    assert run_settings_from_args(args).hold_margin == 0.25


def test_run_rejects_hold_margin_that_is_not_a_number(capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*RUN, "--hold-margin", "abc"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "--hold-margin" in err
    assert "invalid float value" in err


@pytest.mark.parametrize("value", ["-0.1", "1.1", "nan", "inf"])
def test_run_hold_margin_range_errors_exit_before_hardware(
    no_hardware, tmp_path, capsys, value
):
    runs_dir = tmp_path / "runs"
    code = main([*RUN, "--runs-dir", str(runs_dir), *ALL_FAKE, "--hold-margin", value])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--hold-margin" in captured.err
    assert not runs_dir.exists()


@pytest.mark.parametrize("value, expected", [("0", 0.0), ("0.3", 0.3), ("1", 1.0)])
def test_run_passes_the_hold_margin_in_settings(monkeypatch, value, expected):
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, *ALL_FAKE, "--hold-margin", value]) == 0
    [(settings, _)] = created
    assert settings.hold_margin == expected
    # 起動引数は run.jsonl の start の args に入る (settings.run_settings_to_json)
    assert run_settings_to_json(settings)["hold_margin"] == expected


def test_run_passes_the_hold_margin_to_the_jev_surface(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
    created = stub_hardware_and_app(monkeypatch)
    assert main([*RUN, "--middle", "fake", "--hold-margin", "0.3"]) == 0
    [(_, parts)] = created
    assert isinstance(parts.surface, JevSurfaceDecider)
    assert parts.surface.settings.hold_margin == 0.3
