"""Switch 2 を 1 コマンドずつ操作するための常駐デーモン。

キャプチャ (ffmpeg.exe, 720p NV12) と controller_bridge.ps1 を起動したまま保持する。
キャプチャは処理レートに間引いたフレームのうち最新の 1 枚を保持し、ブリッジから作った Controller は押下状態を保持する。
Unix ソケット (抽象名前空間 "switch2-remote") で 1 行コマンドを受け付けて 1 行で応答する。
クライアントは switch2-remote を使う。
終了時は Controller、ブリッジ、キャプチャの順に閉じる。

ブリッジは bridge_supervisor.BridgeSupervisor で包み、ブリッジが使えなくなったら裏ですぐ復旧を始める:
COM ポートの開き直しを再試行し、戻らなければ Windows の Bluetooth 無線をオフ・オンしてから開き直す。
復旧中の入力のコマンドは error を返す。復旧に成功したら押下状態を空にし (Controller.reset_state)、
status の bridge_state が ok に戻り、bridge_restarts が 1 増える。復旧できなければ bridge_state が failed になる。
ブリッジの最初の起動に失敗したら、デーモンは起動せずに終了する。

コマンド (CHORD は SPEC を + でつないだもの。詳しくは commands を参照):
    tap CHORD [CHORD ...]  CHORD を順に押す (押下 120ms、離して 100ms、CHORD 間にさらに 150ms)。送り終えてから応答する
    hold CHORD MS          CHORD を MS ミリ秒 (1〜60000) 押す。押したらすぐ応答し、MS 経過後にバックグラウンドで離す
    down CHORD [CHORD ...] CHORD を押したままにする
    up TARGET [TARGET ...] TARGET (ボタン名か LS / RS) を離す。up ALL ですべて離す
    shot PATH              持っている最新フレームを、新しいフレームを待たずに PATH に PNG 保存する。PATH は絶対パスで空白を含んでよい
    fps N                  処理レートを N (整数 1〜ffmpeg_fps) に変える
    status                 受信フレーム数、最新フレームの経過時間、フレームレート、ffmpeg のフレーム落ち回数、
                           ブリッジの生存・状態・復旧に成功した回数、押下状態を返す
    quit                  コントローラー、ブリッジ、キャプチャを止めて終了する

起動引数:
    --device-fps N      デバイスから受け取るフレームレート (30 か 60、既定 60。環境変数 SWITCH2_DEVICE_FPS があれば
                        その値)。起動後は変えられない。PC との USB の接続で 60fps を受け取れないときは 30 にする
    --ffmpeg-fps N      ffmpeg の出力のフレームレート (整数 1〜device_fps、既定 30)。起動後は変えられない
    --process-fps N     間引いて残すフレームレート (整数 1〜ffmpeg_fps、既定 10)。fps コマンドで変えられる
    --com-port PORT     ブリッジが開く COM ポート (Pico 2 W の Bluetooth の発信側。COM1、COM2 のような COM と 1 以上の
                        整数で、大文字小文字を区別しない。既定 COM3。環境変数 SWITCH2_COM_PORT があればその値)。
                        最初の起動にも復旧での起動し直しにも使う
    範囲外の値と、--ffmpeg-fps が --device-fps より大きい組み合わせと、--process-fps が --ffmpeg-fps より大きい
    組み合わせと、--com-port の形の誤りと、環境変数 SWITCH2_COM_PORT・SWITCH2_DEVICE_FPS の値の誤りは
    起動時にエラーにする (環境変数は hardware_env で読む。空の値は無いのと同じに扱う)

使い方:
    uv run switch2-remote-daemon
    uv run switch2-remote-daemon --ffmpeg-fps 30 --process-fps 12
    uv run switch2-remote-daemon --device-fps 30
    uv run switch2-remote-daemon --com-port COM4
"""

import argparse
import functools
import os
import socket
import struct
from collections.abc import Mapping

import cv2

from switch2_remote.bluetooth_radio import reset_bluetooth_radio
from switch2_remote.bridge_supervisor import BridgeSupervisor
from switch2_remote.capture import DEFAULT_DEVICE_FPS, DEVICE_FPS_CHOICES, Capture
from switch2_remote.commands import PROCESS_FPS_MIN, handle_command
from switch2_remote.controller import Controller
from switch2_remote.controller_bridge import (
    DEFAULT_COM_PORT,
    Bridge,
    BridgeStartError,
    parse_com_port,
)
from switch2_remote.error_message import describe_error
from switch2_remote.hardware_env import (
    COM_PORT_ENV,
    DEVICE_FPS_ENV,
    com_port_from_env,
    device_fps_from_env,
)

SOCKET_ADDRESS = (
    "\0switch2-remote"  # 抽象名前空間。ファイルパスの長さ制限と後片付けが不要
)
REQUEST_READ_TIMEOUT = 5.0  # リクエスト 1 行の受信と応答の送信にかける上限 (秒)
FFMPEG_FPS_MIN = 1  # 上限は --device-fps
DEFAULT_FFMPEG_FPS = 30
DEFAULT_PROCESS_FPS = 10


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
            reply = f"error {describe_error(e)}"
        conn.sendall((reply + "\n").encode())
    except OSError:
        pass  # 応答前にクライアントが切断した場合は、その接続だけを捨てる
    return False


def parse_args(
    argv: list[str] | None, environ: Mapping[str, str] | None = None
) -> argparse.Namespace:
    """起動引数を解釈し、device_fps・ffmpeg_fps・process_fps・com_port を持つ Namespace を返す。

    argv が None なら sys.argv[1:] を使う。範囲外の値と、ffmpeg_fps が device_fps より大きい組み合わせと、
    process_fps が ffmpeg_fps より大きい組み合わせは、parser.error で使い方とともに表示して終了する (SystemExit)。
    device_fps は DEVICE_FPS_CHOICES のどれかだけを受け付ける (argparse の choices)。
    com_port は controller_bridge.parse_com_port で確かめて大文字にする。形の誤りは
    argparse が使い方とともに表示して終了する (SystemExit)。
    --com-port と --device-fps を省いたときは、環境変数の辞書 environ (None なら os.environ) から
    hardware_env.com_port_from_env と hardware_env.device_fps_from_env で決める (変数が無いか空なら
    DEFAULT_COM_PORT と DEFAULT_DEVICE_FPS)。指定したときは環境変数を読まない。環境変数の値の誤りも
    parser.error で使い方とともに表示して終了する (SystemExit)。ffmpeg_fps は、決めた後の device_fps と比べる。
    """
    parser = argparse.ArgumentParser(
        prog="switch2-remote-daemon",
        description="Switch 2 を 1 コマンドずつ操作するための常駐デーモン",
    )
    parser.add_argument(
        "--device-fps",
        type=int,
        choices=DEVICE_FPS_CHOICES,
        default=None,
        help=(
            f"デバイスから受け取るフレームレート (既定 {DEFAULT_DEVICE_FPS}。"
            f"環境変数 {DEVICE_FPS_ENV} があればその値)。"
            "PC との USB の接続で 60fps を受け取れないときは 30 にする"
        ),
    )
    parser.add_argument(
        "--ffmpeg-fps",
        type=int,
        default=DEFAULT_FFMPEG_FPS,
        help=f"ffmpeg の出力のフレームレート ({FFMPEG_FPS_MIN}〜device_fps、既定 {DEFAULT_FFMPEG_FPS})",
    )
    parser.add_argument(
        "--process-fps",
        type=int,
        default=DEFAULT_PROCESS_FPS,
        help=f"間引いて残すフレームレート ({PROCESS_FPS_MIN}〜ffmpeg_fps、既定 {DEFAULT_PROCESS_FPS})",
    )
    parser.add_argument(
        "--com-port",
        type=parse_com_port,
        default=None,
        metavar="PORT",
        help=(
            "ブリッジが開く COM ポート (Pico 2 W の Bluetooth の発信側。"
            f"COM1、COM2 のように指定する。既定 {DEFAULT_COM_PORT}。"
            f"環境変数 {COM_PORT_ENV} があればその値)"
        ),
    )
    args = parser.parse_args(argv)
    if environ is None:
        environ = os.environ
    try:
        if args.com_port is None:
            args.com_port = com_port_from_env(environ)
        if args.device_fps is None:
            args.device_fps = device_fps_from_env(environ)
    except ValueError as e:
        parser.error(str(e))
    if args.ffmpeg_fps < FFMPEG_FPS_MIN:
        parser.error(
            f"--ffmpeg-fps must be {FFMPEG_FPS_MIN} or more: {args.ffmpeg_fps}"
        )
    if args.ffmpeg_fps > args.device_fps:
        parser.error(
            f"--ffmpeg-fps ({args.ffmpeg_fps}) must not exceed "
            f"--device-fps ({args.device_fps})"
        )
    if args.process_fps < PROCESS_FPS_MIN:
        parser.error(
            f"--process-fps must be {PROCESS_FPS_MIN} or more: {args.process_fps}"
        )
    if args.process_fps > args.ffmpeg_fps:
        message = (
            f"--process-fps ({args.process_fps}) must not exceed "
            f"--ffmpeg-fps ({args.ffmpeg_fps})"
        )
        # --process-fps を省略した場合も、既定値が --ffmpeg-fps を超えていればここで止める
        if args.process_fps == DEFAULT_PROCESS_FPS:
            message += f". --process-fps defaults to {DEFAULT_PROCESS_FPS}"
        parser.error(message)
    return args


def main(argv: list[str] | None = None):
    """起動引数を解釈してから、ブリッジ・Controller・キャプチャ・ソケットを用意してコマンドを受け付ける。

    ブリッジは BridgeSupervisor で包み、最初の起動も起動し直しも Bridge(port_name=args.com_port) で行う
    (functools.partial で COM ポートを固定した start_bridge を渡す)。
    最初のブリッジの起動に失敗したら (BridgeStartError)、そのメッセージで終了する (SystemExit)。
    復旧に成功したら Controller の押下状態を空にする (on_recovered = controller.reset_state)。
    """
    args = parse_args(argv)
    try:
        supervisor = BridgeSupervisor(
            start_bridge=functools.partial(Bridge, port_name=args.com_port),
            reset_radio=reset_bluetooth_radio,
        )
    except BridgeStartError as e:
        raise SystemExit(str(e)) from None
    controller = None
    capture = None
    server = None
    try:
        controller = Controller(supervisor)
        supervisor.on_recovered = controller.reset_state
        capture = Capture(args.ffmpeg_fps, args.process_fps, device_fps=args.device_fps)
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
        supervisor.close()
        if capture is not None:
            capture.close()
        if server is not None:
            server.close()


if __name__ == "__main__":
    main()
