import json
from pathlib import Path

import pytest

from switch2_layers.cli import build_parser, main
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
)

COMMANDS = ["run", "middle-once", "deep-once", "publish-note"]


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
    code = main(["middle-once", "--run-dir", str(run_dir)])
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
