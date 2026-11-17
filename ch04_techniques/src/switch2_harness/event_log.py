"""コマンドの記録 records/events.jsonl の型と、追記・読み込み。

1 行が 1 イベントの JSON で、形は次のとおり:
    {"time": float, "command": str, "args": [str, ...], "reply": str, "ok": bool, "elapsed_s": float, "frame": str | null}
記録は要素の有効/無効によらず常に取り、session_report の集計に使う。
ただし command が "stagnation-alert" の行は、停滞の知らせの要素 (stagnation-alert) が知らせを出した look の行の直後に足す
(args は秒数の文字列 1 つ、reply は知らせの文、frame はその look の frame)。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

# イベントの command に入る値
EVENT_COMMANDS: tuple[str, ...] = (
    "look",
    "press",
    "status",
    "finish",
    "run",
    "stagnation-alert",
)


@dataclass(frozen=True)
class CommandEvent:
    """1 つのコマンドの記録。

    time: コマンドを始めた時刻 (epoch 秒)
    command: "look" / "press" / "status" / "finish" / "run" / "stagnation-alert" のいずれか。
        "stagnation-alert" は、停滞の知らせを出した look の直後に足す行 (time はその look の time)
    args: コマンドの引数 (commands の各処理の説明を参照。"stagnation-alert" は秒数の文字列 1 つ)
    reply: デーモンの応答、または拒否・説明のメッセージ ("stagnation-alert" は知らせの文)
    ok: 成功したか (reply が error で始まらなければ True)
    elapsed_s: コマンドにかかった秒数
    frame: look で保存した PNG の、作業ディレクトリーからの相対パス (例: "frames/000001.png")。
        "stagnation-alert" では、知らせを出した look の frame。
        look が失敗したときと、look・"stagnation-alert" 以外のコマンドでは None
    """

    time: float
    command: str
    args: tuple[str, ...]
    reply: str
    ok: bool
    elapsed_s: float
    frame: str | None = None


def event_to_json_line(event: CommandEvent) -> str:
    """event を events.jsonl の 1 行 (末尾の改行を含む) にする。

    キーは time / command / args / reply / ok / elapsed_s / frame で、args は JSON の配列、frame が None なら null。
    json.dumps(..., ensure_ascii=False) を使い、日本語はそのまま書く。
    """
    data = {
        "time": event.time,
        "command": event.command,
        "args": list(event.args),
        "reply": event.reply,
        "ok": event.ok,
        "elapsed_s": event.elapsed_s,
        "frame": event.frame,
    }
    return json.dumps(data, ensure_ascii=False) + "\n"


def _is_number(value: object) -> TypeGuard[int | float]:
    """value が数値 (int か float。bool は除く) なら True を返す。"""
    return isinstance(value, int | float) and not isinstance(value, bool)


def event_from_json_line(line: str) -> CommandEvent:
    """events.jsonl の 1 行を CommandEvent にする (args はタプルにする)。

    JSON のオブジェクトでない行と、値の型が上の形と違う行は ValueError にする。
    """
    data: object = json.loads(line)
    if isinstance(data, dict):
        return _event_from_object(data, line)
    raise ValueError(f"event must be a JSON object: {line!r}")


def _event_from_object(data: dict[str, object], line: str) -> CommandEvent:
    """JSON のオブジェクト data を CommandEvent にする。値の型が違えば ValueError にする (line はメッセージ用)。"""
    time = data.get("time")
    command = data.get("command")
    args = data.get("args")
    reply = data.get("reply")
    ok = data.get("ok")
    elapsed_s = data.get("elapsed_s")
    frame = data.get("frame")
    if (
        not _is_number(time)
        or not isinstance(command, str)
        or not isinstance(args, list)
        or not all(isinstance(arg, str) for arg in args)
        or not isinstance(reply, str)
        or not isinstance(ok, bool)
        or not _is_number(elapsed_s)
        or not (frame is None or isinstance(frame, str))
    ):
        raise ValueError(f"event has unexpected fields: {line!r}")
    return CommandEvent(
        time=float(time),
        command=command,
        args=tuple(str(arg) for arg in args),
        reply=reply,
        ok=ok,
        elapsed_s=float(elapsed_s),
        frame=frame,
    )


def append_event(path: Path, event: CommandEvent) -> None:
    """event を path に追記する ("a" モード、UTF-8)。path の親ディレクトリーは作らない。"""
    with path.open("a", encoding="utf-8") as events_file:
        events_file.write(event_to_json_line(event))


def read_events(path: Path) -> list[CommandEvent]:
    """path の各行を CommandEvent にして、ファイルの順に返す。

    行は "\\n" だけで分ける (reply の中にありうる U+2028 などでは分けない)。
    空行 (空白だけの行を含む) は読み飛ばす。path が無ければ空のリストを返す。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [event_from_json_line(line) for line in text.split("\n") if line.strip()]
