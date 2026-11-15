"""TypeSafe Jev を呼ぶ SurfaceDecider (JevSurfaceDecider) と、要求の state の作り方。

要求の state (キーはこの順):
    {
      "instructions": surface_questions.SURFACE_INSTRUCTIONS,
      "documents": {"tasks": 短期タスクリスト, "tips": 攻略情報, "controls": 操作説明, "advice": 助言},
      "timeline": {"created": 系列情報の作成時刻のラベル (無ければ NO_TIMELINE_TEXT), "text": 系列情報},
      "recent_actions": [{"time": 時刻のラベル, "held": [...], "taps": [...], "sticks": {"LS": [x, y], "RS": [x, y]}}, ...],
      "frames": [{"label": 時刻のラベル, "image": <画像の参照文字列>}, ...]
    }
時刻のラベルは relative_time_label (例: "t=12.345 秒 (0.4 秒前)")。「何秒前」は要求の時刻 (SurfaceInput.t) から数える。
recent_actions と frames は古い順。画像 (SurfaceInput.frames の JPEG) は assets に入れ、state の frames の image に
参照文字列を 1 回だけ置く。

1 回の decide の流れ:
    1. 要求の番号を 1 増やす (最初の decide が 1)
    2. 対象の一覧を surface.box_targets.read_box_targets(notes/, 場面 ID) で読み、BoxTrackingState.set_targets に渡す
    3. 問い直す対象を box_tracking_state.select_requery_targets で選ぶ
    4. 問いの一覧 (surface_questions.build_surface_questions) と state を作り、JevClient.ask で送る
    5. 答えから、操作・deep を呼ぶ種類・情報ラベル・場面転換を決める (surface.action_rules)
    6. box: 存在の確率が 0.5 未満の対象は追跡をやめ、box を出さない。問い直した対象は Jev の box で追跡を始め直し、
       Jev の box を出す (出どころ jev)。Jev の box が使えない (幅か高さが 0 以下で None) なら、追跡をやめ、box を
       出さない。ほかの対象は、要求の最新のフレームで追跡が見えていれば、その位置を出す (出どころ tracker)
ask の例外 (JevError など) はそのまま外に出す (surface のループが surface の失敗として数える)。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from switch2_layers.run_directory import RunPaths
from switch2_layers.settings import (
    DEFAULT_BOX_REQUERY_EVERY,
    DEFAULT_THRESHOLD_ADVISOR,
)
from switch2_layers.surface.box_tracking_state import BoxTrackingState
from switch2_layers.surface.jev_client import (
    JevAnswers,
    JevImage,
    new_asset_reference,
)
from switch2_layers.surface.surface_questions import SurfaceQuestion
from switch2_layers.surface.surface_types import (
    RecentAction,
    SurfaceDocuments,
    SurfaceInput,
    SurfaceOutput,
)

NO_TIMELINE_TEXT = "まだ作られていない"  # 系列情報がまだ無いときの timeline の created


class JevAsker(Protocol):
    """Jev に問いを送って答えを受け取るもの。run では surface.jev_client.JevClient。"""

    def ask(
        self,
        state: Mapping[str, object],
        questions: Sequence[SurfaceQuestion],
        images: Sequence[JevImage],
    ) -> JevAnswers:
        """state・questions・images の要求を送り、すべての問いの答えを返す。失敗したら例外を出す。"""
        ...


@dataclass(frozen=True)
class JevSurfaceSettings:
    """JevSurfaceDecider の設定。

    allowed_buttons     押せるボタン (buttons.allowed_buttons(--allow-home))。ボタンの問いはこのボタンだけにする
    threshold_advisor   advisor 判定のしきい値 (--threshold-advisor)。確率がこれを超えた種類の deep を呼ぶ
    box_requery_every   前回の問い直しからこの回数の要求がたった対象の box を問い直す (--box-requery-every)
    """

    allowed_buttons: frozenset[str]
    threshold_advisor: float = DEFAULT_THRESHOLD_ADVISOR
    box_requery_every: int = DEFAULT_BOX_REQUERY_EVERY


def relative_time_label(t: float, request_t: float) -> str:
    """時刻 t のラベル `t=<t を小数第 3 位まで> 秒 (<何秒前> 秒前)` を返す (例: "t=12.345 秒 (0.4 秒前)")。

    何秒前は request_t − t を小数第 1 位まで書いた値。負なら 0.0 にする。
    """
    raise NotImplementedError


def documents_to_state(documents: SurfaceDocuments) -> dict[str, str]:
    """文書を state の documents {"tasks", "tips", "controls", "advice"} (この順) にする。"""
    raise NotImplementedError


def timeline_to_state(
    timeline: str, generated_t: float | None, request_t: float
) -> dict[str, str]:
    """系列情報を state の timeline {"created", "text"} にする。

    created は relative_time_label(generated_t, request_t)。generated_t が None なら NO_TIMELINE_TEXT。
    """
    raise NotImplementedError


def recent_actions_to_state(
    recent_actions: Sequence[RecentAction], request_t: float
) -> list[dict[str, object]]:
    """直近の操作を、古い順の [{"time", "held", "taps", "sticks"}] にする。

    time は relative_time_label(操作の t, request_t)、held・taps・sticks は pad_action.action_to_json の値。
    """
    raise NotImplementedError


def build_surface_state(
    surface_input: SurfaceInput, references: Sequence[str]
) -> dict[str, object]:
    """要求の state (モジュールの docstring の形) を返す。

    references は surface_input.frames と同じ順・同じ数の画像の参照文字列。frames の各要素は
    {"label": relative_time_label(frame.t, surface_input.t), "image": 参照文字列}。数が合わなければ ValueError。
    """
    raise NotImplementedError


class JevSurfaceDecider:
    """SurfaceDecider の実装。TypeSafe Jev に問いを送り、操作と付随する情報を決める (モジュールの docstring)。

    client           Jev に問いを送るもの
    tracking         box の追跡の状態 (キャプチャーの on_frame と、送る画像の boxes_at にも同じものを使う)
    settings         設定
    notes_dir        文書のディレクトリー (notes/)。None なら use_run_directory で後から渡す
    make_reference   画像の参照文字列を作る関数 (既定 jev_client.new_asset_reference。呼ぶたびに異なる文字列を返す)
    decide は 1 つのスレッド (surface のループ) からだけ呼ぶ。
    """

    def __init__(
        self,
        client: JevAsker,
        tracking: BoxTrackingState,
        settings: JevSurfaceSettings,
        notes_dir: Path | None = None,
        make_reference: Callable[[], str] = new_asset_reference,
    ) -> None:
        """属性を持つ。要求の番号は 0 から始める。"""
        raise NotImplementedError

    @property
    def request_count(self) -> int:
        """これまでに decide を呼んだ回数 (直前の要求の番号)。"""
        raise NotImplementedError

    def use_run_directory(self, paths: RunPaths) -> None:
        """実行ディレクトリーの notes/ (paths.notes_dir) を、対象の一覧を読むディレクトリーにする。

        app.LayersApp が実行ディレクトリーを作った後、スレッドを起動する前に 1 回呼ぶ (LayersParts.on_run_directory)。
        """
        raise NotImplementedError

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        """surface_input から Jev に問いを送り、出力を返す (モジュールの docstring の流れ)。

        notes_dir がまだ無ければ (use_run_directory の前)、Jev を呼ばずに RuntimeError。
        box を問う画像と、出力の boxes が指すフレームは、要求の最新のフレーム (surface_input.frames の最後)。
        出力の boxes は対象の一覧の順で、Box の label は対象の名前。
        - 出どころ jev: 座標は Jev の box (x_min = left、y_min = top、x_max = right、y_max = bottom)、
          confidence は Jev の確信度 (null なら None)
        - 出どころ tracker: 座標は BoxTrackingState.result_at(名前, 最新のフレームの t) の位置、
          confidence は min(max(score, 0.0), 1.0)。その位置が無いか見失っていれば出さない
        Jev の box で追跡を始められなかったとき (問い合わせたフレームを捨てた後など) も、Jev の box は出す
        (その対象は追跡していないため、次の要求で問い直す)。Jev の box が None (使える box が無い) の対象は、
        存在の確率に依らず追跡をやめ (BoxTrackingState.forget)、box を出さない (次の要求で問い直す)。
        """
        raise NotImplementedError
