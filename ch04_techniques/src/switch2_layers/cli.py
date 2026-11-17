"""コマンド switch2-layers の入口。サブコマンド run / middle-once / deep-once を持つ。

    switch2-layers run [--mode advisor] --software NAME --user NAME [--runs-dir DIR] [--interval-fps N]
                       [--interval-width N]
                       [--max-surface-errors N] [--middle-interval S] [--middle-window S] [--max-middle-errors N]
                       [--deep-min-start-interval S] [--deep-auto-seconds S] [--deep-timeout S]
                       [--middle fake|luna] [--middle-effort none|low] [--surface fake|jev] [--threshold-advisor P]
                       [--box-requery-every N]
                       [--hold-margin P] [--surface-map text|none] [--deep claude|fake] [--safety jev|fake]
                       [--safety-interval S] [--threshold-safety P]
                       [--max-safety-errors N] [--progress-window S] [--stuck-advice-seconds S]
                       [--threshold-task P] [--task-stuck-seconds S] [--system-advice-seconds S]
        実機で 3 層を動かす (advisor 型。--mode の既定)。準備 (コントローラーの登録・指定のソフト (--software) を
        指定のユーザー (--user) で起動する) から始め、止めるときは片付けを行う (app と system_screen.run_phase の段階)。
        surface は --surface で選ぶ (既定 jev は TypeSafe Jev、fake は surface.fake_surface)。
        middle は --middle で選ぶ (既定 luna は gpt-6-luna、fake は middle.fake_middle)。本体の画面の middle も
        --middle で選ぶ (luna は middle.system_screen_middle.LunaSystemScreenMiddle、fake は操作なしで done を
        真で返し続けるフェイク)。deep は --deep で選ぶ (既定 claude = Claude Code、fake = deep.fake_deep)。
        安全の監視は --safety で選ぶ (既定 jev は TypeSafe Jev、fake は safety.fake_safety)
    switch2-layers run --mode orchestrator [--instruction TEXT | --instruction-file PATH]
                       [--orchestrator-effort low|medium|high|xhigh|max] [--rotate-tokens N] [--turn-timeout S]
                       [--max-decision-errors N] [--monitor-threshold P] [--max-monitor-errors N]
                       [--runs-dir DIR] [--interval-fps N] [--interval-width N] [--safety jev|fake] [--safety-interval S]
                       [--threshold-safety P] [--max-safety-errors N] [--progress-window S]
        実機で Orchestrator 型を動かす。主ループの Claude Code (orchestrator.main_loop) と、操作の実行と監視
        (orchestrator.orchestrator_controller) を、キャプチャー・安全の監視・停滞の計測と組み立てる (app)。
        準備と片付けの段階は使わない (ソフトの起動とユーザー選択は、主ループが本体の操作のスキルで行う)。
        起動するソフトと遊ぶユーザーは、人の初期指示 (--instruction か --instruction-file) に書く。--software と
        --user は使わない (指定しても動きは変わらない)。surface・middle・deep の引数も使わない
    switch2-layers run --mode delegate [--instruction TEXT | --instruction-file PATH]
                       [--orchestrator-effort low|medium|high|xhigh|max] [--rotate-tokens N] [--turn-timeout S]
                       [--max-decision-errors N] [--delegate-image-width N] [--max-delegate-errors N]
                       [--runs-dir DIR] [--interval-fps N] [--interval-width N] [--safety jev|fake] [--safety-interval S]
                       [--threshold-safety P] [--max-safety-errors N] [--progress-window S]
        実機で委任型を動かす。主ループの Claude Code (orchestrator.main_loop) と、操作の実行と委任のループ
        (orchestrator.orchestrator_controller の委任型と orchestrator.delegate_loop) を、キャプチャー・安全の監視・
        停滞の計測と組み立てる (app)。主ループはリアルタイムの場面の操作を Jev に委任する。監視は使わない
        (--monitor-threshold と --max-monitor-errors は使わない)。そのほかは --mode orchestrator と同じ
    switch2-layers middle-once --run-dir DIR [--middle-window S] [--middle fake|luna]
        保存済みの実行に対して middle を 1 回動かし、middle の出力ファイルを書く
    switch2-layers deep-once --run-dir DIR --doc KIND [--scene SCENE_ID] [--deep claude|fake]
        保存済みの実行に対して deep を 1 回動かす (きっかけ manual。deep は run と同じく --deep で選ぶ)
deep は notes/ の文書の本体を直接編集するため、文書を入れ替えるサブコマンドは無い。

--middle (run と middle-once) は middle の実装を選ぶ。既定の luna は gpt-6-luna を呼び (middle.luna_middle)、
fake は骨組みのフェイク (middle.fake_middle) を使う。luna で環境変数 OPENAI_API_KEY が無い (空を含む) ときは、
実機 (ブリッジ・キャプチャー) と実行ディレクトリーに触れる前に、標準エラー出力に書いて終了コード 2 を返す。
--middle-effort (run だけ) は、ゲーム用の middle (luna) の要求の reasoning effort を none か low から選ぶ (既定 none)。
本体の画面の middle の effort (low) は変えない。--middle fake では使わない (指定しても動きは変わらない)。
middle-once は既定の none で呼ぶ。
--surface (run) は surface の実装を選ぶ。既定の jev は TypeSafe Jev を呼び (surface.jev_surface)、fake は骨組みの
フェイク (surface.fake_surface) を使う。jev で環境変数 TYPESAFE_API_KEY が無い (空を含む) ときも、実機と実行
ディレクトリーに触れる前に、標準エラー出力に書いて終了コード 2 を返す。
--safety (run) は安全の監視の実装を選ぶ。既定の jev は TypeSafe Jev に問い (safety.jev_safety)、fake はすべての
確率を 0 として返す (safety.fake_safety)。jev で環境変数 TYPESAFE_API_KEY が無い (空を含む) ときも、--surface jev と
同じく終了コード 2 を返す。
run --mode orchestrator は、API キーとして環境変数 TYPESAFE_API_KEY だけを確かめる (監視は常に Jev を使うため、
--safety fake でも要る)。OPENAI_API_KEY は確かめない。無ければ、実機と実行ディレクトリーに触れる前に終了コード 2 を返す。
run --mode delegate も同じく、API キーとして環境変数 TYPESAFE_API_KEY だけを確かめる (委任は常に Jev を使うため)。

引数の形の誤り (知らない引数、数として読めない値、--doc・--middle・--middle-effort の知らない値、run --mode advisor の
--software・--user が無い・空か空白だけ、--instruction と --instruction-file の両方、など) は argparse が使い方とともに表示し、
SystemExit (終了コード 2) にする。値の範囲の誤りと人の初期指示の誤り (空か空白だけ、ファイルが読めない) は、
各サブコマンドの処理が標準エラー出力に書いて終了コード 2 を返す。
"""

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from switch2_remote.bluetooth_radio import reset_bluetooth_radio
from switch2_remote.bridge_supervisor import BridgeSupervisor
from switch2_remote.capture import Capture
from switch2_remote.controller_bridge import Bridge, BridgeStartError

from switch2_layers.app import (
    REASON_INTERRUPTED,
    LayersApp,
    LayersParts,
    OrchestratorLayersParts,
)
from switch2_layers.deep.claude_code_runner import ClaudeCodeDeepRunner
from switch2_layers.deep.deep_scheduler import DeepJobStatus, run_deep_once
from switch2_layers.deep.deep_types import DOCUMENT_KINDS, DeepRunner, DocumentKind
from switch2_layers.deep.fake_deep import FakeDeepRunner
from switch2_layers.file_io import format_json_line
from switch2_layers.hardware.capture_source import (
    LatestFrameReader,
    SwitchCaptureReader,
)
from switch2_layers.middle.fake_middle import FakeMiddleAnalyzer
from switch2_layers.middle.luna_middle import LunaMiddleAnalyzer
from switch2_layers.middle.middle_loop import run_middle_once
from switch2_layers.middle.middle_types import MiddleAnalyzer
from switch2_layers.middle.system_screen_middle import (
    FAKE_DONE_OUTPUT,
    FakeSystemScreenMiddle,
    LunaSystemScreenMiddle,
    SystemScreenMiddle,
)
from switch2_layers.orchestrator.delegate_loop import (
    DELEGATE_IMAGE_WIDTH_MAX,
    DELEGATE_IMAGE_WIDTH_MIN,
)
from switch2_layers.orchestrator.initial_instruction import (
    InitialInstructionError,
    load_initial_instruction,
)
from switch2_layers.safety.safety_assembly import build_safety_assembly
from switch2_layers.safety.safety_types import SafetyChecker
from switch2_layers.settings import (
    AUTOCOMPACT_MAX_TOKENS,
    CAPTURE_FFMPEG_FPS,
    CAPTURE_PROCESS_FPS,
    DEFAULT_BOX_REQUERY_EVERY,
    DEFAULT_DEEP_AUTO_SECONDS,
    DEFAULT_DEEP_MIN_START_INTERVAL_S,
    DEFAULT_DEEP_RUNNER,
    DEFAULT_DEEP_TIMEOUT_S,
    DEFAULT_DELEGATE_IMAGE_WIDTH,
    DEFAULT_HOLD_MARGIN,
    DEFAULT_INTERVAL_FPS,
    DEFAULT_INTERVAL_WIDTH,
    DEFAULT_MAX_DECISION_ERRORS,
    DEFAULT_MAX_DELEGATE_ERRORS,
    DEFAULT_MAX_MIDDLE_ERRORS,
    DEFAULT_MAX_MONITOR_ERRORS,
    DEFAULT_MAX_SAFETY_ERRORS,
    DEFAULT_MAX_SURFACE_ERRORS,
    DEFAULT_MIDDLE_EFFORT,
    DEFAULT_MIDDLE_INTERVAL_S,
    DEFAULT_MIDDLE_KIND,
    DEFAULT_MIDDLE_WINDOW_S,
    DEFAULT_MONITOR_THRESHOLD,
    DEFAULT_PROGRESS_WINDOW_S,
    DEFAULT_ROTATE_TOKENS,
    DEFAULT_RUN_MODE,
    DEFAULT_RUNS_DIR,
    DEFAULT_SAFETY,
    DEFAULT_SAFETY_INTERVAL_S,
    DEFAULT_STUCK_ADVICE_SECONDS,
    DEFAULT_SURFACE,
    DEFAULT_SURFACE_MAP,
    DEFAULT_SYSTEM_ADVICE_SECONDS,
    DEFAULT_TASK_STUCK_SECONDS,
    DEFAULT_THRESHOLD_ADVISOR,
    DEFAULT_THRESHOLD_SAFETY,
    DEFAULT_THRESHOLD_TASK,
    DEFAULT_TURN_TIMEOUT_S,
    INTERVAL_FPS_MAX,
    INTERVAL_WIDTH_MAX,
    INTERVAL_WIDTH_MIN,
    MIDDLE_INTERVAL_MAX_S,
    MIDDLE_INTERVAL_MIN_S,
    OPENAI_API_KEY_ENV,
    ORCHESTRATOR_EFFORT,
    ORCHESTRATOR_EFFORTS,
    SAFETY_INTERVAL_MAX_S,
    SAFETY_INTERVAL_MIN_S,
    TYPESAFE_API_KEY_ENV,
    DeepOnceSettings,
    DeepRunnerChoice,
    MiddleEffort,
    MiddleKind,
    MiddleOnceSettings,
    RunMode,
    RunSettings,
    SafetyKind,
    SurfaceKind,
    SurfaceMapMode,
    deep_once_settings_errors,
    delegate_api_key_errors,
    middle_once_settings_errors,
    openai_api_key_errors,
    orchestrator_api_key_errors,
    run_settings_errors,
    safety_api_key_errors,
    typesafe_api_key_errors,
)
from switch2_layers.surface.jev_client import JevClient
from switch2_layers.surface.surface_assembly import (
    SurfaceAssembly,
    build_surface_assembly,
)
from switch2_layers.system_screen.system_screen_references import SYSTEM_SCREEN_NAMES

PROG = "switch2-layers"
# 範囲の誤りで返す終了コード (argparse の使い方の誤りと同じ)
EXIT_USAGE = 2
# run --mode advisor だけで必須にする引数 (Namespace の属性名, 起動引数の名前)
ADVISOR_REQUIRED_OPTIONS: tuple[tuple[str, str], ...] = (
    ("software", "--software"),
    ("user", "--user"),
)


class LayersArgumentParser(argparse.ArgumentParser):
    """switch2-layers の最上位の ArgumentParser。run の --software と --user を、--mode advisor のときだけ必須にする。

    argparse の required は、ほかの引数 (--mode) の値を見て決められない。そのため、引数を解釈し終えた後
    (parse_known_args。parse_args もこれを通る) に確かめ、--mode advisor で無い引数があれば、run の parser の誤り
    (`the following arguments are required: --software, --user` の形。使い方を表示して SystemExit 2) にする。

    run_parser   サブコマンド run の ArgumentParser (build_parser が入れる)。None なら確かめない
    """

    run_parser: argparse.ArgumentParser | None = None

    def parse_known_args(
        self,
        args: list[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> tuple[argparse.Namespace, list[str]]:
        """引数を解釈し (argparse.ArgumentParser.parse_known_args)、run --mode advisor の必須の引数を確かめる。"""
        parsed, extras = super().parse_known_args(args, namespace)
        run_parser = self.run_parser
        if (
            run_parser is not None
            and parsed.command == "run"
            and parsed.mode == RunMode.ADVISOR.value
        ):
            missing = [
                option
                for name, option in ADVISOR_REQUIRED_OPTIONS
                if getattr(parsed, name) is None
            ]
            if missing:
                run_parser.error(
                    f"the following arguments are required: {', '.join(missing)}"
                )
        return parsed, extras


def build_parser() -> argparse.ArgumentParser:
    """3 つのサブコマンド (run・middle-once・deep-once) の引数を定義した ArgumentParser を返す。

    各サブコマンドの Namespace には、処理の関数 (handler: Namespace → 終了コード) を持たせる。
    サブコマンドは必須 (無ければ argparse が SystemExit 2 にする)。知らないサブコマンドは argparse が SystemExit 2 にする。
    最上位は LayersArgumentParser (run --mode advisor の --software と --user を必須にする)、サブコマンドの parser は
    argparse.ArgumentParser にする。
    """
    parser = LayersArgumentParser(
        prog=PROG,
        description=(
            "surface・middle・deep の 3 層 (advisor 型) か、Claude Code の主ループ (Orchestrator 型) で "
            "Nintendo Switch 2 のゲームを遊ぶ"
        ),
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        parser_class=argparse.ArgumentParser,
    )
    parser.run_parser = add_run_parser(commands)
    add_middle_once_parser(commands)
    add_deep_once_parser(commands)
    return parser


def add_run_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> argparse.ArgumentParser:
    """サブコマンド run の引数を定義し、run の ArgumentParser を返す。既定値は settings の DEFAULT_* と同じ。

    --mode は settings.RunMode の値 (advisor / orchestrator / delegate) から選ぶ (既定 advisor)。Namespace の mode には
    文字列が入る。
    --software NAME と --user NAME は、--mode advisor のときだけ必須 (無ければ LayersArgumentParser が argparse の誤り
    にする)。空か空白だけの値は、どちらの --mode でも argparse の誤り (SystemExit 2) にする。値は書かれたまま
    Namespace の software と user に入る (無ければ None)。
    --middle-effort は settings.MiddleEffort の値 (none / low) から選ぶ (既定 settings.DEFAULT_MIDDLE_EFFORT の none。
    ほかの値は argparse の invalid choice の誤り)。--middle (add_middle_kind_argument) の直後に足し、Namespace の
    middle_effort には文字列が入る (run_settings_from_args が MiddleEffort にする)。
    --surface は settings.SurfaceKind の値 (jev / fake) から選び (既定 jev)、--threshold-advisor は float (既定 0.5)、
    --box-requery-every は int (既定 5)、--hold-margin は float (既定 0.1)。
    --surface-map は settings.SurfaceMapMode の値 (text / none) から選ぶ (既定 text)。
    --safety は settings.SafetyKind の値 (jev / fake) から選び (既定 jev)、--safety-interval は float (既定 1.0)、
    --threshold-safety は float (既定 0.7)、--max-safety-errors は int (既定 3)。
    --threshold-task は float (既定 0.5)、--task-stuck-seconds は float (既定 30)。
    Orchestrator 型の引数 (add_orchestrator_arguments) と委任型の引数 (add_delegate_arguments) も足す。
    """
    parser = commands.add_parser(
        "run",
        help="実機で 3 層 (advisor 型)・Orchestrator 型・委任型のどれかを動かす",
        description=(
            "実機で動かす。--mode advisor (既定) は 3 層 (surface は --surface、middle は --middle、deep は --deep で"
            "選ぶ)、--mode orchestrator は Claude Code の主ループと、操作の実行と監視を動かす。--mode delegate は "
            "Claude Code の主ループと、操作の実行と委任のループを動かす。Ctrl+C で止まる"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in RunMode],
        default=DEFAULT_RUN_MODE.value,
        help=(
            "run の型。advisor は surface・middle・deep の 3 層、orchestrator は Claude Code の主ループと、"
            "操作の実行と監視、delegate は Claude Code の主ループと、操作の実行と委任のループを動かす "
            f"(既定 {DEFAULT_RUN_MODE.value})"
        ),
    )
    parser.add_argument(
        "--software",
        metavar="NAME",
        type=_non_blank_name,
        default=None,
        help=(
            "起動するソフトの名前 (HOME メニューの絵の上に出る名前。--mode advisor では必須。"
            "orchestrator では使わない)"
        ),
    )
    parser.add_argument(
        "--user",
        metavar="NAME",
        type=_non_blank_name,
        default=None,
        help=(
            "ソフトを遊ぶユーザーの名前 (ユーザー選択に出る名前。--mode advisor では必須。"
            "orchestrator では使わない)"
        ),
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help=f"実行ディレクトリーを作る親 (既定 {DEFAULT_RUNS_DIR})",
    )
    parser.add_argument(
        "--interval-fps",
        type=float,
        default=DEFAULT_INTERVAL_FPS,
        help=(
            "一定間隔のフレームを frames/interval/ に保存するフレームレート "
            f"(0 より大きく {INTERVAL_FPS_MAX:g} 以下。既定 {DEFAULT_INTERVAL_FPS:g})"
        ),
    )
    parser.add_argument(
        "--interval-width",
        type=int,
        default=DEFAULT_INTERVAL_WIDTH,
        help=(
            "一定間隔のフレームを縮小する幅 "
            f"({INTERVAL_WIDTH_MIN}〜{INTERVAL_WIDTH_MAX}。既定 {DEFAULT_INTERVAL_WIDTH})"
        ),
    )
    parser.add_argument(
        "--max-surface-errors",
        type=int,
        default=DEFAULT_MAX_SURFACE_ERRORS,
        help=(
            "surface の失敗がこの回数続いたら全体を止める "
            f"(1 以上。既定 {DEFAULT_MAX_SURFACE_ERRORS})"
        ),
    )
    parser.add_argument(
        "--middle-interval",
        type=float,
        default=DEFAULT_MIDDLE_INTERVAL_S,
        help=(
            "middle の前回の応答から次を始めるまでの秒数 "
            f"({MIDDLE_INTERVAL_MIN_S:g}〜{MIDDLE_INTERVAL_MAX_S:g}。"
            f"既定 {DEFAULT_MIDDLE_INTERVAL_S:g})"
        ),
    )
    add_middle_window_argument(parser)
    parser.add_argument(
        "--max-middle-errors",
        type=int,
        default=DEFAULT_MAX_MIDDLE_ERRORS,
        help=(
            "middle の失敗がこの回数続いたら全体を止める "
            f"(1 以上。既定 {DEFAULT_MAX_MIDDLE_ERRORS})"
        ),
    )
    parser.add_argument(
        "--deep-min-start-interval",
        type=float,
        default=DEFAULT_DEEP_MIN_START_INTERVAL_S,
        help=(
            "同じ文書の deep を、前回の起動からこの秒数たつまで、起動も保留もしない "
            f"(0 以上。既定 {DEFAULT_DEEP_MIN_START_INTERVAL_S:g})"
        ),
    )
    parser.add_argument(
        "--deep-auto-seconds",
        type=float,
        default=DEFAULT_DEEP_AUTO_SECONDS,
        help=(
            "同じ文書の前回の deep の終了からこの秒数たったら自動で deep を呼ぶ "
            "(0 より大きく、--deep-min-start-interval 以上の値。"
            f"既定 {DEFAULT_DEEP_AUTO_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--deep-timeout",
        type=float,
        default=DEFAULT_DEEP_TIMEOUT_S,
        help=(
            "deep のジョブの実行時間の上限 (秒) "
            f"(0 より大きい値。既定 {DEFAULT_DEEP_TIMEOUT_S:g})"
        ),
    )
    add_middle_kind_argument(parser)
    parser.add_argument(
        "--middle-effort",
        choices=[effort.value for effort in MiddleEffort],
        default=DEFAULT_MIDDLE_EFFORT.value,
        help=(
            "ゲーム用の middle (luna) の要求の reasoning effort。本体の画面の middle と --middle fake には使わない "
            f"(既定 {DEFAULT_MIDDLE_EFFORT.value})"
        ),
    )
    parser.add_argument(
        "--surface",
        choices=[kind.value for kind in SurfaceKind],
        default=DEFAULT_SURFACE.value,
        help=(
            "surface の実装。jev は TypeSafe Jev を呼び、環境変数 TYPESAFE_API_KEY が要る。"
            f"fake は中立の操作を返すフェイク (既定 {DEFAULT_SURFACE.value})"
        ),
    )
    parser.add_argument(
        "--threshold-advisor",
        type=float,
        default=DEFAULT_THRESHOLD_ADVISOR,
        help=(
            "surface の advisor 判定のしきい値。確率がこれを超えた文書の deep を求める "
            f"(0〜1。既定 {DEFAULT_THRESHOLD_ADVISOR:g})"
        ),
    )
    parser.add_argument(
        "--box-requery-every",
        type=int,
        default=DEFAULT_BOX_REQUERY_EVERY,
        help=(
            "追えている box も、前回の問い直しからこの回数の要求がたったら Jev に問い直す "
            f"(1 以上。既定 {DEFAULT_BOX_REQUERY_EVERY})"
        ),
    )
    parser.add_argument(
        "--hold-margin",
        type=float,
        default=DEFAULT_HOLD_MARGIN,
        help=(
            "surface のボタンと十字キーで、hold の確率が tap の確率をこの値より大きく上回ったときだけ押し続けにする "
            f"(0〜1。既定 {DEFAULT_HOLD_MARGIN:g})"
        ),
    )
    parser.add_argument(
        "--surface-map",
        choices=[mode.value for mode in SurfaceMapMode],
        default=DEFAULT_SURFACE_MAP.value,
        help=(
            "surface に現在地の場所のマップを渡すか。text は文字で渡し、none は渡さない "
            f"(既定 {DEFAULT_SURFACE_MAP.value})"
        ),
    )
    add_deep_runner_argument(parser)
    parser.add_argument(
        "--safety",
        choices=[kind.value for kind in SafetyKind],
        default=DEFAULT_SAFETY.value,
        help=(
            "安全の監視の実装。jev は TypeSafe Jev に問い、環境変数 TYPESAFE_API_KEY が要る。"
            f"fake はすべての確率を 0 として返すフェイク (既定 {DEFAULT_SAFETY.value})"
        ),
    )
    parser.add_argument(
        "--safety-interval",
        type=float,
        default=DEFAULT_SAFETY_INTERVAL_S,
        help=(
            "安全の監視が最新のフレームを確かめる間隔 (秒) "
            f"({SAFETY_INTERVAL_MIN_S:g}〜{SAFETY_INTERVAL_MAX_S:g}。"
            f"既定 {DEFAULT_SAFETY_INTERVAL_S:g})"
        ),
    )
    parser.add_argument(
        "--threshold-safety",
        type=float,
        default=DEFAULT_THRESHOLD_SAFETY,
        help=(
            "安全の問いのしきい値。確率がこれを超えた画面で止まる "
            f"(0 より大きく 1 より小さい値。既定 {DEFAULT_THRESHOLD_SAFETY:g})"
        ),
    )
    parser.add_argument(
        "--max-safety-errors",
        type=int,
        default=DEFAULT_MAX_SAFETY_ERRORS,
        help=(
            "安全の監視の失敗がこの回数続いたら全体を止める "
            f"(1 以上。既定 {DEFAULT_MAX_SAFETY_ERRORS})"
        ),
    )
    parser.add_argument(
        "--progress-window",
        type=float,
        default=DEFAULT_PROGRESS_WINDOW_S,
        help=(
            "画面の停滞の計測で、新しい画面かを決めるときに見る範囲 (直近の秒数。0 より大きい値。"
            f"既定 {DEFAULT_PROGRESS_WINDOW_S:g})"
        ),
    )
    parser.add_argument(
        "--stuck-advice-seconds",
        type=float,
        default=DEFAULT_STUCK_ADVICE_SECONDS,
        help=(
            "新しい画面が出ていない秒数がこれを超えたら、advice の deep を求める。超えたまま続く間は、"
            f"この秒数ごとに求める (0 より大きい値。既定 {DEFAULT_STUCK_ADVICE_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--threshold-task",
        type=float,
        default=DEFAULT_THRESHOLD_TASK,
        help=(
            "surface の先頭のタスクの完了と取り消しの要求の問いのしきい値。確率がこれを超えた要求が、"
            f"同じタスクについて続けて 2 回あったら成立にする (0〜1。既定 {DEFAULT_THRESHOLD_TASK:g})"
        ),
    )
    parser.add_argument(
        "--task-stuck-seconds",
        type=float,
        default=DEFAULT_TASK_STUCK_SECONDS,
        help=(
            "現在の場面の最も古いタスクがキューに入ってからの秒数がこれを超えたら、間隔を待たずに middle を呼ぶ。"
            "超えたまま続く間は、この秒数ごとに呼ぶ "
            f"(0 より大きい値。既定 {DEFAULT_TASK_STUCK_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--system-advice-seconds",
        type=float,
        default=DEFAULT_SYSTEM_ADVICE_SECONDS,
        help=(
            "本体の画面の段階 (準備・復帰) がこの秒数を超えて続いたら、advice の deep を求める。"
            "超えたまま続く間は、この秒数ごとに求める "
            f"(0 より大きい値。既定 {DEFAULT_SYSTEM_ADVICE_SECONDS:g})"
        ),
    )
    add_orchestrator_arguments(parser)
    add_delegate_arguments(parser)
    parser.set_defaults(handler=run_command)
    return parser


def add_orchestrator_arguments(parser: argparse.ArgumentParser) -> None:
    """run の parser に、--mode orchestrator (と、--monitor-* のほかは --mode delegate) で使う引数を足す (使い方の
    表示では 1 つのグループにまとめる)。

    --instruction TEXT と --instruction-file PATH は相互排他 (両方なら argparse の誤り)。Namespace の instruction
    (str か None) と instruction_file (Path か None) に入る。中身の確かめ (空か空白だけ・読めないファイル) は
    run_command が行う。
    --orchestrator-effort は settings.ORCHESTRATOR_EFFORTS から選び (既定 medium)、--rotate-tokens は int
    (既定 120000)、--turn-timeout は float (既定 120)、--max-decision-errors は int (既定 3)、--monitor-threshold は
    float (既定 0.5)、--max-monitor-errors は int (既定 5)。--monitor-threshold と --max-monitor-errors は
    --mode delegate では使わない。
    """
    group = parser.add_argument_group(
        "Orchestrator 型 (--mode orchestrator と --mode delegate) の引数",
        "--mode advisor では使わない (指定しても動きは変わらない)。--monitor-threshold と --max-monitor-errors は "
        "--mode delegate でも使わない",
    )
    instruction = group.add_mutually_exclusive_group()
    instruction.add_argument(
        "--instruction",
        metavar="TEXT",
        default=None,
        help=(
            "人の初期指示 (起動するソフトと遊ぶユーザーなど。ゲーム名を書いてよい)。"
            "--instruction-file と一緒には使えない"
        ),
    )
    instruction.add_argument(
        "--instruction-file",
        metavar="PATH",
        type=Path,
        default=None,
        help="人の初期指示を書いた UTF-8 のファイル。--instruction と一緒には使えない",
    )
    group.add_argument(
        "--orchestrator-effort",
        choices=ORCHESTRATOR_EFFORTS,
        default=ORCHESTRATOR_EFFORT,
        help=f"主ループの Claude Code の --effort (既定 {ORCHESTRATOR_EFFORT})",
    )
    group.add_argument(
        "--rotate-tokens",
        type=int,
        default=DEFAULT_ROTATE_TOKENS,
        help=(
            "主ループのセッションの使用量 (トークン) がこれを超えたら、引き継ぎを書かせてセッションを入れ替える "
            f"(1 以上 {AUTOCOMPACT_MAX_TOKENS} 未満。既定 {DEFAULT_ROTATE_TOKENS})"
        ),
    )
    group.add_argument(
        "--turn-timeout",
        type=float,
        default=DEFAULT_TURN_TIMEOUT_S,
        help=(
            "主ループの 1 ターンの上限の秒数 "
            f"(0 より大きい値。既定 {DEFAULT_TURN_TIMEOUT_S:g})"
        ),
    )
    group.add_argument(
        "--max-decision-errors",
        type=int,
        default=DEFAULT_MAX_DECISION_ERRORS,
        help=(
            "主ループの JSON の誤りが続けてこの回数になったら全体を止める "
            f"(1 以上。既定 {DEFAULT_MAX_DECISION_ERRORS})"
        ),
    )
    group.add_argument(
        "--monitor-threshold",
        type=float,
        default=DEFAULT_MONITOR_THRESHOLD,
        help=(
            "監視の当たりのしきい値。確率がこれを超えた条件の操作を行う "
            f"(0 より大きく 1 より小さい値。既定 {DEFAULT_MONITOR_THRESHOLD:g})"
        ),
    )
    group.add_argument(
        "--max-monitor-errors",
        type=int,
        default=DEFAULT_MAX_MONITOR_ERRORS,
        help=(
            "監視の Jev の失敗がこの回数続いたら監視を止める "
            f"(1 以上。既定 {DEFAULT_MAX_MONITOR_ERRORS})"
        ),
    )


def add_delegate_arguments(parser: argparse.ArgumentParser) -> None:
    """run の parser に、--mode delegate だけで使う引数を足す (使い方の表示では 1 つのグループにまとめる)。

    --delegate-image-width は int (既定 settings.DEFAULT_DELEGATE_IMAGE_WIDTH の 1280)、--max-delegate-errors は int
    (既定 settings.DEFAULT_MAX_DELEGATE_ERRORS の 5)。範囲は run_command が settings.run_settings_errors で、--mode に
    依らず確かめる。
    """
    group = parser.add_argument_group(
        "委任型 (--mode delegate) の引数",
        "--mode advisor と --mode orchestrator では使わない (指定しても動きは変わらない)",
    )
    group.add_argument(
        "--delegate-image-width",
        type=int,
        default=DEFAULT_DELEGATE_IMAGE_WIDTH,
        help=(
            "委任のループが Jev に送る画像の幅 "
            f"({DELEGATE_IMAGE_WIDTH_MIN}〜{DELEGATE_IMAGE_WIDTH_MAX} の整数。既定 {DEFAULT_DELEGATE_IMAGE_WIDTH})"
        ),
    )
    group.add_argument(
        "--max-delegate-errors",
        type=int,
        default=DEFAULT_MAX_DELEGATE_ERRORS,
        help=(
            "委任のループの Jev の失敗がこの回数続いたら、操作権を主ループに返す "
            f"(1 以上。既定 {DEFAULT_MAX_DELEGATE_ERRORS})"
        ),
    )


def _non_blank_name(value: str) -> str:
    """--software と --user の値を、書かれたまま返す。空か空白だけなら argparse.ArgumentTypeError にする。"""
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be empty or blank")
    return value


def add_middle_kind_argument(parser: argparse.ArgumentParser) -> None:
    """--middle の引数を parser に足す (run と middle-once で共通)。

    値は MiddleKind の値 ("fake" / "luna") から選び、既定は DEFAULT_MIDDLE_KIND の値 ("luna")。
    Namespace の middle には文字列のまま入る (*_settings_from_args が MiddleKind にする)。
    """
    parser.add_argument(
        "--middle",
        choices=[kind.value for kind in MiddleKind],
        default=DEFAULT_MIDDLE_KIND.value,
        help=(
            "middle の実装。luna は gpt-6-luna を呼び、環境変数 OPENAI_API_KEY が要る。"
            f"fake は固定の出力を返すフェイク (既定 {DEFAULT_MIDDLE_KIND.value})"
        ),
    )


def add_deep_runner_argument(parser: argparse.ArgumentParser) -> None:
    """--deep の引数を parser に足す (run と deep-once で共通)。

    値は DeepRunnerChoice の値 (claude・fake) から選び、既定は DEFAULT_DEEP_RUNNER の値 (claude)。
    Namespace の deep には文字列が入る (処理の関数が DeepRunnerChoice(args.deep) にする)。
    """
    parser.add_argument(
        "--deep",
        choices=[choice.value for choice in DeepRunnerChoice],
        default=DEFAULT_DEEP_RUNNER.value,
        help=(
            "deep のジョブを実行するもの (claude = Claude Code、fake = 何もせず成功を返すフェイク。"
            f"既定 {DEFAULT_DEEP_RUNNER.value})"
        ),
    )


def build_deep_runner(choice: DeepRunnerChoice) -> DeepRunner:
    """--deep の選択 choice の DeepRunner を作って返す。

    CLAUDE → deep.claude_code_runner.ClaudeCodeDeepRunner() (既定の設定。claude の起動は run を呼んだときに行う)
    FAKE   → deep.fake_deep.FakeDeepRunner()
    """
    if choice is DeepRunnerChoice.CLAUDE:
        return ClaudeCodeDeepRunner()
    return FakeDeepRunner()


def add_middle_window_argument(parser: argparse.ArgumentParser) -> None:
    """--middle-window の引数を parser に足す (run と middle-once で共通)。"""
    parser.add_argument(
        "--middle-window",
        type=float,
        default=DEFAULT_MIDDLE_WINDOW_S,
        help=(
            "middle に渡す一定間隔のフレームの範囲 (直近の秒数。0 より大きい値。"
            f"既定 {DEFAULT_MIDDLE_WINDOW_S:g})"
        ),
    )


def add_middle_once_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """サブコマンド middle-once の引数を定義する。"""
    parser = commands.add_parser(
        "middle-once",
        help="保存済みの実行に対して middle を 1 回動かす",
        description="保存済みの実行に対して middle を 1 回動かし、middle の出力ファイルを書く",
    )
    parser.add_argument(
        "--run-dir", type=Path, required=True, help="保存済みの実行ディレクトリー"
    )
    add_middle_window_argument(parser)
    add_middle_kind_argument(parser)
    parser.set_defaults(handler=middle_once_command)


def add_deep_once_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """サブコマンド deep-once の引数を定義する。--doc は DocumentKind の値から選ぶ。"""
    parser = commands.add_parser(
        "deep-once",
        help="保存済みの実行に対して deep を 1 回動かす",
        description="保存済みの実行に対して deep を 1 回動かす (きっかけ manual)",
    )
    parser.add_argument(
        "--run-dir", type=Path, required=True, help="保存済みの実行ディレクトリー"
    )
    parser.add_argument(
        "--doc",
        required=True,
        choices=[kind.value for kind in DOCUMENT_KINDS],
        help="文書の種類",
    )
    parser.add_argument(
        "--scene",
        metavar="SCENE_ID",
        default=None,
        help=(
            "場面 ID (scene-001 の形。controls と tips だけ)。"
            "付けなければ middle/state.json の現在の場面 (無ければ共通の文書) を対象にする"
        ),
    )
    add_deep_runner_argument(parser)
    parser.set_defaults(handler=deep_once_command)


def run_settings_from_args(args: argparse.Namespace) -> RunSettings:
    """run の Namespace を RunSettings にする (範囲は確かめない)。

    --software と --user は software_name と user_name にする (無い (None) ときは空文字列。--mode orchestrator と
    --mode delegate では使わないため無くてよい)。--middle は MiddleKind に、--middle-effort は MiddleEffort
    (middle_effort) に、--surface は SurfaceKind に、--surface-map は SurfaceMapMode に、
    --deep は DeepRunnerChoice に、--safety は SafetyKind に、--mode は RunMode にする。Orchestrator 型の 6 つの値と
    委任型の 2 つの値 (--delegate-image-width・--max-delegate-errors) は、--mode に依らず写す。
    --instruction と --instruction-file は写さない (run_command が orchestrator と delegate のときだけ読む)。
    """
    return RunSettings(
        software_name=args.software if args.software is not None else "",
        user_name=args.user if args.user is not None else "",
        runs_dir=args.runs_dir,
        interval_fps=args.interval_fps,
        interval_width=args.interval_width,
        max_surface_errors=args.max_surface_errors,
        middle_interval_s=args.middle_interval,
        middle_window_s=args.middle_window,
        max_middle_errors=args.max_middle_errors,
        deep_min_start_interval_s=args.deep_min_start_interval,
        deep_auto_seconds=args.deep_auto_seconds,
        deep_timeout_s=args.deep_timeout,
        middle=MiddleKind(args.middle),
        middle_effort=MiddleEffort(args.middle_effort),
        surface=SurfaceKind(args.surface),
        threshold_advisor=args.threshold_advisor,
        box_requery_every=args.box_requery_every,
        hold_margin=args.hold_margin,
        surface_map=SurfaceMapMode(args.surface_map),
        deep=DeepRunnerChoice(args.deep),
        safety=SafetyKind(args.safety),
        safety_interval_s=args.safety_interval,
        threshold_safety=args.threshold_safety,
        threshold_task=args.threshold_task,
        max_safety_errors=args.max_safety_errors,
        progress_window_s=args.progress_window,
        stuck_advice_seconds=args.stuck_advice_seconds,
        task_stuck_seconds=args.task_stuck_seconds,
        system_advice_seconds=args.system_advice_seconds,
        mode=RunMode(args.mode),
        orchestrator_effort=args.orchestrator_effort,
        rotate_tokens=args.rotate_tokens,
        turn_timeout_s=args.turn_timeout,
        max_decision_errors=args.max_decision_errors,
        monitor_threshold=args.monitor_threshold,
        max_monitor_errors=args.max_monitor_errors,
        delegate_image_width=args.delegate_image_width,
        max_delegate_errors=args.max_delegate_errors,
    )


def middle_once_settings_from_args(args: argparse.Namespace) -> MiddleOnceSettings:
    """middle-once の Namespace を MiddleOnceSettings にする (--middle は MiddleKind にする。範囲は確かめない)。"""
    return MiddleOnceSettings(
        run_dir=args.run_dir,
        middle_window_s=args.middle_window,
        middle=MiddleKind(args.middle),
    )


def deep_once_settings_from_args(args: argparse.Namespace) -> DeepOnceSettings:
    """deep-once の Namespace を DeepOnceSettings にする (--doc は DocumentKind にする。範囲は確かめない)。"""
    return DeepOnceSettings(
        run_dir=args.run_dir, kind=DocumentKind(args.doc), scene_id=args.scene
    )


def create_middle_analyzer(
    kind: MiddleKind,
    environ: Mapping[str, str],
    effort: MiddleEffort = DEFAULT_MIDDLE_EFFORT,
) -> MiddleAnalyzer:
    """--middle の値 kind の MiddleAnalyzer を作って返す。要求はまだ送らない。

    effort は 3 つ目の引数 (settings.MiddleEffort。既定 settings.DEFAULT_MIDDLE_EFFORT の none)。
    - MiddleKind.FAKE: middle.fake_middle.FakeMiddleAnalyzer() (environ と effort を見ない)
    - MiddleKind.LUNA: middle.luna_middle.LunaMiddleAnalyzer(api_key=environ[OPENAI_API_KEY_ENV] の前後の空白を
      除いたもの, reasoning_effort=effort の値の文字列)。キーが無いか空なら ValueError (呼ぶ前に
      settings.openai_api_key_errors で確かめておく)
    run_command は (settings.middle, os.environ, settings.middle_effort)、middle_once_command は (settings.middle,
    os.environ) を渡してこの関数で作る (middle-once の effort は既定の none)。テストはこの関数を
    (kind, environ) を受け取る関数に差し替えて、MockTransport の Client を持つ LunaMiddleAnalyzer を渡せる。
    """
    if kind is MiddleKind.FAKE:
        return FakeMiddleAnalyzer()
    api_key = environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key:
        raise ValueError(f"the environment variable {OPENAI_API_KEY_ENV} is not set")
    return LunaMiddleAnalyzer(api_key=api_key, reasoning_effort=effort.value)


def create_system_screen_middle(
    kind: MiddleKind, environ: Mapping[str, str]
) -> SystemScreenMiddle:
    """--middle の値 kind の、本体の画面の middle を作って返す。要求はまだ送らない。

    - MiddleKind.FAKE: middle.system_screen_middle.FakeSystemScreenMiddle(exhausted_output=FAKE_DONE_OUTPUT)
      (操作なしで done を真で返し続ける。environ を見ない)
    - MiddleKind.LUNA: middle.system_screen_middle.LunaSystemScreenMiddle(api_key=environ[OPENAI_API_KEY_ENV] の
      前後の空白を除いたもの, screen_names=system_screen.system_screen_references.SYSTEM_SCREEN_NAMES)
      (照合と同じ 5 個の見本の名前)。キーが無いか空なら ValueError (呼ぶ前に settings.openai_api_key_errors で
      確かめておく)
    run_command は、environ に os.environ を渡してこの関数で作る。
    """
    if kind is MiddleKind.FAKE:
        return FakeSystemScreenMiddle(exhausted_output=FAKE_DONE_OUTPUT)
    api_key = environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key:
        raise ValueError(f"the environment variable {OPENAI_API_KEY_ENV} is not set")
    return LunaSystemScreenMiddle(api_key=api_key, screen_names=SYSTEM_SCREEN_NAMES)


def start_isolated_bridge() -> Bridge:
    """ブリッジを新しいセッションで起動して返す (controller_bridge.Bridge(new_session=True))。

    run の BridgeSupervisor の start_bridge に渡し、最初の起動にも起動し直しにも使う。最初の Ctrl+C の SIGINT が
    ブリッジに届かないので、片付けの操作を送れる。
    """
    return Bridge(new_session=True)


def create_monitor_client(environ: Mapping[str, str]) -> JevClient:
    """--mode orchestrator の監視と --mode delegate の委任の要求の送り先 (TypeSafe Jev のクライアント) を作って返す。
    要求はまだ送らない。

    API キーは environ[TYPESAFE_API_KEY_ENV] の前後の空白を除いたもの。キーが無いか空なら ValueError (呼ぶ前に
    settings.orchestrator_api_key_errors か delegate_api_key_errors で確かめておく)。安全の監視のクライアントとは別に
    作り、閉じるのは run_command。run_command は、environ に os.environ を渡してこの関数で作る (テストはこの関数を
    差し替えられる)。
    """
    api_key = environ.get(TYPESAFE_API_KEY_ENV, "").strip()
    if not api_key:
        raise ValueError(f"the environment variable {TYPESAFE_API_KEY_ENV} is not set")
    return JevClient(api_key)


def run_command(args: argparse.Namespace) -> int:
    """run の処理。終了コードを返す。

    1. run_settings_from_args → settings.run_settings_errors。誤りがあれば 1 行ずつ標準エラー出力に書き、EXIT_USAGE を返す
       (実機には触れない。Orchestrator 型の 6 つの値と委任型の 2 つの値も、--mode に依らず確かめる)
    --mode orchestrator なら、ここから _run_orchestrator_command の流れに、--mode delegate なら _run_delegate_command の
    流れに進む。以下の 2〜9 は --mode advisor の流れ。
    2. settings.openai_api_key_errors(settings.middle, os.environ)。誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す
       (実機にも実行ディレクトリーにも触れない)
    3. settings.typesafe_api_key_errors(settings.surface, os.environ) と
       settings.safety_api_key_errors(settings.safety, os.environ)。どちらかに誤りがあれば (jev で環境変数
       TYPESAFE_API_KEY が無いか空)、両方のメッセージをこの順に標準エラー出力に書いて EXIT_USAGE を返す
       (実機にも実行ディレクトリーにも触れない)。
       surface と安全の監視のそれぞれについて、jev なら API キーは環境変数 TYPESAFE_API_KEY の前後の空白を除いたもの、
       fake なら None (読まない)
    4. middle = create_middle_analyzer(settings.middle, os.environ, settings.middle_effort) と
       system_middle = create_system_screen_middle(settings.middle, os.environ) (本体の画面の middle には
       settings.middle_effort を渡さない)
    5. surface.surface_assembly.build_surface_assembly(settings, surface の API キー) で surface の部品を作り、
       safety.safety_assembly.build_safety_assembly(settings.safety, 安全の監視の API キー) で安全の監視の部品を作る
    6. switch2_remote.bridge_supervisor.BridgeSupervisor(start_bridge=start_isolated_bridge,
       reset_radio=bluetooth_radio.reset_bluetooth_radio) を作る。最初のブリッジの起動に失敗したら
       (controller_bridge.BridgeStartError) メッセージを標準エラー出力に書いて 1 を返す
    7. switch2_remote.capture.Capture(CAPTURE_FFMPEG_FPS, CAPTURE_PROCESS_FPS, new_session=True) を作る
    6 と 7 は、ブリッジと ffmpeg を新しいセッションで起動する。最初の Ctrl+C の SIGINT は端末の前面のプロセスグループの
    すべてに届くため、同じグループのままだとブリッジと ffmpeg も終わり、片付けの操作を送れないため
    8. app.LayersApp(settings, LayersParts(frame_reader=SwitchCaptureReader(capture), report_sender=supervisor,
       bridge_state=supervisor の state を返す関数, surface=5 の部品の decider, middle=4 の middle,
       deep=build_deep_runner(settings.deep), safety=安全の監視の部品の checker, system_middle=4 の system_middle,
       on_frame=部品の on_frame,
       surface_boxes_at=部品の boxes_at, on_run_directory=部品の on_run_directory,
       surface_plan_boxes=部品の plan_boxes)).run() で動かし、止まるまで待つ
       (settings.deep は --deep の値。run.jsonl の start の args にも deep として書く)
    9. 止まったら、作れた部品を capture.close()、supervisor.close()、安全の監視の部品の close()、surface の部品の close()
       の順に閉じる (5 の後に 6 で失敗しても、surface と安全の監視の部品は閉じる)
    6〜7 と、止まった後の capture と supervisor を閉じるところは、_run_with_hardware で Orchestrator 型と共有する。
    advisor では --instruction と --instruction-file を読まない (指定しても動きは変わらない)。
    止まった理由が app.REASON_INTERRUPTED (Ctrl+C) なら 0、ほかの理由なら 1 を返す。止まった理由は標準エラー出力に 1 行で書く。
    """
    settings = run_settings_from_args(args)
    errors = run_settings_errors(settings)
    if errors:
        _print_errors(errors)
        return EXIT_USAGE
    if settings.mode is RunMode.ORCHESTRATOR:
        return _run_orchestrator_command(settings, args)
    if settings.mode is RunMode.DELEGATE:
        return _run_delegate_command(settings, args)
    key_errors = openai_api_key_errors(settings.middle, os.environ)
    if key_errors:
        _print_errors(key_errors)
        return EXIT_USAGE
    typesafe_key_errors = typesafe_api_key_errors(
        settings.surface, os.environ
    ) + safety_api_key_errors(settings.safety, os.environ)
    if typesafe_key_errors:
        _print_errors(typesafe_key_errors)
        return EXIT_USAGE
    middle = create_middle_analyzer(settings.middle, os.environ, settings.middle_effort)
    system_middle = create_system_screen_middle(settings.middle, os.environ)
    surface = build_surface_assembly(
        settings, _typesafe_api_key(settings.surface is SurfaceKind.JEV)
    )
    try:
        safety = build_safety_assembly(
            settings.safety, _typesafe_api_key(settings.safety is SafetyKind.JEV)
        )
        try:
            return _run_with_hardware(
                settings,
                lambda frame_reader, supervisor: _advisor_parts(
                    settings,
                    frame_reader,
                    supervisor,
                    middle,
                    system_middle,
                    surface,
                    safety.checker,
                ),
            )
        finally:
            safety.close()
    finally:
        surface.close()


def _run_orchestrator_command(settings: RunSettings, args: argparse.Namespace) -> int:
    """run_command の --mode orchestrator の流れ (1 の範囲の確かめの後)。終了コードを返す。

    2. orchestrator.initial_instruction.load_initial_instruction(--instruction, --instruction-file)。
       InitialInstructionError なら標準エラー出力に書き、EXIT_USAGE を返す (実機にも実行ディレクトリーにも触れない)
    3. settings.orchestrator_api_key_errors(os.environ)。誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す
       (OPENAI_API_KEY は確かめない。surface・middle・本体の画面の middle の部品も作らない)
    4. safety.safety_assembly.build_safety_assembly(settings.safety, jev なら環境変数 TYPESAFE_API_KEY の前後の空白を
       除いたもの、fake なら None) で安全の監視の部品を作り、create_monitor_client(os.environ) で監視の Jev の
       クライアントを作る
    5. ブリッジとキャプチャーを advisor と同じく作る (_run_with_hardware)
    6. app.LayersApp(settings, OrchestratorLayersParts(frame_reader=SwitchCaptureReader(capture),
       report_sender=supervisor, bridge_state=supervisor の state を返す関数, safety=4 の部品の checker,
       monitor=4 のクライアント, initial_instruction=2 の値)).run() で動かし、止まるまで待つ
    7. 止まったら、作れた部品を capture.close()、supervisor.close()、監視のクライアントの close()、安全の監視の部品の
       close() の順に閉じる (5 で失敗しても、4 で作ったものは閉じる)
    終了コードは advisor と同じ (REASON_INTERRUPTED なら 0、ほかの理由なら 1。ブリッジの起動の失敗も 1)。
    """
    try:
        instruction = load_initial_instruction(args.instruction, args.instruction_file)
    except InitialInstructionError as error:
        _print_errors([str(error)])
        return EXIT_USAGE
    key_errors = orchestrator_api_key_errors(os.environ)
    if key_errors:
        _print_errors(key_errors)
        return EXIT_USAGE
    safety = build_safety_assembly(
        settings.safety, _typesafe_api_key(settings.safety is SafetyKind.JEV)
    )
    try:
        monitor = create_monitor_client(os.environ)
        try:
            return _run_with_hardware(
                settings,
                lambda frame_reader, supervisor: OrchestratorLayersParts(
                    frame_reader=frame_reader,
                    report_sender=supervisor,
                    bridge_state=lambda: supervisor.state,
                    safety=safety.checker,
                    monitor=monitor,
                    initial_instruction=instruction,
                ),
            )
        finally:
            monitor.close()
    finally:
        safety.close()


def _run_delegate_command(settings: RunSettings, args: argparse.Namespace) -> int:
    """run_command の --mode delegate の流れ (1 の範囲の確かめの後)。終了コードを返す。

    _run_orchestrator_command の 2〜7 と同じ流れで、3 の API キーの確かめだけを settings.delegate_api_key_errors
    (os.environ) にする (誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す。実機にも実行ディレクトリーにも触れない)。
    create_monitor_client(os.environ) で作ったクライアントを、委任の要求の送り先として OrchestratorLayersParts の
    monitor に渡す。終了コードは advisor と同じ。
    """
    try:
        instruction = load_initial_instruction(args.instruction, args.instruction_file)
    except InitialInstructionError as error:
        _print_errors([str(error)])
        return EXIT_USAGE
    key_errors = delegate_api_key_errors(os.environ)
    if key_errors:
        _print_errors(key_errors)
        return EXIT_USAGE
    safety = build_safety_assembly(
        settings.safety, _typesafe_api_key(settings.safety is SafetyKind.JEV)
    )
    try:
        jev = create_monitor_client(os.environ)
        try:
            return _run_with_hardware(
                settings,
                lambda frame_reader, supervisor: OrchestratorLayersParts(
                    frame_reader=frame_reader,
                    report_sender=supervisor,
                    bridge_state=lambda: supervisor.state,
                    safety=safety.checker,
                    monitor=jev,
                    initial_instruction=instruction,
                ),
            )
        finally:
            jev.close()
    finally:
        safety.close()


def _typesafe_api_key(needed: bool) -> str | None:
    """needed なら環境変数 TYPESAFE_API_KEY の前後の空白を除いた値を返し、そうでなければ None を返す (読まない)。"""
    if not needed:
        return None
    return os.environ.get(TYPESAFE_API_KEY_ENV, "").strip()


def _advisor_parts(
    settings: RunSettings,
    frame_reader: LatestFrameReader,
    supervisor: BridgeSupervisor,
    middle: MiddleAnalyzer,
    system_middle: SystemScreenMiddle,
    surface: SurfaceAssembly,
    safety: SafetyChecker,
) -> LayersParts:
    """run_command の 8 の LayersParts (advisor 型の部品) を返す。"""
    return LayersParts(
        frame_reader=frame_reader,
        report_sender=supervisor,
        bridge_state=lambda: supervisor.state,
        surface=surface.decider,
        middle=middle,
        deep=build_deep_runner(settings.deep),
        safety=safety,
        system_middle=system_middle,
        on_frame=surface.on_frame,
        surface_boxes_at=surface.boxes_at,
        on_run_directory=surface.on_run_directory,
        surface_plan_boxes=surface.plan_boxes,
    )


def _run_with_hardware(
    settings: RunSettings,
    build_parts: Callable[
        [LatestFrameReader, BridgeSupervisor], LayersParts | OrchestratorLayersParts
    ],
) -> int:
    """ブリッジとキャプチャーを作って LayersApp を動かし、閉じる (advisor と Orchestrator 型で共有)。終了コードを返す。

    1. BridgeSupervisor(start_bridge=start_isolated_bridge, reset_radio=reset_bluetooth_radio) を作る。最初のブリッジの
       起動に失敗したら (BridgeStartError) メッセージを標準エラー出力に書いて 1 を返す
    2. Capture(CAPTURE_FFMPEG_FPS, CAPTURE_PROCESS_FPS, new_session=True) を作る
    3. LayersApp(settings, build_parts(SwitchCaptureReader(capture), supervisor)).run() で動かし、止まるまで待つ
    4. 止まったら (途中の例外でも)、作れた capture と supervisor を閉じる
    止まった理由を標準エラー出力に 1 行で書き、REASON_INTERRUPTED なら 0、ほかの理由なら 1 を返す。
    build_parts に渡した部品 (surface・安全の監視・監視のクライアント) は閉じない (呼び出し側が閉じる)。
    """
    try:
        supervisor = BridgeSupervisor(
            start_bridge=start_isolated_bridge, reset_radio=reset_bluetooth_radio
        )
    except BridgeStartError as error:
        print(str(error), file=sys.stderr)
        return 1
    capture: Capture | None = None
    try:
        capture = Capture(CAPTURE_FFMPEG_FPS, CAPTURE_PROCESS_FPS, new_session=True)
        parts = build_parts(SwitchCaptureReader(capture), supervisor)
        reason = LayersApp(settings, parts).run()
    finally:
        if capture is not None:
            capture.close()
        supervisor.close()
    print(f"stopped: {reason}", file=sys.stderr)
    return 0 if reason == REASON_INTERRUPTED else 1


def middle_once_command(args: argparse.Namespace) -> int:
    """middle-once の処理。終了コードを返す。

    1. middle_once_settings_from_args → settings.middle_once_settings_errors。誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す
    2. --run-dir がディレクトリーでなければ、標準エラー出力に書いて EXIT_USAGE を返す
    3. settings.openai_api_key_errors(settings.middle, os.environ)。誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す
       (実行ディレクトリーに何も書かない)
    4. middle.middle_loop.run_middle_once(run_dir, create_middle_analyzer(settings.middle, os.environ), middle_window_s)
       を呼ぶ
    5. 標準出力に結果の JSON 1 行 {"status": "ok" | "error", "scene_id": 場面 ID か null, "new_scene": 真偽,
       "deep_requests": [種類...], "error": 説明か null} を書く
    成功なら 0、analyze か書き込みに失敗したら 1 を返す (1〜3 の誤りでは標準出力に何も書かない)。
    """
    settings = middle_once_settings_from_args(args)
    errors = middle_once_settings_errors(settings)
    if errors:
        _print_errors(errors)
        return EXIT_USAGE
    if not settings.run_dir.is_dir():
        _print_errors([f"--run-dir is not a directory: {settings.run_dir}"])
        return EXIT_USAGE
    key_errors = openai_api_key_errors(settings.middle, os.environ)
    if key_errors:
        _print_errors(key_errors)
        return EXIT_USAGE
    result = run_middle_once(
        settings.run_dir,
        create_middle_analyzer(settings.middle, os.environ),
        settings.middle_window_s,
    )
    _print_json(
        {
            "status": "ok" if result.ok else "error",
            "scene_id": None if result.state is None else result.state.scene_id,
            "new_scene": result.new_scene is not None,
            "deep_requests": [kind.value for kind in result.deep_requests],
            "error": result.error,
        }
    )
    return 0 if result.ok else 1


def deep_once_command(args: argparse.Namespace) -> int:
    """deep-once の処理。終了コードを返す。

    1. deep_once_settings_from_args → settings.deep_once_settings_errors。誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す
    2. --run-dir がディレクトリーでなければ、標準エラー出力に書いて EXIT_USAGE を返す
    3. deep.deep_scheduler.run_deep_once(run_dir, kind, scene_id, build_deep_runner(DeepRunnerChoice(args.deep)))
       を呼ぶ
    4. 標準出力に結果の JSON 1 行 {"job": 実行ディレクトリーからのジョブのディレクトリーの相対パス, "status": deep_end の status,
       "message": 説明} を書く
    status が "ok" なら 0、それ以外なら 1 を返す。
    """
    settings = deep_once_settings_from_args(args)
    errors = deep_once_settings_errors(settings)
    if errors:
        _print_errors(errors)
        return EXIT_USAGE
    if not settings.run_dir.is_dir():
        _print_errors([f"--run-dir is not a directory: {settings.run_dir}"])
        return EXIT_USAGE
    result = run_deep_once(
        settings.run_dir,
        settings.kind,
        settings.scene_id,
        build_deep_runner(DeepRunnerChoice(args.deep)),
    )
    _print_json(
        {
            "job": result.job,
            "status": result.outcome.status.value,
            "message": result.outcome.message,
        }
    )
    return 0 if result.outcome.status is DeepJobStatus.OK else 1


def main(argv: list[str] | None = None) -> int:
    """起動引数 argv (None なら sys.argv[1:]) を解釈し、サブコマンドの処理を呼んで終了コードを返す。

    --help は使い方を表示して SystemExit(0)、引数の形の誤りは SystemExit(2) にする (argparse)。
    """
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


def _print_errors(errors: list[str]) -> None:
    """誤りを 1 行ずつ標準エラー出力に書く。"""
    for error in errors:
        print(error, file=sys.stderr)


def _print_json(value: dict[str, object] | dict[str, str]) -> None:
    """value を JSON の 1 行にして標準出力に書く。"""
    sys.stdout.write(format_json_line(value))
