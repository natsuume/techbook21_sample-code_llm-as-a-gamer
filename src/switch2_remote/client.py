"""switch2-remote-daemon に 1 行コマンドを送り、応答を表示するクライアント。

引数は空白でつないで 1 行にして送る (L+R や LS:0.5,-1 のように + や , を含む引数も変えずに送る)。
shot は `shot PATH` で受け付け、PATH をこのクライアントの作業ディレクトリ基準で絶対パスに直して `shot <絶対パス>` を送る。
接続先が自分と同じユーザーのプロセスでなければ中断する (抽象名前空間のソケット名は誰でも先に取れるため)。
デーモンに接続できない場合、応答が error で始まる場合、応答が無いまま接続が閉じた場合は終了コード 1 で終了する。

使い方:
    uv run switch2-remote tap RIGHT RIGHT A
    uv run switch2-remote tap L+R
    uv run switch2-remote hold B+RIGHT 2000
    uv run switch2-remote down LS:0,1
    uv run switch2-remote up ALL
    uv run switch2-remote shot /tmp/screen.png
    uv run switch2-remote fps 12
    uv run switch2-remote quit
"""

import os
import socket
import struct
import sys

# デーモンと同じ値。クライアントは OpenCV 等を読み込まずに起動できるよう、デーモン側のモジュールを import しない
SOCKET_ADDRESS = "\0switch2-remote"


def build_shot_args(path: str) -> list[str]:
    """`shot PATH` の引数を、PATH をこのクライアントの作業ディレクトリ基準で絶対パスにした ["shot", 絶対パス] にする。"""
    raise NotImplementedError


def build_request(args: list[str]) -> str:
    """1 行のリクエストを組み立てる。改行を含む引数は ValueError にする。"""
    if any("\n" in arg or "\r" in arg for arg in args):
        raise ValueError("arguments must not contain line breaks")
    if args[0] == "shot" and len(args) == 2:
        args = build_shot_args(args[1])
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
        try:
            conn.sendall(request.encode())
            reply = conn.makefile(encoding="utf-8", errors="replace").readline().strip()
        except OSError as e:
            # 応答を待つ間にデーモンが終了した場合も error 行で終える
            print(f"error lost connection to daemon: {e}", file=sys.stderr)
            return 1
    if not reply:
        print("error no reply from daemon", file=sys.stderr)
        return 1
    print(reply)
    return 1 if reply.startswith("error") else 0


if __name__ == "__main__":
    sys.exit(main())
