"""middle 層の型: MiddleAnalyzer の入力 (MiddleInput) と出力 (MiddleOutput)、middle/state.json (MiddleState)、Protocol。

middle/state.json の形:
    {"generated_t", "based_on_t", "scene_id", "scene_name", "is_stuck", "stuck_description", "surface_wait_s", "timeline",
     "surface_rows_until_t", "operations_rows_until_t"}
surface_rows_until_t と operations_rows_until_t は、次の middle の入力に入れる surface.jsonl と operations.jsonl の
行の下限 (この時刻より後の行を読む)。入力を作った時刻ではなく、実際に読んだ行の時刻から決める
(middle_input_builder のモジュールの docstring)。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from switch2_layers.notes.scene_labels import SceneLabel


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

    generated_t               この出力を書いた時刻 (analyze が戻った時刻)
    based_on_t                この出力の入力を作った時刻 (MiddleInput.t)
    scene_id                  現在の場面 ID。場面が決まっていなければ None
    scene_name                現在の場面名。場面が決まっていなければ None
    is_stuck                  止まっている (詰まっている) か
    stuck_description         詰まり方の説明。無ければ空文字列
    surface_wait_s            surface の待ち秒 (範囲に収めるのは読む側の surface)
    timeline                  系列情報
    surface_rows_until_t      この出力の入力までに読んだ surface.jsonl の行の t_received の最大値
                              (MiddleInput.surface_rows_until_t)。まだ 1 行も読んでいなければ None
    operations_rows_until_t   この出力の入力までに読んだ operations.jsonl の行の t の最大値
                              (MiddleInput.operations_rows_until_t)。まだ 1 行も読んでいなければ None
    """

    generated_t: float
    based_on_t: float
    scene_id: str | None
    scene_name: str | None
    is_stuck: bool
    stuck_description: str
    surface_wait_s: float
    timeline: str
    surface_rows_until_t: float | None
    operations_rows_until_t: float | None


@dataclass(frozen=True)
class MiddleInput:
    """MiddleAnalyzer.analyze の入力。実行ディレクトリーのファイルだけから作る (middle_input_builder)。

    t                         入力を作った時刻。フレームと行の範囲の終わり
    frames                    直近 middle_window 秒の一定間隔のフレームから最大 4 枚 (古い順)
    surface_rows              前回の middle の実行以降の surface.jsonl の行 (書かれた順)
    operation_rows            前回の middle の実行以降の operations.jsonl の行 (書かれた順)
    scene_change_reported     surface_rows のどれかで scene_changed が真なら True (surface が場面転換を検知した印)
    scene_labels              場面ラベルの一覧
    previous_state            前回の middle/state.json。無いか読めなければ None
    strategy                  大方針 (notes/strategy.md)。無ければ空文字列
    current_tasks             現在の場面 (previous_state の scene_id) の短期タスクリスト。場面が無いか文書が無ければ空文字列
    surface_rows_until_t      surface_rows の t_received の最大値。surface_rows が空なら前回の値
                              (previous_state.surface_rows_until_t。前回が無ければ None)。
                              この入力で書いた middle/state.json に記録し、次の入力の下限にする
    operations_rows_until_t   operation_rows の t の最大値。operation_rows が空なら前回の値
                              (previous_state.operations_rows_until_t。前回が無ければ None)。使い方は surface_rows_until_t と同じ
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
    surface_rows_until_t: float | None
    operations_rows_until_t: float | None


@dataclass(frozen=True)
class MiddleOutput:
    """MiddleAnalyzer.analyze の出力。

    scene_name          場面名。既存の一覧から選び、当てはまるものが無いときだけ新しい名前にする。決められなければ None
    is_stuck            止まっている (詰まっている) か
    stuck_description   詰まり方の説明
    surface_wait_s      surface の待ち秒 (0.2〜2.0 の範囲を想定する。範囲外でも surface が収める)
    timeline            系列情報
    tasks               現在の場面の短期タスクリスト (Markdown)
    """

    scene_name: str | None
    is_stuck: bool
    stuck_description: str
    surface_wait_s: float
    timeline: str
    tasks: str


class MiddleAnalyzer(Protocol):
    """middle の分析。後続の実装では gpt-6-luna を呼ぶ。"""

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        """middle_input から場面・詰まり・待ち時間・系列情報・短期タスクリストを決めて返す。失敗したら例外を出す。"""
        ...


def middle_state_to_json(state: MiddleState) -> dict[str, object]:
    """MiddleState を middle/state.json の辞書 (モジュールの docstring のキーの順) にする。"""
    raise NotImplementedError


def middle_state_from_json(value: object) -> MiddleState:
    """middle/state.json を読んだ値を MiddleState にする。

    value が object でない、キーが足りない、型が合わないときは ValueError にする:
    generated_t・based_on_t・surface_wait_s は数 (bool を除く)、surface_rows_until_t と operations_rows_until_t は
    数 (bool を除く) か null、scene_id と scene_name は文字列か null、is_stuck は真偽、stuck_description と timeline は
    文字列。scene_id が文字列なら場面 ID の形に限る。
    """
    raise NotImplementedError
