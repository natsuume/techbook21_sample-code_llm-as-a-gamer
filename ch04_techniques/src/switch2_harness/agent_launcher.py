"""Claude Code を headless (claude -p) で起動し、決めた時間がたったら止める。

起動引数 (build_claude_args) と環境変数 (build_claude_env) の組み立て、agent.jsonl からの session_id・
最後の result の subtype・result の total_cost_usd の最大の値の読み取り (last_session_id・last_result_subtype・
max_total_cost_usd)、終わったプロセスを起動し直すかの判定 (stop_reason_after_exit) は純粋関数。
時間を区切った起動 (run_agent_until) は、プロセスの起動・時計・待機・デーモンへの送信・記録・
後片付けの間の割り込みの扱いをすべて引数で受け取る。

claude は新しいセッション (subprocess.Popen の start_new_session=True) で起動する。端末の Ctrl-C が claude に直接届かず、
run の Python が KeyboardInterrupt を受けて、後片付けの順序 (claude を止めてから up ALL) で止めるためである。
claude のプロセスとの関係は次のとおり (Claude Code 2.1.325 で 2026-11-17 に確かめた):
    - start_claude_process で起動した claude は、自分の pid のプロセスグループとセッションにいる
    - claude の Bash ツールは、コマンドを `/bin/bash -c ...` として新しいセッションで起動する (sid と pgid がその bash 自身の pid)。
      その下のコマンドは、さらに別のプロセスグループにいる。どちらも claude のプロセスグループには入らないため、
      claude のプロセスグループに送ったシグナルは Bash ツールのコマンドには届かない
    - claude のプロセスグループに SIGINT か SIGTERM を送ると、claude は終わり、Bash ツールのコマンドも claude 自身が止める
    - SIGINT と SIGTERM で claude が終わらず SIGKILL で止めるときは、claude は後始末できず、別のセッションの子が残りうる
そのため stop_process は、SIGKILL を送るときだけ、その前に claude の子孫を /proc から控え (process_tree)、
claude が終わった後に、控えた子孫を pid の再利用を見分けながら SIGKILL で止める。

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
    - 環境変数 workspace_pin.PINNED_WORKSPACE_ENV に作業ディレクトリーを入れ、エージェント用のサブコマンドを
      その作業ディレクトリーに固定する (--workspace で別の実験の作業ディレクトリーに書かせない)
作業ディレクトリーの CLAUDE.md も CLAUDE_CODE_DISABLE_CLAUDE_MDS で読まれなくなるため、
--append-system-prompt-file で渡す。

agent.jsonl は追記するため、同じ作業ディレクトリーで run を繰り返すと前の run の出力が残る。
run_agent_until は開始時の agent.jsonl の大きさ (バイト数) を覚え、判定にはそれより後の部分 (今回の run の出力) だけを使う。
強制終了したプロセスの出力はマルチバイト文字の途中で切れることがあるため、agent.jsonl は UTF-8 の
errors="replace" でデコードし、JSON として読めない行は読み飛ばす。
"""

import json
import math
import os
import signal
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from switch2_harness.daemon_client import SendCommand
from switch2_harness.event_log import CommandEvent
from switch2_harness.process_tree import (
    ProcessEntry,
    kill_if_unchanged,
    read_descendants,
)
from switch2_harness.signal_guard import defer_interrupts, ignore_interrupts
from switch2_harness.workspace import WorkspacePaths
from switch2_harness.workspace_pin import PINNED_WORKSPACE_ENV

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
# 後片付けの up ALL の待ち時間の上限 (秒)。後片付けの間はシグナルを無視するため、デーモンが応答しなくても止まれるようにする
RELEASE_TIMEOUT_S = 10.0

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

# 名前が "CLAUDE" で始まる環境変数は、claude に渡さないのを基本にする。
# run は親の Claude Code のセッションの Bash から起動されることがあり、その環境には親のセッションの変数
# (CLAUDECODE・CLAUDE_CODE_SESSION_ID・CLAUDE_EFFORT など) がある。それらで子が子のセッションとして扱われたり
# effort が変わったりしないようにするため。残すのは、Claude Code 自身の認証と設定の場所に使う次の変数と、
# プロバイダーの選択 (KEPT_CLAUDE_ENV_PREFIX で始まるもの) と認証を省く変数 (SKIP_AUTH_ENV_PREFIX で始まり
# SKIP_AUTH_ENV_SUFFIX で終わるもの)
KEPT_CLAUDE_ENV_NAMES: tuple[str, ...] = (
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_OAUTH_TOKEN",
)
KEPT_CLAUDE_ENV_PREFIX = (
    "CLAUDE_CODE_USE_"  # 例: CLAUDE_CODE_USE_BEDROCK・CLAUDE_CODE_USE_VERTEX
)
SKIP_AUTH_ENV_PREFIX = "CLAUDE_CODE_SKIP_"  # 例: CLAUDE_CODE_SKIP_BEDROCK_AUTH
SKIP_AUTH_ENV_SUFFIX = "_AUTH"

# 名前が "_API_KEY" で終わる環境変数は、この接頭辞で始まるもの (Claude Code のプロバイダーの API キー) だけを残す。
# ほかの層の API キー (OPENAI_API_KEY など) を子に渡す理由は無いため除く
KEPT_API_KEY_ENV_PREFIX = "ANTHROPIC_"
API_KEY_ENV_SUFFIX = "_API_KEY"

# claude -p が --max-budget-usd の上限に達して終わったときの、result メッセージの subtype
BUDGET_EXHAUSTED_SUBTYPE = "error_max_budget_usd"

# run が止まった理由。
# deadline: 期限 (または残りが MIN_REMAINING_S 未満)
# budget_exhausted: 予算を使い切った。次の 2 つの場合がある
#     - CLI が予算切れで終わった (今回の run の agent.jsonl の最後の result の subtype が error_max_budget_usd)
#     - run 全体の残りの予算 (--max-budget-usd − 今回の run の result の total_cost_usd の最大の値) を
#       format_budget_usd で丸めた値が 0 以下になった
# no_session: 起動し直すための session_id が今回の run の agent.jsonl に無い
# resumes_exhausted: 起動し直した回数が MAX_RESUMES に達した
StopReason = Literal["deadline", "budget_exhausted", "no_session", "resumes_exhausted"]

# シグナルの扱いを一時的に変えるコンテキストマネージャーを作る関数の型
# (後片付けを守る shield の本番は signal_guard.ignore_interrupts、起動を守る defer の本番は signal_guard.defer_interrupts)
Shield = Callable[[], AbstractContextManager[None]]


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
    """起動した claude のプロセスとして使う操作。本番は ClaudeProcess。"""

    @property
    def pid(self) -> int:
        """claude のプロセスの番号。SIGKILL で止める前に、この番号の子孫を控える。"""
        ...

    def poll(self) -> int | None:
        """終わっていれば終了コードを、動いていれば None を返す。"""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """終わるまで待って終了コードを返す。timeout 秒たっても終わらなければ subprocess.TimeoutExpired を送出する。"""
        ...

    def signal_group(self, sig: int) -> None:
        """claude のプロセスグループに sig を送る。

        claude の Bash ツールが起動したコマンドは別のセッションにいるため、このグループには入らない
        (Claude Code 2.1.325 で 2026-11-17 に確かめた)。
        グループにプロセスが 1 つも残っていなければ ProcessLookupError を送出する。
        """
        ...


class ClaudeProcess:
    """start_claude_process が起動した claude のプロセス (subprocess.Popen を包む)。

    claude は新しいセッションで起動しているため、プロセスグループの番号は claude の pid と同じ。
    """

    def __init__(self, popen: subprocess.Popen[bytes]) -> None:
        self._popen = popen

    @property
    def pid(self) -> int:
        """claude のプロセスの番号 (claude のプロセスグループの番号でもある)。"""
        return self._popen.pid

    def poll(self) -> int | None:
        return self._popen.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._popen.wait(timeout=timeout)

    def signal_group(self, sig: int) -> None:
        os.killpg(self._popen.pid, sig)


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
    return f"指示に従って、ゲーム「{game}」のプレイを始めてください。"


def allowed_tools(agent_commands: Sequence[str]) -> list[str]:
    """--allowedTools に渡す値の列を返す。

    agent_commands の順に `Bash(uv run switch2-harness <サブコマンド> *)` を並べたものだけを返す
    (Read は作業ディレクトリーの中なら承認なしで通るため、挙げない)。
    例: ("look",) なら ["Bash(uv run switch2-harness look *)"]。
    """
    return [f"Bash({HARNESS_COMMAND_PREFIX} {command} *)" for command in agent_commands]


def format_budget_usd(budget_usd: float) -> str:
    """--max-budget-usd に渡す文字列を返す。小数 4 桁 (f"{budget_usd:.4f}")。例: 0.6 は "0.6000"。"""
    return f"{budget_usd:.4f}"


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
    args = [
        CLAUDE_COMMAND,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "Bash,Read",
        "--allowedTools",
        *allowed_tools(agent_commands),
        "--disallowedTools",
        "Skill",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--restricted",
        "--settings",
        CLAUDE_SETTINGS_JSON,
        "--append-system-prompt-file",
        str(instructions_path),
        "--model",
        options.model,
    ]
    if options.effort is not None:
        args += ["--effort", options.effort]
    if options.max_budget_usd is not None:
        args += ["--max-budget-usd", format_budget_usd(options.max_budget_usd)]
    if resume_session_id is not None:
        args += ["--resume", resume_session_id]
    return args


def is_kept_env_name(name: str) -> bool:
    """環境変数の名前 name を claude に渡すなら True を返す (名前の大文字小文字は区別する)。

    - "CLAUDE" で始まる名前は、KEPT_CLAUDE_ENV_NAMES にあるもの、KEPT_CLAUDE_ENV_PREFIX で始まるもの、
      SKIP_AUTH_ENV_PREFIX で始まり SKIP_AUTH_ENV_SUFFIX で終わるものだけを渡す
      (例: CLAUDE_CODE_USE_FOUNDRY・CLAUDE_CODE_SKIP_BEDROCK_AUTH は渡し、CLAUDECODE・CLAUDE_CODE_SKIP_FOO は渡さない)
    - API_KEY_ENV_SUFFIX ("_API_KEY") で終わる名前は、KEPT_API_KEY_ENV_PREFIX ("ANTHROPIC_") で始まるものだけを渡す
      (例: ANTHROPIC_FOUNDRY_API_KEY は渡し、OPENAI_API_KEY は渡さない)
    - それ以外の名前 (ANTHROPIC_BASE_URL、AWS_・GOOGLE_ で始まるもの、PATH など) は渡す
    """
    if name.startswith("CLAUDE"):
        return (
            name in KEPT_CLAUDE_ENV_NAMES
            or name.startswith(KEPT_CLAUDE_ENV_PREFIX)
            or (
                name.startswith(SKIP_AUTH_ENV_PREFIX)
                and name.endswith(SKIP_AUTH_ENV_SUFFIX)
            )
        )
    if name.endswith(API_KEY_ENV_SUFFIX):
        return name.startswith(KEPT_API_KEY_ENV_PREFIX)
    return True


def build_claude_env(
    base_env: Mapping[str, str], workspace_root: Path
) -> dict[str, str]:
    """claude に渡す環境変数の辞書を返す。base_env は変えない。

    base_env のうち is_kept_env_name に当たる変数だけを残し、CLAUDE_ENV_OVERRIDES と、
    workspace_pin.PINNED_WORKSPACE_ENV = str(workspace_root) (作業ディレクトリーの絶対パス) を足した
    (同じ名前は上書きした) ものを返す。PINNED_WORKSPACE_ENV により、claude が使うエージェント用のサブコマンドは
    この作業ディレクトリーに固定される (workspace_pin.resolve_agent_workspace)。
    """
    env = {name: value for name, value in base_env.items() if is_kept_env_name(name)}
    env.update(CLAUDE_ENV_OVERRIDES)
    env[PINNED_WORKSPACE_ENV] = str(workspace_root)
    return env


def _json_objects(lines: Iterable[str]) -> Iterator[dict[str, object]]:
    """行のうち、JSON のオブジェクトとして読める行を順に返す。読めない行と空行は読み飛ばす。"""
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            value: object = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def last_session_id(lines: Iterable[str]) -> str | None:
    """agent.jsonl の行のうち、JSON のオブジェクトの行にある "session_id" (文字列) の最後のものを返す。

    JSON として読めない行、JSON のオブジェクトでない行、"session_id" が無いか文字列でない行は読み飛ばす。
    見つからなければ None。
    """
    found: str | None = None
    for message in _json_objects(lines):
        session_id = message.get("session_id")
        if isinstance(session_id, str):
            found = session_id
    return found


def last_result_subtype(lines: Iterable[str]) -> str | None:
    """agent.jsonl の行のうち、JSON のオブジェクトで "type" が "result" の行にある "subtype" (文字列) の最後のものを返す。

    JSON として読めない行、JSON のオブジェクトでない行、"type" が "result" でない行、
    "subtype" が無いか文字列でない行は読み飛ばす。見つからなければ None。
    例: subtype が "success" の result の後に "error_max_budget_usd" の result があれば "error_max_budget_usd"。
    """
    found: str | None = None
    for message in _json_objects(lines):
        subtype = message.get("subtype")
        if message.get("type") == "result" and isinstance(subtype, str):
            found = subtype
    return found


def max_total_cost_usd(lines: Iterable[str]) -> float | None:
    """agent.jsonl の行のうち、JSON のオブジェクトで "type" が "result" の行にある "total_cost_usd" (数値) の最大のものを返す。

    クラッシュや起動時のエラーで終わった result は total_cost_usd が 0 になることがあるため、最後の値ではなく最大の値を返す
    (--resume で続けたセッションの total_cost_usd は累計のため、最大の値がそれまでに使った費用になる。
    --resume で続けても session_id は同じで、result の total_cost_usd はセッションの累計になることを、
    Claude Code 2.1.325 で 2026-11-17 に確かめた)。
    値は float にして返す。JSON として読めない行、JSON のオブジェクトでない行、"type" が "result" でない行、
    "total_cost_usd" が無いか数値でない行 (bool は数値とみなさない) は読み飛ばす。見つからなければ None。
    例: result の total_cost_usd が 0.4、0.0 の順なら 0.4。
    """
    found: float | None = None
    for message in _json_objects(lines):
        cost = message.get("total_cost_usd")
        if (
            message.get("type") == "result"
            and isinstance(cost, int | float)
            and not isinstance(cost, bool)
        ):
            found = float(cost) if found is None else max(found, float(cost))
    return found


def agent_log_size(path: Path) -> int:
    """path (agent.jsonl) の大きさ (バイト数) を返す。path が無ければ 0。"""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def read_agent_log_lines(path: Path, offset: int) -> list[str]:
    """path (agent.jsonl) の offset バイト目から後を行に分けて返す。path が無ければ空のリスト。

    path をバイトで開いて offset に移動してから最後まで読み、UTF-8 の errors="replace" でデコードする
    (途中で切れたマルチバイト文字は置換文字になる)。行は "\\n" だけで分け、最後の "\\n" の後の空の要素は含めない
    (str.splitlines() は JSON の文字列の中にありうる U+2028 などでも分けてしまうため使わない)。
    """
    try:
        with path.open("rb") as log_file:
            log_file.seek(offset)
            data = log_file.read()
    except FileNotFoundError:
        return []
    lines = data.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def read_last_session_id(path: Path, offset: int) -> str | None:
    """read_agent_log_lines(path, offset) の行の last_session_id を返す。path が無ければ None。"""
    return last_session_id(read_agent_log_lines(path, offset))


def read_last_result_subtype(path: Path, offset: int) -> str | None:
    """read_agent_log_lines(path, offset) の行の last_result_subtype を返す。path が無ければ None。"""
    return last_result_subtype(read_agent_log_lines(path, offset))


def read_max_total_cost_usd(path: Path, offset: int) -> float | None:
    """read_agent_log_lines(path, offset) の行の max_total_cost_usd を返す。path が無ければ None。"""
    return max_total_cost_usd(read_agent_log_lines(path, offset))


def validate_minutes(minutes: float) -> None:
    """run の --minutes を検証する。正の数でなければ (0 以下と NaN) ValueError にする。

    メッセージは `--minutes must be greater than 0: <minutes>`。
    """
    if not minutes > 0:
        raise ValueError(f"--minutes must be greater than 0: {minutes}")


def validate_max_budget_usd(value: float | None) -> None:
    """run の --max-budget-usd を検証する。None (指定なし) は通す。

    None でなければ、正の有限の数 (0 より大きく、NaN・inf でない) で、さらに float(format_budget_usd(値)) が
    0 より大きく (--max-budget-usd に渡す小数 4 桁の文字列が "0.0000" にならない) なければ ValueError にする
    (例: 0.00004 は拒否し、0.0001 は通す)。メッセージは `--max-budget-usd must be a positive finite number: <値>`。
    """
    if value is None:
        return
    if not (math.isfinite(value) and value > 0 and float(format_budget_usd(value)) > 0):
        raise ValueError(f"--max-budget-usd must be a positive finite number: {value}")


def start_claude_process(
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> ClaudeProcess:
    """StartProcess の本番の実装。stdout_path と stderr_path を "ab" で開き、subprocess.Popen で起動する。

    新しいセッション (start_new_session=True) で起動する。端末の Ctrl-C が claude に直接届かず、
    run の Python が KeyboardInterrupt を受けて、後片付けの順序 (claude を止めてから up ALL) で止めるためである。
    claude は自分の pid のプロセスグループとセッションにいる (Claude Code 2.1.325 で 2026-11-17 に確かめた)。
    標準入力は subprocess.DEVNULL にする。開いたファイルは起動後に閉じる (子プロセスは複製を持つ)。
    """
    with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
        popen = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
    return ClaudeProcess(popen)


def _signal_group_if_present(process: AgentProcess, sig: int) -> bool:
    """process のプロセスグループに sig を送る。グループが無ければ (ProcessLookupError) 何もせず False を返す。"""
    try:
        process.signal_group(sig)
    except ProcessLookupError:
        return False
    return True


def stop_process(
    process: AgentProcess,
    *,
    snapshot_descendants: Callable[[int], list[ProcessEntry]] = read_descendants,
    kill_descendant: Callable[[ProcessEntry, int], bool] = kill_if_unchanged,
) -> None:
    """動いている claude を、claude のプロセスグループへのシグナルで段階的に止める。

    1. グループに signal.SIGINT を送り、process.wait(timeout=STOP_GRACE_S) で待つ
    2. まだ終わっていなければ (subprocess.TimeoutExpired) グループに signal.SIGTERM を送り、
       process.wait(timeout=TERMINATE_GRACE_S) で待つ
    3. まだ終わっていなければ、snapshot_descendants(process.pid) で claude の子孫を控えてから、グループに
       signal.SIGKILL を送り、process.wait() で終わるのを待つ。その後、控えた子孫それぞれに
       kill_descendant(<子孫>, signal.SIGKILL) を送る。snapshot_descendants が OSError か ValueError を
       送出したときは、控えた子孫を空として扱い、必ずグループへの SIGKILL を送る
    SIGINT か SIGTERM で claude が終わったときは、Bash ツールのコマンドは claude 自身が止めるため、子孫を控えない。
    SIGKILL で止めた claude は後始末できず、別のセッションにいる Bash ツールのコマンドが残りうるため、控えて止める
    (Claude Code 2.1.325 で 2026-11-17 に確かめた)。
    グループが無い (ProcessLookupError) ときは、送らずに待つ (例外にしない)。
    snapshot_descendants の既定は process_tree.read_descendants、kill_descendant の既定は
    process_tree.kill_if_unchanged (控えたときと同じプロセスのときだけ送る)。
    """
    for sig, grace_s in (
        (signal.SIGINT, STOP_GRACE_S),
        (signal.SIGTERM, TERMINATE_GRACE_S),
    ):
        _signal_group_if_present(process, sig)
        try:
            process.wait(timeout=grace_s)
            return
        except subprocess.TimeoutExpired:
            continue
    try:
        descendants = snapshot_descendants(process.pid)
    except (OSError, ValueError):
        # 子孫を控えられなくても、claude を止める SIGKILL は送り損ねない
        descendants = []
    _signal_group_if_present(process, signal.SIGKILL)
    process.wait()
    for descendant in descendants:
        kill_descendant(descendant, signal.SIGKILL)


def stop_reason_after_exit(
    remaining_s: float,
    last_subtype: str | None,
    remaining_budget_usd: float | None,
    resumes: int,
    session_id: str | None,
) -> StopReason | None:
    """期限より前にプロセスが終わったとき、止める理由を返す。起動し直すなら None。

    次の順に判定する:
        1. remaining_s (期限までの残りの秒数) が MIN_REMAINING_S 未満なら "deadline"
        2. last_subtype (今回の run の最後の result の subtype) が BUDGET_EXHAUSTED_SUBTYPE なら "budget_exhausted"
        3. remaining_budget_usd (run 全体の残りの予算。上限が無ければ None) が None でなく、
           float(format_budget_usd(remaining_budget_usd)) が 0 以下なら "budget_exhausted"
           (丸めると "0.0000" になる残りでは起動しない)
        4. resumes (起動し直した回数) が MAX_RESUMES 以上なら "resumes_exhausted"
        5. session_id (今回の run の最後の session_id) が None なら "no_session"
    """
    if remaining_s < MIN_REMAINING_S:
        return "deadline"
    if last_subtype == BUDGET_EXHAUSTED_SUBTYPE:
        return "budget_exhausted"
    if (
        remaining_budget_usd is not None
        and float(format_budget_usd(remaining_budget_usd)) <= 0
    ):
        return "budget_exhausted"
    if resumes >= MAX_RESUMES:
        return "resumes_exhausted"
    if session_id is None:
        return "no_session"
    return None


def _run_event(
    time: float,
    args: tuple[str, ...],
    reply: str,
    ok: bool = True,
    elapsed_s: float = 0.0,
) -> CommandEvent:
    return CommandEvent(
        time=time, command="run", args=args, reply=reply, ok=ok, elapsed_s=elapsed_s
    )


def run_agent_until(
    minutes: float,
    paths: WorkspacePaths,
    game: str,
    agent_commands: Sequence[str],
    options: LaunchOptions,
    *,
    start_process: StartProcess,
    clock: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    send: SendCommand,
    record: Callable[[CommandEvent], None],
    base_env: Mapping[str, str],
    shield: Shield = ignore_interrupts,
    defer: Shield = defer_interrupts,
) -> RunOutcome:
    """claude -p を起動し、期限 (開始時刻 + minutes 分) になったら止める。minutes は正の数を渡す。

    時計は 2 つ受け取る。monotonic (本番は time.monotonic) は期限・残り時間・経過時間 (elapsed_s) を計る単調時計で、
    clock (本番は time.time) は記録の時刻 (CommandEvent.time) に使う壁時計。壁時計は NTP などで進んだり戻ったりするため、
    期限の計算には使わない。期限は、最初の起動の前の monotonic() + minutes * 60。

    最初の起動の前に offset = agent_log_size(paths.agent_log_file) を覚える。以下の read_last_* と
    read_max_total_cost_usd には (paths.agent_log_file, offset) を渡し、今回の run の出力だけを読む。
    起動は start_process(args, paths.root, build_claude_env(base_env, paths.root), paths.agent_log_file,
    paths.agent_stderr_file) (claude のエージェント用のサブコマンドは paths.root に固定される)。
    起動 (最初の起動と起動し直し) は、プロセスを作って後片付けが参照する変数に入れるまでを with defer(): の中で行う
    (本番の defer は signal_guard.defer_interrupts。起動の直後に届いた Ctrl-C・SIGTERM・SIGHUP で、
    起動した claude を見失って止め損ねないように、シグナルを変数に入れた後まで遅らせる)。
    最初の起動のプロンプトは first_prompt(game) で、--resume は付けず、--max-budget-usd には options.max_budget_usd を渡す。

    POLL_INTERVAL_S ごとに (sleep(POLL_INTERVAL_S)) 次を確かめる:
        - プロセスが動いていて、monotonic() が期限以上なら、理由 "deadline" で終える (プロセスは後片付けで止める)
        - プロセスが終わっていたら、stop_reason_after_exit で判定する。渡す値は、期限までの残りの秒数 (期限 − monotonic())、
          read_last_result_subtype、残りの予算 (options.max_budget_usd が None でなければ
          options.max_budget_usd − read_max_total_cost_usd。read_max_total_cost_usd が None なら
          options.max_budget_usd のまま。上限が無ければ None)、起動し直した回数、read_last_session_id。
          理由が返ればその理由で終える。None なら RESUME_PROMPT と --resume <session_id> で起動し直す。
          options.max_budget_usd が None でなければ、dataclasses.replace(options, max_budget_usd=<残りの予算>) を
          build_claude_args に渡す (CLI は --max-budget-usd を 1 つのプロセスが使った分だけで判定するため、
          run 全体の上限にするには残りを渡す)

    後片付けは、終えるとき (例外・KeyboardInterrupt で抜けるときも finally で) に with shield(): の中で行う
    (本番の shield は signal_guard.ignore_interrupts で、後片付けの途中の Ctrl-C などで up ALL の前に抜けないようにする)。
    claude は新しいセッションで動くため、端末の Ctrl-C は claude に直接届かず、run の Python が KeyboardInterrupt として受け、
    この後片付けで claude を止める。
        1. 最後に起動したプロセスがまだ動いていれば、stop_process で止める
           (止めないと、up ALL の後もエージェントが入力を送り続けるため)。SIGINT か SIGTERM で止まれば、
           claude の Bash ツールのコマンドは claude 自身が止める。SIGKILL まで進んだときは、stop_process が
           控えた claude の子孫も止める (Claude Code 2.1.325 で 2026-11-17 に確かめたふるまい。モジュールの説明を参照)
        2. 理由が決まって終えるときは、停止のイベントを記録する
        3. 1・2 が例外になっても、send(["up", "ALL"], timeout_s=RELEASE_TIMEOUT_S) を送り、そのイベントを記録する
           (後片付けの間はシグナルを無視するため、デーモンが応答しなくても止まれるように待ち時間に上限を付ける)
    後片付けの中では claude を起動しない (無視の設定を子が受け継ぐため)。

    record で次のイベントを記録する (command は "run"、frame は None):
        - 起動: args=("start",)、reply="launched claude launch=1"、ok=True、elapsed_s=0.0
        - 起動し直し: args=("resume", <session_id>)、reply="launched claude launch=<起動した回数> resume=<session_id>"、ok=True、elapsed_s=0.0
        - 停止: args=("stop", <理由>)、reply="stopped reason=<理由> launches=<起動した回数>"、ok=True、elapsed_s=0.0
          (プロセスを止めた後に記録する。例外で抜けるときは記録しない)
        - up ALL: args=("release",)、reply=<デーモンの応答>、ok=<応答が error で始まらない>、
          elapsed_s=<送信にかかった秒数 (monotonic() の差)> (例外で抜けるときも記録する)
    各イベントの time はそのときの clock() (壁時計)。
    """
    log_path = paths.agent_log_file
    offset = agent_log_size(log_path)
    env = build_claude_env(base_env, paths.root)
    deadline = monotonic() + minutes * 60.0
    launches = 0
    resumes = 0
    process: AgentProcess | None = None
    reason: StopReason | None = None

    def launch(
        launch_options: LaunchOptions, prompt: str, session_id: str | None
    ) -> AgentProcess:
        args = build_claude_args(
            prompt, agent_commands, paths.instructions_file, launch_options, session_id
        )
        return start_process(args, paths.root, env, log_path, paths.agent_stderr_file)

    try:
        with defer():
            process = launch(options, first_prompt(game), None)
        launches = 1
        record(_run_event(clock(), ("start",), "launched claude launch=1"))
        while True:
            now = monotonic()
            if process.poll() is None:
                if now >= deadline:
                    reason = "deadline"
                    break
                sleep(POLL_INTERVAL_S)
                continue
            remaining_budget: float | None = None
            if options.max_budget_usd is not None:
                spent = read_max_total_cost_usd(log_path, offset)
                remaining_budget = options.max_budget_usd - (
                    spent if spent is not None else 0.0
                )
            session_id = read_last_session_id(log_path, offset)
            reason = stop_reason_after_exit(
                remaining_s=deadline - now,
                last_subtype=read_last_result_subtype(log_path, offset),
                remaining_budget_usd=remaining_budget,
                resumes=resumes,
                session_id=session_id,
            )
            # session_id が None なら reason は "no_session" になっている (下の起動のために型も絞る)
            if reason is not None or session_id is None:
                break
            resume_options = (
                options
                if remaining_budget is None
                else replace(options, max_budget_usd=remaining_budget)
            )
            with defer():
                process = launch(resume_options, RESUME_PROMPT, session_id)
            launches += 1
            resumes += 1
            record(
                _run_event(
                    clock(),
                    ("resume", session_id),
                    f"launched claude launch={launches} resume={session_id}",
                )
            )
    finally:
        with shield():
            try:
                if process is not None and process.poll() is None:
                    stop_process(process)
                if reason is not None:
                    record(
                        _run_event(
                            clock(),
                            ("stop", reason),
                            f"stopped reason={reason} launches={launches}",
                        )
                    )
            finally:
                sent_at = clock()
                started = monotonic()
                reply = send(["up", "ALL"], timeout_s=RELEASE_TIMEOUT_S)
                record(
                    _run_event(
                        sent_at,
                        ("release",),
                        reply,
                        ok=not reply.startswith("error"),
                        elapsed_s=monotonic() - started,
                    )
                )
    # reason が None のまま finally を抜けるのは例外のときだけで、そのときはここに来ない
    assert reason is not None
    return RunOutcome(reason=reason, launches=launches)
