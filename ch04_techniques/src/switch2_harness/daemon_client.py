"""switch2-remote-daemon に 1 行コマンドを送り、応答の 1 行を返す。

プロトコルは第2章のクライアント (switch2_remote.client) と同じで、引数を空白でつないだ 1 行を送り、1 行の応答を受け取る。
1 行の組み立て (switch2_remote.client.build_request) と、接続先のユーザーの確認 (switch2_remote.client.peer_uid) は第2章のものを使う。
失敗は例外にせず `error <説明>` の 1 行で返し、呼び出し側はデーモンの応答と同じく先頭の error で失敗を判定する。
"""

import os
import socket
from typing import Protocol

from switch2_remote.client import SOCKET_ADDRESS, build_request, peer_uid


class SendCommand(Protocol):
    """デーモンに引数の列を送り、応答の 1 行を返す関数の型。commands と agent_launcher はこの型で送信を受け取る。

    timeout_s を渡すと、接続・送信・受信のそれぞれの待ち時間の上限 (秒) にする。None なら上限なし。
    """

    def __call__(self, args: list[str], *, timeout_s: float | None = None) -> str: ...


def timeout_message(timeout_s: float) -> str:
    """待ち時間の上限を超えたときの error 行を返す。例: 0.2 は `error daemon did not reply within 0.2s`。"""
    return f"error daemon did not reply within {timeout_s:g}s"


def send_command(
    args: list[str], address: str = SOCKET_ADDRESS, *, timeout_s: float | None = None
) -> str:
    """args を 1 行にしてデーモンに送り、応答の 1 行 (前後の空白を除いたもの) を返す。

    1 行は switch2_remote.client.build_request で組み立てる (例: ["tap", "B+RIGHT"] は "tap B+RIGHT\\n")。
    応答が複数行でも、最初の 1 行だけを返す。
    timeout_s が None なら待ち時間に上限を設けない (エージェント用のサブコマンドは、長い tap があるため上限を付けない)。
    数値なら、接続・送信・受信のそれぞれにその秒数の上限を付け (socket.settimeout)、超えたら
    timeout_message(timeout_s) (`error daemon did not reply within <秒数>s`) を返す。
    次の場合もデーモンの応答の代わりに error 行を返す (例外にしない):
        - 引数に改行が含まれる (build_request の ValueError): `error <ValueError のメッセージ>` (何も送らない)
        - 接続できない (OSError): `error cannot connect to daemon: <OSError>`
        - 接続先が自分と同じユーザーのプロセスでない: `error daemon is owned by another user`
        - 送信中・応答の待機中に接続が切れた (OSError): `error lost connection to daemon: <OSError>`
        - 応答が空のまま接続が閉じた: `error no reply from daemon`
    address は抽象名前空間のソケット名 (先頭が "\\0")。既定は第2章のデーモンの "\\0switch2-remote"。
    """
    try:
        request = build_request(args)
    except ValueError as e:
        return f"error {e}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout_s)
        try:
            conn.connect(address)
        except OSError as e:
            if isinstance(e, TimeoutError) and timeout_s is not None:
                return timeout_message(timeout_s)
            return f"error cannot connect to daemon: {e}"
        try:
            # 抽象名前空間のソケット名は誰でも先に取れるため、接続先が自分と同じユーザーかを確かめる
            if peer_uid(conn) != os.getuid():
                return "error daemon is owned by another user"
            conn.sendall(request.encode())
            with conn.makefile(encoding="utf-8", errors="replace") as reader:
                reply = reader.readline().strip()
        except OSError as e:
            if isinstance(e, TimeoutError) and timeout_s is not None:
                return timeout_message(timeout_s)
            return f"error lost connection to daemon: {e}"
    if not reply:
        return "error no reply from daemon"
    return reply
