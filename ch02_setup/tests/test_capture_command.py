import pytest

from switch2_remote.capture_command import DEVICE_NAME, build_command, select_filter

# デバイスと同じ 1280x720 の NV12 の 1 フレームのバイト数 (1382400)
DEVICE_NV12_FRAME_BYTES = 1280 * 720 * 3 // 2


def command_for(output_fps: int = 30) -> tuple[list[str], int]:
    """デバイス 1280x720・60fps で、出力のフレームレートを指定したコマンドを組み立てる。"""
    return build_command(
        "ffmpeg.exe",
        device_width=1280,
        device_height=720,
        device_fps=60,
        output_fps=output_fps,
    )


def option_value(cmd: list[str], option: str) -> str:
    """コマンドライン中の option の直後の値を返す。"""
    return cmd[cmd.index(option) + 1]


def test_build_command_starts_with_ffmpeg_and_writes_rawvideo_to_stdout():
    cmd, _ = command_for()
    assert cmd[0] == "ffmpeg.exe"
    assert cmd[-3:] == ["-f", "rawvideo", "-"]


def test_build_command_reads_device_with_dshow_input_options():
    cmd, _ = command_for()
    input_index = cmd.index("-i")
    assert option_value(cmd, "-i") == f"video={DEVICE_NAME}"
    assert option_value(cmd, "-f") == "dshow"
    assert option_value(cmd, "-video_size") == "1280x720"
    assert option_value(cmd, "-framerate") == "60"
    assert option_value(cmd, "-pixel_format") == "nv12"
    # 入力オプションは -i より前に置く
    assert cmd.index("-video_size") < input_index
    assert cmd.index("-framerate") < input_index
    assert cmd.index("-pixel_format") < input_index


@pytest.mark.parametrize(
    "output_fps, expected",
    [
        (
            30,
            "select='isnan(prev_selected_t)+gt(floor(t*30+0.5)\\,floor(prev_selected_t*30+0.5))'",
        ),
        (
            7,
            "select='isnan(prev_selected_t)+gt(floor(t*7+0.5)\\,floor(prev_selected_t*7+0.5))'",
        ),
    ],
)
def test_select_filter_keeps_first_frame_of_each_interval(output_fps, expected):
    assert select_filter(output_fps) == expected


@pytest.mark.parametrize("output_fps", [30, 12, 60, 1])
def test_build_command_reduces_frame_rate_with_output_filter(output_fps):
    cmd, _ = command_for(output_fps)
    assert option_value(cmd, "-vf") == select_filter(output_fps)
    # 間引きは出力側のフィルターで行うため -i より後に置く
    assert cmd.index("-vf") > cmd.index("-i")


@pytest.mark.parametrize("output_fps", [30, 12, 60, 1])
def test_build_command_does_not_scale_output(output_fps):
    # 出力はデバイスと同じ 1280x720 のままとし、縮小のフィルターも出力側の解像度の指定も付けない
    cmd, _ = command_for(output_fps)
    assert [arg for arg in cmd if "scale" in arg] == []
    assert "-s" not in cmd
    assert option_value(cmd, "-video_size") == "1280x720"


def test_build_command_encodes_nv12_rawvideo():
    cmd, _ = command_for()
    assert option_value(cmd, "-c:v") == "rawvideo"
    assert option_value(cmd, "-pix_fmt") == "nv12"


def test_build_command_does_not_copy_stream():
    # フィルターと -c:v copy は併用できない
    cmd, _ = command_for()
    assert "copy" not in cmd


def test_build_command_passes_selected_frames_through():
    # 出力側でフレームを複製・間引きせず、select が残したフレームをそのまま書く
    cmd, _ = command_for()
    assert option_value(cmd, "-fps_mode") == "passthrough"
    assert cmd.index("-fps_mode") > cmd.index("-i")


@pytest.mark.parametrize("output_fps", [30, 10, 60, 1])
def test_build_command_returns_device_size_nv12_frame_bytes(output_fps):
    # 1 フレームのバイト数はデバイスと同じ 1280x720 の NV12 で、output_fps によらない
    _, frame_bytes = command_for(output_fps)
    assert frame_bytes == DEVICE_NV12_FRAME_BYTES
