"""主ループのセッションの Codex の実装 (CodexMainLoopSession と CodexMainLoopSessionFactory)。

orchestrator.main_loop_session の境界 (MainLoopSession・MainLoopSessionFactory) を、codex app-server を標準入出力の
JSON-RPC で開いたままにする子プロセス (orchestrator.codex_app_server_process.CodexAppServerProcess) で満たす。
1 つのセッションは、1 つの app-server と、その中の 1 つのスレッド (ephemeral) にあたる。
    - 作る (CodexMainLoopSessionFactory.create): run_dir = paths.root のシンボリックリンクを解決した絶対パスとして、
      env = codex_session_command.build_codex_env(environ, hidden_env_names)、
      command = build_codex_command(executable, codex_skill_paths.find_codex_skill_files_to_disable(run_dir, env))、
      cwd = run_dir の CodexAppServerProcess を作り、CodexMainLoopSession で包む (起動はしない)。create を呼ぶたびに
      組み立てる
    - 起動 (start): 子プロセスを起動し、initialize の応答を待って通知 initialized を送り、Codex 本体のディレクトリーを
      探し (find_native_codex_dir)、thread/start (codex_session_command.build_thread_start_params) の応答のスレッドの
      ID を覚える。待つ上限は、合わせて startup_timeout_s 秒 (実時間)。途中で失敗したら (応答が誤り・応答が来ない・
      子プロセスが終わった・本体のディレクトリーが見つからない)、子プロセスを終わらせてから CodexStartError (OSError)
      を出す (主ループは、起動に失敗したセッションの close を呼ばないため)
    - 入力 (send_turn_in_background): 書き込み用のスレッド (daemon) で、画像の JPEG を
      <セッションのディレクトリー>/images/turn-<NNNN>-<順番>.jpg に書き、codex_messages.build_turn_input の入力の
      turn/start を送る。書き終わるのを待たずに戻る。turn/start の応答が誤りなら、TurnResult(is_error=True) を出来事に
      する (でないと --turn-timeout まで待つことになる)
    - 出来事 (read): 届いた通知を codex_messages.notification_events で出来事にする (このセッションのスレッドのもの
      だけ)。サーバーからの要求には codex_messages.server_request_reply の応答を返す (出来事にはしない)。標準出力の
      終わりは SessionEnded にする。要約の出来事は、同じ turnId のものを 1 つにする (thread/compacted と
      contextCompaction の item の両方が届いたとき)
    - 終了 (close): CodexAppServerProcess.close (Claude 版と同じ手順)
"""

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.deep.claude_code_command import HIDDEN_ENV_NAMES
from switch2_layers.orchestrator.claude_stream_session import (
    SessionLaunch,
    StreamEnd,
    StreamProcessStarter,
)
from switch2_layers.orchestrator.codex_app_server_process import (
    CodexAppServerProcess,
)
from switch2_layers.orchestrator.codex_messages import (
    INITIALIZE_METHOD,
    INITIALIZED_METHOD,
    THREAD_START_METHOD,
    TURN_START_METHOD,
    build_turn_input,
    build_turn_start_params,
    compaction_turn_id,
    is_notification,
    is_response,
    is_server_request,
    notification_events,
    notification_message,
    request_message,
    response_error_text,
    server_request_reply,
    thread_id_of_start_response,
)
from switch2_layers.orchestrator.codex_session_command import (
    CODEX_EXECUTABLE,
    build_codex_command,
    build_codex_env,
    build_initialize_params,
    build_thread_start_params,
)
from switch2_layers.orchestrator.codex_skill_paths import (
    find_codex_skill_files_to_disable,
)
from switch2_layers.orchestrator.main_loop_session import (
    AutoCompaction,
    MainLoopSessionEvent,
    SessionEnded,
    TurnResult,
)
from switch2_layers.orchestrator.pending_send import PendingSend
from switch2_layers.orchestrator.turn_input import TurnImage
from switch2_layers.run_directory import RunPaths

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# 起動 (initialize からスレッドの開始まで) を待つ上限の既定値 (実時間の秒)。2026-11-21 の確かめでは、起動から
# スレッドの開始の応答まで 5 秒ほどだった
CODEX_STARTUP_TIMEOUT_S = 30.0
# 起動の途中で、応答と Codex 本体のプロセスを待つ 1 回の間隔 (秒)
STARTUP_POLL_S = 0.05
# 起動に失敗したときに、子プロセスを終わらせる close に渡す秒数
STARTUP_FAILURE_CLOSE_GRACE_S = 1.0
# Codex 本体の実行ファイルの名前 (codex のコマンドは Node の包みで、その子孫に本体がいる)
NATIVE_CODEX_NAME = "codex"
# ターンの画像を書く、セッションのディレクトリーの下のディレクトリー
TURN_IMAGES_DIR = "images"


class CodexStartError(OSError):
    """codex app-server の起動の手順 (初期化・本体のディレクトリーの探索・スレッドの開始) が失敗した。

    主ループは、ほかの起動の失敗 (OSError) と同じく、最初のターンを終えずに終わったセッションとして数える。
    """


def turn_image_path(session_dir: Path, turn_number: int, index: int) -> Path:
    """turn_number 番目 (1 始まり) の入力の index 番目 (1 始まり、古い順) の画像を書くパス
    session_dir / TURN_IMAGES_DIR / f"turn-{turn_number:04d}-{index}.jpg" を返す。"""
    return session_dir / TURN_IMAGES_DIR / f"turn-{turn_number:04d}-{index}.jpg"


def find_native_codex_dir(root_pid: int, proc_dir: Path = Path("/proc")) -> Path | None:
    """プロセス root_pid とその子孫のうち、実行ファイル (proc_dir/<pid>/exe のリンク先) の名前が NATIVE_CODEX_NAME の
    ものを探し、その実行ファイルのディレクトリーを返す。見つからなければ None。

    子孫は proc_dir/<pid>/task/<tid>/children をたどって探す (root_pid 自身から、幅優先)。読めないプロセスは飛ばす。
    """
    pending = [root_pid]
    seen: set[int] = set()
    while pending:
        pid = pending.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        try:
            exe = Path(os.readlink(proc_dir / str(pid) / "exe"))
        except OSError:
            exe = None
        if exe is not None and exe.name == NATIVE_CODEX_NAME:
            return exe.parent
        try:
            tasks = sorted((proc_dir / str(pid) / "task").iterdir())
        except OSError:
            continue
        for task in tasks:
            try:
                children = (task / "children").read_text(encoding="ascii").split()
            except OSError:
                continue
            pending.extend(int(child) for child in children if child.isdigit())
    return None


class CodexMainLoopSession:
    """主ループのセッション (main_loop_session.MainLoopSession) の Codex の実装。codex app-server 1 つを包む。

    process             包む codex app-server の子プロセス (CodexMainLoopSessionFactory.create が作る)
    run_dir             実行ディレクトリーのシンボリックリンクを解決した絶対パス (スレッドの cwd)
    session_dir         セッションのディレクトリーの絶対パス (orchestrator/session-<NNN>/。ターンの画像を書く)
    model               スレッドのモデル (--codex-model)
    instructions        変わらない指示の文 (スレッドの developerInstructions)
    effort              ターンの effort (--orchestrator-effort)
    find_native_dir     Codex 本体のディレクトリーを探す関数 (既定 find_native_codex_dir。テストでは偽物)
    startup_timeout_s   起動を待つ上限 (実時間の秒)
    monotonic           起動の上限を測る時計 (既定 time.monotonic。主ループの時計はテストで進まないため別にする)
    start・send_turn_in_background・read・close は 1 つのスレッド (主ループ) から呼ぶ。
    """

    def __init__(
        self,
        process: CodexAppServerProcess,
        run_dir: Path,
        session_dir: Path,
        model: str,
        instructions: str,
        effort: str,
        find_native_dir: Callable[[int], Path | None] = find_native_codex_dir,
        startup_timeout_s: float = CODEX_STARTUP_TIMEOUT_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """属性を持つ。子プロセスはまだ起動しない。"""
        self.process = process
        self.run_dir = run_dir
        self.session_dir = session_dir
        self.model = model
        self.instructions = instructions
        self.effort = effort
        self._find_native_dir = find_native_dir
        self._startup_timeout_s = startup_timeout_s
        self._monotonic = monotonic
        self._next_request_id = 0
        self._thread_id: str | None = None
        self._turn_count = 0
        # 最後に送った turn/start の要求の id (その応答が誤りなら、ターンの結果を誤りにする)
        self._turn_start_id: int | None = None
        self._pending_send: PendingSend | None = None
        self._pending_events: deque[MainLoopSessionEvent] = deque()
        self._compacted_turns: set[str] = set()
        self._closed = False

    @property
    def thread_id(self) -> str | None:
        """スレッドの ID。起動を終えていなければ None。"""
        return self._thread_id

    def start(self) -> None:
        """codex app-server を起動し、スレッドを始める (モジュールの docstring の「起動」)。

        子プロセスを起動できなければ OSError をそのまま出す。起動の後の手順が失敗したら、子プロセスを
        STARTUP_FAILURE_CLOSE_GRACE_S 秒で終わらせてから CodexStartError を出す。ほかの例外 (想定外のもの) も、
        子プロセスを終わらせてから、そのまま出す。
        """
        self.process.start()
        deadline = self._monotonic() + self._startup_timeout_s
        try:
            self._request_and_wait(
                INITIALIZE_METHOD, build_initialize_params(), deadline
            )
            if not self.process.send(notification_message(INITIALIZED_METHOD)):
                raise CodexStartError("could not send initialized to codex")
            native_dir = self._wait_for_native_dir(deadline)
            params = build_thread_start_params(
                self.run_dir, self.model, self.instructions, native_dir
            )
            response = self._request_and_wait(THREAD_START_METHOD, params, deadline)
            thread_id = thread_id_of_start_response(response)
            if thread_id is None:
                raise CodexStartError("thread/start of codex returned no thread id")
        except BaseException:
            self.process.close(STARTUP_FAILURE_CLOSE_GRACE_S)
            raise
        self._thread_id = thread_id

    def send_turn_in_background(
        self, text: str, images: Sequence[TurnImage]
    ) -> PendingSend:
        """1 ターンの入力を、書き込み用のスレッド (daemon) で送り始め、PendingSend を返す (書き終わるのを待たない)。

        書き込み用のスレッドは、images の JPEG を turn_image_path(session_dir, ターンの番号, 順番) に書き (ディレクトリーは
        作る)、turn/start (codex_messages.build_turn_start_params(スレッドの ID, build_turn_input(text, 画像とパス),
        effort)) を送る。画像を書けない (OSError) か送れなければ、送れなかった (False) として終わる。
        起動を終えていない (スレッドが無い) か close の後は、送れなかった (False) として終わった PendingSend を返す。
        前の入力を送り終わる前に呼んだら RuntimeError にする。
        """
        pending = PendingSend()
        thread_id = self._thread_id
        if thread_id is None or self._closed:
            pending.finish(False)
            return pending
        if self._pending_send is not None and not self._pending_send.finished:
            raise RuntimeError("the previous turn input to codex has not been sent")
        self._turn_count += 1
        turn_number = self._turn_count
        request_id = self._new_request_id()
        self._turn_start_id = request_id
        self._pending_send = pending
        snapshot = list(images)

        def write_and_send() -> None:
            try:
                paths = self._write_turn_images(turn_number, snapshot)
            except OSError:
                logger.warning(
                    "could not write the turn images for codex", exc_info=True
                )
                pending.finish(False)
                return
            turn_input = build_turn_input(text, list(zip(snapshot, paths, strict=True)))
            params = build_turn_start_params(thread_id, turn_input, self.effort)
            message = request_message(request_id, TURN_START_METHOD, params)
            pending.finish(self.process.send(message))

        threading.Thread(
            target=write_and_send, name="codex-turn-input", daemon=True
        ).start()
        return pending

    def read(self, timeout_s: float) -> MainLoopSessionEvent | None:
        """次の出来事を返す。

        前に読んだ行の出来事が残っていれば、待たずにその先頭を返す。残っていなければ CodexAppServerProcess.read で
        次を受け取り、None なら None、StreamEnd なら SessionEnded() を返す。行なら _line_events で出来事にして残し、
        その先頭を返す。出来事にならない行 (サーバーからの要求を含む) なら、待ち直さずに None を返す。
        """
        if self._pending_events:
            return self._pending_events.popleft()
        item = self.process.read(timeout_s)
        if item is None:
            return None
        if isinstance(item, StreamEnd):
            return SessionEnded()
        self._pending_events.extend(self._line_events(item.data))
        if not self._pending_events:
            return None
        return self._pending_events.popleft()

    def close(self, grace_s: float) -> int | None:
        """CodexAppServerProcess.close(grace_s) で子プロセスを終わらせ、終了コードを返す。"""
        self._closed = True
        return self.process.close(grace_s)

    def _line_events(self, message: object) -> list[MainLoopSessionEvent]:
        """届いた 1 行 message を出来事にする (モジュールの docstring の「出来事」)。

        サーバーからの要求には、ここで応答を返す。最後の turn/start の応答が誤りなら [TurnResult(is_error=True)]。
        通知は notification_events で出来事にし、既に出した turnId の要約の出来事を除く。JSON の object でない行は
        出来事にしない。
        """
        if not isinstance(message, dict):
            return []
        if is_server_request(message):
            if not self.process.send(server_request_reply(message)):
                logger.warning("could not answer the request from codex: %s", message)
            return []
        if is_response(message):
            if message.get("id") == self._turn_start_id:
                error = response_error_text(message)
                if error is not None:
                    logger.warning("turn/start of codex failed: %s", error)
                    return [TurnResult(is_error=True)]
            return []
        thread_id = self._thread_id
        if not is_notification(message) or thread_id is None:
            return []
        events: list[MainLoopSessionEvent] = []
        for event in notification_events(message, thread_id):
            if isinstance(event, AutoCompaction):
                turn_id = compaction_turn_id(event)
                if turn_id is not None:
                    if turn_id in self._compacted_turns:
                        continue
                    self._compacted_turns.add(turn_id)
            events.append(event)
        return events

    def _new_request_id(self) -> int:
        """次の要求の id (1 から順に) を返す。"""
        self._next_request_id += 1
        return self._next_request_id

    def _request_and_wait(
        self, method: str, params: Mapping[str, object], deadline: float
    ) -> dict[str, object]:
        """要求を送り、その応答を deadline (monotonic の時刻) まで待って返す (起動の手順で使う)。

        待つ間に届いたサーバーからの要求には応答を返し、ほかの行は読み捨てる (まだスレッドが無いため)。送れない・
        応答が誤り・応答が来ない・標準出力が終わったら CodexStartError を出す。
        """
        request_id = self._new_request_id()
        if not self.process.send(request_message(request_id, method, params)):
            raise CodexStartError(f"could not send {method} to codex")
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise CodexStartError(f"{method} of codex did not answer in time")
            item = self.process.read(min(remaining, STARTUP_POLL_S))
            if item is None:
                continue
            if isinstance(item, StreamEnd):
                raise CodexStartError(f"codex ended before answering {method}")
            message = item.data
            if not isinstance(message, dict):
                continue
            if is_server_request(message):
                self.process.send(server_request_reply(message))
                continue
            if not is_response(message) or message.get("id") != request_id:
                continue
            error = response_error_text(message)
            if error is not None:
                raise CodexStartError(f"{method} of codex failed: {error}")
            return message

    def _wait_for_native_dir(self, deadline: float) -> Path:
        """Codex 本体のディレクトリーを、deadline まで STARTUP_POLL_S 秒ごとに探して返す。見つからなければ
        CodexStartError を出す (起動の直後は、本体のプロセスがまだいないことがあるため)。"""
        pid = self.process.pid
        if pid is None:
            raise CodexStartError("codex has not been started")
        while True:
            found = self._find_native_dir(pid)
            if found is not None:
                return found
            if self._monotonic() >= deadline:
                raise CodexStartError("could not find the native codex directory")
            time.sleep(STARTUP_POLL_S)

    def _write_turn_images(
        self, turn_number: int, images: Sequence[TurnImage]
    ) -> list[Path]:
        """images の JPEG を turn_image_path に書き、書いたパスを images の順に返す。書けなければ OSError をそのまま出す。"""
        paths = [
            turn_image_path(self.session_dir, turn_number, index)
            for index in range(1, len(images) + 1)
        ]
        if paths:
            paths[0].parent.mkdir(parents=True, exist_ok=True)
        for image, path in zip(images, paths, strict=True):
            path.write_bytes(image.jpeg)
        return paths


@dataclass(frozen=True)
class CodexMainLoopSessionFactory:
    """主ループのセッションを作るもの (main_loop_session.MainLoopSessionFactory) の Codex の実装。

    paths                  実行ディレクトリー
    instructions           スレッドの developerInstructions に渡す変わらない指示の文 (orchestrator.codex_instructions)
    model                  スレッドのモデル (--codex-model)
    effort                 ターンの effort (--orchestrator-effort)
    clock                  stream.jsonl の elapsed_s を測る時計 (秒を返す関数。主ループは RunClock.now を渡す)
    environ                子プロセスの環境変数の元 (None なら create を呼んだときの os.environ)
    start_process          子プロセスを起動する関数 (None なら subprocess.Popen)
    signal_process_group   プロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    executable             codex の実行ファイル (既定 codex_session_command.CODEX_EXECUTABLE)
    hidden_env_names       子プロセスに渡さない環境変数の名前 (既定 deep の HIDDEN_ENV_NAMES。OPENAI_API_KEY を含む)
    find_native_dir        Codex 本体のディレクトリーを探す関数 (既定 find_native_codex_dir)
    startup_timeout_s      起動を待つ上限 (実時間の秒。既定 CODEX_STARTUP_TIMEOUT_S)
    monotonic              起動の上限を測る時計 (既定 time.monotonic)
    """

    paths: RunPaths
    instructions: str
    model: str
    effort: str
    clock: Callable[[], float]
    environ: Mapping[str, str] | None = None
    start_process: StreamProcessStarter | None = None
    signal_process_group: Callable[[int, int], None] | None = None
    executable: str = CODEX_EXECUTABLE
    hidden_env_names: tuple[str, ...] = HIDDEN_ENV_NAMES
    find_native_dir: Callable[[int], Path | None] = find_native_codex_dir
    startup_timeout_s: float = CODEX_STARTUP_TIMEOUT_S
    monotonic: Callable[[], float] = time.monotonic

    def create(self, number: int, stream_path: Path) -> CodexMainLoopSession:
        """codex app-server 1 つ (モジュールの docstring の「作る」) を、起動せずに作って返す。

        number は使わない (セッションのファイルの置き場所は stream_path で決まる)。ターンの画像は stream_path の
        ディレクトリー (の絶対パス) の下に書く。instructions が空なら ValueError にする (起動の前に知らせるため)。
        """
        if not self.instructions:
            raise ValueError("instructions must not be empty")
        run_dir = self.paths.root.resolve()
        environ = self.environ if self.environ is not None else os.environ
        env = build_codex_env(environ, self.hidden_env_names)
        command = build_codex_command(
            self.executable, find_codex_skill_files_to_disable(run_dir, env)
        )
        process = CodexAppServerProcess(
            SessionLaunch(command=command, cwd=run_dir, env=env),
            stream_path,
            self.clock,
            start_process=self.start_process,
            signal_process_group=self.signal_process_group,
        )
        return CodexMainLoopSession(
            process,
            run_dir=run_dir,
            session_dir=stream_path.parent.resolve(),
            model=self.model,
            instructions=self.instructions,
            effort=self.effort,
            find_native_dir=self.find_native_dir,
            startup_timeout_s=self.startup_timeout_s,
            monotonic=self.monotonic,
        )
