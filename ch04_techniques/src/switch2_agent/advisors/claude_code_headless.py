"""headless の Claude Code (claude -p) を、裏のスレッドで advisor として実行する。

advisor は Skill (record-controls・record-tips・record-progress・record-strategy) の手順で、
ゲームの情報の 4 ファイルを書き換える。同時に実行するのは 1 件だけで、実行中に来た要求は捨てる。
実行ごとに runs_dir/advisor-<連番>/ を作り、入力 (画像と context.md) と結果 (result.json) を残す。

フラグの綴りと許可ルールの書き方は公式ドキュメント (https://code.claude.com/docs/en/headless、
https://code.claude.com/docs/en/permissions、https://code.claude.com/docs/en/skills、2026-11-12 に確認) に合わせる。

コマンドの形:
    claude -p <プロンプト> --output-format json --permission-mode dontAsk
        --setting-sources project --settings '{"disableAllHooks": true}'
        --allowedTools "Read,Edit(/<ゲームの情報のディレクトリー>/**),Skill(record-controls),Skill(record-tips),Skill(record-progress),Skill(record-strategy)"
        --max-turns 30
- dontAsk で、許可ルールに無い操作 (確認が要る操作) を対話なしで拒否させる。Bash は許可しない
  (Edit のルールでは Bash やサブプロセスによる書き込みは止められないため、Bash を許さないことで防ぐ)
- `/path` の形のルールは、CLI のフラグで渡すと primary working directory (cwd) が基準になる。
  cwd は workdir (ch04_techniques のルート) にする (.claude/settings.json と .claude/skills は cwd から読まれるため)
- `Write(...)` のルールはパスと照合されないため、書き込みの許可は Edit のルールで渡す
- --setting-sources project でユーザー側の設定を読まず、disableAllHooks で hook を動かさない。
  --bare は付けない (付けるとプロジェクトの Skill を読まない)
- ~/.claude.json (フォルダーの信頼) は書き換えない
拒否された操作は出力の JSON の permission_denials に入り、拒否されても実行は止まらない。

未確認 (公式ドキュメントに記載なし): --max-turns に達したときと、拒否された操作があったときの終了コード
(終了コードと出力をそのまま記録する)。--output-format json の出力に permission_denials が必ず含まれるか
(ドキュメントでは stream-json の result メッセージに入ると書かれている。無ければ空のリストとして記録する)。
--setting-sources project を付けたときに ~/.claude/CLAUDE.md が読まれるか。
"""

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from switch2_agent.advisors.base import AdvisorContext

CLAUDE_EXECUTABLE = "claude"
# advisor に使わせる Skill の名前 (.claude/skills/<名前>/SKILL.md)
ADVISOR_SKILLS = (
    "record-controls",
    "record-tips",
    "record-progress",
    "record-strategy",
)
DEFAULT_MAX_TURNS = 30  # claude -p の --max-turns の既定値
DEFAULT_ADVISOR_TIMEOUT_S = 300.0  # 1 回の advisor の実行の上限 (秒)
# --settings に渡すインライン JSON。hook を動かさない
DISABLE_HOOKS_SETTINGS = '{"disableAllHooks": true}'
CONTEXT_FILE_NAME = "context.md"
RESULT_FILE_NAME = "result.json"


def advisor_run_dir_name(index: int) -> str:
    """index 回目 (1 始まり) の実行のディレクトリー名 `advisor-<index を 3 桁の 0 埋め>` を返す (例: advisor-001)。"""
    raise NotImplementedError


def image_file_name(index: int) -> str:
    """index 枚目 (1 始まり) の画像のファイル名 `frame-<index を 2 桁の 0 埋め>.jpg` を返す (例: frame-01.jpg)。"""
    raise NotImplementedError


def write_advisor_inputs(run_dir: Path, context: AdvisorContext) -> Path:
    """run_dir (無ければ親を含めて作る) に advisor への入力を書き、context.md のパスを返す。

    - context.images を順に image_file_name(1)、image_file_name(2)、… のファイル名で JPEG のまま書く
    - CONTEXT_FILE_NAME (context.md) に次を Markdown で書く (UTF-8):
        ゲーム名、きっかけの advisor の問いの確率 (小数第 2 位まで) と要求の時刻 (trigger_time_text)、
        画像のファイル名とラベルの一覧 (画像の順)、今の戦略 (current_strategy。空なら「(まだ書かれていない)」)、
        前回 advisor を呼んでからの応答と送った操作 (operation_log_text。空なら「(記録なし)」)
    """
    raise NotImplementedError


def build_advisor_prompt(context_path: Path, notes_dir: Path, game: str) -> str:
    """advisor (headless の Claude Code) への指示の文を返す。先頭は / で始めない (Skill の直接起動と区別する)。

    次の内容を書く:
        - あなたは Switch のゲーム (game) を遊ぶ速い層 (型付きの判断を返すモデル) の戦略を考える advisor である
        - context_path (context.md) と、そこに並ぶ同じディレクトリーの画像、notes_dir の 4 ファイル
          (game_notes.NOTE_KINDS の <種別>.md) を読む
        - 必要な種別を Skill (record-controls・record-tips・record-progress) で更新し、
          record-strategy で戦略を必ず更新する
        - 4 ファイルは速い層の要求に毎回そのまま入るため、短く保つ
        - 4 ファイル以外を書き換えない
        - safety.SAFETY_RULES (全文を入れる) を守り、戦略にも反映する
        - 最後に、更新した内容を 3 行以内で答える
    """
    raise NotImplementedError


def build_allowed_tools(notes_dir_relative: str) -> list[str]:
    """--allowedTools に渡す許可ルールの一覧を返す。

    [`Read`, `Edit(/<notes_dir_relative>/**)`, `Skill(record-controls)`, `Skill(record-tips)`,
    `Skill(record-progress)`, `Skill(record-strategy)`] の順。notes_dir_relative は workdir からの
    相対パス (例: game_notes/zelda)。書き込みの許可は notes_dir_relative の配下の Edit だけで、Bash は含めない。
    """
    raise NotImplementedError


def build_claude_command(
    prompt: str,
    notes_dir_relative: str,
    max_turns: int = DEFAULT_MAX_TURNS,
    executable: str = CLAUDE_EXECUTABLE,
) -> list[str]:
    """claude -p の引数の列を返す (純粋な関数)。

    [executable, "-p", prompt, "--output-format", "json", "--permission-mode", "dontAsk",
     "--setting-sources", "project", "--settings", DISABLE_HOOKS_SETTINGS,
     "--allowedTools", <build_allowed_tools(notes_dir_relative) をカンマでつないだ 1 つの文字列>,
     "--max-turns", str(max_turns)]
    --bare は付けない。
    """
    raise NotImplementedError


def start_daemon_thread(job: Callable[[], None]) -> None:
    """job を daemon スレッドで起動し、終わるのを待たずに戻る。"""
    raise NotImplementedError


class ClaudeCodeHeadlessAdvisor:
    """advisors.base.Advisor の実装。claude -p を裏のスレッドで 1 件ずつ実行する。

    workdir       claude -p の cwd (ch04_techniques のルート。.claude/skills がある)
    notes_dir     ゲームの情報のディレクトリー (workdir の配下。game_notes.game_notes_dir の結果)
    runs_dir      実行ごとのディレクトリー advisor-<連番>/ を作る場所
    game          ゲーム名
    timeout_s     1 回の実行の上限 (秒)。run_command の timeout に渡す
    run_command   コマンドを実行する関数 (subprocess.run と同じ引数を受け取る)。テストでは偽物を渡す
    log           実行ごとに 1 件の辞書を渡す関数 (run では RunLog.write)。None なら呼ばない
    start_thread  実行の本体 (引数なしの関数) を受け取って裏で起動する関数。既定では daemon スレッドで起動して待たない
                  (テストではその場で呼ぶ関数や、呼ばずに保留する関数を渡す)
    clock         所要時間を測る時計 (既定 time.monotonic)
    """

    workdir: Path
    notes_dir: Path
    runs_dir: Path
    game: str
    timeout_s: float
    run_command: Callable[..., subprocess.CompletedProcess]
    log: Callable[[dict], None] | None
    start_thread: Callable[[Callable[[], None]], None]
    clock: Callable[[], float]
    lock: threading.Lock
    busy: bool
    run_count: int

    def __init__(
        self,
        workdir: Path,
        notes_dir: Path,
        runs_dir: Path,
        game: str,
        timeout_s: float = DEFAULT_ADVISOR_TIMEOUT_S,
        run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        log: Callable[[dict], None] | None = None,
        start_thread: Callable[[Callable[[], None]], None] = start_daemon_thread,
        clock: Callable[[], float] = time.monotonic,
    ):
        """属性を入れ、busy を False、run_count を 0 にする。

        notes_dir が workdir の配下でなければ ValueError にする (許可ルールを workdir からの相対パスで書くため)。
        """
        raise NotImplementedError

    def request_update(self, context: AdvisorContext) -> bool:
        """実行中 (busy) なら何もせず False を返す。そうでなければ busy にし、run_count を 1 増やして、
        その連番の実行の本体を start_thread に渡し、True を返す。busy の確認と変更はロックの中で行う。

        実行の本体 (裏のスレッド) は次を行う:
            1. run_dir = runs_dir / advisor_run_dir_name(連番) を作り、write_advisor_inputs で入力を書く
            2. build_advisor_prompt と build_claude_command (notes_dir の workdir からの相対パス) でコマンドを作り、
               run_command(cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout_s) を実行する
            3. run_dir / RESULT_FILE_NAME (result.json) に次の辞書を JSON (UTF-8) で書く:
                 returncode           終了コード。タイムアウトと例外では None
                 duration_s           2 の所要時間 (clock の差、秒)
                 timed_out            subprocess.TimeoutExpired なら True
                 error                タイムアウトなら `timed out after <timeout_s> seconds`、例外 (Exception) なら
                                      その説明 (switch2_remote.error_message.describe_error)。どちらでもなければ None
                 output               標準出力を JSON として読んだもの。読めなければ None
                 stdout               JSON として読めなかった標準出力 (読めたとき、または無いときは None)
                 stderr               標準エラー出力 (無ければ空文字)。タイムアウトでは途中までの出力 (bytes なら UTF-8 で読む)
                 permission_denials   output の permission_denials (output が object でないか、無ければ [])
            4. log があれば、{"event": "advisor", "run_dir": <run_dir の文字列>, "returncode", "duration_s",
               "timed_out", "error", "permission_denials", "result": <output の result。無ければ None>} を渡す
            1〜4 の途中の例外 (Exception) も捕まえ、書けるなら result.json に、log があれば log に記録する。
            最後に必ず (ロックの中で) busy を False に戻す。
        """
        raise NotImplementedError

    def is_busy(self) -> bool:
        """実行中なら True を返す。"""
        raise NotImplementedError

    def close(self) -> None:
        """終了する。実行中の advisor の終了は待たない (プロセスは timeout_s で止まる)。"""
        raise NotImplementedError
