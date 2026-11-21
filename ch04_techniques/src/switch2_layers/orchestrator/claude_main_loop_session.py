"""主ループのセッションの Claude Code の実装 (ClaudeMainLoopSession と ClaudeMainLoopSessionFactory)。

orchestrator.main_loop_session の境界 (MainLoopSession・MainLoopSessionFactory) を、claude -p を stream-json の入出力で
開いたままにする子プロセス (orchestrator.claude_stream_session.ClaudeStreamSession) で満たす。
    - 作る (ClaudeMainLoopSessionFactory.create): run_dir = paths.root のシンボリックリンクを解決した絶対パスとして、
      command = claude_session_command.build_session_command(RunPaths(run_dir), instructions,
      parent_skill_names.find_parent_skill_names(run_dir), claude, rotate_tokens)、
      env = claude_session_command.build_session_env(environ, claude.hidden_env_names)、cwd = run_dir の
      ClaudeStreamSession を作り、ClaudeMainLoopSession で包む (起動はしない)。どれも create を呼ぶたびに組み立てる
    - 起動と終了 (start・close): ClaudeStreamSession.start と close をそのまま呼ぶ
    - 入力 (send_turn_in_background): turn_input.build_turn_content(text, images) の内容のブロック (テキストと、画像ごとの
      時刻のラベルと base64 の JPEG) を、ClaudeStreamSession.send_in_background で 1 行の user のメッセージとして送る
    - 出来事 (read): ClaudeStreamSession.read で受け取った標準出力の行を、stream_line_events で出来事にする
      (標準出力の終わり StreamEnd は SessionEnded にする)
stream-json の行と出来事の対応 (stream_line_events。行の読み方は orchestrator.stream_messages):
    assistant の行             文があれば AssistantText (deep.claude_stream_log.assistant_text)、message.usage があれば
                               ContextUsage (stream_messages.assistant_usage_tokens。この順)
    system/compact_boundary    AutoCompaction (metadata は stream_messages.compact_metadata)
    result の行                TurnResult (is_error は行の is_error が true か)
    ほかの行 (system/init・thinking_tokens・rate_limit_event など) と、JSON として読めない行は、出来事にしない
    (stream.jsonl には ClaudeStreamSession が書く)
"""

import os
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.deep.claude_stream_log import assistant_text, is_result_message
from switch2_layers.orchestrator.claude_session_command import (
    ClaudeSessionOptions,
    build_session_command,
    build_session_env,
)
from switch2_layers.orchestrator.claude_stream_session import (
    ClaudeStreamSession,
    SessionLaunch,
    StreamEnd,
    StreamProcessStarter,
)
from switch2_layers.orchestrator.main_loop_session import (
    AssistantText,
    AutoCompaction,
    ContextUsage,
    MainLoopSessionEvent,
    SessionEnded,
    TurnResult,
)
from switch2_layers.orchestrator.parent_skill_names import find_parent_skill_names
from switch2_layers.orchestrator.pending_send import PendingSend
from switch2_layers.orchestrator.stream_messages import (
    assistant_usage_tokens,
    compact_metadata,
    is_compact_boundary,
)
from switch2_layers.orchestrator.turn_input import TurnImage, build_turn_content
from switch2_layers.run_directory import RunPaths


def stream_line_events(data: object) -> tuple[MainLoopSessionEvent, ...]:
    """stream-json の 1 行 data (行を JSON として読んだ値。読めない行は None) から作った出来事を、次の順に返す (I/O なし)。

        1. deep.claude_stream_log.assistant_text(data) が None でなければ AssistantText(その文)
        2. stream_messages.assistant_usage_tokens(data) が None でなければ ContextUsage(その値)
        3. stream_messages.is_compact_boundary(data) なら AutoCompaction(stream_messages.compact_metadata(data))
        4. deep.claude_stream_log.is_result_message(data) なら TurnResult(is_error = data の is_error が true か)
    どれにも当たらない行は空のタプル。
    """
    events: list[MainLoopSessionEvent] = []
    text = assistant_text(data)
    if text is not None:
        events.append(AssistantText(text=text))
    tokens = assistant_usage_tokens(data)
    if tokens is not None:
        events.append(ContextUsage(tokens=tokens))
    if is_compact_boundary(data):
        events.append(AutoCompaction(metadata=compact_metadata(data)))
    if is_result_message(data):
        events.append(
            TurnResult(is_error=isinstance(data, dict) and data.get("is_error") is True)
        )
    return tuple(events)


class ClaudeMainLoopSession:
    """主ループのセッション (main_loop_session.MainLoopSession) の Claude Code の実装。claude の子プロセス 1 つを包む。

    stream   包む claude の子プロセス (ClaudeStreamSession。ClaudeMainLoopSessionFactory.create が作る)
    start・send_turn_in_background・read・close は 1 つのスレッド (主ループ) から呼ぶ。
    """

    def __init__(self, stream: ClaudeStreamSession) -> None:
        """包む子プロセスを持つ。子プロセスはまだ起動しない。"""
        self.stream = stream
        # 読んだ 1 行から作り、まだ返していない出来事 (古い順)
        self._pending_events: deque[MainLoopSessionEvent] = deque()

    def start(self) -> None:
        """ClaudeStreamSession.start で子プロセスを起動する。起動できなければ OSError をそのまま出す。"""
        self.stream.start()

    def send_turn_in_background(
        self, text: str, images: Sequence[TurnImage]
    ) -> PendingSend:
        """turn_input.build_turn_content(text, images) を ClaudeStreamSession.send_in_background で送り始め、PendingSend を
        返す (書き終わるのを待たない)。"""
        return self.stream.send_in_background(build_turn_content(text, images))

    def read(self, timeout_s: float) -> MainLoopSessionEvent | None:
        """次の出来事を返す。

        前に読んだ行の出来事が残っていれば、待たずにその先頭を返す。残っていなければ ClaudeStreamSession.read(timeout_s)
        で次を受け取り、None なら None、StreamEnd なら SessionEnded() を返す。行 (StreamLine) なら stream_line_events で
        出来事にして残し、その先頭を返す。出来事にならない行なら、待ち直さずに None を返す。
        """
        if self._pending_events:
            return self._pending_events.popleft()
        item = self.stream.read(timeout_s)
        if item is None:
            return None
        if isinstance(item, StreamEnd):
            return SessionEnded()
        self._pending_events.extend(stream_line_events(item.data))
        if not self._pending_events:
            return None
        return self._pending_events.popleft()

    def close(self, grace_s: float) -> int | None:
        """ClaudeStreamSession.close(grace_s) で子プロセスを終わらせ、終了コードを返す。"""
        return self.stream.close(grace_s)


@dataclass(frozen=True)
class ClaudeMainLoopSessionFactory:
    """主ループのセッションを作るもの (main_loop_session.MainLoopSessionFactory) の Claude Code の実装。

    paths                  実行ディレクトリー
    instructions           --append-system-prompt で渡す変わらない指示の文 (orchestrator.orchestrator_instructions)
    claude                 Claude Code の起動の設定 (claude のパス・モデル・effort・隠す環境変数)
    rotate_tokens          セッションを入れ替える使用量 (--rotate-tokens。claude_session_command.autocompact_arguments に渡す)
    clock                  stream.jsonl の elapsed_s を測る時計 (秒を返す関数。主ループは RunClock.now を渡す)
    environ                子プロセスの環境変数の元 (None なら create を呼んだときの os.environ)
    start_process          子プロセスを起動する関数 (None なら subprocess.Popen)
    signal_process_group   プロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    """

    paths: RunPaths
    instructions: str
    claude: ClaudeSessionOptions
    rotate_tokens: int
    clock: Callable[[], float]
    environ: Mapping[str, str] | None = None
    start_process: StreamProcessStarter | None = None
    signal_process_group: Callable[[int, int], None] | None = None

    def create(self, number: int, stream_path: Path) -> ClaudeMainLoopSession:
        """claude の子プロセス 1 つ (モジュールの docstring の「作る」) を、起動せずに作って返す。

        number は使わない (セッションのファイルの置き場所は stream_path で決まる)。build_session_command の
        ValueError (指示の文が空か - で始まる、rotate_tokens を autocompact_arguments が受け付けない、実行ディレクトリーの
        パスを権限のルールに埋められない) は、そのまま出す。
        """
        run_dir = self.paths.root.resolve()
        command = build_session_command(
            RunPaths(run_dir),
            self.instructions,
            find_parent_skill_names(run_dir),
            self.claude,
            self.rotate_tokens,
        )
        environ = self.environ if self.environ is not None else os.environ
        env = build_session_env(environ, self.claude.hidden_env_names)
        stream = ClaudeStreamSession(
            SessionLaunch(command=command, cwd=run_dir, env=env),
            stream_path,
            self.clock,
            start_process=self.start_process,
            signal_process_group=self.signal_process_group,
        )
        return ClaudeMainLoopSession(stream)
