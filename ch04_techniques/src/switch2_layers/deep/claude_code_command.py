"""deep の Claude Code (claude -p) の起動の引数・権限のルール・子プロセスの環境変数を作る (どれも純粋な関数)。

フラグの綴りと権限のルールの書き方は、公式ドキュメントと claude --help に合わせる (2026-11-15 に Claude Code 2.1.325 で確認):
    https://code.claude.com/docs/en/cli-reference          フラグ
    https://code.claude.com/docs/en/permissions            ルールの書き方 (パス・deny の優先・シンボリックリンク)
    https://code.claude.com/docs/en/tools-reference        ツールの名前と、ルールが効くツール (Glob と Grep の既定)
    https://code.claude.com/docs/en/permission-modes       dontAsk
    https://code.claude.com/docs/en/settings-reference     DEEP_SETTINGS のキー
    https://code.claude.com/docs/en/skills                 Skill のツールを拒むルール
    https://code.claude.com/docs/en/headless               stream-json・--bare の認証

コマンドの形 (build_claude_command):
    claude -p <プロンプト>
        --model claude-opus-5-5 --effort high
        --output-format stream-json --verbose
        --permission-mode dontAsk
        --restricted --safe-mode --strict-mcp-config --disable-slash-commands
        --settings <DEEP_SETTINGS の JSON>
        --tools Read,Glob,Grep,Edit,Write
        --allowedTools <build_allowed_tools の結果をカンマでつないだ 1 つの文字列>
        --disallowedTools <DISALLOWED_TOOLS をカンマでつないだ 1 つの文字列>
子プロセスの作業ディレクトリーは、実行ディレクトリー (シンボリックリンクを解決した絶対パス) にする。

使えるツールと、その範囲:
    - --tools で、使える組み込みのツールを Read・Glob・Grep・Edit・Write の 5 つに限る。Bash・WebSearch・WebFetch・
      Agent (サブエージェント)・Skill・NotebookEdit などのほかのツールは使えない。macOS・Linux・WSL では Glob と Grep は
      既定のツールに含まれないが、--tools で名前を挙げたものは使える。--tools は MCP のツールには効かない
    - --allowedTools は許可の確認を省くだけで、使えるツールを限らない
    - Read・Glob・Grep: 実行ディレクトリーの中だけ。Read(//<実行ディレクトリー>/**) で許す (read_rule。先頭の // は
      ファイルシステムの根からの絶対パス)。Read のルールは Glob と Grep にも効く (Glob(path)・Grep(path) のルールは
      照合されないため書かない)。作業ディレクトリー (実行ディレクトリー) の外の読み取りは、--restricted と
      permissions.blockReadsOutsideWorkingDirectories で拒む
    - Edit・Write: そのジョブの対象の文書 (job.json の documents) だけ。文書ごとに
      Edit(//<実行ディレクトリー>/notes/<文書>) で許す (edit_rule)。Edit のルールは Write にも効く (Write(path) のルールは
      照合されないため書かない)。まだ無い文書は Write で作る
    - --permission-mode dontAsk: 許すルールが無く、確認が要る操作をすべて拒む。ファイルの書き込みは確認が要る操作なので、
      対象の文書のほかには書けない
    - 拒むルール (DISALLOWED_TOOLS) は許すルールより優先する。ツール名だけのルールは、そのツールを Claude から見えなくする。
      --tools で外したツールのうち、コマンドの実行・Web・サブエージェント・skill のツール (Bash・WebSearch・WebFetch・
      Agent・Skill) と、MCP のツール (mcp__*) を、拒むルールでも外す
シンボリックリンクの扱い (permissions の Symlinks の節):
    - 権限の判定は、求めたパスと、そのパスが解決した先の両方で行う。許すルールは両方が一致したときだけ効き、拒むルールは
      どちらかが一致すれば効く。実行ディレクトリーの中のリンクが外を指していれば、Read・Edit の許すルールに一致しない
    - Edit・Write は、書くパス自体がシンボリックリンクなら書かない (リンクの先を書くよう Claude に伝える)。途中の
      ディレクトリーがリンクのときの書き込みも、求めたパスと解決した先の両方で判定する
    - 許可した後にファイルを開くときに、パスが許可したときと同じ場所に解決されることを確かめる。解決できないパス
      (リンクの循環など) は、Read・Edit・Write が扱わない
    - Grep と Glob は、path を解決した先のディレクトリーを探す
    このほかに、起動する前に、対象の文書とその親のディレクトリーがシンボリックリンクでないことを、呼び出し側
    (deep.claude_code_runner が notes.note_symlink_check で) 確かめる
作業ディレクトリー・hooks・ユーザーの CLAUDE.md や設定の影響を除く方法:
    - --restricted: ユーザー・プロジェクト・ローカルの設定ファイルを読まず、managed の設定と --settings だけを読む
      (ユーザーの permissions.allow のルールが混ざらない)。コマンドやコードを実行する組み込みのツールと WebFetch を、
      --tools で名前を挙げない限り外す。ファイルのツールを作業ディレクトリーに閉じ込める。bypassPermissions を拒む
    - --safe-mode: CLAUDE.md・skills・plugins・hooks・MCP サーバー・カスタムのコマンドとエージェント・auto memory などを
      読まない。認証・モデルの選択・組み込みのツール・権限はふだんどおり動く
    - --strict-mcp-config: --mcp-config で渡したもの以外の MCP サーバーを読まない (--mcp-config は渡さない)
    - --disable-slash-commands: skills とコマンドをすべて使えなくする
    - --settings (DEEP_SETTINGS): disableAllHooks (hooks と、インストールした mods を止める)、autoMemoryEnabled false、
      disableClaudeAiConnectors (claude.ai のコネクターを取得しない)、blockReadsOutsideWorkingDirectories、
      blockWritesOutsideWorkingDirectories
    - --bare は使わない。--bare では認証が ANTHROPIC_API_KEY (か --settings の apiKeyHelper) だけになり、
      ログインの認証情報を読まないため
    - --mcp-config・--plugin-dir・--plugin-url・--agents・--agent・--add-dir は渡さない
未確認 (公式ドキュメントに記載が無く、実際の起動で確かめる事項):
    - --restricted と --safe-mode を同時に付けたときの動き (--safe-mode のもとで --settings が読まれるか)
    - stream-json の system/init の行で、tools が 5 つのツールだけになり、mcp_servers・plugins が空になるか
    - 作業ディレクトリーの中の、外を指すシンボリックリンクを読むとき (許すルールが無くても通る読み取り) の扱い
    - Write が、まだ無い親のディレクトリー (notes/scenes/<場面ID>/ など) を作るか
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CLAUDE_EXECUTABLE = "claude"
DEEP_MODEL = "claude-opus-5-5"
DEEP_EFFORT = "high"
# 許すルールが無い、確認が要る操作をすべて拒むモード
DEEP_PERMISSION_MODE = "dontAsk"
# deep が使える組み込みのツール (--tools)
DEEP_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "Edit", "Write")
# 拒むルール (--disallowedTools)。どれもツール名だけのルールで、そのツールを Claude から見えなくする
DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash",
    "WebSearch",
    "WebFetch",
    "Agent",
    "Skill",
    "mcp__*",
)
# 子プロセスに渡さない環境変数 (surface と middle の API キー)
HIDDEN_ENV_NAMES: tuple[str, ...] = ("TYPESAFE_API_KEY", "OPENAI_API_KEY")
# --settings に渡す設定
DEEP_SETTINGS: dict[str, object] = {
    "disableAllHooks": True,
    "autoMemoryEnabled": False,
    "disableClaudeAiConnectors": True,
    "permissions": {
        "blockReadsOutsideWorkingDirectories": True,
        "blockWritesOutsideWorkingDirectories": True,
    },
}
# 権限のルールに埋めてよい絶対パスの形: / で始まり、英数字と . _ / @ + - だけからなる。
# 空白・カンマ・括弧・gitignore の特殊文字 (* ? [ ] ! \ #) を含むパスは、ルールの区切りや照合を壊すため使わない
RULE_PATH_PATTERN = re.compile(r"/[A-Za-z0-9._/@+-]*")
# 権限のルールに埋めてよい、文書の notes/ からの相対パスの 1 要素の形 (英数字と . _ @ + - だけ)
NOTE_PART_PATTERN = re.compile(r"[A-Za-z0-9._@+-]+")


class RulePathError(ValueError):
    """権限のルールに埋められないパス (絶対パスでない、文字が合わない、notes/ の外を指す文書など)。"""


@dataclass(frozen=True)
class ClaudeCodeOptions:
    """deep の Claude Code の起動の設定。

    executable         claude の実行ファイル (既定 CLAUDE_EXECUTABLE)。テストでは偽物を起動するときに変える
    model              --model の値 (既定 DEEP_MODEL)
    effort             --effort の値 (既定 DEEP_EFFORT)
    hidden_env_names   子プロセスに渡さない環境変数の名前 (既定 HIDDEN_ENV_NAMES)
    """

    executable: str = CLAUDE_EXECUTABLE
    model: str = DEEP_MODEL
    effort: str = DEEP_EFFORT
    hidden_env_names: tuple[str, ...] = HIDDEN_ENV_NAMES


def rule_path(path: Path) -> str:
    """絶対パス path を、ルールに埋める / 区切りの文字列 (path.as_posix()) にして返す。

    path が絶対パスでないか、文字列が RULE_PATH_PATTERN に fullmatch しなければ RulePathError にする。
    シンボリックリンクの解決はしない (呼び出し側が解決したパスを渡す)。
    """
    raise NotImplementedError


def validate_document_note(note: str) -> str:
    """文書の notes/ からの / 区切りの相対パス note を確かめ、そのまま返す。

    次のときは RulePathError にする (notes/ の外を指す文書や、ルールの照合を壊す文書をルールに埋めないため):
        - note が空、または / で始まる
        - / で区切った要素のどれかが、空 (`a//b`・末尾の `/`)、`.`、`..` のどれか
        - 要素のどれかが NOTE_PART_PATTERN に fullmatch しない (空白・カンマ・括弧・* などを含む)
    """
    raise NotImplementedError


def read_rule(run_dir: Path) -> str:
    """実行ディレクトリーの中の読み取りを許すルール `Read(/<rule_path(run_dir)>/**)` を返す。

    rule_path は / で始まるため、結果は `Read(//...)` (ファイルシステムの根からの絶対パス) になる。Read・Glob・Grep に効く。
    run_dir が rule_path で RulePathError なら、そのまま出す。
    """
    raise NotImplementedError


def edit_rule(run_dir: Path, note: str) -> str:
    """対象の文書 note の書き込みを許すルール `Edit(/<rule_path(run_dir / run_directory.NOTES_DIR / note)>)` を返す。

    結果は `Edit(//<実行ディレクトリー>/notes/<note>)` になる。Edit・Write に効き、その 1 つのファイルだけに一致する。
    note を validate_document_note で確かめる。note か run_dir が RulePathError なら、そのまま出す。
    """
    raise NotImplementedError


def build_allowed_tools(run_dir: Path, notes: Sequence[str]) -> list[str]:
    """--allowedTools に渡す許すルールの一覧を、次の順で返す。

        read_rule(run_dir)                       実行ディレクトリーの中の読み取り (Read・Glob・Grep)
        edit_rule(run_dir, note) を notes の順に   そのジョブの対象の文書の書き込み (Edit・Write)
    run_dir はシンボリックリンクを解決した絶対パス、notes はジョブの対象の文書 (job.json の documents)。
    パスを限らないルール (Read・Edit・Write・Glob・Grep などのツール名だけのもの、`*` を含むツール名) と、
    Bash・Write・Glob・Grep のルールは含めない。どれかのパスが RulePathError なら、そのまま出す。
    """
    raise NotImplementedError


def build_claude_command(
    prompt: str, run_dir: Path, notes: Sequence[str], options: ClaudeCodeOptions
) -> list[str]:
    """claude -p の引数の列を返す。

    [options.executable, "-p", prompt,
     "--model", options.model, "--effort", options.effort,
     "--output-format", "stream-json", "--verbose",
     "--permission-mode", DEEP_PERMISSION_MODE,
     "--restricted", "--safe-mode", "--strict-mcp-config", "--disable-slash-commands",
     "--settings", deep_settings_json(),
     "--tools", ",".join(DEEP_TOOLS),
     "--allowedTools", ",".join(build_allowed_tools(run_dir, notes)),
     "--disallowedTools", ",".join(DISALLOWED_TOOLS)]
    プロンプトは -p の直後に置く (後ろの --tools などは複数の値を取るため、プロンプトを値として取り込ませない)。
    プロンプトが空か、/ か - で始まるなら ValueError にする (スラッシュコマンドやオプションとして読まれないように)。
    --dangerously-skip-permissions・--allow-dangerously-skip-permissions・--bare・--add-dir・--mcp-config・
    --plugin-dir・--plugin-url・--agents・--agent は付けない。
    """
    raise NotImplementedError


def deep_settings_json() -> str:
    """--settings に渡す DEEP_SETTINGS の JSON の文字列を返す。

    json.dumps(DEEP_SETTINGS, ensure_ascii=False, separators=(",", ":")) (空白を入れない形)。
    """
    raise NotImplementedError


def build_child_env(
    environ: Mapping[str, str], hidden_names: Collection[str]
) -> dict[str, str]:
    """子プロセスに渡す環境変数の辞書を返す。

    environ から hidden_names にある名前の変数を除いた新しい辞書を返す (environ は変えない)。
    ほかの変数は claude 自身の認証や HOME・PATH に使われるため、すべて残す。
    """
    raise NotImplementedError
