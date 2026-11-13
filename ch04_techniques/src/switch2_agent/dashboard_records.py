"""ダッシュボードが読む記録 (起動引数 --record-dashboard) の、行の組み立てと型 (I/O を持たない純粋な関数)。

フレームの判定の記録 frames.jsonl、送ったレポートの記録 pad.jsonl、run.jsonl の record_error と record_end の
行を、辞書として組み立てる。ファイルへの書き込みとスレッドは dashboard_recorder.DashboardRecorder が行う。
時刻は、どの記録も time.monotonic() の値 (秒) を mono のキーで書く。run.jsonl の start の origin_mono
(Pipeline.started_at) を引けば、requests.jsonl と同じ起点からの経過秒になる。

frames.jsonl の 1 行 (判定に入れたフレーム 1 枚につき 1 行。build_frame_record):
    {"mono": 12345.678901, "sharpness": 4528.3, "diff": 0.09, "phase": "rising", "peak_mono": 12340.1,
     "peak_sharpness": 5199.4, "ref": 2599.7, "event": null, "thumb": "thumbs/12345678901.jpg"}
pad.jsonl の 1 行 (レポートを送ろうとした 1 回につき 1 行。build_pad_record):
    {"mono": 12345.678901, "source": "apply", "state": "A+LS:0.8,0", "ok": true, "error": null}
"""

from dataclasses import dataclass
from typing import Literal

from switch2_remote.controller_state import PadState

from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.meaningful_frames import (
    SharpnessValleyDetector,
    ValleyPhase,
)

FRAMES_RECORD_FILE_NAME = "frames.jsonl"
PAD_RECORD_FILE_NAME = "pad.jsonl"
# 縮小画像を書くディレクトリー (実行のディレクトリーの下)
THUMBS_DIR_NAME = "thumbs"
# 縮小画像の大きさ (幅, 高さ) と JPEG の品質
THUMB_WIDTH, THUMB_HEIGHT = 320, 180
THUMB_JPEG_QUALITY = 70

# pad.jsonl の source。レポートを送ろうとした PadOutput のメソッド
#   apply         判断の操作の反映 (PadOutput.apply)
#   tap_start     待たせた短押しの開始 (PadOutput.start_due_taps)
#   tap_release   短押しの解除 (PadOutput.release_due_taps)
#   neutral       中立に戻す (PadOutput.neutral)
#   stop_output   出力を止めて中立に戻す (PadOutput.stop_output)
PadReportSource = Literal["apply", "tap_start", "tap_release", "neutral", "stop_output"]


def thumb_file_name(mono: float) -> str:
    """時刻 mono (time.monotonic の値) のフレームの縮小画像のファイル名を、実行のディレクトリーからの相対パスで返す。

    `thumbs/<mono をマイクロ秒に丸めた整数>.jpg` (round(mono * 1_000_000)。0 埋めしない)。
    例: mono=12345.678901 なら thumbs/12345678901.jpg、mono=1.5 なら thumbs/1500000.jpg。
    frames.jsonl の thumb と requests.jsonl の images の thumb は、どちらもこの名前を使う。
    """
    raise NotImplementedError


@dataclass(frozen=True)
class DetectorSnapshot:
    """SharpnessValleyDetector の、ある時点の状態 (同名の属性の参照。フレームはコピーしない)。

    phase       "rising" か "falling"
    peak        今の区間で鮮明度が最大のフレーム。最初のフレームを入れるまでは None
    valley      下降中に鮮明度が最小のフレーム。下降に入るまでは None
    last_kept   直前に残したフレーム。まだ無ければ None
    """

    phase: ValleyPhase
    peak: TimedFrame | None
    valley: TimedFrame | None
    last_kept: TimedFrame | None


def snapshot_detector(detector: SharpnessValleyDetector) -> DetectorSnapshot:
    """detector の phase・peak・valley・last_kept を、そのまま DetectorSnapshot にして返す。

    フレームはコピーしない。detector は属性を置き換えて状態を変えるため、後で detector.feed を呼んでも、
    返した DetectorSnapshot の値は変わらない。
    """
    raise NotImplementedError


@dataclass(frozen=True)
class FrameStep:
    """判定に入れたフレーム 1 枚分の、frames の記録のキューに入れる値。

    frame          判定に入れたフレーム
    before         detector.feed(frame) の前の判定の状態
    after          detector.feed(frame) の後の判定の状態
    kept           detector.feed(frame) の戻り値 (残したフレーム)。残さなければ None
    valley_ratio   判定の valley_ratio (SharpnessValleyDetector.valley_ratio)
    after_drop     直前のフレームの記録を捨てたか (キューが満杯で入れられなかったか)。キューに入れる側
                   (DashboardRecorder.record_frame) が付ける。True なら、書き出すときの diff を null にする
    """

    frame: TimedFrame
    before: DetectorSnapshot
    after: DetectorSnapshot
    kept: TimedFrame | None
    valley_ratio: float
    after_drop: bool


def detector_event(step: FrameStep) -> dict | None:
    """step のフレームで判定に起きた出来事を、frames.jsonl の event の辞書にして返す。出来事が無ければ None。

    - before.phase が "rising" で after.phase が "falling" (谷に向かい始めた): {"kind": "fall"}
    - before.phase が "falling" で after.phase が "rising" (谷を確定した):
          {"kind": "close", "candidate_mono": before.peak.time, "candidate_sharpness": before.peak.sharpness,
           "kept": step.kept is not None,
           "similar_diff": image_metrics.mean_abs_diff(before.peak.gray_small, before.last_kept.gray_small)
                           (before.last_kept が None なら None)}
          candidate は、確定した区間の山 (残すかを判定した候補)。similar_diff は、直前に残したフレームとの差分
    - それ以外 (最初のフレーム、山の更新、谷の更新、谷にならない浅い揺れ、まだ回復していない谷): None
    """
    raise NotImplementedError


def reference_sharpness(after: DetectorSnapshot, valley_ratio: float) -> float:
    """frames.jsonl の ref (判定が次に比べる鮮明度の値) を返す。

    after.phase が "rising" なら after.peak.sharpness * valley_ratio (これ以下まで下がると谷に向かい始める)、
    "falling" なら after.valley.sharpness / valley_ratio (これ以上まで回復すると谷を確定する)。
    after は、1 枚以上のフレームを入れた後の状態 (peak がある。falling なら valley もある) とする。
    """
    raise NotImplementedError


def build_frame_record(step: FrameStep, diff: float | None) -> dict:
    """step を、frames.jsonl の 1 行の辞書にする。

    キーはこの順:
        mono             step.frame.time
        sharpness        step.frame.sharpness
        diff             diff (直前に書き出したフレームとの差分。求めない (最初のフレームか、直前の記録を
                         捨てた) なら None。求めるのは DashboardRecorder)
        phase            step.after.phase
        peak_mono        step.after.peak.time
        peak_sharpness   step.after.peak.sharpness
        ref              reference_sharpness(step.after, step.valley_ratio)
        event            detector_event(step)
        thumb            thumb_file_name(step.frame.time)
    """
    raise NotImplementedError


@dataclass(frozen=True)
class PadReport:
    """PadOutput がレポートを送ろうとした 1 回分の記録。

    mono     送信が戻った後の時刻 (PadOutput の clock の値。失敗したら例外が戻った後の時刻)
    source   レポートを送ろうとしたメソッド (PadReportSource)
    state    送った押下状態 (pad_output.compose_report_state の結果)
    ok       送信が成功したか
    error    失敗したときの説明 (switch2_remote.error_message.describe_error)。成功したら None
    """

    mono: float
    source: PadReportSource
    state: PadState
    ok: bool
    error: str | None


def build_pad_record(report: PadReport) -> dict:
    """report を、pad.jsonl の 1 行の辞書 {"mono", "source", "state", "ok", "error"} にする (キーはこの順)。

    state は controller_state.format_state(report.state) の表記 (例: A+LS:0.8,0。何も押していなければ none)。
    ほかのキーは report の同名の値。
    """
    raise NotImplementedError


def build_record_error_event(kind: str, error: str) -> dict:
    """記録の書き込みの失敗を run.jsonl に書く 1 件の辞書を返す。

    {"event": "record_error", "kind": kind ("frames" か "pad"), "error": error}
    """
    raise NotImplementedError


def build_record_end_event(written: dict[str, int], dropped: dict[str, int]) -> dict:
    """終了時に run.jsonl に書く 1 件の辞書を返す。

    {"event": "record_end", "written": written, "dropped": dropped}。written と dropped は、種類 ("frames" と
    "pad") → 書いた件数・捨てた件数。種類ごとに written + dropped が記録しようとした件数になる。
    """
    raise NotImplementedError
