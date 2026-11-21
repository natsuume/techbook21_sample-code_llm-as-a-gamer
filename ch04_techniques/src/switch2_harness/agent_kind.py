"""プレイするエージェントの種類 (Claude Code か OpenAI Codex CLI) と、種類ごとに変わる名前と既定値。

種類は init --agent で選び、harness.json の "agent" に保存する (claude のときは書かない。harness_config)。
run は保存した種類のエージェントを起動し、振り返りの批評役も同じ種類にする。
このモジュールは純粋な値と関数だけを持つ (ファイル・プロセスに触れない)。
"""

from typing import Literal, cast

# エージェントの種類。claude は Claude Code (claude -p)、codex は OpenAI Codex CLI (codex exec)
AgentKind = Literal["claude", "codex"]

CLAUDE_AGENT: AgentKind = "claude"
CODEX_AGENT: AgentKind = "codex"

# 選べる種類 (init --agent の choices。この順に使い方の表示に出る)
AGENT_KINDS: tuple[AgentKind, ...] = (CLAUDE_AGENT, CODEX_AGENT)

# init --agent を省いたとき、harness.json に "agent" が無いときの種類
DEFAULT_AGENT: AgentKind = CLAUDE_AGENT

# run --model を省いたときのモデル (Claude は第3章の試行と同じ)
DEFAULT_MODELS: dict[AgentKind, str] = {
    CLAUDE_AGENT: "claude-opus-5-5",
    CODEX_AGENT: "gpt-6.2-sol",
}

# 作業ディレクトリーの直下に置く、エージェントへの指示ファイルの名前
INSTRUCTIONS_FILE_NAMES: dict[AgentKind, str] = {
    CLAUDE_AGENT: "CLAUDE.md",
    CODEX_AGENT: "AGENTS.md",
}


def parse_agent_kind(value: object) -> AgentKind:
    """value が AGENT_KINDS のどれかならその種類を返す。そうでなければ ValueError にする。

    メッセージは `unknown agent: <value> (available: claude, codex)`。
    """
    if isinstance(value, str) and value in AGENT_KINDS:
        return cast(AgentKind, value)
    raise ValueError(f"unknown agent: {value} (available: {', '.join(AGENT_KINDS)})")


def default_model(agent: AgentKind) -> str:
    """agent の既定のモデル (DEFAULT_MODELS) を返す。"""
    return DEFAULT_MODELS[agent]


def instructions_file_name(agent: AgentKind) -> str:
    """agent の指示ファイルの名前 (INSTRUCTIONS_FILE_NAMES) を返す。"""
    return INSTRUCTIONS_FILE_NAMES[agent]
