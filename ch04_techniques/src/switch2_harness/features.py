"""要素 (feature) の一覧と、要素の名前の検証。

要素は先行研究の手法を 1 つずつ足したもので、有効にすると、要素によって、CLAUDE.md に節が加わるか、
エージェントが使えるサブコマンドが増えるか、エージェントが書き換えられるファイルが増えるか、
サブコマンドの出力が変わる (行動履歴・停滞の知らせ・振り返り・複数フレーム。停滞の知らせは、run の間に裏で撮った画面をもとに、
look の出力に知らせの行を足す。振り返りは、run の間に別のモデルに批評させて notes.md に書き、look で知らせる。
複数フレームは、look のときに続けて撮った画面を縮小して 1 枚に並べた画像を保存し、look の出力にそのパスの行を足す)。
ほかの要素と併用する要素 (Feature.requires) は、併用する要素を有効にしないと有効にできない。
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
class FileAppend:
    """要素が、ほかの要素の書き換えられるファイル (WritableFile) の最初の内容の末尾に足す内容。

    name: 足す先のファイル名 (例: "notes.md")。Feature.requires で併用する要素の writable_files にあるファイルに限る
    template_file: 足す内容の、instructions/ の下のファイル名 (例: "reflection-notes-section.md")。
        置換 ($game などの string.Template) はせず、そのまま足す
    """

    name: str
    template_file: str


@dataclass(frozen=True)
class Feature:
    """1 つの要素。

    name: CLI (init --enable) と harness.json で使う名前 (例: "action-history")
    title: 日本語の名前
    instruction_file: CLAUDE.md に足す節のテンプレートの、instructions/ の下のファイル名。None なら CLAUDE.md に節を足さない
    commands: この要素で使えるようになる、エージェント用のサブコマンドの名前
    writable_files: この要素で、エージェントが Write・Edit ツールで書き換えられるようになるファイル
    requires: この要素と一緒に有効にしなければならない要素の名前
    file_appends: init で、併用する要素のファイルの最初の内容の末尾に足す内容
    """

    name: str
    title: str
    instruction_file: str | None
    commands: tuple[str, ...]
    writable_files: tuple[WritableFile, ...] = ()
    requires: tuple[str, ...] = ()
    file_appends: tuple[FileAppend, ...] = ()


# 行動履歴の要素の名前。有効にすると、look が直近の行動 (look と press) を表示する (action_history)
ACTION_HISTORY_FEATURE = "action-history"

# メモファイルの要素の名前。有効にすると、エージェントが作業ディレクトリーの notes.md を Write・Edit ツールで書き換えられる
NOTES_FEATURE = "notes"
NOTES_FILE_NAME = "notes.md"

# 停滞の知らせの要素の名前。有効にすると、run の間に裏で画面を撮って records/screen-samples/ に記録し (screen_recorder)、
# 撮影ごとに新しい画面かを判定する。新しい画面が 120 秒出ていないと、look が知らせの行を表示する (stagnation_alert)
STAGNATION_ALERT_FEATURE = "stagnation-alert"

# 振り返りの要素の名前。メモファイルの要素と併用する。有効にすると、run の間に 5 分ごとに別のモデル (批評役) に進め方を
# 批評させ、ハーネスが notes.md の「振り返り」の節に書く (reflection_critic)。書いた後の最初の look が知らせの行を表示する (reflection)
REFLECTION_FEATURE = "reflection"

# 複数フレームの要素の名前。有効にすると、look が 0.2 秒おきに 6 回画面を撮り、6 枚を縮小して 1 枚に並べた画像を
# multi-frames/ に保存して、そのパスの行を表示する (multi_frame)
MULTI_FRAME_FEATURE = "multi-frame"

# 利用できる要素の一覧。この順が CLAUDE.md の節の順、サブコマンドの順、書き換えられるファイルの順、
# ファイルの末尾に足す内容の順になる
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
    Feature(
        name=STAGNATION_ALERT_FEATURE,
        title="停滞の知らせ",
        instruction_file="stagnation-alert.md",
        commands=(),
    ),
    Feature(
        name=REFLECTION_FEATURE,
        title="振り返り",
        instruction_file="reflection.md",
        commands=(),
        requires=(NOTES_FEATURE,),
        file_appends=(
            FileAppend(
                name=NOTES_FILE_NAME, template_file="reflection-notes-section.md"
            ),
        ),
    ),
    Feature(
        name=MULTI_FRAME_FEATURE,
        title="複数フレーム",
        instruction_file="multi-frame.md",
        commands=(),
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
    無い名前が無ければ、続けて、available の順に各要素の requires を見て、names に無い要素があれば ValueError にする。
    メッセージは、最初に見つかった組について `feature <要素の名前> requires <併用する要素の名前>`
    (例: `feature reflection requires notes`)。
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
    for feature in available:
        if feature.name not in wanted:
            continue
        for required in feature.requires:
            if required not in wanted:
                raise ValueError(f"feature {feature.name} requires {required}")
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


def agent_file_appends(
    enabled: Iterable[str], available: tuple[Feature, ...] = FEATURES
) -> tuple[FileAppend, ...]:
    """init で、書き換えられるファイルの最初の内容の末尾に足す内容を返す。

    enabled に名前がある要素の file_appends を、available の順に続けたもの。
    enabled は validate_feature_names を通した名前を渡す。
    """
    enabled_names = set(enabled)
    return tuple(
        file_append
        for feature in available
        if feature.name in enabled_names
        for file_append in feature.file_appends
    )
