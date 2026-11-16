"""TypeSafe Jev を呼ぶ SurfaceDecider (JevSurfaceDecider) と、要求の state の作り方。

要求の state (キーはこの順):
    {
      "instructions": surface_questions.SURFACE_INSTRUCTIONS,
      "documents": {"tasks": 短期タスクリスト, "tips": 攻略情報, "controls": 操作説明, "advice": 助言,
                    "game": ゲームの性質},
      "timeline": {"created": 系列情報の作成時刻のラベル (無ければ NO_TIMELINE_TEXT), "text": 系列情報},
      "recent_actions": [{"time": 時刻のラベル, "held": [...], "hold_seconds": {ボタン名: 秒}, "taps": [...],
                          "sticks": {"LS": [x, y], "RS": [x, y]}}, ...],
      "advisor": {文書の種類の値: deep の呼び出しの状況の文, ...},
      "frames": [{"label": 時刻のラベル, "image": <画像の参照文字列>}, ...]
    }
時刻のラベルは relative_time_label (例: "t=12.345 秒 (0.4 秒前)")。「何秒前」は要求の時刻 (SurfaceInput.t) から数える。
recent_actions と frames は古い順。advisor は surface_questions.ADVISOR_KINDS の順 (advisor_to_state。文は
deep_status_text)。画像 (SurfaceInput.frames の JPEG) は assets に入れ、state の frames の image に参照文字列を
1 回だけ置く。

1 回の decide の流れ:
    1. 要求の番号を 1 増やす (最初の decide が 1)
    2. 対象の一覧を surface.box_targets.read_box_targets(notes/, 場面 ID) で読み、BoxTrackingState.set_targets に渡す。
       前回読んだ一覧と名前が同じで説明が違う対象は、追跡をやめる (forget。3 で問い直す対象になる)
    3. 問い直す対象を box_tracking_state.select_requery_targets で選ぶ
       (2 と 3 は、送る画像を作る前に plan_boxes で済ませていれば、そのときの結果を使う)
    4. 問いの一覧 (surface_questions.build_surface_questions) と state を作る。送る直前に、記録の形の要求
       (surface_types.SurfaceRequest。state は build_recorded_state) を SurfaceInput.request_sink に渡し
       (None なら渡さない)、JevClient.ask で送る
    5. 答えから、操作・deep を呼ぶ種類・情報ラベル・場面転換を決める (surface.action_rules)
    6. box: 存在の確率が 0.5 未満の対象は追跡をやめ、box を出さない。問い直した対象は Jev の box で追跡を始め直し、
       Jev の box を出す (出どころ jev)。Jev の box が使えない (座標か確信度が 0〜1 の外、幅か高さが 0 以下で
       None) なら、追跡をやめ、box を出さない。ほかの対象は、要求の最新のフレームの位置があれば出す (送る画像に描く
       box と同じ BoxTrackingState.boxes_at の規則。そのフレームで Jev の box から追跡を始め直していればその Jev の
       box で出どころ jev、そうでなければ追跡の位置で出どころ tracker)
ask の例外 (JevError など) はそのまま外に出す (surface のループが surface の失敗として数える)。

送る画像への box の書き込み (plan_boxes):
    surface のループは、送る画像を作る前に plan_boxes(場面 ID) を呼ぶ。plan_boxes は次の要求の 2 と 3 を行って結果を
    持ち、書き込み方 (frames.surface_frames.BoxDrawingPlan: 対象の一覧の中の番号と、最新のフレームに書き込まない
    問い直す対象) を返す。続く decide は、要求の番号と場面 ID が同じなら、その結果をそのまま使う
    (最新のフレームに書き込まなかった対象と、Box を問い直す対象を一致させるため)。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from switch2_layers.deep.deep_scheduler import (
    SKIP_MIN_START_INTERVAL,
    DeepDocumentStatus,
    DeepJobEnd,
    DeepJobStatus,
    DeepRequestOutcome,
)
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.frames.surface_frames import BoxDrawingPlan
from switch2_layers.pad_action import action_to_json
from switch2_layers.run_directory import RunPaths
from switch2_layers.settings import (
    DEFAULT_BOX_REQUERY_EVERY,
    DEFAULT_THRESHOLD_ADVISOR,
)
from switch2_layers.surface.action_rules import (
    decide_deep_kinds,
    decide_info_labels,
    decide_pad_action,
    decide_scene_changed,
    visible_target_names,
)
from switch2_layers.surface.box_targets import BoxTarget, read_box_targets
from switch2_layers.surface.box_tracker import NormalizedBox
from switch2_layers.surface.box_tracking_state import (
    BoxTrackingState,
    select_requery_targets,
)
from switch2_layers.surface.jev_client import (
    JevAnswers,
    JevImage,
    new_asset_reference,
)
from switch2_layers.surface.surface_questions import (
    ADVISOR_KINDS,
    SURFACE_INSTRUCTIONS,
    SurfaceQuestion,
    box_question_name,
    build_surface_questions,
)
from switch2_layers.surface.surface_types import (
    Box,
    BoxSource,
    RecentAction,
    SurfaceDocuments,
    SurfaceExchange,
    SurfaceInput,
    SurfaceOutput,
    SurfaceRequest,
)

NO_TIMELINE_TEXT = "まだ作られていない"  # 系列情報がまだ無いときの timeline の created
# deep の呼び出しの状況を読めなかった種類の、state の advisor の文
ADVISOR_STATUS_UNAVAILABLE_TEXT = "状況を取得できない"
# deep のジョブの終わり方 → state の advisor の「前回の終了」の文に書く言い方
DEEP_JOB_STATUS_TEXTS: dict[DeepJobStatus, str] = {
    DeepJobStatus.OK: "正常に終了",
    DeepJobStatus.FAILED: "文書を更新できずに終了",
    DeepJobStatus.ERROR: "エラーで終了",
    DeepJobStatus.STOPPED: "止めた",
    DeepJobStatus.KILLED: "強制終了した",
}


class JevAsker(Protocol):
    """Jev に問いを送って答えを受け取るもの。run では surface.jev_client.JevClient。"""

    @property
    def model(self) -> str:
        """要求の model (モデル名)。送った要求の記録 (SurfaceRequest.model) に書く。"""
        ...

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


@dataclass(frozen=True)
class _RequestTargets:
    """1 回の要求の box の対象 (モジュールの docstring の decide の流れの 2 と 3 の結果)。

    request_index   要求の番号
    scene_id        対象の一覧を読んだ場面 ID
    targets         対象の一覧
    requery_names   Box を問い直す対象の名前 (targets の順)
    """

    request_index: int
    scene_id: str | None
    targets: tuple[BoxTarget, ...]
    requery_names: tuple[str, ...]


def relative_time_label(t: float, request_t: float) -> str:
    """時刻 t のラベル `t=<t を小数第 3 位まで> 秒 (<何秒前> 秒前)` を返す (例: "t=12.345 秒 (0.4 秒前)")。

    何秒前は request_t − t を小数第 1 位まで書いた値。負なら 0.0 にする。
    """
    ago = max(request_t - t, 0.0)
    return f"t={t:.3f} 秒 ({ago:.1f} 秒前)"


def documents_to_state(documents: SurfaceDocuments) -> dict[str, str]:
    """文書を state の documents {"tasks", "tips", "controls", "advice", "game"} (この順) にする。

    値は SurfaceDocuments の同じ名前の属性 (無い・読めない文書は read_surface_documents が空文字列にしたもの)。
    """
    return {
        "tasks": documents.tasks,
        "tips": documents.tips,
        "controls": documents.controls,
        "advice": documents.advice,
    }


def timeline_to_state(
    timeline: str, generated_t: float | None, request_t: float
) -> dict[str, str]:
    """系列情報を state の timeline {"created", "text"} にする。

    created は relative_time_label(generated_t, request_t)。generated_t が None なら NO_TIMELINE_TEXT。
    """
    created = (
        NO_TIMELINE_TEXT
        if generated_t is None
        else relative_time_label(generated_t, request_t)
    )
    return {"created": created, "text": timeline}


def recent_actions_to_state(
    recent_actions: Sequence[RecentAction], request_t: float
) -> list[dict[str, object]]:
    """直近の操作を、古い順の [{"time", "held", "hold_seconds", "taps", "sticks"}] にする。

    time は relative_time_label(操作の t, request_t)、held・hold_seconds (押し続けの秒数)・taps・sticks は
    pad_action.action_to_json の値。
    """
    return [
        {
            "time": relative_time_label(recent.t, request_t),
            **action_to_json(recent.action),
        }
        for recent in recent_actions
    ]


def deep_status_text(status: DeepDocumentStatus | None, request_t: float) -> str:
    """文書 1 つの deep の呼び出しの状況を、state の advisor の文にする。

    status が None (状況を読めなかった) なら ADVISOR_STATUS_UNAVAILABLE_TEXT。そうでなければ次の 4 つの文を
    この順に「。」でつないだもの (最後には「。」を付けない)。<時刻> は relative_time_label(その t, request_t):
        1. 実行中: running_count が 1 以上なら「実行中のジョブ: <running_count> 件 (最も新しい起動 <latest_running_start_t の時刻>)」、
           0 なら「実行中のジョブ: なし」
        2. 前回の終了: last_end があれば「前回の終了: <last_end.t の時刻>、<DEEP_JOB_STATUS_TEXTS[last_end.status]>」、
           None なら「前回の終了: まだ無い」
        3. 最小間隔: min_interval_remaining_s が 0 より大きければ「今求めると捨てられる (あと <残りを小数第 1 位まで> 秒)」、
           そうでなければ「今求めれば起動する」
        4. surface の最後の要求: last_surface_request が None なら「surface はまだ求めていない」。そうでなければ
           「surface が最後に求めた: <t の時刻>、<結果>」で、結果は、起動したら「起動した」、捨てた理由が
           deep_scheduler.SKIP_MIN_START_INTERVAL なら「前回の起動から間もないため捨てられた」、ほかの理由なら
           「捨てられた (<理由>)」
    例: 「実行中のジョブ: なし。前回の終了: t=120.300 秒 (15.2 秒前)、正常に終了。今求めれば起動する。
    surface が最後に求めた: t=100.000 秒 (35.5 秒前)、起動した」
    """
    if status is None:
        return ADVISOR_STATUS_UNAVAILABLE_TEXT
    return "。".join(
        (
            _running_jobs_text(status, request_t),
            _last_end_text(status.last_end, request_t),
            _min_interval_text(status.min_interval_remaining_s),
            _last_surface_request_text(status.last_surface_request, request_t),
        )
    )


def _running_jobs_text(status: DeepDocumentStatus, request_t: float) -> str:
    """deep_status_text の 1 の文 (実行中のジョブ) を返す。"""
    latest = status.latest_running_start_t
    if status.running_count <= 0 or latest is None:
        return "実行中のジョブ: なし"
    label = relative_time_label(latest, request_t)
    return f"実行中のジョブ: {status.running_count} 件 (最も新しい起動 {label})"


def _last_end_text(last_end: DeepJobEnd | None, request_t: float) -> str:
    """deep_status_text の 2 の文 (前回の終了) を返す。"""
    if last_end is None:
        return "前回の終了: まだ無い"
    label = relative_time_label(last_end.t, request_t)
    return f"前回の終了: {label}、{DEEP_JOB_STATUS_TEXTS[last_end.status]}"


def _min_interval_text(remaining_s: float) -> str:
    """deep_status_text の 3 の文 (最小間隔) を返す。"""
    if remaining_s > 0:
        return f"今求めると捨てられる (あと {remaining_s:.1f} 秒)"
    return "今求めれば起動する"


def _last_surface_request_text(
    outcome: DeepRequestOutcome | None, request_t: float
) -> str:
    """deep_status_text の 4 の文 (surface の最後の要求) を返す。"""
    if outcome is None:
        return "surface はまだ求めていない"
    if outcome.started:
        result = "起動した"
    elif outcome.reason == SKIP_MIN_START_INTERVAL:
        result = "前回の起動から間もないため捨てられた"
    else:
        result = f"捨てられた ({outcome.reason})"
    label = relative_time_label(outcome.t, request_t)
    return f"surface が最後に求めた: {label}、{result}"


def advisor_to_state(
    statuses: Mapping[DocumentKind, DeepDocumentStatus | None], request_t: float
) -> dict[str, str]:
    """advisor 判定で問う種類ごとの状況を、state の advisor {種類の値: 文} にする。

    キーは surface_questions.ADVISOR_KINDS のすべての種類の値で、ADVISOR_KINDS の順。文は
    deep_status_text(statuses の値, request_t)。statuses に無い種類は None (状況を読めなかった) と同じに扱い、
    ADVISOR_KINDS に無い種類は無視する。
    """
    return {
        kind.value: deep_status_text(statuses.get(kind), request_t)
        for kind in ADVISOR_KINDS
    }


def build_surface_state(
    surface_input: SurfaceInput, references: Sequence[str]
) -> dict[str, object]:
    """要求の state (モジュールの docstring の形) を返す。

    references は surface_input.frames と同じ順・同じ数の画像の参照文字列。frames の各要素は
    {"label": relative_time_label(frame.t, surface_input.t), "image": 参照文字列}。数が合わなければ ValueError。
    """
    return _state_with_images(surface_input, references)


def build_recorded_state(surface_input: SurfaceInput) -> dict[str, object]:
    """記録に書く state (SurfaceRequest.state) を返す。

    build_surface_state と同じキー・同じ内容で、frames の各要素の image だけを、画像の参照文字列ではなく
    そのフレームの SurfaceFrame.file (保存を予約できなかった画像は None) にしたもの。
    """
    return _state_with_images(
        surface_input, [frame.file for frame in surface_input.frames]
    )


def _state_with_images(
    surface_input: SurfaceInput, images: Sequence[str | None]
) -> dict[str, object]:
    """state (モジュールの docstring の形) を、frames の各要素の image を images の同じ位置の値にして返す。

    images は surface_input.frames と同じ順・同じ数。数が合わなければ ValueError。
    """
    if len(images) != len(surface_input.frames):
        raise ValueError(f"{len(images)} images for {len(surface_input.frames)} frames")
    request_t = surface_input.t
    return {
        "instructions": SURFACE_INSTRUCTIONS,
        "documents": documents_to_state(surface_input.documents),
        "timeline": timeline_to_state(
            surface_input.timeline, surface_input.timeline_generated_t, request_t
        ),
        "recent_actions": recent_actions_to_state(
            surface_input.recent_actions, request_t
        ),
        "advisor": advisor_to_state(surface_input.advisor_statuses, request_t),
        "frames": [
            {"label": relative_time_label(frame.t, request_t), "image": image}
            for frame, image in zip(surface_input.frames, images, strict=True)
        ],
    }


class JevSurfaceDecider:
    """SurfaceDecider の実装。TypeSafe Jev に問いを送り、操作と付随する情報を決める (モジュールの docstring)。

    client           Jev に問いを送るもの
    tracking         box の追跡の状態 (キャプチャーの on_frame と、送る画像の boxes_at にも同じものを使う)
    settings         設定
    notes_dir        文書のディレクトリー (notes/)。None なら use_run_directory で後から渡す
    make_reference   画像の参照文字列を作る関数 (既定 jev_client.new_asset_reference。呼ぶたびに異なる文字列を返す)
    decide と plan_boxes は 1 つのスレッド (surface のループ) からだけ呼ぶ。
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
        self.client = client
        self.tracking = tracking
        self.settings = settings
        self.notes_dir = notes_dir
        self.make_reference = make_reference
        self._request_count = 0
        # plan_boxes が決めた次の要求の box の対象 (decide が受け取ったら None に戻す)
        self._planned: _RequestTargets | None = None
        # 前回読んだ対象の一覧の、名前 → 説明 (説明が変わった対象を見つけるため)
        self._target_descriptions: dict[str, str] = {}

    @property
    def request_count(self) -> int:
        """これまでに decide を呼んだ回数 (直前の要求の番号)。"""
        return self._request_count

    def use_run_directory(self, paths: RunPaths) -> None:
        """実行ディレクトリーの notes/ (paths.notes_dir) を、対象の一覧を読むディレクトリーにする。

        app.LayersApp が実行ディレクトリーを作った後、スレッドを起動する前に 1 回呼ぶ (LayersParts.on_run_directory)。
        """
        self.notes_dir = paths.notes_dir

    def plan_boxes(self, scene_id: str | None) -> BoxDrawingPlan:
        """次の decide の要求 (番号 request_count + 1) の box の対象を決めて持ち、送る画像への書き込み方を返す。

        モジュールの docstring の decide の流れの 2 と 3 を、場面 ID scene_id で行う (BoxTrackingState.set_targets も
        呼ぶ)。戻り値の target_numbers は対象の名前 → 対象の一覧の中の番号 (1 始まり)、hidden_on_latest は Box を
        問い直す対象の名前。もう一度呼んだら、前の結果を捨てて決め直す。
        notes_dir がまだ無ければ (use_run_directory の前) RuntimeError。
        """
        planned = self._select_targets(self._request_count + 1, scene_id)
        self._planned = planned
        return BoxDrawingPlan(
            target_numbers={
                target.name: number
                for number, target in enumerate(planned.targets, start=1)
            },
            hidden_on_latest=frozenset(planned.requery_names),
        )

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        """surface_input から Jev に問いを送り、出力を返す (モジュールの docstring の流れ)。

        notes_dir がまだ無ければ (use_run_directory の前)、Jev を呼ばずに RuntimeError。
        box を問う画像と、出力の boxes が指すフレームは、要求の最新のフレーム (surface_input.frames の最後)。
        出力の boxes は対象の一覧の順で、Box の label は対象の名前。
        - 問い直した対象 (出どころ jev): 座標は Jev の box (x_min = left、y_min = top、x_max = right、
          y_max = bottom)、confidence は Jev の確信度 (null なら None)
        - 問い直さない対象: BoxTrackingState.boxes_at(最新のフレームの t) のその対象の box (送る画像に描く box と
          同じ規則)。そのフレームで Jev の box から追跡を始め直していれば、その Jev の box (出どころ jev、
          confidence None)。そうでなければ追跡の位置 (出どころ tracker、confidence は min(max(score, 0.0), 1.0))。
          その位置が無いか見失っていれば出さない
        Jev の box で追跡を始められなかったとき (問い合わせたフレームを捨てた後など) も、Jev の box は出す
        (その対象は追跡していないため、次の要求で問い直す)。Jev の box が None (使える box が無い) の対象は、
        存在の確率に依らず追跡をやめ (BoxTrackingState.forget)、box を出さない (次の要求で問い直す)。
        直前の plan_boxes の結果が、この要求の番号と場面 ID (surface_input.documents.scene_id) のものなら、対象の一覧と
        問い直す対象はその結果を使う。そうでなければ、ここで決める。

        送る要求の記録: ask を呼ぶ直前に、surface_input.request_sink (None なら呼ばない) を 1 回だけ
        SurfaceRequest(要求の番号, client.model, build_recorded_state(surface_input), 問いの一覧) で呼ぶ。
        要求を作る前に失敗したとき (notes_dir が無いときなど) は呼ばない。ask が失敗したとき
        (送った後の失敗) は呼んだ後に例外が出る。request_sink が例外を出したら、ask を呼ばずにその例外を出す。
        出力の exchange は SurfaceExchange(request_sink に渡したものと同じ SurfaceRequest, ask の答え)。
        """
        if self.notes_dir is None:
            raise RuntimeError("JevSurfaceDecider.decide needs the run directory")
        self._request_count += 1
        request_index = self._request_count
        scene_id = surface_input.documents.scene_id
        planned, self._planned = self._planned, None
        if (
            planned is None
            or planned.request_index != request_index
            or planned.scene_id != scene_id
        ):
            planned = self._select_targets(request_index, scene_id)
        frames = surface_input.frames
        references = [self.make_reference() for _ in frames]
        images = [
            JevImage(reference, frame.jpeg)
            for reference, frame in zip(references, frames, strict=True)
        ]
        questions = build_surface_questions(
            self.settings.allowed_buttons,
            planned.targets,
            planned.requery_names,
            len(frames),
        )
        state = build_surface_state(surface_input, references)
        request = SurfaceRequest(
            request_index=request_index,
            model=self.client.model,
            state=build_recorded_state(surface_input),
            questions=questions,
        )
        # 送る直前に書く (書けなければ送らない)。送った後の失敗でも、この記録は残る
        if surface_input.request_sink is not None:
            surface_input.request_sink(request)
        answers = self.client.ask(state, questions, images)
        return SurfaceOutput(
            action=decide_pad_action(answers.choices, answers.nouls, questions),
            deep_kinds=decide_deep_kinds(
                answers.nouls, self.settings.threshold_advisor
            ),
            info_labels=decide_info_labels(answers.nouls),
            boxes=self._decide_boxes(planned, answers, frames[-1].t),
            scene_changed=decide_scene_changed(answers.nouls),
            exchange=SurfaceExchange(request=request, answers=answers),
        )

    def _select_targets(
        self, request_index: int, scene_id: str | None
    ) -> _RequestTargets:
        """番号 request_index の要求の box の対象を決める (モジュールの docstring の decide の流れの 2 と 3)。

        notes_dir がまだ無ければ RuntimeError。
        """
        if self.notes_dir is None:
            raise RuntimeError("JevSurfaceDecider needs the run directory")
        targets = read_box_targets(self.notes_dir, scene_id)
        names = [target.name for target in targets]
        self.tracking.set_targets(names)
        # 名前が同じでも説明が変わった対象は別の物を指しうるため、追跡をやめて問い直す
        for target in targets:
            previous = self._target_descriptions.get(target.name)
            if previous is not None and previous != target.description:
                self.tracking.forget(target.name)
        self._target_descriptions = {
            target.name: target.description for target in targets
        }
        requery_names = select_requery_targets(
            names,
            self.tracking.statuses(),
            request_index,
            self.settings.box_requery_every,
        )
        return _RequestTargets(request_index, scene_id, targets, requery_names)

    def _decide_boxes(
        self, planned: _RequestTargets, answers: JevAnswers, latest_t: float
    ) -> tuple[Box, ...]:
        """答えと追跡から、要求の最新のフレーム (時刻 latest_t) の box を対象の一覧の順で返す (decide の流れの 6)。"""
        visible = visible_target_names(answers.nouls, planned.targets)
        requery = set(planned.requery_names)
        tracked_boxes = {box.label: box for box in self.tracking.boxes_at(latest_t)}
        boxes: list[Box] = []
        for number, target in enumerate(planned.targets, start=1):
            name = target.name
            if name in requery:
                jev_box = answers.boxes.get(box_question_name(number))
                if jev_box is None or name not in visible:
                    # 使える box が無いか、写っていない: 追跡をやめ、次の要求で問い直す
                    self.tracking.forget(name)
                    continue
                self.tracking.start_from_jev(
                    name,
                    NormalizedBox(
                        left=jev_box.left,
                        top=jev_box.top,
                        right=jev_box.right,
                        bottom=jev_box.bottom,
                    ),
                    latest_t,
                    planned.request_index,
                )
                boxes.append(
                    Box(
                        label=name,
                        x_min=jev_box.left,
                        y_min=jev_box.top,
                        x_max=jev_box.right,
                        y_max=jev_box.bottom,
                        confidence=jev_box.confidence,
                        source=BoxSource.JEV,
                    )
                )
                continue
            if name not in visible:
                self.tracking.forget(name)
                continue
            # 送る画像に描く box と同じ規則 (BoxTrackingState.boxes_at) で、最新のフレームの位置を求める
            tracked = tracked_boxes.get(name)
            if tracked is not None:
                boxes.append(tracked)
        return tuple(boxes)
