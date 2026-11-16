"""作業ディレクトリーのパスの組み立てと、init での作成。

作業ディレクトリーの構成:
    <DIR>/
      harness.json   設定 (harness_config)
      CLAUDE.md      エージェントへの指示 (instructions)
      records/       events.jsonl (全コマンドの記録)、agent.jsonl (claude -p の stream-json)、agent-stderr.log
      frames/        撮影した PNG (000001.png から連番)
"""

from dataclasses import dataclass
from pathlib import Path

from switch2_harness.harness_config import HarnessConfig

HARNESS_FILE_NAME = "harness.json"
INSTRUCTIONS_FILE_NAME = "CLAUDE.md"
RECORDS_DIR_NAME = "records"
FRAMES_DIR_NAME = "frames"
EVENTS_FILE_NAME = "events.jsonl"
AGENT_LOG_FILE_NAME = "agent.jsonl"
AGENT_STDERR_FILE_NAME = "agent-stderr.log"


class NotAWorkspaceError(Exception):
    """harness.json が無いディレクトリーを作業ディレクトリーとして使おうとした。"""


class WorkspaceNotEmptyError(Exception):
    """init の DIR が既に存在し、空のディレクトリーでない。"""


@dataclass(frozen=True)
class WorkspacePaths:
    """作業ディレクトリーとその中のファイル・ディレクトリーの絶対パス。

    root: 作業ディレクトリー
    harness_file: root/harness.json
    instructions_file: root/CLAUDE.md
    records_dir: root/records
    frames_dir: root/frames
    events_file: root/records/events.jsonl
    agent_log_file: root/records/agent.jsonl
    agent_stderr_file: root/records/agent-stderr.log
    """

    root: Path
    harness_file: Path
    instructions_file: Path
    records_dir: Path
    frames_dir: Path
    events_file: Path
    agent_log_file: Path
    agent_stderr_file: Path


def workspace_paths(directory: str | Path) -> WorkspacePaths:
    """directory を絶対パス (os.path.abspath。カレントディレクトリー基準) にし、作業ディレクトリーの各パスを組み立てる。

    ファイルシステムには触れない (存在の確認もしない)。
    """
    raise NotImplementedError


def require_workspace(directory: str | Path) -> WorkspacePaths:
    """directory が作業ディレクトリー (harness.json がある) なら、そのパスを返す。

    harness.json が無ければ NotAWorkspaceError にする。メッセージは
    `not a harness workspace (harness.json not found): <作業ディレクトリーの絶対パス>`。
    """
    raise NotImplementedError


def create_workspace(
    paths: WorkspacePaths, config: HarnessConfig, instructions_text: str
) -> None:
    """init の作業ディレクトリーを作る。

    paths.root が存在し、空のディレクトリーでなければ (ファイルである場合を含む) 何も作らずに
    WorkspaceNotEmptyError にする。メッセージは `directory is not empty: <paths.root>`。
    paths.root が存在しなければ親も含めて作る。
    続けて harness.json (harness_config.write_config)、CLAUDE.md (instructions_text を UTF-8 で書く)、
    records/、frames/ を作る。records/ の中のファイルは作らない。
    """
    raise NotImplementedError
