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

import logging
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.surface.box_tracker import (
    BoxTracker,
    NccBoxTracker,
    NormalizedBox,
    TrackingFrame,
    TrackResult,
    tracking_frame_from_captured,
)
from switch2_layers.surface.surface_types import Box, BoxSource

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

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
    if requery_every < 1:
        raise ValueError(f"requery_every must be 1 or more: {requery_every}")
    selected: list[str] = []
    for name in target_names:
        status = statuses.get(name)
        if (
            status is None
            or status.lost
            or request_index - status.last_requery_index >= requery_every
        ):
            selected.append(name)
    return tuple(selected)


@dataclass
class _TargetTrack:
    """追跡している対象 1 つ (BoxTrackingState の中だけで使う)。

    tracker              追跡の部品
    results              フレームの t → そのフレームの追跡の結果 (t の古い順)
    jev_boxes            Jev の box で追跡を始め直したフレームの t → その Jev の box (t の古い順)
    lost                 最後に更新したフレームで見失っていたら True
    last_requery_index   最後に box を問い直した要求の番号
    """

    tracker: BoxTracker
    last_requery_index: int
    results: dict[float, TrackResult] = field(default_factory=dict)
    jev_boxes: dict[float, NormalizedBox] = field(default_factory=dict)
    lost: bool = False

    def record(self, t: float, result: TrackResult) -> None:
        """フレーム t の結果を記録し、lost を更新する。"""
        self.results[t] = result
        self.lost = not result.found


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
        if not retain_s > 0:
            raise ValueError(f"retain_s must be more than 0: {retain_s}")
        self.tracker_factory = tracker_factory
        self.retain_s = retain_s
        # 以下の状態は、キャプチャーのスレッドと surface のスレッドがこのロックの中で読み書きする
        self._lock = threading.Lock()
        self._frames: deque[TrackingFrame] = deque()
        self._target_names: tuple[str, ...] = ()
        self._tracks: dict[str, _TargetTrack] = {}

    def on_frame(self, frame: CapturedFrame) -> None:
        """キャプチャーの新しいフレームを受け取る (キャプチャーのスレッドから呼ばれる。CaptureSource の on_frame)。

        box_tracker.tracking_frame_from_captured で TrackingFrame にして持ち、追跡しているすべての対象の update を
        呼んで、その t の位置を記録する。持っている最新のフレームの t 以下のフレームは無視する。
        その後、t が (このフレームの t − retain_s) より古いフレームと位置を捨てる。
        update が例外 (Exception) を出した対象は、追跡をやめる (forget と同じ)。ほかの対象の更新は続ける。
        """
        with self._lock:
            if self._frames and frame.t <= self._frames[-1].t:
                return
        tracking_frame = tracking_frame_from_captured(frame)
        with self._lock:
            if self._frames and frame.t <= self._frames[-1].t:
                return
            self._frames.append(tracking_frame)
            for name, track in list(self._tracks.items()):
                try:
                    result = track.tracker.update(tracking_frame)
                except Exception:
                    logger.warning("tracking %s failed", name, exc_info=True)
                    del self._tracks[name]
                    continue
                track.record(frame.t, result)
            self._drop_older_than(frame.t - self.retain_s)

    def set_targets(self, names: Sequence[str]) -> None:
        """今の対象の一覧を names にする。names に無い対象の追跡をやめ (forget と同じ)、boxes_at の並びを names の順にする。"""
        with self._lock:
            self._target_names = tuple(names)
            for name in list(self._tracks):
                if name not in self._target_names:
                    del self._tracks[name]

    def statuses(self) -> dict[str, TargetStatus]:
        """追跡している対象の名前 → TargetStatus を返す (追跡していない対象は含めない)。"""
        with self._lock:
            return {
                name: TargetStatus(
                    lost=track.lost, last_requery_index=track.last_requery_index
                )
                for name, track in self._tracks.items()
            }

    def start_from_jev(
        self, name: str, box: NormalizedBox, frame_t: float, request_index: int
    ) -> bool:
        """Jev が返した box で、対象 name の追跡を始め直す。始められたら True を返す。

        1. 持っているフレームの中から t が frame_t と等しいフレームを探す。無ければ (捨てた後・まだ届いていない)、
           name の追跡をやめて (forget と同じ) False を返す
        2. tracker_factory() で新しい追跡の部品を作って、そのフレームと box で start する (start の ValueError は
           外に出さず、1 と同じく追跡をやめて False を返す)
        3. name の位置のうち t が frame_t 以降のもの (frame_t ちょうどの古い追跡の位置と、それより新しい位置) を
           捨て、frame_t の位置を Jev の box (box) で置き換える。持っているフレームのうち t が frame_t より新しいものを
           古い順に update して、位置を記録する (最新まで追いつかせる)。update が例外 (Exception) を出したら、
           追跡をやめて False を返す
        4. 前回問い直した要求の番号を request_index にする
        frame_t のフレームの位置は Jev の答えで、追跡の結果としては記録しない (result_at(name, frame_t) は None)。
        boxes_at(frame_t) は、この対象を Jev の box (出どころ jev) で返す。
        """
        with self._lock:
            queried = next((f for f in self._frames if f.t == frame_t), None)
            if queried is None:
                self._tracks.pop(name, None)
                return False
            tracker = self.tracker_factory()
            try:
                tracker.start(queried, box)
            except ValueError:
                logger.debug("could not start tracking %s", name, exc_info=True)
                self._tracks.pop(name, None)
                return False
            track = _TargetTrack(tracker=tracker, last_requery_index=request_index)
            previous = self._tracks.get(name)
            if previous is not None:
                # frame_t より古い位置だけを残す (frame_t ちょうどの古い追跡の位置は Jev の box で置き換える)
                track.results = {
                    t: result for t, result in previous.results.items() if t < frame_t
                }
                track.jev_boxes = {
                    t: jev_box
                    for t, jev_box in previous.jev_boxes.items()
                    if t < frame_t
                }
            track.jev_boxes[frame_t] = box
            for later in (f for f in self._frames if f.t > frame_t):
                try:
                    result = tracker.update(later)
                except Exception:
                    logger.warning("catching up %s failed", name, exc_info=True)
                    self._tracks.pop(name, None)
                    return False
                track.record(later.t, result)
            self._tracks[name] = track
            return True

    def forget(self, name: str) -> None:
        """対象 name の追跡をやめ、記録した位置を捨てる。追跡していなければ何もしない。"""
        with self._lock:
            self._tracks.pop(name, None)

    def result_at(self, name: str, t: float) -> TrackResult | None:
        """対象 name の、時刻 t のフレームでの追跡の結果を返す。追跡していないか、その t の位置が無ければ None。"""
        with self._lock:
            track = self._tracks.get(name)
            return None if track is None else track.results.get(t)

    def boxes_at(self, t: float) -> tuple[Box, ...]:
        """時刻 t のフレームで見えている (found) 対象の box を、set_targets の順で返す (surface に送る画像に描く)。

        Box は label が対象の名前、座標が位置、confidence が min(max(score, 0.0), 1.0)、source が BoxSource.TRACKER。
        その t の位置が無いか、見失っていた対象は含めない。
        t がその対象を Jev の box で追跡を始め直したフレーム (start_from_jev の frame_t) なら、Box は座標が Jev の box、
        confidence が None (追跡の状態は Jev の確信度を持たない)、source が BoxSource.JEV。
        """
        with self._lock:
            boxes: list[Box] = []
            for name in self._target_names:
                track = self._tracks.get(name)
                if track is None:
                    continue
                jev_box = track.jev_boxes.get(t)
                if jev_box is not None:
                    boxes.append(
                        Box(
                            label=name,
                            x_min=jev_box.left,
                            y_min=jev_box.top,
                            x_max=jev_box.right,
                            y_max=jev_box.bottom,
                            confidence=None,
                            source=BoxSource.JEV,
                        )
                    )
                    continue
                result = track.results.get(t)
                if result is None or not result.found:
                    continue
                boxes.append(
                    Box(
                        label=name,
                        x_min=result.box.left,
                        y_min=result.box.top,
                        x_max=result.box.right,
                        y_max=result.box.bottom,
                        confidence=min(max(result.score, 0.0), 1.0),
                        source=BoxSource.TRACKER,
                    )
                )
            return tuple(boxes)

    def _drop_older_than(self, oldest_kept_t: float) -> None:
        """t が oldest_kept_t より古いフレームと位置を捨てる。呼び出し側が _lock を持つ。"""
        while self._frames and self._frames[0].t < oldest_kept_t:
            self._frames.popleft()
        for track in self._tracks.values():
            for positions in (track.results, track.jev_boxes):
                for t in list(positions):
                    if t >= oldest_kept_t:
                        break
                    del positions[t]
