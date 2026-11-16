"""Claude Code を headless (claude -p) で起動し、決めた時間がたったら止める。

起動引数 (build_claude_args) と環境変数 (build_claude_env) の組み立て、agent.jsonl からの session_id・
最後の result の subtype・最後の result の total_cost_usd の読み取り (last_session_id・last_result_subtype・
last_total_cost_usd) は純粋関数。
時間を区切った起動 (run_agent_until) は、プロセスの起動・時計・待機・デーモンへの送信・記録をすべて引数で受け取る。

プレイ中のエージェントには、ユーザー・プロジェクト・ローカルの設定 (plugin・hook・許可の設定など)、MCP、
CLAUDE.md (~/.claude/CLAUDE.md を含む)、自動メモリー、親ディレクトリーの .claude/skills (既存の第4章の Skill)、
作業ディレクトリーの外のファイルを混ぜない。そのために次のようにする:
    - --restricted: 設定ファイルはユーザー・プロジェクト・ローカルのものを読まず、managed settings と --settings だけを読む。
      コマンドを実行するツールは --tools で名前を挙げたものだけが残り、ファイルのツールは作業ディレクトリーの中に閉じ込められる
    - --settings CLAUDE_SETTINGS_JSON: 作業ディレクトリーの外の読み取りを、Read と読み取り専用の Bash の両方で止める
    - --tools Bash,Read: --restricted では Bash も名前を挙げないと使えないため、Bash と Read を挙げる
    - --allowedTools: 承認なしで通す Bash を switch2-harness のエージェント用サブコマンドだけにする
      (作業ディレクトリーの中の Read は承認なしで通るため、Read は挙げない)
    - --strict-mcp-config: --mcp-config で渡したもの以外の MCP を使わない (何も渡さない)
    - --disallowedTools Skill・--disable-slash-commands: Skill とスラッシュコマンドを使わせない
    - 環境変数 CLAUDE_CODE_DISABLE_CLAUDE_MDS・CLAUDE_CODE_DISABLE_AUTO_MEMORY を 1 にし、
      親のセッションの変数とほかの API キーを除く (build_claude_env)
作業ディレクトリーの CLAUDE.md も CLAUDE_CODE_DISABLE_CLAUDE_MDS で読まれなくなるため、
--append-system-prompt-file で渡す。

agent.jsonl は追記するため、同じ作業ディレクトリーで run を繰り返すと前の run の出力が残る。
run_agent_until は開始時の agent.jsonl の大きさ (バイト数) を覚え、判定にはそれより後の部分 (今回の run の出力) だけを使う。
強制終了したプロセスの出力はマルチバイト文字の途中で切れることがあるため、agent.jsonl は UTF-8 の
errors="replace" でデコードし、JSON として読めない行は読み飛ばす。
"""

import json
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

# --settings に渡す設定。作業ディレクトリーの外の読み取りを、Read と読み取り専用の Bash (cat など) の両方で止めるため
CLAUDE_SETTINGS_JSON = json.dumps(
    {"permissions": {"blockReadsOutsideWorkingDirectories": True}}
)

# claude に渡す環境変数に足す値。~/.claude/CLAUDE.md を含むどの CLAUDE.md も、自動メモリーも読ませないため
# (作業ディレクトリーの CLAUDE.md は --append-system-prompt-file で渡す)
CLAUDE_ENV_OVERRIDES: Mapping[str, str] = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}

# 名前が "CLAUDE" で始まる環境変数のうち、claude に渡すもの。
# run は親の Claude Code のセッションの Bash から起動されることがあり、その環境には親のセッションの変数
# (CLAUDECODE・CLAUDE_CODE_SESSION_ID・CLAUDE_EFFORT など) がある。それらで子が子のセッションとして扱われたり
# effort が変わったりしないように、"CLAUDE" で始まる変数は除き、Claude Code 自身の認証と設定の場所に使う変数だけを残す
KEPT_CLAUDE_ENV_NAMES: tuple[str, ...] = (
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)

# 名前が "_API_KEY" で終わる環境変数のうち、claude に渡すもの (ほかの API キーを子に渡す理由は無いため除く)
KEPT_API_KEY_ENV_NAME = "ANTHROPIC_API_KEY"

# claude -p が --max-budget-usd の上限に達して終わったときの、result メッセージの subtype
BUDGET_EXHAUSTED_SUBTYPE = "error_max_budget_usd"

# run が止まった理由。
# deadline: 期限 (または残りが MIN_REMAINING_S 未満)
# budget_exhausted: 予算を使い切った。次の 2 つの場合がある
#     - CLI が予算切れで終わった (今回の run の agent.jsonl の最後の result の subtype が error_max_budget_usd)
#     - run 全体の残りの予算 (--max-budget-usd − 今回の run の最後の result の total_cost_usd) が 0 以下になった
# no_session: 起動し直すための session_id が今回の run の agent.jsonl に無い
# resumes_exhausted: 起動し直した回数が MAX_RESUMES に達した
StopReason = Literal["deadline", "budget_exhausted", "no_session", "resumes_exhausted"]


@dataclass(frozen=True)
class LaunchOptions:
    """claude に渡す指定。

    model: --model に渡すモデル
    effort: --effort に渡す値。None なら --effort を付けない
    max_budget_usd: run 全体の費用の上限 (USD)。--max-budget-usd に、小数 4 桁の文字列
        (f"{値:.4f}"。例: 2.5 は "2.5000"、1.0 - 0.4 は "0.6000") にして渡す。None なら付けない
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

    agent_commands の順に `Bash(uv run switch2-harness <サブコマンド> *)` を並べたものだけを返す
    (Read は作業ディレクトリーの中なら承認なしで通るため、挙げない)。
    例: ("look",) なら ["Bash(uv run switch2-harness look *)"]。
    """
    raise NotImplementedError


def format_budget_usd(budget_usd: float) -> str:
    """--max-budget-usd に渡す文字列を返す。小数 4 桁 (f"{budget_usd:.4f}")。例: 0.6 は "0.6000"。"""
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
          --restricted
          --settings <CLAUDE_SETTINGS_JSON>
          --append-system-prompt-file <str(instructions_path)>
          --model <options.model>
          [--effort <options.effort>] [--max-budget-usd <format_budget_usd(options.max_budget_usd)>]
          [--resume <resume_session_id>]

    プロンプトを -p の直後に置くのは、可変長の --allowedTools などが後ろの位置引数を取り込まないようにするため。
    [] の中は、値が None でないときだけ付ける。
    """
    raise NotImplementedError


def build_claude_env(base_env: Mapping[str, str]) -> dict[str, str]:
    """claude に渡す環境変数の辞書を返す。base_env は変えない。

    base_env から次の変数を除き (名前の大文字小文字は区別する)、CLAUDE_ENV_OVERRIDES を足した (同じ名前は上書きした) ものを返す:
        - 名前が "CLAUDE" で始まり、KEPT_CLAUDE_ENV_NAMES に無い変数 (例: CLAUDECODE、CLAUDE_CODE_SESSION_ID、CLAUDE_EFFORT)
        - 名前が "_API_KEY" で終わり、KEPT_API_KEY_ENV_NAME ("ANTHROPIC_API_KEY") でない変数 (例: OPENAI_API_KEY)
    それ以外の変数 (PATH・HOME など) はそのまま残す。
    """
    raise NotImplementedError


def last_session_id(lines: Iterable[str]) -> str | None:
    """agent.jsonl の行のうち、JSON のオブジェクトの行にある "session_id" (文字列) の最後のものを返す。

    JSON として読めない行、JSON のオブジェクトでない行、"session_id" が無いか文字列でない行は読み飛ばす。
    見つからなければ None。
    """
    raise NotImplementedError


def last_result_subtype(lines: Iterable[str]) -> str | None:
    """agent.jsonl の行のうち、JSON のオブジェクトで "type" が "result" の行にある "subtype" (文字列) の最後のものを返す。

    JSON として読めない行、JSON のオブジェクトでない行、"type" が "result" でない行、
    "subtype" が無いか文字列でない行は読み飛ばす。見つからなければ None。
    例: subtype が "success" の result の後に "error_max_budget_usd" の result があれば "error_max_budget_usd"。
    """
    raise NotImplementedError


def last_total_cost_usd(lines: Iterable[str]) -> float | None:
    """agent.jsonl の行のうち、JSON のオブジェクトで "type" が "result" の行にある "total_cost_usd" (数値) の最後のものを返す。

    値は float にして返す。JSON として読めない行、JSON のオブジェクトでない行、"type" が "result" でない行、
    "total_cost_usd" が無いか数値でない行 (bool は数値とみなさない) は読み飛ばす。見つからなければ None。
    """
    raise NotImplementedError


def agent_log_size(path: Path) -> int:
    """path (agent.jsonl) の大きさ (バイト数) を返す。path が無ければ 0。"""
    raise NotImplementedError


def read_agent_log_lines(path: Path, offset: int) -> list[str]:
    """path (agent.jsonl) の offset バイト目から後を行に分けて返す。path が無ければ空のリスト。

    path をバイトで開いて offset に移動してから最後まで読み、UTF-8 の errors="replace" でデコードする
    (途中で切れたマルチバイト文字は置換文字になる)。行は "\\n" だけで分け、最後の "\\n" の後の空の要素は含めない
    (str.splitlines() は JSON の文字列の中にありうる U+2028 などでも分けてしまうため使わない)。
    """
    raise NotImplementedError


def read_last_session_id(path: Path, offset: int) -> str | None:
    """read_agent_log_lines(path, offset) の行の last_session_id を返す。path が無ければ None。"""
    raise NotImplementedError


def read_last_result_subtype(path: Path, offset: int) -> str | None:
    """read_agent_log_lines(path, offset) の行の last_result_subtype を返す。path が無ければ None。"""
    raise NotImplementedError


def read_last_total_cost_usd(path: Path, offset: int) -> float | None:
    """read_agent_log_lines(path, offset) の行の last_total_cost_usd を返す。path が無ければ None。"""
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

    最初の起動の前に offset = agent_log_size(paths.agent_log_file) を覚える。以下の read_last_* には
    (paths.agent_log_file, offset) を渡し、今回の run の出力だけを読む。
    起動は start_process(args, paths.root, build_claude_env(base_env), paths.agent_log_file, paths.agent_stderr_file)。
    最初の起動のプロンプトは first_prompt(game) で、--resume は付けず、--max-budget-usd には options.max_budget_usd を渡す。

    POLL_INTERVAL_S ごとに (sleep(POLL_INTERVAL_S)) 次を確かめる:
        - プロセスが動いていて、clock() が期限以上なら stop_process で止め、理由 "deadline" で終える
        - プロセスが終わっていたら、次の順に判定する:
            1. 期限までの残りが MIN_REMAINING_S 未満なら、理由 "deadline" で終える
            2. read_last_result_subtype が BUDGET_EXHAUSTED_SUBTYPE なら、
               理由 "budget_exhausted" で終える (予算の上限で終わったプロセスは起動し直さない)
            3. options.max_budget_usd が None でなければ、残りの予算 = options.max_budget_usd − read_last_total_cost_usd
               (None なら options.max_budget_usd のまま) を求め、0 以下なら理由 "budget_exhausted" で終える
            4. 起動し直した回数が MAX_RESUMES なら、理由 "resumes_exhausted" で終える
            5. read_last_session_id が None なら、理由 "no_session" で終える
            6. RESUME_PROMPT と --resume <session_id> で起動し直す。options.max_budget_usd が None でなければ、
               dataclasses.replace(options, max_budget_usd=<残りの予算>) を build_claude_args に渡す
               (CLI は --max-budget-usd を 1 つのプロセスが使った分だけで判定するため、run 全体の上限にするには残りを渡す)
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
