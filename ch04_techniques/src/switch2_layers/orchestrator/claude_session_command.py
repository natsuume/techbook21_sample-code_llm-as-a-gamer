"""主ループの Claude Code (claude -p を stream-json の入出力で開いたままにするもの) の起動の引数・権限のルール・
--settings の中身・子プロセスの環境変数を作る (どれも純粋な関数)。

コマンドの形 (build_session_command):
    claude -p --input-format stream-json --output-format stream-json --verbose
        --model claude-opus-5-5 --effort <effort。既定 medium>
        --permission-mode dontAsk --setting-sources project --strict-mcp-config
        --settings <build_session_settings の JSON>
        --tools Read,Glob,Grep,Write,Edit,Skill
        --allowedTools <build_allowed_rules の結果をカンマでつないだ 1 つの文字列>
        --disallowedTools <build_disallowed_rules の結果をカンマでつないだ 1 つの文字列>
        --append-system-prompt <変わらない指示>
        <autocompact_arguments の結果>
子プロセスの作業ディレクトリーは、実行ディレクトリー (シンボリックリンクを解決した絶対パス) にする。

使えるツールと範囲:
    - Read・Glob・Grep: frames/・notes/・.claude/skills/。dontAsk のモードでは、作業ディレクトリーの中の読み取りは
      許すルールが無くても通るため、実行ディレクトリーの直下のほかの項目 (ROOT_ENTRY_ACCESS で HIDDEN_FILE・
      HIDDEN_DIRECTORY にしたもの) を、拒むルールで読めなくする
    - Write・Edit: notes/ の下だけ (Edit(//<実行ディレクトリー>/notes/**) のルールは Write にも当てはまる)
    - Skill: 実行ディレクトリーの .claude/skills/ のスキル。親のディレクトリーの .claude/skills/ のスキルと、
      組み込みのスキルのうち disableBundledSkills でも残るもの (BUNDLED_SKILLS_TO_HIDE) は、skillOverrides で off にする
    - Bash・PowerShell・WebSearch・WebFetch・Agent と MCP のツール (mcp__*) は、拒むルールで外す
    - dontAsk のモードでは、.claude/ の下 (notes/.claude/ を含む) への Write は保護されたパスとして拒まれる。
      そのため、ゲームの内容のスキルは notes/skills/game-<名前>/SKILL.md に書かせ、主ループのコードが写す
      (orchestrator.skill_copy)
ユーザーの設定・plugin・hook・CLAUDE.md・自動メモリ・MCP を読ませない方法:
    - --setting-sources project: ユーザーとローカルの設定ファイルを読まない
    - --strict-mcp-config: --mcp-config で渡したもの以外の MCP サーバーを読まない (--mcp-config は渡さない)
    - --settings: SESSION_SETTINGS_BASE と skillOverrides (build_session_settings)。crossSessionInbound を refuse にし、
      ほかのセッションから届いたメッセージを Claude に渡さずに捨てる
    - --restricted と --safe-mode は、実行ディレクトリーのスキルも読まなくなるため付けない。--bare は、認証が
      ANTHROPIC_API_KEY だけになるため付けない
    - 子プロセスの環境変数から、deep の claude_code_command.build_child_env が除く変数に加えて、AUTH_ENV_NAMES を除き、
      サブスクリプションのログインで動かす (build_session_env)

実際の起動で確かめたこと (2026-11-16、Claude Code 2.1.325。build_session_command と同じ引数で起動した):
    - system/init の行: skills は実行ディレクトリーの .claude/skills/ のスキルと組み込みの plugin のスキル
      (plugin-authoring) だけ。plugins は組み込みの 4 つ (path は builtin) だけ。mcp_servers は空。tools は
      Edit・Glob・Grep・Read・Skill・Write。apiKeySource は none で、ターンは result (success) まで終わる
    - 作業ディレクトリーの CLAUDE.md・AGENTS.md に書いた目印の語が、答えに出ない
    - notes/ と notes/skills/game-<名前>/SKILL.md への Write は通る。frames/・.claude/skills/・notes/.claude/skills/・
      実行ディレクトリーの直下の新しいファイルへの Write は拒まれる
    - 直下の run.jsonl の Read は拒むルールで拒まれ、frames/・notes/・.claude/skills/ の Read は通る。作業ディレクトリー
      全体の Grep にも run.jsonl の中身は出ない
    - --settings の crossSessionInbound が refuse なら、inbox のソケットに届いた行は Claude に渡らない
      (accept にした同じ形のセッションには渡り、新しいターンが始まる)。init の行の messaging_socket_path は、
      refuse でも出る
    - 各ユーザーのターンの最初に、system/init の行が出る
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from switch2_layers.deep.claude_code_command import (
    AGENTS_MD_INSTRUCTION_FILES,
    AGENTS_MD_PLUGIN_ID,
    CLAUDE_EXECUTABLE,
    HIDDEN_ENV_NAMES,
)
from switch2_layers.run_directory import (
    CLAUDE_DIR,
    DEEP_DIR,
    FRAMES_DIR,
    FRAMES_LOG_FILE,
    MIDDLE_DIR,
    NOTES_DIR,
    OPERATIONS_LOG_FILE,
    ORCHESTRATOR_DIR,
    PROGRESS_DIR,
    RUN_LOG_FILE,
    SAFETY_LOG_FILE,
    SURFACE_LOG_FILE,
    SURFACE_REQUESTS_LOG_FILE,
    RunPaths,
)

ORCHESTRATOR_MODEL = "claude-opus-5-5"
ORCHESTRATOR_EFFORT = "medium"  # --orchestrator-effort の既定値
# 許すルールが無く、確認が要る操作をすべて拒むモード。bypassPermissions は使わない
ORCHESTRATOR_PERMISSION_MODE = "dontAsk"
# 読む設定ファイルの源 (--setting-sources)
ORCHESTRATOR_SETTING_SOURCES = "project"
# 主ループが使える組み込みのツール (--tools)
ORCHESTRATOR_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "Write", "Edit", "Skill")
# 拒むルールのうち、ツール名だけのもの (そのツールを Claude から見えなくする)
DISALLOWED_TOOL_NAMES: tuple[str, ...] = (
    "Bash",
    "PowerShell",
    "WebSearch",
    "WebFetch",
    "Agent",
    "mcp__*",
)
# 子プロセスに渡さない認証の環境変数 (サブスクリプションのログインで動かすため)
AUTH_ENV_NAMES: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# disableBundledSkills を付けても system/init の skills に残る組み込みのスキル (skillOverrides で off にする)
BUNDLED_SKILLS_TO_HIDE: tuple[str, ...] = ("design", "doctor")
# skillOverrides でスキルを Claude から見えなくする値
SKILL_OFF = "off"
# ほかのセッションから届いたメッセージを捨てる crossSessionInbound の値
CROSS_SESSION_INBOUND = "refuse"
# Claude Code の自動の要約が始まる大きさ (トークン)。1M のコンテキストのモデル (Opus 4.7 以降) の既定は約 967K
# (https://code.claude.com/docs/en/model-config の Default auto-compact thresholds、2026-11-16 に確認)
AUTO_COMPACT_START_TOKENS = 967_000
# --autocompact に渡せる最大の値 (トークン)
AUTOCOMPACT_MAX_TOKENS = 1_000_000
# --settings に渡す設定のうち、skillOverrides 以外のもの (この順に書く)
SESSION_SETTINGS_BASE: dict[str, object] = {
    "disableAllHooks": True,
    "autoMemoryEnabled": False,
    "disableClaudeAiConnectors": True,
    "disableBundledSkills": True,
    "syncClaudeAiSkills": False,
    "permissions": {
        "blockReadsOutsideWorkingDirectories": True,
        "blockWritesOutsideWorkingDirectories": True,
    },
    "pluginConfigs": {
        AGENTS_MD_PLUGIN_ID: {
            "options": {"instructionFiles": AGENTS_MD_INSTRUCTION_FILES}
        }
    },
}


class RootEntryAccess(StrEnum):
    """実行ディレクトリーの直下の項目を、主ループの Claude に読ませるか。"""

    READABLE = "readable"  # 読ませる (frames/・notes/・.claude/)
    HIDDEN_FILE = "hidden_file"  # 読ませないファイル。Read(//<パス>) で拒む
    HIDDEN_DIRECTORY = (
        "hidden_directory"  # 読ませないディレクトリー。Read(//<パス>/**) で拒む
    )


# 実行ディレクトリーの直下の項目 (run_directory.RunPaths の property のパスの、実行ディレクトリーからの最初の要素) の
# 扱い。RunPaths に直下の項目を足したら、ここにも足す (足していなければ、そのことを確かめるテストが失敗する)
ROOT_ENTRY_ACCESS: dict[str, RootEntryAccess] = {
    FRAMES_DIR: RootEntryAccess.READABLE,
    NOTES_DIR: RootEntryAccess.READABLE,
    CLAUDE_DIR: RootEntryAccess.READABLE,
    RUN_LOG_FILE: RootEntryAccess.HIDDEN_FILE,
    FRAMES_LOG_FILE: RootEntryAccess.HIDDEN_FILE,
    SURFACE_LOG_FILE: RootEntryAccess.HIDDEN_FILE,
    SURFACE_REQUESTS_LOG_FILE: RootEntryAccess.HIDDEN_FILE,
    SAFETY_LOG_FILE: RootEntryAccess.HIDDEN_FILE,
    OPERATIONS_LOG_FILE: RootEntryAccess.HIDDEN_FILE,
    MIDDLE_DIR: RootEntryAccess.HIDDEN_DIRECTORY,
    DEEP_DIR: RootEntryAccess.HIDDEN_DIRECTORY,
    PROGRESS_DIR: RootEntryAccess.HIDDEN_DIRECTORY,
    ORCHESTRATOR_DIR: RootEntryAccess.HIDDEN_DIRECTORY,
}


@dataclass(frozen=True)
class ClaudeSessionOptions:
    """主ループの Claude Code の起動の設定。

    executable         claude の実行ファイル (既定 deep の CLAUDE_EXECUTABLE)
    model              --model の値 (既定 ORCHESTRATOR_MODEL)
    effort             --effort の値 (--orchestrator-effort。既定 ORCHESTRATOR_EFFORT)
    hidden_env_names   子プロセスに渡さない環境変数の名前 (既定 deep の HIDDEN_ENV_NAMES)。親の Claude Code の
                       セッションに結びつく変数・設定を上書きする変数・AUTH_ENV_NAMES は、これに関わらず
                       build_session_env がいつも除く
    """

    executable: str = CLAUDE_EXECUTABLE
    model: str = ORCHESTRATOR_MODEL
    effort: str = ORCHESTRATOR_EFFORT
    hidden_env_names: tuple[str, ...] = HIDDEN_ENV_NAMES


def build_allowed_rules(paths: RunPaths) -> list[str]:
    """--allowedTools に渡す許すルールの一覧を、次の順で返す (R は rule_path(paths.root))。

        Read(/R/frames/**)          frames/ の読み取り (Read・Glob・Grep)
        Read(/R/notes/**)           notes/ の読み取り
        Read(/R/.claude/skills/**)  実行ディレクトリーのスキルの読み取り
        Edit(/R/notes/**)           notes/ の書き込み (Edit・Write)
        Skill                       スキルの呼び出し
    R は / で始まるため、パスのルールは `Read(//...)` (ファイルシステムの根からの絶対パス) になる。
    paths.root はシンボリックリンクを解決した絶対パスにする (呼び出し側が解決する)。paths.root が deep の
    claude_code_command.rule_path で RulePathError なら、そのまま出す。
    """
    raise NotImplementedError


def build_disallowed_rules(paths: RunPaths) -> list[str]:
    """--disallowedTools に渡す拒むルールの一覧を返す。

    DISALLOWED_TOOL_NAMES の順に並べた後に、ROOT_ENTRY_ACCESS の順に、読ませない項目ごとの Read のルールを並べる
    (R は rule_path(paths.root)):
        HIDDEN_FILE の項目        Read(/R/<名前>)
        HIDDEN_DIRECTORY の項目   Read(/R/<名前>/**)
    READABLE の項目のルールは作らない。paths.root が RulePathError なら、そのまま出す。
    """
    raise NotImplementedError


def build_session_settings(parent_skill_names: Collection[str]) -> dict[str, object]:
    """--settings に渡す設定の辞書を返す。

    SESSION_SETTINGS_BASE のキーをその順に並べた後に、次の 2 つを足す:
        "skillOverrides"        parent_skill_names と BUNDLED_SKILLS_TO_HIDE を合わせて重複を除き、名前の順に並べた
                                それぞれ → SKILL_OFF
        "crossSessionInbound"   CROSS_SESSION_INBOUND
    parent_skill_names は、実行ディレクトリーの親のディレクトリーのスキルの名前
    (orchestrator.parent_skill_names.find_parent_skill_names の結果)。
    """
    raise NotImplementedError


def session_settings_json(parent_skill_names: Collection[str]) -> str:
    """build_session_settings(parent_skill_names) を、空白を入れない JSON の文字列にして返す。

    json.dumps(…, ensure_ascii=False, separators=(",", ":"))。
    """
    raise NotImplementedError


def autocompact_arguments(rotate_tokens: int) -> list[str]:
    """--autocompact の引数を返す。

    rotate_tokens (--rotate-tokens) が AUTO_COMPACT_START_TOKENS より小さければ、自動の要約はセッションの入れ替えより
    後に始まるため、空のリスト (--autocompact を付けない) を返す。
    AUTO_COMPACT_START_TOKENS 以上で AUTOCOMPACT_MAX_TOKENS より小さければ、["--autocompact", str(AUTOCOMPACT_MAX_TOKENS)]
    を返す (この場合の起動は実際には確かめていない)。AUTOCOMPACT_MAX_TOKENS 以上なら、rotate_tokens より大きい値を
    渡せないため ValueError にする。rotate_tokens が 1 未満でも ValueError にする。
    """
    raise NotImplementedError


def build_session_command(
    paths: RunPaths,
    instructions: str,
    parent_skill_names: Collection[str],
    options: ClaudeSessionOptions,
    rotate_tokens: int,
) -> list[str]:
    """主ループの claude -p の引数の列を返す。

    [options.executable, "-p",
     "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
     "--model", options.model, "--effort", options.effort,
     "--permission-mode", ORCHESTRATOR_PERMISSION_MODE,
     "--setting-sources", ORCHESTRATOR_SETTING_SOURCES,
     "--strict-mcp-config",
     "--settings", session_settings_json(parent_skill_names),
     "--tools", ",".join(ORCHESTRATOR_TOOLS),
     "--allowedTools", ",".join(build_allowed_rules(paths)),
     "--disallowedTools", ",".join(build_disallowed_rules(paths)),
     "--append-system-prompt", instructions,
     *autocompact_arguments(rotate_tokens)]
    instructions は変わらない指示 (orchestrator.orchestrator_instructions)。空か、- で始まるなら ValueError にする
    (オプションとして読まれないように)。
    --dangerously-skip-permissions・--allow-dangerously-skip-permissions・--restricted・--safe-mode・--bare・
    --add-dir・--mcp-config・--plugin-dir・--plugin-url・--agents・--agent は付けない。
    """
    raise NotImplementedError


def build_session_env(
    environ: Mapping[str, str], hidden_names: Sequence[str]
) -> dict[str, str]:
    """子プロセスに渡す環境変数の辞書を返す。

    environ から、deep の claude_code_command.build_child_env(environ, hidden_names) が除く変数 (hidden_names・
    PARENT_SESSION_ENV_NAMES・SETTING_OVERRIDE_ENV_NAMES) と、AUTH_ENV_NAMES を除いた新しい辞書を返す
    (environ は変えない)。ほかの変数 (CLAUDE_CODE_OAUTH_TOKEN・CLAUDE_CONFIG_DIR・HOME・PATH など) は残す。
    """
    raise NotImplementedError
