"""デーモンが受け取る 1 行コマンドを解釈し、キャプチャとブリッジに振り分ける。

コマンド:
    tap BTN [BTN ...]      各ボタンを順に押す (押下 120ms、ボタン間 150ms)。BTN は controller_bridge.ps1 と同じ名前
    hold BTN MS            BTN を MS ミリ秒押す
    shot TIMEOUT PATH      コマンド受信後のフレームで画面が STABLE_FRAMES 枚静止するまで待ち (最大 TIMEOUT 秒, 0〜60)、PATH に PNG 保存する。PATH は絶対パスで空白を含んでよい
    status                 受信フレーム数、ffmpeg のフレーム落ち回数、ブリッジの生存を返す

quit は接続の処理 (daemon.serve_connection) で扱う。
"""

import os
import time

from switch2_remote.capture import Capture
from switch2_remote.controller_bridge import Bridge, UnconfirmedInputError

TAP_INTERVAL = 0.15
# controller_bridge.ps1 が受け付けるボタン名
BUTTONS = frozenset(
    [
        "A",
        "B",
        "X",
        "Y",
        "L",
        "R",
        "ZL",
        "ZR",
        "PLUS",
        "MINUS",
        "HOME",
        "UP",
        "RIGHT",
        "DOWN",
        "LEFT",
        "LR",
    ]
)
HOLD_MS_MIN, HOLD_MS_MAX = 1, 5000
# shot の待ち上限 (秒)。単一スレッドで処理するため、上限が無いと後続のコマンド (quit を含む) が止まる
SHOT_TIMEOUT_MIN, SHOT_TIMEOUT_MAX = 0.0, 60.0


def validate_buttons(buttons: list[str]) -> list[str]:
    """ボタン名を大文字にそろえ、1 つでも不明な名前があれば入力を送る前に ValueError にする。"""
    upper = [button.upper() for button in buttons]
    unknown = [button for button in upper if button not in BUTTONS]
    if unknown:
        raise ValueError(f"unknown button: {' '.join(unknown)}")
    return upper


def parse_hold_ms(text: str) -> int:
    hold_ms = int(text)
    if not HOLD_MS_MIN <= hold_ms <= HOLD_MS_MAX:
        raise ValueError(f"hold ms must be {HOLD_MS_MIN}..{HOLD_MS_MAX}: {hold_ms}")
    return hold_ms


def tap_sequence(bridge: Bridge, buttons: list[str]) -> str:
    """ボタンを順に押す。途中で失敗したら、送信済みの入力を含めて error を返す。"""
    replies = []
    for button in buttons:
        try:
            replies.append(bridge.tap(button))
        except (RuntimeError, OSError) as e:
            sent = " | ".join(replies) if replies else "none"
            # ack 後の失敗では押下は送られている可能性があるため、未送信と区別して報告する
            unconfirmed = button if isinstance(e, UnconfirmedInputError) else "none"
            return f"error {e!r} (already sent: {sent}; unconfirmed: {unconfirmed})"
        time.sleep(TAP_INTERVAL)
    return " | ".join(replies)


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


def handle_command(request: str, capture: Capture, bridge: Bridge) -> str:
    parts = request.split()
    cmd, args = parts[0], parts[1:]
    if cmd == "tap" and args:
        return tap_sequence(bridge, validate_buttons(args))
    if cmd == "hold" and len(args) == 2:
        (button,) = validate_buttons(args[:1])
        return bridge.tap(button, parse_hold_ms(args[1]))
    if cmd == "shot":
        timeout, path = parse_shot(request)
        return capture.shot(path, timeout)
    if cmd == "status":
        return f"{capture.status()} bridge_alive={bridge.is_alive()}"
    return f"error unknown command: {' '.join(parts)}"
