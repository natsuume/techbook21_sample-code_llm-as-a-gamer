"""要素 (feature) の一覧と、要素の名前の検証。

要素は先行研究の手法を 1 つずつ足したもので、有効にすると CLAUDE.md に節が加わり、要素によって、
エージェントが使えるサブコマンドが増えるか、エージェントが書き換えられるファイルが増えるか、
サブコマンドの出力が変わる。
すべての要素を無効にした状態が、第3章と同じ基準条件になる。
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class WritableFile:
    """要素が作業ディレクトリーの直下に置き、エージェントが Write・Edit ツールで書き換えられるファイル。

    name: 作業ディレクトリーの直下のファイル名 (例: "notes.md")
    template_file: init で置く最初の内容の、instructions/ の下のファイル名 (例: "notes-initial.md")。
        置換 ($game などの string.Template) はせず、そのまま書く
    """

    name: str
    template_file: str


@dataclass(frozen=True)
class Feature:
    """1 つの要素。

    name: CLI (init --enable) と harness.json で使う名前 (例: "action-history")
    title: 日本語の名前
    instruction_file: CLAUDE.md に足す節のテンプレートの、instructions/ の下のファイル名
    commands: この要素で使えるようになる、エージェント用のサブコマンドの名前
    writable_files: この要素で、エージェントが Write・Edit ツールで書き換えられるようになるファイル
    """

    name: str
    title: str
    instruction_file: str
    commands: tuple[str, ...]
    writable_files: tuple[WritableFile, ...] = ()


# 行動履歴の要素の名前。有効にすると、look が直近の行動 (look と press) を表示する (action_history)
ACTION_HISTORY_FEATURE = "action-history"

# メモファイルの要素の名前。有効にすると、エージェントが作業ディレクトリーの notes.md を Write・Edit ツールで書き換えられる
NOTES_FEATURE = "notes"
NOTES_FILE_NAME = "notes.md"

# 利用できる要素の一覧。この順が CLAUDE.md の節の順、サブコマンドの順、書き換えられるファイルの順になる
FEATURES: tuple[Feature, ...] = (
    Feature(
        name=ACTION_HISTORY_FEATURE,
        title="行動履歴",
        instruction_file="action-history.md",
        commands=(),
    ),
    Feature(
        name=NOTES_FEATURE,
        title="メモファイル",
        instruction_file="notes.md",
        commands=(),
        writable_files=(
            WritableFile(name=NOTES_FILE_NAME, template_file="notes-initial.md"),
        ),
    ),
)

# 要素によらずエージェントが使えるサブコマンド
BASE_AGENT_COMMANDS: tuple[str, ...] = ("look", "press", "status", "finish")


def available_names_text(available: tuple[Feature, ...] = FEATURES) -> str:
    """利用できる要素の名前を ", " でつないだ文字列を返す。1 つも無ければ "none"。

    例: 名前が "a" と "b" の 2 つなら "a, b"。
    """
    return ", ".join(feature.name for feature in available) or "none"


def validate_feature_names(
    names: Iterable[str], available: tuple[Feature, ...] = FEATURES
) -> tuple[str, ...]:
    """names を検証し、重複を除いて available の順に並べた名前のタプルを返す。

    available に無い名前があれば ValueError にする。メッセージは
    `unknown feature: <無い名前を指定された順に (重複は除いて) ", " でつないだもの> (available: <available_names_text>)`。
    例: available が空で names が ["x"] なら `unknown feature: x (available: none)`。
    """
    requested = list(names)
    known = {feature.name for feature in available}
    # dict.fromkeys で、指定された順を保ったまま重複を除く
    unknown = list(dict.fromkeys(name for name in requested if name not in known))
    if unknown:
        raise ValueError(
            f"unknown feature: {', '.join(unknown)} "
            f"(available: {available_names_text(available)})"
        )
    wanted = set(requested)
    return tuple(feature.name for feature in available if feature.name in wanted)


def agent_commands(
    enabled: Iterable[str], available: tuple[Feature, ...] = FEATURES
) -> tuple[str, ...]:
    """エージェントが使えるサブコマンドを返す。

    BASE_AGENT_COMMANDS に、enabled に名前がある要素の commands を available の順に続けたもの。
    enabled は validate_feature_names を通した名前を渡す。
    """
    enabled_names = set(enabled)
    feature_commands = tuple(
        command
        for feature in available
        if feature.name in enabled_names
        for command in feature.commands
    )
    return BASE_AGENT_COMMANDS + feature_commands


def agent_writable_files(
    enabled: Iterable[str], available: tuple[Feature, ...] = FEATURES
) -> tuple[WritableFile, ...]:
    """エージェントが書き換えられるファイルを返す。

    enabled に名前がある要素の writable_files を、available の順に続けたもの。
    enabled は validate_feature_names を通した名前を渡す。
    """
    enabled_names = set(enabled)
    return tuple(
        writable_file
        for feature in available
        if feature.name in enabled_names
        for writable_file in feature.writable_files
    )
