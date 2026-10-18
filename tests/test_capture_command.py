import pytest

from switch2_remote.capture_command import DEVICE_NAME, build_command, select_filter


def command_for(
    output_fps: int = 30, output_width: int = 1280, output_height: int = 720
) -> tuple[list[str], int]:
    """デバイス 1280x720・60fps で、出力の解像度とフレームレートを指定したコマンドを組み立てる。"""
    return build_command(
        "ffmpeg.exe",
        device_width=1280,
        device_height=720,
        device_fps=60,
        output_width=output_width,
        output_height=output_height,
        output_fps=output_fps,
    )


def option_value(cmd: list[str], option: str) -> str:
    """コマンドライン中の option の直後の値を返す。"""
    return cmd[cmd.index(option) + 1]


def test_build_command_starts_with_ffmpeg_and_writes_rawvideo_to_stdout():
    cmd, _ = command_for()
    assert cmd[0] == "ffmpeg.exe"
    assert cmd[-3:] == ["-f", "rawvideo", "-"]


@pytest.mark.parametrize("output_width, output_height", [(1280, 720), (640, 360)])
def test_build_command_reads_device_with_dshow_input_options(
    output_width, output_height
):
    # 入力は出力の解像度によらず、デバイスの解像度で受け取る
    cmd, _ = command_for(output_width=output_width, output_height=output_height)
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


@pytest.mark.parametrize(
    "output_fps, output_width, output_height",
    [(30, 1280, 720), (12, 1280, 720), (60, 640, 360), (10, 2, 2)],
)
def test_build_command_reduces_frame_rate_and_size_with_output_filter(
    output_fps, output_width, output_height
):
    cmd, _ = command_for(output_fps, output_width, output_height)
    assert (
        option_value(cmd, "-vf")
        == f"{select_filter(output_fps)},scale={output_width}:{output_height}"
    )
    # 間引きと縮小は出力側のフィルターで行うため -i より後に置く
    assert cmd.index("-vf") > cmd.index("-i")


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


@pytest.mark.parametrize(
    "output_width, output_height, output_fps",
    [(1280, 720, 30), (1280, 720, 10), (640, 360, 60), (2, 2, 1)],
)
def test_build_command_returns_output_nv12_frame_bytes(
    output_width, output_height, output_fps
):
    _, frame_bytes = command_for(output_fps, output_width, output_height)
    assert frame_bytes == output_width * output_height * 3 // 2
