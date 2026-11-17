"""switch2-harness コマンドの入口。argparse でサブコマンドを解釈し、commands の各処理へ振り分ける。

使い方:
    uv run switch2-harness init plays/sample --game "ゲーム名" [--goal "目標"] [--enable NAME ...]
    uv run switch2-harness look [--workspace DIR]
    uv run switch2-harness press tap B+RIGHT [--workspace DIR]
    uv run switch2-harness status [--workspace DIR]
    uv run switch2-harness finish [--workspace DIR]
    uv run switch2-harness report [--json] [--workspace DIR]
    uv run switch2-harness run --minutes 30 [--model MODEL] [--effort LEVEL] [--max-budget-usd X] [--workspace DIR]

init 以外の --workspace の既定はカレントディレクトリー。ただし、エージェント用のサブコマンド
(look / press / status / finish) は、環境変数 workspace_pin.PINNED_WORKSPACE_ENV があるときはその作業ディレクトリーに固定する
(run が起動した claude からは、この作業ディレクトリーにしか書けない)。
終了コードは成功 0、失敗 1、使い方の誤り (argparse) 2。
"""

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from switch2_harness.action_history import FramesChanged, frames_changed_on_disk
from switch2_harness.agent_launcher import (
    DEFAULT_MODEL,
    LaunchOptions,
    StartProcess,
    start_claude_process,
)
from switch2_harness.commands import (
    CommandResult,
    handle_finish,
    handle_init,
    handle_look,
    handle_press,
    handle_report,
    handle_run,
    handle_status,
)
from switch2_harness.daemon_client import SendCommand, send_command
from switch2_harness.features import (
    ACTION_HISTORY_FEATURE,
    BASE_AGENT_COMMANDS,
    FEATURES,
    STAGNATION_ALERT_FEATURE,
    Feature,
)
from switch2_harness.harness_config import DEFAULT_GOAL, read_config
from switch2_harness.instructions import ReadTemplate, read_package_template
from switch2_harness.screen_recorder import read_latest_last_new_screen_time
from switch2_harness.workspace import (
    NotAWorkspaceError,
    WorkspacePaths,
    require_workspace,
)
from switch2_harness.workspace_pin import WorkspacePinError, resolve_agent_workspace

PROG = "switch2-harness"


@dataclass(frozen=True)
class CliDependencies:
    """サブコマンドの処理に渡す外部とのやり取り。テストでは fake を渡す。

    send: デーモンへの送信 (本番は daemon_client.send_command)
    clock: 現在の epoch 秒。記録の時刻に使う (本番は time.time)
    deadline_clock: run の期限と経過時間を計る時計 (本番は time.time。WSL2 では time.monotonic が実時間より遅く進むため使わない)
    now: init の created_at に使うタイムゾーン付きの現在時刻 (本番は datetime.now().astimezone())
    sleep: 待機 (本番は time.sleep)
    start_process: claude の起動 (本番は agent_launcher.start_claude_process)
    environ: claude に渡す環境変数の元。エージェント用のサブコマンドを固定する環境変数もここから読む (本番は os.environ)
    read_template: CLAUDE.md と、要素が置くファイルの最初の内容のテンプレートの読み込み (本番は instructions.read_package_template)
    available: 利用できる要素 (本番は features.FEATURES)
    """

    send: SendCommand
    clock: Callable[[], float]
    deadline_clock: Callable[[], float]
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    start_process: StartProcess
    environ: Mapping[str, str]
    read_template: ReadTemplate
    available: tuple[Feature, ...]


def _local_now() -> datetime:
    """ローカルのタイムゾーン付きの現在時刻を返す。"""
    return datetime.now().astimezone()


def production_dependencies() -> CliDependencies:
    """本番の CliDependencies (各フィールドの説明にある本番の値) を返す。"""
    return CliDependencies(
        send=send_command,
        clock=time.time,
        deadline_clock=time.time,
        now=_local_now,
        sleep=time.sleep,
        start_process=start_claude_process,
        environ=os.environ,
        read_template=read_package_template,
        available=FEATURES,
    )


def _add_workspace_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        default=None,
        metavar="DIR",
        help="作業ディレクトリー (既定: カレントディレクトリー)",
    )


def build_parser() -> argparse.ArgumentParser:
    """サブコマンドの parser を返す (prog は PROG)。サブコマンドの指定は必須。

    init: DIR (位置引数)、--game NAME (必須)、--goal TEXT (既定 harness_config.DEFAULT_GOAL)、
        --enable NAME ... (action="extend"、nargs="+"、既定は空のリスト。--enable a b と --enable a --enable b のどちらも書ける)
    look / status / finish: --workspace DIR
    press: ACTION (位置引数)、ARGS (位置引数、1 つ以上)、--workspace DIR。
        ACTION の値は argparse では制限せず、commands.handle_press で検証する
    report: --json (指定すると集計を JSON で出す)、--workspace DIR
    run: --minutes N (必須、float)、--model MODEL (既定 agent_launcher.DEFAULT_MODEL)、--effort LEVEL (既定 None)、
        --max-budget-usd X (float、既定 None)、--workspace DIR。
        --minutes と --max-budget-usd の値の範囲は commands.handle_run で検証する
    --workspace は、省いたかどうかを見分けるため既定を None にする (どの作業ディレクトリーにするかは run_cli が決める)。
    """
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Claude Code が Nintendo Switch 2 をプレイするための補助コマンド",
    )
    subcommands = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND"
    )

    init = subcommands.add_parser("init", help="作業ディレクトリーを作る")
    init.add_argument("directory", metavar="DIR", help="作る作業ディレクトリー")
    init.add_argument("--game", required=True, metavar="NAME", help="ゲームの名前")
    init.add_argument(
        "--goal",
        default=DEFAULT_GOAL,
        metavar="TEXT",
        help=f"プレイの目標 (既定: {DEFAULT_GOAL})",
    )
    init.add_argument(
        "--enable",
        action="extend",
        nargs="+",
        default=[],
        metavar="NAME",
        help="有効にする要素の名前",
    )

    look = subcommands.add_parser("look", help="画面を撮影して frames/ に保存する")
    _add_workspace_option(look)

    press = subcommands.add_parser("press", help="ボタン入力を送る (HOME は押せない)")
    press.add_argument("action", metavar="ACTION", help="tap / hold / down / up")
    press.add_argument("args", nargs="+", metavar="ARGS", help="CHORD・MS・TARGET")
    _add_workspace_option(press)

    status = subcommands.add_parser("status", help="デーモンの動作状況を表示する")
    _add_workspace_option(status)

    finish = subcommands.add_parser(
        "finish", help="押したままの入力を離してから HOME を押す"
    )
    _add_workspace_option(finish)

    report = subcommands.add_parser("report", help="実験の集計を表示する")
    report.add_argument(
        "--json", dest="as_json", action="store_true", help="集計を JSON で表示する"
    )
    _add_workspace_option(report)

    run = subcommands.add_parser(
        "run", help="claude -p を起動し、決めた時間がたったら止める"
    )
    run.add_argument(
        "--minutes", type=float, required=True, metavar="N", help="動かす時間 (分)"
    )
    run.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        metavar="MODEL",
        help=f"claude のモデル (既定: {DEFAULT_MODEL})",
    )
    run.add_argument("--effort", default=None, metavar="LEVEL", help="claude の effort")
    run.add_argument(
        "--max-budget-usd",
        type=float,
        default=None,
        metavar="X",
        help="run 全体の費用の上限 (USD)",
    )
    _add_workspace_option(run)
    return parser


def _look_frames_changed(
    paths: WorkspacePaths, available: tuple[Feature, ...]
) -> FramesChanged | None:
    """look に渡す frames_changed を返す。

    harness_config.read_config(paths.harness_file, available) の features に行動履歴の要素
    (features.ACTION_HISTORY_FEATURE) があれば action_history.frames_changed_on_disk(paths.root)、無ければ None。
    """
    config = read_config(paths.harness_file, available)
    if ACTION_HISTORY_FEATURE in config.features:
        return frames_changed_on_disk(paths.root)
    return None


def _look_last_new_screen_time(
    paths: WorkspacePaths, available: tuple[Feature, ...]
) -> Callable[[], float | None] | None:
    """look に渡す last_new_screen_time を返す。

    harness_config.read_config(paths.harness_file, available) の features に停滞の知らせの要素
    (features.STAGNATION_ALERT_FEATURE) があれば、screen_recorder.read_latest_last_new_screen_time を
    paths で呼ぶ関数、無ければ None。
    """
    config = read_config(paths.harness_file, available)
    if STAGNATION_ALERT_FEATURE in config.features:
        return partial(read_latest_last_new_screen_time, paths)
    return None


def _dispatch(
    args: argparse.Namespace, paths: WorkspacePaths, deps: CliDependencies
) -> CommandResult:
    """init 以外のサブコマンドを commands の処理に振り分ける。"""
    command: str = args.command
    if command == "look":
        return handle_look(
            paths,
            send=deps.send,
            clock=deps.clock,
            frames_changed=_look_frames_changed(paths, deps.available),
            last_new_screen_time=_look_last_new_screen_time(paths, deps.available),
        )
    if command == "press":
        return handle_press(
            paths, args.action, args.args, send=deps.send, clock=deps.clock
        )
    if command == "status":
        return handle_status(paths, send=deps.send, clock=deps.clock)
    if command == "finish":
        return handle_finish(paths, send=deps.send, clock=deps.clock)
    if command == "report":
        return handle_report(paths, args.as_json)
    if command == "run":
        options = LaunchOptions(
            model=args.model, effort=args.effort, max_budget_usd=args.max_budget_usd
        )
        return handle_run(
            paths,
            args.minutes,
            options,
            start_process=deps.start_process,
            clock=deps.clock,
            deadline_clock=deps.deadline_clock,
            sleep=deps.sleep,
            send=deps.send,
            base_env=deps.environ,
            available=deps.available,
        )
    raise ValueError(f"unknown command: {command}")


def run_cli(argv: list[str] | None, deps: CliDependencies) -> int:
    """argv を解釈して commands の処理を呼び、結果を表示して終了コードを返す。

    argv が None なら sys.argv[1:] を使う。使い方の誤りは argparse が使い方を表示して SystemExit(2) を送出する。
    init 以外は、まず作業ディレクトリーを決める:
        - エージェント用のサブコマンド (features.BASE_AGENT_COMMANDS: look / press / status / finish) は、
          workspace_pin.resolve_agent_workspace(--workspace, deps.environ) で決める。WorkspacePinError なら
          `error <メッセージ>` を stderr に出して 1 を返す (デーモンには送らず、どの作業ディレクトリーにも記録しない)
        - 人用のサブコマンド (report / run) は、固定の環境変数を見ずに --workspace (省けば ".") を使う
    続けて workspace.require_workspace で作業ディレクトリーを確かめる。
    NotAWorkspaceError なら `error <メッセージ>` を stderr に出して 1 を返す (デーモンには送らない)。
    look は、harness.json (read_config(..., deps.available)) で行動履歴の要素 (features.ACTION_HISTORY_FEATURE) が
    有効なら、action_history.frames_changed_on_disk(<作業ディレクトリー>) を frames_changed として commands.handle_look に渡す
    (look の応答の後に直近の行動を表示する)。無効なら渡さない。
    また look は、停滞の知らせの要素 (features.STAGNATION_ALERT_FEATURE) が有効なら、
    screen_recorder.read_latest_last_new_screen_time(<作業ディレクトリー>) を呼ぶ関数を last_new_screen_time として渡す
    (最後の新しい画面から stagnation_alert.STAGNATION_SECONDS 秒以上たっていたら、look の応答の直後に知らせの行を表示する)。
    無効なら渡さない。
    CommandResult の stdout_lines は標準出力に、stderr_lines は標準エラー出力に 1 行ずつ出す。
    """
    args = build_parser().parse_args(argv)
    if args.command == "init":
        result = handle_init(
            args.directory,
            args.game,
            args.goal,
            args.enable,
            now=deps.now,
            read_template=deps.read_template,
            available=deps.available,
        )
    else:
        requested: str | None = args.workspace
        if args.command in BASE_AGENT_COMMANDS:
            try:
                directory = resolve_agent_workspace(requested, deps.environ)
            except WorkspacePinError as e:
                print(f"error {e}", file=sys.stderr)
                return 1
        else:
            directory = requested if requested is not None else "."
        try:
            paths = require_workspace(directory)
        except NotAWorkspaceError as e:
            print(f"error {e}", file=sys.stderr)
            return 1
        result = _dispatch(args, paths, deps)
    for line in result.stdout_lines:
        print(line)
    for line in result.stderr_lines:
        print(line, file=sys.stderr)
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    """switch2-harness の入口。run_cli(argv, production_dependencies()) の結果を返す。"""
    return run_cli(argv, production_dependencies())
