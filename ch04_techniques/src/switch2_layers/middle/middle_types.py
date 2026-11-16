"""middle 層の型: MiddleAnalyzer の入力 (MiddleInput) と出力 (MiddleOutput)、middle/state.json (MiddleState)、Protocol。

出力のマップの観察 (MapObservation と ObservationKind) もここに置く。つながり (MapConnection) は、map.json と同じ型
(notes.map_document) を使う。

middle/state.json の形:
    {"generated_t", "based_on_t", "scene_id", "scene_name", "is_stuck", "stuck_description", "surface_wait_s", "timeline",
     "surface_rows_offset", "operations_rows_offset"}
surface_rows_offset と operations_rows_offset は、次の middle の入力で surface.jsonl と operations.jsonl を読み始める
バイト位置 (前回までに読んだ完全な行の末尾)。行の時刻ではなくファイルの位置で持つ (middle_input_builder のモジュールの
docstring)。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from switch2_layers.notes.map_document import MapConnection, MapDocument
from switch2_layers.notes.note_paths import is_scene_id
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.progress.progress_state import ProgressState


class ObservationKind(StrEnum):
    """middle が記録する観察の種類 (gpt-6-luna の出力の new_observations の kind の値)。"""

    EXIT = "exit"  # 出口
    LANDMARK = "landmark"  # 目印
    BLOCKED = "blocked"  # 通れなかった位置


@dataclass(frozen=True)
class MapObservation:
    """middle の出力の new_observations の要素 1 つ (新しく分かった、出口・目印・通れなかった位置)。

    kind          観察の種類
    place         観察した場所の名前
    description   画面上の位置などの説明
    """

    kind: ObservationKind
    place: str
    description: str


@dataclass(frozen=True)
class MiddleFrame:
    """middle に渡す一定間隔のフレーム 1 枚 (frames/interval/ に保存したもの)。

    t      フレームの時刻
    file   画像の実行ディレクトリーからの相対パス
    jpeg   画像の JPEG のバイト列
    """

    t: float
    file: str
    jpeg: bytes


@dataclass(frozen=True)
class MiddleState:
    """middle/state.json の中身 (middle の最新の出力)。

    generated_t              この出力を書いた時刻 (analyze が戻った時刻)
    based_on_t               この出力の入力を作った時刻 (MiddleInput.t)
    scene_id                 現在の場面 ID。場面が決まっていなければ None
    scene_name               現在の場面名。場面が決まっていなければ None
    is_stuck                 止まっている (詰まっている) か
    stuck_description        詰まり方の説明。無ければ空文字列
    surface_wait_s           surface の待ち秒 (範囲に収めるのは読む側の surface)
    timeline                 系列情報
    surface_rows_offset      この出力の入力までに読んだ surface.jsonl の完全な行の末尾のバイト位置
                             (MiddleInput.surface_rows_offset)。次の入力はこの位置から読む
    operations_rows_offset   この出力の入力までに読んだ operations.jsonl の完全な行の末尾のバイト位置
                             (MiddleInput.operations_rows_offset)。次の入力はこの位置から読む
    """

    generated_t: float
    based_on_t: float
    scene_id: str | None
    scene_name: str | None
    is_stuck: bool
    stuck_description: str
    surface_wait_s: float
    timeline: str
    surface_rows_offset: int
    operations_rows_offset: int


@dataclass(frozen=True)
class MiddleInput:
    """MiddleAnalyzer.analyze の入力。実行ディレクトリーのファイルだけから作る (middle_input_builder)。

    t                        入力を作った時刻。フレームの範囲の終わり
    frames                   直近 middle_window 秒の一定間隔のフレームから最大 4 枚 (古い順)
    surface_rows             前回の middle の実行以降の surface.jsonl の行 (書かれた順)
    operation_rows           前回の middle の実行以降の operations.jsonl の行 (書かれた順)
    scene_change_reported    surface_rows のどれかで scene_changed が真なら True (surface が場面転換を検知した印)
    scene_labels             場面ラベルの一覧
    previous_state           前回の middle/state.json。無いか読めなければ None
    strategy                 大方針 (notes/strategy.md)。無ければ空文字列
    current_tasks            現在の場面 (previous_state の scene_id) の短期タスクリスト。場面が無いか文書が無ければ空文字列
    surface_rows_offset      surface_rows を読んだ後の、surface.jsonl の完全な行の末尾のバイト位置。
                             この入力で書いた middle/state.json に記録し、次の入力はこの位置から読む
    operations_rows_offset   operation_rows を読んだ後の、operations.jsonl の完全な行の末尾のバイト位置。使い方は
                             surface_rows_offset と同じ
    current_controls         現在の場面 (previous_state の scene_id) の操作説明。surface と同じ規則
                             (notes.surface_documents) で選ぶ: 場面が決まっていて、場面の controls.md があって空 (空白だけを
                             含む) でなければ場面の文書、そうでなければ notes/common/controls.md。読めなければ空文字列。
                             既定値の空文字列は、操作説明を渡さない入力 (テストなど) のため
    place_names              場所の名前の一覧 (middle.place_names.collect_place_names)。notes/map/map.json の places の
                             name と aliases、middle/map_records.jsonl の place_name を合わせ、重複を除いて最初に現れた順に
                             並べたもの。既定値の空のタプルは、マップを渡さない入力 (テストなど) のため
    map_document             notes/map/map.json の全体 (notes.map_document.read_map_document)。無い・読めない・形に
                             合わないときは None (マップなし)。既定値の None は、マップを渡さない入力 (テストなど) のため
    progress                 画面の停滞の計測の最新の値 (progress/state.json。progress.progress_state.read_progress_state)。
                             無い・読めないときは None。既定値の None は、値を渡さない入力 (テストなど) のため
    """

    t: float
    frames: tuple[MiddleFrame, ...]
    surface_rows: tuple[Mapping[str, object], ...]
    operation_rows: tuple[Mapping[str, object], ...]
    scene_change_reported: bool
    scene_labels: tuple[SceneLabel, ...]
    previous_state: MiddleState | None
    strategy: str
    current_tasks: str
    surface_rows_offset: int
    operations_rows_offset: int
    current_controls: str = ""
    place_names: tuple[str, ...] = ()
    map_document: MapDocument | None = None
    progress: ProgressState | None = None


@dataclass(frozen=True)
class MiddleOutput:
    """MiddleAnalyzer.analyze の出力。

    scene_name          場面名。既存の一覧から選び、当てはまるものが無いときだけ新しい名前にする。決められなければ None
    is_stuck            止まっている (詰まっている) か
    stuck_description   詰まり方の説明
    surface_wait_s      surface の待ち秒 (0.2〜2.0 の範囲を想定する。範囲外でも surface が収める)
    timeline            系列情報
    tasks               現在の場面の短期タスクリスト (Markdown)
    place_name          現在地の場所の名前。場所の名前の一覧から選び、当てはまるものが無いときだけ新しい名前にする。
                        ゲームの世界の中の場所が映っていない (メニュー・タイトル・戦闘など) か、決められなければ None
    new_connections     前回の実行より後に新しく分かった、場所から場所への移動 (無ければ空)
    new_observations    新しく分かった、出口・目印・通れなかった位置 (無ければ空)
    place_name・new_connections・new_observations の既定値は、マップを記録しない出力 (フェイクやテストなど) のため。
    """

    scene_name: str | None
    is_stuck: bool
    stuck_description: str
    surface_wait_s: float
    timeline: str
    tasks: str
    place_name: str | None = None
    new_connections: tuple[MapConnection, ...] = ()
    new_observations: tuple[MapObservation, ...] = ()


class MiddleAnalyzer(Protocol):
    """middle の分析。後続の実装では gpt-6-luna を呼ぶ。"""

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        """middle_input から場面・詰まり・待ち時間・系列情報・短期タスクリストを決めて返す。失敗したら例外を出す。"""
        ...


def middle_state_to_json(state: MiddleState) -> dict[str, object]:
    """MiddleState を middle/state.json の辞書 (モジュールの docstring のキーの順) にする。"""
    return {
        "generated_t": state.generated_t,
        "based_on_t": state.based_on_t,
        "scene_id": state.scene_id,
        "scene_name": state.scene_name,
        "is_stuck": state.is_stuck,
        "stuck_description": state.stuck_description,
        "surface_wait_s": state.surface_wait_s,
        "timeline": state.timeline,
        "surface_rows_offset": state.surface_rows_offset,
        "operations_rows_offset": state.operations_rows_offset,
    }


def middle_state_from_json(value: object) -> MiddleState:
    """middle/state.json を読んだ値を MiddleState にする。

    value が object でない、キーが足りない、型が合わないときは ValueError にする:
    generated_t・based_on_t・surface_wait_s は数 (bool を除く)、surface_rows_offset と operations_rows_offset は
    0 以上の整数 (bool を除く)、scene_id と scene_name は文字列か null、is_stuck は真偽、stuck_description と timeline は
    文字列。scene_id が文字列なら場面 ID の形に限る。
    """
    if isinstance(value, dict):
        return _middle_state_from_object(value)
    raise ValueError("middle state must be a JSON object")


def _middle_state_from_object(value: Mapping[str, object]) -> MiddleState:
    """middle/state.json の object を MiddleState にする (middle_state_from_json の本体)。"""
    scene_id = _optional_string(value, "scene_id")
    if scene_id is not None and not is_scene_id(scene_id):
        raise ValueError(f"invalid scene id in middle state: {scene_id!r}")
    return MiddleState(
        generated_t=_number(value, "generated_t"),
        based_on_t=_number(value, "based_on_t"),
        scene_id=scene_id,
        scene_name=_optional_string(value, "scene_name"),
        is_stuck=_boolean(value, "is_stuck"),
        stuck_description=_string(value, "stuck_description"),
        surface_wait_s=_number(value, "surface_wait_s"),
        timeline=_string(value, "timeline"),
        surface_rows_offset=_offset(value, "surface_rows_offset"),
        operations_rows_offset=_offset(value, "operations_rows_offset"),
    )


# 以下の読み取りは、契約 (middle_state_from_json) どおり型の誤りも ValueError にする。
# 型が合えば値を返し、合わなければ if の後で ValueError を出す


def _field(value: Mapping[str, object], key: str) -> object:
    """value[key] を返す。キーが無ければ ValueError。"""
    if key not in value:
        raise ValueError(f"middle state has no {key!r}")
    return value[key]


def _number(value: Mapping[str, object], key: str) -> float:
    """value[key] が数 (bool を除く) ならその値を返す。それ以外は ValueError。"""
    item = _field(value, key)
    if isinstance(item, int | float) and not isinstance(item, bool):
        return float(item)
    raise ValueError(f"{key!r} in middle state must be a number: {item!r}")


def _offset(value: Mapping[str, object], key: str) -> int:
    """value[key] が 0 以上の整数 (bool を除く) ならその値を返す。それ以外は ValueError。"""
    item = _field(value, key)
    if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
        return item
    raise ValueError(
        f"{key!r} in middle state must be a non-negative integer: {item!r}"
    )


def _string(value: Mapping[str, object], key: str) -> str:
    """value[key] が文字列ならその値を返す。それ以外は ValueError。"""
    item = _field(value, key)
    if isinstance(item, str):
        return item
    raise ValueError(f"{key!r} in middle state must be a string: {item!r}")


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    """value[key] が文字列か null ならその値を返す。それ以外は ValueError。"""
    item = _field(value, key)
    if item is None or isinstance(item, str):
        return item
    raise ValueError(f"{key!r} in middle state must be a string or null: {item!r}")


def _boolean(value: Mapping[str, object], key: str) -> bool:
    """value[key] が真偽ならその値を返す。それ以外は ValueError。"""
    item = _field(value, key)
    if isinstance(item, bool):
        return item
    raise ValueError(f"{key!r} in middle state must be a boolean: {item!r}")
