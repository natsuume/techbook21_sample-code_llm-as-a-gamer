"""switch2-remote-daemon に 1 行コマンドを送り、応答を表示するクライアント。

shot は `shot PATH [TIMEOUT]` で受け付け、PATH をこのクライアントの作業ディレクトリ基準で絶対パスに直して
デーモンの形式 `shot TIMEOUT PATH` に並べ替えて送る。
接続先が自分と同じユーザーのプロセスでなければ中断する (抽象名前空間のソケット名は誰でも先に取れるため)。
デーモンに接続できない場合、応答が error で始まる場合、応答が無いまま接続が閉じた場合は終了コード 1 で終了する。

使い方:
    uv run switch2-remote tap RIGHT RIGHT A
    uv run switch2-remote shot /tmp/screen.png 5
    uv run switch2-remote quit
"""

import os
import socket
import struct
import sys

# デーモンと同じ値。クライアントは OpenCV 等を読み込まずに起動できるよう、デーモン側のモジュールを import しない
SOCKET_ADDRESS = "\0switch2-remote"
DEFAULT_SHOT_TIMEOUT = "5"


def build_request(args: list[str]) -> str:
    """1 行のリクエストを組み立てる。改行を含む引数と、数値でない shot の TIMEOUT は ValueError にする。"""
    if any("\n" in arg or "\r" in arg for arg in args):
        raise ValueError("arguments must not contain line breaks")
    if args[0] == "shot" and len(args) in (2, 3):
        timeout = repr(float(args[2] if len(args) == 3 else DEFAULT_SHOT_TIMEOUT))
        args = ["shot", timeout, os.path.abspath(args[1])]
    return " ".join(args) + "\n"


def peer_uid(conn: socket.socket) -> int:
    """Unix ソケットの接続先プロセスの UID を返す (SO_PEERCRED は pid, uid, gid の順)。"""
    creds = conn.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    return struct.unpack("3i", creds)[1]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        request = build_request(sys.argv[1:])
    except ValueError as e:
        print(f"error {e}", file=sys.stderr)
        return 1
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        try:
            conn.connect(SOCKET_ADDRESS)
        except OSError as e:
            # デーモン未起動のときも、呼び出し側が error 行で失敗を判定できるようにする
            print(f"error cannot connect to daemon: {e}", file=sys.stderr)
            return 1
        if peer_uid(conn) != os.getuid():
            print("error daemon is owned by another user", file=sys.stderr)
            return 1
        conn.sendall(request.encode())
        reply = conn.makefile(encoding="utf-8", errors="replace").readline().strip()
    if not reply:
        print("error no reply from daemon", file=sys.stderr)
        return 1
    print(reply)
    return 1 if reply.startswith("error") else 0


if __name__ == "__main__":
    sys.exit(main())
