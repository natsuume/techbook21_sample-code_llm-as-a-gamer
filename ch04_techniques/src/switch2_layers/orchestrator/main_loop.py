"""Orchestrator 型の主ループ (OrchestratorMainLoop)。

主ループの Claude Code (claude -p) を stream-json の入出力で開いたままにし、前の判断が終わるとすぐ次の画面と出来事を
渡し、各ターンの最後の JSON を、操作の実行と監視の部品 (OrchestratorController。parse_decision と apply) に渡す。
セッションの使用量がしきい値を超えたら、引き継ぎを書かせてからセッションを入れ替える。

使う部品:
    claude_session_command   起動の引数・--settings・権限のルール・環境変数
    parent_skill_names       親のディレクトリーのスキルの名前 (skillOverrides で off にする)
    skill_copy               本体の操作のスキルとゲームの内容のスキルを .claude/skills/ に写す
    orchestrator_instructions  変わらない指示 (--append-system-prompt) と引き継ぎの依頼の文
    turn_input               入力のテキストと画像
    claude_stream_session    子プロセスの起動・標準入力・標準出力・終了
    stream_messages          stream-json の行の読み方と、標準入力に送る行の形
    session_record           orchestrator/sessions.jsonl・turns.jsonl
入れ替えの間も、操作の実行と監視の部品は動き続ける (前のセッションの actions の残りは、そのまま実行される)。
時計 (RunClock)・子プロセスの起動・シグナルの送り先・環境変数・本体の操作のスキルの写す元を差し替えられ、テストでは
偽の stream-json を返すプロセスと、時間を進められる時計を使う。
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from switch2_layers.orchestrator.claude_session_command import ClaudeSessionOptions
from switch2_layers.orchestrator.claude_stream_session import (
    SESSION_CLOSE_GRACE_S,
    StreamProcessStarter,
)
from switch2_layers.orchestrator.decision_types import Decision, DecisionError
from switch2_layers.orchestrator.orchestrator_events import OrchestratorEvents
from switch2_layers.orchestrator.skill_copy import DEFAULT_SWITCH_SKILLS_DIR
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

DEFAULT_ROTATE_TOKENS = 120_000  # --rotate-tokens の既定値
DEFAULT_TURN_TIMEOUT_S = 120.0  # --turn-timeout の既定値 (秒)
DEFAULT_MAX_DECISION_ERRORS = 3  # --max-decision-errors の既定値
# 新しいセッションが最初のターンの result を受け取る前に終わることが、続けてこの回数になったら実行全体を止める
MAX_SESSION_FAILURES = 3
# 標準出力の行を待つ 1 回の上限 (秒)。この間隔で、停止の合図と --turn-timeout を確かめる
READ_POLL_S = 0.05
# set_started を待つ間に、停止の合図を確かめる間隔 (秒)
START_POLL_S = 0.05


def decision_failure_reason(count: int) -> str:
    """JSON の誤りが count 回続いて止まるときの理由 `orchestrator decision invalid <count> times in a row` を返す。"""
    return f"orchestrator decision invalid {count} times in a row"


def session_failure_reason(count: int) -> str:
    """セッションが最初のターンを終えずに終わることが count 回続いて止まるときの理由
    `orchestrator session failed <count> times in a row` を返す。"""
    return f"orchestrator session failed {count} times in a row"


def main_loop_failure_reason(description: str) -> str:
    """主ループが想定外の例外で止まるときの理由 `orchestrator main loop failed: <説明>` を返す。"""
    return f"orchestrator main loop failed: {description}"


class DecisionParser(Protocol):
    """応答の本文から JSON を読み、検証した結果を返す関数 (操作の実行と監視の部品の parse_decision)。"""

    def __call__(self, text: str) -> Decision | DecisionError:
        """text の最後の ```json のブロックを読み、Decision か DecisionError を返す。"""
        ...


class DecisionApplier(Protocol):
    """主ループが使う、操作の実行と監視の部品 (OrchestratorController) の部分。

    run と set_started は、組み立て (app) が別のスレッドで呼ぶ。主ループは apply と take_events だけを呼ぶ。
    """

    def apply(self, decision: Decision) -> None:
        """マクロ・監視・操作を当てる。"""
        ...

    def take_events(self) -> OrchestratorEvents:
        """前回の取り出し以降に起きたことと、今のマクロと監視の一覧を返す。"""
        ...


@dataclass(frozen=True)
class OrchestratorMainLoopSettings:
    """主ループのしきい値と上限。

    rotate_tokens         セッションの使用量がこれを超えたら、引き継ぎの依頼を送って入れ替える (--rotate-tokens)
    turn_timeout_s        1 ターン (入力を送ってから result まで) の上限の秒数 (--turn-timeout)
    max_decision_errors   JSON の誤りが続けてこの回数になったら、実行全体を止める (--max-decision-errors)
    close_grace_s         セッションを終えるときに、標準入力を閉じてから SIGTERM まで・SIGTERM から SIGKILL までに
                          待つ秒数 (既定 claude_stream_session.SESSION_CLOSE_GRACE_S)
    """

    rotate_tokens: int = DEFAULT_ROTATE_TOKENS
    turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S
    max_decision_errors: int = DEFAULT_MAX_DECISION_ERRORS
    close_grace_s: float = SESSION_CLOSE_GRACE_S


@dataclass(frozen=True)
class OrchestratorMainLoopParts:
    """主ループが使う部品。

    paths                  実行ディレクトリー
    clock                  実行の経過秒の時計。記録の時刻と --turn-timeout は、すべてこの時計で測る
    claude                 Claude Code の起動の設定 (claude のパス・モデル・effort・隠す環境変数)
    parse_decision         応答の本文から JSON を読む関数 (テストではフェイク)
    controller             操作の実行と監視の部品 (テストではフェイク)
    initial_instruction    人の初期指示。無ければ None (各セッションの最初の入力に入れる)
    run_log                run.jsonl (orchestrator_session_start・orchestrator_session_end・
                           orchestrator_decision_error を書く)
    on_stop                実行全体を止める関数 (止まる理由の文字列を受け取る)
    start_process          子プロセスを起動する関数 (None なら subprocess.Popen)
    signal_process_group   プロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    environ                子プロセスの環境変数の元 (None ならセッションを始めるときの os.environ)
    switch_skills_dir      本体の操作のスキルを写す元 (既定 skill_copy.DEFAULT_SWITCH_SKILLS_DIR)
    """

    paths: RunPaths
    clock: RunClock
    claude: ClaudeSessionOptions
    parse_decision: DecisionParser
    controller: DecisionApplier
    initial_instruction: str | None
    run_log: RunLog
    on_stop: Callable[[str], None]
    start_process: StreamProcessStarter | None = None
    signal_process_group: Callable[[int, int], None] | None = None
    environ: Mapping[str, str] | None = None
    switch_skills_dir: Path = field(default=DEFAULT_SWITCH_SKILLS_DIR)


class OrchestratorMainLoop:
    """Orchestrator 型の主ループ。run は 1 つのスレッド (主ループのスレッド) から 1 回だけ呼ぶ。set_started は
    ほかのスレッドから呼んでよい。

    セッション (1 つの claude の子プロセス) の流れ:
        1. 始める: 番号 (1 始まり) を 1 増やし、paths.orchestrator_session_dir(番号) を (親を含めて) 作る。
           skill_copy.prepare_claude_skills_dir で .claude/skills/ を作り、copy_switch_skills(switch_skills_dir) と
           copy_game_skills で写す (claude を起動する前に行う。写すスキルが無くても .claude/skills/ は作る)。
           copy_game_skills の結果の skipped を、写さなかったスキルとして覚える (copy_game_skills を呼ぶたびに
           置き換え、次の入力の TurnContext.skipped_skills に渡す)。
           run_dir = paths.root のシンボリックリンクを解決した絶対パスとして、
           command = claude_session_command.build_session_command(RunPaths(run_dir),
           orchestrator_instructions.orchestrator_instructions(), parent_skill_names.find_parent_skill_names(run_dir),
           claude, settings.rotate_tokens)、env = build_session_env(environ, claude.hidden_env_names)、cwd = run_dir で、
           claude_stream_session.ClaudeStreamSession(stream_path=paths.orchestrator_session_stream(番号)) を起動する。
           sessions.jsonl に開始の行 (stream は paths.relative(stream_path))、run.jsonl に
           orchestrator_session_start {"session"} を書く。起動できなければ (OSError)、開始の行は書かずに、終了の行
           (理由 exit、returncode null、error に説明) と orchestrator_session_end を書き、最初のターンを終えずに終わった
           セッションとして数える
        2. 最初の入力: events = turn_input.carry_over_events(繰り越した出来事, controller.take_events()) とし、
           turn_input.build_session_opening_text(SessionOpening(initial_instruction, list_note_files(paths.notes_dir),
           notes/handoff.md の中身 (notes.note_reader.read_note_text))) と build_turn_text(TurnContext(clock.now(),
           events, read_progress_state(paths.progress_state), 前の JSON の誤り, 写さなかったスキル)) を改行でつないだ
           テキストと、select_turn_frames(frames.jsonl の記録, None, now) (最新の 1 枚) の画像を、build_turn_content の
           内容にして送る。入力を作ったら、繰り越した出来事を None に戻す
           出来事の繰り越し: ふつうのターンの入力が result を受け取れなかったとき (送れなかった・ターンの途中で
           子プロセスが終わった・turn_timeout_s で打ち切った)、その入力に載せた events (繰り越した分を含む) を
           繰り越した出来事として覚え、次の入力 (次のセッションの最初の入力を含む) で carry_over_events に渡す
           (一度だけの出来事を失わないため)。停止の合図で打ち切ったときは、主ループが戻るため繰り越さない
        3. ターンを待つ: READ_POLL_S 秒ごとに ClaudeStreamSession.read で行を受け取る。ターンの最後の文と使用量は、
           次のように読む (stream_messages):
             - assistant の行: deep.claude_stream_log.assistant_text が None でなければ、そのターンの最後の文にする
               (入力を送るたびに None に戻す。文の無いターンの文は None)。assistant_usage_tokens が None でなければ、
               セッションの使用量にする (足し合わせない。セッションを始めるたびに 0 に戻す)
             - compact_boundary の行: sessions.jsonl に要約の行を書く。セッションは続ける
             - result の行: そのターンを終える (4)
             - ほかの行 (system/init・thinking_tokens・rate_limit_event など): 何もしない (stream.jsonl には書かれる)
        4. result を受け取ったら、copy_game_skills で写し (写さなかったスキルを覚え直す) てから:
             ふつうのターン: 最後の文 (None なら空文字列) を parse_decision に渡す。
               Decision なら controller.apply(decision) を呼び、JSON の誤りが続いた回数を 0 にし、前の JSON の誤りを
               None にする。DecisionError なら回数を 1 増やし、前の JSON の誤りをそれにし、run.jsonl に
               orchestrator_decision_error {"session", "count", "errors"} を書く。
               turns.jsonl に行 (outcome は APPLIED か INVALID) を書く。
               回数が max_decision_errors に達したら、on_stop(decision_failure_reason(回数)) を呼び、セッションを終えて
               (理由 close) 戻る。
               stop が立っていれば、セッションを終えて (理由 close) 戻る。
               使用量が rotate_tokens を超えていれば (ちょうどなら超えない)、orchestrator_instructions.
               handoff_request_text() のテキストだけの入力 (画像を付けず、take_events も呼ばない。出来事は次の
               セッションの最初の入力に入る) を、引き継ぎの依頼として送る。
               そうでなければ、すぐ次の入力を送る (2 と同じ材料で、セッションの開始の節は付けず、画像は
               select_turn_frames(記録, 前の入力を送った時刻, now)。出来事も 2 と同じく carry_over_events で作る)
             引き継ぎの依頼: JSON は読まない (parse_decision も apply も呼ばず、JSON の誤りの回数も変えない)。
               turns.jsonl に行 (kind HANDOFF、outcome HANDOFF) を書き、セッションを終える (理由 rotate)
        5. ターンを終えずにセッションを終えるとき (turns.jsonl に、t_result が null の行を書く):
             - 停止の合図: そのターンの result を待たずに終える (理由 close。outcome STOPPED)。apply は呼ばない
             - 入力を送ってから turn_timeout_s 秒 (clock の差。ちょうどその秒数で超えたとする): 引き継ぎの依頼を送らずに
               終える (理由 timeout。引き継ぎの依頼のターンなら理由 rotate。outcome TIMEOUT)
             - 標準出力の終わり (StreamEnd): 理由 exit (引き継ぎの依頼のターンなら rotate)。outcome EXITED
             - 入力を送れなかった (send が False): 子プロセスが終わったものとして、理由 exit で終える (この入力の行は
               書かない)
        セッションを終える手順は、ClaudeStreamSession.close(settings.close_grace_s) (標準入力を閉じ、close_grace_s 秒で
        終わらなければ SIGTERM、さらに close_grace_s 秒で SIGKILL)。終えたら、sessions.jsonl に終了の行、run.jsonl に
        orchestrator_session_end {"session", "reason", "returncode", "usage_tokens"} を書く。
    セッションの続け方:
        - 理由 rotate・timeout・exit で終えたら、すぐ次のセッションを始める (引き継ぎは notes/ にあるものだけ)
        - 最初のターンの result を受け取る前に終わったセッション (起動できない・timeout・exit) が、続けて
          MAX_SESSION_FAILURES 回になったら、on_stop(session_failure_reason(回数)) を呼んで戻る。最初のターンの result を
          受け取ったセッションで、回数を 0 に戻す。理由 close で終えたセッションは数えない
        - JSON の誤りの回数は、セッションを入れ替えても続けて数える
    run が想定外の例外 (Exception) で抜けるときは、外に出さずに、開いているセッションを終えて (理由 close)、
    on_stop(main_loop_failure_reason(error_text.describe_exception(例外))) を呼んで戻る。
    """

    def __init__(
        self, parts: OrchestratorMainLoopParts, settings: OrchestratorMainLoopSettings
    ) -> None:
        """セッションを開いていない状態で作る。

        settings の値が次のときは ValueError にする: rotate_tokens が 1 未満か、
        claude_session_command.autocompact_arguments が受け付けない値、turn_timeout_s が 0 以下、
        max_decision_errors が 1 未満、close_grace_s が負。
        """
        raise NotImplementedError

    def set_started(self) -> None:
        """実行を始めるときの安全の確かめが通ったことを伝える。これが呼ばれるまで、run は claude を起動しない。

        2 回目以降の呼び出しは何もしない。
        """
        raise NotImplementedError

    def run(self, stop: threading.Event) -> None:
        """主ループの本体 (クラスの docstring の流れ)。stop が立ったら、開いているセッションを終えて (理由 close) 戻る。

        set_started が呼ばれるまで、START_POLL_S 秒ごとに stop を確かめながら待つ。その間に stop が立ったら、
        何もせずに (claude を起動せず、記録も書かずに) 戻る。
        """
        raise NotImplementedError
