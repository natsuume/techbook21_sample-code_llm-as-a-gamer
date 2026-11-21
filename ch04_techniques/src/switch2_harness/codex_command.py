"""OpenAI Codex CLI (codex exec) を、プレイヤーと批評役として起動するための引数・環境変数・設定ファイルの本文を作る。

どの関数も純粋関数 (ファイル・プロセスに触れない)。ファイルに書くのは codex_home、起動は agent_launcher と
reflection_critic が行う。

Codex は、作業ディレクトリーごとの専用の CODEX_HOME (codex_home_dir) で動かす。CODEX_HOME には、run の始めに次のものを置く:
    config.toml (codex_config_toml): ユーザーの ~/.codex/config.toml の代わりに読まれる設定。
        - 承認を求めない (approval_policy = "never")。Web 検索・MCP サーバー・plugin・apps・画像の生成・目標・
          コンピューターとブラウザーの操作・記憶・サブエージェント・同梱のスキルを止める
        - リポジトリーの AGENTS.md を読ませない (project_doc_max_bytes = 0)。指示は developer_instructions で渡す
        - 作業ディレクトリーの親の階層とユーザーのスキルは、skills.config で 1 つずつ止める
        - 権限プロファイル (player_permission_profile・critic_permission_profile) を定義し、プレイヤーの
          プロファイルを既定 (default_permissions) にする
    rules/switch2-harness.rules (codex_rules_text): `<ハーネスの実行ファイルの絶対パス> <エージェント用のサブコマンド> ...`
        だけをサンドボックスの外で動かす規則。デーモンのソケット (抽象名前空間の Unix ソケット) に届くのは、この規則に
        当たったコマンドだけ。ほかのコマンドは Codex のサンドボックスの中で動く。
        ハーネスの実行ファイルは、ch04_techniques の仮想環境の console script (harness_executable。作業ディレクトリーの外)。
        `uv run switch2-harness ...` は許さない。uv は cwd (作業ディレクトリー) から上にたどって見つけた pyproject.toml・
        uv.toml・.python-version・.venv を使うため、エージェントが作業ディレクトリーにそれらを置くと、サンドボックスの外で
        任意のコードを動かせる (2026-11-21 に、作業ディレクトリーに置いた pyproject.toml のビルドバックエンドが、
        `uv run switch2-harness status` で作業ディレクトリーの外にファイルを書くことを確かめた)
    auth.json: ユーザーの ${CODEX_HOME:-~/.codex}/auth.json へのシンボリックリンク (codex_auth_source)

権限プロファイルの値の優先は deny > write > read で、作業ディレクトリーの下では、細かいパスに書いた read が、
親に書いた write より優先される (2026-11-21、codex-cli 0.171.0 で `codex sandbox` で確かめた)。ファイル 1 つだけを
write にすること (例 "notes.md" = "write") は、サンドボックスの起動 (bwrap) が失敗するためできない。そこで、書き換えられる
ファイルがあるときは作業ディレクトリーの直下を write にし、ハーネスが置くもの (harness.json・指示ファイル・frames/・
multi-frames/・records/。codex_home.prepare_codex_home が選ぶ) を read にする。直下に新しいファイルを作ることは防げない。
書き換えられるファイルが無いときは、直下も read にする (書ける場所を作らない)。
エージェントが作ったもの (シンボリックリンクを含む) は read にしない。作業ディレクトリーの外を指すリンクを read に書くと、
リンク先がサンドボックスから読めるようになるおそれがあるため。
read にするのは、サンドボックスの起動の時点で直下にあるものだけにする。無いパスを read にすると、サンドボックスが
その場所にマウントの的として空のファイルを作り、codex を SIGINT で止めるとそれが作業ディレクトリーに残る
(2026-11-21 に multi-frames で確かめた。後で look が multi-frames/ を作れなくなる)。直下を write にしたときは、
同じ理由で .agents・.aws・.codex・.git の空のディレクトリーが作業ディレクトリーに残る。
"""

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from switch2_harness.workspace_pin import PINNED_WORKSPACE_ENV

CODEX_COMMAND = "codex"

# プレイヤーのモデルの既定は agent_kind.DEFAULT_MODELS。批評役のモデルと effort (Claude 版の批評役と同じ medium)
CODEX_CRITIC_MODEL = "gpt-6.2-sol"
CODEX_CRITIC_EFFORT = "medium"

# 専用の CODEX_HOME を置くディレクトリーの、キャッシュのディレクトリー (${XDG_CACHE_HOME:-~/.cache}) からの相対パス
CODEX_HOMES_RELATIVE_DIR = Path("switch2-harness") / "codex-home"
# CODEX_HOME の名前に使う、作業ディレクトリーの絶対パスの SHA-256 の 16 進の桁数
CODEX_HOME_HASH_LENGTH = 12

# 専用の CODEX_HOME の中のファイル
CODEX_CONFIG_FILE_NAME = "config.toml"
CODEX_RULES_DIR_NAME = "rules"
CODEX_RULES_FILE_NAME = "switch2-harness.rules"
CODEX_AUTH_FILE_NAME = "auth.json"

# Codex の設定の場所を変える環境変数と、その既定 ($HOME からの相対パス)
CODEX_HOME_ENV = "CODEX_HOME"
DEFAULT_USER_CODEX_HOME = Path(".codex")

# 権限プロファイルの名前
PLAYER_PERMISSION_PROFILE = "switch2-harness-player"
CRITIC_PERMISSION_PROFILE = "switch2-harness-critic"

# 権限プロファイルの値
FILESYSTEM_READ = "read"
FILESYSTEM_WRITE = "write"

# config.toml の features で止める機能
DISABLED_FEATURES: tuple[str, ...] = (
    "plugins",
    "apps",
    "image_generation",
    "goals",
    "computer_use",
    "browser_use",
    "memories",
)

# 批評役で、さらに --disable で止める機能 (シェルのコマンドと画像を開くツール)
CRITIC_DISABLED_FEATURES: tuple[str, ...] = ("shell_tool", "unified_exec", "view_image")

# ハーネスの console script の名前 (仮想環境の bin/ の下)
HARNESS_EXECUTABLE_NAME = "switch2-harness"

# 子プロセスに渡さない環境変数。名前がこの接尾辞で終わるもの (OPENAI_API_KEY・CODEX_API_KEY・ANTHROPIC_API_KEY など。
# Codex はユーザーのログインで動かす) と、これらの接頭辞で始まるもの。CLAUDE は run を起動した Claude Code の
# セッションの変数。PYTHON は PYTHONPATH などで、渡すと、サンドボックスの外で動くハーネスの実行ファイルが
# 作業ディレクトリーに置かれたファイルを読むようになりうるため
HIDDEN_ENV_SUFFIX = "_API_KEY"
HIDDEN_ENV_PREFIXES: tuple[str, ...] = ("CLAUDE", "PYTHON")


def toml_basic_string(value: str) -> str:
    """value を TOML の基本文字列 ("..." で囲んだもの) にして返す。

    " と \\ の前に \\ を置き、制御文字 (U+0000〜U+001F と U+007F) は \\uXXXX にする。ほかの文字はそのまま入れる。
    例: 'a"b\\n' → '"a\\"b\\u000A"'。
    """
    escaped: list[str] = []
    for character in value:
        if character in ('"', "\\"):
            escaped.append("\\" + character)
        elif ord(character) < 0x20 or ord(character) == 0x7F:
            escaped.append(f"\\u{ord(character):04X}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def _toml_key(key: str) -> str:
    """TOML のキーを返す。英数字・_・- だけなら裸のキー、それ以外は基本文字列にする。"""
    if key and all(c.isascii() and (c.isalnum() or c in "_-") for c in key):
        return key
    return toml_basic_string(key)


def toml_value(value: object) -> str:
    """value を 1 行の TOML の値にする。

    str は基本文字列、bool は true/false、int はそのまま、dict はインラインテーブル ({ k = v, ... }。
    空なら {})、list・tuple は配列 ([v, ...])。ほかの型は TypeError にする。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return toml_basic_string(value)
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        items = ", ".join(
            f"{_toml_key(str(k))} = {toml_value(v)}" for k, v in value.items()
        )
        return "{ " + items + " }"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} as TOML")


def codex_home_dir(workspace_root: Path, environ: Mapping[str, str]) -> Path:
    """作業ディレクトリー workspace_root (絶対パス) の専用の CODEX_HOME のパスを返す (作らない)。

    <キャッシュ>/switch2-harness/codex-home/<workspace_root の名前>-<str(workspace_root) の SHA-256 の先頭 12 桁>。
    <キャッシュ> は environ の XDG_CACHE_HOME があればそれ、無ければ $HOME/.cache。
    作業ディレクトリーの外 (エージェントから見えない場所) で、/tmp の外に置くため。
    XDG_CACHE_HOME も HOME も無ければ ValueError にする。
    """
    cache = environ.get("XDG_CACHE_HOME")
    if not cache:
        home = environ.get("HOME")
        if not home:
            raise ValueError("HOME is not set; cannot place the CODEX_HOME for codex")
        cache = str(Path(home) / ".cache")
    digest = hashlib.sha256(str(workspace_root).encode("utf-8")).hexdigest()
    name = f"{workspace_root.name}-{digest[:CODEX_HOME_HASH_LENGTH]}"
    return Path(cache) / CODEX_HOMES_RELATIVE_DIR / name


def codex_auth_source(environ: Mapping[str, str]) -> Path:
    """ユーザーの Codex の認証のファイル ${CODEX_HOME:-$HOME/.codex}/auth.json のパスを返す。

    CODEX_HOME も HOME も無ければ ValueError にする。
    """
    user_home = environ.get(CODEX_HOME_ENV)
    if user_home:
        return Path(user_home) / CODEX_AUTH_FILE_NAME
    home = environ.get("HOME")
    if not home:
        raise ValueError("HOME is not set; cannot find the codex login (auth.json)")
    return Path(home) / DEFAULT_USER_CODEX_HOME / CODEX_AUTH_FILE_NAME


def is_kept_codex_env_name(name: str) -> bool:
    """環境変数の名前 name を codex に渡すなら True を返す。

    HIDDEN_ENV_SUFFIX ("_API_KEY") で終わる名前と、HIDDEN_ENV_PREFIXES ("CLAUDE"・"PYTHON") のどれかで始まる名前は
    渡さない。
    """
    return not (
        name.endswith(HIDDEN_ENV_SUFFIX) or name.startswith(HIDDEN_ENV_PREFIXES)
    )


def build_codex_env(
    base_env: Mapping[str, str], codex_home: Path, workspace_root: Path
) -> dict[str, str]:
    """codex に渡す環境変数の辞書を返す。base_env は変えない。

    base_env のうち is_kept_codex_env_name に当たる変数だけを残し、CODEX_HOME = str(codex_home) と、
    workspace_pin.PINNED_WORKSPACE_ENV = str(workspace_root) を足す (同じ名前は上書きする)。
    PINNED_WORKSPACE_ENV は Codex のシェルのコマンドに引き継がれ、エージェント用のサブコマンドを
    この作業ディレクトリーに固定する (2026-11-21 に確かめた)。
    """
    env = {
        name: value for name, value in base_env.items() if is_kept_codex_env_name(name)
    }
    env[CODEX_HOME_ENV] = str(codex_home)
    env[PINNED_WORKSPACE_ENV] = str(workspace_root)
    return env


def player_permission_profile(
    native_codex_dir: Path,
    root_entries: Collection[str],
    writable_files: Sequence[str],
) -> dict[str, object]:
    """プレイヤーの権限プロファイル (config.toml の permissions.<名前> の値) を返す。

    {"filesystem": {":minimal": "read", "<native_codex_dir>": "read", ":workspace_roots": <作業ディレクトリー>}}
    <作業ディレクトリー> は、writable_files (書き換えられるファイルの名前) が空なら {".": "read"}。
    空でなければ {".": "write"} に、root_entries (サンドボックスを起動する前に作業ディレクトリーの直下にあるものの名前) を
    名前の順に並べ、writable_files を除いたものを "read" で続ける。
    :minimal はシェルが動くのに要るシステムのファイル。native_codex_dir (Codex 本体のディレクトリー) を読めないと、
    サンドボックスの補助 (Codex が自分を bwrap の中で実行する) が起動できず、シェルのコマンドがすべて失敗する。
    作業ディレクトリーの外は、この 2 つのほかは見えない。
    """
    workspace: dict[str, str]
    if not writable_files:
        workspace = {".": FILESYSTEM_READ}
    else:
        workspace = {".": FILESYSTEM_WRITE}
        for name in sorted(set(root_entries)):
            if name not in writable_files:
                workspace[name] = FILESYSTEM_READ
    return {
        "filesystem": {
            ":minimal": FILESYSTEM_READ,
            str(native_codex_dir): FILESYSTEM_READ,
            ":workspace_roots": workspace,
        }
    }


def critic_permission_profile(native_codex_dir: Path) -> dict[str, object]:
    """批評役の権限プロファイルを返す。どこにも書けない。

    {"filesystem": {":minimal": "read", "<native_codex_dir>": "read", ":workspace_roots": {".": "read"}}}
    """
    return {
        "filesystem": {
            ":minimal": FILESYSTEM_READ,
            str(native_codex_dir): FILESYSTEM_READ,
            ":workspace_roots": {".": FILESYSTEM_READ},
        }
    }


def codex_config_toml(
    native_codex_dir: Path,
    root_entries: Collection[str],
    writable_files: Sequence[str],
    disabled_skill_files: Sequence[Path] = (),
) -> str:
    """専用の CODEX_HOME の config.toml の本文を返す (末尾は改行 1 つ)。

    先頭のコメントの行に続けて、次の順に `<キー> = <toml_value の値>` の行を並べる:
        approval_policy = "never"
        web_search = "disabled"
        project_doc_max_bytes = 0
        mcp_servers = {}
        features = { <DISABLED_FEATURES のそれぞれ> = false, ... }
        agents = { enabled = false }
        skills = { bundled = { enabled = false }[, config = [{ path = "<SKILL.md>", enabled = false }, ...]] }
        default_permissions = "<PLAYER_PERMISSION_PROFILE>"
        permissions = { <PLAYER_PERMISSION_PROFILE> = player_permission_profile(...),
                        <CRITIC_PERMISSION_PROFILE> = critic_permission_profile(...) }
    skills の config は、disabled_skill_files が空なら付けない。
    サブエージェントは agents.enabled = false で止める (features の multi_agent は非推奨の警告が出るため使わない)。
    """
    skills: dict[str, object] = {"bundled": {"enabled": False}}
    if disabled_skill_files:
        skills["config"] = [
            {"path": str(path), "enabled": False} for path in disabled_skill_files
        ]
    settings: dict[str, object] = {
        "approval_policy": "never",
        "web_search": "disabled",
        "project_doc_max_bytes": 0,
        "mcp_servers": {},
        "features": {feature: False for feature in DISABLED_FEATURES},
        "agents": {"enabled": False},
        "skills": skills,
        "default_permissions": PLAYER_PERMISSION_PROFILE,
        "permissions": {
            PLAYER_PERMISSION_PROFILE: player_permission_profile(
                native_codex_dir, root_entries, writable_files
            ),
            CRITIC_PERMISSION_PROFILE: critic_permission_profile(native_codex_dir),
        },
    }
    lines = [
        "# switch2-harness が run の始めに書き直す設定 (この作業ディレクトリーの Codex 専用)",
        *(f"{_toml_key(key)} = {toml_value(value)}" for key, value in settings.items()),
    ]
    return "\n".join(lines) + "\n"


def harness_executable(venv_prefix: Path) -> Path:
    """仮想環境 venv_prefix (例: <ch04_techniques>/.venv) のハーネスの console script のパス
    venv_prefix / "bin" / HARNESS_EXECUTABLE_NAME を返す。"""
    return venv_prefix / "bin" / HARNESS_EXECUTABLE_NAME


def codex_rules_text(executable: Path, agent_commands: Sequence[str]) -> str:
    """専用の CODEX_HOME の rules/switch2-harness.rules の本文を返す (末尾は改行 1 つ)。

    `<executable> <agent_commands のどれか> ...` をサンドボックスの外で動かす prefix_rule を 1 つ書く:
        prefix_rule(
            pattern = ["<executable の絶対パス>", ["look", "press", "status", "finish"]],
            decision = "allow",
        )
    文字列は json.dumps で書く。executable はハーネスの実行ファイルの絶対パス (harness_executable)。
    前に環境変数を付けた形 (`X=1 <executable> ...`・`env X=1 <executable> ...`)、相対パスやほかの場所の
    switch2-harness、`uv run switch2-harness ...`、ほかのサブコマンド (init など) は当たらない
    (2026-11-21 に `codex execpolicy check` で確かめた)。
    """
    commands = ", ".join(json.dumps(command) for command in agent_commands)
    return (
        "# switch2-harness が run の始めに書き直す規則。エージェント用のサブコマンドだけを、サンドボックスの外で動かす\n"
        "prefix_rule(\n"
        f"    pattern = [{json.dumps(str(executable))}, [{commands}]],\n"
        '    decision = "allow",\n'
        ")\n"
    )


def build_codex_exec_args(
    prompt: str,
    model: str,
    effort: str | None,
    instructions: str,
    resume_thread_id: str | None = None,
) -> list[str]:
    """プレイヤーの codex exec の起動引数を返す。順序は次のとおり:

        codex exec [resume] --json --skip-git-repo-check
          -m <model>
          [-c model_reasoning_effort=<effort の TOML の文字列>]
          -c developer_instructions=<instructions の TOML の文字列>
          [<resume_thread_id>] <prompt>
    resume_thread_id が None でなければ、exec の直後に resume を、prompt の前にその ID を置く (codex exec resume)。
    [] の中は、値が None でないときだけ付ける。作業ディレクトリーは起動するプロセスの cwd で決まる。
    instructions (作業ディレクトリーの AGENTS.md) は、起動し直すときも同じ値を渡す。
    """
    args = [CODEX_COMMAND, "exec"]
    if resume_thread_id is not None:
        args.append("resume")
    args += ["--json", "--skip-git-repo-check", "-m", model]
    if effort is not None:
        args += ["-c", f"model_reasoning_effort={toml_basic_string(effort)}"]
    args += ["-c", f"developer_instructions={toml_basic_string(instructions)}"]
    if resume_thread_id is not None:
        args.append(resume_thread_id)
    args.append(prompt)
    return args


def build_codex_critic_args(image_paths: Sequence[Path]) -> list[str]:
    """批評役の codex exec の起動引数を返す。プロンプトは標準入力で渡す (位置引数のプロンプトは付けない)。

        codex exec --json --ephemeral --skip-git-repo-check --ignore-rules
          -m <CODEX_CRITIC_MODEL> -c model_reasoning_effort="<CODEX_CRITIC_EFFORT>"
          -c default_permissions="<CRITIC_PERMISSION_PROFILE>"
          --disable shell_tool --disable unified_exec --disable view_image
          [-i <画像> ...]
    --ignore-rules で、プレイヤーと共有する CODEX_HOME の rules (エージェント用のサブコマンドをサンドボックスの外で
    動かす規則) を読ませない。--ephemeral で、会話を CODEX_HOME に残さない。
    image_paths の画像を、順に `-i <絶対パス>` で添付する。
    """
    args = [
        CODEX_COMMAND,
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-rules",
        "-m",
        CODEX_CRITIC_MODEL,
        "-c",
        f"model_reasoning_effort={toml_basic_string(CODEX_CRITIC_EFFORT)}",
        "-c",
        f"default_permissions={toml_basic_string(CRITIC_PERMISSION_PROFILE)}",
    ]
    for feature in CRITIC_DISABLED_FEATURES:
        args += ["--disable", feature]
    for path in image_paths:
        args += ["-i", str(path)]
    return args
