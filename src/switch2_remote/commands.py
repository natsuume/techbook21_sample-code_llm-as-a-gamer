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
    shot TIMEOUT PATH      コマンド受信後のフレームで画面が STABLE_FRAMES 枚静止するまで待ち (最大 TIMEOUT 秒, 0〜60)、PATH に PNG 保存する。PATH は絶対パスで空白を含んでよい
    status                 受信フレーム数、ffmpeg のフレーム落ち回数、ブリッジの生存、押下状態を返す
                           応答: ok frames=... ffmpeg_drops=... ended=... bridge_alive=... held=... holds=B+RIGHT:1200ms last_error=none

tap・hold・down は、既に押している入力と重なる CHORD (同じボタン、同じスティック、相反する十字キー) をエラーにし、何も送らない。
上記以外のコマンドと引数の数が合わないコマンドには `error unknown command: ...` を返す。
quit は接続の処理 (daemon.serve_connection) で扱う。
"""

import os

from switch2_remote.capture import Capture
from switch2_remote.controller import Controller

HOLD_MS_MIN, HOLD_MS_MAX = 1, 60000
# shot の待ち上限 (秒)。単一スレッドで処理するため、上限が無いと後続のコマンド (quit を含む) が止まる
SHOT_TIMEOUT_MIN, SHOT_TIMEOUT_MAX = 0.0, 60.0


def parse_hold_ms(text: str) -> int:
    hold_ms = int(text)
    if not HOLD_MS_MIN <= hold_ms <= HOLD_MS_MAX:
        raise ValueError(f"hold ms must be {HOLD_MS_MIN}..{HOLD_MS_MAX}: {hold_ms}")
    return hold_ms


def parse_shot(request: str) -> tuple[float, str]:
    """`shot TIMEOUT PATH` を解釈する。PATH は空白を含みうるため、2 つ目の空白以降をすべてパスとして扱う。"""
    parts = request.rstrip("\r\n").split(maxsplit=2)
    if len(parts) != 3:
        raise ValueError("usage: shot TIMEOUT ABSOLUTE_PATH")
    timeout, path = float(parts[1]), parts[2]
    if not (SHOT_TIMEOUT_MIN < timeout <= SHOT_TIMEOUT_MAX):
        raise ValueError(
            f"shot timeout must be in ({SHOT_TIMEOUT_MIN}, {SHOT_TIMEOUT_MAX}]: {parts[1]}"
        )
    if not os.path.isabs(path):
        raise ValueError(f"shot path must be absolute: {path}")
    return timeout, path


def handle_command(request: str, capture: Capture, controller: Controller) -> str:
    """1 行コマンドを解釈し、Controller か Capture の対応する操作を呼んで応答の 1 行を返す。

    文字列の解釈 (controller_state.parse_chord / parse_up_targets、parse_hold_ms、parse_shot) と振り分けだけを行う。
    解釈できない CHORD・TARGET・MS は、何も送らずに ValueError にする。
    up の引数が ALL の 1 つだけなら controller.up_all() を呼ぶ。
    status は capture.status() に ` bridge_alive=<controller.bridge.is_alive()> ` と controller.status_text() を続けた行を返す。
    """
    raise NotImplementedError
