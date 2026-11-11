import pytest

from switch2_agent import run

REQUIRED = ["--game", "zelda", "--model", "jev"]


def test_parse_args_uses_defaults():
    assert vars(run.parse_args(REQUIRED)) == {
        "game": "zelda",
        "model": "jev",
        "ffmpeg_fps": 30,
        "process_fps": 10,
        "history_seconds": 5.0,
        "image_width": 1280,
        "jpeg_quality": 85,
        "threshold_press": 0.5,
        "threshold_tap": 0.5,
        "threshold_stick": 0.5,
        "threshold_advisor": 0.5,
        "valley_ratio": 0.5,
        "similarity_threshold": 0.15,
        "advisor_timeout": 300.0,
        "max_consecutive_errors": 5,
    }


def test_parse_args_accepts_decisions_model():
    assert (
        run.parse_args(["--game", "zelda", "--model", "decisions"]).model == "decisions"
    )


def test_parse_args_reads_command_line_when_argv_is_none(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["switch2-agent", "--game", "mario", "--model", "decisions"]
    )
    args = run.parse_args(None)
    assert (args.game, args.model) == ("mario", "decisions")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--game", "zelda"],
        ["--model", "jev"],
        ["--game", "zelda", "--model", "gpt"],
    ],
)
def test_parse_args_requires_game_and_model(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run.parse_args(argv)
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra, attribute, expected",
    [
        (["--ffmpeg-fps", "1", "--process-fps", "1"], "process_fps", 1),
        (["--ffmpeg-fps", "60", "--process-fps", "60"], "ffmpeg_fps", 60),
        (["--history-seconds", "0.1"], "history_seconds", 0.1),
        (["--image-width", "498"], "image_width", 498),
        (["--image-width", "1280"], "image_width", 1280),
        (["--jpeg-quality", "1"], "jpeg_quality", 1),
        (["--jpeg-quality", "100"], "jpeg_quality", 100),
        (["--threshold-press", "0"], "threshold_press", 0.0),
        (["--threshold-tap", "1"], "threshold_tap", 1.0),
        (["--threshold-stick", "0.75"], "threshold_stick", 0.75),
        (["--threshold-advisor", "1.0"], "threshold_advisor", 1.0),
        (["--valley-ratio", "0.01"], "valley_ratio", 0.01),
        (["--valley-ratio", "0.99"], "valley_ratio", 0.99),
        (["--similarity-threshold", "0"], "similarity_threshold", 0.0),
        (["--similarity-threshold", "255"], "similarity_threshold", 255.0),
        (["--advisor-timeout", "0.5"], "advisor_timeout", 0.5),
        (["--max-consecutive-errors", "1"], "max_consecutive_errors", 1),
    ],
)
def test_parse_args_accepts_values_in_range(extra, attribute, expected):
    assert getattr(run.parse_args([*REQUIRED, *extra]), attribute) == expected


@pytest.mark.parametrize(
    "extra",
    [
        ["--ffmpeg-fps", "0"],
        ["--ffmpeg-fps", "61"],
        ["--ffmpeg-fps", "abc"],
        ["--process-fps", "0"],
        ["--process-fps", "1.5"],
        ["--history-seconds", "0"],
        ["--history-seconds", "-1"],
        ["--image-width", "497"],
        ["--image-width", "1281"],
        ["--image-width", "640.5"],
        ["--jpeg-quality", "0"],
        ["--jpeg-quality", "101"],
        ["--threshold-press", "-0.1"],
        ["--threshold-tap", "1.1"],
        ["--threshold-stick", "nan"],
        ["--threshold-advisor", "2"],
        ["--valley-ratio", "0"],
        ["--valley-ratio", "1"],
        ["--similarity-threshold", "-0.1"],
        ["--similarity-threshold", "255.1"],
        ["--advisor-timeout", "0"],
        ["--advisor-timeout", "-5"],
        ["--max-consecutive-errors", "0"],
        ["--max-consecutive-errors", "2.5"],
    ],
)
def test_parse_args_rejects_values_out_of_range(extra, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run.parse_args([*REQUIRED, *extra])
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [
        ["--ffmpeg-fps", "30", "--process-fps", "31"],
        ["--process-fps", "40"],
        # --process-fps の既定値 10 も --ffmpeg-fps 以下でなければならない
        ["--ffmpeg-fps", "5"],
    ],
)
def test_parse_args_rejects_process_fps_above_ffmpeg_fps(extra, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run.parse_args([*REQUIRED, *extra])
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err
