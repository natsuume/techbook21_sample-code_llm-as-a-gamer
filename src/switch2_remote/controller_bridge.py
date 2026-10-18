"""Windows 側の controller_bridge.ps1 を常駐させ、Pico 2 W 経由で Switch 2 にゲームパッドのレポートを送る。

controller_bridge.ps1 はこのパッケージに同梱しており、このファイルと同じディレクトリから探す。
ブリッジとは標準入出力の 1 行ずつでやり取りする:
    起動時         ブリッジが COM ポートを開き、中立を 200ms 送ってから `ready <PORT> <epoch_ms>` を出力する
    set <HEX>      HEX (8 バイトのレポートを表す 16 桁の小文字 16 進) を現在のレポートにする。
                   ブリッジの送信スレッドが新しいレポートを 1 回書き込んだ後に `ack <HEX> <epoch_ms>` を出力する。
                   不正な行には `error <理由>` を出力する
    quit           送信スレッドを止め、中立を 200ms 送り、ポートを閉じて `closed` を出力する (標準入力の EOF でも同じ)
ブリッジは中立以外のレポートを 10ms ごとに書き込み続け、中立に変わったら中立を 100ms 書き込んでから止まる。
書き込みに失敗したブリッジは `fatal <メッセージ>` を出力して終了する。
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
BRIDGE_ACK_TIMEOUT = 2.0  # set を書き込んでから ack が返るまでの上限 (秒)


class UnconfirmedInputError(RuntimeError):
    """set を書き込んだ後に ack を確認できなかった。レポートは Switch 2 に届いている可能性がある。"""


class Bridge:
    """controller_bridge.ps1 を常駐させ、set コマンドでレポートを差し替えて ack を待つ。

    set_report は書き込みと ack 待ちを 1 つのロックで囲むため、複数のスレッドから呼んでよい。
    """

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

    def set_report(self, report: bytes) -> str:
        """8 バイトのレポートを `set <16 桁の小文字 16 進>` で送り、ブリッジの ack の行を返す。

        書き込みと ack 待ちを 1 つのロックで囲み、スレッドから呼ばれても応答の対応がずれないようにする。
        - broken または終了済みのブリッジでは、書き込まずに RuntimeError にする
        - ack が BRIDGE_ACK_TIMEOUT 秒以内に来なければ UnconfirmedInputError にする (broken にする)
        - ack の前に出力が終われば (EOF) RuntimeError にする (broken にする)
        - `ack <同じ16進> ` で始まらない応答は UnconfirmedInputError にする (broken にする)
        """
        raise NotImplementedError

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
