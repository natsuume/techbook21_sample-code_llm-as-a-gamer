"""主ループの claude -p (stream-json の入出力) の子プロセス 1 つ (ClaudeStreamSession)。

起動・標準入力への書き込み・標準出力の読み取り・終了を扱う。行の中身の読み方は orchestrator.stream_messages と
deep.claude_stream_log の関数で行い、このモジュールはプロセスと行の受け渡しだけを扱う。
    - 起動 (start): 子プロセスを新しいセッション (プロセスグループ) の先頭として起動する (start_new_session=True。
      プロセスグループの ID は子プロセスの pid と同じ)。作業ディレクトリーは実行ディレクトリー
    - 標準出力: 別のスレッド (daemon) が 1 行ずつ読み、届くたびに stream.jsonl に書き (deep.claude_stream_log の
      ClaudeStreamLog。deep の stream.jsonl と同じ形)、読んだ行をキューに入れる。主ループは read で 1 つずつ受け取る
    - 標準エラー出力: 別のスレッド (daemon) が最後まで読む
    - 終了 (close): 標準入力を閉じ、grace_s 秒 (既定 5 秒) たっても終わらなければプロセスグループに SIGTERM を送り、
      さらに grace_s 秒たっても終わらなければ SIGKILL を送る
外部に触れないテストのため、子プロセスの起動・シグナルの送り先・時計を差し替えられる (deep の ClaudeCodeDeepRunner と
同じ形)。
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# close で、標準入力を閉じてから SIGTERM まで、SIGTERM から SIGKILL までに待つ秒数の既定値
SESSION_CLOSE_GRACE_S = 5.0
# close で、標準出力を読むスレッドの終わりを待つ上限 (秒)
READER_JOIN_TIMEOUT_S = 5.0


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
        raise NotImplementedError

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
        raise NotImplementedError

    def send(self, content: Sequence[Mapping[str, object]]) -> bool:
        """標準入力に orchestrator.stream_messages.user_message_line(content) を書いて flush し、書けたら True を返す。

        子プロセスが既に終わっていて書けないとき (BrokenPipeError を含む OSError、閉じた標準入力の ValueError) は、
        例外を出さずに False を返す。start の前と close の後は False を返す。
        """
        raise NotImplementedError

    def read(self, timeout_s: float) -> StreamLine | StreamEnd | None:
        """標準出力の次の行を、最大 timeout_s 秒待って返す。

        行が届いていれば StreamLine、標準出力が終わっていれば StreamEnd を返す。timeout_s 秒のうちに何も届かなければ
        None を返す。キューに残った行は、StreamEnd より先に、届いた順に返す。start の前は StreamEnd を返す。
        """
        raise NotImplementedError

    def close(self, grace_s: float = SESSION_CLOSE_GRACE_S) -> int | None:
        """子プロセスを終わらせ、終了コードを返す。

        1. 標準入力を閉じる (閉じる失敗の OSError は無視する)
        2. wait(timeout=grace_s) で待つ。終わればその終了コード
        3. 終わらなければ (subprocess.TimeoutExpired)、signal_process_group(pid, signal.SIGTERM) を呼び、
           wait(timeout=grace_s) で待つ
        4. まだ終わらなければ signal_process_group(pid, signal.SIGKILL) を呼び、wait(timeout=grace_s) で待つ。
           それでも終わらなければ、logging で警告を書いて None を返す
        シグナルの ProcessLookupError (既に終わったプロセスグループ) は無視し、ほかの OSError は logging で警告を書いて
        続ける。既に終わったプロセス (returncode がある) にはシグナルを送らない。
        終わった後に、標準出力を読むスレッドの終わりを最大 READER_JOIN_TIMEOUT_S 秒待ち、ClaudeStreamLog を閉じる。
        start の前に呼んだら何もせずに None を返す。2 回目以降の呼び出しは、1 回目と同じ値を返す (何もしない)。
        """
        raise NotImplementedError

    @property
    def returncode(self) -> int | None:
        """子プロセスの終了コード。起動していないか、まだ終わっていなければ None。"""
        raise NotImplementedError

    @property
    def stderr_text(self) -> str:
        """標準エラー出力のうち、読み終えた分 (close の後は最後まで)。"""
        raise NotImplementedError
