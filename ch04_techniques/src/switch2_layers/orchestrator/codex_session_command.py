"""主ループの Codex (codex app-server を標準入出力の JSON-RPC で開いたままにするもの) の起動の引数・子プロセスの
環境変数・権限プロファイル・初期化とスレッドの開始の引数を作る (どれも純粋な関数)。

コマンドの形 (build_codex_command):
    codex app-server --disable <CODEX_DISABLED_FEATURES のそれぞれ>
        -c web_search="disabled" -c mcp_servers={} -c skills.bundled.enabled=false -c project_doc_max_bytes=0
        [-c skills.config=[{path="<止めるスキルの SKILL.md>", enabled=false}, ...]]
子プロセスの作業ディレクトリーは、実行ディレクトリー (シンボリックリンクを解決した絶対パス) にする。
モデル・変わらない指示・権限はスレッドの開始の引数 (build_thread_start_params) で、effort はターンの開始の引数
(codex_messages.build_turn_start_params) で渡す。

使えるツールと範囲 (Codex に組み込まれたツールと権限プロファイルで作る):
    - シェル (コマンドの実行)・apply_patch (ファイルの編集)・view_image (画像を見る) を使う。シェルのコマンドと
      apply_patch は、Codex のサンドボックスの中で、権限プロファイル (codex_permission_profile) の範囲だけで動く
    - 読める: 実行ディレクトリーの全体 (".": read) から、Claude 版で読ませない項目
      (claude_session_command.ROOT_ENTRY_ACCESS で HIDDEN_FILE・HIDDEN_DIRECTORY にしたもの) を deny で除く。
      値の優先は deny > write > read なので、"." を deny にして一部を read にすることはできない。実行ディレクトリーに
      まだ無い項目を deny にしても、シェルは動く (2026-11-21 に確かめた)
    - 書ける: notes/ の下だけ ("notes": write)。実行ディレクトリーの直下などへの書き込みは拒まれる
    - :minimal (シェルが動くのに要るシステムのファイル) と、Codex 本体のディレクトリーを読める。Codex 本体の
      ディレクトリーを読めないと、サンドボックスの補助 (Codex が自分を bwrap の中で実行する) が起動できず、シェルの
      コマンドがすべて失敗する。場所は起動した後に探す (codex_main_loop_session.find_native_codex_dir)
    - 実行ディレクトリーの外のファイルは見えない (view_image で外の画像を開いても見えない)
    - 承認は求めさせない (approvalPolicy never)。サンドボックスの外で動かす承認の要求が来たら、断る
      (codex_messages.server_request_reply)
ユーザーの設定・plugin・スキル・AGENTS.md の扱い:
    - CODEX_DISABLED_FEATURES の機能 (plugin・apps・複数のエージェント・画像の生成・目標・コンピューターとブラウザーの
      操作・記憶) を --disable で止め、web_search を "disabled" にする。plugin のツールは消えるが、collaboration
      (サブエージェント) の名前空間は残る。サブエージェントのスレッドの出力は、主ループの出来事にしない
      (codex_messages.notification_events がスレッドの ID で選ぶ)
    - mcp_servers={} で、ユーザーの設定の MCP サーバーを読ませない
    - 同梱のスキル (imagegen・openai-docs・skill-creator・skill-installer) は skills.bundled.enabled=false で止め、
      実行ディレクトリーの親の階層の .agents/skills/ とユーザーのスキルは skills.config で 1 つずつ止める
      (codex_skill_paths.find_codex_skill_files_to_disable)。実行ディレクトリーの .agents/skills/
      (CODEX_SKILLS_RELATIVE_DIR) のスキルだけが一覧に載る
    - project_doc_max_bytes=0 で、リポジトリーの AGENTS.md (リポジトリーのルートから作業ディレクトリーまで) を
      読ませない。$CODEX_HOME/AGENTS.md (既定 ~/.codex/AGENTS.md) は、これでも読まれる (止める設定は見つかって
      いない)
    - ~/.codex/config.toml のほかの設定 (service_tier など) は効く (Claude 版の --setting-sources project のように、
      ユーザーの設定ファイルをまとめて読ませない方法は使っていない)
    - 子プロセスの環境変数から、ほかの層の API キー (HIDDEN_ENV_NAMES。OPENAI_API_KEY を含む) と CODEX_AUTH_ENV_NAMES を
      除き、ユーザーのログイン (codex login) で動かす (build_codex_env)

実際の起動で確かめたこと (2026-11-21、codex-cli 0.171.0):
    - この形のコマンドと権限プロファイルで、シェルの `ls frames/latest` と apply_patch の notes/ への書き込みは通り、
      シェルの `cat run.jsonl` と orchestrator/ の中の読み取りは権限プロファイルで拒まれる。実行ディレクトリーの直下への
      シェルの書き込み (`echo x > root.txt`) は Read-only file system で拒まれる。view_image で frames/latest/ の画像を
      開ける
    - 権限プロファイルにはファイルの範囲だけを書いているが、シェルの curl は外のホストの名前を引けずに失敗する
    - skills/list の結果で、skills.config で止めた親の階層のスキルは enabled が false になり、同梱のスキルは載らない。
      モデルが答えたスキルの一覧も、実行ディレクトリーのスキルだけ
    - codex debug prompt-input で、project_doc_max_bytes=0 のときはリポジトリーの AGENTS.md の目印が消え、
      CODEX_HOME の AGENTS.md の目印は残る
"""

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from switch2_layers.deep.claude_code_command import build_child_env
from switch2_layers.orchestrator.claude_session_command import (
    ROOT_ENTRY_ACCESS,
    RootEntryAccess,
)
from switch2_layers.run_directory import NOTES_DIR, RunPaths

CODEX_EXECUTABLE = "codex"
# --codex-model の既定値
DEFAULT_CODEX_MODEL = "gpt-6.2-sol"
# --disable で止める Codex の機能 (codex debug prompt-input で、止めた後もシェル・apply_patch・view_image が残ることを
# 確かめた)
CODEX_DISABLED_FEATURES: tuple[str, ...] = (
    "plugins",
    "apps",
    "multi_agent",
    "image_generation",
    "goals",
    "computer_use",
    "browser_use",
    "memories",
)
# -c で渡す設定の上書き (この順に渡す。値は TOML)
CODEX_CONFIG_OVERRIDES: tuple[str, ...] = (
    'web_search="disabled"',
    "mcp_servers={}",
    "skills.bundled.enabled=false",
    "project_doc_max_bytes=0",
)
# 子プロセスに渡さない、Codex の認証の環境変数 (ユーザーのログインで動かすため)
CODEX_AUTH_ENV_NAMES: tuple[str, ...] = ("CODEX_API_KEY",)
# Codex がスキルを読むディレクトリーの、実行ディレクトリーからの相対パス。本体の操作のスキルとゲームの内容のスキルを
# ここに写す (skill_copy)
CODEX_SKILLS_RELATIVE_DIR = Path(".agents") / "skills"
# スレッドの権限プロファイルの名前 (thread/start の permissions と config の default_permissions)
CODEX_PERMISSION_PROFILE = "switch2-layers"
# 承認の求め方 (never: 求めない)
CODEX_APPROVAL_POLICY = "never"
# initialize の clientInfo
CODEX_CLIENT_NAME = "switch2-layers"
CODEX_CLIENT_VERSION = "0.1.0"
# 権限プロファイルの値
FILESYSTEM_READ = "read"
FILESYSTEM_WRITE = "write"
FILESYSTEM_DENY = "deny"


def codex_skills_dir(paths: RunPaths) -> Path:
    """主ループの Codex のスキルの写し先 paths.root / CODEX_SKILLS_RELATIVE_DIR を返す。"""
    return paths.root / CODEX_SKILLS_RELATIVE_DIR


def toml_basic_string(value: str) -> str:
    """value を TOML の基本文字列 ("..." で囲んだもの) にして返す。

    " と \\ の前に \\ を置き、制御文字 (U+0000〜U+001F と U+007F) は \\uXXXX にする。ほかの文字はそのまま入れる。
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


def skills_disable_override(skill_files: Sequence[Path]) -> str:
    """skill_files (止めるスキルの SKILL.md の絶対パス) を止める -c の値
    `skills.config=[{path="<パス>", enabled=false}, ...]` を返す (skill_files の順)。"""
    entries = ", ".join(
        "{path=" + toml_basic_string(str(path)) + ", enabled=false}"
        for path in skill_files
    )
    return f"skills.config=[{entries}]"


def build_codex_command(
    executable: str, disabled_skill_files: Sequence[Path]
) -> list[str]:
    """主ループの codex app-server の引数の列を返す。

    [executable, "app-server", "--disable", <機能>, ... (CODEX_DISABLED_FEATURES の順),
     "-c", <CODEX_CONFIG_OVERRIDES のそれぞれ>, ..., "-c", skills_disable_override(disabled_skill_files)]
    disabled_skill_files が空なら、最後の skills.config は付けない。
    """
    command = [executable, "app-server"]
    for feature in CODEX_DISABLED_FEATURES:
        command += ["--disable", feature]
    for override in CODEX_CONFIG_OVERRIDES:
        command += ["-c", override]
    if disabled_skill_files:
        command += ["-c", skills_disable_override(disabled_skill_files)]
    return command


def build_codex_env(
    environ: Mapping[str, str], hidden_names: Collection[str]
) -> dict[str, str]:
    """子プロセスに渡す環境変数の辞書を返す。

    environ から、deep の claude_code_command.build_child_env(environ, hidden_names) が除く変数 (hidden_names・
    PARENT_SESSION_ENV_NAMES・SETTING_OVERRIDE_ENV_NAMES) と、CODEX_AUTH_ENV_NAMES を除いた新しい辞書を返す
    (environ は変えない)。HOME・PATH・CODEX_HOME などは残す。
    """
    env = build_child_env(environ, hidden_names)
    return {
        name: value for name, value in env.items() if name not in CODEX_AUTH_ENV_NAMES
    }


def codex_permission_profile(native_codex_dir: Path) -> dict[str, object]:
    """スレッドの権限プロファイル 1 つ (config の permissions.<名前> の値) を返す。

    {"filesystem": {":minimal": "read", "<native_codex_dir>": "read",
                    ":workspace_roots": {".": "read", "notes": "write", <隠す項目>: "deny", ...}}}
    隠す項目は、claude_session_command.ROOT_ENTRY_ACCESS で HIDDEN_FILE か HIDDEN_DIRECTORY にした項目
    (ROOT_ENTRY_ACCESS の順)。native_codex_dir は Codex 本体のディレクトリーの絶対パス。
    """
    workspace: dict[str, str] = {".": FILESYSTEM_READ, NOTES_DIR: FILESYSTEM_WRITE}
    for name, access in ROOT_ENTRY_ACCESS.items():
        if access is not RootEntryAccess.READABLE:
            workspace[name] = FILESYSTEM_DENY
    return {
        "filesystem": {
            ":minimal": FILESYSTEM_READ,
            str(native_codex_dir): FILESYSTEM_READ,
            ":workspace_roots": workspace,
        }
    }


def build_thread_config(native_codex_dir: Path) -> dict[str, object]:
    """thread/start の config (config.toml の上書き) の辞書を返す。

    {"default_permissions": CODEX_PERMISSION_PROFILE,
     "permissions": {CODEX_PERMISSION_PROFILE: codex_permission_profile(native_codex_dir)}}
    """
    return {
        "default_permissions": CODEX_PERMISSION_PROFILE,
        "permissions": {
            CODEX_PERMISSION_PROFILE: codex_permission_profile(native_codex_dir)
        },
    }


def build_initialize_params() -> dict[str, object]:
    """initialize の引数を返す。

    {"clientInfo": {"name": CODEX_CLIENT_NAME, "version": CODEX_CLIENT_VERSION},
     "capabilities": {"experimentalApi": true}}。experimentalApi が無いと、thread/start の permissions が誤りになる。
    """
    return {
        "clientInfo": {"name": CODEX_CLIENT_NAME, "version": CODEX_CLIENT_VERSION},
        "capabilities": {"experimentalApi": True},
    }


def build_thread_start_params(
    run_dir: Path, model: str, instructions: str, native_codex_dir: Path
) -> dict[str, object]:
    """thread/start の引数を返す。

    {"cwd": str(run_dir), "model": model, "developerInstructions": instructions, "approvalPolicy": "never",
     "ephemeral": true, "permissions": CODEX_PERMISSION_PROFILE, "config": build_thread_config(native_codex_dir)}
    run_dir は実行ディレクトリーのシンボリックリンクを解決した絶対パス。ephemeral のスレッドは ~/.codex に残らない。
    instructions が空なら ValueError にする。
    """
    if not instructions:
        raise ValueError("instructions must not be empty")
    return {
        "cwd": str(run_dir),
        "model": model,
        "developerInstructions": instructions,
        "approvalPolicy": CODEX_APPROVAL_POLICY,
        "ephemeral": True,
        "permissions": CODEX_PERMISSION_PROFILE,
        "config": build_thread_config(native_codex_dir),
    }
