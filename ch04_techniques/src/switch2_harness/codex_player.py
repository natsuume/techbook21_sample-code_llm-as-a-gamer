"""agent_launcher.PlayerAgent の OpenAI Codex CLI (codex exec --json) の実装。

run_agent_until が、Claude Code の代わりにこれで codex exec を起動し、期限より前に終わったら
codex exec resume <thread_id> で同じ会話を続ける。
    - 起動引数: codex_command.build_codex_exec_args (指示は作業ディレクトリーの AGENTS.md を developer_instructions で渡す)
    - 環境変数: codex_command.build_codex_env (専用の CODEX_HOME と、エージェント用のサブコマンドを固定する変数)
    - 起動し直しの ID: 出力の thread.started の thread_id (codex_log.last_thread_id)
    - 予算: Codex は費用を出さないため、result の subtype も使った費用も無い (None)。
      run の --max-budget-usd は commands.handle_run が起動の前に拒む
使えるサブコマンドと書き換えられるファイルは、起動引数ではなく、run の始めに専用の CODEX_HOME に書く rules と
config.toml で決まる (codex_home.prepare_codex_home)。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from switch2_harness.agent_kind import CODEX_AGENT, default_model
from switch2_harness.agent_launcher import LaunchOptions
from switch2_harness.codex_command import build_codex_env, build_codex_exec_args
from switch2_harness.codex_log import last_thread_id
from switch2_harness.workspace import WorkspacePaths


@dataclass(frozen=True)
class CodexPlayer:
    """PlayerAgent の Codex の実装。

    codex_home: 専用の CODEX_HOME (codex_home.prepare_codex_home が用意したもの)
    instructions: エージェントへの指示 (作業ディレクトリーの AGENTS.md の本文)
    """

    codex_home: Path
    instructions: str
    name: str = CODEX_AGENT

    def build_args(
        self,
        prompt: str,
        agent_commands: Sequence[str],
        paths: WorkspacePaths,
        options: LaunchOptions,
        resume_id: str | None,
        writable_files: Sequence[str],
    ) -> list[str]:
        """build_codex_exec_args(prompt, <options.model。None なら codex の既定のモデル>, options.effort,
        self.instructions, resume_id) を返す。agent_commands・paths・writable_files は使わない
        (rules と config.toml で決まるため)。"""
        model = (
            options.model if options.model is not None else default_model(CODEX_AGENT)
        )
        return build_codex_exec_args(
            prompt, model, options.effort, self.instructions, resume_id
        )

    def build_env(
        self, base_env: Mapping[str, str], workspace_root: Path
    ) -> dict[str, str]:
        """build_codex_env(base_env, self.codex_home, workspace_root) を返す。"""
        return build_codex_env(base_env, self.codex_home, workspace_root)

    def resume_id(self, lines: Sequence[str]) -> str | None:
        """codex_log.last_thread_id(lines) を返す。"""
        return last_thread_id(lines)

    def result_subtype(self, lines: Sequence[str]) -> str | None:
        """Codex は result を出さないため、常に None。"""
        return None

    def spent_usd(self, lines: Sequence[str]) -> float | None:
        """Codex は費用を出さないため、常に None。"""
        return None
