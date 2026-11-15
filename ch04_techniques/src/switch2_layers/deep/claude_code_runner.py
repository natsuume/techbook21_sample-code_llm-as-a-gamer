"""deep の 1 回のジョブを、Claude Code (claude -p) を子プロセスとして起動して実行する DeepRunner (ClaudeCodeDeepRunner)。

1 回のジョブの流れ (run):
    1. map のジョブは Claude Code を起動せず、何も書かずに DeepResult(ok=True, message=MAP_SKIPPED_MESSAGE) を返す。
       DeepScheduler は、この message を run.jsonl の deep_end に書く
    2. job.json を読み (deep_prompt)、対象の文書とその親のディレクトリー (notes/ を含む) がシンボリックリンクでない
       ことを確かめる (notes.note_symlink_check)。リンクがあれば Claude Code を起動せず、DeepResult(ok=False,
       message=リンクだったパスを含む説明) を返す。DeepScheduler は、この message を run.jsonl の deep_end に書く
    3. プロンプト (deep_prompt) と、引数と環境変数 (claude_code_command) を作り、claude -p を子プロセスとして起動する。
       作業ディレクトリーは実行ディレクトリー。子プロセスは新しいセッション (プロセスグループ) の先頭にする
       (start_new_session=True。プロセスグループの ID は子プロセスの pid と同じ)
    4. 標準出力の stream-json を 1 行ずつ読み、ジョブのディレクトリーの stream.jsonl に書く (claude_stream_log)。
       標準エラー出力は別のスレッドで最後まで読む
    5. 子プロセスが終わったら、ジョブのディレクトリーの result.json に結果を書き、DeepResult を返す
deep は notes/ の対象の文書の本体を直接編集する。このクラスは文書を読み書きしない。
止め方:
    stop はプロセスグループに SIGTERM を、kill は SIGKILL を送り、どちらも待たずにすぐ戻る。止められた run は
    DeepResult(ok=False, stopped=True) を返す。claude は SIGTERM で終了コード 143 で終わり、result の行を出さない
    (https://code.claude.com/docs/en/headless、2026-11-15 に確認)。
    タイムアウトは DeepScheduler が判定し、stop・kill を呼ぶ (このクラスは自分では時間を測って止めない)。
    run が例外 (deep-once の最中の Ctrl+C の KeyboardInterrupt を含む) で抜けるときは、子プロセスが動いていれば
    プロセスグループに SIGTERM を送り、ERROR_STOP_GRACE_S 秒待っても終わらなければ SIGKILL を送って回収してから、
    例外を外に出す (Claude Code を動かしたまま残さない)。
外部に触れないテストのため、子プロセスの起動・シグナルの送り先・環境変数・時計を差し替えられる。
"""

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from switch2_layers.deep.claude_code_command import (
    ClaudeCodeOptions,
    build_child_env,
    build_claude_command,
)
from switch2_layers.deep.claude_stream_log import (
    RESULT_FILE,
    STREAM_FILE,
    ClaudeStreamLog,
    build_result_record,
)
from switch2_layers.deep.deep_prompt import build_deep_prompt, read_prompt_context
from switch2_layers.deep.deep_types import DeepJob, DeepResult, DocumentKind
from switch2_layers.error_text import describe_exception
from switch2_layers.file_io import write_json_atomic
from switch2_layers.notes.note_symlink_check import find_symlink_in_note_paths
from switch2_layers.run_directory import NOTES_DIR

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# map のジョブの DeepResult.message (run.jsonl の deep_end の message になる)
MAP_SKIPPED_MESSAGE = "map jobs do not start Claude Code; nothing was done"
# stop で止めた run の DeepResult.message
STOPPED_MESSAGE = "stopped"
# kill で止めた run の DeepResult.message
KILLED_MESSAGE = "killed"
# DeepResult.message に入れる、最後のメッセージの最大の文字数
RESULT_MESSAGE_MAX_CHARS = 300
# 子プロセスが終わった後に、標準エラー出力を読むスレッドの終わりを待つ上限 (秒)
STDERR_JOIN_TIMEOUT_S = 5.0
# run が例外で抜けるときに、子プロセスのプロセスグループに SIGTERM を送ってから終わるのを待つ上限 (秒)。
# 待っても終わらなければ SIGKILL を送り、同じ秒数まで回収を待つ
ERROR_STOP_GRACE_S = 5.0


def symlink_refusal_message(link: Path) -> str:
    """対象の文書のパスにシンボリックリンクがあって起動しなかったときの DeepResult.message を返す。

    `Claude Code was not started: <link.as_posix()> is a symbolic link` (link は解決しないパス)。
    """
    return f"Claude Code was not started: {link.as_posix()} is a symbolic link"


class ClaudeProcess(Protocol):
    """起動した claude の子プロセス (subprocess.Popen[str] が満たす部分)。

    pid          プロセス ID。start_new_session=True で起動するため、プロセスグループの ID でもある
    stdout       標準出力 (テキスト)。行ごとに読む (for line in stdout)
    stderr       標準エラー出力 (テキスト)。read() で最後まで読む
    returncode   終了コード。終わっていなければ None
    """

    pid: int
    stdout: IO[str] | None
    stderr: IO[str] | None
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int:
        """終わるのを待ち、終了コードを返す。"""
        ...


class ClaudeProcessStarter(Protocol):
    """子プロセスを起動する関数 (subprocess.Popen と同じ引数を受け取る)。"""

    def __call__(self, args: list[str], **kwargs: object) -> ClaudeProcess:
        """args を起動して ClaudeProcess を返す。起動できなければ OSError を出す。"""
        ...


@dataclass(frozen=True)
class _Launch:
    """claude の起動に使う値 (run の 3 で作り、4 で使う)。

    command   claude -p の引数の列 (claude_code_command.build_claude_command)
    cwd       作業ディレクトリー (実行ディレクトリーのシンボリックリンクを解決した絶対パス)
    env       子プロセスの環境変数 (claude_code_command.build_child_env)
    """

    command: list[str]
    cwd: Path
    env: dict[str, str]


@dataclass(frozen=True)
class _ProcessOutput:
    """子プロセスの出力を最後まで読んだ結果 (run の 5 と 6)。

    returncode            終了コード
    duration_s            起動から、終了と標準エラー出力の読み終わりを待ち終えるまでの秒
    result_message        stream-json の最後の result の行 (無ければ None)
    last_assistant_text   最後の、文を持つ assistant の行の文 (無ければ None)
    write_error           stream.jsonl の書き込みの失敗の説明 (無ければ None)
    stderr                標準エラー出力
    """

    returncode: int
    duration_s: float
    result_message: dict[str, object] | None
    last_assistant_text: str | None
    write_error: str | None
    stderr: str


class _StderrReader:
    """子プロセスの標準エラー出力を、別のスレッド (daemon) で最後まで読む。"""

    def __init__(self, stderr: IO[str] | None) -> None:
        """読むスレッドを起動する。stderr が None なら何も読まない。"""
        self._stderr = stderr
        self._text = ""
        self._thread = threading.Thread(
            target=self._read_all, name="claude-stderr", daemon=True
        )
        self._thread.start()

    def _read_all(self) -> None:
        """標準エラー出力を最後まで読んで覚える。読めなければ警告を書き、空のままにする。"""
        if self._stderr is None:
            return
        try:
            self._text = self._stderr.read()
        except (OSError, ValueError):
            logger.warning("failed to read the stderr of claude", exc_info=True)

    def join(self, timeout: float) -> str:
        """読み終わるのを最大 timeout 秒待ち、読んだ文字列を返す (読み終わっていなければ空文字列)。"""
        self._thread.join(timeout)
        return self._text


class ClaudeCodeDeepRunner:
    """deep_types.DeepRunner の実装。ジョブごとに claude -p を 1 つ起動する。

    同じインスタンスで、別々のジョブ (job_dir) の run を並行して呼んでよい。run はジョブのスレッドから、
    stop と kill はスケジューラーのスレッドから呼ばれる。ジョブは job_dir の絶対パス (Path.absolute) で区別する。

    options                ClaudeCodeOptions (None なら既定値)
    start_process          子プロセスを起動する関数 (None なら subprocess.Popen。呼ぶときに subprocess.Popen を引く)
    signal_process_group   プロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    environ                子プロセスの環境変数の元 (None なら run のときの os.environ)
    clock                  所要時間と stream.jsonl の elapsed_s を測る時計 (None なら time.monotonic)
    """

    def __init__(
        self,
        options: ClaudeCodeOptions | None = None,
        start_process: ClaudeProcessStarter | None = None,
        signal_process_group: Callable[[int, int], None] | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """属性を持つ。実行中のジョブの記録 (job_dir → 子プロセス) と、止める要求の記録を空にする。"""
        self.options = options if options is not None else ClaudeCodeOptions()
        self._start_process = start_process
        self._signal_process_group = (
            signal_process_group if signal_process_group is not None else os.killpg
        )
        self._environ = environ
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.Lock()
        # 実行中のジョブ (job_dir の絶対パス → 子プロセス)
        self._running: dict[Path, ClaudeProcess] = {}
        # stop か kill が呼ばれたジョブと、そのうち kill が呼ばれたジョブ
        self._stop_requested: set[Path] = set()
        self._kill_requested: set[Path] = set()
        # run が戻ったジョブ (以後の stop・kill は何もしない)
        self._finished: set[Path] = set()

    def run(self, job: DeepJob, job_dir: Path) -> DeepResult:
        """job_dir (deep/<種類>-<連番>/。job.json を用意済み) で job を実行し、結果を返す。

        1. job.kind が map なら、子プロセスを起動せず、ファイルも書かずに DeepResult(ok=True, message=MAP_SKIPPED_MESSAGE)
        2. このジョブに stop か kill が既に呼ばれていれば (run より前に止める要求が来た)、起動せずに
           DeepResult(ok=False, stopped=True, message=kill が呼ばれていれば KILLED_MESSAGE、そうでなければ STOPPED_MESSAGE)
           を返す (result.json は書かない)
        3. 起動の用意。次の順に行う。どれかが例外 (Exception) を出したら起動せず、説明 =
           error_text.describe_exception(例外) を result.json の error に書いて DeepResult(ok=False, message=説明) を返す:
             context = deep_prompt.read_prompt_context(job_dir)
                       (context.run_dir は、実行ディレクトリーのシンボリックリンクを解決した絶対パス)
             link = notes.note_symlink_check.find_symlink_in_note_paths(context.run_dir / run_directory.NOTES_DIR,
                    context.notes)
             link が None でなければ (対象の文書か、その親のディレクトリー (notes/ を含む) がシンボリックリンク)、
             起動せず、説明 = symlink_refusal_message(link) を result.json の error に書いて
             DeepResult(ok=False, message=説明) を返す (stopped は False)
             command = claude_code_command.build_claude_command(deep_prompt.build_deep_prompt(context), context.run_dir,
                       context.notes, options)
             env = claude_code_command.build_child_env(environ, options.hidden_env_names)
           この 3 と 4 の起動の失敗で書く result.json は、claude_stream_log.build_result_record(returncode=None,
           duration_s=0.0, result_message=None, last_assistant_text=None, stopped=False, killed=False, error=説明,
           stderr="") の辞書
        4. ロックの中で止める要求を確かめ、来ていなければ start_process(command, cwd=context.run_dir, env=env,
           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
           errors="replace", start_new_session=True) で起動して、実行中のジョブとして記録する。
           ロックの中で確かめるのは、起動の直前に来た stop を取りこぼさないため (来ていれば 2 と同じに戻る)。
           start_process が OSError を出したら (claude が無いなど)、3 と同じく、説明 (describe_exception) を
           result.json の error に書いて DeepResult(ok=False, message=説明) を返す (returncode は None)
        5. 標準エラー出力を別のスレッド (daemon) で最後まで読む。標準出力を 1 行ずつ読み、届くたびに
           claude_stream_log.ClaudeStreamLog (job_dir/stream.jsonl) の add_line に、起動からの秒 (clock の差) とともに渡す
        6. 標準出力が終わったら wait で終了を待ち、標準エラー出力のスレッドの終わりを最大 STDERR_JOIN_TIMEOUT_S 秒待つ
        7. ロックの中で実行中の記録から外し (以後の stop・kill はシグナルを送らない)、stop・kill が呼ばれたかを読む
        8. job_dir/result.json に claude_stream_log.build_result_record の辞書を置き換えの書き込み
           (file_io.write_json_atomic) で書く。duration_s は起動から 6 の終わりまでの秒、stopped は stop か kill が
           呼ばれたら True、killed は kill が呼ばれたら True、error は stream.jsonl の書き込みの失敗があればその説明
        9. 戻り値:
             stop か kill が呼ばれた         DeepResult(ok=False, stopped=True, message=KILLED_MESSAGE か STOPPED_MESSAGE)
             終了コード 0 で、result の行があり、is_error が True でない
                                             DeepResult(ok=True, message=最後のメッセージの先頭 RESULT_MESSAGE_MAX_CHARS 文字
                                             (無ければ空文字列))
             それ以外                         DeepResult(ok=False, message=`claude exited with code <終了コード>` と、
                                             最後のメッセージがあれば `: <その先頭 RESULT_MESSAGE_MAX_CHARS 文字>`)
        result.json を書けない (OSError) ときは、logging で警告を書き、戻り値は 9 のとおりにする。
        起動した後の 5 と 6 で例外 (BaseException。stream.jsonl を作れない OSError や、deep-once の最中の Ctrl+C の
        KeyboardInterrupt を含む) が出たら、子プロセスがまだ回収されていなければ (returncode が None)、プロセスグループに
        signal.SIGTERM を送って最大 ERROR_STOP_GRACE_S 秒待ち、終わらなければ signal.SIGKILL を送って最大
        ERROR_STOP_GRACE_S 秒待って回収してから、実行中の記録から外し、例外をそのまま出す (result.json は書かない)。
        待つ間に別の例外が出たら (もう一度 Ctrl+C を押したなど)、SIGKILL を送ってから、その例外を出す。
        """
        if job.kind is DocumentKind.MAP:
            return DeepResult(ok=True, message=MAP_SKIPPED_MESSAGE)
        key = job_dir.absolute()
        try:
            return self._run_claude(key, job_dir)
        finally:
            with self._lock:
                self._running.pop(key, None)
                self._finished.add(key)

    def stop(self, job_dir: Path) -> None:
        """job_dir のジョブに止める要求を出し、止まるのを待たずにすぐ戻る。例外は出さない。

        - 子プロセスが実行中なら、signal_process_group(子プロセスの pid, signal.SIGTERM) を呼ぶ
        - まだ起動していなければ、止める要求だけを記録する (run は起動せずに stopped で戻る)
        - run が既に戻っていれば (実行中の記録から外れていれば)、シグナルを送らない
        signal_process_group の ProcessLookupError (既に終わったプロセスグループ) と、ほかの OSError は外に出さない
        (ProcessLookupError 以外は logging で警告を書く)。
        """
        self._request_stop(job_dir, kill=False)

    def kill(self, job_dir: Path) -> None:
        """job_dir のジョブを強制終了し、終わるのを待たずにすぐ戻る。例外は出さない。

        stop と同じ扱いで、送るシグナルを signal.SIGKILL にし、kill の要求として記録する。
        """
        self._request_stop(job_dir, kill=True)

    def _run_claude(self, key: Path, job_dir: Path) -> DeepResult:
        """run の 2〜9 (map 以外のジョブ)。key は job_dir の絶対パス。"""
        with self._lock:
            stopped = self._stopped_result_locked(key)
        if stopped is not None:
            return stopped
        # 3. 起動の用意
        try:
            launch = self._prepare_launch(job_dir)
        except Exception as error:
            logger.warning("could not prepare the deep job %s", job_dir, exc_info=True)
            return self._fail_before_start(job_dir, describe_exception(error))
        if isinstance(launch, str):
            return self._fail_before_start(job_dir, launch)
        # 4. 起動 (止める要求の確かめと起動を、同じロックの中で行う)
        try:
            with self._lock:
                stopped = self._stopped_result_locked(key)
                if stopped is not None:
                    return stopped
                process = self._start_locked(key, launch)
        except OSError as error:
            return self._fail_before_start(job_dir, describe_exception(error))
        # 5・6. 出力を最後まで読む。例外で抜けるときは、子プロセスを止めて回収してから出す
        started_at = self._clock()
        try:
            output = self._read_output(process, job_dir, started_at)
        except BaseException:
            self._stop_process_after_error(process)
            raise
        # 7. 実行中の記録から外し、止める要求を読む
        with self._lock:
            self._running.pop(key, None)
            self._finished.add(key)
            stop_requested = key in self._stop_requested
            kill_requested = key in self._kill_requested
        # 8. result.json
        record = build_result_record(
            returncode=output.returncode,
            duration_s=output.duration_s,
            result_message=output.result_message,
            last_assistant_text=output.last_assistant_text,
            stopped=stop_requested,
            killed=kill_requested,
            error=output.write_error,
            stderr=output.stderr,
        )
        _write_result(job_dir, record)
        # 9. 戻り値
        if stop_requested:
            return _stopped_result(kill_requested)
        last_message = record["last_message"]
        last_text = last_message if isinstance(last_message, str) else ""
        if (
            output.returncode == 0
            and output.result_message is not None
            and output.result_message.get("is_error") is not True
        ):
            return DeepResult(ok=True, message=last_text[:RESULT_MESSAGE_MAX_CHARS])
        message = f"claude exited with code {output.returncode}"
        if last_text:
            message += f": {last_text[:RESULT_MESSAGE_MAX_CHARS]}"
        return DeepResult(ok=False, message=message)

    def _prepare_launch(self, job_dir: Path) -> _Launch | str:
        """run の 3 (起動の用意)。起動の引数・作業ディレクトリー・環境変数を返す。

        対象の文書のパスにシンボリックリンクがあれば、起動しない理由 (symlink_refusal_message) の文字列を返す。
        job.json が読めないなどの失敗は、例外をそのまま出す。
        """
        context = read_prompt_context(job_dir)
        link = find_symlink_in_note_paths(context.run_dir / NOTES_DIR, context.notes)
        if link is not None:
            return symlink_refusal_message(link)
        command = build_claude_command(
            build_deep_prompt(context), context.run_dir, context.notes, self.options
        )
        environ = self._environ if self._environ is not None else os.environ
        env = build_child_env(environ, self.options.hidden_env_names)
        return _Launch(command=command, cwd=context.run_dir, env=env)

    def _start_locked(self, key: Path, launch: _Launch) -> ClaudeProcess:
        """claude を起動し、実行中のジョブとして記録して返す (ロックの中で呼ぶ)。起動できなければ OSError を出す。"""
        start_process = (
            self._start_process if self._start_process is not None else subprocess.Popen
        )
        process = start_process(
            launch.command,
            cwd=launch.cwd,
            env=launch.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        self._running[key] = process
        return process

    def _read_output(
        self, process: ClaudeProcess, job_dir: Path, started_at: float
    ) -> _ProcessOutput:
        """標準出力を stream.jsonl に書きながら最後まで読み、終了を待つ (run の 5 と 6)。"""
        stream_log = ClaudeStreamLog(job_dir / STREAM_FILE)
        try:
            stderr_reader = _StderrReader(process.stderr)
            if process.stdout is not None:
                for line in process.stdout:
                    stream_log.add_line(line, self._clock() - started_at)
            returncode = process.wait()
            stderr = stderr_reader.join(STDERR_JOIN_TIMEOUT_S)
            duration_s = self._clock() - started_at
        finally:
            stream_log.close()
        return _ProcessOutput(
            returncode=returncode,
            duration_s=duration_s,
            result_message=stream_log.result_message,
            last_assistant_text=stream_log.last_assistant_text,
            write_error=stream_log.write_error,
            stderr=stderr,
        )

    def _stop_process_after_error(self, process: ClaudeProcess) -> None:
        """run が例外で抜けるときに、まだ回収していない子プロセスのプロセスグループを止めて回収する。"""
        if process.returncode is not None:
            return
        logger.warning(
            "stopping claude (process group %s) because the deep job raised an exception",
            process.pid,
        )
        self._send_signal(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=ERROR_STOP_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            pass
        except BaseException:
            # 待つ間に別の例外 (もう一度 Ctrl+C を押したなど) が出た。回収は待たずに強制終了だけを送る
            self._send_signal(process.pid, signal.SIGKILL)
            raise
        self._send_signal(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=ERROR_STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            logger.warning(
                "claude (process group %s) did not end after SIGKILL", process.pid
            )

    def _request_stop(self, job_dir: Path, kill: bool) -> None:
        """stop と kill の本体。止める要求を記録し、実行中ならプロセスグループにシグナルを送る。"""
        key = job_dir.absolute()
        with self._lock:
            if key in self._finished:
                return
            self._stop_requested.add(key)
            if kill:
                self._kill_requested.add(key)
            process = self._running.get(key)
            # 回収済みのプロセス (returncode がある) には送らない (pid が別のプロセスに使い回されうるため)
            if process is not None and process.returncode is None:
                self._send_signal(
                    process.pid, signal.SIGKILL if kill else signal.SIGTERM
                )

    def _send_signal(self, pgid: int, sig: int) -> None:
        """プロセスグループ pgid に sig を送る。既に無いグループは無視し、ほかの OSError は警告を書いて外に出さない。"""
        try:
            self._signal_process_group(pgid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            logger.warning(
                "failed to send signal %s to the process group %s",
                sig,
                pgid,
                exc_info=True,
            )

    def _stopped_result_locked(self, key: Path) -> DeepResult | None:
        """key のジョブに stop か kill が呼ばれていれば、止められた結果を返す。呼ばれていなければ None。"""
        if key not in self._stop_requested:
            return None
        return _stopped_result(key in self._kill_requested)

    def _fail_before_start(self, job_dir: Path, message: str) -> DeepResult:
        """起動しなかった (起動できなかった) ことを result.json に書き、DeepResult(ok=False, message=message) を返す。"""
        record = build_result_record(
            returncode=None,
            duration_s=0.0,
            result_message=None,
            last_assistant_text=None,
            stopped=False,
            killed=False,
            error=message,
            stderr="",
        )
        _write_result(job_dir, record)
        return DeepResult(ok=False, message=message)


def _stopped_result(killed: bool) -> DeepResult:
    """stop か kill で止められた run の結果を返す。"""
    return DeepResult(
        ok=False, message=KILLED_MESSAGE if killed else STOPPED_MESSAGE, stopped=True
    )


def _write_result(job_dir: Path, record: dict[str, object]) -> None:
    """job_dir/result.json に record を置き換えて書く。書けなければ (OSError) 警告を書く。"""
    try:
        write_json_atomic(job_dir / RESULT_FILE, record)
    except OSError:
        logger.warning("failed to write %s", job_dir / RESULT_FILE, exc_info=True)
