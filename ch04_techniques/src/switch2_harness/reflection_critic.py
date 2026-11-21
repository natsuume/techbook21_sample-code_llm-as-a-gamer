"""要素「振り返り」(reflection) のうち、run の間に批評役 (別のエージェント) を起動し、批評を notes.md に書く部分。

commands.handle_run が reflecting_with_critic で裏のスレッドを動かし、批評はエージェントの操作と並行に進む
(プレイ中のエージェントの判断は止めない)。スレッドの中では批評役を 1 つずつ起動し、前の批評が長引いて
起動の時刻を過ぎた回は起動しない (reflection.next_tick_index)。

批評役は、プレイヤーと同じ種類のエージェントにする。種類ごとの起動引数・入力・環境変数・出力の読み方は
CriticAgent で受け取り、既定は Claude Code の実装 (CLAUDE_CRITIC = ClaudeCritic())、Codex の実装は codex_critic.CodexCritic。

Claude Code の批評役の起動 (build_critic_args):
    プレイ中のエージェントと同じく、起動した人の設定・CLAUDE.md・MCP・Skill を読ませない。ツールは渡さない (--tools "")。
    入力は --input-format stream-json で、撮影の画像を含む 1 つのユーザーのメッセージ (reflection.critic_input_line) を
    標準入力に書いて閉じる。会話はディスクに残さない (--no-session-persistence)。
    build_critic_args の引数で、1280x720 の PNG の撮影 8 枚を base64 で入れた入力 (1 行で約 10.9 MB) を渡すと、
    ツールなしで画像を読み、約 11 秒で批評を返すことを、Claude Code 2.1.326 で 2026-11-17 に確かめた。

記録は records/reflections/ に置く:
    reflections.jsonl   回ごとに 1 行の JSON (json.dumps(..., ensure_ascii=False))
        起動した回: {"index": <通し番号>, "tick": <run の中の回の番号>, "time": <起動の時刻>,
                     "window_start": <区間の始まりの時刻>, "frames": [<渡した撮影の frame>...],
                     "agent_log_from": <発言を読み始めたバイト位置>, "agent_log_to": <読み終えたバイト位置>,
                     "status": "written" | "failed" | "timeout" | "stopped", "critique": <批評の本文。無ければ null>,
                     "elapsed_s": <起動から終わるまでの秒数>}
        飛ばした回: {"tick": <回の番号>, "time": <その回の起動の時刻>, "status": "skipped"}
    critic-<NNN>.jsonl  批評役の標準出力 (Claude Code は stream-json、Codex は codex exec --json)。NNN は通し番号を 3 桁以上の 0 埋めにしたもの (例: critic-001.jsonl)
    critic-stderr.log   批評役の標準エラー出力 (追記)
通し番号は、作業ディレクトリーの中で 1 から数える (run をまたいで続く)。

時計 (clock) は壁時計 (本番は time.time)。待ち (wait) は秒数を受け取り、止める合図があれば True を返す関数で、
本番は threading.Event.wait。Event.wait の秒数は time.monotonic で計られ、この WSL2 では壁時計より約 8% 遅く進むため、
1 回の待ちは POLL_INTERVAL_S 秒までにして、そのたびに壁時計で起動の時刻を確かめる。
"""

import base64
import errno
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from string import Template
from typing import Literal, Protocol

from switch2_harness.agent_launcher import (
    CLAUDE_COMMAND,
    CLAUDE_SETTINGS_JSON,
    AgentProcess,
    ClaudeProcess,
    agent_log_size,
    build_claude_env,
)
from switch2_harness.event_log import append_event, read_events
from switch2_harness.features import NOTES_FILE_NAME
from switch2_harness.harness_config import HarnessConfig
from switch2_harness.instructions import ReadTemplate, read_package_template
from switch2_harness.reflection import (
    CRITIC_INSTRUCTION_FILE,
    REFLECTION_INTERVAL_S,
    CriticFrame,
    assistant_texts,
    build_critic_content,
    build_reflection_event,
    critic_input_line,
    critique_from_result,
    format_action_lines,
    next_tick_index,
    replace_reflection_section,
    section_body,
    select_frames,
    window_actions,
)
from switch2_harness.workspace import WorkspacePaths

# 批評役のモデルと effort (ユーザーが決めた値。プレイ中のエージェントと同じ)
CRITIC_MODEL = "claude-opus-5-5"
CRITIC_EFFORT = "medium"

# 批評役の起動から、終わらなければ止めるまでの秒数 (起動の間隔より短くする)
CRITIC_TIMEOUT_S = 240.0

# 1 回の待ちの上限の秒数。待ちのたびに、壁時計と止める合図を確かめる
POLL_INTERVAL_S = 1.0

# 批評役に SIGTERM を送ってから SIGKILL を送るまで待つ秒数
STOP_GRACE_S = 5.0

# 振り返りを止めるときに、裏のスレッドの終わりを待つ上限の秒数 (批評役を止める時間を含む)
STOP_JOIN_TIMEOUT_S = 15.0

# records/ の下の、記録を置くディレクトリーの名前
REFLECTIONS_DIR_NAME = "reflections"

# 回ごとの記録のファイル名
REFLECTIONS_INDEX_FILE_NAME = "reflections.jsonl"

# 批評役の標準エラー出力のファイル名
CRITIC_STDERR_FILE_NAME = "critic-stderr.log"

# 批評役の標準出力のファイル名に合う正規表現。グループ 1 が通し番号
_CRITIC_OUTPUT_PATTERN = re.compile(r"critic-([0-9]+)\.jsonl")

# 批評役を止めたときの、回の結果 ("stopped": 止める合図で止めた、"timeout": 時間切れで止めた)
StopStatus = Literal["stopped", "timeout"]

# 作業ディレクトリーと設定を受け取り、振り返りを始めて終えるコンテキストマネージャーを返す関数の型。commands.handle_run が受け取る
Reflecting = Callable[[WorkspacePaths, HarnessConfig], AbstractContextManager[object]]


class StartCritic(Protocol):
    """批評役のプロセスを起動する関数の型。本番は start_critic_process、テストでは fake を渡す。"""

    def __call__(
        self,
        args: list[str],
        cwd: Path,
        env: dict[str, str],
        input_text: str,
        stdout_path: Path,
        stderr_path: Path,
    ) -> AgentProcess: ...


@dataclass(frozen=True)
class CriticRequest:
    """批評役に渡すもの。

    instructions: 批評役への指示 (reflection.CRITIC_INSTRUCTION_FILE を置換したもの)
    frames: 渡す撮影 (base64 を含む。Claude Code の批評役が使う)
    frame_paths: frames と同じ順の、撮影のファイルの絶対パス (Codex の批評役が -i で添付する)
    action_lines: reflection.format_action_lines の行
    statements: プレイヤーの発言
    notes_text: notes.md の本文
    """

    instructions: str
    frames: Sequence[CriticFrame]
    frame_paths: Sequence[Path]
    action_lines: Sequence[str]
    statements: Sequence[str]
    notes_text: str


@dataclass(frozen=True)
class CriticCommand:
    """批評役の起動引数 args と、標準入力に書いて閉じる input_text。"""

    args: list[str]
    input_text: str


class CriticAgent(Protocol):
    """批評役のエージェントの種類ごとの、起動と出力の読み方。本番は ClaudeCritic と codex_critic.CodexCritic。"""

    def statements(self, agent_log_lines: Sequence[str]) -> list[str]:
        """プレイヤーの出力 (agent.jsonl) の行から、プレイヤーの発言を順に返す。"""
        ...

    def build_command(self, request: CriticRequest) -> CriticCommand:
        """request を渡す批評役の起動引数と標準入力を返す。"""
        ...

    def build_env(
        self, base_env: Mapping[str, str], workspace_root: Path
    ) -> dict[str, str]:
        """批評役の環境変数を返す。"""
        ...

    def critique(self, output_lines: Sequence[str]) -> str | None:
        """批評役の出力の行から批評の本文を返す。取り出せなければ None。"""
        ...


@dataclass(frozen=True)
class ClaudeCritic:
    """CriticAgent の Claude Code の実装。

    statements は reflection.assistant_texts、build_command は
    CriticCommand(build_critic_args(), reflection.critic_input_line(reflection.build_critic_content(
    instructions, frames, action_lines, statements, notes_text)))、build_env は agent_launcher.build_claude_env、
    critique は reflection.critique_from_result。
    """

    def statements(self, agent_log_lines: Sequence[str]) -> list[str]:
        return assistant_texts(agent_log_lines)

    def build_command(self, request: CriticRequest) -> CriticCommand:
        content = build_critic_content(
            request.instructions,
            request.frames,
            request.action_lines,
            request.statements,
            request.notes_text,
        )
        return CriticCommand(build_critic_args(), critic_input_line(content))

    def build_env(
        self, base_env: Mapping[str, str], workspace_root: Path
    ) -> dict[str, str]:
        return build_claude_env(base_env, workspace_root)

    def critique(self, output_lines: Sequence[str]) -> str | None:
        return critique_from_result(output_lines)


# 批評役の既定 (Claude Code)
CLAUDE_CRITIC = ClaudeCritic()


@dataclass(frozen=True)
class CriticSetting:
    """批評役の起動に使う、run の間は変わらない値。

    paths: 作業ディレクトリー
    game, goal: harness.json のゲーム名と目標 (批評役への指示の $game と $goal に入れる)
    base_env: 起動した人の環境変数 (critic.build_env に渡す)
    start_critic: 批評役のプロセスを起動する関数
    clock: 壁時計
    read_template: instructions/ の下のテンプレートを読む関数 (批評役への指示 reflection.CRITIC_INSTRUCTION_FILE を読む)
    critic: 批評役のエージェントの種類の実装 (既定は Claude Code)
    """

    paths: WorkspacePaths
    game: str
    goal: str
    base_env: Mapping[str, str]
    start_critic: StartCritic
    clock: Callable[[], float]
    read_template: ReadTemplate
    critic: CriticAgent = CLAUDE_CRITIC


def build_critic_args() -> list[str]:
    """批評役の claude -p の起動引数を返す。順序は次のとおり:

        claude -p
          --input-format stream-json --output-format stream-json --verbose
          --permission-mode dontAsk
          --tools ""
          --disallowedTools Skill
          --disable-slash-commands
          --strict-mcp-config
          --restricted
          --settings <agent_launcher.CLAUDE_SETTINGS_JSON>
          --no-session-persistence
          --model <CRITIC_MODEL>
          --effort <CRITIC_EFFORT>
    `--tools ""` は、引数 "--tools" と空の文字列 "" の 2 つの要素にする。
    """
    return [
        CLAUDE_COMMAND,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
        "--disallowedTools",
        "Skill",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--restricted",
        "--settings",
        CLAUDE_SETTINGS_JSON,
        "--no-session-persistence",
        "--model",
        CRITIC_MODEL,
        "--effort",
        CRITIC_EFFORT,
    ]


def start_critic_process(
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    input_text: str,
    stdout_path: Path,
    stderr_path: Path,
) -> ClaudeProcess:
    """StartCritic の本番の実装。stdout_path を "wb"、stderr_path を "ab" で開き、subprocess.Popen で起動する。

    標準入力は subprocess.PIPE にし、起動した直後に input_text を UTF-8 で書いて閉じる。
    新しいセッション (start_new_session=True) で起動する (端末の Ctrl-C が直接届かないようにし、
    止めるときはプロセスグループにシグナルを送る)。開いたファイルは起動後に閉じる (子プロセスは複製を持つ)。
    """
    with stdout_path.open("wb") as stdout_file, stderr_path.open("ab") as stderr_file:
        popen = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
    # stdin=subprocess.PIPE で起動したので、popen.stdin は None でない
    if popen.stdin is not None:
        popen.stdin.write(input_text.encode("utf-8"))
        popen.stdin.close()
    return ClaudeProcess(popen)


def reflections_dir(paths: WorkspacePaths) -> Path:
    """記録を置くディレクトリー paths.records_dir / REFLECTIONS_DIR_NAME を返す (作らない)。"""
    return paths.records_dir / REFLECTIONS_DIR_NAME


def next_critic_index(directory: Path) -> int:
    """次の批評の通し番号を返す。directory の直下の、名前が `critic-<数字>.jsonl` のファイルの数字の最大 + 1。

    該当するファイルが無いか、directory が無ければ 1。
    """
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1))
        for entry in directory.iterdir()
        if entry.is_file() and (match := _CRITIC_OUTPUT_PATTERN.fullmatch(entry.name))
    ]
    return max(numbers, default=0) + 1


def critic_output_file_name(index: int) -> str:
    """批評役の標準出力のファイル名を返す。例: 1 → "critic-001.jsonl"、1234 → "critic-1234.jsonl"。"""
    return f"critic-{index:03d}.jsonl"


def write_notes(path: Path, text: str) -> None:
    """notes.md を text に書き換える。

    path と同じディレクトリーに、名前が `<path の名前>.<ランダムな文字列>.tmp` の一時ファイルを tempfile.mkstemp で
    新しく作り (既にあるファイルやシンボリックリンクを開かない)、UTF-8 で書いてから、os.replace で path に置き換える
    (プレイ中のエージェントに、書きかけの notes.md を読ませない)。書けなければ一時ファイルを消して、例外をそのまま出す。
    Codex のエージェントは作業ディレクトリーの直下を書けるため、決まった名前の一時ファイルを使うと、エージェントが
    その名前で作業ディレクトリーの外を指すシンボリックリンクを置き、サンドボックスの外で動くハーネスにリンク先を
    書かせられる。それを防ぐため、名前を決めずに新しく作る。
    """
    fd, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        # mkstemp は 0600 で作るため、置き換える前の notes.md と同じ権限にする (無いか普通のファイルでなければ 0644)
        os.fchmod(fd, _notes_mode(path))
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _signal_group_if_present(process: AgentProcess, sig: int) -> None:
    """process のプロセスグループに sig を送る。グループが無ければ (ProcessLookupError) 何もしない。"""
    try:
        process.signal_group(sig)
    except ProcessLookupError:
        pass


def stop_critic(process: AgentProcess, wait: Callable[[float], bool]) -> None:
    """批評役を止める。

    プロセスグループに SIGTERM を送り (グループが無ければ何もしない)、poll() が None でなくなるまで、
    STOP_GRACE_S 秒まで POLL_INTERVAL_S 秒ずつ wait で待つ (wait の戻り値は見ない)。
    それでも終わらなければ SIGKILL を送り (グループが無ければ何もしない)、process.wait() で終わりを待つ。
    """
    _signal_group_if_present(process, signal.SIGTERM)
    waited_s = 0.0
    while process.poll() is None and waited_s < STOP_GRACE_S:
        wait(POLL_INTERVAL_S)
        waited_s += POLL_INTERVAL_S
    if process.poll() is None:
        _signal_group_if_present(process, signal.SIGKILL)
        process.wait()


def _notes_mode(path: Path) -> int:
    """path (notes.md) が普通のファイルならその権限のビットを、無いか普通のファイルでなければ 0o644 を返す。

    シンボリックリンクはたどらない (os.lstat)。
    """
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return 0o644
    if stat.S_ISREG(status.st_mode):
        return stat.S_IMODE(status.st_mode)
    return 0o644


def _read_notes(path: Path) -> str:
    """notes.md を UTF-8 で読んで返す。無ければ空の文字列。

    シンボリックリンク (O_NOFOLLOW で開けず ELOOP になる) と、普通のファイルでないもの (FIFO など。O_NONBLOCK で
    開いて待たずに確かめる) は読まず、空の文字列を返す。ほかの OSError (読む権限が無いなど) はそのまま送出する。Codex のエージェントは作業ディレクトリーの直下を書けるため、
    notes.md を作業ディレクトリーの外を指すリンクに置き換えて、サンドボックスの外で動くハーネスにリンク先を
    読ませる (批評役に渡させ、notes.md に書き戻させる) ことができてしまうため。
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return ""
    except OSError as e:
        # シンボリックリンク (ELOOP) はリンク先を読まない。ほかの誤り (読む権限が無いなど) はそのまま出す
        if e.errno == errno.ELOOP:
            return ""
        raise
    with os.fdopen(fd, "rb") as notes_file:
        if not stat.S_ISREG(os.fstat(notes_file.fileno()).st_mode):
            return ""
        return notes_file.read().decode("utf-8")


def _read_log_lines(path: Path, start: int, end: int | None = None) -> list[str]:
    """path の start バイト目から end バイト目の手前まで (end が None なら最後まで) を行に分けて返す。

    agent_launcher.read_agent_log_lines と同じく、UTF-8 の errors="replace" でデコードし、"\\n" だけで分け、
    最後の "\\n" の後の空の要素は含めない。path が無ければ空のリスト。
    """
    try:
        with path.open("rb") as log_file:
            log_file.seek(start)
            data = (
                log_file.read() if end is None else log_file.read(max(0, end - start))
            )
    except FileNotFoundError:
        return []
    lines = data.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _append_index_line(directory: Path, entry: Mapping[str, object]) -> None:
    """entry を 1 行の JSON (json.dumps(..., ensure_ascii=False)) にして、directory の reflections.jsonl に追記する。

    directory が無ければ作る。
    """
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / REFLECTIONS_INDEX_FILE_NAME).open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _critic_frames(
    paths: WorkspacePaths,
    looks: list[tuple[float, str]],
    window_start: float,
) -> tuple[list[CriticFrame], list[str]]:
    """選んだ撮影 looks ((時刻, frame) の組) を読んで、批評役に渡す撮影と、その frame のリストを返す。

    paths.root / frame を読んで base64 にする。ファイルが無い撮影は、どちらのリストにも入れない。
    """
    frames: list[CriticFrame] = []
    passed: list[str] = []
    for time, frame in looks:
        try:
            data = (paths.root / frame).read_bytes()
        except FileNotFoundError:
            continue
        frames.append(
            CriticFrame(
                file_name=PurePosixPath(frame).name,
                offset_s=time - window_start,
                png_base64=base64.b64encode(data).decode("ascii"),
            )
        )
        passed.append(frame)
    return frames, passed


def _wait_for_critic(
    process: AgentProcess,
    started: float,
    clock: Callable[[], float],
    wait: Callable[[float], bool],
) -> StopStatus | None:
    """批評役が終わるまで、POLL_INTERVAL_S 秒ずつ wait で待つ。

    自分で終わったら None を返す。clock() - started が CRITIC_TIMEOUT_S 以上になったら stop_critic で止めて "timeout"、
    wait が True (止める合図) を返したら stop_critic で止めて "stopped" を返す。
    """
    while process.poll() is None:
        if clock() - started >= CRITIC_TIMEOUT_S:
            stop_critic(process, wait)
            return "timeout"
        if wait(POLL_INTERVAL_S):
            stop_critic(process, wait)
            return "stopped"
    return None


def reflect_once(
    setting: CriticSetting,
    tick: int,
    run_start: float,
    agent_log_offset: int,
    wait: Callable[[float], bool],
) -> int:
    """tick 回目の批評役を起動し、終わったら批評を notes.md に書く。次の回の発言を読み始めるバイト位置を返す。

    1. started = clock()。区間は window_start = started - reflection.REFLECTION_INTERVAL_S から started まで
    2. events = event_log.read_events(paths.events_file) のうち、command が "look" で成功し frame があり、
       window_start <= time <= started のものの (time, frame) から、reflection.select_frames で撮影を選ぶ。
       選んだ撮影は paths.root / frame を読んで base64 にし、reflection.CriticFrame(<frame のファイル名>,
       <time - window_start>, <base64>) にする。ファイルが無い撮影は渡さない
    3. 行動は reflection.format_action_lines(reflection.window_actions(events, window_start, started), window_start)
    4. agent_log_to = agent_launcher.agent_log_size(paths.agent_log_file) を控え、paths.agent_log_file の
       agent_log_offset バイト目から agent_log_to バイト目の手前までを、agent_launcher.read_agent_log_lines と同じ方法
       (UTF-8 の errors="replace" でデコードし、"\\n" だけで分ける) で行に分け、critic.statements で発言にする
       (Claude Code は reflection.assistant_texts。控えた後に書き足された行は次の回に回す)。ファイルが無ければ発言は無し
    5. notes.md (paths.root / features.NOTES_FILE_NAME。無ければ空の文字列) を読む
    6. 指示は read_template(reflection.CRITIC_INSTRUCTION_FILE) を string.Template で game と goal に置換したもの。
       critic.build_command(CriticRequest(...)) で起動引数と標準入力を作る (Claude Code は build_critic_args と、
       reflection.build_critic_content と reflection.critic_input_line で作る入力の 1 行)
    7. index = next_critic_index(<記録のディレクトリー>) (ディレクトリーは無ければ作る)。
       start_critic(<起動引数>, paths.root, critic.build_env(base_env, paths.root), <標準入力>,
       <記録のディレクトリー>/critic_output_file_name(index), <記録のディレクトリー>/CRITIC_STDERR_FILE_NAME) で起動する
       (Claude Code の build_env は agent_launcher.build_claude_env)
    8. poll() が None でなくなるまで、POLL_INTERVAL_S 秒ずつ wait で待つ。
       wait が True を返したら (止める合図) stop_critic で止めて status を "stopped" に、
       clock() - started が CRITIC_TIMEOUT_S 以上になったら stop_critic で止めて status を "timeout" にする
    9. 終わったら、批評役の標準出力の行から critic.critique (Claude Code は reflection.critique_from_result) で
       批評を取り出す。
       取り出せれば、notes.md をもう一度読み (無ければ空の文字列)、
       reflection.replace_reflection_section(<notes.md>, reflection.section_body(<tick * 5>, <批評>)) を write_notes で書き、
       event_log.append_event(paths.events_file, reflection.build_reflection_event(clock(), index, <clock() - started>))
       を足して status を "written" にする。取り出せなければ status を "failed" にし、notes.md と events.jsonl は変えない。
       "stopped" と "timeout" のときは取り出さない
    10. reflections.jsonl に、起動した回の 1 行を追記する (モジュールの説明の形。frames は渡した撮影の frame
        (例: "frames/000002.png")。ファイルが無くて渡さなかった撮影は含めない)。time は 1. の started
    11. agent_log_to を返す
    section_body の分は tick * int(REFLECTION_INTERVAL_S / 60) (5 分ごとなら tick * 5)。
    """
    paths = setting.paths
    clock = setting.clock
    started = clock()
    window_start = started - REFLECTION_INTERVAL_S
    events = read_events(paths.events_file)
    looks = [
        (event.time, event.frame)
        for event in events
        if event.command == "look"
        and event.ok
        and event.frame is not None
        and window_start <= event.time <= started
    ]
    frames, passed = _critic_frames(paths, select_frames(looks), window_start)
    action_lines = format_action_lines(
        window_actions(events, window_start, started), window_start
    )
    agent_log_to = agent_log_size(paths.agent_log_file)
    critic = setting.critic
    statements = critic.statements(
        _read_log_lines(paths.agent_log_file, agent_log_offset, agent_log_to)
    )
    notes_path = paths.root / NOTES_FILE_NAME
    instructions = Template(setting.read_template(CRITIC_INSTRUCTION_FILE)).substitute(
        game=setting.game, goal=setting.goal
    )
    command = critic.build_command(
        CriticRequest(
            instructions=instructions,
            frames=frames,
            frame_paths=[paths.root / frame for frame in passed],
            action_lines=action_lines,
            statements=statements,
            notes_text=_read_notes(notes_path),
        )
    )
    directory = reflections_dir(paths)
    directory.mkdir(parents=True, exist_ok=True)
    index = next_critic_index(directory)
    output_path = directory / critic_output_file_name(index)
    process = setting.start_critic(
        command.args,
        paths.root,
        critic.build_env(setting.base_env, paths.root),
        command.input_text,
        output_path,
        directory / CRITIC_STDERR_FILE_NAME,
    )
    status: str | None = _wait_for_critic(process, started, clock, wait)
    critique: str | None = None
    if status is None:
        critique = critic.critique(_read_log_lines(output_path, 0))
        if critique is None:
            status = "failed"
        else:
            minutes = tick * int(REFLECTION_INTERVAL_S / 60)
            body = section_body(minutes, critique)
            write_notes(
                notes_path, replace_reflection_section(_read_notes(notes_path), body)
            )
            written_at = clock()
            append_event(
                paths.events_file,
                build_reflection_event(written_at, index, written_at - started),
            )
            status = "written"
    _append_index_line(
        directory,
        {
            "index": index,
            "tick": tick,
            "time": started,
            "window_start": window_start,
            "frames": passed,
            "agent_log_from": agent_log_offset,
            "agent_log_to": agent_log_to,
            "status": status,
            "critique": critique,
            "elapsed_s": clock() - started,
        },
    )
    return agent_log_to


def run_reflections(setting: CriticSetting, wait: Callable[[float], bool]) -> None:
    """止める合図があるまで、run の開始から REFLECTION_INTERVAL_S 秒ごとに reflect_once を呼ぶ。

    reflecting_with_critic が裏のスレッドで動かす。
    1. run_start = clock()、offset = agent_launcher.agent_log_size(paths.agent_log_file)、last = 0
    2. 次を繰り返す:
       a. tick = reflection.next_tick_index(run_start, clock(), last)。last + 1 から tick - 1 までの回は、
          reflections.jsonl に飛ばした回の行を、回の番号の順に追記する
       b. clock() が run_start + tick * REFLECTION_INTERVAL_S 以上になるまで、
          min(POLL_INTERVAL_S, <起動の時刻 - clock()>) 秒ずつ wait で待つ。wait が True を返したら終える
       c. offset = reflect_once(setting, tick, run_start, offset, wait)、last = tick
       d. wait(0.0) が True なら (止める合図がすでにあれば) 終える
    """
    paths = setting.paths
    clock = setting.clock
    run_start = clock()
    offset = agent_log_size(paths.agent_log_file)
    last = 0
    while True:
        tick = next_tick_index(run_start, clock(), last)
        for skipped in range(last + 1, tick):
            _append_index_line(
                reflections_dir(paths),
                {
                    "tick": skipped,
                    "time": run_start + skipped * REFLECTION_INTERVAL_S,
                    "status": "skipped",
                },
            )
        launch_at = run_start + tick * REFLECTION_INTERVAL_S
        while (now := clock()) < launch_at:
            if wait(min(POLL_INTERVAL_S, launch_at - now)):
                return
        offset = reflect_once(setting, tick, run_start, offset, wait)
        last = tick
        if wait(0.0):
            return


@contextmanager
def reflecting_with_critic(
    paths: WorkspacePaths,
    config: HarnessConfig,
    *,
    clock: Callable[[], float],
    base_env: Mapping[str, str],
    start_critic: StartCritic = start_critic_process,
    read_template: ReadTemplate = read_package_template,
    critic: CriticAgent = CLAUDE_CRITIC,
) -> Iterator[None]:
    """with の中にいる間、裏のスレッドで run_reflections を動かす。

    1. stop = threading.Event() と CriticSetting(paths, config.game, config.goal, base_env, start_critic, clock,
       read_template, critic) を作る (critic は批評役の種類。commands.handle_run がプレイヤーと同じ種類を渡す)
    2. threading.Thread(target=run_reflections, args=(setting, stop.wait), daemon=True) を start する
    3. yield する
    4. with を抜けるとき (例外で抜けるときも finally で)、stop.set() で止める合図をし、
       thread.join(timeout=STOP_JOIN_TIMEOUT_S) でスレッドの終わりを待つ (批評役が動いていれば、止めるまで待つ)

    commands.handle_run は、reflecting を受け取らなかったときに、これを clock と base_env を渡して使う。
    """
    stop = threading.Event()
    setting = CriticSetting(
        paths=paths,
        game=config.game,
        goal=config.goal,
        base_env=base_env,
        start_critic=start_critic,
        clock=clock,
        read_template=read_template,
        critic=critic,
    )
    thread = threading.Thread(
        target=run_reflections, args=(setting, stop.wait), daemon=True
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=STOP_JOIN_TIMEOUT_S)
