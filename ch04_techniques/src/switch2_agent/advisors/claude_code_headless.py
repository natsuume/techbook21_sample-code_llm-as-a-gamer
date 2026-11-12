"""headless の Claude Code (claude -p) を、裏のスレッドで advisor として実行する。

advisor は Skill (record-controls・record-tips・record-progress・record-strategy) の手順で、
ゲームの情報の 4 ファイルを書き換える。同時に実行するのは 1 件だけで、実行中に来た要求は捨てる。
実行ごとに runs_dir/advisor-<連番>/ を作り、入力 (画像と context.md) と結果 (result.json) を残す。

子プロセスの寿命: claude は subprocess.Popen で起動し、実行中の子プロセスを持っておく。
daemon スレッドは親のプロセスの終了とともに消えるため、待ちの上限 (timeout_s) を過ぎたとき、待つ処理がほかの例外で
失敗したとき、close() のときに、子プロセスを terminate し、TERMINATE_GRACE_S 秒たっても終わらなければ kill して、
子プロセスを残さない。kill の後も TERMINATE_GRACE_S 秒で出力を読み切れなければ (子孫のプロセスがパイプを持ち続ける
など)、パイプを閉じて読むのをやめ、advisor のスレッドを終わらせる (stop_process)。

フラグの綴りと許可ルールの書き方は公式ドキュメント (https://code.claude.com/docs/en/headless、
https://code.claude.com/docs/en/permissions、https://code.claude.com/docs/en/skills、2026-11-12 に確認) に合わせる。

コマンドの形:
    claude -p <プロンプト> --output-format json --permission-mode dontAsk
        --setting-sources project --settings '{"disableAllHooks": true}'
        --allowedTools "Read(/<ゲームの情報のディレクトリー>/**),Read(/<入力を置くディレクトリー>/**),Edit(/<ゲームの情報のディレクトリー>/controls.md),Edit(/<…>/tips.md),Edit(/<…>/progress.md),Edit(/<…>/strategy.md),Skill(record-controls),Skill(record-tips),Skill(record-progress),Skill(record-strategy)"
        --max-turns 30
- dontAsk で、許可ルールに無い操作 (確認が要る操作) を対話なしで拒否させる。Bash は許可しない
  (Edit のルールでは Bash やサブプロセスによる書き込みは止められないため、Bash を許さないことで防ぐ)
- 書き込みは、ゲームの情報の 4 ファイルのパスだけに許す (ディレクトリー全体には許さない)。画面の文字で指示を
  注入されても、そのディレクトリーにほかのファイル (サブディレクトリーの CLAUDE.md など、claude が指示として
  読みうるファイル) を作れないようにするため。4 ファイルは起動時に game_notes.ensure_game_notes が作る
- 読み取りもディレクトリーを限る。許可ルールで読み取りを許すのは、ゲームの情報のディレクトリーと、入力を置く
  ディレクトリー (runs_dir。実行ごとの advisor-<連番>/ に画像と context.md を置く) だけにする。パスを限らない
  `Read` のルールはすべての Read に一致し、作業ディレクトリーの外 (ホームディレクトリーの認証情報など) の読み取りも
  承認してしまう。なお、作業ディレクトリーの中の読み取りは、dontAsk でも許可ルールなしで承認される
  (https://code.claude.com/docs/en/permission-modes)。
  advisor はゲームの画面の画像を読むため、画面の文字で指示を注入されると、読んだ内容をゲームの情報のファイルに
  写すおそれがあり、そのファイルは毎回の要求で Decision Model の API に送られる
- 子プロセスには Decision Model の API キーを渡さない。環境変数から hidden_env_names の名前の変数を除いて渡す
  (build_child_env)。ほかの変数は claude 自身の認証や HOME・PATH に使われるため、すべて残す
- `/path` の形のルールは、CLI のフラグで渡すと primary working directory (cwd) が基準になる。
  cwd は workdir (ch04_techniques のルート) にする (.claude/settings.json と .claude/skills は cwd から読まれるため)
- `Write(...)` のルールはパスと照合されないため、書き込みの許可は Edit のルールで渡す
- --setting-sources project でユーザー側の設定を読まず、disableAllHooks で hook を動かさない。
  --bare は付けない (付けるとプロジェクトの Skill を読まない)
- ~/.claude.json (フォルダーの信頼) は書き換えない
- 標準入力は渡さない (subprocess.DEVNULL)。claude -p は標準入力も読むため、端末の入力を読ませない
拒否された操作は出力の JSON の permission_denials に入り、拒否されても実行は止まらない。

未確認 (公式ドキュメントに記載なし): --max-turns に達したときと、拒否された操作があったときの終了コード
(終了コードと出力をそのまま記録する)。--output-format json の出力に permission_denials が必ず含まれるか
(ドキュメントでは stream-json の result メッセージに入ると書かれている。無ければ空のリストとして記録する)。
--setting-sources project を付けたときに ~/.claude/CLAUDE.md が読まれるか。
"""

import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Collection, Mapping
from pathlib import Path

from switch2_remote.error_message import describe_error

from switch2_agent.advisors.base import AdvisorContext
from switch2_agent.game_notes import NOTE_KINDS, NOTE_TITLES
from switch2_agent.safety import SAFETY_RULES

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

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
# terminate してから kill するまでに、子プロセスが終わるのを待つ時間 (秒)
TERMINATE_GRACE_S = 5.0
# close() が、advisor のスレッドの終了を待つ上限 (秒)
CLOSE_WAIT_S = 10.0
# --settings に渡すインライン JSON。hook を動かさない
DISABLE_HOOKS_SETTINGS = '{"disableAllHooks": true}'
CONTEXT_FILE_NAME = "context.md"
RESULT_FILE_NAME = "result.json"
EMPTY_STRATEGY_TEXT = "(まだ書かれていない)"
EMPTY_OPERATION_LOG_TEXT = "(記録なし)"


def advisor_run_dir_name(index: int) -> str:
    """index 回目 (1 始まり) の実行のディレクトリー名 `advisor-<index を 3 桁の 0 埋め>` を返す (例: advisor-001)。"""
    return f"advisor-{index:03d}"


def image_file_name(index: int) -> str:
    """index 枚目 (1 始まり) の画像のファイル名 `frame-<index を 2 桁の 0 埋め>.jpg` を返す (例: frame-01.jpg)。"""
    return f"frame-{index:02d}.jpg"


def write_advisor_inputs(run_dir: Path, context: AdvisorContext) -> Path:
    """run_dir (無ければ親を含めて作る) に advisor への入力を書き、context.md のパスを返す。

    - context.images を順に image_file_name(1)、image_file_name(2)、… のファイル名で JPEG のまま書く
    - CONTEXT_FILE_NAME (context.md) に次を Markdown で書く (UTF-8):
        ゲーム名、きっかけの advisor の問いの確率 (小数第 2 位まで) と要求の時刻 (trigger_time_text)、
        画像のファイル名とラベルの一覧 (画像の順)、今の戦略 (current_strategy。空なら「(まだ書かれていない)」)、
        前回 advisor を呼んでからの応答と送った操作 (operation_log_text。空なら「(記録なし)」)
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    image_lines = []
    for index, image in enumerate(context.images, start=1):
        name = image_file_name(index)
        (run_dir / name).write_bytes(image.jpeg)
        image_lines.append(f"- {name}: {image.label}")
    strategy = context.current_strategy.strip() or EMPTY_STRATEGY_TEXT
    operation_log = context.operation_log_text.strip() or EMPTY_OPERATION_LOG_TEXT
    text = "\n".join(
        [
            "# advisor への文脈",
            "",
            f"- ゲーム: {context.game}",
            (
                f"- きっかけ: {context.trigger_time_text} の要求で、advisor の問いの確率が "
                f"{context.trigger_probability:.2f} だった"
            ),
            "",
            "## 画像 (古い順)",
            "",
            *(image_lines or ["(画像なし)"]),
            "",
            "## 今の戦略",
            "",
            strategy,
            "",
            "## 前回 advisor を呼んでからの応答と送った操作",
            "",
            operation_log,
            "",
        ]
    )
    context_path = run_dir / CONTEXT_FILE_NAME
    context_path.write_text(text, encoding="utf-8")
    return context_path


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
    note_lines = "\n".join(
        f"   - {notes_dir / f'{kind}.md'} ({NOTE_TITLES[kind]})" for kind in NOTE_KINDS
    )
    return f"""\
あなたは、Nintendo Switch 2 のゲーム「{game}」を遊ぶ速い層 (画面の画像を見て、問いごとに型付きの判断を返すモデル) のために、戦略を考える advisor である。ゲーム名は {game}。

手順:
1. {context_path} を読む。そこに並ぶ画像 (同じディレクトリーの frame-01.jpg など) も読む。
2. ゲームの情報の 4 ファイルを読む。
{note_lines}
3. 新しく分かったことがあれば、種別ごとの Skill で更新する。操作方法は record-controls、攻略情報は record-tips、進行状況は record-progress。
4. 戦略は record-strategy で必ず更新する。
5. 最後に、更新した内容を 3 行以内で答える。

守ること:
- 4 ファイルは速い層の要求に毎回そのまま入るため、短く保つ。
- 4 ファイル以外のファイルを書き換えない。
- 次の安全のための規則を守り、戦略にも反映する。

{SAFETY_RULES}"""


def build_allowed_tools(notes_dir_relative: str, runs_dir_relative: str) -> list[str]:
    """--allowedTools に渡す許可ルールの一覧を返す。

    [`Read(/<notes_dir_relative>/**)`, `Read(/<runs_dir_relative>/**)`,
    game_notes.NOTE_KINDS の順に `Edit(/<notes_dir_relative>/<種別>.md)` の 4 つ,
    `Skill(record-controls)`, `Skill(record-tips)`, `Skill(record-progress)`, `Skill(record-strategy)`] の順。
    notes_dir_relative (ゲームの情報のディレクトリー) と runs_dir_relative (入力を置くディレクトリー) は
    workdir からの相対パス (例: game_notes/zelda、runs/20261112-120000)。
    読み取りの許可はこの 2 つのディレクトリーの配下だけで、パスを限らない `Read` は含めない。
    書き込みの許可はゲームの情報の 4 ファイルの Edit だけで、ディレクトリー全体の Edit と Bash は含めない。
    """
    return [
        f"Read(/{notes_dir_relative}/**)",
        f"Read(/{runs_dir_relative}/**)",
        *(f"Edit(/{notes_dir_relative}/{kind}.md)" for kind in NOTE_KINDS),
        *(f"Skill({name})" for name in ADVISOR_SKILLS),
    ]


def build_claude_command(
    prompt: str,
    notes_dir_relative: str,
    runs_dir_relative: str,
    max_turns: int = DEFAULT_MAX_TURNS,
    executable: str = CLAUDE_EXECUTABLE,
) -> list[str]:
    """claude -p の引数の列を返す (純粋な関数)。

    [executable, "-p", prompt, "--output-format", "json", "--permission-mode", "dontAsk",
     "--setting-sources", "project", "--settings", DISABLE_HOOKS_SETTINGS,
     "--allowedTools", <build_allowed_tools(notes_dir_relative, runs_dir_relative) をカンマでつないだ 1 つの文字列>,
     "--max-turns", str(max_turns)]
    --bare は付けない。
    """
    return [
        executable,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "project",
        "--settings",
        DISABLE_HOOKS_SETTINGS,
        "--allowedTools",
        ",".join(build_allowed_tools(notes_dir_relative, runs_dir_relative)),
        "--max-turns",
        str(max_turns),
    ]


def build_result_record(
    returncode: int | None,
    duration_s: float,
    timed_out: bool,
    error: str | None,
    stdout: str | None,
    stderr: str | None,
) -> dict:
    """result.json に書く辞書を返す (純粋な関数)。キーは ClaudeCodeHeadlessAdvisor.request_update の docstring のとおり。

    stdout を JSON として読めたら output に入れて stdout は None に、読めなければ output を None にして
    stdout をそのまま入れる (空なら None)。permission_denials は output が object で、その permission_denials が
    リストならそれ、そうでなければ []。stderr が None なら空文字にする。
    """
    output = None
    raw_stdout = stdout or None
    if raw_stdout is not None:
        try:
            output = json.loads(raw_stdout)
            raw_stdout = None
        except ValueError:
            output = None
    denials = output.get("permission_denials") if isinstance(output, dict) else None
    return {
        "returncode": returncode,
        "duration_s": duration_s,
        "timed_out": timed_out,
        "error": error,
        "output": output,
        "stdout": raw_stdout,
        "stderr": stderr or "",
        "permission_denials": denials if isinstance(denials, list) else [],
    }


def build_log_event(run_dir: Path, record: dict) -> dict:
    """log に渡す 1 件の辞書を、result.json の辞書 record から作る (純粋な関数)。"""
    output = record["output"]
    return {
        "event": "advisor",
        "run_dir": str(run_dir),
        "returncode": record["returncode"],
        "duration_s": record["duration_s"],
        "timed_out": record["timed_out"],
        "error": record["error"],
        "permission_denials": record["permission_denials"],
        "result": output.get("result") if isinstance(output, dict) else None,
    }


def build_child_env(
    environ: Mapping[str, str], hidden_names: Collection[str]
) -> dict[str, str]:
    """子プロセスに渡す環境変数の辞書を返す (純粋な関数)。

    environ から hidden_names にある名前の変数を除いた新しい辞書を返す (environ は変えない)。
    ほかの変数は claude 自身の認証や HOME・PATH に使われるため、すべて残す。
    """
    return {name: value for name, value in environ.items() if name not in hidden_names}


def start_daemon_thread(job: Callable[[], None]) -> None:
    """job を daemon スレッドで起動し、終わるのを待たずに戻る。"""
    threading.Thread(target=job, daemon=True).start()


def stop_process(process: subprocess.Popen) -> tuple[str | None, str | None]:
    """実行中の process を止め、出力を読み切って (標準出力, 標準エラー出力) を返す。

    terminate して TERMINATE_GRACE_S 秒まで出力を読み、終わらなければ kill して、さらに TERMINATE_GRACE_S 秒まで読む。
    kill の後も読み切れなければ (claude が起動した子孫のプロセスがパイプを持ち続けて EOF が来ないなど)、
    出力のパイプを閉じて (None, None) を返す (advisor のスレッドを有限の時間で終わらせるため)。
    process の communicate を呼んでいるスレッドから呼ぶ。
    """
    process.terminate()
    try:
        return process.communicate(timeout=TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        process.kill()
    try:
        return process.communicate(timeout=TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        logger.warning(
            "claude output pipes stayed open after kill; stopped reading them"
        )
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        return None, None


class ClaudeCodeHeadlessAdvisor:
    """advisors.base.Advisor の実装。claude -p を裏のスレッドで 1 件ずつ実行する。

    workdir        claude -p の cwd (ch04_techniques のルート。.claude/skills がある)
    notes_dir      ゲームの情報のディレクトリー (workdir の配下。game_notes.game_notes_dir の結果)
    runs_dir       実行ごとのディレクトリー advisor-<連番>/ を作る場所 (workdir の配下)
    game           ゲーム名
    timeout_s      1 回の実行の上限 (秒)。communicate の timeout に渡す
    hidden_env_names  子プロセスに渡さない環境変数の名前 (run では Decision Model の API キーの環境変数)
    start_process  子プロセスを起動する関数 (subprocess.Popen と同じ引数を受け取り、Popen を返す)。テストでは偽物を渡す
    log            実行ごとに 1 件の辞書を渡す関数 (run では RunLog.write)。None なら呼ばない
    start_thread   実行の本体 (引数なしの関数) を受け取って裏で起動する関数。既定では daemon スレッドで起動して待たない
                   (テストではその場で呼ぶ関数や、呼ばずに保留する関数を渡す)
    clock          所要時間を測る時計 (既定 time.monotonic)

    状態 (lock で守る):
        busy       実行中なら True
        run_count  受け付けた実行の数 (実行のディレクトリーの連番)
        process    実行中の子プロセス。無ければ None
        closed     close() を呼んだら True。以後は要求を受け付けず、子プロセスを起動しない
    idle は実行中でなければ set されたイベントで、close() が advisor のスレッドの終了を待つのに使う。
    """

    def __init__(
        self,
        workdir: Path,
        notes_dir: Path,
        runs_dir: Path,
        game: str,
        timeout_s: float = DEFAULT_ADVISOR_TIMEOUT_S,
        hidden_env_names: Collection[str] = (),
        start_process: Callable[..., subprocess.Popen] = subprocess.Popen,
        log: Callable[[dict], None] | None = None,
        start_thread: Callable[[Callable[[], None]], None] = start_daemon_thread,
        clock: Callable[[], float] = time.monotonic,
    ):
        """属性を入れ、busy を False、run_count を 0 にする。

        notes_dir か runs_dir が workdir の配下でなければ ValueError にする
        (許可ルールを workdir からの相対パスで書くため)。
        """
        if not notes_dir.is_relative_to(workdir):
            raise ValueError(f"notes_dir must be under workdir: {notes_dir}")
        if not runs_dir.is_relative_to(workdir):
            raise ValueError(f"runs_dir must be under workdir: {runs_dir}")
        self.workdir = workdir
        self.notes_dir = notes_dir
        self.notes_dir_relative = notes_dir.relative_to(workdir).as_posix()
        self.runs_dir = runs_dir
        self.runs_dir_relative = runs_dir.relative_to(workdir).as_posix()
        self.game = game
        self.timeout_s = timeout_s
        self.hidden_env_names = hidden_env_names
        self.start_process = start_process
        self.log = log
        self.start_thread = start_thread
        self.clock = clock
        self.lock = threading.Lock()
        self.busy = False
        self.run_count = 0
        self.process: subprocess.Popen | None = None
        self.closed = False
        self.idle = threading.Event()
        self.idle.set()

    def request_update(self, context: AdvisorContext) -> bool:
        """実行中 (busy) か close() の後なら何もせず False を返す。そうでなければ busy にし、run_count を 1 増やして、
        その連番の実行の本体を start_thread に渡し、True を返す。busy の確認と変更はロックの中で行う。

        実行の本体 (裏のスレッド) は次を行う:
            1. run_dir = runs_dir / advisor_run_dir_name(連番) を作り、write_advisor_inputs で入力を書く
            2. build_advisor_prompt と build_claude_command (notes_dir と runs_dir の workdir からの相対パス) で
               コマンドを作り、start_process(cmd, cwd=workdir, env=build_child_env(os.environ, hidden_env_names),
               stdin=DEVNULL, stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8", errors="replace") で起動して、
               communicate(timeout=timeout_s) で終わるのを待つ。
               タイムアウトしたら stop_process で terminate → TERMINATE_GRACE_S 秒 → kill する。
               communicate がほかの例外 (Exception) を出したら、同じく stop_process で止めてから、その例外を 3・4 で記録する。
               close() の後なら子プロセスを起動しない
            3. run_dir / RESULT_FILE_NAME (result.json) に次の辞書 (build_result_record) を JSON (UTF-8) で書く:
                 returncode           終了コード。タイムアウトと例外では None
                 duration_s           2 の所要時間 (clock の差、秒)
                 timed_out            communicate がタイムアウトしたら True
                 error                タイムアウトなら `timed out after <timeout_s> seconds`、例外 (Exception) なら
                                      その説明 (switch2_remote.error_message.describe_error)、close() で止めたなら
                                      `stopped by close`。どれでもなければ None
                 output               標準出力を JSON として読んだもの。読めなければ None
                 stdout               JSON として読めなかった標準出力 (読めたとき、または無いときは None)
                 stderr               標準エラー出力 (無ければ空文字)
                 permission_denials   output の permission_denials (output が object でないか、無ければ [])
            4. log があれば、build_log_event の辞書 ({"event": "advisor", "run_dir", "returncode", "duration_s",
               "timed_out", "error", "permission_denials", "result": <output の result。無ければ None>}) を渡す
            1〜2 の途中の例外 (Exception) も捕まえて 3・4 で記録し、logging で標準エラー出力にトレースバックを書く。
            result.json を書けなければ (OSError)、その説明を error に入れて 4 に進む。
            最後に必ず (ロックの中で) busy を False に戻し、idle を set する (log の例外はスレッドの外に出る)。
        """
        with self.lock:
            if self.busy or self.closed:
                return False
            self.busy = True
            self.run_count += 1
            index = self.run_count
            self.idle.clear()
        self.start_thread(lambda: self._run(index, context))
        return True

    def is_busy(self) -> bool:
        """実行中なら True を返す。"""
        with self.lock:
            return self.busy

    def close(self) -> None:
        """終了する。以後の要求は受け付けない。

        実行中の子プロセスがあれば terminate し、TERMINATE_GRACE_S 秒たっても終わらなければ kill する。
        そのあと、advisor のスレッドの終了を最大 CLOSE_WAIT_S 秒待つ。
        """
        with self.lock:
            self.closed = True
            process = self.process
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=TERMINATE_GRACE_S)
            except subprocess.TimeoutExpired:
                process.kill()
        self.idle.wait(CLOSE_WAIT_S)

    def _run(self, index: int, context: AdvisorContext) -> None:
        """実行の本体 (request_update の docstring の 1〜4)。裏のスレッドで呼ぶ。"""
        try:
            self._run_and_record(index, context)
        finally:
            # 記録や log の失敗で busy が戻らなくならないよう、必ず最後に戻す
            with self.lock:
                self.busy = False
                self.idle.set()

    def _run_and_record(self, index: int, context: AdvisorContext) -> None:
        """入力を書いて claude を実行し、result.json と log に記録する。

        1〜2 の途中の例外 (Exception) は result.json と log の error に記録し、原因を追えるよう
        logging で標準エラー出力にもトレースバックを書く。
        """
        run_dir = self.runs_dir / advisor_run_dir_name(index)
        started = self.clock()
        outcome: dict = {"returncode": None, "timed_out": False, "error": None}
        stdout: str | None = None
        stderr: str | None = None
        try:
            context_path = write_advisor_inputs(run_dir, context)
            prompt = build_advisor_prompt(context_path, self.notes_dir, self.game)
            cmd = build_claude_command(
                prompt, self.notes_dir_relative, self.runs_dir_relative
            )
            stdout, stderr = self._run_process(cmd, outcome)
        # claude を起動できない (FileNotFoundError) など、失敗の型を問わず記録して次の要求を受け付ける
        except Exception as e:
            outcome["error"] = describe_error(e)
            logger.exception("advisor run %s failed", advisor_run_dir_name(index))
        record = build_result_record(
            outcome["returncode"],
            self.clock() - started,
            outcome["timed_out"],
            outcome["error"],
            stdout,
            stderr,
        )
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / RESULT_FILE_NAME).write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            record["error"] = record["error"] or describe_error(e)
        if self.log is not None:
            self.log(build_log_event(run_dir, record))

    def _run_process(
        self, cmd: list[str], outcome: dict
    ) -> tuple[str | None, str | None]:
        """cmd を子プロセスで実行し、(標準出力, 標準エラー出力) を返す。outcome に returncode・timed_out・error を入れる。

        communicate がタイムアウト以外の例外 (Exception) を出したら、stop_process で子プロセスを止めてから、
        その例外をそのまま出す。
        """
        with self.lock:
            if self.closed:
                outcome["error"] = "stopped by close"
                return None, None
            # close() が子プロセスを見つけられるよう、起動と保持を同じロックの中で行う
            process = self.start_process(
                cmd,
                cwd=self.workdir,
                env=build_child_env(os.environ, self.hidden_env_names),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.process = process
        try:
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                outcome["timed_out"] = True
                outcome["error"] = f"timed out after {self.timeout_s} seconds"
                return stop_process(process)
            except Exception:
                # 待つ処理の失敗で子プロセスを残さないよう、止めてから出し直す
                stop_process(process)
                raise
            outcome["returncode"] = process.returncode
            with self.lock:
                if self.closed:
                    outcome["error"] = "stopped by close"
            return stdout, stderr
        finally:
            with self.lock:
                self.process = None
