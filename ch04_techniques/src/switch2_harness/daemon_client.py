"""switch2-remote-daemon に 1 行コマンドを送り、応答の 1 行を返す。

プロトコルは第2章のクライアント (switch2_remote.client) と同じで、引数を空白でつないだ 1 行を送り、1 行の応答を受け取る。
1 行の組み立て (switch2_remote.client.build_request) と、接続先のユーザーの確認 (switch2_remote.client.peer_uid) は第2章のものを使う。
失敗は例外にせず `error <説明>` の 1 行で返し、呼び出し側はデーモンの応答と同じく先頭の error で失敗を判定する。
"""

import os
import socket
from collections.abc import Callable

from switch2_remote.client import SOCKET_ADDRESS, build_request, peer_uid

# デーモンに引数の列を送り、応答の 1 行を返す関数の型。commands と agent_launcher はこの型で送信を受け取る
SendCommand = Callable[[list[str]], str]


def send_command(args: list[str], address: str = SOCKET_ADDRESS) -> str:
    """args を 1 行にしてデーモンに送り、応答の 1 行 (前後の空白を除いたもの) を返す。

    1 行は switch2_remote.client.build_request で組み立てる (例: ["tap", "B+RIGHT"] は "tap B+RIGHT\\n")。
    応答が複数行でも、最初の 1 行だけを返す。応答を待つ時間に上限は設けない。
    次の場合はデーモンの応答の代わりに error 行を返す:
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
        try:
            conn.connect(address)
        except OSError as e:
            return f"error cannot connect to daemon: {e}"
        try:
            # 抽象名前空間のソケット名は誰でも先に取れるため、接続先が自分と同じユーザーかを確かめる
            if peer_uid(conn) != os.getuid():
                return "error daemon is owned by another user"
            conn.sendall(request.encode())
            with conn.makefile(encoding="utf-8", errors="replace") as reader:
                reply = reader.readline().strip()
        except OSError as e:
            return f"error lost connection to daemon: {e}"
    if not reply:
        return "error no reply from daemon"
    return reply
