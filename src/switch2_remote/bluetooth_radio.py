"""Windows 側の bluetooth_radio.ps1 を実行し、Windows の Bluetooth 無線をオフ・オンする。

bluetooth_radio.ps1 はこのパッケージに同梱しており、このファイルと同じディレクトリから探す。
COM ポートの開き直しで戻らないブリッジを、Bluetooth の接続を作り直して復旧させるために使う
(bridge_supervisor.BridgeSupervisor の reset_radio)。
bluetooth_radio.ps1 は BOM なし UTF-8 で 1 行を出力する:
    成功  `ok` (終了コード 0)
    失敗  `error <理由>` (終了コード 1。例: `error radio access denied: DeniedByUser`, `error no bluetooth radio`)
"""

import os
import subprocess
from collections.abc import Callable

RADIO_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bluetooth_radio.ps1"
)
# 無線のオフ・オン (既定でオフの後に 5 秒待つ) と PowerShell の起動を合わせた上限 (秒)
RADIO_RESET_TIMEOUT = 60


def to_windows_path_with_wslpath(path: str) -> str:
    """WSL のパスを `wslpath -w` で Windows のパスにする。"""
    return subprocess.run(
        ["wslpath", "-w", path], capture_output=True, text=True, check=True
    ).stdout.strip()


def reset_bluetooth_radio(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    to_windows_path: Callable[[str], str] = to_windows_path_with_wslpath,
) -> None:
    """bluetooth_radio.ps1 で Bluetooth 無線をオフ・オンし、成功したら None を返す。

    run は subprocess.run と同じ引数を受け取る関数、to_windows_path は WSL のパスを Windows のパスにする関数
    (テストでは偽物を渡す)。POWERSHELL (ffmpeg_locator.POWERSHELL) で、次の引数で 1 回実行する:
        run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", to_windows_path(RADIO_SCRIPT)],
            cwd="/mnt/c", capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=RADIO_RESET_TIMEOUT)
    標準出力の最後の空でない行 (先頭の BOM U+FEFF と前後の空白を除く) が `ok` で、かつ終了コードが 0 なら成功。
    それ以外は RuntimeError(f"bluetooth radio reset failed: {理由}") にする。理由は次の順で決める:
        - 最後の空でない行が `error ` で始まれば、その後ろ
        - そうでなく、標準出力と標準エラー出力をつないで前後の空白を除いたものが空でなければ、それ
        - どちらも無ければ `exit code <終了コード>`
    RADIO_RESET_TIMEOUT 秒以内に終わらなければ (subprocess.TimeoutExpired)、
    RuntimeError(f"bluetooth radio reset failed: timed out after {RADIO_RESET_TIMEOUT} seconds") にする。
    """
    raise NotImplementedError
