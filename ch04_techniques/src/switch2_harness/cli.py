"""switch2-harness コマンドの入口。argparse でサブコマンドを解釈し、commands の各処理へ振り分ける。

使い方:
    uv run switch2-harness init plays/sample --game "ゲーム名" [--goal "目標"] [--enable NAME ...]
    uv run switch2-harness look [--workspace DIR]
    uv run switch2-harness press tap B+RIGHT [--workspace DIR]
    uv run switch2-harness status [--workspace DIR]
    uv run switch2-harness finish [--workspace DIR]
    uv run switch2-harness report [--json] [--workspace DIR]
    uv run switch2-harness run --minutes 30 [--model MODEL] [--effort LEVEL] [--max-budget-usd X] [--workspace DIR]

init 以外の --workspace の既定はカレントディレクトリー。
終了コードは成功 0、失敗 1、使い方の誤り (argparse) 2。
"""

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from switch2_harness.agent_launcher import StartProcess
from switch2_harness.daemon_client import SendCommand
from switch2_harness.features import Feature
from switch2_harness.instructions import ReadTemplate

PROG = "switch2-harness"


@dataclass(frozen=True)
class CliDependencies:
    """サブコマンドの処理に渡す外部とのやり取り。テストでは fake を渡す。

    send: デーモンへの送信 (本番は daemon_client.send_command)
    clock: 現在の epoch 秒 (本番は time.time)
    now: init の created_at に使うタイムゾーン付きの現在時刻 (本番は datetime.now().astimezone())
    sleep: 待機 (本番は time.sleep)
    start_process: claude の起動 (本番は agent_launcher.start_claude_process)
    environ: claude に渡す環境変数の元 (本番は os.environ)
    read_template: CLAUDE.md のテンプレートの読み込み (本番は instructions.read_package_template)
    available: 利用できる要素 (本番は features.FEATURES)
    """

    send: SendCommand
    clock: Callable[[], float]
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    start_process: StartProcess
    environ: Mapping[str, str]
    read_template: ReadTemplate
    available: tuple[Feature, ...]


def production_dependencies() -> CliDependencies:
    """本番の CliDependencies (各フィールドの説明にある本番の値) を返す。"""
    raise NotImplementedError


def build_parser() -> argparse.ArgumentParser:
    """サブコマンドの parser を返す (prog は PROG)。サブコマンドの指定は必須。

    init: DIR (位置引数)、--game NAME (必須)、--goal TEXT (既定 harness_config.DEFAULT_GOAL)、
        --enable NAME ... (action="extend"、nargs="+"、既定は空のリスト。--enable a b と --enable a --enable b のどちらも書ける)
    look / status / finish: --workspace DIR (既定 ".")
    press: ACTION (位置引数)、ARGS (位置引数、1 つ以上)、--workspace DIR (既定 ".")。
        ACTION の値は argparse では制限せず、commands.handle_press で検証する
    report: --json (指定すると集計を JSON で出す)、--workspace DIR (既定 ".")
    run: --minutes N (必須、float)、--model MODEL (既定 agent_launcher.DEFAULT_MODEL)、--effort LEVEL (既定 None)、
        --max-budget-usd X (float、既定 None)、--workspace DIR (既定 ".")。--minutes の正負は commands.handle_run で検証する
    """
    raise NotImplementedError


def run_cli(argv: list[str] | None, deps: CliDependencies) -> int:
    """argv を解釈して commands の処理を呼び、結果を表示して終了コードを返す。

    argv が None なら sys.argv[1:] を使う。使い方の誤りは argparse が使い方を表示して SystemExit(2) を送出する。
    init 以外は、まず workspace.require_workspace(--workspace) で作業ディレクトリーを確かめる。
    NotAWorkspaceError なら `error <メッセージ>` を stderr に出して 1 を返す (デーモンには送らない)。
    CommandResult の stdout_lines は標準出力に、stderr_lines は標準エラー出力に 1 行ずつ出す。
    """
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    """switch2-harness の入口。run_cli(argv, production_dependencies()) の結果を返す。"""
    raise NotImplementedError
