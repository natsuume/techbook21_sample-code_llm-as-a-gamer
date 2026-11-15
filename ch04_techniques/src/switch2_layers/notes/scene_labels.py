"""場面ラベルの一覧 notes/scene_labels.json の読み書きと、名前から場面 ID を引く処理、一覧に足す処理。

形式: {"scenes": [{"id": "scene-001", "name": 場面名, "first_seen_t": 秒}]}
ID は `scene-` と 3 桁の連番 (1000 番目からは 4 桁)。一覧に足すのは middle のスレッド (と middle-once) だけ。
新しい場面の ID は resolve_scene で一覧を書き換えずに決め、ほかのファイルを書き終えてから add_scene_label で足す
(middle.middle_output_writer の書く順)。
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.file_io import write_json_atomic
from switch2_layers.notes.note_paths import is_scene_id

SCENE_ID_PREFIX = "scene-"


@dataclass(frozen=True)
class SceneLabel:
    """場面ラベル 1 件。

    id             場面 ID (scene-001 の形)
    name           場面名
    first_seen_t   初めてその場面に名前を付けた middle の入力の時刻 (実行の経過秒)
    """

    id: str
    name: str
    first_seen_t: float


def read_scene_labels(path: Path) -> list[SceneLabel]:
    """path の一覧を読み、書かれた順の SceneLabel のリストを返す。

    ファイルが無い・読めない・JSON として壊れている・"scenes" がリストでないときは空のリストを返す。
    要素のうち、id が場面 ID の形の文字列、name が文字列、first_seen_t が数 (bool を除く) でないものは読み飛ばす。
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(value, dict) or not isinstance(value.get("scenes"), list):
        return []
    labels: list[SceneLabel] = []
    for item in value["scenes"]:
        if not isinstance(item, dict):
            continue
        scene_id = item.get("id")
        name = item.get("name")
        first_seen_t = item.get("first_seen_t")
        if not isinstance(scene_id, str) or not is_scene_id(scene_id):
            continue
        if not isinstance(name, str):
            continue
        if not isinstance(first_seen_t, int | float) or isinstance(first_seen_t, bool):
            continue
        labels.append(SceneLabel(id=scene_id, name=name, first_seen_t=first_seen_t))
    return labels


def scene_labels_to_json(labels: Sequence[SceneLabel]) -> dict[str, object]:
    """一覧を {"scenes": [{"id", "name", "first_seen_t"}, ...]} の形にする (並びは labels の順)。"""
    return {
        "scenes": [
            {"id": label.id, "name": label.name, "first_seen_t": label.first_seen_t}
            for label in labels
        ]
    }


def write_scene_labels(path: Path, labels: Sequence[SceneLabel]) -> None:
    """scene_labels_to_json(labels) を path に置き換えて書く (file_io.write_json_atomic)。"""
    write_json_atomic(path, scene_labels_to_json(labels))


def normalize_scene_name(name: str) -> str:
    """場面名の比較と記録に使う形 (前後の空白を除いたもの) を返す。"""
    return name.strip()


def find_scene_id(labels: Sequence[SceneLabel], name: str) -> str | None:
    """normalize_scene_name で比べて name と同じ名前の場面の ID を返す。無ければ None。"""
    wanted = normalize_scene_name(name)
    for label in labels:
        if normalize_scene_name(label.name) == wanted:
            return label.id
    return None


def next_scene_id(labels: Sequence[SceneLabel]) -> str:
    """次の場面 ID を返す。一覧の ID の番号の最大 + 1 を 3 桁 (足りなければ 0 で埋める) で書く。一覧が空なら scene-001。"""
    numbers = [
        int(label.id[len(SCENE_ID_PREFIX) :])
        for label in labels
        if label.id.startswith(SCENE_ID_PREFIX)
        and label.id[len(SCENE_ID_PREFIX) :].isdigit()
    ]
    return f"{SCENE_ID_PREFIX}{max(numbers, default=0) + 1:03d}"


def resolve_scene(
    labels: Sequence[SceneLabel], name: str, first_seen_t: float
) -> tuple[SceneLabel, bool]:
    """一覧 labels から name の場面を引く。(場面ラベル, 一覧に無い新しい場面なら True) を返す。ファイルは扱わない。

    - 一覧にあれば (find_scene_id)、その SceneLabel と False を返す
    - 無ければ SceneLabel(next_scene_id(labels), normalize_scene_name(name), first_seen_t) と True を返す
      (一覧には足さない。足すのは add_scene_label)
    name が空か空白だけなら ValueError にする。
    """
    normalized = normalize_scene_name(name)
    if not normalized:
        raise ValueError("scene name must not be blank")
    for label in labels:
        if normalize_scene_name(label.name) == normalized:
            return label, False
    return SceneLabel(next_scene_id(labels), normalized, first_seen_t), True


def add_scene_label(path: Path, label: SceneLabel) -> bool:
    """path の一覧の末尾に label を足して書き (write_scene_labels)、足したら True を返す。

    一覧に同じ名前 (normalize_scene_name で比べる) の場面が既にあれば、何も書かずに False を返す。
    ファイルが無いか読めないときは、空の一覧に足したものを書く。
    """
    labels = read_scene_labels(path)
    if find_scene_id(labels, label.name) is not None:
        return False
    write_scene_labels(path, [*labels, label])
    return True
