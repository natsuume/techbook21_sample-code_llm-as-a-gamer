"""主ループの codex app-server の子プロセス 1 つ (CodexAppServerProcess) と、その stream.jsonl (CodexStreamLog)。

起動・標準入力への書き込み・標準出力の読み取り・終了を扱う。行の中身の読み方は orchestrator.codex_messages の関数で
行い、このモジュールはプロセスと行の受け渡しだけを扱う (Claude 版の claude_stream_session.ClaudeStreamSession と
同じ役割)。
    - 起動 (start): 子プロセスを新しいセッション (プロセスグループ) の先頭として起動する (start_new_session=True)。
      作業ディレクトリーは実行ディレクトリー
    - 標準出力: 別のスレッド (daemon) が 1 行ずつ読み、届くたびに stream.jsonl に書き、読んだ行
      (claude_stream_session.StreamLine) をキューに入れる。read で 1 つずつ受け取る。終わったら StreamEnd を入れる
    - 標準エラー出力: 別のスレッド (daemon) が最後まで読む
    - 標準入力: send は、1 つのロックを取ってから 1 行の JSON を書く (ターンの入力の書き込みと、サーバーからの要求への
      応答が、別のスレッドから同時に書いても行が混ざらないようにする)。書いた要求と応答も stream.jsonl に書く
    - 終了 (close): Claude 版 (ClaudeStreamSession.close) と同じ手順。標準入力を閉じ、grace_s 秒たっても終わらなければ
      プロセスグループに SIGTERM を送り、さらに grace_s 秒たっても終わらなければ SIGKILL を送る。ほかのスレッドが
      書き込みの途中なら、標準入力を閉じると書き込みの終わりを待ってしまうため、閉じずにすぐ SIGTERM を送る
stream.jsonl の 1 行の形 (CodexStreamLog):
    {"elapsed_s": <起動からの秒 (小数第 3 位まで)>, "data": <届いた行を JSON として読んだもの>}   届いた行
    {"elapsed_s": <同じ>, "raw": <JSON として読めなかった届いた行 (末尾の改行を除く)>}         届いた行
    {"elapsed_s": <同じ>, "sent": <送った JSON>}                                           送った行
    届いた行は deep.claude_stream_log と同じ形で、送った行はキーで見分ける。
外部に触れないテストのため、子プロセスの起動・シグナルの送り先・時計を差し替えられる。
"""

import json
import logging
import os
import queue
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO

from switch2_layers.deep.claude_stream_log import stream_line_record
from switch2_layers.error_text import describe_exception
from switch2_layers.file_io import format_json_line
from switch2_layers.orchestrator.claude_stream_session import (
    SESSION_CLOSE_GRACE_S,
    SessionLaunch,
    StreamEnd,
    StreamLine,
    StreamProcess,
    StreamProcessStarter,
)

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# close で、標準出力を読むスレッドの終わりを待つ上限 (秒)
READER_JOIN_TIMEOUT_S = 5.0


class CodexStreamLog:
    """届いた行と送った行を stream.jsonl に 1 行ずつ追記する (モジュールの docstring の形)。

    path   stream.jsonl のパス (親のディレクトリーは呼び出し側が作る)。作るときにファイルを空にして作る
    標準出力を読むスレッドと、書き込むスレッドの両方から呼ぶため、1 つのロックを通して書く。
    """

    def __init__(self, path: Path) -> None:
        """path を空のファイルとして作る (既にあれば中身を消す)。作れなければ OSError をそのまま出す。"""
        self.path = path
        self._file = path.open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self._closed = False
        self._write_error: str | None = None

    def add_received(self, line: str, elapsed_s: float) -> None:
        """届いた行 line を deep.claude_stream_log.stream_line_record(line, elapsed_s) の形で追記する。"""
        self._write(stream_line_record(line, elapsed_s))

    def add_sent(self, message: Mapping[str, object], elapsed_s: float) -> None:
        """送った message を {"elapsed_s": 小数第 3 位に丸めた elapsed_s, "sent": message} の形で追記する。"""
        self._write({"elapsed_s": round(elapsed_s, 3), "sent": dict(message)})

    def _write(self, record: Mapping[str, object]) -> None:
        """record を 1 行追記して flush する。失敗したら (OSError・ValueError)、以後は書かず、write_error に最初の
        失敗の説明を入れる (例外は出さない。記録の失敗で app-server とのやり取りを止めないため)。閉じた後は書かない。"""
        with self._lock:
            if self._closed or self._write_error is not None:
                return
            try:
                self._file.write(format_json_line(record))
                self._file.flush()
            except (OSError, ValueError) as error:
                self._write_error = describe_exception(error)

    @property
    def write_error(self) -> str | None:
        """stream.jsonl への最初の書き込みの失敗の説明。失敗していなければ None。"""
        return self._write_error

    def close(self) -> None:
        """ファイルを閉じる。2 回目以降の呼び出しと、閉じる失敗 (OSError) は無視する。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._file.close()
            except OSError:
                pass


class CodexAppServerProcess:
    """主ループの codex app-server の子プロセス 1 つ。

    launch                 起動に使う値 (claude_stream_session.SessionLaunch。command・cwd・env)
    stream_path            stream.jsonl のパス (orchestrator/session-<NNN>/stream.jsonl。親のディレクトリーは
                           呼び出し側が作る)。start のときに空のファイルとして作る
    clock                  elapsed_s を測る時計 (秒を返す関数)
    start_process          子プロセスを起動する関数 (None なら subprocess.Popen)
    signal_process_group   プロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    start・read・close は 1 つのスレッド (主ループ) から呼ぶ。send はどのスレッドから呼んでもよい。
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
        self._stream_log: CodexStreamLog | None = None
        self._started_at = 0.0
        self._items: queue.Queue[StreamLine | StreamEnd] = queue.Queue()
        self._ended = False
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_text = ""
        self._closed = False
        self._close_code: int | None = None
        # 標準入力への書き込みを 1 つずつにするロック
        self._write_lock = threading.Lock()

    def start(self) -> None:
        """子プロセスを起動し、標準出力と標準エラー出力を読むスレッドを起動する。

        stream_path に CodexStreamLog を作ってから (作れなければ OSError をそのまま出し、起動しない)、
        start_process(launch.command, cwd=launch.cwd, env=launch.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", start_new_session=True) を呼ぶ
        (Claude 版と同じ引数)。start_process の OSError は、CodexStreamLog を閉じてからそのまま出す。
        2 回目の呼び出しは RuntimeError にする。
        """
        if self._started:
            raise RuntimeError("the codex app-server has already been started")
        self._started = True
        stream_log = CodexStreamLog(self.stream_path)
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
            target=self._read_stdout, name="codex-app-server-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="codex-app-server-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def pid(self) -> int | None:
        """子プロセスのプロセス ID (プロセスグループの ID でもある)。起動していなければ None。"""
        return self._process.pid if self._process is not None else None

    def send(self, message: Mapping[str, object]) -> bool:
        """標準入力に message の JSON (json.dumps(…, ensure_ascii=False)) と改行を書いて flush し、書けたら True。

        ロックを取ってから書き、書けたら stream.jsonl に送った行として書く。子プロセスが既に終わっていて書けない
        とき (BrokenPipeError を含む OSError、閉じた標準入力の ValueError) は、例外を出さずに False を返す。start の前と
        close の後も False を返す。
        """
        line = json.dumps(dict(message), ensure_ascii=False) + "\n"
        with self._write_lock:
            process = self._process
            if process is None or self._closed or process.stdin is None:
                return False
            if not _write_line(process.stdin, line):
                return False
        if self._stream_log is not None:
            self._stream_log.add_sent(message, self._clock() - self._started_at)
        return True

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
        """子プロセスを終わらせ、終了コードを返す (Claude 版の ClaudeStreamSession.close と同じ手順)。

        1. 書き込みのロックを待たずに取れたら、標準入力を閉じ (閉じる失敗の OSError と ValueError は無視する)、
           wait(timeout=grace_s) で待つ。取れなければ (ほかのスレッドが書き込みの途中)、poll で終わったかだけを確かめる
        2. 終わらなければ signal_process_group(pid, signal.SIGTERM) を呼び、wait(timeout=grace_s) で待つ
        3. まだ終わらなければ signal_process_group(pid, signal.SIGKILL) を呼び、wait(timeout=grace_s) で待つ。
           それでも終わらなければ、logging で警告を書いて None を返す
        シグナルの ProcessLookupError は無視し、ほかの OSError は logging で警告を書いて続ける。既に終わったプロセスには
        シグナルを送らない。終わった後に、標準出力を読むスレッドの終わりを最大 READER_JOIN_TIMEOUT_S 秒待ち、
        stream.jsonl を閉じる。start の前に呼んだら何もせずに None を返す。2 回目以降の呼び出しは、1 回目と同じ値を返す。
        """
        process = self._process
        if process is None:
            return None
        if self._closed:
            return self._close_code
        self._closed = True
        if self._write_lock.acquire(blocking=False):
            try:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except (OSError, ValueError):
                        pass
            finally:
                self._write_lock.release()
            code = _wait_for(process, grace_s)
        else:
            code = process.poll()
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if code is not None:
                break
            self._send_signal(process, sig)
            code = _wait_for(process, grace_s)
        if code is None:
            logger.warning(
                "codex app-server (process group %s) did not end after SIGKILL",
                process.pid,
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
        """標準出力を読むスレッドの本体。行ごとに stream.jsonl に書いてから StreamLine をキューに入れ、終わったら
        StreamEnd を入れる。読む失敗は logging に書く。"""
        process = self._process
        stream_log = self._stream_log
        try:
            if process is not None and process.stdout is not None:
                for line in process.stdout:
                    elapsed_s = self._clock() - self._started_at
                    if stream_log is not None:
                        stream_log.add_received(line, elapsed_s)
                    self._items.put(
                        StreamLine(
                            data=_parse_line(line), elapsed_s=round(elapsed_s, 3)
                        )
                    )
        except (OSError, ValueError):
            logger.warning("failed to read the stdout of codex", exc_info=True)
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
            logger.warning("failed to read the stderr of codex", exc_info=True)

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
    """stdin に line を書いて flush し、書けたら True を返す。書けなければ (OSError・ValueError) False。"""
    try:
        stdin.write(line)
        stdin.flush()
    except (OSError, ValueError):
        logger.info("failed to write to the stdin of codex", exc_info=True)
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
