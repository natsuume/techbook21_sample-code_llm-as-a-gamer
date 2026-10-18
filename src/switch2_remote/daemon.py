"""Switch 2 を 1 コマンドずつ操作するための常駐デーモン。

キャプチャ (ffmpeg.exe, 720p NV12) と controller_bridge.ps1 を起動したまま保持する。
キャプチャは処理レートに間引いたフレームを直近の一定時間分保持し、ブリッジから作った Controller は押下状態を保持する。
Unix ソケット (抽象名前空間 "switch2-remote") で 1 行コマンドを受け付けて 1 行で応答する。
クライアントは switch2-remote を使う。
終了時は Controller、ブリッジ、キャプチャの順に閉じる。

コマンド (CHORD は SPEC を + でつないだもの。詳しくは commands を参照):
    tap CHORD [CHORD ...]  CHORD を順に押す (押下 120ms、離して 100ms、CHORD 間にさらに 150ms)。送り終えてから応答する
    hold CHORD MS          CHORD を MS ミリ秒 (1〜60000) 押す。押したらすぐ応答し、MS 経過後にバックグラウンドで離す
    down CHORD [CHORD ...] CHORD を押したままにする
    up TARGET [TARGET ...] TARGET (ボタン名か LS / RS) を離す。up ALL ですべて離す
    shot PATH              バッファーの最新フレームを待たずに PATH に PNG 保存する。PATH は絶対パスで空白を含んでよい
    fps N                  処理レートを N (整数 1〜ffmpeg_fps) に変える
    status                 受信フレーム数、バッファー内の枚数、フレームレート、ffmpeg のフレーム落ち回数、ブリッジの生存、押下状態を返す
    quit                   コントローラー、ブリッジ、キャプチャを止めて終了する

起動引数:
    --ffmpeg-fps N      ffmpeg の出力のフレームレート (整数 1〜60、既定 30)。起動後は変えられない
    --process-fps N     バッファーに残すフレームレート (整数 1〜ffmpeg_fps、既定 10)。fps コマンドで変えられる
    --buffer-seconds N  バッファーに保持する秒数 (整数 1〜60、既定 5)
    範囲外の値と、--process-fps が --ffmpeg-fps より大きい組み合わせは起動時にエラーにする

使い方:
    uv run switch2-remote-daemon
    uv run switch2-remote-daemon --ffmpeg-fps 30 --process-fps 12 --buffer-seconds 5
"""

import argparse
import os
import socket
import struct

import cv2

from switch2_remote.capture import DEVICE_FPS, Capture
from switch2_remote.commands import handle_command
from switch2_remote.controller import Controller
from switch2_remote.controller_bridge import Bridge

SOCKET_ADDRESS = (
    "\0switch2-remote"  # 抽象名前空間。ファイルパスの長さ制限と後片付けが不要
)
REQUEST_READ_TIMEOUT = 5.0  # リクエスト 1 行の受信と応答の送信にかける上限 (秒)
FFMPEG_FPS_MIN, FFMPEG_FPS_MAX = 1, DEVICE_FPS
DEFAULT_FFMPEG_FPS = 30
DEFAULT_PROCESS_FPS = 10
BUFFER_SECONDS_MIN, BUFFER_SECONDS_MAX = 1, 60
DEFAULT_BUFFER_SECONDS = 5


def peer_uid(conn: socket.socket) -> int:
    """Unix ソケットの接続元プロセスの UID を返す (SO_PEERCRED は pid, uid, gid の順)。"""
    creds = conn.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    return struct.unpack("3i", creds)[1]


def serve_connection(
    conn: socket.socket, capture: Capture, controller: Controller
) -> bool:
    """1 接続分のコマンドを処理し、quit を受けたら True を返す。

    抽象名前空間のソケットにはファイル権限が無いため、デーモンと同じユーザー以外からの接続は拒否する。
    コマンドの失敗やクライアントの切断ではデーモンを止めない。
    """
    try:
        if peer_uid(conn) != os.getuid():
            conn.sendall(b"error permission denied\n")
            return False
        # 1 行を送らないまま止まったクライアントで、後続の接続が待たされ続けないようにする
        conn.settimeout(REQUEST_READ_TIMEOUT)
        request = conn.makefile("r", encoding="utf-8", errors="replace").readline()
        parts = request.split()
        if not parts:
            return False
        if parts[0] == "quit":
            conn.sendall(b"bye\n")
            return True
        try:
            reply = handle_command(request, capture, controller)
        except (RuntimeError, ValueError, OSError, cv2.error) as e:
            reply = f"error {e!r}"
        conn.sendall((reply + "\n").encode())
    except OSError:
        pass  # 応答前にクライアントが切断した場合は、その接続だけを捨てる
    return False


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """起動引数を解釈し、ffmpeg_fps・process_fps・buffer_seconds を持つ Namespace を返す。

    argv が None なら sys.argv[1:] を使う。範囲外の値と、process_fps が ffmpeg_fps より大きい組み合わせは
    parser.error で使い方とともに表示して終了する (SystemExit)。
    """
    raise NotImplementedError


def main(argv: list[str] | None = None):
    """起動引数を解釈してから、ブリッジ・Controller・キャプチャ・ソケットを用意してコマンドを受け付ける。"""
    args = parse_args(argv)
    bridge = Bridge()
    controller = None
    capture = None
    server = None
    try:
        controller = Controller(bridge)
        capture = Capture(args.ffmpeg_fps, args.process_fps, args.buffer_seconds)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_ADDRESS)
        server.listen(1)
        print("daemon ready", flush=True)
        while True:
            conn, _ = server.accept()
            with conn:
                if serve_connection(conn, capture, controller):
                    break
    finally:
        # 解放スレッドを止めてからブリッジを閉じ、hold の解放がブリッジの終了と重ならないようにする
        if controller is not None:
            controller.close()
        bridge.close()
        if capture is not None:
            capture.close()
        if server is not None:
            server.close()


if __name__ == "__main__":
    main()
