"""主ループの claude -p (stream-json の入出力) の子プロセス 1 つ (ClaudeStreamSession)。

起動・標準入力への書き込み・標準出力の読み取り・終了を扱う。行の中身の読み方は orchestrator.stream_messages と
deep.claude_stream_log の関数で行い、このモジュールはプロセスと行の受け渡しだけを扱う。
    - 起動 (start): 子プロセスを新しいセッション (プロセスグループ) の先頭として起動する (start_new_session=True。
      プロセスグループの ID は子プロセスの pid と同じ)。作業ディレクトリーは実行ディレクトリー
    - 標準出力: 別のスレッド (daemon) が 1 行ずつ読み、届くたびに stream.jsonl に書き (deep.claude_stream_log の
      ClaudeStreamLog。deep の stream.jsonl と同じ形)、読んだ行をキューに入れる。主ループは read で 1 つずつ受け取る
    - 標準エラー出力: 別のスレッド (daemon) が最後まで読む
    - 標準入力: send は呼んだスレッドで書く。send_in_background は書き込み用のスレッド (daemon) で書き、すぐ戻る。
      子プロセスが標準入力を読まなくなると、パイプのバッファーを超える行 (画像を含む入力など) の書き込みは戻らない。
      主ループは send_in_background を使い、書き終わるのを、停止の合図と --turn-timeout を確かめながら待つ
    - 終了 (close): 標準入力を閉じ、grace_s 秒 (既定 5 秒) たっても終わらなければプロセスグループに SIGTERM を送り、
      さらに grace_s 秒たっても終わらなければ SIGKILL を送る。send_in_background の書き込みが終わっていなければ、
      標準入力を閉じると書き込みの終わりを待ってしまうため、閉じずに (最初の grace_s 秒も待たずに) すぐ SIGTERM を
      送る (子プロセスが終われば、戻らなかった書き込みは失敗として終わる)
外部に触れないテストのため、子プロセスの起動・シグナルの送り先・時計を差し替えられる (deep の ClaudeCodeDeepRunner と
同じ形)。
"""

import json
import logging
import os
import queue
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from switch2_layers.deep.claude_stream_log import ClaudeStreamLog
from switch2_layers.orchestrator.stream_messages import user_message_line

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# close で、標準入力を閉じてから SIGTERM まで、SIGTERM から SIGKILL までに待つ秒数の既定値
SESSION_CLOSE_GRACE_S = 5.0
# close で、標準出力を読むスレッドの終わりを待つ上限 (秒)
READER_JOIN_TIMEOUT_S = 5.0


class PendingSend:
    """send_in_background で書き始めた 1 行の書き込み。"""

    def __init__(self) -> None:
        """書き終えていない状態で作る。"""
        self._finished = threading.Event()
        self._written = False

    def finish(self, written: bool) -> None:
        """書き込みが終わったことを記録する (written は書けたら True)。書き込み用のスレッドが呼ぶ。"""
        self._written = written
        self._finished.set()

    def wait(self, timeout_s: float) -> bool | None:
        """書き込みの終わりを最大 timeout_s 秒待つ。書けたら True、書けなかったら False、まだ終わっていなければ None。"""
        if not self._finished.wait(max(timeout_s, 0.0)):
            return None
        return self._written

    @property
    def finished(self) -> bool:
        """書き込みが終わっていれば (書けたかに依らず) True。"""
        return self._finished.is_set()


class StreamProcess(Protocol):
    """起動した claude の子プロセス (subprocess.Popen[str] が満たす部分)。

    pid          プロセス ID。start_new_session=True で起動するため、プロセスグループの ID でもある
    stdin        標準入力 (テキスト)。write と flush で 1 行ずつ書き、close で閉じる
    stdout       標準出力 (テキスト)。行ごとに読む (for line in stdout)
    stderr       標準エラー出力 (テキスト)。read() で最後まで読む
    returncode   終了コード。終わっていなければ None
    """

    pid: int
    stdin: IO[str] | None
    stdout: IO[str] | None
    stderr: IO[str] | None
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int:
        """終わるのを最大 timeout 秒待ち、終了コードを返す。終わらなければ subprocess.TimeoutExpired を出す。"""
        ...

    def poll(self) -> int | None:
        """終わっていれば終了コード、終わっていなければ None を返す。"""
        ...


class StreamProcessStarter(Protocol):
    """子プロセスを起動する関数 (subprocess.Popen と同じ引数を受け取る)。"""

    def __call__(self, args: list[str], **kwargs: object) -> StreamProcess:
        """args を起動して StreamProcess を返す。起動できなければ OSError を出す。"""
        ...


@dataclass(frozen=True)
class SessionLaunch:
    """claude の起動に使う値。

    command   引数の列 (orchestrator.claude_session_command.build_session_command)
    cwd       作業ディレクトリー (実行ディレクトリーのシンボリックリンクを解決した絶対パス)
    env       子プロセスの環境変数 (orchestrator.claude_session_command.build_session_env)
    """

    command: list[str]
    cwd: Path
    env: dict[str, str]


@dataclass(frozen=True)
class StreamLine:
    """標準出力の 1 行。

    data        行 (末尾の改行を除く) を JSON として読んだ値。読めない行は None
    elapsed_s   起動からその行を読むまでの秒 (stream.jsonl の elapsed_s と同じ値)
    """

    data: object | None
    elapsed_s: float


@dataclass(frozen=True)
class StreamEnd:
    """標準出力が終わった (子プロセスが終わった) ことを表す。read はこれを返した後も、毎回これを返す。"""


class ClaudeStreamSession:
    """主ループの claude の子プロセス 1 つ。

    launch                 起動に使う値
    stream_path            stream.jsonl のパス (orchestrator/session-<NNN>/stream.jsonl。親のディレクトリーは
                           呼び出し側が作る)。start のときに空のファイルとして作る
    clock                  elapsed_s を測る時計 (秒を返す関数)
    start_process          子プロセスを起動する関数 (None なら subprocess.Popen。start のときに subprocess.Popen を引く)
    signal_process_group   プロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    start・send・read・close は 1 つのスレッド (主ループ) から呼ぶ。
    """

    def __init__(
        self,
        launch: SessionLaunch,
        stream_path: Path,
        clock: Callable[[], float],
        start_process: StreamProcessStarter | None = None,
        signal_process_group: Callable[[int, int], None] | None = None,
    ) -> None:
        """属性を持つ。子プロセスはまだ起動しない。"""
        self.launch = launch
        self.stream_path = stream_path
        self._clock = clock
        self._start_process = start_process
        self._signal_process_group = (
            signal_process_group if signal_process_group is not None else os.killpg
        )
        self._started = False
        self._process: StreamProcess | None = None
        self._stream_log: ClaudeStreamLog | None = None
        self._started_at = 0.0
        self._items: queue.Queue[StreamLine | StreamEnd] = queue.Queue()
        self._ended = False
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_text = ""
        self._closed = False
        self._close_code: int | None = None
        # send_in_background の、最後に始めた書き込み
        self._pending_send: PendingSend | None = None

    def start(self) -> None:
        """子プロセスを起動し、標準出力と標準エラー出力を読むスレッドを起動する。

        stream_path に deep.claude_stream_log.ClaudeStreamLog を作ってから (作れなければ OSError をそのまま出し、
        起動しない)、start_process(launch.command, cwd=launch.cwd, env=launch.env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        start_new_session=True) を呼ぶ。start_process の OSError は、ClaudeStreamLog を閉じてからそのまま出す。
        標準出力を読むスレッドは、行が届くたびに ClaudeStreamLog.add_line(行, 経過秒) を呼んでから、
        StreamLine(行を JSON として読んだ値 (読めなければ None), 経過秒) をキューに入れ、標準出力が終わったら
        StreamEnd() を入れる。経過秒は clock() と、起動した直後の clock() の差。
        2 回目の呼び出しは RuntimeError にする。
        """
        if self._started:
            raise RuntimeError("the claude session has already been started")
        self._started = True
        stream_log = ClaudeStreamLog(self.stream_path)
        start_process = (
            self._start_process if self._start_process is not None else subprocess.Popen
        )
        try:
            process = start_process(
                self.launch.command,
                cwd=self.launch.cwd,
                env=self.launch.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except OSError:
            stream_log.close()
            raise
        self._started_at = self._clock()
        self._process = process
        self._stream_log = stream_log
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, name="claude-session-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="claude-session-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def send(self, content: Sequence[Mapping[str, object]]) -> bool:
        """標準入力に orchestrator.stream_messages.user_message_line(content) を書いて flush し、書けたら True を返す。

        子プロセスが既に終わっていて書けないとき (BrokenPipeError を含む OSError、閉じた標準入力の ValueError) は、
        例外を出さずに False を返す。start の前と close の後は False を返す。
        """
        process = self._process
        if process is None or self._closed or process.stdin is None:
            return False
        return _write_line(process.stdin, user_message_line(content))

    def send_in_background(
        self, content: Sequence[Mapping[str, object]]
    ) -> PendingSend:
        """send と同じ 1 行の書き込みを、書き込み用のスレッド (daemon) で始め、書き終わるのを待たずに戻る。

        戻り値の PendingSend.wait で、書き終わったか (書けたら True、書けなかったら False) を待つ。行は呼んだスレッドで
        作る (content が空なら ValueError)。start の前と close の後は、書けなかった (False) として終わった PendingSend を
        返す。前の send_in_background の書き込みが終わっていなければ、RuntimeError にする (書き込みは 1 つずつ)。
        """
        line = user_message_line(content)
        pending = PendingSend()
        process = self._process
        if process is None or self._closed or process.stdin is None:
            pending.finish(False)
            return pending
        if self._pending_send is not None and not self._pending_send.finished:
            raise RuntimeError(
                "the previous write to the stdin of claude has not finished"
            )
        self._pending_send = pending
        stdin = process.stdin
        threading.Thread(
            target=lambda: pending.finish(_write_line(stdin, line)),
            name="claude-session-stdin",
            daemon=True,
        ).start()
        return pending

    def read(self, timeout_s: float) -> StreamLine | StreamEnd | None:
        """標準出力の次の行を、最大 timeout_s 秒待って返す。

        行が届いていれば StreamLine、標準出力が終わっていれば StreamEnd を返す。timeout_s 秒のうちに何も届かなければ
        None を返す。キューに残った行は、StreamEnd より先に、届いた順に返す。start の前は StreamEnd を返す。
        """
        if self._process is None or self._ended:
            return StreamEnd()
        try:
            item = self._items.get(timeout=max(timeout_s, 0.0))
        except queue.Empty:
            return None
        if isinstance(item, StreamEnd):
            self._ended = True
        return item

    def close(self, grace_s: float = SESSION_CLOSE_GRACE_S) -> int | None:
        """子プロセスを終わらせ、終了コードを返す。

        1. 標準入力を閉じる (閉じる失敗の OSError は無視する)
        2. wait(timeout=grace_s) で待つ。終わればその終了コード
           send_in_background の書き込みが終わっていなければ、閉じると書き込みの終わりを待ってしまうため、1 と 2 の
           代わりに poll で終わったかだけを確かめ、終わっていなければすぐ 3 へ進む
        3. 終わらなければ (subprocess.TimeoutExpired)、signal_process_group(pid, signal.SIGTERM) を呼び、
           wait(timeout=grace_s) で待つ
        4. まだ終わらなければ signal_process_group(pid, signal.SIGKILL) を呼び、wait(timeout=grace_s) で待つ。
           それでも終わらなければ、logging で警告を書いて None を返す
        シグナルの ProcessLookupError (既に終わったプロセスグループ) は無視し、ほかの OSError は logging で警告を書いて
        続ける。既に終わったプロセス (returncode がある) にはシグナルを送らない。
        終わった後に、標準出力を読むスレッドの終わりを最大 READER_JOIN_TIMEOUT_S 秒待ち、ClaudeStreamLog を閉じる。
        start の前に呼んだら何もせずに None を返す。2 回目以降の呼び出しは、1 回目と同じ値を返す (何もしない)。
        """
        process = self._process
        if process is None:
            return None
        if self._closed:
            return self._close_code
        self._closed = True
        if self._pending_send is not None and not self._pending_send.finished:
            code = process.poll()
        else:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
            code = _wait_for(process, grace_s)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if code is not None:
                break
            self._send_signal(process, sig)
            code = _wait_for(process, grace_s)
        if code is None:
            logger.warning(
                "claude (process group %s) did not end after SIGKILL", process.pid
            )
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(READER_JOIN_TIMEOUT_S)
        if self._stream_log is not None:
            self._stream_log.close()
        self._close_code = code
        return code

    @property
    def returncode(self) -> int | None:
        """子プロセスの終了コード。起動していないか、まだ終わっていなければ None。"""
        if self._process is None:
            return None
        return self._process.returncode

    @property
    def stderr_text(self) -> str:
        """標準エラー出力のうち、読み終えた分 (close の後は最後まで)。"""
        return self._stderr_text

    def _read_stdout(self) -> None:
        """標準出力を読むスレッドの本体 (start の docstring)。読む失敗は logging に書き、StreamEnd を入れて終わる。"""
        process = self._process
        stream_log = self._stream_log
        try:
            if process is not None and process.stdout is not None:
                for line in process.stdout:
                    elapsed_s = self._clock() - self._started_at
                    if stream_log is not None:
                        stream_log.add_line(line, elapsed_s)
                    self._items.put(
                        StreamLine(
                            data=_parse_line(line), elapsed_s=round(elapsed_s, 3)
                        )
                    )
        except (OSError, ValueError):
            logger.warning("failed to read the stdout of claude", exc_info=True)
        finally:
            self._items.put(StreamEnd())

    def _read_stderr(self) -> None:
        """標準エラー出力を最後まで読むスレッドの本体。読めなければ logging に書く。"""
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            self._stderr_text = process.stderr.read()
        except (OSError, ValueError):
            logger.warning("failed to read the stderr of claude", exc_info=True)

    def _send_signal(self, process: StreamProcess, sig: int) -> None:
        """終わっていない process のプロセスグループに sig を送る。既に無いグループは無視し、ほかの OSError は警告を書く。"""
        if process.poll() is not None:
            return
        try:
            self._signal_process_group(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            logger.warning(
                "failed to send signal %s to the process group %s",
                sig,
                process.pid,
                exc_info=True,
            )


def _write_line(stdin: IO[str], line: str) -> bool:
    """stdin に line を書いて flush し、書けたら True を返す。

    子プロセスが終わっていて書けないとき (BrokenPipeError を含む OSError、閉じた標準入力の ValueError) は False。
    """
    try:
        stdin.write(line)
        stdin.flush()
    except (OSError, ValueError):
        logger.info("failed to write to the stdin of claude", exc_info=True)
        return False
    return True


def _wait_for(process: StreamProcess, timeout_s: float) -> int | None:
    """process の終わりを最大 timeout_s 秒待ち、終了コードを返す。終わらなければ None。"""
    try:
        return process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return None


def _parse_line(line: str) -> object | None:
    """行 (末尾の改行を除く) を JSON として読んだ値を返す。読めなければ None。"""
    try:
        return json.loads(line.rstrip("\r\n"))
    except ValueError:
        return None
