"""surface のループ。1 回の要求の処理 (SurfaceLoop.step) と、それを繰り返すスレッドの本体 (SurfaceLoop.run)。

1 回の要求の流れ (step):
    1. フレームを選ぶ: 直近 2 秒以内のフレームから最大 5 枚を選び、各画像の左上に t を書き込んで JPEG にする。
       送る画像は frames/surface/ に保存する (同じ t は 1 回だけ)。2 秒以内のフレームが無ければ要求を送らず、
       NO_FRAME_RETRY_S 秒待って選び直す
    2. 文書を読む: middle/state.json から現在の場面と系列情報を読み、その場面の文書を読む
    3. SurfaceDecider.decide を呼ぶ
    4. 出力の操作をボタンの制限に通してから送る (PadOutput.apply)
    5. 1 行を surface.jsonl に書く
    6. deep を呼ぶ要求を deep のスケジューラーに渡す
    7. 待つ (middle/state.json の surface_wait_s を 0.2〜2.0 秒に収めた値)
decide が例外を出したら、操作を中立にして DECIDE_ERROR_WAIT_S 秒待つ。max_errors 回続けたら on_stop で全体を止める。
ブリッジの復旧中は要求を送らず、操作を中立にして BRIDGE_WAIT_S 秒待つ。
テストでは step を 1 回ずつ呼び、待ちは戻り値の wait_s で確かめる。
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from switch2_layers.deep.deep_types import DeepRequestSink
from switch2_layers.frames.frame_buffer import FrameBuffer
from switch2_layers.frames.frame_store import FrameStore
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY
from switch2_layers.frames.surface_frames import SURFACE_IMAGE_WIDTH
from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.surface.surface_types import SurfaceDecider

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


@dataclass(frozen=True)
class SurfaceStepResult:
    """1 回の step の結果。

    outcome   結果の種類
    wait_s    次の step までに待つ秒数
    row       surface.jsonl に書いた行。書かなかったら None
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

    paths           実行ディレクトリー (middle/state.json・notes/・surface.jsonl を使う)
    decider         surface の判断
    frame_buffer    直近のフレーム
    frame_store     送った画像の保存
    pad_output      操作の送信 (ボタンの制限を含む)
    deep_requests   deep を呼ぶ要求の受け口 (run では DeepScheduler)
    run_log         run.jsonl (surface_error を書く)
    clock           実行の経過秒の時計
    bridge_state    ブリッジの状態 ("ok" / "recovering" / "failed") を返す関数
    on_stop         全体を止める関数 (理由の文字列を受け取る)
    """

    paths: RunPaths
    decider: SurfaceDecider
    frame_buffer: FrameBuffer
    frame_store: FrameStore
    pad_output: PadOutput
    deep_requests: DeepRequestSink
    run_log: RunLog
    clock: RunClock
    bridge_state: Callable[[], str]
    on_stop: Callable[[str], None]


def surface_failure_reason(count: int) -> str:
    """decide の失敗が count 回続いて止まるときの理由 `surface failed <count> times in a row` を返す。"""
    raise NotImplementedError


class SurfaceLoop:
    """surface の要求を繰り返す。step は 1 つのスレッドからだけ呼ぶ。"""

    def __init__(self, parts: SurfaceLoopParts, settings: SurfaceLoopSettings) -> None:
        """続けて失敗した回数 0、直近の操作の記録が空の状態で作る。"""
        raise NotImplementedError

    @property
    def consecutive_errors(self) -> int:
        """decide の失敗が続いている回数 (成功で 0 に戻る)。"""
        raise NotImplementedError

    def step(self) -> SurfaceStepResult:
        """1 回の要求を処理し、結果を返す (待ちはしない)。

        1. bridge_state() が BRIDGE_OK でなければ、要求を送らず BRIDGE_NOT_READY (wait_s = BRIDGE_WAIT_S) を返す。
           直前の step でブリッジを使えていたなら (最初の step を含む)、このとき pad_output.neutral() を 1 回呼ぶ
           (使えない間は毎回は呼ばない)
        2. now_t = clock.now()。frames.surface_frames.select_surface_frames(frame_buffer.frames(), now_t) が空なら
           NO_FRAMES (wait_s = NO_FRAME_RETRY_S) を返す
        3. 選んだフレームを build_surface_image(frame, image_width, jpeg_quality) で JPEG にし、
           frame_store.save_surface(t, 画像) で保存して、SurfaceFrame(t, jpeg, format_frame_label(t)) にする
        4. read_surface_context(paths.middle_state) と read_surface_documents(paths.notes_dir, その scene_id) を読む
        5. t_sent = clock.now() で SurfaceInput(t=now_t, frames, documents, timeline, timeline_generated_t,
           直近の操作) を decide に渡し、戻ったら (例外でも) t_received = clock.now()
        6. decide が例外 (Exception) を出したら:
           pad_output.neutral() を呼び、続けて失敗した回数を 1 増やし、run_log に
           surface_error {"error": 説明, "count": 続けて失敗した回数} を書き、surface.jsonl に error の行
           (action は null、blocked_buttons・deep_requests・info_labels・boxes は空、scene_changed は False、
           wait_s は DECIDE_ERROR_WAIT_S) を書く。回数が max_errors に達したら on_stop(surface_failure_reason(回数)) を呼ぶ。
           DECIDE_ERROR (wait_s = DECIDE_ERROR_WAIT_S) を返す
        7. 成功したら、続けて失敗した回数を 0 にし、pad_output.apply(出力の操作) で送る。行の action と blocked_buttons は
           apply が返した FilteredAction の値 (制限と相反する方向を外した後の操作と、制限で外したボタン) にする。
           apply が例外 (OutputStoppedError・送信の失敗) を出したら、行の error にその説明を書き、action と
           blocked_buttons は buttons.filter_action(出力の操作, pad_output.allowed_buttons) の結果にする。送れたら、送った操作を
           直近の操作に足す (RECENT_ACTIONS_COUNT 件まで)。apply の例外は、decide の失敗の回数に数えない
        8. surface.jsonl に 1 行書く (deep_requests は出力の deep_kinds を DOCUMENT_KINDS の順に並べたもの、
           wait_s は SurfaceContext.wait_s)。surface.jsonl に書くのはこのスレッドだけ
        9. deep_kinds の種類ごとに (DOCUMENT_KINDS の順)、deep_requests.request(種類, DeepTrigger.SURFACE, 現在の場面 ID)
           を呼ぶ。request が例外 (Exception) を出したら、外に出さずに run_log に deep_request_error
           {"source": "surface", "kind", "trigger", "scene_id", "error": 説明} を書き、残りの種類の要求を続ける
           (decide の失敗の回数には数えず、ループも止めない)
        SENT (wait_s = SurfaceContext.wait_s) を返す。
        """
        raise NotImplementedError

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで step を繰り返し、各 step の後に stop.wait(wait_s) で待つ (スレッドの本体)。

        step の想定外の例外 (Exception) は外に出さず、on_stop(説明を含む理由) を呼んでから戻る。
        """
        raise NotImplementedError
