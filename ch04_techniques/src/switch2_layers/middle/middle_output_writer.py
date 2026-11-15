"""middle の出力 (MiddleOutput) を実行ディレクトリーに書く。

書く順 (surface が middle/state.json の新しい場面を読んだときに、その場面のファイルがそろっているようにする):
    1. 場面名が一覧に無ければ notes/scene_labels.json に足し、新しい場面 ID を付ける
    2. 現在の場面の notes/scenes/<場面ID>/tasks.md を置き換える
    3. middle/state.json を置き換える
middle/calls.jsonl の行は middle_loop.run_middle_step が書く。
"""

from dataclasses import dataclass

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput, MiddleState
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.run_directory import RunPaths

# 初めての場面に名前が付いたときに deep を呼ぶ文書の種類 (その場面の操作説明と攻略情報)
NEW_SCENE_DEEP_KINDS: tuple[DocumentKind, ...] = (
    DocumentKind.CONTROLS,
    DocumentKind.TIPS,
)


@dataclass(frozen=True)
class MiddleWriteResult:
    """middle の出力を書いた結果。

    state       middle/state.json に書いた値
    new_scene   一覧に新しく足した場面。足さなかったら None
    """

    state: MiddleState
    new_scene: SceneLabel | None


def write_middle_output(
    paths: RunPaths,
    middle_input: MiddleInput,
    output: MiddleOutput,
    generated_t: float,
) -> MiddleWriteResult:
    """output を実行ディレクトリー paths に書く (モジュールの docstring の順)。

    1. output.scene_name が空でなければ (前後の空白を除いて)、notes.scene_labels.find_or_add_scene(
       notes/scene_labels.json, scene_name, first_seen_t=middle_input.t) で場面 ID を得る。足したらそれが new_scene。
       scene_name が None か空なら、場面 ID も場面名も None (tasks.md は書かない)
    2. 場面 ID があれば、notes/scenes/<場面ID>/tasks.md を output.tasks で置き換える (ディレクトリーが無ければ作る)
    3. MiddleState(generated_t, based_on_t=middle_input.t, scene_id, scene_name (前後の空白を除いたもの), is_stuck,
       stuck_description, surface_wait_s, timeline) を middle/state.json に置き換えて書く
    書き込みの例外はそのまま出す (それより前に書いたファイルは戻さない)。
    """
    raise NotImplementedError
