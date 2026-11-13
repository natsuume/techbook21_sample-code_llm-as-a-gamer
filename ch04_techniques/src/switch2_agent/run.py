"""第4章のエージェント (switch2-agent) を起動する。

1 つのプロセスで、キャプチャー (CaptureWithHistory) とその見張り (CaptureWatcher)、コントローラーのブリッジ
(BridgeSupervisor)、押下状態の送信 (PadOutput)、advisor (ClaudeCodeHeadlessAdvisor)、Decision Model、
判断のループ (Pipeline) を動かす。
Decision Model が TypeSafe Jev (--model jev) なら、直前の応答が返ってから jev.JEV_MIN_REQUEST_INTERVAL_S 秒 (0.2 秒)
空けて次の要求を送る (Pipeline の min_request_interval_s)。OpenAI Decisions API (--model decisions) では空けない。
Ctrl+C で止まる。

起動引数:
    --game NAME                  ゲーム名 (必須)。ゲームの情報は game_notes/<NAME>/ に置く
    --model {jev,decisions}      Decision Model (必須)。jev は TypeSafe Jev、decisions は OpenAI Decisions API
    --ffmpeg-fps N               ffmpeg の出力のフレームレート (整数 1〜60、既定 30)
    --process-fps N              間引いて残すフレームレート (整数 1〜ffmpeg_fps、既定 10)
    --history-seconds S          フレームの履歴を持つ秒数 (0 より大きい有限の値、既定 5.0)
    --image-width N              要求に載せる画像の幅 (整数 498〜1280、既定 1280)
    --jpeg-quality N             要求に載せる画像の JPEG の品質 (整数 1〜100、既定 85)
    --threshold-advisor P        advisor の問いのしきい値 (0〜1、既定 0.5)。ボタンとスティックの問いは選択肢の問いと
                                 段階の確率の問いで決めるため、しきい値を持たない (action_decision.decide_action)
    --valley-ratio R             鮮明度の谷とみなす比 (0 と 1 を含まない範囲、既定 0.5)
    --similarity-threshold D     意味のあるフレームが似ているとみなす差分の上限 (0〜255、既定 0.15)
    --advisor-timeout S          advisor の 1 回の実行の上限 (秒。0 より大きい有限の値、既定 300)
    --max-consecutive-errors N   Decision Model の失敗がこの回数続いたら止まる (整数 1 以上、既定 5)
    --save-requests              要求ごとの入出力 (画像・ゲームの情報・問いごとの答え・応答の本文・操作) を
                                 runs/<実行>/requests.jsonl と frames/ に書く (容量が大きくなるため既定はオフ)
    --record-dashboard           ダッシュボードが読む記録を runs/<実行>/ に書く (既定はオフ): フレームの判定と縮小画像
                                 (frames.jsonl と thumbs/)、送ったレポート (pad.jsonl)、要求の行 (requests.jsonl。
                                 --save-requests が無ければ frames/ は書かない)、advisor の途中経過
                                 (advisor-<連番>/stream.jsonl)、run.jsonl の起動引数と時計の起点 (start の args と
                                 origin_mono) と、終了時の記録の件数 (record_end)
    範囲外の値と、--process-fps が --ffmpeg-fps より大きい組み合わせは起動時にエラーにする

使い方 (ch04_techniques ディレクトリーで実行する):
    uv run switch2-agent --game <名前> --model jev
"""

import argparse
import math
import os
import threading
import time
from pathlib import Path

from switch2_remote.bluetooth_radio import reset_bluetooth_radio
from switch2_remote.bridge_supervisor import BridgeSupervisor
from switch2_remote.capture import DEVICE_FPS, DEVICE_HEIGHT, DEVICE_WIDTH
from switch2_remote.controller_bridge import Bridge, BridgeStartError

from switch2_agent.action_decision import DEFAULT_THRESHOLD, Thresholds
from switch2_agent.advisors.claude_code_headless import (
    DEFAULT_ADVISOR_TIMEOUT_S,
    ClaudeCodeHeadlessAdvisor,
)
from switch2_agent.capture_watcher import CaptureWatcher
from switch2_agent.capture_with_history import CaptureWithHistory
from switch2_agent.dashboard_recorder import DashboardRecorder, feed_detector
from switch2_agent.decision_models import decisions_api, jev
from switch2_agent.decision_models.base import DecisionModel
from switch2_agent.decision_models.decisions_api import DecisionsApiModel
from switch2_agent.decision_models.jev import JevModel
from switch2_agent.decision_request import JpegCache, build_instructions
from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.even_interval import EvenIntervalSelector
from switch2_agent.frame_selection.meaningful_frames import (
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_VALLEY_RATIO,
    MeanAbsDiffSimilarity,
    MeaningfulFrameStore,
    SharpnessValleyDetector,
)
from switch2_agent.game_notes import ensure_game_notes, game_notes_dir, read_game_notes
from switch2_agent.image_metrics import nv12_to_jpeg
from switch2_agent.operation_log import OperationLog
from switch2_agent.pad_output import PadOutput
from switch2_agent.pipeline import (
    DEFAULT_MAX_CONSECUTIVE_ERRORS,
    DEFAULT_STALE_FRAME_TIMEOUT_S,
    Pipeline,
    PipelineParts,
)
from switch2_agent.questions import build_questions
from switch2_agent.request_log import RequestLog
from switch2_agent.run_log import RunLog

FFMPEG_FPS_MIN, FFMPEG_FPS_MAX = 1, DEVICE_FPS  # 第2章の daemon と同じ範囲
PROCESS_FPS_MIN = 1  # 上限は --ffmpeg-fps
# 画像の幅の範囲。Jev の画像は各辺 280px 以上が必要で、16:9 なら幅 498 以上。nv12_to_jpeg は縮小だけ行う
IMAGE_WIDTH_MIN, IMAGE_WIDTH_MAX = 498, DEVICE_WIDTH
JPEG_QUALITY_MIN, JPEG_QUALITY_MAX = 1, 100
# 意味のあるフレームの差分 (image_metrics.mean_abs_diff) が取る範囲
SIMILARITY_THRESHOLD_MIN, SIMILARITY_THRESHOLD_MAX = 0.0, 255.0
MODEL_CHOICES = ("jev", "decisions")
RUNS_DIR_NAME = "runs"  # 実行のログを置くディレクトリー (ch04_techniques の下)
NOTES_DIR_NAME = "game_notes"  # ゲームの情報を置くディレクトリー (ch04_techniques の下)
RUN_LOG_FILE_NAME = "run.jsonl"
DEFAULT_FFMPEG_FPS = 30
DEFAULT_PROCESS_FPS = 10
DEFAULT_HISTORY_SECONDS = 5.0
# 画像の幅と JPEG の品質の既定値。Decision Model の精度と要求の大きさの釣り合いは、実機と実際の API で確かめていない
DEFAULT_IMAGE_WIDTH = DEVICE_WIDTH
DEFAULT_JPEG_QUALITY = 85
# --model ごとの API キーの環境変数の名前
API_KEY_ENVS = {"jev": jev.API_KEY_ENV, "decisions": decisions_api.API_KEY_ENV}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """起動引数を解釈し、次の属性を持つ Namespace を返す。

    game (str)、model ("jev" / "decisions")、ffmpeg_fps (int、既定 30)、process_fps (int、既定 10)、
    history_seconds (float、既定 5.0)、image_width (int、既定 1280)、jpeg_quality (int、既定 85)、
    threshold_advisor (float、既定 0.5)、
    valley_ratio (float、既定 0.5)、similarity_threshold (float、既定 0.15)、advisor_timeout (float、既定 300.0)、
    max_consecutive_errors (int、既定 5)、save_requests (bool、既定 False。--save-requests を付けたら True)、
    record_dashboard (bool、既定 False。--record-dashboard を付けたら True)。

    argv が None なら sys.argv[1:] を使う。--game と --model は必須。範囲 (モジュールの docstring の起動引数) の外の値、
    数として読めない値、--process-fps が --ffmpeg-fps より大きい組み合わせは、第2章の daemon.parse_args と同じく
    parser.error で使い方とともに表示して終了する (SystemExit、終了コード 2)。
    """
    parser = argparse.ArgumentParser(
        prog="switch2-agent",
        description="型付きの判断を返すモデルと Claude Code の advisor で Switch 2 のゲームを遊ぶ",
    )
    parser.add_argument(
        "--game",
        required=True,
        help="ゲーム名 (英数字・_・-)。game_notes/<名前>/ に情報を置く",
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=MODEL_CHOICES,
        help="Decision Model (jev: TypeSafe Jev、decisions: OpenAI Decisions API)",
    )
    parser.add_argument(
        "--ffmpeg-fps",
        type=int,
        default=DEFAULT_FFMPEG_FPS,
        help=f"ffmpeg の出力のフレームレート ({FFMPEG_FPS_MIN}〜{FFMPEG_FPS_MAX})",
    )
    parser.add_argument(
        "--process-fps",
        type=int,
        default=DEFAULT_PROCESS_FPS,
        help=f"間引いて残すフレームレート ({PROCESS_FPS_MIN}〜ffmpeg_fps)",
    )
    parser.add_argument(
        "--history-seconds",
        type=float,
        default=DEFAULT_HISTORY_SECONDS,
        help="フレームの履歴を持つ秒数 (0 より大きい有限の値)",
    )
    parser.add_argument(
        "--image-width",
        type=int,
        default=DEFAULT_IMAGE_WIDTH,
        help=f"要求に載せる画像の幅 ({IMAGE_WIDTH_MIN}〜{IMAGE_WIDTH_MAX})",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=DEFAULT_JPEG_QUALITY,
        help=f"要求に載せる画像の JPEG の品質 ({JPEG_QUALITY_MIN}〜{JPEG_QUALITY_MAX})",
    )
    parser.add_argument(
        "--threshold-advisor",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="advisor の問いのしきい値 (0〜1)。確率がこれを超えたら真",
    )
    parser.add_argument(
        "--valley-ratio",
        type=float,
        default=DEFAULT_VALLEY_RATIO,
        help="鮮明度の谷とみなす比 (0 と 1 を含まない範囲)",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help=(
            "意味のあるフレームが似ているとみなす差分の上限 "
            f"({SIMILARITY_THRESHOLD_MIN:g}〜{SIMILARITY_THRESHOLD_MAX:g})"
        ),
    )
    parser.add_argument(
        "--advisor-timeout",
        type=float,
        default=DEFAULT_ADVISOR_TIMEOUT_S,
        help="advisor の 1 回の実行の上限 (秒。0 より大きい有限の値)",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_ERRORS,
        help="Decision Model の失敗がこの回数続いたら止まる (1 以上)",
    )
    parser.add_argument(
        "--save-requests",
        action="store_true",
        help=(
            "要求ごとの入出力 (画像・ゲームの情報・問いごとの答え・応答の本文・操作) を "
            "runs/<実行>/requests.jsonl と frames/ に書く。容量が大きくなるため既定はオフ"
        ),
    )
    parser.add_argument(
        "--record-dashboard",
        action="store_true",
        help=(
            "ダッシュボードが読む記録 (フレームの判定と縮小画像 frames.jsonl・thumbs/、"
            "送ったレポート pad.jsonl、要求の行 requests.jsonl、advisor の途中経過 "
            "advisor-<連番>/stream.jsonl、run.jsonl の起動引数と記録の件数) を "
            "runs/<実行>/ に書く。既定はオフ"
        ),
    )
    args = parser.parse_args(argv)
    for message in range_errors(args):
        parser.error(message)
    return args


def range_errors(args: argparse.Namespace) -> list[str]:
    """parse_args の結果の範囲の誤りを、起動引数の名前を含むメッセージのリストにする (誤りが無ければ空)。

    nan は、どの範囲にも入らないものとして扱う。--history-seconds と --advisor-timeout は有限の値に限り、inf も誤りにする。
    """
    errors = []
    if not FFMPEG_FPS_MIN <= args.ffmpeg_fps <= FFMPEG_FPS_MAX:
        errors.append(
            f"--ffmpeg-fps must be {FFMPEG_FPS_MIN}..{FFMPEG_FPS_MAX}: {args.ffmpeg_fps}"
        )
    if args.process_fps < PROCESS_FPS_MIN:
        errors.append(
            f"--process-fps must be {PROCESS_FPS_MIN} or more: {args.process_fps}"
        )
    elif args.process_fps > args.ffmpeg_fps:
        message = (
            f"--process-fps ({args.process_fps}) must not exceed "
            f"--ffmpeg-fps ({args.ffmpeg_fps})"
        )
        if args.process_fps == DEFAULT_PROCESS_FPS:
            message += f". --process-fps defaults to {DEFAULT_PROCESS_FPS}"
        errors.append(message)
    if not (math.isfinite(args.history_seconds) and args.history_seconds > 0):
        errors.append(
            "--history-seconds must be a finite number more than 0: "
            f"{args.history_seconds}"
        )
    if not IMAGE_WIDTH_MIN <= args.image_width <= IMAGE_WIDTH_MAX:
        errors.append(
            f"--image-width must be {IMAGE_WIDTH_MIN}..{IMAGE_WIDTH_MAX}: {args.image_width}"
        )
    if not JPEG_QUALITY_MIN <= args.jpeg_quality <= JPEG_QUALITY_MAX:
        errors.append(
            f"--jpeg-quality must be {JPEG_QUALITY_MIN}..{JPEG_QUALITY_MAX}: "
            f"{args.jpeg_quality}"
        )
    if not 0.0 <= args.threshold_advisor <= 1.0:
        errors.append(f"--threshold-advisor must be 0..1: {args.threshold_advisor}")
    if not 0.0 < args.valley_ratio < 1.0:
        errors.append(
            f"--valley-ratio must be between 0 and 1 (exclusive): {args.valley_ratio}"
        )
    if (
        not SIMILARITY_THRESHOLD_MIN
        <= args.similarity_threshold
        <= SIMILARITY_THRESHOLD_MAX
    ):
        errors.append(
            f"--similarity-threshold must be {SIMILARITY_THRESHOLD_MIN:g}.."
            f"{SIMILARITY_THRESHOLD_MAX:g}: {args.similarity_threshold}"
        )
    if not (math.isfinite(args.advisor_timeout) and args.advisor_timeout > 0):
        errors.append(
            "--advisor-timeout must be a finite number more than 0: "
            f"{args.advisor_timeout}"
        )
    if args.max_consecutive_errors < 1:
        errors.append(
            f"--max-consecutive-errors must be 1 or more: {args.max_consecutive_errors}"
        )
    return errors


def build_request_log(run_dir: Path, args: argparse.Namespace) -> RequestLog | None:
    """起動引数 args に応じて、要求ごとの入出力のログ (RequestLog) を作って返す。作らないなら None を返す。

    --record-dashboard が付いていれば RequestLog(run_dir, save_frames=args.save_requests, record_thumbs=True)
    (要求の行に縮小画像の名前 thumb を足す。--save-requests が無ければ frames/ を作らず、画像の file を null にする)。
    --save-requests だけなら RequestLog(run_dir) (要求の行と、要求に載せた画像 frames/ を書く)。
    どちらも無ければ None。
    """
    if args.record_dashboard:
        return RequestLog(run_dir, save_frames=args.save_requests, record_thumbs=True)
    if args.save_requests:
        return RequestLog(run_dir)
    return None


def main(argv: list[str] | None = None) -> None:
    """起動引数を解釈し、部品を用意して Pipeline を動かす。

    作業ディレクトリーは ch04_techniques を前提にする。Path.cwd() / ".claude" / "skills" が無ければ、
    ch04_techniques で実行するよう説明して終了する (SystemExit)。
    API キーは --model に応じた環境変数 (decisions_api.API_KEY_ENV / jev.API_KEY_ENV) から読み、
    無ければ説明して終了する。ゲーム名が不正 (game_notes.game_notes_dir の ValueError) なら説明して終了する。

    起動の順:
        1. ゲームの情報のファイルを用意する (game_notes.ensure_game_notes(cwd / NOTES_DIR_NAME, game))
        2. BridgeSupervisor(start_bridge=Bridge, reset_radio=reset_bluetooth_radio)。最初のブリッジの起動に
           失敗したら (BridgeStartError)、そのメッセージで終了する
        3. --record-dashboard が付いていれば、ダッシュボードの記録 recorder = DashboardRecorder(実行のディレクトリー,
           RunLog.write)。付いていなければ recorder は None
        4. PadOutput(supervisor)。recorder があれば PadOutput(supervisor, on_report=recorder.record_pad)
           (送ろうとしたレポートを pad.jsonl に書く)
        5. 意味のあるフレームの判定 (SharpnessValleyDetector(valley_ratio, MeanAbsDiffSimilarity(similarity_threshold)))
           と保管 (MeaningfulFrameStore)
        6. CaptureWithHistory(ffmpeg_fps, process_fps, FrameHistory(history_seconds), on_frame)。on_frame は
           dashboard_recorder.feed_detector(frame, 判定, 保管, recorder) を呼ぶ (判定の feed が返したフレームを
           保管に add し、recorder があれば判定の前後を frames.jsonl と thumbs/ に記録する)
        7. CaptureWatcher(capture_ended=capture.ended を返す関数, latest_frame_time=history.latest() の time
           (無ければ None) を返す関数, on_lost=output.stop_output, stale_timeout_s=pipeline.DEFAULT_STALE_FRAME_TIMEOUT_S)
        8. ClaudeCodeHeadlessAdvisor(cwd, ゲームの情報のディレクトリー, 実行のディレクトリー, game, advisor_timeout,
           hidden_env_names=API_KEY_ENVS の値 (Decision Model の API キーの環境変数。advisor の子プロセスに渡さない),
           log=RunLog.write, record_steps=--record-dashboard の値)
        9. Decision Model (JevModel / DecisionsApiModel)
        10. Pipeline (encode は image_width と jpeg_quality で nv12_to_jpeg する関数を包んだ JpegCache。
           bridge_failed は supervisor.state == "failed"、bridge_recovering は supervisor.state == "recovering"、
           capture_ended は capture.ended、capture_lost は watcher.lost。request_log は
           build_request_log(実行のディレクトリー, 起動引数)。min_request_interval_s は、
           --model が jev なら jev.JEV_MIN_REQUEST_INTERVAL_S、decisions なら 0.0)
        11. request_log があれば、Pipeline を作った後に request_log.write_header(args.model, build_instructions(),
           Pipeline に渡した問いの一覧) で requests.jsonl の header の行を書く
        12. run.jsonl に start の行 {"event": "start", "game", "model", "run_dir"} を書く。--record-dashboard が
           付いていれば、"args": vars(起動引数) (argparse の属性名をキーにした起動引数のすべて) と
           "origin_mono": pipeline.started_at (経過秒の起点の time.monotonic の値) も足す
    ログは cwd / RUNS_DIR_NAME / <YYYYmmdd-HHMMSS> / RUN_LOG_FILE_NAME に RunLog で書く (advisor の入出力も同じ実行のディレクトリーに置く)。
    --save-requests が付いていれば、要求ごとの入出力も同じ実行のディレクトリーの requests.jsonl と frames/ に書く。
    --record-dashboard が付いていれば、同じ実行のディレクトリーに、フレームの判定と縮小画像 (frames.jsonl と thumbs/)、
    送ったレポート (pad.jsonl)、要求の行 (requests.jsonl。--save-requests が無ければ frames/ は書かない)、
    advisor の途中経過 (advisor-<連番>/stream.jsonl) を書き、終了時に run.jsonl に record_end を書く。
    Ctrl+C (KeyboardInterrupt) で stop を立てて止まる。Pipeline が止まった理由の例外は、片付けの後にそのまま出す。
    終了時は、作れた部品だけを watcher.close()、output.neutral()、output.close()、advisor.close()、capture.close()、
    recorder.close()、supervisor.close() の順に閉じる (見張りが片付けの途中で on_lost を呼ばないよう、見張りを
    最初に止める。recorder は、記録を送ってくる PadOutput とキャプチャーを止めた後に閉じ、中立のレポートまで記録する)。
    """
    args = parse_args(argv)
    workdir = Path.cwd()
    if not (workdir / ".claude" / "skills").is_dir():
        raise SystemExit(
            f"{workdir / '.claude' / 'skills'} was not found. "
            "Run switch2-agent in the ch04_techniques directory"
        )
    api_key_env = API_KEY_ENVS[args.model]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise SystemExit(f"Set the API key of --model {args.model} in {api_key_env}")
    notes_root = workdir / NOTES_DIR_NAME
    try:
        note_paths = ensure_game_notes(notes_root, args.game)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    notes_dir = game_notes_dir(notes_root, args.game)
    run_dir = workdir / RUNS_DIR_NAME / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    run_log = RunLog(run_dir / RUN_LOG_FILE_NAME)

    try:
        supervisor = BridgeSupervisor(
            start_bridge=Bridge, reset_radio=reset_bluetooth_radio
        )
    except BridgeStartError as e:
        raise SystemExit(str(e)) from None
    output = None
    capture = None
    watcher = None
    advisor = None
    recorder = None
    stop = threading.Event()
    try:
        if args.record_dashboard:
            recorder = DashboardRecorder(run_dir, run_log.write)
            output = PadOutput(supervisor, on_report=recorder.record_pad)
        else:
            output = PadOutput(supervisor)
        detector = SharpnessValleyDetector(
            args.valley_ratio, MeanAbsDiffSimilarity(args.similarity_threshold)
        )
        store = MeaningfulFrameStore()

        def feed_frame(frame: TimedFrame) -> None:
            feed_detector(frame, detector, store, recorder)

        history = FrameHistory(args.history_seconds)
        capture = CaptureWithHistory(
            args.ffmpeg_fps, args.process_fps, history, feed_frame
        )
        running_capture = capture

        def latest_frame_time() -> float | None:
            latest = history.latest()
            return latest.time if latest is not None else None

        watcher = CaptureWatcher(
            capture_ended=lambda: running_capture.ended,
            latest_frame_time=latest_frame_time,
            on_lost=output.stop_output,
            stale_timeout_s=DEFAULT_STALE_FRAME_TIMEOUT_S,
        )
        advisor = ClaudeCodeHeadlessAdvisor(
            workdir,
            notes_dir,
            run_dir,
            args.game,
            args.advisor_timeout,
            hidden_env_names=tuple(API_KEY_ENVS.values()),
            log=run_log.write,
            record_steps=args.record_dashboard,
        )
        model: DecisionModel = (
            JevModel(api_key) if args.model == "jev" else DecisionsApiModel(api_key)
        )

        def encode(frame: TimedFrame) -> bytes:
            return nv12_to_jpeg(
                frame.nv12, DEVICE_HEIGHT, args.image_width, args.jpeg_quality
            )

        questions = build_questions()
        request_log = build_request_log(run_dir, args)
        pipeline = Pipeline(
            PipelineParts(
                history=history,
                store=store,
                selector=EvenIntervalSelector(),
                model=model,
                output=output,
                advisor=advisor,
                read_notes=lambda: read_game_notes(note_paths),
                questions=questions,
                thresholds=Thresholds(advisor=args.threshold_advisor),
                encode=JpegCache(encode),
                operation_log=OperationLog(),
                log=run_log.write,
                clock=time.monotonic,
                sleep=time.sleep,
                game=args.game,
                capture_ended=lambda: running_capture.ended,
                bridge_failed=lambda: supervisor.state == "failed",
                bridge_recovering=lambda: supervisor.state == "recovering",
                capture_lost=watcher.lost,
                request_log=request_log,
            ),
            max_consecutive_errors=args.max_consecutive_errors,
            min_request_interval_s=(
                jev.JEV_MIN_REQUEST_INTERVAL_S if args.model == "jev" else 0.0
            ),
        )
        if request_log is not None:
            request_log.write_header(args.model, build_instructions(), questions)
        start: dict[str, object] = {
            "event": "start",
            "game": args.game,
            "model": args.model,
            "run_dir": run_dir,
        }
        if args.record_dashboard:
            # ダッシュボードが、しきい値などの起動引数と、記録の時刻 (mono) の起点を読めるようにする
            start["args"] = dict(vars(args))
            start["origin_mono"] = pipeline.started_at
        run_log.write(start)
        try:
            pipeline.run(stop)
        except KeyboardInterrupt:
            stop.set()
            run_log.write({"event": "interrupted"})
    finally:
        if watcher is not None:
            watcher.close()
        if output is not None:
            output.neutral()
            output.close()
        if advisor is not None:
            advisor.close()
        if capture is not None:
            capture.close()
        if recorder is not None:
            recorder.close()
        supervisor.close()


if __name__ == "__main__":
    main()
