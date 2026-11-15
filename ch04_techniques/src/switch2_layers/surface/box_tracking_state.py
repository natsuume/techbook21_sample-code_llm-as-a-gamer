"""box の対象ごとの追跡の状態 (BoxTrackingState) と、次の要求で box を問い直す対象の選び方 (select_requery_targets)。

追跡はキャプチャーが保持するすべてのフレーム (約 10 fps) で行う。キャプチャーのスレッドが新しいフレームごとに
on_frame を呼び、追跡中のすべての対象の位置を更新する。間引いたフレームで追うと当たりが大きく落ちるため、
フレームを飛ばさない。
Jev が返した box は、問い合わせた画像のフレームで追跡を始め (start_from_jev)、そこから最新まで、保持している
フレームで追いつかせる。そのため、直近 retain_s 秒のフレーム (TrackingFrame) を持っておく。retain_s の既定は、
Jev のタイムアウト (10 秒) と surface に送るフレームの範囲 (2 秒) の和 (DEFAULT_RETAIN_S) で、要求を送ってから
応答が返るまでのフレームが残る。
on_frame (キャプチャーのスレッド) とほかのメソッド (surface のスレッド) は、1 つのロックの中で状態を読み書きする。

問い直す対象 (select_requery_targets) は次のどれかに当たる対象:
    - まだ box が無い (追跡していない) 対象
    - 見失った対象 (最後に更新したフレームで found が False)
    - 前回の問い直しから requery_every 回の要求がたった対象 (今の要求の番号 − 前回問い直した要求の番号 >= requery_every)
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.surface.box_tracker import (
    BoxTracker,
    NccBoxTracker,
    NormalizedBox,
    TrackResult,
)
from switch2_layers.surface.surface_types import Box

DEFAULT_RETAIN_S = 12.0  # フレームと追跡の位置を持つ秒数 (Jev のタイムアウト 10 秒 + 送るフレームの範囲 2 秒)


@dataclass(frozen=True)
class TargetStatus:
    """追跡している対象 1 つの状態 (問い直す対象の選び方に使う)。

    lost                 最後に更新したフレームで見失っていたら True (追跡を始めてまだ更新していなければ False)
    last_requery_index   最後に box を問い直した要求の番号 (JevSurfaceDecider の要求の番号。1 始まり)
    """

    lost: bool
    last_requery_index: int


def select_requery_targets(
    target_names: Sequence[str],
    statuses: Mapping[str, TargetStatus],
    request_index: int,
    requery_every: int,
) -> tuple[str, ...]:
    """target_names のうち、番号 request_index の要求で box を問い直す対象の名前を、target_names の順で返す。

    statuses は追跡している対象の名前 → 状態 (BoxTrackingState.statuses)。名前が statuses に無い対象は、まだ box が
    無いものとして問い直す。statuses の中の target_names に無い名前は無視する。
    requery_every が 1 未満なら ValueError。
    例 (requery_every = 5): 番号 1 で問い直した対象は、見失わなければ番号 6 で問い直す (2〜5 では問い直さない)。
    """
    raise NotImplementedError


class BoxTrackingState:
    """対象ごとの追跡 (BoxTracker) と、直近のフレームと位置を持つ。

    tracker_factory   対象ごとに追跡の部品を作る関数 (既定 NccBoxTracker)
    retain_s          フレームと位置を持つ秒数 (最新のフレームの t から retain_s 秒より古いものを捨てる)
    """

    def __init__(
        self,
        tracker_factory: Callable[[], BoxTracker] = NccBoxTracker,
        retain_s: float = DEFAULT_RETAIN_S,
    ) -> None:
        """追跡している対象もフレームも無い状態で作る。retain_s が 0 以下なら ValueError。"""
        raise NotImplementedError

    def on_frame(self, frame: CapturedFrame) -> None:
        """キャプチャーの新しいフレームを受け取る (キャプチャーのスレッドから呼ばれる。CaptureSource の on_frame)。

        box_tracker.tracking_frame_from_captured で TrackingFrame にして持ち、追跡しているすべての対象の update を
        呼んで、その t の位置を記録する。持っている最新のフレームの t 以下のフレームは無視する。
        その後、t が (このフレームの t − retain_s) より古いフレームと位置を捨てる。
        update が例外 (Exception) を出した対象は、追跡をやめる (forget と同じ)。ほかの対象の更新は続ける。
        """
        raise NotImplementedError

    def set_targets(self, names: Sequence[str]) -> None:
        """今の対象の一覧を names にする。names に無い対象の追跡をやめ (forget と同じ)、boxes_at の並びを names の順にする。"""
        raise NotImplementedError

    def statuses(self) -> dict[str, TargetStatus]:
        """追跡している対象の名前 → TargetStatus を返す (追跡していない対象は含めない)。"""
        raise NotImplementedError

    def start_from_jev(
        self, name: str, box: NormalizedBox, frame_t: float, request_index: int
    ) -> bool:
        """Jev が返した box で、対象 name の追跡を始め直す。始められたら True を返す。

        1. 持っているフレームの中から t が frame_t と等しいフレームを探す。無ければ (捨てた後・まだ届いていない)、
           name の追跡をやめて (forget と同じ) False を返す
        2. tracker_factory() で新しい追跡の部品を作って、そのフレームと box で start する (start の ValueError は
           外に出さず、1 と同じく追跡をやめて False を返す)
        3. name の位置のうち t が frame_t より新しいものを捨て、持っているフレームのうち t が frame_t より新しいものを
           古い順に update して、位置を記録する (最新まで追いつかせる)。update が例外 (Exception) を出したら、
           追跡をやめて False を返す
        4. 前回問い直した要求の番号を request_index にする
        frame_t のフレーム自体の位置は記録しない (そのフレームの box は Jev の答え)。
        """
        raise NotImplementedError

    def forget(self, name: str) -> None:
        """対象 name の追跡をやめ、記録した位置を捨てる。追跡していなければ何もしない。"""
        raise NotImplementedError

    def result_at(self, name: str, t: float) -> TrackResult | None:
        """対象 name の、時刻 t のフレームでの追跡の結果を返す。追跡していないか、その t の位置が無ければ None。"""
        raise NotImplementedError

    def boxes_at(self, t: float) -> tuple[Box, ...]:
        """時刻 t のフレームで見えている (found) 対象の box を、set_targets の順で返す (surface に送る画像に描く)。

        Box は label が対象の名前、座標が位置、confidence が min(max(score, 0.0), 1.0)、source が BoxSource.TRACKER。
        その t の位置が無いか、見失っていた対象は含めない。
        """
        raise NotImplementedError
