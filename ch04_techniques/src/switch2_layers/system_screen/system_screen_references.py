"""本体の画面の見本 (SYSTEM_SCREEN_REFERENCES): 見本の名前、照合の選択肢の説明の文、画像のファイル名。

見本 (並びはこの順):
    home_menu          HOME メニュー
    controllers        HOME の「コントローラー」
    controller_order   持ちかた/順番を変える
    user_select        ソフトを起動したときのユーザー選択
    software_close     HOME メニューでソフトを終了するときの確認
    software_list      HOME メニューの「もっとみる」で開く、すべてのソフトの一覧
見本の名前は、照合の choice の選択肢の名前、安全の監視の要求の state の references のキー、照合の結果の名前を兼ねる
(英数字と _ だけ)。

見本の画像は、パッケージの switch2_layers/system_screen/references/ に置く (importlib.resources で読む)。1 つの見本に
画像は 1 枚。どれも画面と同じ 1280x720 の JPEG で、本体の UI の部分だけを残し、ほかを (128, 128, 128) の灰色で
塗りつぶす (切り出したままの寸法では Jev が HTTP 400 を返すため、画面の大きさのまま塗りつぶす)。ソフトのアイコンの絵、
ソフトの名前、アカウントの名前とアイコンは含めない。

照合の choice の選択肢は、どの見本の画面でもないこと (NONE_CHOICE) を先頭に置き、続けて見本の名前を上の順に並べる
(SYSTEM_SCREEN_CHOICE_NAMES)。選択肢の説明の文は、今のフレームを state の中のパス `frame` で、見本の画像を
`references.<名前>` で指す (safety.safety_questions の state の形)。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

# 見本の画像を置くパッケージと、その中のディレクトリー
# (importlib.resources.files(REFERENCES_PACKAGE) / REFERENCES_DIR)
REFERENCES_PACKAGE = "switch2_layers.system_screen"
REFERENCES_DIR = "references"

# どの見本の画面でもないことを表す、照合の選択肢の名前と説明
NONE_CHOICE = "none"
NONE_CHOICE_DESCRIPTION = "`frame` は、どの見本の画面でもない"


@dataclass(frozen=True)
class SystemScreenReference:
    """本体の画面の見本 1 つ。

    name          見本の名前 (照合の選択肢の名前と照合の結果の名前を兼ねる)
    description   照合の choice の選択肢の説明の文
    files         references/ の中の画像のファイル名 (1 つの見本に 1 枚)
    """

    name: str
    description: str
    files: tuple[str, ...]


# 本体の画面の見本 (モジュールの docstring の順)
SYSTEM_SCREEN_REFERENCES: tuple[SystemScreenReference, ...] = (
    SystemScreenReference(
        name="home_menu",
        description=(
            "Nintendo Switch 2 の HOME メニュー。"
            "`references.home_menu` と同じ、本体の機能のアイコンの段がある"
        ),
        files=("home_menu.jpg",),
    ),
    SystemScreenReference(
        name="controllers",
        description=(
            "本体の「コントローラー」の画面。"
            "`references.controllers` と同じ、右側の項目の並びがある"
        ),
        files=("controllers.jpg",),
    ),
    SystemScreenReference(
        name="controller_order",
        description=(
            "本体の「持ちかた/順番を変える」の画面。"
            "`references.controller_order` と同じ表示がある"
        ),
        files=("controller_order.jpg",),
    ),
    SystemScreenReference(
        name="user_select",
        description=(
            "本体のユーザー選択の画面。`references.user_select` と同じ見出しの帯がある"
        ),
        files=("user_select.jpg",),
    ),
    SystemScreenReference(
        name="software_close",
        description=(
            "HOME メニューでプレイ中のソフトを終了するときの確認の画面。"
            "`references.software_close` と同じ、終了の確認の文と"
            "「キャンセル」「終了する」のボタンの段がある"
        ),
        files=("software_close.jpg",),
    ),
    SystemScreenReference(
        name="software_list",
        description=(
            "HOME メニューの「もっとみる」で開く、すべてのソフトの一覧の画面。"
            "`references.software_list` と同じ、上の「ソフト」「グループ」のタブ、"
            "左の絞り込みと並べ替えのアイコン、右下の「B もどる」「A 決定」がある"
        ),
        files=("software_list.jpg",),
    ),
)
# 見本の名前 (SYSTEM_SCREEN_REFERENCES と同じ順)
SYSTEM_SCREEN_NAMES: tuple[str, ...] = tuple(
    reference.name for reference in SYSTEM_SCREEN_REFERENCES
)
# 照合の choice の選択肢の名前 (NONE_CHOICE を先頭に、見本の名前を SYSTEM_SCREEN_NAMES の順に並べる)
SYSTEM_SCREEN_CHOICE_NAMES: tuple[str, ...] = (NONE_CHOICE, *SYSTEM_SCREEN_NAMES)


def load_reference_images(
    references: Sequence[SystemScreenReference] = SYSTEM_SCREEN_REFERENCES,
) -> dict[str, tuple[bytes, ...]]:
    """references の見本の画像を、パッケージの references/ から読んで返す。

    戻り値は 見本の名前 → その見本の files の順の JPEG のバイト列 のタプル で、キーは references の順。
    画像の中身 (JPEG であるか、寸法) は確かめない。
    ファイルが無ければ FileNotFoundError をそのまま出す (importlib.resources で読む)。
    run の始めに 1 回だけ呼ぶ (safety.safety_assembly.build_safety_assembly)。
    """
    directory = resources.files(REFERENCES_PACKAGE).joinpath(REFERENCES_DIR)
    return {
        reference.name: tuple(
            directory.joinpath(file).read_bytes() for file in reference.files
        )
        for reference in references
    }
