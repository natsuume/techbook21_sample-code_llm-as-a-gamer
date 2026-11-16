"""コマンドの記録 records/events.jsonl の型と、追記・読み込み。

1 行が 1 イベントの JSON で、形は次のとおり:
    {"time": float, "command": str, "args": [str, ...], "reply": str, "ok": bool, "elapsed_s": float, "frame": str | null}
記録は要素の有効/無効によらず常に取り、session_report の集計に使う。
"""

from dataclasses import dataclass
from pathlib import Path

# イベントの command に入る値
EVENT_COMMANDS: tuple[str, ...] = ("look", "press", "status", "finish", "run")


@dataclass(frozen=True)
class CommandEvent:
    """1 つのコマンドの記録。

    time: コマンドを始めた時刻 (epoch 秒)
    command: "look" / "press" / "status" / "finish" / "run" のいずれか
    args: コマンドの引数 (commands の各処理の説明を参照)
    reply: デーモンの応答、または拒否・説明のメッセージ
    ok: 成功したか (reply が error で始まらなければ True)
    elapsed_s: コマンドにかかった秒数
    frame: look で保存した PNG の、作業ディレクトリーからの相対パス (例: "frames/000001.png")。
        look が失敗したときと、look 以外のコマンドでは None
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
    raise NotImplementedError


def event_from_json_line(line: str) -> CommandEvent:
    """events.jsonl の 1 行を CommandEvent にする (args はタプルにする)。"""
    raise NotImplementedError


def append_event(path: Path, event: CommandEvent) -> None:
    """event を path に追記する ("a" モード、UTF-8)。path の親ディレクトリーは作らない。"""
    raise NotImplementedError


def read_events(path: Path) -> list[CommandEvent]:
    """path の各行を CommandEvent にして、ファイルの順に返す。

    空行 (空白だけの行を含む) は読み飛ばす。path が無ければ空のリストを返す。
    """
    raise NotImplementedError
