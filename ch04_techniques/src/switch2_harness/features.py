"""要素 (feature) の一覧と、要素の名前の検証。

要素は先行研究の手法を 1 つずつ足したもので、有効にすると CLAUDE.md に節が加わり、
エージェントが使えるサブコマンドが増える。すべての要素を無効にした状態が、第3章と同じ基準条件になる。
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    """1 つの要素。

    name: CLI (init --enable) と harness.json で使う名前 (例: "action-history")
    title: 日本語の名前
    instruction_file: CLAUDE.md に足す節のテンプレートの、instructions/ の下のファイル名
    commands: この要素で使えるようになる、エージェント用のサブコマンドの名前
    """

    name: str
    title: str
    instruction_file: str
    commands: tuple[str, ...]


# 利用できる要素の一覧。この順が CLAUDE.md の節の順とサブコマンドの順になる
FEATURES: tuple[Feature, ...] = ()

# 要素によらずエージェントが使えるサブコマンド
BASE_AGENT_COMMANDS: tuple[str, ...] = ("look", "press", "status", "finish")


def available_names_text(available: tuple[Feature, ...] = FEATURES) -> str:
    """利用できる要素の名前を ", " でつないだ文字列を返す。1 つも無ければ "none"。

    例: 名前が "a" と "b" の 2 つなら "a, b"。
    """
    raise NotImplementedError


def validate_feature_names(
    names: Iterable[str], available: tuple[Feature, ...] = FEATURES
) -> tuple[str, ...]:
    """names を検証し、重複を除いて available の順に並べた名前のタプルを返す。

    available に無い名前があれば ValueError にする。メッセージは
    `unknown feature: <無い名前を指定された順に ", " でつないだもの> (available: <available_names_text>)`。
    例: available が空で names が ["x"] なら `unknown feature: x (available: none)`。
    """
    raise NotImplementedError


def agent_commands(
    enabled: Iterable[str], available: tuple[Feature, ...] = FEATURES
) -> tuple[str, ...]:
    """エージェントが使えるサブコマンドを返す。

    BASE_AGENT_COMMANDS に、enabled に名前がある要素の commands を available の順に続けたもの。
    enabled は validate_feature_names を通した名前を渡す。
    """
    raise NotImplementedError
