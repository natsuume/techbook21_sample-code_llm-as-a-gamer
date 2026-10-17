"""Windows 側の controller_bridge.ps1 を常駐させ、Pico 2 W 経由で Switch 2 にボタン入力を送る。

controller_bridge.ps1 はこのパッケージに同梱しており、このファイルと同じディレクトリから探す。
ブリッジは 1 行 1 コマンドで `tap BUTTON MS` を受け取り、押下の送信開始直前に `ack`、
ニュートラルの送信完了後に `done` を返す。
"""

import os
import queue
import subprocess
import threading

from switch2_remote.ffmpeg_locator import POWERSHELL

BRIDGE_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "controller_bridge.ps1"
)
BRIDGE_READY_TIMEOUT = 30.0  # PowerShell の起動と COM ポートを開くまでの上限 (秒)
BRIDGE_ACK_TIMEOUT = 2.0  # tap を書き込んでから ack が返るまでの上限 (秒)
BRIDGE_DONE_MARGIN = 2.0  # ack 後、押下時間に加えて done を待つ余裕 (秒)


class UnconfirmedInputError(RuntimeError):
    """ブリッジが ack (押下の送信開始) を返した後に完了を確認できなかった。入力は届いている可能性がある。"""


class Bridge:
    """controller_bridge.ps1 を常駐させ、tap コマンドを送って ack / done を待つ。"""

    def __init__(self):
        bridge_win_path = subprocess.run(
            ["wslpath", "-w", BRIDGE_SCRIPT], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.proc = subprocess.Popen(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                bridge_win_path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd="/mnt/c",
            # ブリッジの警告等が UTF-8 以外でも、読み取りの例外で入力の記録を失わない
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        # 応答を期限付きで待つため、読み取りは専用スレッドで行いキューに渡す (None は出力の終了)
        self.lines: queue.Queue[str | None] = queue.Queue()
        # 応答が期限内に来なかった後は、遅れて届く応答と対応がずれるため使わない
        self.broken = False
        threading.Thread(target=self._read_stdout, daemon=True).start()
        try:
            line = self._next_line(BRIDGE_READY_TIMEOUT)
        except (
            BaseException
        ):  # 起動待ちの割り込み等でも、COM ポートを掴んだ子プロセスを残さない
            self.proc.kill()
            self.proc.wait()
            raise
        if line is None or not line.startswith("ready"):
            self.proc.kill()
            self.proc.wait()
            raise SystemExit(f"controller bridge failed: {line}")

    def _read_stdout(self):
        for line in self.proc.stdout:
            self.lines.put(line.strip())
        self.lines.put(None)

    def _next_line(self, timeout: float) -> str | None:
        """次の 1 行を返す。出力が終わっていれば None、期限切れなら queue.Empty を送出する。"""
        return self.lines.get(timeout=timeout)

    def is_alive(self) -> bool:
        return self.proc.poll() is None and not self.broken

    def tap(self, button: str, hold_ms: int = 120) -> str:
        if not self.is_alive():
            raise RuntimeError("controller bridge is not usable; restart the daemon")
        self.proc.stdin.write(f"tap {button} {hold_ms}\n")
        self.proc.stdin.flush()
        # 書き込んだ後は、応答が無くてもブリッジが遅れて押下を送る可能性があるため未確認として扱う
        try:
            ack = self._next_line(BRIDGE_ACK_TIMEOUT)
        except queue.Empty as e:
            self.broken = True
            raise UnconfirmedInputError("no ack from bridge in time") from e
        if ack is None:
            # 出力の終了は一度しか届かないため、以降の tap がプロセスの回収前でも止まったブリッジに書き込まないようにする
            self.broken = True
            raise RuntimeError("controller bridge exited before ack")
        if not ack.startswith(f"ack {button} "):
            self.broken = True
            raise UnconfirmedInputError(f"unexpected bridge reply: {ack}")
        try:
            done = self._next_line(hold_ms / 1000 + BRIDGE_DONE_MARGIN)
        except queue.Empty as e:
            self.broken = True
            raise UnconfirmedInputError("no completion reply after ack in time") from e
        if done is None or not done.startswith(f"done {button} "):
            self.broken = True
            raise UnconfirmedInputError(f"bridge did not confirm completion: {done}")
        return f"{ack} / {done}"

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
