"""surface のループ。1 回の要求の処理 (SurfaceLoop.step) と、それを繰り返すスレッドの本体 (SurfaceLoop.run)。

1 回の要求の流れ (step):
    1. フレームを選ぶ: 直近 2 秒以内のフレームから最大 5 枚を選ぶ。2 秒以内のフレームが無ければ要求を送らず、
       NO_FRAME_RETRY_S 秒待って選び直す
    2. 文書を読む: middle/state.json から現在の場面と系列情報を読み、その場面の文書を読む。
       画面の停滞の計測の値 (progress/state.json) も読む
    3. 画像を作る: 各画像の左上に t を書き込み、追えている box の枠と名前も書き込んで JPEG にする。どの box を
       書き込むかと枠の色は、その要求の書き込み方 (frames.surface_frames.BoxDrawingPlan) で決める (その要求で
       Box を問い直す対象は、最新のフレームの画像に書き込まない)。
       送る画像は frames/surface/ に保存する (t と、書き込んだ box の内容の組ごとに 1 回だけ)
    4. advisor 判定で問う文書の種類ごとに、現在の場面の文書の deep の呼び出しの状況を deep のスケジューラーから読んで
       入力に入れ、SurfaceDecider.decide を呼ぶ。Jev を呼ぶ実装は、Jev に送る直前に、送る要求を surface_requests.jsonl に
       1 行書く (書き込み先 SurfaceRequestWriter をこのループが渡す)
    5. 出力の操作をボタンの制限に通してから送る (PadOutput.apply)
    6. 1 行を surface.jsonl に書く
    7. deep を呼ぶ要求を deep のスケジューラーに渡す
    8. 待つ (middle/state.json の surface_wait_s を 0.2〜2.0 秒に収めた値)
decide が例外を出したら、操作を中立にして DECIDE_ERROR_WAIT_S 秒待つ。max_errors 回続けたら on_stop で全体を止める。
ブリッジの復旧中は要求を送らず、操作を中立にして BRIDGE_WAIT_S 秒待つ。
止める合図は、decide が戻ったとき (例外を含む)、操作の送信 (5 の apply と、decide の失敗の後の neutral) が戻ったとき
(成功・失敗とも)、surface.jsonl の行を書く直前、deep を要求する直前 (種類ごと) に確かめる。立っていたら、その回の
残りの送信・記録 (surface.jsonl・run.jsonl)・deep の要求をせずにループを抜ける (止めた後に記録を増やさない)。
surface.jsonl と surface_requests.jsonl の追記の失敗は、どちらも step の外に例外として出し、run が全体を止める。
テストでは step を 1 回ずつ呼び、待ちは戻り値の wait_s で確かめる。
"""

import logging
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from switch2_layers.buttons import filter_action
from switch2_layers.deep.deep_scheduler import DeepDocumentStatus
from switch2_layers.deep.deep_types import (
    DOCUMENT_KINDS,
    DeepRequestSink,
    DeepTrigger,
    DocumentKind,
)
from switch2_layers.error_text import describe_exception
from switch2_layers.file_io import append_json_line
from switch2_layers.frames.frame_buffer import CapturedFrame, FrameBuffer
from switch2_layers.frames.frame_store import FrameStore
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY
from switch2_layers.frames.surface_frames import (
    SURFACE_IMAGE_WIDTH,
    BoxDrawingPlan,
    build_surface_image,
    drawn_boxes_key,
    format_frame_label,
    select_boxes_to_draw,
    select_surface_frames,
)
from switch2_layers.hardware.pad_output import OutputStoppedError, PadOutput
from switch2_layers.notes.surface_documents import read_surface_documents
from switch2_layers.progress.progress_state import read_progress_state
from switch2_layers.records.run_log import (
    EVENT_DEEP_REQUEST_ERROR,
    EVENT_SURFACE_ERROR,
    RunLog,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.surface.middle_state_reader import (
    SurfaceContext,
    read_surface_context,
)
from switch2_layers.surface.surface_questions import ADVISOR_KINDS
from switch2_layers.surface.surface_record import (
    SentFrame,
    SurfaceRecord,
    surface_request_row,
    surface_row,
)
from switch2_layers.surface.surface_types import (
    Box,
    DeepStatusReader,
    RecentAction,
    SurfaceDecider,
    SurfaceDocuments,
    SurfaceFrame,
    SurfaceInput,
    SurfaceOutput,
    SurfaceRequest,
)

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

NO_FRAME_RETRY_S = 0.2  # 2 秒以内のフレームが無いときに、選び直すまで待つ秒数
DECIDE_ERROR_WAIT_S = 1.0  # decide が例外を出した後に待つ秒数
BRIDGE_WAIT_S = 0.2  # ブリッジが使えない (復旧中・失敗) ときに待つ秒数
# SurfaceInput.recent_actions に入れる、直近に送った操作の件数
RECENT_ACTIONS_COUNT = 10
# bridge_state が返す、ブリッジを使える状態 (BridgeSupervisor.state と同じ値)
BRIDGE_OK = "ok"


class SurfaceStepOutcome(StrEnum):
    """1 回の step の結果の種類。"""

    SENT = "sent"  # decide が成功した (操作の送信に失敗した場合も含む)。surface.jsonl に 1 行書いた
    NO_FRAMES = (
        "no_frames"  # 2 秒以内のフレームが無く、要求を送らなかった。行は書かない
    )
    BRIDGE_NOT_READY = (
        "bridge_not_ready"  # ブリッジが使えず、要求を送らなかった。行は書かない
    )
    DECIDE_ERROR = (
        "decide_error"  # decide が例外を出した。surface.jsonl に error の行を書いた
    )
    # 止める合図を見て途中で抜けた (SurfaceLoop.step)。それより後の送信・記録・deep の要求をしていない
    STOPPED = "stopped"


@dataclass(frozen=True)
class SurfaceStepResult:
    """1 回の step の結果。

    outcome   結果の種類
    wait_s    次の step までに待つ秒数
    row       surface.jsonl に書いた行。書かなかったら None。STOPPED では、行を書いた後に抜けた場合も None
    """

    outcome: SurfaceStepOutcome
    wait_s: float
    row: dict[str, object] | None


@dataclass(frozen=True)
class SurfaceLoopSettings:
    """surface のループの設定。

    max_errors     decide の失敗がこの回数続いたら on_stop を呼ぶ (--max-surface-errors)
    image_width    送る画像の幅の上限
    jpeg_quality   送る画像の JPEG の品質
    """

    max_errors: int
    image_width: int = SURFACE_IMAGE_WIDTH
    jpeg_quality: int = DEFAULT_JPEG_QUALITY


@dataclass(frozen=True)
class SurfaceLoopParts:
    """surface のループが使う部品。

    paths           実行ディレクトリー (middle/state.json・notes/・surface.jsonl・surface_requests.jsonl を使う)
    decider         surface の判断
    frame_buffer    直近のフレーム
    frame_store     送った画像の保存
    pad_output      操作の送信 (ボタンの制限を含む)
    deep_requests   deep を呼ぶ要求の受け口 (run では DeepScheduler)
    deep_status     deep の文書ごとの呼び出しの状況の読み取り (run では deep_requests と同じ DeepScheduler)。step が要求ごとに、
                    advisor 判定で問う種類の状況を読む
    run_log         run.jsonl (surface_error を書く)
    clock           実行の経過秒の時計
    bridge_state    ブリッジの状態 ("ok" / "recovering" / "failed") を返す関数
    on_stop         全体を止める関数 (理由の文字列を受け取る)
    boxes_at        フレームの時刻 t を受け取り、その画像に書き込む box を返す関数 (run の surface が jev なら
                    surface.box_tracking_state.BoxTrackingState.boxes_at)。None (既定) なら box を書き込まない
    plan_boxes      場面 ID を受け取り、その要求の box の書き込み方 (frames.surface_frames.BoxDrawingPlan) を返す関数
                    (run の surface が jev なら surface.jev_surface.JevSurfaceDecider.plan_boxes)。step が要求ごとに
                    1 回、画像を作る前に呼ぶ。None (既定) なら、boxes_at の box をすべて書き込み、色は box の並びで決める
    """

    paths: RunPaths
    decider: SurfaceDecider
    frame_buffer: FrameBuffer
    frame_store: FrameStore
    pad_output: PadOutput
    deep_requests: DeepRequestSink
    deep_status: DeepStatusReader
    run_log: RunLog
    clock: RunClock
    bridge_state: Callable[[], str]
    on_stop: Callable[[str], None]
    boxes_at: Callable[[float], Sequence[Box]] | None = None
    plan_boxes: Callable[[str | None], BoxDrawingPlan] | None = None


def surface_failure_reason(count: int) -> str:
    """decide の失敗が count 回続いて止まるときの理由 `surface failed <count> times in a row` を返す。"""
    return f"surface failed {count} times in a row"


class SurfaceRequestWriter:
    """surface_requests.jsonl の書き込み先。step が要求ごとに作り、SurfaceInput.request_sink として decide に渡す。

    path            surface_requests.jsonl のパス (RunPaths.surface_requests_log)
    clock           実行の経過秒の時計 (行の t_sent を読む)
    request_index   書いた要求の番号。まだ書いていなければ None
    write_error     追記で出た例外。出ていなければ None
    """

    def __init__(self, path: Path, clock: RunClock) -> None:
        """まだ何も書いていない状態 (request_index と write_error が None) で作る。"""
        self.path = path
        self.clock = clock
        self.request_index: int | None = None
        self.write_error: Exception | None = None

    def __call__(self, request: SurfaceRequest) -> None:
        """surface_record.surface_request_row(request, clock.now()) を path に追記し (file_io.append_json_line)、
        request_index を request.request_index にする。

        追記が例外 (Exception) を出したら、write_error にその例外を持ってから、そのまま出す (request_index は変えない)。
        """
        row = surface_request_row(request, self.clock.now())
        try:
            append_json_line(self.path, row)
        except Exception as error:
            self.write_error = error
            raise
        self.request_index = request.request_index


class SurfaceLoop:
    """surface の要求を繰り返す。step は 1 つのスレッドからだけ呼ぶ。"""

    def __init__(self, parts: SurfaceLoopParts, settings: SurfaceLoopSettings) -> None:
        """続けて失敗した回数 0、直近の操作の記録が空の状態で作る。"""
        self.parts = parts
        self.settings = settings
        self._consecutive_errors = 0
        self._recent_actions: deque[RecentAction] = deque(maxlen=RECENT_ACTIONS_COUNT)
        # 直前の step でブリッジを使えていたか (最初の step の前は使えていたものとする)
        self._bridge_was_ok = True

    @property
    def consecutive_errors(self) -> int:
        """decide の失敗が続いている回数 (成功で 0 に戻る)。"""
        return self._consecutive_errors

    def step(self, stop: threading.Event | None = None) -> SurfaceStepResult:
        """1 回の要求を処理し、結果を返す (待ちはしない)。

        stop は止める合図 (run が渡す)。None なら合図を確かめない。

        1. bridge_state() が BRIDGE_OK でなければ、要求を送らず BRIDGE_NOT_READY (wait_s = BRIDGE_WAIT_S) を返す。
           直前の step でブリッジを使えていたなら (最初の step を含む)、このとき pad_output.neutral() を 1 回呼ぶ
           (使えない間は毎回は呼ばない)
        2. now_t = clock.now()。frames.surface_frames.select_surface_frames(frame_buffer.frames(), now_t) が空なら
           NO_FRAMES (wait_s = NO_FRAME_RETRY_S) を返す
        3. read_surface_context(paths.middle_state) と read_surface_documents(paths.notes_dir, その scene_id) と
           progress.progress_state.read_progress_state(paths.progress_state) (無い・読めなければ None) を読む
        4. 書き込み方 plan = plan_boxes(documents.scene_id) を 1 回求める (plan_boxes が None なら plan も None)。
           plan_boxes が例外 (Exception) を出したら、logging で警告を書き、この要求のどの画像にも box を書き込まない
           (decide の失敗には数えない)。
           選んだフレームごとに、boxes_at(frame.t) (boxes_at が None なら空) から
           frames.surface_frames.select_boxes_to_draw(boxes, plan, 最新のフレームか) で書き込む box を選び、
           build_surface_image(frame, image_width, jpeg_quality, その box, plan.target_numbers (plan が None なら None))
           で JPEG にする。frame_store.save_surface(t, 画像, drawn_boxes_key(その box, 同じ target_numbers)) で保存して、
           SurfaceFrame(t, jpeg, format_frame_label(t), save_surface の戻り値) にする。boxes_at が例外 (Exception) を
           出したら、logging で警告を書き、その画像には box を書き込まない (decide の失敗には数えない)。
           保存する画像と送る画像は同じもの
        5. advisor の状況 statuses を読む: surface_questions.ADVISOR_KINDS の種類ごとに (この順で)
           deep_status.document_status(種類, 現在の場面 ID (SurfaceContext.scene_id)) を呼び、{種類: 状況} にする。
           document_status が例外 (Exception) を出した種類は None にし (state の文は「状況を取得できない」)、logging で
           警告を書いて残りの種類を続ける (decide の失敗には数えず、run.jsonl にも書かない)。
           writer = SurfaceRequestWriter(paths.surface_requests_log, clock) を作る。t_sent = clock.now() で
           SurfaceInput(t=now_t, frames, documents, timeline, timeline_generated_t, 直近の操作, advisor_statuses=statuses,
           request_sink=writer, progress=3 で読んだ値) を decide に渡し、戻ったら (例外でも) t_received = clock.now()。
           ここで stop を確かめる。
           続けて、writer.write_error があれば (surface_requests.jsonl の追記の失敗)、decide の成否に依らず、その例外を
           step の外にそのまま出す (surface.jsonl の追記の失敗と同じ扱い。操作の送信・失敗の回数の変更・
           run.jsonl と surface.jsonl への書き込み・deep の要求をしない)
        6. decide が例外 (Exception) を出したら:
           pad_output.neutral() を呼び、戻ったら stop を確かめる。続けて失敗した回数を 1 増やし、run_log に
           surface_error {"error": 説明, "count": 続けて失敗した回数} を書き、surface.jsonl に error の行
           (action は null、blocked_buttons・deep_requests・info_labels・boxes は空、scene_changed は False、
           wait_s は DECIDE_ERROR_WAIT_S、request は writer.request_index (要求を送る前に失敗して書いていなければ
           null)、answers と usage は null) を書く (書く直前に stop を確かめる)。回数が max_errors に達したら
           on_stop(surface_failure_reason(回数)) を呼ぶ。DECIDE_ERROR (wait_s = DECIDE_ERROR_WAIT_S) を返す
        7. 成功したら、続けて失敗した回数を 0 にし、pad_output.apply(出力の操作) で送る。apply が戻ったら
           (例外でも) stop を確かめる。行の action と blocked_buttons は
           apply が返した FilteredAction の値 (制限と相反する方向を外した後の操作と、制限で外したボタン) にする。
           apply が例外 (OutputStoppedError・送信の失敗) を出したら、行の error にその説明を書き、action と
           blocked_buttons は buttons.filter_action(出力の操作, pad_output.allowed_buttons) の結果にする。送れたら、送った操作を
           直近の操作に足す (RECENT_ACTIONS_COUNT 件まで)。apply の例外は、decide の失敗の回数に数えない
        8. surface.jsonl に 1 行書く (書く直前に stop を確かめる。deep_requests は出力の deep_kinds を DOCUMENT_KINDS の順に
           並べたもの、wait_s は SurfaceContext.wait_s、request は出力の exchange.request.request_index、answers は
           exchange.answers (usage はその usage)。exchange が None (フェイク) なら request・answers・usage は null)。
           surface.jsonl と surface_requests.jsonl に書くのはこのスレッドだけ
        9. deep_kinds の種類ごとに (DOCUMENT_KINDS の順)、request の直前に stop を確かめてから、
           deep_requests.request(種類, DeepTrigger.SURFACE, 現在の場面 ID) を呼ぶ。request が例外 (Exception) を出したら、
           外に出さずに run_log に deep_request_error {"source": "surface", "kind", "trigger", "scene_id", "error": 説明} を書き、
           残りの種類の要求を続ける (decide の失敗の回数には数えず、ループも止めない)
        SENT (wait_s = SurfaceContext.wait_s) を返す。

        stop を確かめたときに立っていたら、そこで STOPPED (wait_s = 0.0、row は None) を返し、それより後の手順
        (操作の送信・失敗の回数の変更・run.jsonl と surface.jsonl への書き込み・on_stop・deep の要求) をしない。
        それより前にしたことは戻さない。抜ける位置ごとの結果:
            - decide が戻ったとき: 操作を送らず (neutral も呼ばない)、何も書かず、失敗の回数も変えず、deep も呼ばない
            - neutral が戻ったとき (6): 失敗の回数を変えず、surface_error も行も書かない
            - apply が戻ったとき (7。OutputStoppedError を含む): 行を書かず、deep も呼ばない
            - 行を書く直前 (6・8): 行を書かず、deep も呼ばない (6 では、run.jsonl の surface_error は書いた後で、
              on_stop は呼ばない)
            - deep を要求する直前 (9): その種類と残りの種類の要求をしない (行は書いた後)
        """
        parts = self.parts
        if parts.bridge_state() != BRIDGE_OK:
            if self._bridge_was_ok:
                parts.pad_output.neutral()
            self._bridge_was_ok = False
            return SurfaceStepResult(
                SurfaceStepOutcome.BRIDGE_NOT_READY, BRIDGE_WAIT_S, None
            )
        self._bridge_was_ok = True
        now_t = parts.clock.now()
        selected = select_surface_frames(parts.frame_buffer.frames(), now_t)
        if not selected:
            return SurfaceStepResult(
                SurfaceStepOutcome.NO_FRAMES, NO_FRAME_RETRY_S, None
            )
        context = read_surface_context(parts.paths.middle_state)
        documents = read_surface_documents(parts.paths.notes_dir, context.scene_id)
        frames, sent_frames = self._prepare_frames(
            selected, self._box_plan(documents.scene_id)
        )
        advisor_statuses = self._read_advisor_statuses(context.scene_id)
        writer = SurfaceRequestWriter(parts.paths.surface_requests_log, parts.clock)
        surface_input = SurfaceInput(
            t=now_t,
            frames=frames,
            documents=documents,
            timeline=context.timeline,
            timeline_generated_t=context.generated_t,
            recent_actions=tuple(self._recent_actions),
            advisor_statuses=advisor_statuses,
            request_sink=writer,
            progress=read_progress_state(parts.paths.progress_state),
        )
        t_sent = parts.clock.now()
        try:
            output = parts.decider.decide(surface_input)
        except Exception as error:
            t_received = parts.clock.now()
            if _is_stopped(stop):
                return _stopped_result()
            _raise_write_error(writer)
            logger.debug("surface decide failed", exc_info=True)
            return self._handle_decide_error(
                error,
                t_sent,
                t_received,
                sent_frames,
                documents,
                context,
                writer.request_index,
                stop,
            )
        t_received = parts.clock.now()
        if _is_stopped(stop):
            return _stopped_result()
        _raise_write_error(writer)
        return self._handle_output(
            output, t_sent, t_received, sent_frames, documents, context, stop
        )

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで step を繰り返し、各 step の後に stop.wait(wait_s) で待つ (スレッドの本体)。

        step の想定外の例外 (Exception) は外に出さず、on_stop(説明を含む理由) を呼んでから戻る。
        step が STOPPED を返したら (止める合図を見て途中で抜けた)、待たずに戻る。
        """
        while not stop.is_set():
            try:
                result = self.step(stop)
            except Exception as error:
                logger.warning("surface loop failed", exc_info=True)
                self.parts.on_stop(f"surface loop failed: {describe_exception(error)}")
                return
            if result.outcome is SurfaceStepOutcome.STOPPED:
                return
            stop.wait(result.wait_s)

    def _box_plan(self, scene_id: str | None) -> BoxDrawingPlan | None:
        """この要求の box の書き込み方を plan_boxes で求める (step の 4)。

        plan_boxes が None なら None。例外を出したら警告を書き、box を書き込まない書き込み方 (対象が空) を返す。
        """
        plan_boxes = self.parts.plan_boxes
        if plan_boxes is None:
            return None
        try:
            return plan_boxes(scene_id)
        except Exception:
            logger.warning("planning the surface boxes failed", exc_info=True)
            return BoxDrawingPlan(target_numbers={})

    def _read_advisor_statuses(
        self, scene_id: str | None
    ) -> dict[DocumentKind, DeepDocumentStatus | None]:
        """advisor 判定で問う種類ごとの deep の呼び出しの状況を、ADVISOR_KINDS の順に読む (step の 5)。

        document_status が例外を出した種類は None にし、警告を書いて残りの種類を続ける。
        """
        statuses: dict[DocumentKind, DeepDocumentStatus | None] = {}
        for kind in ADVISOR_KINDS:
            try:
                statuses[kind] = self.parts.deep_status.document_status(kind, scene_id)
            except Exception:
                logger.warning(
                    "reading the deep status of %s failed", kind.value, exc_info=True
                )
                statuses[kind] = None
        return statuses

    def _boxes_at(self, t: float) -> Sequence[Box]:
        """時刻 t のフレームの box を boxes_at で求める (step の 4)。

        boxes_at が None なら空。例外を出したら警告を書き、空を返す。
        """
        boxes_at = self.parts.boxes_at
        if boxes_at is None:
            return ()
        try:
            return boxes_at(t)
        except Exception:
            logger.warning("getting the surface boxes at t=%s failed", t, exc_info=True)
            return ()

    def _prepare_frames(
        self, selected: Sequence[CapturedFrame], plan: BoxDrawingPlan | None
    ) -> tuple[tuple[SurfaceFrame, ...], tuple[SentFrame, ...]]:
        """選んだフレームを、plan のとおりに box を書き込んだ JPEG にして保存を予約し、(送る画像, 記録に書く画像) を
        返す (step の 4)。"""
        frames: list[SurfaceFrame] = []
        sent_frames: list[SentFrame] = []
        latest_t = selected[-1].t
        target_numbers = None if plan is None else plan.target_numbers
        for frame in selected:
            boxes = select_boxes_to_draw(
                self._boxes_at(frame.t), plan, frame.t == latest_t
            )
            image = build_surface_image(
                frame,
                self.settings.image_width,
                self.settings.jpeg_quality,
                boxes,
                target_numbers,
            )
            file = self.parts.frame_store.save_surface(
                frame.t, image, drawn_boxes_key(boxes, target_numbers)
            )
            frames.append(
                SurfaceFrame(frame.t, image.jpeg, format_frame_label(frame.t), file)
            )
            sent_frames.append(SentFrame(frame.t, file))
        return tuple(frames), tuple(sent_frames)

    def _handle_decide_error(
        self,
        error: Exception,
        t_sent: float,
        t_received: float,
        sent_frames: Sequence[SentFrame],
        documents: SurfaceDocuments,
        context: SurfaceContext,
        request_index: int | None,
        stop: threading.Event | None,
    ) -> SurfaceStepResult:
        """decide の例外を扱う (step の 6)。

        request_index は surface_requests.jsonl に書いた要求の番号 (送る前に失敗して書いていなければ None)。
        neutral が戻った後と、surface.jsonl の行を書く直前に stop を確かめ、立っていれば STOPPED を返す。
        """
        parts = self.parts
        parts.pad_output.neutral()
        # 中立のレポートの送信 (ブリッジの応答待ち) の間に全体が止められたら、失敗を数えず何も書かない
        if _is_stopped(stop):
            return _stopped_result()
        self._consecutive_errors += 1
        description = describe_exception(error)
        parts.run_log.write(
            EVENT_SURFACE_ERROR, error=description, count=self._consecutive_errors
        )
        record = SurfaceRecord(
            t_sent=t_sent,
            t_received=t_received,
            frames=sent_frames,
            documents=documents,
            middle_generated_t=context.generated_t,
            action=None,
            blocked_buttons=(),
            deep_requests=(),
            info_labels=(),
            boxes=(),
            scene_changed=False,
            wait_s=DECIDE_ERROR_WAIT_S,
            error=description,
            request=request_index,
            answers=None,
        )
        row = surface_row(record)
        if _is_stopped(stop):
            return _stopped_result()
        append_json_line(parts.paths.surface_log, row)
        if self._consecutive_errors >= self.settings.max_errors:
            parts.on_stop(surface_failure_reason(self._consecutive_errors))
        return SurfaceStepResult(
            SurfaceStepOutcome.DECIDE_ERROR, DECIDE_ERROR_WAIT_S, row
        )

    def _handle_output(
        self,
        output: SurfaceOutput,
        t_sent: float,
        t_received: float,
        sent_frames: Sequence[SentFrame],
        documents: SurfaceDocuments,
        context: SurfaceContext,
        stop: threading.Event | None,
    ) -> SurfaceStepResult:
        """decide の出力を送り、記録して、deep を要求する (step の 7〜9)。

        apply が戻った後 (例外でも)、surface.jsonl の行を書く直前、deep を要求する直前 (種類ごと) に stop を確かめ、
        立っていれば STOPPED を返す。
        """
        parts = self.parts
        self._consecutive_errors = 0
        error_text: str | None = None
        try:
            filtered = parts.pad_output.apply(output.action)
        except OutputStoppedError as error:
            logger.debug("the pad output was stopped", exc_info=True)
            error_text = describe_exception(error)
            filtered = filter_action(output.action, parts.pad_output.allowed_buttons)
        except Exception as error:
            # 送信の失敗は行の error に書く (decide の失敗には数えない)
            logger.debug("sending the surface action failed", exc_info=True)
            error_text = describe_exception(error)
            filtered = filter_action(output.action, parts.pad_output.allowed_buttons)
        else:
            self._recent_actions.append(RecentAction(t_received, filtered.action))
        # 操作の送信 (ブリッジの応答待ち) の間に全体が止められたら、止めた後に記録を増やさない
        if _is_stopped(stop):
            return _stopped_result()
        deep_kinds = [kind for kind in DOCUMENT_KINDS if kind in output.deep_kinds]
        exchange = output.exchange
        record = SurfaceRecord(
            t_sent=t_sent,
            t_received=t_received,
            frames=sent_frames,
            documents=documents,
            middle_generated_t=context.generated_t,
            action=filtered.action,
            blocked_buttons=filtered.blocked_buttons,
            deep_requests=deep_kinds,
            info_labels=output.info_labels,
            boxes=output.boxes,
            scene_changed=output.scene_changed,
            wait_s=context.wait_s,
            error=error_text,
            request=None if exchange is None else exchange.request.request_index,
            answers=None if exchange is None else exchange.answers,
        )
        row = surface_row(record)
        if _is_stopped(stop):
            return _stopped_result()
        append_json_line(parts.paths.surface_log, row)
        if not self._request_deep(deep_kinds, context.scene_id, stop):
            return _stopped_result()
        return SurfaceStepResult(SurfaceStepOutcome.SENT, context.wait_s, row)

    def _request_deep(
        self,
        kinds: Sequence[DocumentKind],
        scene_id: str | None,
        stop: threading.Event | None,
    ) -> bool:
        """deep を要求する (step の 9)。受け口の例外は deep_request_error に書き、残りの種類を続ける。

        要求の直前に stop を確かめ、立っていたらその種類と残りの種類を要求せずに False を返す。
        すべての種類を要求し終えたら True を返す。
        """
        for kind in kinds:
            if _is_stopped(stop):
                return False
            try:
                self.parts.deep_requests.request(kind, DeepTrigger.SURFACE, scene_id)
            except Exception as error:
                logger.debug("surface deep request failed", exc_info=True)
                self.parts.run_log.write(
                    EVENT_DEEP_REQUEST_ERROR,
                    source="surface",
                    kind=kind.value,
                    trigger=DeepTrigger.SURFACE.value,
                    scene_id=scene_id,
                    error=describe_exception(error),
                )
        return True


def _raise_write_error(writer: SurfaceRequestWriter) -> None:
    """writer の surface_requests.jsonl への追記で例外が出ていれば、その例外を出す (SurfaceLoop.step の 5)。"""
    if writer.write_error is not None:
        raise writer.write_error


def _is_stopped(stop: threading.Event | None) -> bool:
    """止める合図 stop が立っていれば True。stop が None (合図を確かめない) なら False。"""
    return stop is not None and stop.is_set()


def _stopped_result() -> SurfaceStepResult:
    """止める合図で抜けるときの結果を返す。"""
    return SurfaceStepResult(SurfaceStepOutcome.STOPPED, 0.0, None)
