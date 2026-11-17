"""要素「振り返り」(reflection) のうち、run の間に批評役 (別の claude -p) を起動し、批評を notes.md に書く部分。

commands.handle_run が reflecting_with_critic で裏のスレッドを動かし、批評はエージェントの操作と並行に進む
(プレイ中のエージェントの判断は止めない)。スレッドの中では批評役を 1 つずつ起動し、前の批評が長引いて
起動の時刻を過ぎた回は起動しない (reflection.next_tick_index)。

批評役の起動 (build_critic_args):
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
    critic-<NNN>.jsonl  批評役の標準出力 (stream-json)。NNN は通し番号を 3 桁以上の 0 埋めにしたもの (例: critic-001.jsonl)
    critic-stderr.log   批評役の標準エラー出力 (追記)
通し番号は、作業ディレクトリーの中で 1 から数える (run をまたいで続く)。

時計 (clock) は壁時計 (本番は time.time)。待ち (wait) は秒数を受け取り、止める合図があれば True を返す関数で、
本番は threading.Event.wait。Event.wait の秒数は time.monotonic で計られ、この WSL2 では壁時計より約 8% 遅く進むため、
1 回の待ちは POLL_INTERVAL_S 秒までにして、そのたびに壁時計で起動の時刻を確かめる。
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from switch2_harness.agent_launcher import AgentProcess, ClaudeProcess
from switch2_harness.harness_config import HarnessConfig
from switch2_harness.instructions import ReadTemplate, read_package_template
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
class CriticSetting:
    """批評役の起動に使う、run の間は変わらない値。

    paths: 作業ディレクトリー
    game, goal: harness.json のゲーム名と目標 (批評役への指示の $game と $goal に入れる)
    base_env: 起動した人の環境変数 (agent_launcher.build_claude_env に渡す)
    start_critic: 批評役のプロセスを起動する関数
    clock: 壁時計
    read_template: instructions/ の下のテンプレートを読む関数 (批評役への指示 reflection.CRITIC_INSTRUCTION_FILE を読む)
    """

    paths: WorkspacePaths
    game: str
    goal: str
    base_env: Mapping[str, str]
    start_critic: StartCritic
    clock: Callable[[], float]
    read_template: ReadTemplate


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
    raise NotImplementedError("Phase B で実装する")


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
    raise NotImplementedError("Phase B で実装する")


def reflections_dir(paths: WorkspacePaths) -> Path:
    """記録を置くディレクトリー paths.records_dir / REFLECTIONS_DIR_NAME を返す (作らない)。"""
    return paths.records_dir / REFLECTIONS_DIR_NAME


def next_critic_index(directory: Path) -> int:
    """次の批評の通し番号を返す。directory の直下の、名前が `critic-<数字>.jsonl` のファイルの数字の最大 + 1。

    該当するファイルが無いか、directory が無ければ 1。
    """
    raise NotImplementedError("Phase B で実装する")


def critic_output_file_name(index: int) -> str:
    """批評役の標準出力のファイル名を返す。例: 1 → "critic-001.jsonl"、1234 → "critic-1234.jsonl"。"""
    raise NotImplementedError("Phase B で実装する")


def write_notes(path: Path, text: str) -> None:
    """notes.md を text に書き換える。

    path に ".tmp" を足したファイルに UTF-8 で書いてから、os.replace で path に置き換える
    (プレイ中のエージェントに、書きかけの notes.md を読ませない)。
    """
    raise NotImplementedError("Phase B で実装する")


def stop_critic(process: AgentProcess, wait: Callable[[float], bool]) -> None:
    """批評役を止める。

    プロセスグループに SIGTERM を送り (グループが無ければ何もしない)、poll() が None でなくなるまで、
    STOP_GRACE_S 秒まで POLL_INTERVAL_S 秒ずつ wait で待つ (wait の戻り値は見ない)。
    それでも終わらなければ SIGKILL を送り (グループが無ければ何もしない)、process.wait() で終わりを待つ。
    """
    raise NotImplementedError("Phase B で実装する")


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
       (UTF-8 の errors="replace" でデコードし、"\\n" だけで分ける) で行に分け、reflection.assistant_texts で発言にする
       (控えた後に書き足された行は次の回に回す)。ファイルが無ければ発言は無し
    5. notes.md (paths.root / features.NOTES_FILE_NAME。無ければ空の文字列) を読む
    6. 指示は read_template(reflection.CRITIC_INSTRUCTION_FILE) を string.Template で game と goal に置換したもの。
       reflection.build_critic_content と reflection.critic_input_line で入力の 1 行を作る
    7. index = next_critic_index(<記録のディレクトリー>) (ディレクトリーは無ければ作る)。
       start_critic(build_critic_args(), paths.root, agent_launcher.build_claude_env(base_env, paths.root), <入力の 1 行>,
       <記録のディレクトリー>/critic_output_file_name(index), <記録のディレクトリー>/CRITIC_STDERR_FILE_NAME) で起動する
    8. poll() が None でなくなるまで、POLL_INTERVAL_S 秒ずつ wait で待つ。
       wait が True を返したら (止める合図) stop_critic で止めて status を "stopped" に、
       clock() - started が CRITIC_TIMEOUT_S 以上になったら stop_critic で止めて status を "timeout" にする
    9. 終わったら、批評役の標準出力の行から reflection.critique_from_result で批評を取り出す。
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
    raise NotImplementedError("Phase B で実装する")


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
    raise NotImplementedError("Phase B で実装する")


@contextmanager
def reflecting_with_critic(
    paths: WorkspacePaths,
    config: HarnessConfig,
    *,
    clock: Callable[[], float],
    base_env: Mapping[str, str],
    start_critic: StartCritic = start_critic_process,
    read_template: ReadTemplate = read_package_template,
) -> Iterator[None]:
    """with の中にいる間、裏のスレッドで run_reflections を動かす。

    1. stop = threading.Event() と CriticSetting(paths, config.game, config.goal, base_env, start_critic, clock,
       read_template) を作る
    2. threading.Thread(target=run_reflections, args=(setting, stop.wait), daemon=True) を start する
    3. yield する
    4. with を抜けるとき (例外で抜けるときも finally で)、stop.set() で止める合図をし、
       thread.join(timeout=STOP_JOIN_TIMEOUT_S) でスレッドの終わりを待つ (批評役が動いていれば、止めるまで待つ)

    commands.handle_run は、reflecting を受け取らなかったときに、これを clock と base_env を渡して使う。
    """
    raise NotImplementedError("Phase B で実装する")
    yield
