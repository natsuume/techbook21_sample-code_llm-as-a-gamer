"""デーモンが受け取る 1 行コマンドを解釈し、キャプチャとコントローラーに振り分ける。

CHORD は SPEC (ボタン名か LS:x,y / RS:x,y) を + でつないだもので、含まれる SPEC を同時に押す。
ボタン名は A B X Y L R ZL ZR PLUS MINUS HOME UP RIGHT DOWN LEFT (大文字小文字を区別しない)。
スティックの x は右、y は上を正とする -1.0〜1.0 の小数。詳しい規則は controller_state を参照。

コマンド:
    tap CHORD [CHORD ...]  CHORD を順に押す (押下 120ms、離して 100ms、CHORD 間にさらに 150ms)。
                           down / hold で押している入力は保ったまま押す。全 CHORD を送り終えてから応答する
                           応答: ok tap RIGHT RIGHT A+B
    hold CHORD MS          CHORD を MS ミリ秒 (整数 1〜60000) 押す。押したらすぐ応答し、MS 経過後にバックグラウンドで離す
                           応答: ok hold B+RIGHT 2000ms
    down CHORD [CHORD ...] CHORD を押したままにする。押したままのボタンは何もせず、倒したままのスティックは新しい値で置き換える
                           応答: ok held=A+LS:0,1
    up TARGET [TARGET ...] TARGET (ボタン名か LS / RS。+ でつないでもよい) を、down と hold の両方から離す。
                           押されていない TARGET が 1 つでもあればエラーにする
                           応答: ok held=<down で押したままの入力。無ければ none>
    up ALL                 down と hold で押している入力をすべて離す (ALL は大文字小文字を区別しない)
                           応答: ok held=none
    shot PATH              バッファーの最新フレームを待たずに PATH に PNG 保存する。PATH は絶対パスで空白を含んでよい
                           応答: ok /tmp/screen.png
    fps N                  処理レート (バッファーに残すフレームレート) を N (整数 1〜ffmpeg_fps) に変える
                           応答: ok fps=12
    status                 受信フレーム数、バッファー内の枚数、最新フレームの経過時間、フレームレート、ffmpeg のフレーム落ち回数、
                           ブリッジの生存・状態 (ok / recovering / failed)・復旧に成功した回数、押下状態を返す
                           応答: ok frames=... buffered=... latest_age=0.05s ffmpeg_fps=... process_fps=... ffmpeg_drops=... ended=... bridge_alive=... bridge_state=ok bridge_restarts=0 held=... holds=B+RIGHT:1200ms last_error=none

tap・hold・down は、既に押している入力と重なる CHORD (同じボタン、同じスティック、相反する十字キー) をエラーにし、何も送らない。
上記以外のコマンドと引数の数が合わないコマンドには `error unknown command: ...` を返す。
quit は接続の処理 (daemon.serve_connection) で扱う。
"""

import os

from switch2_remote.capture import Capture
from switch2_remote.controller import Controller
from switch2_remote.controller_state import parse_chord, parse_up_targets

HOLD_MS_MIN, HOLD_MS_MAX = 1, 60000
PROCESS_FPS_MIN = 1  # 処理レートの下限。上限は Capture.ffmpeg_fps


def parse_hold_ms(text: str) -> int:
    hold_ms = int(text)
    if not HOLD_MS_MIN <= hold_ms <= HOLD_MS_MAX:
        raise ValueError(f"hold ms must be {HOLD_MS_MIN}..{HOLD_MS_MAX}: {hold_ms}")
    return hold_ms


def parse_shot(request: str) -> str:
    """`shot PATH` を解釈して PATH を返す。PATH は空白を含みうるため、1 つ目の空白以降をすべてパスとして扱う。

    PATH が無い場合と、絶対パスでない場合は ValueError にする。
    """
    parts = request.rstrip("\r\n").split(maxsplit=1)
    if len(parts) != 2:
        raise ValueError("usage: shot ABSOLUTE_PATH")
    path = parts[1]
    if not os.path.isabs(path):
        raise ValueError(f"shot path must be absolute: {path}")
    return path


def parse_fps(text: str, max_fps: int) -> int:
    """`fps N` の N を解釈する。N は整数 PROCESS_FPS_MIN〜max_fps。

    範囲外と整数でない N は、現在の範囲を含むメッセージ (例: `fps must be 1..30: 45`) の ValueError にする。
    """
    error = ValueError(f"fps must be {PROCESS_FPS_MIN}..{max_fps}: {text}")
    try:
        fps = int(text)
    except ValueError:
        raise error from None
    if not PROCESS_FPS_MIN <= fps <= max_fps:
        raise error
    return fps


def handle_command(request: str, capture: Capture, controller: Controller) -> str:
    """1 行コマンドを解釈し、Controller か Capture の対応する操作を呼んで応答の 1 行を返す。

    文字列の解釈 (controller_state.parse_chord / parse_up_targets、parse_hold_ms、parse_shot、parse_fps) と振り分けだけを行う。
    解釈できない CHORD・TARGET・MS・PATH・N は、何も送らずに ValueError にする。
    up の引数が ALL の 1 つだけなら controller.up_all() を呼ぶ。
    fps の N の上限には capture.ffmpeg_fps を使う。
    status は capture.status() に ` bridge_alive=<controller.bridge.is_alive()> bridge_state=<controller.bridge.state>
    bridge_restarts=<controller.bridge.restarts> ` と controller.status_text() を続けた 1 行を返す。
    """
    parts = request.split()
    cmd, args = parts[0], parts[1:]
    if cmd == "tap" and args:
        return controller.tap([parse_chord(arg) for arg in args])
    if cmd == "hold" and len(args) == 2:
        return controller.hold(parse_chord(args[0]), parse_hold_ms(args[1]))
    if cmd == "down" and args:
        return controller.down([parse_chord(arg) for arg in args])
    if cmd == "up" and args:
        if len(args) == 1 and args[0].upper() == "ALL":
            return controller.up_all()
        return controller.up(parse_up_targets(args))
    if cmd == "shot":
        return capture.shot(parse_shot(request))
    if cmd == "fps" and len(args) == 1:
        return capture.set_process_fps(parse_fps(args[0], capture.ffmpeg_fps))
    if cmd == "status":
        bridge = controller.bridge
        return (
            f"{capture.status()} bridge_alive={bridge.is_alive()} "
            f"bridge_state={bridge.state} bridge_restarts={bridge.restarts} "
            f"{controller.status_text()}"
        )
    return f"error unknown command: {' '.join(parts)}"
