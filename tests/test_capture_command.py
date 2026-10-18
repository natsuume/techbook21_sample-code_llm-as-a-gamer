import pytest

from switch2_remote.capture_command import DEVICE_NAME, build_command


def option_value(cmd: list[str], option: str) -> str:
    """コマンドライン中の option の直後の値を返す。"""
    return cmd[cmd.index(option) + 1]


def test_build_command_starts_with_ffmpeg_and_writes_rawvideo_to_stdout():
    cmd, _ = build_command("ffmpeg.exe", 1280, 720, 60, 30)
    assert cmd[0] == "ffmpeg.exe"
    assert cmd[-3:] == ["-f", "rawvideo", "-"]


def test_build_command_reads_device_with_dshow_input_options():
    cmd, _ = build_command("ffmpeg.exe", 1280, 720, 60, 30)
    input_index = cmd.index("-i")
    assert option_value(cmd, "-i") == f"video={DEVICE_NAME}"
    assert option_value(cmd, "-f") == "dshow"
    assert option_value(cmd, "-video_size") == "1280x720"
    assert option_value(cmd, "-framerate") == "60"
    assert option_value(cmd, "-pixel_format") == "nv12"
    # 入力オプションは -i より前に置く
    assert cmd.index("-framerate") < input_index
    assert cmd.index("-pixel_format") < input_index


@pytest.mark.parametrize("output_fps", [30, 12, 60])
def test_build_command_reduces_frame_rate_with_output_filter(output_fps):
    cmd, _ = build_command("ffmpeg.exe", 1280, 720, 60, output_fps)
    assert option_value(cmd, "-vf") == f"fps={output_fps}"
    # 間引きは出力側のフィルターで行うため -i より後に置く
    assert cmd.index("-vf") > cmd.index("-i")


def test_build_command_encodes_nv12_rawvideo():
    cmd, _ = build_command("ffmpeg.exe", 1280, 720, 60, 30)
    assert option_value(cmd, "-c:v") == "rawvideo"
    assert option_value(cmd, "-pix_fmt") == "nv12"


def test_build_command_does_not_copy_stream_or_pass_through_timestamps():
    # フィルターと -c:v copy は併用できない
    cmd, _ = build_command("ffmpeg.exe", 1280, 720, 60, 30)
    assert "copy" not in cmd
    assert "-fps_mode" not in cmd


@pytest.mark.parametrize(
    "width, height, output_fps", [(1280, 720, 30), (1280, 720, 10), (1920, 1080, 60)]
)
def test_build_command_returns_nv12_frame_bytes(width, height, output_fps):
    _, frame_bytes = build_command("ffmpeg.exe", width, height, 60, output_fps)
    assert frame_bytes == width * height * 3 // 2
