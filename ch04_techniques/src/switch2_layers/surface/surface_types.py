"""surface 層の型: SurfaceDecider の入力 (SurfaceInput) と出力 (SurfaceOutput)、その部品と Protocol。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction


@dataclass(frozen=True)
class SurfaceFrame:
    """surface に送る画像 1 枚。

    t       フレームの時刻 (実行の経過秒)
    jpeg    左上に t を書き込んだ JPEG のバイト列 (frames.surface_frames.build_surface_image)
    label   画像のラベル (frames.surface_frames.format_frame_label(t))
    """

    t: float
    jpeg: bytes
    label: str


@dataclass(frozen=True)
class SurfaceDocuments:
    """surface が読む文書 (notes.surface_documents.read_surface_documents が作る)。

    tasks                  現在の場面の短期タスクリスト。場面が無いか、場面の文書が無ければ空文字列
    tips                   攻略情報 (場面の文書、無いか空なら共通の文書)
    controls               操作説明 (場面の文書、無いか空なら共通の文書)
    advice                 詰まったときの助言 (notes/advice.md)
    scene_id               現在の場面 ID。場面が決まっていなければ None
    uses_common_controls   操作説明に共通の文書を使ったら True
    uses_common_tips       攻略情報に共通の文書を使ったら True
    """

    tasks: str
    tips: str
    controls: str
    advice: str
    scene_id: str | None
    uses_common_controls: bool
    uses_common_tips: bool


@dataclass(frozen=True)
class RecentAction:
    """surface が直近に送った操作 1 件。

    t        操作を送った時刻 (decide の応答を受け取った時刻。実行の経過秒)
    action   送った操作 (ボタンの制限を通した後)
    """

    t: float
    action: PadAction


@dataclass(frozen=True)
class SurfaceInput:
    """SurfaceDecider.decide の入力。

    t                      要求の時刻 (フレームを選んだ時刻。実行の経過秒)
    frames                 送る画像 (古い順。1〜5 枚)
    documents              文書
    timeline               系列情報 (middle/state.json の timeline)。ファイルが無ければ空文字列
    timeline_generated_t   系列情報を作った時刻 (middle/state.json の generated_t)。ファイルが無ければ None
    recent_actions         直近に送った操作 (古い順。最大 surface_loop.RECENT_ACTIONS_COUNT 件)
    """

    t: float
    frames: tuple[SurfaceFrame, ...]
    documents: SurfaceDocuments
    timeline: str
    timeline_generated_t: float | None
    recent_actions: tuple[RecentAction, ...]


@dataclass(frozen=True)
class InfoLabel:
    """フレームに付けた情報ラベル (名前と確率)。

    name          ラベルの名前
    probability   確率 (0〜1)
    """

    name: str
    probability: float


class BoxSource(StrEnum):
    """box の出どころ。値は surface.jsonl の boxes の source に書く。"""

    JEV = "jev"  # Jev の Box の問いの答え
    TRACKER = "tracker"  # 追跡の部品 (surface.box_tracker) が前のフレームから追った位置


@dataclass(frozen=True)
class Box:
    """最新のフレームから取った box。座標は画像の幅・高さを 1 とした 0〜1 の値。

    label        box のラベル
    x_min        左端
    y_min        上端
    x_max        右端
    y_max        下端
    confidence   確信度 (0〜1)。Jev が確信度を返さないとき None
    source       出どころ。None は不明 (フェイクなど)
    """

    label: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    confidence: float | None
    source: BoxSource | None = None


@dataclass(frozen=True)
class SurfaceOutput:
    """SurfaceDecider.decide の出力。

    action          コントローラーの操作 (送る前にボタンの制限に通す)
    deep_kinds      deep を呼ぶ文書の種類の集合 (advisor 判定)。呼ばなければ空
    info_labels     フレームへの情報ラベル
    boxes           要求の最新のフレーム (SurfaceInput.frames の最後) での box
    scene_changed   場面転換を検知したら True (次の middle の入力に伝わる)
    """

    action: PadAction
    deep_kinds: frozenset[DocumentKind] = frozenset()
    info_labels: tuple[InfoLabel, ...] = ()
    boxes: tuple[Box, ...] = ()
    scene_changed: bool = False


class SurfaceDecider(Protocol):
    """surface の判断。実装は surface.jev_surface.JevSurfaceDecider (TypeSafe Jev を呼ぶ) と
    surface.fake_surface.FakeSurfaceDecider (フェイク)。"""

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        """surface_input から操作と付随する情報を決めて返す。失敗したら例外を出す (surface_loop が数える)。"""
        ...
