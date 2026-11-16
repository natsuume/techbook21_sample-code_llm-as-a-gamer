"""有効な要素から、作業ディレクトリーの CLAUDE.md の本文を組み立てる。

テンプレートはパッケージの instructions/ ディレクトリーにある Markdown で、$game と $goal を
string.Template で置換する。基本のテンプレートは instructions/base.md で、有効な要素ごとに
Feature.instruction_file の節を続ける。テンプレートの読み込みは引数 (read_template) で受け取り、
本番では read_package_template を使う。
"""

from collections.abc import Callable
from importlib import resources
from string import Template

from switch2_harness.features import FEATURES, Feature
from switch2_harness.harness_config import HarnessConfig

# 基本のテンプレートの、instructions/ の下のファイル名
BASE_TEMPLATE_FILE = "base.md"

# instructions/ の下のファイル名を受け取り、テンプレートの本文を返す関数の型
ReadTemplate = Callable[[str], str]


def read_package_template(file_name: str) -> str:
    """パッケージの instructions/<file_name> を UTF-8 で読んで返す。

    importlib.resources.files("switch2_harness") を起点に "instructions" と file_name をつないで読む
    (このモジュール switch2_harness.instructions ではなく、パッケージ switch2_harness を起点にする)。
    """
    template = resources.files("switch2_harness") / "instructions" / file_name
    return template.read_text(encoding="utf-8")


def render_instructions(
    config: HarnessConfig,
    read_template: ReadTemplate,
    available: tuple[Feature, ...] = FEATURES,
) -> str:
    """CLAUDE.md の本文を返す。

    1. read_template(BASE_TEMPLATE_FILE) を string.Template(...).substitute(game=config.game, goal=config.goal) で置換する
    2. available のうち config.features に名前がある要素の instruction_file を、available の順に読み、同じ置換をする
    3. 各部分の末尾の改行を除き、空行 1 つ ("\\n\\n") で区切ってつなぎ、末尾を改行 1 つで終える
    例: base が "# B $game\\n"、要素の節が "## F $goal\\n\\n" なら "# B G\\n\\n## F X\\n" (game="G", goal="X")。
    """
    enabled = set(config.features)
    file_names = [BASE_TEMPLATE_FILE] + [
        feature.instruction_file for feature in available if feature.name in enabled
    ]
    sections = [
        Template(read_template(file_name))
        .substitute(game=config.game, goal=config.goal)
        .rstrip("\n")
        for file_name in file_names
    ]
    return "\n\n".join(sections) + "\n"
