"""テストのための、合成の実行のディレクトリー (runs/<実行>/) を書く補助 (テストではない)。

6 つの実行を書く関数があり、どの関数も、テストが期待値を参照できる値を返す:
    write_dashboard_run                --record-dashboard の記録がそろった実行 (DashboardRun)
    write_no_dashboard_run             --record-dashboard も --save-requests も付けていない実行 (NoDashboardRun)
    write_save_requests_run            --save-requests だけを付けた実行 (SaveRequestsRun)
    write_broken_run                   write_dashboard_run と同じ記録に、JSON として読めない行を足した実行 (DashboardRun)
    write_no_answer_type_run           header の問いに answer_type が無い形の実行 (OldFormatRun)
    write_single_advisor_question_run  advisor の問いが name `advisor` の 1 問の形の実行 (OldFormatRun)
write_all_runs は 6 つをまとめて書く。

第4章の関数で作れる記録は第4章の関数で作る: requests.jsonl の header と要求の行 (request_log)、frames.jsonl
(実際の SharpnessValleyDetector に合成のフレームを入れて dashboard_records で作る)、pad.jsonl、run.jsonl の
record_error・record_end・advisor の行、advisor のディレクトリーの入力 (context.md と画像)・stream.jsonl の行・
result.json。手で書くのは、run.jsonl の行の time と、start・decision など第4章に組み立ての関数が無い行と、
古い形の記録だけ。
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from switch2_agent.action_decision import PadAction, Thresholds, decide_action
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.advisors.base import AdvisorContext
from switch2_agent.advisors.claude_code_headless import (
    RESULT_FILE_NAME,
    advisor_run_dir_name,
    build_log_event,
    build_result_record,
    build_stream_result_record,
    write_advisor_inputs,
)
from switch2_agent.advisors.claude_code_headless import (
    build_record_error_event as build_advisor_record_error_event,
)
from switch2_agent.advisors.stream_json_log import (
    STREAM_FILE_NAME,
    build_stream_line_record,
)
from switch2_agent.dashboard_records import (
    FRAMES_RECORD_FILE_NAME,
    PAD_RECORD_FILE_NAME,
    FrameStep,
    PadReport,
    PadReportSource,
    build_frame_record,
    build_pad_record,
    build_record_end_event,
    build_record_error_event,
    snapshot_detector,
)
from switch2_agent.decision_models.base import Decision
from switch2_agent.decision_request import (
    DEFAULT_HISTORY_COUNT,
    LabeledImage,
    build_decision_request,
    build_instructions,
)
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.meaningful_frames import (
    MeanAbsDiffSimilarity,
    SharpnessValleyDetector,
)
from switch2_agent.image_metrics import mean_abs_diff
from switch2_agent.operation_log import (
    NeutralRecord,
    OperationLog,
    OperationRecord,
    format_action,
)
from switch2_agent.pipeline import DECISION_ERROR_NEUTRAL_REASON
from switch2_agent.questions import NONE_CHOICE, build_questions
from switch2_agent.request_log import (
    REQUEST_LOG_FILE_NAME,
    RequestLog,
    RequestLogEntry,
    build_request_record,
    frame_file_name,
)
from switch2_agent.run import RUN_LOG_FILE_NAME
from switch2_agent.run import parse_args as parse_agent_args
from switch2_remote.controller_state import PadState

GAME = "smb"
ROLES_BY_NAME = {role.name: role for role in ADVISOR_ROLES}
SECONDS_PER_DAY = 86400


def encode_jpeg(gray: int) -> bytes:
    """全画素が gray の 32x18 の小さな画像を JPEG のバイト列にして返す。"""
    ok, encoded = cv2.imencode(".jpg", np.full((18, 32, 3), gray, np.uint8))
    if not ok:
        raise ValueError("failed to encode a synthetic JPEG")
    return encoded.tobytes()


# 縮小画像・要求の画像・advisor の画像に使う JPEG (どの画像も同じ中身)
JPEG = encode_jpeg(128)

# --- 実行のディレクトリーの名前 (名前の降順はこの並び) ---
DASHBOARD_RUN_NAME = "20261113-235950"
BROKEN_RUN_NAME = "20261113-120000"
SAVE_REQUESTS_RUN_NAME = "20261112-110000"
NO_DASHBOARD_RUN_NAME = "20261112-100000"
SINGLE_ADVISOR_QUESTION_RUN_NAME = "20261111-090000"
NO_ANSWER_TYPE_RUN_NAME = "20261110-090000"
RUN_NAMES_NEWEST_FIRST = [
    DASHBOARD_RUN_NAME,
    BROKEN_RUN_NAME,
    SAVE_REQUESTS_RUN_NAME,
    NO_DASHBOARD_RUN_NAME,
    SINGLE_ADVISOR_QUESTION_RUN_NAME,
    NO_ANSWER_TYPE_RUN_NAME,
]

# --- ゲームの情報 (strategy の中身は、4 つの見出しとは違う ## の見出しを含む) ---
CONTROLS_NOTE = "- A: ジャンプ (このゲームで確かめた)\n- B: ダッシュ (一般)"
STRATEGY_NOTE_FIRST = (
    "## 目標\n- 大目標: ゲームを先に進める (途中)\n\n## 戦略\n- 右へ進む"
)
STRATEGY_NOTE_SECOND = "## 目標\n- 大目標: ゲームを先に進める (途中)\n\n## 戦略\n- 穴の手前で A を押して跳ぶ"
TIPS_NOTE = "- 穴に落ちると最初からやり直しになる"
FIRST_NOTES = {
    "controls": CONTROLS_NOTE,
    "tips": "",
    "strategy": STRATEGY_NOTE_FIRST,
    "advice": "",
}
STRATEGY_CHANGED_NOTES = {**FIRST_NOTES, "strategy": STRATEGY_NOTE_SECOND}
TIPS_CHANGED_NOTES = {**STRATEGY_CHANGED_NOTES, "tips": TIPS_NOTE}


def format_clock(seconds_of_day: float) -> str:
    """0 時からの秒 seconds_of_day を、壁時計の時刻 `HH:MM:SS.mmm` にする (ミリ秒に丸める)。"""
    total_ms = round(seconds_of_day * 1000) % (SECONDS_PER_DAY * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, milliseconds = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def append_json_line(path: Path, record: Mapping) -> None:
    """record を JSON の 1 行 (末尾に改行) として path に追記する。"""
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_run_log(
    run_dir: Path,
    start_seconds: float,
    events: Sequence[tuple[float, dict]],
    right_after_start: Sequence[tuple[float, dict]] = (),
) -> None:
    """run.jsonl に、(経過秒, 行の辞書) の events を経過秒の順に {"time": 壁時計の時刻, **行の辞書} で書く。

    壁時計の時刻は、start の行の時刻 start_seconds (0 時からの秒) に経過秒を足したもの (24 時を過ぎたら 0 時に戻る)。
    right_after_start の行は、経過秒によらず、start の行のすぐ後に (この順で) 書く。第4章の RunLog.write は
    時刻をロックの外で取るため、start と同時に別のスレッドが書いた行は、ファイルでは start の行より後でも
    時刻がわずかに前になることがある。その行を表すのに使う。
    """
    lines: list[tuple[float, dict]] = []
    for t, event in sorted(events, key=lambda pair: pair[0]):
        lines.append((t, event))
        if event.get("event") == "start":
            lines.extend(right_after_start)
    path = run_dir / RUN_LOG_FILE_NAME
    for t, event in lines:
        append_json_line(path, {"time": format_clock(start_seconds + t), **event})


def write_json_file(path: Path, record: Mapping) -> None:
    """record を JSON (UTF-8、字下げ 2) で path に書く (第4章の result.json と同じ書き方)。"""
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def neutral_answers() -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """選択肢の問いは none を 1.0 (ほかの選択肢を 0.0)、確率の問いは 0.0 にした答え (probabilities, choices) を返す。"""
    probabilities: dict[str, float] = {}
    choices: dict[str, dict[str, float]] = {}
    for question in build_questions():
        if question.answer_type == "choice":
            choices[question.name] = {
                choice.name: 1.0 if choice.name == NONE_CHOICE else 0.0
                for choice in question.choices
            }
        else:
            probabilities[question.name] = 0.0
    return probabilities, choices


def make_decision(
    chosen: Mapping[str, Mapping[str, float]] | None = None,
    **probability_overrides: float,
) -> Decision:
    """neutral_answers の答えのうち、chosen (選択肢の問いの name → 選択肢ごとの確率) と probability_overrides
    (確率の問いの name → 確率) の問いだけを置き換えた Decision を返す。知らない問いの name は ValueError にする。
    """
    probabilities, choices = neutral_answers()
    for name, values in (chosen or {}).items():
        if name not in choices:
            raise ValueError(f"unknown choice question: {name}")
        choices[name] = dict(values)
    for name, value in probability_overrides.items():
        if name not in probabilities:
            raise ValueError(f"unknown probability question: {name}")
        probabilities[name] = value
    return Decision(
        probabilities=probabilities,
        choices=choices,
        refusals=0,
        raw_response={"model": "jev-synthetic"},
    )


def make_frame(origin_mono: float, t: float, sharpness: float, gray: int) -> TimedFrame:
    """経過秒 t (時刻は origin_mono + t)・鮮明度 sharpness・全画素が gray の縮小グレー画像の TimedFrame を返す。"""
    return TimedFrame(
        time=origin_mono + t,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.full((18, 32), gray, np.uint8),
        sharpness=float(sharpness),
    )


def encode_frame(frame: TimedFrame) -> bytes:
    """要求の組み立て (build_decision_request) に渡す符号化の関数。どのフレームも JPEG にする。"""
    return JPEG


def role_names_in_order(names: frozenset[str]) -> list[str]:
    """役割の name の集合を、advisor_roles.ADVISOR_ROLES の順のリストにする。"""
    return [role.name for role in ADVISOR_ROLES if role.name in names]


def write_stream(path: Path, lines: Sequence[tuple[float, str]]) -> None:
    """(mono, claude の標準出力の 1 行) の lines を、build_stream_line_record で stream.jsonl の行にして書く。"""
    for mono, line in lines:
        append_json_line(path, build_stream_line_record(line, mono))


def stream_line(data: dict) -> str:
    """stream-json の 1 行 (claude の標準出力の行) を返す。"""
    return json.dumps(data, ensure_ascii=False)


def tool_use_line(tool_use_id: str, name: str, tool_input: dict) -> str:
    """ツールの呼び出し (assistant の tool_use) の stream-json の 1 行を返す。"""
    return stream_line(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": name,
                        "input": tool_input,
                    }
                ]
            },
        }
    )


def tool_result_line(tool_use_id: str, content: object, is_error: bool = False) -> str:
    """ツールの結果 (user の tool_result) の stream-json の 1 行を返す。"""
    item: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        item["is_error"] = True
    return stream_line({"type": "user", "message": {"content": [item]}})


# =====================================================================
# 1. --record-dashboard の記録がそろった実行
# =====================================================================

DASHBOARD_ORIGIN_MONO = 1000.0
# start の行の壁時計の時刻 (23:59:50.000)。経過秒 10 秒より後の行は 00:00 台になる
DASHBOARD_START_SECONDS = 23 * 3600 + 59 * 60 + 50
DASHBOARD_START_TIME = "23:59:50.000"
DASHBOARD_THRESHOLD_ADVISOR = 0.6
DASHBOARD_VALLEY_RATIO = 0.25
DASHBOARD_SIMILARITY_THRESHOLD = 0.5
DASHBOARD_AGENT_ARGV = [
    "--game",
    GAME,
    "--model",
    "jev",
    "--threshold-advisor",
    str(DASHBOARD_THRESHOLD_ADVISOR),
    "--valley-ratio",
    str(DASHBOARD_VALLEY_RATIO),
    "--similarity-threshold",
    str(DASHBOARD_SIMILARITY_THRESHOLD),
    "--record-dashboard",
]
# decision の行の apply_s
DECISION_APPLY_S = 0.03125
# 判定に入れるフレーム (経過秒, 鮮明度, 縮小グレー画像の画素の値)。valley_ratio 0.25 で、1.0 秒で谷に向かい始め
# (fall)、1.25 秒で谷を確定して 0.75 秒の山を残す (close)
DASHBOARD_FRAMES = (
    (0.5, 100.0, 10),
    (0.75, 200.0, 10),
    (1.0, 40.0, 40),
    (1.25, 180.0, 40),
    (5.5, 190.0, 80),
    (12.25, 200.0, 80),
    (13.75, 210.0, 120),
)
# 送ったレポート (経過秒, source, 押下状態, ok, error)
DASHBOARD_PAD_REPORTS: tuple[
    tuple[float, PadReportSource, PadState, bool, str | None], ...
] = (
    (
        1.3125,
        "apply",
        PadState(frozenset({"A", "B", "UP"}), {"LS": (0.4375, 0.0)}),
        True,
        None,
    ),
    (
        1.4375,
        "tap_release",
        PadState(frozenset({"A", "UP"}), {"LS": (0.4375, 0.0)}),
        True,
        None,
    ),
    (2.125, "neutral", PadState(), False, "pipe closed"),
    (6.8125, "apply", PadState(frozenset({"RIGHT"}), {"RS": (0.0, -1.0)}), True, None),
    (12.8125, "apply", PadState(), True, None),
)
DASHBOARD_DROPPED = {"frames": 1, "pad": 0}
# 起動直後 (start の行より前) の record_error の行の経過秒
EARLY_RECORD_ERROR_T = -0.5
# start の行のすぐ後に書いた、時刻が start よりわずかに前 (3 ミリ秒) の record_error の行の経過秒
# (第4章の RunLog.write は時刻をロックの外で取るため、別のスレッドが start と同時に書くと起こる)
SLIGHTLY_BEFORE_START_T = -0.003
ADVISOR_STEPS_RECORD_ERROR_T = 7.0
CAPTURE_STOPPED_T = 14.5
RECORD_END_T = 15.0
RECORD_ERROR_MESSAGE = "No space left on device"

STRATEGY_RUN_NAME = advisor_run_dir_name("strategy", 1)
CONTROLS_RUN_NAME = advisor_run_dir_name("controls", 1)
STRATEGY_DURATION_S = 4.0
# strategy の実行の、最後の result の行 (claude -p の stream-json の結果)。toolu_02 の Bash が拒否された
STRATEGY_RESULT_MESSAGE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "戦略を更新した\n次は穴を越える",
    "permission_denials": [
        {
            "tool_name": "Bash",
            "tool_use_id": "toolu_02",
            "tool_input": {
                "command": "ls game_notes\nwc -l game_notes/smb/strategy.md"
            },
        }
    ],
}


@dataclass(frozen=True)
class RequestSpec:
    """合成の要求 1 回分 (経過秒はどれも Pipeline の起点からの秒)。

    number      要求の通し番号
    sent        送信時刻 (t_sent)
    encoded     model.decide を呼ぶ直前の時刻
    received    応答を受け取った時刻 (応答が無ければ None)
    frame_t     要求に載せたフレームの経過秒
    notes       ゲームの情報の 4 ファイルの中身 (種類 → 中身)
    decision    応答 (応答が無ければ None)
    result      要求の結果
    error       result が ok でないときの説明
    accepted    要求を受け付けた advisor の役割の name
    """

    number: int
    sent: float
    encoded: float
    received: float | None
    frame_t: float
    notes: Mapping[str, str]
    decision: Decision | None
    result: Literal["ok", "error", "discarded"]
    error: str | None
    accepted: frozenset[str]


@dataclass(frozen=True)
class DashboardRun:
    """write_dashboard_run と write_broken_run が書いた実行。

    経過秒はどれも Pipeline の起点 (origin_mono) からの秒。要求の number は次のとおり:
        pressed_request           押下 (A)、短押し (B)、十字キーの UP と DOWN がどちらも押す側になり DOWN が
                                  rejected、スティックの方向の採用 (LS_x の right)、同点 (LS_y)、advisor の問いが
                                  しきい値を超える (strategy) としきい値ちょうど (tips) を含む要求。strategy の
                                  advisor が受け付けた
        error_request             応答が無い要求 (result が error、decision が無い)
        controls_request          controls の advisor を受け付けた要求 (その実行は result.json の無い実行中)
        strategy_changed_request  strategy の advisor の実行の終了から 1 秒を超えて後に strategy が変わった要求。
                                  直前の要求 (controls_request) は、その実行の終了より前に送った
        tips_changed_request      どの advisor の実行にも入らない時刻に tips が変わった要求 (00:00 台)
        discarded_request         キャプチャーが止まって応答を捨てた要求 (result が discarded)
    """

    name: str
    run_dir: Path
    origin_mono: float
    start_time: str
    args: dict
    threshold_advisor: float
    valley_ratio: float
    similarity_threshold: float
    pressed_request: int
    error_request: int
    controls_request: int
    strategy_changed_request: int
    tips_changed_request: int
    discarded_request: int
    t_sent: dict[int, float]
    t_received: dict[int, float | None]
    note_version_requests: list[int]
    note_version_t: list[float]
    strategy_run: str
    controls_run: str
    strategy_duration_s: float
    frame_t: list[float]
    pad_t: list[float]
    jpeg_bytes: int


def dashboard_request_specs() -> list[RequestSpec]:
    """--record-dashboard の記録がそろった実行の要求 (DashboardRun の docstring の要求) を、number の順で返す。"""
    pressed = make_decision(
        {
            "button_A": {"none": 0.125, "hold": 0.75, "tap": 0.125},
            "button_B": {"none": 0.25, "hold": 0.125, "tap": 0.625},
            "button_UP": {"none": 0.125, "hold": 0.75, "tap": 0.125},
            "button_DOWN": {"none": 0.25, "hold": 0.25, "tap": 0.5},
            "stick_LS_x": {"none": 0.25, "left": 0.125, "right": 0.625},
            "stick_LS_y": {"none": 0.375, "up": 0.375, "down": 0.25},
        },
        stick_LS_x_at_least_50=0.5,
        stick_LS_x_at_least_75=0.25,
        advisor_strategy=0.875,
        advisor_advice=0.25,
        advisor_tips=DASHBOARD_THRESHOLD_ADVISOR,
    )
    controls = make_decision(
        {"button_A": {"none": 0.25, "hold": 0.25, "tap": 0.5}},
        advisor_controls=0.75,
    )
    strategy_changed = make_decision(
        {
            "button_RIGHT": {"none": 0.0, "hold": 1.0, "tap": 0.0},
            "stick_RS_y": {"none": 0.0, "up": 0.0, "down": 1.0},
        },
        stick_RS_y_at_least_50=1.0,
        stick_RS_y_at_least_75=1.0,
        stick_RS_y_at_least_100=1.0,
    )
    discarded = make_decision({"button_A": {"none": 0.0, "hold": 1.0, "tap": 0.0}})
    return [
        RequestSpec(
            1,
            1.0,
            1.125,
            1.25,
            1.0,
            FIRST_NOTES,
            pressed,
            "ok",
            None,
            frozenset({"strategy"}),
        ),
        RequestSpec(
            2,
            2.0,
            2.0625,
            None,
            1.25,
            FIRST_NOTES,
            None,
            "error",
            "HTTP 500",
            frozenset(),
        ),
        RequestSpec(
            3,
            3.0,
            3.25,
            3.5,
            1.25,
            FIRST_NOTES,
            controls,
            "ok",
            None,
            frozenset({"controls"}),
        ),
        RequestSpec(
            4,
            6.5,
            6.625,
            6.75,
            5.5,
            STRATEGY_CHANGED_NOTES,
            strategy_changed,
            "ok",
            None,
            frozenset(),
        ),
        RequestSpec(
            5,
            12.375,
            12.5,
            12.75,
            12.25,
            TIPS_CHANGED_NOTES,
            make_decision(),
            "ok",
            None,
            frozenset(),
        ),
        RequestSpec(
            6,
            14.0,
            14.125,
            14.25,
            13.75,
            TIPS_CHANGED_NOTES,
            discarded,
            "discarded",
            "capture ended",
            frozenset(),
        ),
    ]


def decision_event(spec: RequestSpec, action: PadAction) -> dict:
    """操作を送った要求の run.jsonl の decision の行 (switch2_agent.pipeline.Pipeline.run_once の 10 のキー) を返す。"""
    assert spec.received is not None and spec.decision is not None
    return {
        "event": "decision",
        "number": spec.number,
        "interval_wait_s": 0.0,
        "select_s": 0.0,
        "encode_s": spec.encoded - spec.sent,
        "decide_s": spec.received - spec.encoded,
        "apply_s": DECISION_APPLY_S,
        "images": 1,
        "payload_bytes": len(JPEG),
        "action": format_action(action),
        "advisor_probabilities": dict(action.advisor_probabilities),
        "advisor_requested": role_names_in_order(action.advisor_requested),
        "advisor_accepted": role_names_in_order(spec.accepted),
        "refusals": spec.decision.refusals,
    }


def write_dashboard_frames(
    run_dir: Path, origin_mono: float
) -> dict[float, TimedFrame]:
    """DASHBOARD_FRAMES を実際の SharpnessValleyDetector に入れ、frames.jsonl と thumbs/ を書く。

    第4章の dashboard_recorder.feed_detector と同じ順 (入れる前と後の状態を取る) で FrameStep を作り、
    build_frame_record で行にする。diff は直前のフレームとの image_metrics.mean_abs_diff (最初のフレームは None)。
    縮小画像は、行の thumb のパスに JPEG を書く。経過秒 → フレームの辞書を返す。
    """
    detector = SharpnessValleyDetector(
        DASHBOARD_VALLEY_RATIO, MeanAbsDiffSimilarity(DASHBOARD_SIMILARITY_THRESHOLD)
    )
    (run_dir / "thumbs").mkdir()
    frames: dict[float, TimedFrame] = {}
    previous: TimedFrame | None = None
    for t, sharpness, gray in DASHBOARD_FRAMES:
        frame = make_frame(origin_mono, t, sharpness, gray)
        before = snapshot_detector(detector)
        kept = detector.feed(frame)
        step = FrameStep(
            frame=frame,
            before=before,
            after=snapshot_detector(detector),
            kept=kept,
            valley_ratio=detector.valley_ratio,
            after_drop=False,
        )
        diff = (
            mean_abs_diff(frame.gray_small, previous.gray_small)
            if previous is not None
            else None
        )
        record = build_frame_record(step, diff)
        append_json_line(run_dir / FRAMES_RECORD_FILE_NAME, record)
        (run_dir / record["thumb"]).write_bytes(JPEG)
        frames[t] = frame
        previous = frame
    return frames


def write_dashboard_pad(run_dir: Path, origin_mono: float) -> None:
    """DASHBOARD_PAD_REPORTS を build_pad_record で pad.jsonl に書く。"""
    for t, source, state, ok, error in DASHBOARD_PAD_REPORTS:
        report = PadReport(
            mono=origin_mono + t, source=source, state=state, ok=ok, error=error
        )
        append_json_line(run_dir / PAD_RECORD_FILE_NAME, build_pad_record(report))


def write_dashboard_requests(
    run_dir: Path, origin_mono: float, frames: Mapping[float, TimedFrame]
) -> list[tuple[float, dict]]:
    """dashboard_request_specs の要求を requests.jsonl に書き、run.jsonl に書く (経過秒, 行) のリストを返す。

    header は RequestLog(save_frames=False, record_thumbs=True).write_header、要求の行は build_request_record で作る
    (--record-dashboard だけの実行と同じく、画像は書かず、縮小画像の名前 thumb を足す)。要求は
    switch2_agent.decision_request.build_decision_request で組み立て、操作は decide_action で決め、操作の履歴は
    OperationLog に Pipeline と同じ順で足す。返す行は、操作を送った要求の decision の行と、応答が無い要求の
    decision_error の行と、応答を捨てた要求の capture_stopped の行。
    """
    questions = build_questions()
    thresholds = Thresholds(advisor=DASHBOARD_THRESHOLD_ADVISOR)
    request_log = RequestLog(run_dir, save_frames=False, record_thumbs=True)
    request_log.write_header("jev", build_instructions(), questions)
    operation_log = OperationLog()
    events: list[tuple[float, dict]] = []
    for spec in dashboard_request_specs():
        request = build_decision_request(
            frames[spec.frame_t],
            origin_mono + spec.sent,
            spec.notes,
            questions,
            encode_frame,
            origin=origin_mono,
            recent_operations=operation_log.recent(DEFAULT_HISTORY_COUNT),
        )
        action = (
            decide_action(
                spec.decision.probabilities,
                spec.decision.choices,
                questions,
                thresholds,
            )
            if spec.result == "ok" and spec.decision is not None
            else None
        )
        entry = RequestLogEntry(
            number=spec.number,
            origin=origin_mono,
            sent_at=origin_mono + spec.sent,
            encoded_at=origin_mono + spec.encoded,
            received_at=(
                origin_mono + spec.received if spec.received is not None else None
            ),
            request=request,
            decision=spec.decision,
            action=action,
            advisor_accepted=spec.accepted,
            result=spec.result,
            error=spec.error,
        )
        record = build_request_record(
            entry,
            format_clock(DASHBOARD_START_SECONDS + spec.sent),
            save_frames=False,
            record_thumbs=True,
        )
        append_json_line(run_dir / REQUEST_LOG_FILE_NAME, record)
        if spec.result == "ok" and spec.received is not None and action is not None:
            operation_log.add(
                OperationRecord(
                    origin_mono + spec.sent, origin_mono + spec.received, action
                )
            )
            events.append((spec.received, decision_event(spec, action)))
        elif spec.result == "error":
            neutral_t = spec.encoded + 0.0625
            operation_log.add(
                NeutralRecord(origin_mono + neutral_t, DECISION_ERROR_NEUTRAL_REASON)
            )
            events.append((neutral_t, {"event": "decision_error", "error": spec.error}))
        else:
            events.append(
                (CAPTURE_STOPPED_T, {"event": "capture_stopped", "error": spec.error})
            )
    return events


def strategy_stream_lines(origin_mono: float) -> list[tuple[float, str]]:
    """strategy の実行の stream.jsonl に書く (mono, 行) を返す。

    Read の呼び出しと結果、拒否された Bash の呼び出しと結果、JSON として読めない行 (raw の行になる)、Skill の
    呼び出しと結果、最後の result の行。ほかに system・rate_limit_event の行と、assistant の text の項目を含む。
    """
    return [
        (
            origin_mono + 1.5,
            stream_line(
                {"type": "system", "subtype": "init", "session_id": "s-strategy"}
            ),
        ),
        (
            origin_mono + 2.0,
            tool_use_line(
                "toolu_01", "Read", {"file_path": "game_notes/smb/strategy.md"}
            ),
        ),
        (
            origin_mono + 2.25,
            tool_result_line(
                "toolu_01", "## 目標\n- 大目標: ゲームを先に進める (途中)"
            ),
        ),
        (
            origin_mono + 2.5,
            stream_line(
                {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}
            ),
        ),
        (
            origin_mono + 3.0,
            stream_line(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "ファイルの一覧を確かめる"},
                            {
                                "type": "tool_use",
                                "id": "toolu_02",
                                "name": "Bash",
                                "input": {
                                    "command": "ls game_notes\nwc -l game_notes/smb/strategy.md"
                                },
                            },
                        ]
                    },
                }
            ),
        ),
        (
            origin_mono + 3.25,
            tool_result_line(
                "toolu_02",
                [
                    {
                        "type": "text",
                        "text": "Permission to use Bash has been denied.\n詳しい説明",
                    }
                ],
                is_error=True,
            ),
        ),
        (origin_mono + 3.5, "not a json line"),
        (
            origin_mono + 4.0,
            tool_use_line("toolu_03", "Skill", {"skill": "record-strategy"}),
        ),
        (
            origin_mono + 4.25,
            tool_result_line("toolu_03", "Launching skill: record-strategy"),
        ),
        (
            origin_mono + 1.25 + STRATEGY_DURATION_S,
            stream_line(STRATEGY_RESULT_MESSAGE),
        ),
    ]


def controls_stream_lines(origin_mono: float) -> list[tuple[float, str]]:
    """実行中の controls の実行の stream.jsonl に書く (mono, 行) を返す (result の行は無い)。

    Read の呼び出しと結果と、Bash の呼び出しと、エラーの結果 (result の行も result.json も無いため、
    permission_denied にはならない)。
    """
    return [
        (
            origin_mono + 3.75,
            stream_line(
                {"type": "system", "subtype": "init", "session_id": "s-controls"}
            ),
        ),
        (
            origin_mono + 4.0,
            tool_use_line(
                "toolu_11", "Read", {"file_path": "game_notes/smb/controls.md"}
            ),
        ),
        (
            origin_mono + 4.5,
            tool_result_line("toolu_11", "- A: ジャンプ\n- B: ダッシュ"),
        ),
        (
            origin_mono + 5.0,
            tool_use_line("toolu_12", "Bash", {"command": "cat README.md"}),
        ),
        (
            origin_mono + 5.25,
            tool_result_line(
                "toolu_12",
                [{"type": "text", "text": "Permission to use Bash has been denied."}],
                is_error=True,
            ),
        ),
    ]


def write_dashboard_advisors(
    run_dir: Path, origin_mono: float
) -> list[tuple[float, dict]]:
    """strategy の完了した実行と、controls の実行中の実行のディレクトリーを書き、run.jsonl に書く行を返す。

    strategy の実行: write_advisor_inputs の入力、stream.jsonl、build_stream_result_record の result.json
    (所要時間 STRATEGY_DURATION_S、拒否 1 件)。run.jsonl には build_log_event の advisor の行を、要求を受け取った
    時刻 (1.25 秒) + 所要時間の経過秒で書く。
    controls の実行: write_advisor_inputs の入力と stream.jsonl だけ (result.json も advisor の行も無い)。
    run.jsonl には、その stream.jsonl の記録をやめた record_error の行 (claude_code_headless の
    build_record_error_event) を書く。
    """
    strategy_dir = run_dir / STRATEGY_RUN_NAME
    write_advisor_inputs(
        strategy_dir,
        AdvisorContext(
            game=GAME,
            images=[
                LabeledImage(
                    "過去の場面 1/1 (t=0.75 秒、0.50 秒前)", JPEG, origin_mono + 0.75
                )
            ],
            current_strategy=STRATEGY_NOTE_FIRST,
            operation_log_text="",
            trigger_probability=0.875,
            trigger_time_text="23:59:51",
        ),
        ROLES_BY_NAME["strategy"],
    )
    write_stream(strategy_dir / STREAM_FILE_NAME, strategy_stream_lines(origin_mono))
    strategy_record = build_stream_result_record(
        returncode=0,
        duration_s=STRATEGY_DURATION_S,
        timed_out=False,
        error=None,
        last_result=STRATEGY_RESULT_MESSAGE,
        stderr="",
    )
    write_json_file(strategy_dir / RESULT_FILE_NAME, strategy_record)

    controls_dir = run_dir / CONTROLS_RUN_NAME
    write_advisor_inputs(
        controls_dir,
        AdvisorContext(
            game=GAME,
            images=[
                LabeledImage(
                    "過去の場面 1/1 (t=0.75 秒、2.75 秒前)", JPEG, origin_mono + 0.75
                )
            ],
            current_strategy=STRATEGY_NOTE_FIRST,
            operation_log_text="",
            trigger_probability=0.75,
            trigger_time_text="23:59:53",
        ),
        ROLES_BY_NAME["controls"],
    )
    write_stream(controls_dir / STREAM_FILE_NAME, controls_stream_lines(origin_mono))
    return [
        (
            1.25 + STRATEGY_DURATION_S,
            build_log_event(strategy_dir, strategy_record, ROLES_BY_NAME["strategy"]),
        ),
        (
            ADVISOR_STEPS_RECORD_ERROR_T,
            build_advisor_record_error_event(controls_dir, RECORD_ERROR_MESSAGE),
        ),
    ]


def write_dashboard_run(runs_dir: Path, name: str = DASHBOARD_RUN_NAME) -> DashboardRun:
    """--record-dashboard の記録がそろった実行を runs_dir / name に書き、期待値の DashboardRun を返す。

    run.jsonl: start より前の record_error (dashboard_records の build_record_error_event)、start (args と
    origin_mono つき。args は第4章の run.parse_args の結果)、start のすぐ後に書いた時刻がわずかに前の
    record_error、decision (Pipeline.run_once の docstring のキー。number と advisor_accepted を含む)、
    decision_error、advisor、advisor_steps の record_error、capture_stopped、record_end (build_record_end_event)。
    start の time は 23:59:50.000 で、経過秒 10 秒より後の行は 00:00 台 (日付をまたぐ)。
    requests.jsonl・frames.jsonl・thumbs/・pad.jsonl・advisor の 2 つの実行のディレクトリーも書く。
    """
    run_dir = runs_dir / name
    run_dir.mkdir(parents=True)
    origin_mono = DASHBOARD_ORIGIN_MONO
    args = dict(vars(parse_agent_args(DASHBOARD_AGENT_ARGV)))
    frames = write_dashboard_frames(run_dir, origin_mono)
    write_dashboard_pad(run_dir, origin_mono)
    events: list[tuple[float, dict]] = [
        (EARLY_RECORD_ERROR_T, build_record_error_event("pad", RECORD_ERROR_MESSAGE)),
        (
            0.0,
            {
                "event": "start",
                "game": GAME,
                "model": "jev",
                "run_dir": str(run_dir),
                "args": args,
                "origin_mono": origin_mono,
            },
        ),
        (
            RECORD_END_T,
            build_record_end_event(
                {"frames": len(DASHBOARD_FRAMES), "pad": len(DASHBOARD_PAD_REPORTS)},
                DASHBOARD_DROPPED,
            ),
        ),
    ]
    events += write_dashboard_requests(run_dir, origin_mono, frames)
    events += write_dashboard_advisors(run_dir, origin_mono)
    write_run_log(
        run_dir,
        DASHBOARD_START_SECONDS,
        events,
        right_after_start=[
            (
                SLIGHTLY_BEFORE_START_T,
                build_record_error_event("frames", RECORD_ERROR_MESSAGE),
            )
        ],
    )
    specs = dashboard_request_specs()
    return DashboardRun(
        name=name,
        run_dir=run_dir,
        origin_mono=origin_mono,
        start_time=DASHBOARD_START_TIME,
        args=args,
        threshold_advisor=DASHBOARD_THRESHOLD_ADVISOR,
        valley_ratio=DASHBOARD_VALLEY_RATIO,
        similarity_threshold=DASHBOARD_SIMILARITY_THRESHOLD,
        pressed_request=1,
        error_request=2,
        controls_request=3,
        strategy_changed_request=4,
        tips_changed_request=5,
        discarded_request=6,
        t_sent={spec.number: spec.sent for spec in specs},
        t_received={spec.number: spec.received for spec in specs},
        note_version_requests=[1, 4, 5],
        note_version_t=[1.0, 6.5, 12.375],
        strategy_run=STRATEGY_RUN_NAME,
        controls_run=CONTROLS_RUN_NAME,
        strategy_duration_s=STRATEGY_DURATION_S,
        frame_t=[t for t, _, _ in DASHBOARD_FRAMES],
        pad_t=[t for t, _, _, _, _ in DASHBOARD_PAD_REPORTS],
        jpeg_bytes=len(JPEG),
    )


# =====================================================================
# 4. 壊れた行のある実行
# =====================================================================

# frames.jsonl の最後に足す、改行で終わっていない (書いている途中の) 行
UNTERMINATED_FRAME_LINE = '{"mono": 1014.0, "sharpness": 220.0'


def write_broken_run(runs_dir: Path) -> DashboardRun:
    """write_dashboard_run と同じ記録を runs_dir / BROKEN_RUN_NAME に書き、読み飛ばす行を足す。

    足す行 (どれもファイルの最後):
        run.jsonl                         JSON として読めない行
        requests.jsonl                    JSON として読めない行
        frames.jsonl                      JSON として読めない行と、その後に改行で終わっていない行
                                          (UNTERMINATED_FRAME_LINE。読まず、読み飛ばした行にも数えない)
        pad.jsonl                         JSON として読めない行
        advisor-strategy-001/stream.jsonl JSON として読めない行
    読み飛ばす行は、どの種類も 1 行。
    """
    run = write_dashboard_run(runs_dir, BROKEN_RUN_NAME)
    run_dir = run.run_dir
    with (run_dir / RUN_LOG_FILE_NAME).open("a", encoding="utf-8") as file:
        file.write('{"time": "00:00:05.500", "event": \n')
    with (run_dir / REQUEST_LOG_FILE_NAME).open("a", encoding="utf-8") as file:
        file.write('{"event": "request", "number": 7,\n')
    with (run_dir / FRAMES_RECORD_FILE_NAME).open("a", encoding="utf-8") as file:
        file.write('{"mono": 1013.9, "sharpness": }\n')
        file.write(UNTERMINATED_FRAME_LINE)
    with (run_dir / PAD_RECORD_FILE_NAME).open("a", encoding="utf-8") as file:
        file.write('{"mono": 1013.0, "source": "apply"\n')
    with (run_dir / STRATEGY_RUN_NAME / STREAM_FILE_NAME).open(
        "a", encoding="utf-8"
    ) as file:
        file.write('{"mono": 1006.0, "data": {\n')
    return run


# =====================================================================
# 2. --record-dashboard も --save-requests も付けていない実行
# =====================================================================

NO_DASHBOARD_START_SECONDS = 10 * 3600
NO_DASHBOARD_START_TIME = "10:00:00.000"
TIPS_RUN_NAME = advisor_run_dir_name("tips", 1)
TIPS_DURATION_S = 6.5
# tips の実行の advisor の行の経過秒 (要求の記録が無いため、この行から開始と終了の時刻を求める)
TIPS_ADVISOR_LINE_T = 20.0
INTERRUPTED_T = 21.0
# tips の advisor を受け付けた要求の number (その decision の行の advisor_accepted に tips がある)
TIPS_REQUEST = 3
# decision の行 (経過秒, number, decide_s, advisor を受け付けた役割)
NO_DASHBOARD_DECISIONS = (
    (1.0, 1, 0.5, frozenset()),
    (2.0, 2, 0.25, frozenset()),
    (13.5, TIPS_REQUEST, 0.75, frozenset({"tips"})),
)
TIPS_CLAUDE_OUTPUT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Tips を更新した",
    "permission_denials": [],
}


@dataclass(frozen=True)
class NoDashboardRun:
    """write_no_dashboard_run が書いた実行。

    tips_run は advisor の実行のディレクトリー名 (result.json はあり、stream.jsonl は無い)。
    tips_request は、その実行を受け付けた要求の number (run.jsonl の decision の行の advisor_accepted に tips がある。
    requests.jsonl が無いので、その要求の行は無い)。
    tips_t_end は run.jsonl の advisor の行の経過秒、tips_duration_s は result.json の所要時間。
    decide_s は run.jsonl の decision の行の decide_s (ファイルの順)。
    """

    name: str
    run_dir: Path
    start_time: str
    tips_run: str
    tips_request: int
    tips_t_end: float
    tips_duration_s: float
    decide_s: list[float]
    last_event_t: float


def write_no_dashboard_run(runs_dir: Path) -> NoDashboardRun:
    """--record-dashboard も --save-requests も付けていない実行を runs_dir / NO_DASHBOARD_RUN_NAME に書く。

    run.jsonl (start は args と origin_mono を持たない。decision、advisor、interrupted の行。decision の行は
    Pipeline.run_once の docstring のキーで、TIPS_REQUEST の行の advisor_accepted に tips を入れる) と、advisor の
    実行のディレクトリー (write_advisor_inputs の入力と build_result_record の result.json。stream.jsonl は無い)
    だけを書く。requests.jsonl・frames.jsonl・pad.jsonl は無い。
    """
    run_dir = runs_dir / NO_DASHBOARD_RUN_NAME
    run_dir.mkdir(parents=True)
    tips_dir = run_dir / TIPS_RUN_NAME
    write_advisor_inputs(
        tips_dir,
        AdvisorContext(
            game=GAME,
            images=[LabeledImage("直近 1/1 (t=13.40 秒、0.10 秒前)", JPEG, 13.4)],
            current_strategy="",
            operation_log_text="",
            trigger_probability=0.9,
            trigger_time_text="10:00:13",
        ),
        ROLES_BY_NAME["tips"],
    )
    tips_record = build_result_record(
        returncode=0,
        duration_s=TIPS_DURATION_S,
        timed_out=False,
        error=None,
        stdout=json.dumps(TIPS_CLAUDE_OUTPUT, ensure_ascii=False),
        stderr="",
    )
    write_json_file(tips_dir / RESULT_FILE_NAME, tips_record)
    events: list[tuple[float, dict]] = [
        (
            0.0,
            {
                "event": "start",
                "game": GAME,
                "model": "decisions",
                "run_dir": str(run_dir),
            },
        ),
        (
            TIPS_ADVISOR_LINE_T,
            build_log_event(tips_dir, tips_record, ROLES_BY_NAME["tips"]),
        ),
        (INTERRUPTED_T, {"event": "interrupted"}),
    ]
    for t, number, decide_s, accepted in NO_DASHBOARD_DECISIONS:
        events.append(
            (
                t,
                {
                    "event": "decision",
                    "number": number,
                    "interval_wait_s": 0.0,
                    "select_s": 0.0,
                    "encode_s": 0.125,
                    "decide_s": decide_s,
                    "apply_s": DECISION_APPLY_S,
                    "images": 1,
                    "payload_bytes": len(JPEG),
                    "action": "押下 none / 短押し none",
                    "advisor_probabilities": {
                        "controls": 0.0,
                        "strategy": 0.0,
                        "advice": 0.0,
                        "tips": 0.9 if accepted else 0.0,
                    },
                    "advisor_requested": role_names_in_order(accepted),
                    "advisor_accepted": role_names_in_order(accepted),
                    "refusals": 0,
                },
            )
        )
    write_run_log(run_dir, NO_DASHBOARD_START_SECONDS, events)
    return NoDashboardRun(
        name=NO_DASHBOARD_RUN_NAME,
        run_dir=run_dir,
        start_time=NO_DASHBOARD_START_TIME,
        tips_run=TIPS_RUN_NAME,
        tips_request=TIPS_REQUEST,
        tips_t_end=TIPS_ADVISOR_LINE_T,
        tips_duration_s=TIPS_DURATION_S,
        decide_s=[decide_s for _, _, decide_s, _ in NO_DASHBOARD_DECISIONS],
        last_event_t=INTERRUPTED_T,
    )


# =====================================================================
# 3. --save-requests だけを付けた実行
# =====================================================================

SAVE_REQUESTS_ORIGIN_MONO = 500.0
SAVE_REQUESTS_START_SECONDS = 11 * 3600


@dataclass(frozen=True)
class SaveRequestsRun:
    """write_save_requests_run が書いた実行。

    request_files は要求の number → 要求の画像のファイル (実行のディレクトリーからの相対パス)、
    t_sent は要求の number → 送信時刻の経過秒、image_t は要求の number → 画像の経過秒。
    """

    name: str
    run_dir: Path
    request_files: dict[int, str]
    t_sent: dict[int, float]
    image_t: dict[int, float]
    jpeg_bytes: int


def write_save_requests_run(runs_dir: Path) -> SaveRequestsRun:
    """--save-requests だけを付けた実行を runs_dir / SAVE_REQUESTS_RUN_NAME に書く。

    run.jsonl (start は origin_mono と args を持たない) と、RequestLog(run_dir) (save_frames=True、
    record_thumbs=False) の write_header と write で書いた requests.jsonl と frames/ だけ。
    要求は 2 件で、1 件目は A の押下、2 件目は B の短押し。
    """
    run_dir = runs_dir / SAVE_REQUESTS_RUN_NAME
    run_dir.mkdir(parents=True)
    origin_mono = SAVE_REQUESTS_ORIGIN_MONO
    questions = build_questions()
    request_log = RequestLog(run_dir)
    request_log.write_header("jev", build_instructions(), questions)
    operation_log = OperationLog()
    specs = [
        (1, 1.0, 1.25, 1.5, 0.75, {"button_A": {"none": 0.0, "hold": 1.0, "tap": 0.0}}),
        (2, 2.0, 2.25, 2.5, 1.75, {"button_B": {"none": 0.0, "hold": 0.0, "tap": 1.0}}),
    ]
    events: list[tuple[float, dict]] = [
        (0.0, {"event": "start", "game": GAME, "model": "jev", "run_dir": str(run_dir)})
    ]
    request_files: dict[int, str] = {}
    for number, sent, encoded, received, frame_t, chosen in specs:
        decision = make_decision(chosen)
        frame = make_frame(origin_mono, frame_t, 100.0, 50)
        request = build_decision_request(
            frame,
            origin_mono + sent,
            FIRST_NOTES,
            questions,
            encode_frame,
            origin=origin_mono,
            recent_operations=operation_log.recent(DEFAULT_HISTORY_COUNT),
        )
        action = decide_action(
            decision.probabilities, decision.choices, questions, Thresholds()
        )
        request_log.write(
            RequestLogEntry(
                number=number,
                origin=origin_mono,
                sent_at=origin_mono + sent,
                encoded_at=origin_mono + encoded,
                received_at=origin_mono + received,
                request=request,
                decision=decision,
                action=action,
                advisor_accepted=frozenset(),
                result="ok",
                error=None,
            )
        )
        operation_log.add(
            OperationRecord(origin_mono + sent, origin_mono + received, action)
        )
        request_files[number] = f"frames/{frame_file_name(frame_t)}"
        spec = RequestSpec(
            number,
            sent,
            encoded,
            received,
            frame_t,
            FIRST_NOTES,
            decision,
            "ok",
            None,
            frozenset(),
        )
        events.append((received, decision_event(spec, action)))
    write_run_log(run_dir, SAVE_REQUESTS_START_SECONDS, events)
    return SaveRequestsRun(
        name=SAVE_REQUESTS_RUN_NAME,
        run_dir=run_dir,
        request_files=request_files,
        t_sent={number: sent for number, sent, _, _, _, _ in specs},
        image_t={number: frame_t for number, _, _, _, frame_t, _ in specs},
        jpeg_bytes=len(JPEG),
    )


# =====================================================================
# 5・6. 古い形の実行 (手で書く)
# =====================================================================

OLD_ADVISOR_DIR_NAME = "advisor-001"
OLD_STATE_TEXT = (
    "## ゲームの操作方法\n(まだ書かれていない)\n\n## 攻略情報・Tips\n(まだ書かれていない)\n\n"
    "## 進行状況\n(まだ書かれていない)\n\n## 戦略\n右へ進む"
)
OLD_ADVISOR_QUESTION_TEXT = (
    "今、advisor (戦略を考える上位のモデル) に戦略の更新を求めるべきか。"
    "今の戦略が画面の状況に合わないとき、新しい場面に入ったときに真にする。"
)
# decision の行 (経過秒, number, decide_s)。中央値は 0.75
OLD_DECISIONS = ((1.5, 1, 0.5), (3.0, 2, 1.0))


@dataclass(frozen=True)
class OldFormatRun:
    """write_no_answer_type_run と write_single_advisor_question_run が書いた実行。

    advisor_dir_name は役割を持たない形の advisor のディレクトリー名 (advisor-001)。
    request_numbers は requests.jsonl の要求の number、decide_s は run.jsonl の decision の行の decide_s。
    """

    name: str
    run_dir: Path
    advisor_dir_name: str
    request_numbers: list[int]
    decide_s: list[float]
    question_count: int


def old_image_t(number: int) -> float:
    """古い形の実行の、number の要求に載せた画像の経過秒 (送信時刻の 0.1 秒前) を返す。"""
    return number - 0.1


def write_old_format_common(run_dir: Path, start_seconds: float) -> None:
    """古い形の 2 つの実行に共通の run.jsonl と、advisor-001/ と、frames/ の画像を書く。

    run.jsonl の decision と advisor の行は、advisor の役割 (role) を持たない形のキー (advisor_probability、
    真偽値の advisor_requested と advisor_accepted) で書く。
    """
    advisor_dir = run_dir / OLD_ADVISOR_DIR_NAME
    advisor_dir.mkdir()
    (advisor_dir / "context.md").write_text("# advisor への文脈\n", encoding="utf-8")
    write_json_file(
        advisor_dir / RESULT_FILE_NAME,
        {
            "returncode": 0,
            "duration_s": 3.0,
            "timed_out": False,
            "error": None,
            "output": {
                "type": "result",
                "result": "戦略を更新した",
                "permission_denials": [],
            },
            "stdout": None,
            "stderr": "",
            "permission_denials": [],
        },
    )
    frames_dir = run_dir / "frames"
    frames_dir.mkdir()
    for number in (1, 2):
        (frames_dir / frame_file_name(old_image_t(number))).write_bytes(JPEG)
    events: list[tuple[float, dict]] = [
        (
            0.0,
            {"event": "start", "game": GAME, "model": "jev", "run_dir": str(run_dir)},
        ),
        (
            5.0,
            {
                "event": "advisor",
                "run_dir": str(advisor_dir),
                "returncode": 0,
                "duration_s": 3.0,
                "timed_out": False,
                "error": None,
                "permission_denials": [],
                "result": "戦略を更新した",
            },
        ),
    ]
    for t, number, decide_s in OLD_DECISIONS:
        events.append(
            (
                t,
                {
                    "event": "decision",
                    "number": number,
                    "select_s": 0.0,
                    "encode_s": 0.25,
                    "decide_s": decide_s,
                    "apply_s": 0.0,
                    "images": 1,
                    "payload_bytes": len(JPEG),
                    "action": "押下 A / 短押し none",
                    "advisor_probability": 0.75 if number == 1 else 0.0,
                    "advisor_requested": number == 1,
                    "advisor_accepted": number == 1,
                },
            )
        )
    write_run_log(run_dir, start_seconds, events)


def old_request_line(
    number: int, probabilities: dict, extra: Mapping[str, object]
) -> dict:
    """古い形の requests.jsonl の要求の行を返す。extra のキーで行を足す・置き換える。

    受信の経過秒 (t_received) と decide_s は、OLD_DECISIONS の同じ number の decision の行の経過秒と decide_s に
    そろえる (第4章では、decision の行を応答を受け取った直後に書くため)。t_sent は t_received - decide_s。
    """
    t_received, decide_s = next(
        (t, decide_s) for t, decided, decide_s in OLD_DECISIONS if decided == number
    )
    t_sent = t_received - decide_s
    image_t = old_image_t(number)
    return {
        "event": "request",
        "number": number,
        "time": format_clock(9 * 3600 + t_sent),
        "t_sent": t_sent,
        "t_received": t_received,
        "decide_s": decide_s,
        "result": "ok",
        "error": None,
        "images": [
            {
                "label": f"最新 (t={image_t:.2f} 秒、0.10 秒前)",
                "t": image_t,
                "file": f"frames/{frame_file_name(image_t)}",
                "bytes": len(JPEG),
                "after_previous_operation": number > 1,
            }
        ],
        "state_text": OLD_STATE_TEXT,
        "history_text": "## 直近の応答と送った操作 (古い順、最大 5 回)\n(まだ操作を送っていない)",
        "previous_operation_text": None,
        "probabilities": probabilities,
        "refusals": 0,
        "raw_response": None,
        "action": "押下 A / 短押し none",
        "advisor_requested": number == 1,
        "advisor_probability": 0.75 if number == 1 else 0.0,
        "advisor_accepted": number == 1,
        **extra,
    }


def write_no_answer_type_run(runs_dir: Path) -> OldFormatRun:
    """header の問いに answer_type が無い形の実行を runs_dir / NO_ANSWER_TYPE_RUN_NAME に書く (手で書く)。

    header の問いは {"name", "text", "kind"} だけで、ボタンの押下と短押しが別々の確率の問い (press_A・tap_A) の形。
    要求の行は probabilities (choices は無い)・真偽値の advisor_requested・advisor_probability・
    真偽値の advisor_accepted を持つ。advisor のディレクトリーは advisor-001/。
    """
    run_dir = runs_dir / NO_ANSWER_TYPE_RUN_NAME
    run_dir.mkdir(parents=True)
    questions = [
        {
            "name": "press_A",
            "text": "今、A ボタンを押した状態にするべきか。押した状態は次の判断まで続く。",
            "kind": "press",
        },
        {
            "name": "tap_A",
            "text": "今、A ボタンを 1 回だけ短く押すべきか。",
            "kind": "tap",
        },
        {
            "name": "stick_LS_UP",
            "text": "今、左スティックを上に倒すべきか。",
            "kind": "stick",
        },
        {"name": "advisor", "text": OLD_ADVISOR_QUESTION_TEXT, "kind": "advisor"},
    ]
    path = run_dir / REQUEST_LOG_FILE_NAME
    append_json_line(
        path,
        {
            "event": "header",
            "time": "09:00:00.010",
            "model": "jev",
            "instructions": "あなたは Nintendo Switch 2 のゲームを操作するコントローラーの判断をする。",
            "questions": questions,
        },
    )
    for number in (1, 2):
        probabilities = {
            "press_A": 0.75,
            "tap_A": 0.125,
            "stick_LS_UP": 0.0,
            "advisor": 0.75 if number == 1 else 0.0,
        }
        append_json_line(path, old_request_line(number, probabilities, {}))
    write_old_format_common(run_dir, 9 * 3600)
    return OldFormatRun(
        name=NO_ANSWER_TYPE_RUN_NAME,
        run_dir=run_dir,
        advisor_dir_name=OLD_ADVISOR_DIR_NAME,
        request_numbers=[1, 2],
        decide_s=[decide_s for _, _, decide_s in OLD_DECISIONS],
        question_count=len(questions),
    )


def write_single_advisor_question_run(runs_dir: Path) -> OldFormatRun:
    """advisor の問いが name `advisor` の 1 問の形の実行を runs_dir / SINGLE_ADVISOR_QUESTION_RUN_NAME に書く (手で書く)。

    header の問いは answer_type と choices を持ち、ボタンとスティックの問いは今の build_questions() と同じで、
    advisor の問いだけが name `advisor` の 1 問 (34 問)。要求の行は probabilities・choices・真偽値の
    advisor_requested・advisor_probability・真偽値の advisor_accepted を持つ。advisor のディレクトリーは advisor-001/。
    """
    run_dir = runs_dir / SINGLE_ADVISOR_QUESTION_RUN_NAME
    run_dir.mkdir(parents=True)
    questions = [
        {
            "name": question.name,
            "text": question.text,
            "kind": question.kind,
            "answer_type": question.answer_type,
            "choices": [
                {"name": choice.name, "description": choice.description}
                for choice in question.choices
            ],
        }
        for question in build_questions()
        if question.kind != "advisor"
    ]
    questions.append(
        {
            "name": "advisor",
            "text": OLD_ADVISOR_QUESTION_TEXT,
            "kind": "advisor",
            "answer_type": "probability",
            "choices": [],
        }
    )
    path = run_dir / REQUEST_LOG_FILE_NAME
    append_json_line(
        path,
        {
            "event": "header",
            "time": "09:00:00.010",
            "model": "jev",
            "instructions": "あなたは Nintendo Switch 2 のゲームを操作するコントローラーの判断をする。",
            "questions": questions,
        },
    )
    advisor_names = {role.question_name for role in ADVISOR_ROLES}
    for number in (1, 2):
        probabilities, choices = neutral_answers()
        probabilities = {
            name: value
            for name, value in probabilities.items()
            if name not in advisor_names
        }
        probabilities["advisor"] = 0.75 if number == 1 else 0.0
        choices["button_A"] = {"none": 0.0, "hold": 1.0, "tap": 0.0}
        append_json_line(
            path, old_request_line(number, probabilities, {"choices": choices})
        )
    write_old_format_common(run_dir, 9 * 3600)
    return OldFormatRun(
        name=SINGLE_ADVISOR_QUESTION_RUN_NAME,
        run_dir=run_dir,
        advisor_dir_name=OLD_ADVISOR_DIR_NAME,
        request_numbers=[1, 2],
        decide_s=[decide_s for _, _, decide_s in OLD_DECISIONS],
        question_count=len(questions),
    )


# =====================================================================
# 6 つをまとめて書く
# =====================================================================


@dataclass(frozen=True)
class SyntheticRuns:
    """write_all_runs が書いた 6 つの実行。"""

    runs_dir: Path
    dashboard: DashboardRun
    no_dashboard: NoDashboardRun
    save_requests: SaveRequestsRun
    broken: DashboardRun
    no_answer_type: OldFormatRun
    single_advisor_question: OldFormatRun


def write_all_runs(runs_dir: Path) -> SyntheticRuns:
    """runs_dir (無ければ作る) に 6 つの合成の実行を書き、SyntheticRuns を返す。"""
    runs_dir.mkdir(parents=True, exist_ok=True)
    return SyntheticRuns(
        runs_dir=runs_dir,
        dashboard=write_dashboard_run(runs_dir),
        no_dashboard=write_no_dashboard_run(runs_dir),
        save_requests=write_save_requests_run(runs_dir),
        broken=write_broken_run(runs_dir),
        no_answer_type=write_no_answer_type_run(runs_dir),
        single_advisor_question=write_single_advisor_question_run(runs_dir),
    )
