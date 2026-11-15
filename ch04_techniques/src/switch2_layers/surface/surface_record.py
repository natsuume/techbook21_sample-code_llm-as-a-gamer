"""surface.jsonl の 1 行 (surface の 1 回の要求の記録) を作る。

行の形:
    {"t_sent", "t_received", "frames": [{"t", "file"}], "scene_id", "uses_common": {"controls": 真偽, "tips": 真偽},
     "middle_generated_t", "action", "blocked_buttons", "deep_requests", "info_labels", "boxes", "scene_changed",
     "wait_s", "error"}
"""

from collections.abc import Sequence
from dataclasses import dataclass

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction, action_to_json
from switch2_layers.surface.surface_types import Box, InfoLabel, SurfaceDocuments


@dataclass(frozen=True)
class SentFrame:
    """surface に送った画像 1 枚の記録。

    t      フレームの時刻
    file   保存を予約した画像の実行ディレクトリーからの相対パス (frames/surface/<tミリ秒9桁>.jpg。
           frame_store.FrameStore.save_surface の戻り値)。null でない file は保存を予約したことを表し、
           画像を書き終えたことは表さない (書き込みに失敗するか、終了時に捨てられて、画像が無いことがある。
           書けた画像は frames.jsonl に行がある)。保存を捨てた (キューが満杯・終了の後) なら None
    """

    t: float
    file: str | None


@dataclass(frozen=True)
class SurfaceRecord:
    """surface の 1 回の要求の記録。

    t_sent               decide を呼んだ時刻
    t_received           decide が戻った (または例外を出した) 時刻
    frames               送った画像 (古い順)
    documents            読んだ文書 (scene_id と uses_common_* を記録に使う)
    middle_generated_t   読んだ系列情報の作成時刻 (middle/state.json の generated_t)。無ければ None
    action               送った操作 (ボタンの制限を通した後)。decide が失敗したら None
    blocked_buttons      ボタンの制限で外したボタン
    deep_requests        deep を呼ぶ要求を出した文書の種類 (deep_types.DOCUMENT_KINDS の順)
    info_labels          情報ラベル
    boxes                box
    scene_changed        場面転換を検知したか
    wait_s               この要求の後に待つ秒数
    error                失敗の説明 (decide の例外、操作の送信の失敗)。無ければ None
    """

    t_sent: float
    t_received: float
    frames: Sequence[SentFrame]
    documents: SurfaceDocuments
    middle_generated_t: float | None
    action: PadAction | None
    blocked_buttons: Sequence[str]
    deep_requests: Sequence[DocumentKind]
    info_labels: Sequence[InfoLabel]
    boxes: Sequence[Box]
    scene_changed: bool
    wait_s: float
    error: str | None


def info_label_to_json(label: InfoLabel) -> dict[str, object]:
    """情報ラベルを {"name", "probability"} にする。"""
    return {"name": label.name, "probability": label.probability}


def box_to_json(box: Box) -> dict[str, object]:
    """box を {"label", "x_min", "y_min", "x_max", "y_max", "confidence", "source"} にする。

    confidence は None なら null。source は出どころの値 ("jev" / "tracker") で、None (不明) なら null。
    """
    return {
        "label": box.label,
        "x_min": box.x_min,
        "y_min": box.y_min,
        "x_max": box.x_max,
        "y_max": box.y_max,
        "confidence": box.confidence,
        "source": None if box.source is None else box.source.value,
    }


def surface_row(record: SurfaceRecord) -> dict[str, object]:
    """surface.jsonl の 1 行の辞書を、モジュールの docstring のキーの順で返す。

    frames は [{"t", "file"}]、scene_id は documents.scene_id、uses_common は
    {"controls": documents.uses_common_controls, "tips": documents.uses_common_tips}、
    action は pad_action.action_to_json(action) (None なら null)、blocked_buttons はリスト、
    deep_requests は種類の値 (文字列) のリスト、info_labels と boxes は info_label_to_json・box_to_json のリスト。
    """
    documents = record.documents
    return {
        "t_sent": record.t_sent,
        "t_received": record.t_received,
        "frames": [{"t": frame.t, "file": frame.file} for frame in record.frames],
        "scene_id": documents.scene_id,
        "uses_common": {
            "controls": documents.uses_common_controls,
            "tips": documents.uses_common_tips,
        },
        "middle_generated_t": record.middle_generated_t,
        "action": None if record.action is None else action_to_json(record.action),
        "blocked_buttons": list(record.blocked_buttons),
        "deep_requests": [kind.value for kind in record.deep_requests],
        "info_labels": [info_label_to_json(label) for label in record.info_labels],
        "boxes": [box_to_json(box) for box in record.boxes],
        "scene_changed": record.scene_changed,
        "wait_s": record.wait_s,
        "error": record.error,
    }
