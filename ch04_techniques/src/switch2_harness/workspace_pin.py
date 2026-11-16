"""エージェント用のサブコマンド (look / press / status / finish) を、run が起動した作業ディレクトリーに固定する。

許可の規則 `Bash(uv run switch2-harness look *)` は末尾に任意の引数を許すため、エージェントは
--workspace で別の作業ディレクトリーを指定できてしまう。run は claude の環境変数 PINNED_WORKSPACE_ENV に
作業ディレクトリーの絶対パスを入れ (agent_launcher.build_claude_env)、エージェント用のサブコマンドは
この環境変数があるとき、その作業ディレクトリーだけを使う (resolve_agent_workspace)。
人用のサブコマンド (init / report / run) は、この環境変数を見ない。
"""

import os
from collections.abc import Mapping

# run が claude に渡す、エージェント用のサブコマンドを固定する作業ディレクトリーの絶対パスの環境変数
PINNED_WORKSPACE_ENV = "SWITCH2_HARNESS_WORKSPACE"


class WorkspacePinError(Exception):
    """固定された作業ディレクトリーと違う作業ディレクトリーを使おうとした (固定の値が空のときを含む)。"""


def pin_violation_message(pinned: str) -> str:
    """固定に反したときのメッセージを返す。例: `this command is pinned to the workspace: /w`。"""
    return f"this command is pinned to the workspace: {pinned}"


def resolve_agent_workspace(requested: str | None, environ: Mapping[str, str]) -> str:
    """エージェント用のサブコマンドが使う作業ディレクトリーを返す。

    requested は --workspace の値 (省いたときは None)。
    1. environ に PINNED_WORKSPACE_ENV が無ければ、requested (None なら ".") を返す (固定しない)
    2. 値が空なら、WorkspacePinError (pin_violation_message("")) にする (パスが空の固定)
    3. requested が None なら、環境変数の値を返す (カレントディレクトリーではなく固定の作業ディレクトリーを使う)
    4. requested と環境変数の値を os.path.realpath で比べ、違えば WorkspacePinError
       (pin_violation_message(<環境変数の値>)) にする。同じ (相対パス・末尾の / など書き方だけが違う) なら
       環境変数の値を返す
    """
    if PINNED_WORKSPACE_ENV not in environ:
        return requested if requested is not None else "."
    pinned = environ[PINNED_WORKSPACE_ENV]
    if not pinned:
        raise WorkspacePinError(pin_violation_message(pinned))
    if requested is None:
        return pinned
    if os.path.realpath(requested) != os.path.realpath(pinned):
        raise WorkspacePinError(pin_violation_message(pinned))
    return pinned
