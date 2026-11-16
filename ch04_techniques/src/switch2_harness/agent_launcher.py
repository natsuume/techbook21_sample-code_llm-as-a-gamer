"""Claude Code を headless (claude -p) で起動し、決めた時間がたったら止める。

起動引数 (build_claude_args) と環境変数 (build_claude_env) の組み立て、agent.jsonl からの session_id の読み取り
(last_session_id) は純粋関数。時間を区切った起動 (run_agent_until) は、プロセスの起動・時計・待機・
デーモンへの送信・記録をすべて引数で受け取る。

プレイ中のエージェントには、ユーザー設定の plugin・hook・MCP・~/.claude/CLAUDE.md と、
親ディレクトリーの .claude/skills (既存の第4章の Skill) を混ぜない。そのために
--setting-sources project,local・--strict-mcp-config・--disallowedTools Skill・--disable-slash-commands を付け、
環境変数 CLAUDE_CODE_DISABLE_CLAUDE_MDS と CLAUDE_CODE_DISABLE_AUTO_MEMORY を 1 にする。
作業ディレクトリーの CLAUDE.md も CLAUDE_CODE_DISABLE_CLAUDE_MDS で読まれなくなるため、
--append-system-prompt-file で渡す。
"""

import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from switch2_harness.daemon_client import SendCommand
from switch2_harness.event_log import CommandEvent
from switch2_harness.workspace import WorkspacePaths

CLAUDE_COMMAND = "claude"

# 第3章の試行と同じモデル
DEFAULT_MODEL = "claude-opus-5-5"

# エージェント用のサブコマンドを Bash で許可するときのコマンドの書き出し
HARNESS_COMMAND_PREFIX = "uv run switch2-harness"

# 期限より前に終わったプロセスを --resume で起動し直すときのプロンプト
RESUME_PROMPT = "プレイを続けてください。"

POLL_INTERVAL_S = 1.0  # プロセスと期限を確かめる間隔
STOP_GRACE_S = 20.0  # SIGINT を送ってから SIGTERM を送るまで待つ秒数
TERMINATE_GRACE_S = 10.0  # SIGTERM を送ってから kill するまで待つ秒数
MIN_REMAINING_S = 30.0  # 期限までの残りがこれ未満なら、終わったプロセスを起動し直さない
MAX_RESUMES = 20  # 起動し直す回数の上限 (最初の起動は数えない)

# claude に渡す環境変数に足す値。~/.claude/CLAUDE.md を含むどの CLAUDE.md も、自動メモリーも読ませないため
# (作業ディレクトリーの CLAUDE.md は --append-system-prompt-file で渡す)
CLAUDE_ENV_OVERRIDES: Mapping[str, str] = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}

# run が止まった理由。deadline: 期限 (または残りが MIN_REMAINING_S 未満)、
# no_session: 起動し直すための session_id が agent.jsonl に無い、resumes_exhausted: 起動し直した回数が MAX_RESUMES に達した
StopReason = Literal["deadline", "no_session", "resumes_exhausted"]


@dataclass(frozen=True)
class LaunchOptions:
    """claude に渡す指定。

    model: --model に渡すモデル
    effort: --effort に渡す値。None なら --effort を付けない
    max_budget_usd: --max-budget-usd に渡す値 (str() で文字列にする)。None なら付けない
    """

    model: str = DEFAULT_MODEL
    effort: str | None = None
    max_budget_usd: float | None = None


@dataclass(frozen=True)
class RunOutcome:
    """run_agent_until の結果。reason は止まった理由、launches は claude を起動した回数 (起動し直しを含む)。"""

    reason: StopReason
    launches: int


class AgentProcess(Protocol):
    """起動した claude のプロセスとして使う操作 (subprocess.Popen と同じ名前と引数)。"""

    def poll(self) -> int | None:
        """終わっていれば終了コードを、動いていれば None を返す。"""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """終わるまで待って終了コードを返す。timeout 秒たっても終わらなければ subprocess.TimeoutExpired を送出する。"""
        ...

    def send_signal(self, sig: int) -> None:
        """シグナルを送る。"""
        ...

    def kill(self) -> None:
        """SIGKILL を送る。"""
        ...


class StartProcess(Protocol):
    """claude のプロセスを起動する関数の型。

    args を cwd で起動し、環境変数を env にする。標準出力は stdout_path に、標準エラー出力は stderr_path に追記する。
    """

    def __call__(
        self,
        args: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> AgentProcess: ...


def first_prompt(game: str) -> str:
    """最初の起動のプロンプトを返す。例: game が "G" なら `指示に従って、ゲーム「G」のプレイを始めてください。`。"""
    raise NotImplementedError


def allowed_tools(agent_commands: Sequence[str]) -> list[str]:
    """--allowedTools に渡す値の列を返す。

    agent_commands の順に `Bash(uv run switch2-harness <サブコマンド> *)` を並べ、最後に "Read" を足す。
    例: ("look",) なら ["Bash(uv run switch2-harness look *)", "Read"]。
    """
    raise NotImplementedError


def build_claude_args(
    prompt: str,
    agent_commands: Sequence[str],
    instructions_path: Path,
    options: LaunchOptions,
    resume_session_id: str | None = None,
) -> list[str]:
    """claude -p の起動引数を返す。順序は次のとおり:

        claude -p <prompt>
          --output-format stream-json --verbose
          --permission-mode dontAsk
          --tools Bash,Read
          --allowedTools <allowed_tools(agent_commands) の各値>
          --disallowedTools Skill
          --disable-slash-commands
          --strict-mcp-config
          --setting-sources project,local
          --append-system-prompt-file <str(instructions_path)>
          --model <options.model>
          [--effort <options.effort>] [--max-budget-usd <str(options.max_budget_usd)>] [--resume <resume_session_id>]

    プロンプトを -p の直後に置くのは、可変長の --allowedTools などが後ろの位置引数を取り込まないようにするため。
    [] の中は、値が None でないときだけ付ける。
    """
    raise NotImplementedError


def build_claude_env(base_env: Mapping[str, str]) -> dict[str, str]:
    """base_env のコピーに CLAUDE_ENV_OVERRIDES を足した (同じ名前は上書きした) 辞書を返す。base_env は変えない。"""
    raise NotImplementedError


def last_session_id(lines: Iterable[str]) -> str | None:
    """agent.jsonl の行のうち、JSON のオブジェクトの行にある "session_id" (文字列) の最後のものを返す。

    JSON として読めない行、JSON のオブジェクトでない行、"session_id" が無いか文字列でない行は読み飛ばす。
    見つからなければ None。
    """
    raise NotImplementedError


def read_last_session_id(path: Path) -> str | None:
    """path (agent.jsonl) を UTF-8 で読み、last_session_id を返す。path が無ければ None。"""
    raise NotImplementedError


def validate_minutes(minutes: float) -> None:
    """run の --minutes を検証する。正の数でなければ (0 以下と NaN) ValueError にする。

    メッセージは `--minutes must be greater than 0: <minutes>`。
    """
    raise NotImplementedError


def start_claude_process(
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> subprocess.Popen[bytes]:
    """StartProcess の本番の実装。stdout_path と stderr_path を "ab" で開き、subprocess.Popen で起動する。

    標準入力は subprocess.DEVNULL にする。開いたファイルは起動後に閉じる (子プロセスは複製を持つ)。
    """
    raise NotImplementedError


def stop_process(process: AgentProcess) -> None:
    """動いているプロセスを段階的に止める。

    1. signal.SIGINT を送り、process.wait(timeout=STOP_GRACE_S) で待つ
    2. まだ終わっていなければ (subprocess.TimeoutExpired) signal.SIGTERM を送り、process.wait(timeout=TERMINATE_GRACE_S) で待つ
    3. まだ終わっていなければ process.kill() を呼び、process.wait() で終わるのを待つ
    """
    raise NotImplementedError


def run_agent_until(
    minutes: float,
    paths: WorkspacePaths,
    game: str,
    agent_commands: Sequence[str],
    options: LaunchOptions,
    *,
    start_process: StartProcess,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    send: SendCommand,
    record: Callable[[CommandEvent], None],
    base_env: Mapping[str, str],
) -> RunOutcome:
    """claude -p を起動し、期限 (開始時刻 + minutes 分) になったら止める。minutes は正の数を渡す。

    起動は start_process(args, paths.root, build_claude_env(base_env), paths.agent_log_file, paths.agent_stderr_file)。
    最初の起動のプロンプトは first_prompt(game) で、--resume は付けない。

    POLL_INTERVAL_S ごとに (sleep(POLL_INTERVAL_S)) 次を確かめる:
        - プロセスが動いていて、clock() が期限以上なら stop_process で止め、理由 "deadline" で終える
        - プロセスが終わっていたら、次の順に判定する:
            1. 期限までの残りが MIN_REMAINING_S 未満なら、理由 "deadline" で終える
            2. 起動し直した回数が MAX_RESUMES なら、理由 "resumes_exhausted" で終える
            3. read_last_session_id(paths.agent_log_file) が None なら、理由 "no_session" で終える
            4. RESUME_PROMPT と --resume <session_id> で起動し直す
    終えるときは (例外・KeyboardInterrupt で抜けるときも finally で) send(["up", "ALL"]) を送る。
    例外・KeyboardInterrupt で抜けるときにプロセスがまだ動いていれば、up ALL の前に stop_process で止める
    (止めないと、up ALL の後もエージェントが入力を送り続けるため)。

    record で次のイベントを記録する (command は "run"、frame は None):
        - 起動: args=("start",)、reply="launched claude launch=1"、ok=True、elapsed_s=0.0
        - 起動し直し: args=("resume", <session_id>)、reply="launched claude launch=<起動した回数> resume=<session_id>"、ok=True、elapsed_s=0.0
        - 停止: args=("stop", <理由>)、reply="stopped reason=<理由> launches=<起動した回数>"、ok=True、elapsed_s=0.0
          (例外で抜けるときは記録しない)
        - up ALL: args=("release",)、reply=<デーモンの応答>、ok=<応答が error で始まらない>、elapsed_s=<送信にかかった秒数>
          (例外で抜けるときも記録する)
    各イベントの time はそのときの clock()。
    """
    raise NotImplementedError
