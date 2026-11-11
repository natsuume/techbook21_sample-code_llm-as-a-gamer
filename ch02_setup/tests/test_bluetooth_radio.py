import subprocess

import pytest

from switch2_remote.bluetooth_radio import RADIO_SCRIPT, reset_bluetooth_radio
from switch2_remote.ffmpeg_locator import POWERSHELL

WINDOWS_SCRIPT_PATH = r"\\wsl.localhost\Ubuntu\repo\bluetooth_radio.ps1"


class FakeRun:
    """subprocess.run の代わりに、呼ばれた引数を記録して決まった結果を返す偽物。

    raises を設定すると、結果を返す代わりにその例外を送出する。
    """

    def __init__(
        self,
        stdout: str = "ok\n",
        returncode: int = 0,
        stderr: str = "",
        raises: Exception | None = None,
    ):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            args, self.returncode, stdout=self.stdout, stderr=self.stderr
        )


class FakeToWindowsPath:
    """to_windows_path の代わりに、変換を頼まれたパスを記録して決まった Windows のパスを返す偽物。"""

    def __init__(self):
        self.paths: list[str] = []

    def __call__(self, path: str) -> str:
        self.paths.append(path)
        return WINDOWS_SCRIPT_PATH


def reset_with(run: FakeRun) -> None:
    reset_bluetooth_radio(run=run, to_windows_path=FakeToWindowsPath())


def test_reset_runs_radio_script_with_powershell():
    run = FakeRun()
    to_windows_path = FakeToWindowsPath()
    reset_bluetooth_radio(run=run, to_windows_path=to_windows_path)
    assert to_windows_path.paths == [RADIO_SCRIPT]
    assert run.calls == [
        (
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                WINDOWS_SCRIPT_PATH,
            ],
            {
                "cwd": "/mnt/c",
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": 60,
            },
        )
    ]


def test_radio_script_is_next_to_module():
    assert RADIO_SCRIPT.endswith("/switch2_remote/bluetooth_radio.ps1")


@pytest.mark.parametrize("stdout", ["ok\n", "ok", "warning: slow\nok\n", "ok\n\n"])
def test_reset_succeeds_when_last_line_is_ok(stdout):
    assert reset_with(FakeRun(stdout=stdout)) is None


def test_reset_succeeds_when_ok_has_bom():
    assert reset_with(FakeRun(stdout="\ufeffok\n")) is None


@pytest.mark.parametrize(
    "stdout, returncode",
    [
        ("error radio access denied: DeniedByUser\n", 1),
        ("error radio access denied: DeniedByUser\n", 0),
        ("\ufefferror radio access denied: DeniedByUser\n", 1),
    ],
)
def test_reset_raises_with_reason_after_error(stdout, returncode):
    with pytest.raises(RuntimeError) as excinfo:
        reset_with(FakeRun(stdout=stdout, returncode=returncode))
    assert (
        str(excinfo.value)
        == "bluetooth radio reset failed: radio access denied: DeniedByUser"
    )


def test_reset_raises_on_no_bluetooth_radio():
    with pytest.raises(RuntimeError) as excinfo:
        reset_with(FakeRun(stdout="error no bluetooth radio\n", returncode=1))
    assert str(excinfo.value) == "bluetooth radio reset failed: no bluetooth radio"


def test_reset_raises_when_exit_code_is_not_zero_even_if_ok():
    with pytest.raises(RuntimeError, match="^bluetooth radio reset failed: "):
        reset_with(FakeRun(stdout="ok\n", returncode=1))


def test_reset_raises_with_exit_code_when_there_is_no_output():
    with pytest.raises(RuntimeError) as excinfo:
        reset_with(FakeRun(stdout="", returncode=1))
    assert str(excinfo.value) == "bluetooth radio reset failed: exit code 1"


def test_reset_raises_with_output_when_last_line_is_unexpected():
    with pytest.raises(RuntimeError) as excinfo:
        reset_with(FakeRun(stdout="something unexpected\n", returncode=0))
    assert str(excinfo.value) == "bluetooth radio reset failed: something unexpected"


def test_reset_raises_with_stderr_when_stdout_is_empty():
    with pytest.raises(RuntimeError) as excinfo:
        reset_with(FakeRun(stdout="", stderr="script not found\n", returncode=1))
    assert str(excinfo.value) == "bluetooth radio reset failed: script not found"


def test_reset_raises_runtime_error_on_timeout():
    run = FakeRun(raises=subprocess.TimeoutExpired("powershell.exe", 60))
    with pytest.raises(RuntimeError) as excinfo:
        reset_with(run)
    assert (
        str(excinfo.value) == "bluetooth radio reset failed: timed out after 60 seconds"
    )
