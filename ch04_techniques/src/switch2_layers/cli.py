"""コマンド switch2-layers の入口。サブコマンド run / middle-once / deep-once を持つ。

    switch2-layers run --software NAME --user NAME [--runs-dir DIR] [--interval-fps N] [--interval-width N]
                       [--max-surface-errors N] [--middle-interval S] [--middle-window S] [--max-middle-errors N]
                       [--deep-min-start-interval S] [--deep-auto-seconds S] [--deep-timeout S]
                       [--middle fake|luna] [--surface fake|jev] [--threshold-advisor P] [--box-requery-every N]
                       [--deep claude|fake] [--safety jev|fake] [--safety-interval S] [--threshold-safety P]
                       [--max-safety-errors N] [--progress-window S] [--stuck-advice-seconds S]
        実機で 3 層を動かす。準備 (コントローラーの登録・指定のソフト (--software) を指定のユーザー (--user) で
        起動する) から始め、止めるときは片付けを行う (app と system_screen.run_phase の段階)。
        surface は --surface で選ぶ (既定 jev は TypeSafe Jev、fake は surface.fake_surface)。
        middle は --middle で選ぶ (既定 luna は gpt-6-luna、fake は middle.fake_middle)。本体の画面の middle も
        --middle で選ぶ (luna は middle.system_screen_middle.LunaSystemScreenMiddle、fake は操作なしで done を
        真で返し続けるフェイク)。deep は --deep で選ぶ (既定 claude = Claude Code、fake = deep.fake_deep)。
        安全の監視は --safety で選ぶ (既定 jev は TypeSafe Jev、fake は safety.fake_safety)
    switch2-layers middle-once --run-dir DIR [--middle-window S] [--middle fake|luna]
        保存済みの実行に対して middle を 1 回動かし、middle の出力ファイルを書く
    switch2-layers deep-once --run-dir DIR --doc KIND [--scene SCENE_ID] [--deep claude|fake]
        保存済みの実行に対して deep を 1 回動かす (きっかけ manual。deep は run と同じく --deep で選ぶ)
deep は notes/ の文書の本体を直接編集するため、文書を入れ替えるサブコマンドは無い。

--middle (run と middle-once) は middle の実装を選ぶ。既定の luna は gpt-6-luna を呼び (middle.luna_middle)、
fake は骨組みのフェイク (middle.fake_middle) を使う。luna で環境変数 OPENAI_API_KEY が無い (空を含む) ときは、
実機 (ブリッジ・キャプチャー) と実行ディレクトリーに触れる前に、標準エラー出力に書いて終了コード 2 を返す。
--surface (run) は surface の実装を選ぶ。既定の jev は TypeSafe Jev を呼び (surface.jev_surface)、fake は骨組みの
フェイク (surface.fake_surface) を使う。jev で環境変数 TYPESAFE_API_KEY が無い (空を含む) ときも、実機と実行
ディレクトリーに触れる前に、標準エラー出力に書いて終了コード 2 を返す。
--safety (run) は安全の監視の実装を選ぶ。既定の jev は TypeSafe Jev に問い (safety.jev_safety)、fake はすべての
確率を 0 として返す (safety.fake_safety)。jev で環境変数 TYPESAFE_API_KEY が無い (空を含む) ときも、--surface jev と
同じく終了コード 2 を返す。

引数の形の誤り (知らない引数、数として読めない値、--doc や --middle の知らない値、run の --software・--user が
無い・空か空白だけ、など) は argparse が使い方とともに表示し、SystemExit (終了コード 2) にする。値の範囲の誤りは、
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

from switch2_layers.app import REASON_INTERRUPTED, LayersApp, LayersParts
from switch2_layers.deep.claude_code_runner import ClaudeCodeDeepRunner
from switch2_layers.deep.deep_scheduler import DeepJobStatus, run_deep_once
from switch2_layers.deep.deep_types import DOCUMENT_KINDS, DeepRunner, DocumentKind
from switch2_layers.deep.fake_deep import FakeDeepRunner
from switch2_layers.file_io import format_json_line
from switch2_layers.hardware.capture_source import SwitchCaptureReader
from switch2_layers.middle.fake_middle import FakeMiddleAnalyzer
from switch2_layers.middle.luna_middle import LunaMiddleAnalyzer
from switch2_layers.middle.middle_loop import run_middle_once
from switch2_layers.middle.middle_types import MiddleAnalyzer
from switch2_layers.middle.system_screen_middle import SystemScreenMiddle
from switch2_layers.safety.safety_assembly import build_safety_assembly
from switch2_layers.safety.safety_types import SafetyChecker
from switch2_layers.settings import (
    CAPTURE_FFMPEG_FPS,
    CAPTURE_PROCESS_FPS,
    DEFAULT_BOX_REQUERY_EVERY,
    DEFAULT_DEEP_AUTO_SECONDS,
    DEFAULT_DEEP_MIN_START_INTERVAL_S,
    DEFAULT_DEEP_RUNNER,
    DEFAULT_DEEP_TIMEOUT_S,
    DEFAULT_INTERVAL_FPS,
    DEFAULT_INTERVAL_WIDTH,
    DEFAULT_MAX_MIDDLE_ERRORS,
    DEFAULT_MAX_SAFETY_ERRORS,
    DEFAULT_MAX_SURFACE_ERRORS,
    DEFAULT_MIDDLE_INTERVAL_S,
    DEFAULT_MIDDLE_KIND,
    DEFAULT_MIDDLE_WINDOW_S,
    DEFAULT_PROGRESS_WINDOW_S,
    DEFAULT_RUNS_DIR,
    DEFAULT_SAFETY,
    DEFAULT_SAFETY_INTERVAL_S,
    DEFAULT_STUCK_ADVICE_SECONDS,
    DEFAULT_SURFACE,
    DEFAULT_THRESHOLD_ADVISOR,
    DEFAULT_THRESHOLD_SAFETY,
    INTERVAL_FPS_MAX,
    INTERVAL_WIDTH_MAX,
    INTERVAL_WIDTH_MIN,
    MIDDLE_INTERVAL_MAX_S,
    MIDDLE_INTERVAL_MIN_S,
    OPENAI_API_KEY_ENV,
    SAFETY_INTERVAL_MAX_S,
    SAFETY_INTERVAL_MIN_S,
    TYPESAFE_API_KEY_ENV,
    DeepOnceSettings,
    DeepRunnerChoice,
    MiddleKind,
    MiddleOnceSettings,
    RunSettings,
    SafetyKind,
    SurfaceKind,
    deep_once_settings_errors,
    middle_once_settings_errors,
    openai_api_key_errors,
    run_settings_errors,
    safety_api_key_errors,
    typesafe_api_key_errors,
)
from switch2_layers.surface.surface_assembly import (
    SurfaceAssembly,
    build_surface_assembly,
)

PROG = "switch2-layers"
# 範囲の誤りで返す終了コード (argparse の使い方の誤りと同じ)
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    """3 つのサブコマンド (run・middle-once・deep-once) の引数を定義した ArgumentParser を返す。

    各サブコマンドの Namespace には、処理の関数 (handler: Namespace → 終了コード) を持たせる。
    サブコマンドは必須 (無ければ argparse が SystemExit 2 にする)。知らないサブコマンドは argparse が SystemExit 2 にする。
    """
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "surface・middle・deep の 3 層のエージェントで Nintendo Switch 2 のゲームを遊ぶ"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    add_run_parser(commands)
    add_middle_once_parser(commands)
    add_deep_once_parser(commands)
    return parser


def add_run_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """サブコマンド run の引数を定義する。既定値は settings の DEFAULT_* と同じ。

    --software NAME と --user NAME は必須 (無ければ argparse の誤り)。空か空白だけの値も argparse の誤り
    (SystemExit 2) にする。値は書かれたまま Namespace の software と user に入る。
    --surface は settings.SurfaceKind の値 (jev / fake) から選び (既定 jev)、--threshold-advisor は float (既定 0.5)、
    --box-requery-every は int (既定 5)。
    --safety は settings.SafetyKind の値 (jev / fake) から選び (既定 jev)、--safety-interval は float (既定 1.0)、
    --threshold-safety は float (既定 0.7)、--max-safety-errors は int (既定 3)。
    """
    parser = commands.add_parser(
        "run",
        help="実機で 3 層を動かす",
        description=(
            "実機で 3 層を動かす。surface は --surface、middle は --middle、deep は --deep で選ぶ。"
            "Ctrl+C で止まる"
        ),
    )
    parser.add_argument(
        "--software",
        metavar="NAME",
        required=True,
        help="起動するソフトの名前 (HOME メニューの絵の上に出る名前。必須)",
    )
    parser.add_argument(
        "--user",
        metavar="NAME",
        required=True,
        help="ソフトを遊ぶユーザーの名前 (ユーザー選択に出る名前。必須)",
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
    parser.set_defaults(handler=run_command)


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

    --software と --user は software_name と user_name にする。--middle は MiddleKind に、--surface は SurfaceKind に、
    --deep は DeepRunnerChoice に、--safety は SafetyKind にする。
    """
    return RunSettings(
        software_name=args.software,
        user_name=args.user,
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
        surface=SurfaceKind(args.surface),
        threshold_advisor=args.threshold_advisor,
        box_requery_every=args.box_requery_every,
        deep=DeepRunnerChoice(args.deep),
        safety=SafetyKind(args.safety),
        safety_interval_s=args.safety_interval,
        threshold_safety=args.threshold_safety,
        max_safety_errors=args.max_safety_errors,
        progress_window_s=args.progress_window,
        stuck_advice_seconds=args.stuck_advice_seconds,
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
    kind: MiddleKind, environ: Mapping[str, str]
) -> MiddleAnalyzer:
    """--middle の値 kind の MiddleAnalyzer を作って返す。要求はまだ送らない。

    - MiddleKind.FAKE: middle.fake_middle.FakeMiddleAnalyzer() (environ を見ない)
    - MiddleKind.LUNA: middle.luna_middle.LunaMiddleAnalyzer(api_key=environ[OPENAI_API_KEY_ENV] の前後の空白を
      除いたもの)。キーが無いか空なら ValueError (呼ぶ前に settings.openai_api_key_errors で確かめておく)
    run_command と middle_once_command は、environ に os.environ を渡してこの関数で作る
    (テストはこの関数を差し替えて、MockTransport の Client を持つ LunaMiddleAnalyzer を渡せる)。
    """
    if kind is MiddleKind.FAKE:
        return FakeMiddleAnalyzer()
    api_key = environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key:
        raise ValueError(f"the environment variable {OPENAI_API_KEY_ENV} is not set")
    return LunaMiddleAnalyzer(api_key=api_key)


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
    raise NotImplementedError


def run_command(args: argparse.Namespace) -> int:
    """run の処理。終了コードを返す。

    1. run_settings_from_args → settings.run_settings_errors。誤りがあれば 1 行ずつ標準エラー出力に書き、EXIT_USAGE を返す
       (実機には触れない)
    2. settings.openai_api_key_errors(settings.middle, os.environ)。誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す
       (実機にも実行ディレクトリーにも触れない)
    3. settings.typesafe_api_key_errors(settings.surface, os.environ) と
       settings.safety_api_key_errors(settings.safety, os.environ)。どちらかに誤りがあれば (jev で環境変数
       TYPESAFE_API_KEY が無いか空)、両方のメッセージをこの順に標準エラー出力に書いて EXIT_USAGE を返す
       (実機にも実行ディレクトリーにも触れない)。
       surface と安全の監視のそれぞれについて、jev なら API キーは環境変数 TYPESAFE_API_KEY の前後の空白を除いたもの、
       fake なら None (読まない)
    4. middle = create_middle_analyzer(settings.middle, os.environ) と
       system_middle = create_system_screen_middle(settings.middle, os.environ)
    5. surface.surface_assembly.build_surface_assembly(settings, surface の API キー) で surface の部品を作り、
       safety.safety_assembly.build_safety_assembly(settings.safety, 安全の監視の API キー) で安全の監視の部品を作る
    6. switch2_remote.bridge_supervisor.BridgeSupervisor(start_bridge=controller_bridge.Bridge,
       reset_radio=bluetooth_radio.reset_bluetooth_radio) を作る。最初のブリッジの起動に失敗したら
       (controller_bridge.BridgeStartError) メッセージを標準エラー出力に書いて 1 を返す
    7. switch2_remote.capture.Capture(CAPTURE_FFMPEG_FPS, CAPTURE_PROCESS_FPS) を作る
    8. app.LayersApp(settings, LayersParts(frame_reader=SwitchCaptureReader(capture), report_sender=supervisor,
       bridge_state=supervisor の state を返す関数, surface=5 の部品の decider, middle=4 の middle,
       deep=build_deep_runner(settings.deep), safety=安全の監視の部品の checker, system_middle=4 の system_middle,
       on_frame=部品の on_frame,
       surface_boxes_at=部品の boxes_at, on_run_directory=部品の on_run_directory,
       surface_plan_boxes=部品の plan_boxes)).run() で動かし、止まるまで待つ
       (settings.deep は --deep の値。run.jsonl の start の args にも deep として書く)
    9. 止まったら、作れた部品を capture.close()、supervisor.close()、安全の監視の部品の close()、surface の部品の close()
       の順に閉じる (5 の後に 6 で失敗しても、surface と安全の監視の部品は閉じる)
    止まった理由が app.REASON_INTERRUPTED (Ctrl+C) なら 0、ほかの理由なら 1 を返す。止まった理由は標準エラー出力に 1 行で書く。
    """
    settings = run_settings_from_args(args)
    errors = run_settings_errors(settings)
    if errors:
        _print_errors(errors)
        return EXIT_USAGE
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
    middle = create_middle_analyzer(settings.middle, os.environ)
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
                settings, middle, system_middle, surface, safety.checker
            )
        finally:
            safety.close()
    finally:
        surface.close()


def _typesafe_api_key(needed: bool) -> str | None:
    """needed なら環境変数 TYPESAFE_API_KEY の前後の空白を除いた値を返し、そうでなければ None を返す (読まない)。"""
    if not needed:
        return None
    return os.environ.get(TYPESAFE_API_KEY_ENV, "").strip()


def _run_with_hardware(
    settings: RunSettings,
    middle: MiddleAnalyzer,
    system_middle: SystemScreenMiddle,
    surface: SurfaceAssembly,
    safety: SafetyChecker,
) -> int:
    """run_command の 6〜9 (ブリッジとキャプチャーを作って LayersApp を動かし、閉じる)。終了コードを返す。

    surface と安全の監視の部品は閉じない (run_command が閉じる)。
    """
    try:
        supervisor = BridgeSupervisor(
            start_bridge=Bridge, reset_radio=reset_bluetooth_radio
        )
    except BridgeStartError as error:
        print(str(error), file=sys.stderr)
        return 1
    capture: Capture | None = None
    try:
        capture = Capture(CAPTURE_FFMPEG_FPS, CAPTURE_PROCESS_FPS)
        parts = LayersParts(
            frame_reader=SwitchCaptureReader(capture),
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
