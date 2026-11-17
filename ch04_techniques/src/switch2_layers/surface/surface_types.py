"""surface 層の型: SurfaceDecider の入力 (SurfaceInput) と出力 (SurfaceOutput)、その部品と Protocol。"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from switch2_layers.deep.deep_scheduler import DeepDocumentStatus
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.surface.jev_client import JevAnswers
from switch2_layers.surface.surface_questions import SurfaceQuestion


@dataclass(frozen=True)
class SurfaceFrame:
    """surface に送る画像 1 枚。

    t       フレームの時刻 (実行の経過秒)
    jpeg    左上に t を、追えている box の枠と名前を書き込んだ JPEG のバイト列 (frames.surface_frames.build_surface_image)
    label   画像のラベル (frames.surface_frames.format_frame_label(t))
    file    この画像の保存を予約した、実行ディレクトリーからの相対パス (frames/surface/<tミリ秒9桁>.jpg など。
            surface.jsonl の frames の file と同じ値)。保存を捨てたとき (frame_store.FrameStore.save_surface が
            None を返したとき) と、保存しないとき (テストなど) は None
    """

    t: float
    jpeg: bytes
    label: str
    file: str | None = None


@dataclass(frozen=True)
class SurfaceDocuments:
    """surface が読む文書 (notes.surface_documents.read_surface_documents が作る)。

    tasks                  現在の場面のキューの先頭のタスク (今実行中のタスク) の文。場面が無いか、キューが空なら
                           空文字列
    task_id                先頭のタスクの ID。場面が無いか、キューが空なら None (先頭のタスクが無い)
    tips                   攻略情報 (場面の文書、無いか空なら共通の文書)
    controls               操作説明 (場面の文書、無いか空なら共通の文書)
    advice                 詰まったときの助言 (notes/advice.md)
    scene_id               現在の場面 ID。場面が決まっていなければ None
    uses_common_controls   操作説明に共通の文書を使ったら True
    uses_common_tips       攻略情報に共通の文書を使ったら True
    game                   ゲームの性質 (notes/game.md。場面に依らない)。読んだまま (前後の空白も除かない) で、
                           無いか読めなければ空文字列。既定値の空文字列は、ゲームの性質を渡さない入力 (テストなど) のため
    """

    tasks: str
    task_id: str | None
    tips: str
    controls: str
    advice: str
    scene_id: str | None
    uses_common_controls: bool
    uses_common_tips: bool
    game: str = ""


@dataclass(frozen=True)
class RecentAction:
    """surface が直近に送った操作 1 件。

    t        操作を送った時刻 (decide の応答を受け取った時刻。実行の経過秒)
    action   送った操作 (ボタンの制限を通した後)
    """

    t: float
    action: PadAction


@dataclass(frozen=True)
class SurfaceRequest:
    """Jev に送る要求 1 回の、記録に書く内容 (surface_requests.jsonl の 1 行の元)。

    request_index   要求の番号 (1 始まり。JevSurfaceDecider.request_count と同じ数え方)
    model           要求の model (モデル名)
    state           Jev に送った state と同じ内容で、frames の各要素の image (画像の参照文字列) を、その画像の
                    SurfaceFrame.file (保存を予約できなかった画像は None) に置き換えたもの
    questions       送った問い (送った順)
    API キーと HTTP のヘッダーは持たない。
    """

    request_index: int
    model: str
    state: Mapping[str, object]
    questions: tuple[SurfaceQuestion, ...]


@dataclass(frozen=True)
class SurfaceExchange:
    """1 回の要求の、記録のための情報 (SurfaceOutput.exchange)。

    request   送った要求 (要求の番号・モデル名・記録の形の state・問い)
    answers   受け取った答え (すべての問いの答えと、応答の usage)
    """

    request: SurfaceRequest
    answers: JevAnswers


@dataclass(frozen=True)
class SurfaceInput:
    """SurfaceDecider.decide の入力。

    t                      要求の時刻 (フレームを選んだ時刻。実行の経過秒)
    frames                 送る画像 (古い順。1〜5 枚)
    documents              文書
    timeline               系列情報 (middle/state.json の timeline)。ファイルが無ければ空文字列
    timeline_generated_t   系列情報を作った時刻 (middle/state.json の generated_t)。ファイルが無ければ None
    recent_actions         直近に送った操作 (古い順。最大 surface_loop.RECENT_ACTIONS_COUNT 件)
    advisor_statuses       advisor 判定で問う文書の種類 (surface_questions.ADVISOR_KINDS) → その種類の、現在の場面の
                           文書の deep の呼び出しの状況 (surface のループが要求ごとに DeepStatusReader で読む。
                           ADVISOR_KINDS の順)。状況を読めなかった種類は None。Jev を呼ぶ実装は state の advisor に文にして
                           入れ (jev_surface.advisor_to_state。ここに無い種類は None と同じに扱う)、フェイクは使わない。
                           既定は空 (どの種類も状況が無い)
    request_sink           送る要求の書き込み先 (surface のループが要求ごとに渡す)。Jev を呼ぶ実装は、Jev に要求を
                           送る直前に 1 回だけ SurfaceRequest を渡して呼ぶ。要求を作る前に失敗したら呼ばない。
                           呼んだ書き込み先が例外を出したら、要求を送らずにその例外をそのまま出す。
                           None なら何も書かない。Jev を呼ばない実装 (フェイク) は呼ばない
    progress               画面の停滞の計測の最新の値 (progress/state.json)。ファイルが無いか読めなければ None。
                           既定値の None は、値を渡さない入力 (フェイクやテストなど) のため
    """

    t: float
    frames: tuple[SurfaceFrame, ...]
    documents: SurfaceDocuments
    timeline: str
    timeline_generated_t: float | None
    recent_actions: tuple[RecentAction, ...]
    advisor_statuses: Mapping[DocumentKind, DeepDocumentStatus | None] = field(
        default_factory=dict
    )
    request_sink: Callable[[SurfaceRequest], None] | None = None
    progress: ProgressState | None = None


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
    exchange        記録のための情報 (送った要求と受け取った答え)。Jev を呼ばない実装 (フェイク) では None
    task_done_p     先頭のタスクを達成したかの問い (task_done) の確率。問いを送らなかったら None
    task_cancel_p   先頭のタスクが達成できないか状況に合わないかの問い (task_cancel) の確率。問いを送らなかったら None
    task_done_p・task_cancel_p の既定値は、問いを送らない出力 (フェイクやテストなど) のため。
    """

    action: PadAction
    deep_kinds: frozenset[DocumentKind] = frozenset()
    info_labels: tuple[InfoLabel, ...] = ()
    boxes: tuple[Box, ...] = ()
    scene_changed: bool = False
    exchange: SurfaceExchange | None = None
    task_done_p: float | None = None
    task_cancel_p: float | None = None


class DeepStatusReader(Protocol):
    """deep の文書ごとの呼び出しの状況を読むもの。run では deep.deep_scheduler.DeepScheduler を使う。"""

    def document_status(
        self, kind: DocumentKind, scene_id: str | None
    ) -> DeepDocumentStatus:
        """種類 kind の、場面 scene_id のときに対象になる文書 (notes.note_paths.document_key と同じ) の状況を返す。

        読み取りだけで、deep のジョブを起動せず、記録も書かない。読めなければ例外を出す (surface のループが、その種類の
        状況を None にする)。
        """
        ...


class SurfaceDecider(Protocol):
    """surface の判断。実装は surface.jev_surface.JevSurfaceDecider (TypeSafe Jev を呼ぶ) と
    surface.fake_surface.FakeSurfaceDecider (フェイク)。"""

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        """surface_input から操作と付随する情報を決めて返す。失敗したら例外を出す (surface_loop が数える)。"""
        ...
