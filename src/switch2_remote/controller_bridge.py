"""Windows 側の controller_bridge.ps1 を常駐させ、Pico 2 W 経由で Switch 2 にゲームパッドのレポートを送る。

controller_bridge.ps1 はこのパッケージに同梱しており、このファイルと同じディレクトリから探す。
ブリッジとは標準入出力の 1 行ずつでやり取りする:
    起動時         ブリッジが COM ポートを開き、中立を 200ms 送ってから `ready <PORT> <epoch_ms>` を出力する
    set <HEX>      HEX (8 バイトのレポートを表す 16 桁の小文字 16 進) を現在のレポートにする。
                   ブリッジの送信スレッドが新しいレポートを 1 回書き込んだ後に `ack <HEX> <epoch_ms>` を出力する。
                   不正な行には `error <理由>` を出力する
    quit           送信スレッドを止め、中立を 200ms 送り、ポートを閉じて `closed` を出力する (標準入力の EOF でも同じ)
ブリッジは中立以外のレポートを 10ms ごとに書き込み続け、中立に変わったら中立を 100ms 書き込んでから止まる。
起動引数 -KeepSendingNeutral を付けると、中立も 10ms ごとに書き込み続ける (Bridge(keep_sending_neutral=True))。
書き込みに失敗したブリッジは `fatal <メッセージ>` を出力して終了する。
ブリッジの出力は BOM なし UTF-8 で、読み取りでは念のため行頭の BOM (U+FEFF) を除く。
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
# broken にしたブリッジに quit を送ってから、プロセスの終了を待つ上限 (秒)。
# ブリッジの終了処理は、送信スレッドの停止でシリアルの書き込みのタイムアウト (1 秒) を待ち、
# その後に中立を 200ms 送ることがあるため、それより長くとる
BRIDGE_QUIT_TIMEOUT = 3.0
REPORT_BYTES = 8


class UnconfirmedInputError(RuntimeError):
    """set を書き込んだ後に ack を確認できなかった。レポートは Switch 2 に届いている可能性がある。"""


class BridgeStartError(RuntimeError):
    """ブリッジが起動時に ready を出力しなかった (COM ポートを開けなかった等)。メッセージは `controller bridge failed: <行>`。"""


class Bridge:
    """controller_bridge.ps1 を常駐させ、set コマンドでレポートを差し替えて ack を待つ。

    keep_sending_neutral が True なら、PowerShell の引数に -KeepSendingNeutral を加えて起動する
    (中立のレポートも書き込み続ける。接続維持の効果を比べる実験用)。
    起動時の最初の行が ready で始まらなければ (出力の終了も含む)、プロセスを kill して終了を待ってから
    BridgeStartError(f"controller bridge failed: {行}") を送出する。
    set_report は書き込みと ack 待ちを 1 つのロックで囲むため、複数のスレッドから呼んでよい。
    ack のタイムアウト、想定外の応答、ack 前の出力の終了 (EOF) ではブリッジを broken にし、以後は使わない。
    broken にしたときは、押されたままの入力を残さないよう best-effort でブリッジを終了させる
    (quit を書く → 最大 BRIDGE_QUIT_TIMEOUT 秒終了を待つ → 終わらなければ kill)。
    ブリッジの quit の処理が中立を 200ms 送るため、終了できれば押されたままの入力は離れる。
    """

    def __init__(self, keep_sending_neutral: bool = False):
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
        # set の書き込みと ack 待ちの組を、デーモンのコマンド処理と hold の解放スレッドの間で混ぜないためのロック
        self.lock = threading.Lock()
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
        - ack の前に出力が終われば (EOF) UnconfirmedInputError にする (broken にする)。
          ブリッジはレポートを書き込んでから ack を出力するため、ack の前に終了してもレポートは届いている可能性がある
        - `ack <同じ16進> ` で始まらない応答は UnconfirmedInputError にする (broken にする)
        """
        if len(report) != REPORT_BYTES:
            raise ValueError(f"report must be {REPORT_BYTES} bytes: {report.hex()}")
        report_hex = report.hex()
        with self.lock:
            if not self.is_alive():
                raise RuntimeError(
                    "controller bridge is not usable; restart the daemon"
                )
            self.proc.stdin.write(f"set {report_hex}\n")
            self.proc.stdin.flush()
            # 書き込んだ後は、応答が無くてもブリッジが遅れてレポートを送る可能性があるため未確認として扱う
            try:
                ack = self._next_line(BRIDGE_ACK_TIMEOUT)
            except queue.Empty as e:
                self._stop_broken_bridge()
                raise UnconfirmedInputError("no ack from bridge in time") from e
            if ack is None:
                self._stop_broken_bridge()
                raise UnconfirmedInputError("controller bridge exited before ack")
            if not ack.startswith(f"ack {report_hex} "):
                self._stop_broken_bridge()
                raise UnconfirmedInputError(f"unexpected bridge reply: {ack}")
            return ack

    def _stop_broken_bridge(self) -> None:
        """ブリッジを broken にし、best-effort で終了させる。

        quit を書き (書き込みの例外は無視する)、最大 BRIDGE_QUIT_TIMEOUT 秒プロセスの終了を待ち、
        終わらなければ kill する。出力の終了は一度しか届かないため、broken にした後は書き込まない。
        """
        self.broken = True
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass  # 既に終了したブリッジでは、標準入力が閉じている
        try:
            self.proc.wait(timeout=BRIDGE_QUIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
