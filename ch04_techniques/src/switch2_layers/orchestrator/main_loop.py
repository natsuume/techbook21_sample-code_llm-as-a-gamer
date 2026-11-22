"""Orchestrator 型の主ループ (OrchestratorMainLoop)。--mode orchestrator (監視型) と --mode delegate (委任型) で使う。

主ループのエージェントのセッション (既定は Claude Code の claude -p を stream-json の入出力で開いたままにしたもの) を
開いたままにし、前の判断の操作の列が終わって画面が落ち着き、操作の後に撮ったフレームが届いたら (actions が空なら、
すぐ) 次の画面と出来事を渡し、各ターンの最後の JSON を、操作の実行と監視 (委任型では委任) の部品
(OrchestratorController。parse_decision と apply) に渡す。
セッションの使用量がしきい値を超えたら、引き継ぎを書かせてからセッションを入れ替える。
2 つの型の違いは、組み立て (orchestrator_assembly) が渡す部品だけで決まる: 変わらない指示の文
(OrchestratorMainLoopParts.instructions)、JSON の読み方 (parse_decision)、出来事 (委任型では委任の状況を含む。
turn_input が委任の節を置く)。委任中に主ループの actions を捨てたときは、controller の main_actions_run が None に
なるため、待たずに次の入力を作る。

使う部品:
    main_loop_session        セッションとの境界の型 (セッション・セッションを作るもの・read が返す出来事)。主ループは
                             エージェントの入出力の形を知らず、この型だけを通してセッションを使う
    claude_main_loop_session その境界の Claude Code の実装 (OrchestratorMainLoopParts.session_factory が None のときに
                             使う)。起動の引数・環境変数の組み立てと、stream-json の行から出来事への直し方を持つ
    claude_session_command   Claude Code の起動の設定の型 (ClaudeSessionOptions) と、--rotate-tokens の値の確かめ
                             (autocompact_arguments)
    skill_copy               本体の操作のスキルとゲームの内容のスキルを、スキルの写し先 (既定 .claude/skills/) に写す
    orchestrator_instructions  変わらない指示 (--append-system-prompt) と引き継ぎの依頼の文
    action_settle            前のターンの操作の列が終わり、操作の後に撮ったフレームが届くのを待つ
    turn_input               入力のテキストと画像
    session_record           orchestrator/sessions.jsonl・turns.jsonl
入れ替えの間も、操作の実行と監視の部品は動き続ける。
時計 (RunClock)・最新のフレームの受け取り方・セッションを作るもの・子プロセスの起動・シグナルの送り先・環境変数・
本体の操作のスキルの写す元を差し替えられ、テストでは偽の stream-json を返すプロセスと、時間を進められる時計を使う。
"""

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from switch2_layers.error_text import describe_exception
from switch2_layers.frames.latest_frames import LatestFrame, newest_frame_t
from switch2_layers.notes.note_reader import read_note_text
from switch2_layers.orchestrator.action_settle import wait_for_settled_actions
from switch2_layers.orchestrator.claude_main_loop_session import (
    ClaudeMainLoopSessionFactory,
)
from switch2_layers.orchestrator.claude_session_command import (
    ClaudeSessionOptions,
    autocompact_arguments,
)
from switch2_layers.orchestrator.claude_stream_session import (
    SESSION_CLOSE_GRACE_S,
    StreamProcessStarter,
)
from switch2_layers.orchestrator.decision_types import Decision, DecisionError
from switch2_layers.orchestrator.live_trial import LiveTrial
from switch2_layers.orchestrator.main_loop_session import (
    AssistantText,
    AutoCompaction,
    ContextUsage,
    MainLoopSession,
    MainLoopSessionFactory,
    SessionEnded,
    TurnResult,
)
from switch2_layers.orchestrator.orchestrator_events import (
    MainActionsRun,
    OrchestratorEvents,
)
from switch2_layers.orchestrator.orchestrator_instructions import (
    HANDOFF_NOTE,
    handoff_request_text,
    orchestrator_instructions,
)
from switch2_layers.orchestrator.session_record import (
    SessionEndReason,
    SessionRecordWriter,
    TurnKind,
    TurnOutcome,
    TurnRecord,
    compact_row,
    last_json_block,
    session_end_row,
    session_start_row,
)
from switch2_layers.orchestrator.skill_copy import (
    DEFAULT_SWITCH_SKILLS_DIR,
    SkippedGameSkill,
    copy_game_skills,
    copy_switch_skills,
    prepare_skills_dir,
)
from switch2_layers.orchestrator.turn_input import (
    FRAMES_GUIDE_TEXT,
    SessionOpening,
    TurnContext,
    TurnImage,
    build_session_opening_text,
    build_turn_text,
    carry_over_events,
    list_note_files,
    select_turn_frames,
)
from switch2_layers.progress.progress_state import read_progress_state
from switch2_layers.records.run_log import (
    EVENT_ORCHESTRATOR_DECISION_ERROR,
    EVENT_ORCHESTRATOR_SESSION_END,
    EVENT_ORCHESTRATOR_SESSION_START,
    RunLog,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

DEFAULT_ROTATE_TOKENS = 120_000  # --rotate-tokens の既定値
DEFAULT_TURN_TIMEOUT_S = 120.0  # --turn-timeout の既定値 (秒)
DEFAULT_MAX_DECISION_ERRORS = 3  # --max-decision-errors の既定値
# 新しいセッションが最初のターンの result を受け取る前に終わることが、続けてこの回数になったら実行全体を止める
MAX_SESSION_FAILURES = 3
# セッションの出来事を待つ 1 回の上限と、入力を送り終わるのを待つ 1 回の上限 (秒)。
# この間隔で、停止の合図と --turn-timeout を確かめる
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
    """応答の本文から JSON を読み、検証した結果を返す関数。

    組み立て (app) は、今登録しているマクロの名前を decision_parser.parse_decision に渡す関数を渡す
    (lambda text: parse_decision(text, controller.macro_names())。--mode delegate では、さらに今の委任の状態
    controller.delegate_context() も渡す)。parse_decision をそのまま渡すと、登録の照合に空の集合を使うため、前の
    ターンまでに登録したマクロを呼ぶ JSON がすべて誤りになる。
    """

    def __call__(self, text: str) -> Decision | DecisionError:
        """text の最後の ```json のブロックを読み、Decision か DecisionError を返す。"""
        ...


class DecisionApplier(Protocol):
    """主ループが使う、操作の実行と監視の部品 (OrchestratorController) の部分。

    run と set_started は、組み立て (app) が別のスレッドで呼ぶ。主ループは apply・main_actions_run・take_events だけを
    呼ぶ。
    """

    def apply(self, decision: Decision) -> None:
        """マクロ・監視 (委任型では委任)・操作を当てる。空でない actions を受け付けたら、戻る前に main_actions_run を
        その列のものにする。委任中のため actions を捨てたら、戻る前に main_actions_run を None にする。"""
        ...

    def main_actions_run(self) -> MainActionsRun | None:
        """最後に受け付けた主ループの空でない actions の操作の列の時刻を返す (無ければ None)。出来事は取り出さない。

        主ループは、前のターンの操作の列が終わるのを待つ間、READ_POLL_S 秒ごとに呼ぶ (action_settle.wait_for_settled_actions)。
        None なら待たない。
        """
        ...

    def take_events(self) -> OrchestratorEvents:
        """前回の取り出し以降に起きたことと、今のマクロと監視の一覧を返す。"""
        ...


@dataclass(frozen=True)
class OrchestratorMainLoopSettings:
    """主ループのしきい値と上限。

    rotate_tokens         セッションの使用量がこれを超えたら、引き継ぎの依頼を送って入れ替える (--rotate-tokens)
    turn_timeout_s        1 ターン (入力を作った時刻から result まで。入力を送り終わるのを待つ時間を含み、入力を作る
                          前に前のターンの操作の列が終わるのを待つ時間は含まない) の上限の秒数 (--turn-timeout)
    max_decision_errors   JSON の誤りが続けてこの回数になったら、実行全体を止める (--max-decision-errors)
    close_grace_s         セッションを終えるときに、セッションの close に渡す秒数 (Claude Code の実装では、標準入力を
                          閉じてから SIGTERM まで・SIGTERM から SIGKILL までに待つ秒数。
                          既定 claude_stream_session.SESSION_CLOSE_GRACE_S)
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
    parse_decision         応答の本文から JSON を読む関数 (DecisionParser の渡し方。テストではフェイク)
    controller             操作の実行と監視の部品 (テストではフェイク)
    latest_frames          最新の一定間隔のフレームの画像を、新しい順にまとめて返す関数 (run では
                           frames.latest_frames.LatestFrames.snapshot。テストではフェイク)。入力を作るときに 1 回呼ぶ。
                           前のターンの操作の列を待つ間も、最新のフレームの時刻を見るために繰り返し呼ぶ
    initial_instruction    人の初期指示。無ければ None (各セッションの最初の入力に入れる)
    run_log                run.jsonl (orchestrator_session_start・orchestrator_session_end・
                           orchestrator_decision_error を書く)
    on_stop                実行全体を止める関数 (止まる理由の文字列を受け取る)
    start_process          子プロセスを起動する関数 (None なら subprocess.Popen)
    signal_process_group   プロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    environ                子プロセスの環境変数の元 (None ならセッションを始めるときの os.environ)
    switch_skills_dir      本体の操作のスキルを写す元 (既定 skill_copy.DEFAULT_SWITCH_SKILLS_DIR)
    instructions           --append-system-prompt で渡す変わらない指示の文 (既定は
                           orchestrator_instructions.orchestrator_instructions() の監視型の版。--mode delegate では
                           orchestrator_instructions.delegate_instructions() の委任型の版を渡す)
    session_factory        セッションを作るもの (main_loop_session.MainLoopSessionFactory)。None なら、セッションを
                           始めるたびに、claude・instructions・environ・start_process・signal_process_group と
                           settings.rotate_tokens から claude_main_loop_session.ClaudeMainLoopSessionFactory を作って使う
    skills_dir             本体の操作のスキルとゲームの内容のスキルの写し先。None なら paths.claude_skills_dir
                           (.claude/skills/)。主ループの Codex では実行ディレクトリーの .agents/skills/ を渡す
    frames_guide_text      入力の「## 古いフレーム」の節の本文 (既定 turn_input.FRAMES_GUIDE_TEXT。主ループの Codex では
                           codex_instructions.CODEX_FRAMES_GUIDE_TEXT を渡す)
    claude・start_process・signal_process_group・environ・instructions は、session_factory が None のときに、Claude Code の
    セッションを作るのに使う。
    """

    paths: RunPaths
    clock: RunClock
    claude: ClaudeSessionOptions
    parse_decision: DecisionParser
    controller: DecisionApplier
    latest_frames: Callable[[], Sequence[LatestFrame]]
    initial_instruction: str | None
    run_log: RunLog
    on_stop: Callable[[str], None]
    start_process: StreamProcessStarter | None = None
    signal_process_group: Callable[[int, int], None] | None = None
    environ: Mapping[str, str] | None = None
    switch_skills_dir: Path = field(default=DEFAULT_SWITCH_SKILLS_DIR)
    instructions: str = field(default_factory=orchestrator_instructions)
    session_factory: MainLoopSessionFactory | None = None
    skills_dir: Path | None = None
    frames_guide_text: str = FRAMES_GUIDE_TEXT
    live: LiveTrial | None = None


class _TurnEnd(Enum):
    """1 ターンを待ち終えた理由 (_wait_for_result の結果)。"""

    RESULT = "result"  # ターンの結果 (TurnResult) が届いた
    STOPPED = "stopped"  # 停止の合図が立った
    TIMEOUT = "timeout"  # 入力を送ってから turn_timeout_s 秒たった
    ENDED = "ended"  # セッションの出力が終わった (SessionEnded。子プロセスが終わった)


class _AppliedResult(Enum):
    """ふつうのターンの result の JSON を読んだ結果 (_apply_result の結果)。"""

    # 空でない actions の Decision を apply した (次の入力の前に、その操作の列が終わるのを待つ)
    ACTIONS = "actions"
    # 空の actions の Decision を apply したか、JSON に誤りがあった (待たずに次の入力を作る)
    NO_ACTIONS = "no_actions"
    # JSON の誤りが max_decision_errors 回続いた (on_stop を呼んだ)
    STOPPED = "stopped"


class _SendFailure(Enum):
    """入力を送れなかった理由 (_send_input の結果)。"""

    # 書き込みに失敗したか、turn_timeout_s 秒のうちに書き終わらなかった
    NOT_SENT = "not_sent"
    STOPPED = "stopped"  # 書き終わる前に停止の合図が立った


@dataclass
class _SentTurn:
    """送った入力 1 つ (result を待っているターン)。

    number        セッションの中の番号 (1 始まり)
    kind          ふつうのターンか、引き継ぎの依頼か
    t_wait_start  この入力を作る前に、前のターンの操作の列が終わるのを待ち始めた時刻 (待たなかったら t_sent と同じ)
    t_sent        入力を作った時刻 (待ちの後。--turn-timeout はここから測る)
    input_text    送ったテキスト
    images        送った画像の元になった一定間隔のフレームの、frames/interval/ の画像の実行ディレクトリーからの相対パス
    events        その入力に載せた出来事 (引き継ぎの依頼では None)。result を受け取れなければ繰り越す
    last_text     そのターンの最後の assistant の文 (まだ無ければ None)
    result_is_error  届いた result の is_error が true なら True
    """

    number: int
    kind: TurnKind
    t_wait_start: float
    t_sent: float
    input_text: str
    images: tuple[str, ...]
    events: OrchestratorEvents | None
    last_text: str | None = None
    result_is_error: bool = False


@dataclass
class _OpenSession:
    """開いているセッション 1 つ。

    number            セッションの番号 (1 始まり)
    stream            セッション (main_loop_session.MainLoopSession。既定は claude の子プロセス 1 つ)
    usage_tokens      最後に受け取った使用量 (ContextUsage の値)
    turn_count        送った入力の数 (次の入力の番号は turn_count + 1)
    completed_turns   result を受け取ったターンの数 (引き継ぎの依頼を含む。is_error が true の result は数えない)
    previous_sent_t   前の入力を送った時刻。まだ送っていなければ None
    """

    number: int
    stream: MainLoopSession
    usage_tokens: int = 0
    turn_count: int = 0
    completed_turns: int = 0
    previous_sent_t: float | None = None


class OrchestratorMainLoop:
    """Orchestrator 型の主ループ。run は 1 つのスレッド (主ループのスレッド) から 1 回だけ呼ぶ。set_started は
    ほかのスレッドから呼んでよい。

    セッション (主ループのエージェントの子プロセス 1 つ。main_loop_session.MainLoopSession) の流れ:
        1. 始める: 番号 (1 始まり) を 1 増やし、paths.orchestrator_session_dir(番号) を (親を含めて) 作る。
           skill_copy.prepare_skills_dir でスキルの写し先 (parts.skills_dir。None なら .claude/skills/) を作り、
           copy_switch_skills(switch_skills_dir) と copy_game_skills でそこに写す (セッションを起動する前に行う。
           写すスキルが無くても写し先は作る)。
           copy_game_skills の結果の skipped を、写さなかったスキルとして覚える (copy_game_skills を呼ぶたびに
           置き換え、次の入力の TurnContext.skipped_skills に渡す)。
           セッションを作るもの (parts.session_factory。None なら、claude_main_loop_session.ClaudeMainLoopSessionFactory
           を parts.paths・instructions・claude・settings.rotate_tokens・clock.now・environ・start_process・
           signal_process_group で作る) の create(番号, stream_path = paths.orchestrator_session_stream(番号)) で
           セッションを作り、start で起動する (Claude Code の起動の引数・作業ディレクトリー・環境変数は
           claude_main_loop_session に書く)。create の例外は、起動の失敗として扱わない (run の想定外の例外になる)。
           sessions.jsonl に開始の行 (stream は paths.relative(stream_path))、run.jsonl に
           orchestrator_session_start {"session"} を書く。起動できなければ (start の OSError)、開始の行は書かずに、終了の行
           (理由 exit、returncode null、error に説明) と orchestrator_session_end を書き、最初のターンを終えずに終わった
           セッションとして数える
        2. 最初の入力 (待たずに作る): events = turn_input.carry_over_events(繰り越した出来事, controller.take_events()) とし、
           turn_input.build_session_opening_text(SessionOpening(initial_instruction, list_note_files(paths.notes_dir),
           notes/handoff.md の中身 (notes.note_reader.read_note_text))) と build_turn_text(TurnContext(clock.now(),
           events, read_progress_state(paths.progress_state), 前の JSON の誤り, 写さなかったスキル,
           previous_actions=None)) を改行でつないだテキストと、select_turn_frames(latest_frames()) (入力を作るときに
           latest_frames を 1 回呼んで受け取った、同じ時点の最新の 5 枚。古い順) の画像を送る。turns.jsonl の
           t_wait_start は t_sent と同じ (待たない)。入力を作ったら、繰り越した出来事を None に戻す
           入力の送り方: セッションの send_turn_in_background(テキスト, 画像) で送り始め (Claude Code の実装では、
           書き込み用のスレッドが標準入力に書く)、送り終わるのを READ_POLL_S 秒ごとに、停止の合図と turn_timeout_s を
           確かめながら待つ (子プロセスが入力を受け取らなくなっても、主ループのスレッドを止めないため)。送れなかったとき、
           または入力を作った時刻から turn_timeout_s 秒のうちに送り終わらなかったときは、送れなかったものとして扱う
           (5 の「入力を送れなかった」)。送り終わる前に停止の合図が立ったら、送り終わるのを待たずにセッションを終えて
           (理由 close) 戻る (この入力の行は書かない)
           出来事の繰り越し: ふつうのターンの入力が result を受け取れなかったとき (送れなかった・ターンの途中で
           子プロセスが終わった・turn_timeout_s で打ち切った・is_error が true の result が届いた)、その入力に載せた
           events (繰り越した分を含む) を繰り越した出来事として覚え、次の入力 (次のセッションの最初の入力を含む) で
           carry_over_events に渡す (一度だけの出来事を失わないため)。停止の合図で打ち切ったときは、主ループが戻るため
           繰り越さない
        3. ターンを待つ: 停止の合図と turn_timeout_s を確かめてから、セッションの read(READ_POLL_S) で出来事
           (main_loop_session) を 1 つ受け取ることを繰り返す。出来事ごとに次のようにする (Claude Code の stream-json の
           行から出来事への直し方は claude_main_loop_session に書く):
             - AssistantText: その文を、そのターンの最後の文にする (入力を送るたびに None に戻す。文の無いターンの文は
               None)
             - ContextUsage: その値を、セッションの使用量にする (足し合わせない。セッションを始めるたびに 0 に戻す)
             - AutoCompaction: sessions.jsonl に要約の行 (metadata は出来事の metadata) を書く。セッションは続ける
             - TurnResult: そのターンを終える (4)
             - SessionEnded: 5 の「セッションの出力の終わり」
             - None (出来事が無かった): 何もしない
        4. result (TurnResult) を受け取ったら、copy_game_skills で写し (写さなかったスキルを覚え直す) てから:
             is_error が true の result (API の誤りなど): JSON は読まず (parse_decision も apply も
               呼ばず、JSON の誤りの回数も変えない)、そのターンを終えたものとも数えない。turns.jsonl に行
               (outcome ERROR_RESULT、t_result は受け取った時刻) を書き、ふつうのターンなら出来事を繰り越して、
               セッションを終える (理由 exit。引き継ぎの依頼のターンなら rotate)。最初のターンの result を受け取って
               いないセッションなら、最初のターンを終えずに終わったセッションとして数える
             ふつうのターン: 最後の文 (None なら空文字列) を parse_decision に渡す。
               Decision なら controller.apply(decision) を呼び、JSON の誤りが続いた回数を 0 にし、前の JSON の誤りを
               None にする。DecisionError なら回数を 1 増やし、前の JSON の誤りをそれにし、run.jsonl に
               orchestrator_decision_error {"session", "count", "errors"} を書く。
               turns.jsonl に行 (outcome は APPLIED か INVALID) を書く。
               回数が max_decision_errors に達したら、on_stop(decision_failure_reason(回数)) を呼び、セッションを終えて
               (理由 close) 戻る。
               stop が立っていれば、セッションを終えて (理由 close) 戻る。
               次の入力を作る時点: 空でない actions の Decision を apply したら、待ち始めた時刻 (clock.now()) を覚え、
               action_settle.wait_for_settled_actions(controller.main_actions_run,
               frames/latest/ の最新のフレームの時刻を返す関数 (frames.latest_frames.newest_frame_t(latest_frames())),
               clock, stop, READ_POLL_S) で待つ。待つのは、その操作の列 (マクロとコードの手順を含む) が終わり、最後に
               ボタンとスティックを離してから action_settle.ACTIONS_SETTLE_S 秒たち、さらに、離した時刻 +
               ACTIONS_SETTLE_S (ミリ秒に丸めた値) より厳密に後に撮ったフレームが latest_frames に入るまで
               (action_settle.actions_settled。押す操作の無い列では、フレームの条件を使わず、列が終わるまで)。
               監視の操作は待たない。待っている間に stop が立ったら (フレームが来ないまま待っているときも)、
               待つのをやめ、次の入力を送らずにセッションを終えて (理由 close) 戻る (次の入力の行は書かない)。
               入力に添える画像は、待ち終えた後に latest_frames を呼び直して選ぶ (待ちを通したフレームも入る)。
               actions が空の Decision と、JSON の誤り (DecisionError) のときは待たない (待ち始めた時刻は、次の入力を
               作った時刻と同じ)。引き継ぎの依頼も次の入力なので、同じく待ってから送る。
               使用量が rotate_tokens を超えていれば (ちょうどなら超えない)、orchestrator_instructions.
               handoff_request_text() のテキストだけの入力 (画像を付けず、take_events も呼ばない。出来事は次の
               セッションの最初の入力に入る) を、引き継ぎの依頼として送る。
               そうでなければ、次の入力を送る (2 と同じ材料で、セッションの開始の節は付けず、TurnContext の
               previous_actions は待った列の時刻 (待たなかったら None)。画像は 2 と同じく select_turn_frames(
               latest_frames())。出来事も 2 と同じく carry_over_events で作る)。
               どちらの入力も、t_sent (入力を作った時刻) は待ちの後の clock.now() で、turn_timeout_s はここから測る。
               turns.jsonl のその入力の行には、覚えた待ち始めた時刻を t_wait_start に書く
             引き継ぎの依頼: JSON は読まない (parse_decision も apply も呼ばず、JSON の誤りの回数も変えない)。
               turns.jsonl に行 (kind HANDOFF、outcome HANDOFF) を書き、セッションを終える (理由 rotate)
        5. ターンを終えずにセッションを終えるとき (turns.jsonl に、t_result が null の行を書く):
             - 停止の合図: そのターンの result を待たずに終える (理由 close。outcome STOPPED)。apply は呼ばない
             - 入力を送ってから turn_timeout_s 秒 (clock の差。ちょうどその秒数で超えたとする): 引き継ぎの依頼を送らずに
               終える (理由 timeout。引き継ぎの依頼のターンなら理由 rotate。outcome TIMEOUT)
             - セッションの出力の終わり (SessionEnded): 理由 exit (引き継ぎの依頼のターンなら rotate)。outcome EXITED
             - 入力を送れなかった (送る失敗か、turn_timeout_s 秒のうちに送り終わらなかった): 子プロセスが
               終わったものとして、理由 exit で終える (この入力の行は書かない)
        セッションを終える手順は、セッションの close(settings.close_grace_s) (Claude Code の実装では
        claude_stream_session.ClaudeStreamSession.close: 標準入力を閉じ、close_grace_s 秒で終わらなければ SIGTERM、
        さらに close_grace_s 秒で SIGKILL。書き込みが戻っていなければ、標準入力を閉じずに、待たずに SIGTERM を送る)。
        終えたら、sessions.jsonl に終了の行 (returncode は close の戻り値)、run.jsonl に
        orchestrator_session_end {"session", "reason", "returncode", "usage_tokens"} を書く。
    セッションの続け方:
        - 理由 rotate・timeout・exit で終えたら、すぐ次のセッションを始める (引き継ぎは notes/ にあるものだけ)。
          新しいセッションの最初の入力は、前のターンの操作の列を待たずに作る (2)
        - 最初のターンの result を受け取る前に終わったセッション (起動できない・送れない・timeout・exit・最初の
          ターンの result の is_error が true) が、続けて MAX_SESSION_FAILURES 回になったら、
          on_stop(session_failure_reason(回数)) を呼んで戻る。最初のターンの result (is_error が true のものを除く) を
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
        if settings.rotate_tokens < 1:
            raise ValueError(
                f"rotate tokens must be 1 or more: {settings.rotate_tokens}"
            )
        autocompact_arguments(settings.rotate_tokens)
        if settings.turn_timeout_s <= 0:
            raise ValueError(
                f"turn timeout must be more than 0 seconds: {settings.turn_timeout_s}"
            )
        if settings.max_decision_errors < 1:
            raise ValueError(
                f"max decision errors must be 1 or more: {settings.max_decision_errors}"
            )
        if settings.close_grace_s < 0:
            raise ValueError(
                f"close grace must not be negative: {settings.close_grace_s}"
            )
        self.parts = parts
        self.settings = settings
        self._records = SessionRecordWriter(parts.paths)
        self._started = threading.Event()
        self._session_number = 0
        # 開いているセッション (想定外の例外で抜けるときに終えるため)
        self._open: _OpenSession | None = None
        # JSON の誤りが続いた回数と、前のターンの JSON の誤り
        self._decision_errors = 0
        self._previous_error: DecisionError | None = None
        # 最初のターンの result を受け取る前に終わったセッションが続いた回数
        self._session_failures = 0
        # result を受け取れなかった入力に載せた出来事 (次の入力に繰り越す)
        self._carried_events: OrchestratorEvents | None = None
        # 最後にゲームの内容のスキルを写したときに、写さなかったスキル
        self._skipped_skills: tuple[SkippedGameSkill, ...] = ()

    def set_started(self) -> None:
        """実行を始めるときの安全の確かめが通ったことを伝える。これが呼ばれるまで、run は claude を起動しない。

        2 回目以降の呼び出しは何もしない。
        """
        self._started.set()

    def run(self, stop: threading.Event) -> None:
        """主ループの本体 (クラスの docstring の流れ)。stop が立ったら、開いているセッションを終えて (理由 close) 戻る。

        set_started が呼ばれるまで、START_POLL_S 秒ごとに stop を確かめながら待つ。その間に stop が立ったら、
        何もせずに (claude を起動せず、記録も書かずに) 戻る。
        """
        while not self._started.wait(START_POLL_S):
            if stop.is_set():
                return
        try:
            while not stop.is_set():
                if not self._run_session(stop):
                    return
        except Exception as error:
            logger.warning("orchestrator main loop failed", exc_info=True)
            if self._open is not None:
                try:
                    self._end_session(self._open, SessionEndReason.CLOSE)
                except Exception:
                    logger.warning("failed to close the claude session", exc_info=True)
            self.parts.on_stop(main_loop_failure_reason(describe_exception(error)))

    def _run_session(self, stop: threading.Event) -> bool:
        """セッションを 1 つ始めて、終わるまで動かす。主ループを続けるなら True、戻るなら False を返す。"""
        session = self._start_session()
        if session is None:
            return self._count_session_failure()
        turn = self._send_turn(session, first=True, stop=stop)
        while True:
            if turn is _SendFailure.STOPPED:
                # 書き終わる前に停止の合図が立った: 書き込みを待たずに終える
                self._end_session(session, SessionEndReason.CLOSE)
                return False
            if turn is _SendFailure.NOT_SENT:
                # 入力を送れなかった: 子プロセスが終わったものとして終える
                return self._end_unfinished_session(session, SessionEndReason.EXIT)
            live = self.parts.live
            if live is not None:
                live.update(
                    main_thinking=True, turn=f"{session.number} / {turn.number}"
                )
            try:
                end = self._wait_for_result(session, turn, stop)
            finally:
                if live is not None:
                    live.update(main_thinking=False)
            if end is _TurnEnd.STOPPED:
                self._write_turn(session, turn, None, None, TurnOutcome.STOPPED)
                self._end_session(session, SessionEndReason.CLOSE)
                return False
            if end is not _TurnEnd.RESULT:
                return self._finish_cut_turn(session, turn, end)
            self._skipped_skills = copy_game_skills(
                self.parts.paths, target_dir=self._skills_dir()
            ).skipped
            if turn.result_is_error:
                return self._finish_error_result(session, turn)
            session.completed_turns += 1
            if session.completed_turns == 1:
                self._session_failures = 0
            if turn.kind is TurnKind.HANDOFF:
                self._write_turn(
                    session, turn, self.parts.clock.now(), None, TurnOutcome.HANDOFF
                )
                self._end_session(session, SessionEndReason.ROTATE)
                return True
            applied = self._apply_result(session, turn)
            if applied is _AppliedResult.STOPPED:
                self._end_session(session, SessionEndReason.CLOSE)
                return False
            if stop.is_set():
                self._end_session(session, SessionEndReason.CLOSE)
                return False
            # 空でない actions を当てたら、その操作の列が終わって画面が落ち着くまで待ってから次の入力を作る
            t_wait_start: float | None = None
            previous_actions: MainActionsRun | None = None
            if applied is _AppliedResult.ACTIONS:
                t_wait_start = self.parts.clock.now()
                previous_actions = self._wait_for_previous_actions(stop)
                if stop.is_set():
                    self._end_session(session, SessionEndReason.CLOSE)
                    return False
            if session.usage_tokens > self.settings.rotate_tokens:
                turn = self._send_handoff_request(session, stop, t_wait_start)
            else:
                turn = self._send_turn(
                    session,
                    first=False,
                    stop=stop,
                    t_wait_start=t_wait_start,
                    previous_actions=previous_actions,
                )

    def _start_session(self) -> _OpenSession | None:
        """セッションの流れの 1 (始める)。起動できなければ終了の行を書いて None を返す。"""
        parts = self.parts
        paths = parts.paths
        self._session_number += 1
        number = self._session_number
        paths.orchestrator_session_dir(number).mkdir(parents=True, exist_ok=True)
        skills_dir = prepare_skills_dir(self._skills_dir())
        copy_switch_skills(parts.switch_skills_dir, paths, target_dir=skills_dir)
        self._skipped_skills = copy_game_skills(paths, target_dir=skills_dir).skipped
        stream_path = paths.orchestrator_session_stream(number)
        stream = self._session_factory().create(number, stream_path)
        try:
            stream.start()
        except OSError as error:
            logger.warning("could not start claude", exc_info=True)
            self._write_session_end(
                number,
                SessionEndReason.EXIT,
                usage_tokens=0,
                returncode=None,
                completed_turns=0,
                error=describe_exception(error),
            )
            return None
        session = _OpenSession(number=number, stream=stream)
        self._open = session
        self._records.write_session(
            session_start_row(parts.clock.now(), number, paths.relative(stream_path))
        )
        parts.run_log.write(EVENT_ORCHESTRATOR_SESSION_START, session=number)
        return session

    def _skills_dir(self) -> Path:
        """スキルの写し先 (parts.skills_dir。None なら paths.claude_skills_dir)。"""
        parts = self.parts
        if parts.skills_dir is not None:
            return parts.skills_dir
        return parts.paths.claude_skills_dir

    def _session_factory(self) -> MainLoopSessionFactory:
        """セッションを作るものを返す (セッションの流れの 1)。

        parts.session_factory が None でなければそれを返す。None なら、今の parts と settings の値から
        claude_main_loop_session.ClaudeMainLoopSessionFactory を作って返す。
        """
        parts = self.parts
        if parts.session_factory is not None:
            return parts.session_factory
        return ClaudeMainLoopSessionFactory(
            paths=parts.paths,
            instructions=parts.instructions,
            claude=parts.claude,
            rotate_tokens=self.settings.rotate_tokens,
            clock=parts.clock.now,
            environ=parts.environ,
            start_process=parts.start_process,
            signal_process_group=parts.signal_process_group,
        )

    def _wait_for_previous_actions(
        self, stop: threading.Event
    ) -> MainActionsRun | None:
        """前のターンの操作の列が終わって画面が落ち着くまで待ち、落ち着いた列の時刻を返す (セッションの流れの 4 の
        「次の入力を作る時点」)。stop が立ったか、主ループの列が無ければ None。"""
        parts = self.parts
        return wait_for_settled_actions(
            parts.controller.main_actions_run,
            self._newest_frame_t,
            parts.clock,
            stop,
            READ_POLL_S,
        )

    def _newest_frame_t(self) -> float | None:
        """frames/latest/ の最新のフレームの時刻 (newest_frame_t(latest_frames()))。フレームが無ければ None。"""
        return newest_frame_t(self.parts.latest_frames())

    def _send_turn(
        self,
        session: _OpenSession,
        first: bool,
        stop: threading.Event,
        t_wait_start: float | None = None,
        previous_actions: MainActionsRun | None = None,
    ) -> _SentTurn | _SendFailure:
        """ふつうのターンの入力を作って送り、送ったターンを返す。送れなければ出来事を繰り越して _SendFailure を返す。

        first なら、セッションの開始の節を付ける (セッションの流れの 2)。画像は first に関わらず
        select_turn_frames(latest_frames()) (最新の 5 枚)。t_wait_start は前のターンの操作の列を待ち始めた時刻
        (待たなかったら None で、入力を作った時刻にする)。previous_actions は待った列の時刻 (TurnContext に渡す)。
        """
        parts = self.parts
        paths = parts.paths
        events = carry_over_events(self._carried_events, parts.controller.take_events())
        self._carried_events = None
        now = parts.clock.now()
        text = build_turn_text(
            TurnContext(
                now_t=now,
                events=events,
                progress=read_progress_state(paths.progress_state),
                previous_error=self._previous_error,
                skipped_skills=self._skipped_skills,
                previous_actions=previous_actions,
                frames_guide=parts.frames_guide_text,
            )
        )
        if first:
            opening = SessionOpening(
                initial_instruction=parts.initial_instruction,
                note_files=list_note_files(paths.notes_dir),
                handoff_text=read_note_text(paths.notes_dir, HANDOFF_NOTE),
            )
            text = f"{build_session_opening_text(opening)}\n{text}"
        if parts.live is not None:
            text += "\nライブ表示用に、最後の JSON の前に、画面で確認した状況と次に行うことを一文で説明してください。\n"
        images = select_turn_frames(parts.latest_frames())
        turn = _SentTurn(
            number=session.turn_count + 1,
            kind=TurnKind.TURN,
            t_wait_start=now if t_wait_start is None else t_wait_start,
            t_sent=now,
            input_text=text,
            images=tuple(image.file for image in images),
            events=events,
        )
        failure = self._send_input(session, turn, text, images, stop)
        if failure is not None:
            self._carried_events = events
            return failure
        return turn

    def _send_handoff_request(
        self,
        session: _OpenSession,
        stop: threading.Event,
        t_wait_start: float | None = None,
    ) -> _SentTurn | _SendFailure:
        """引き継ぎの依頼 (テキストだけ) を送り、送ったターンを返す。送れなければ _SendFailure を返す。

        t_wait_start は前のターンの操作の列を待ち始めた時刻 (待たなかったら None で、入力を作った時刻にする)。
        """
        now = self.parts.clock.now()
        text = handoff_request_text()
        turn = _SentTurn(
            number=session.turn_count + 1,
            kind=TurnKind.HANDOFF,
            t_wait_start=now if t_wait_start is None else t_wait_start,
            t_sent=now,
            input_text=text,
            images=(),
            events=None,
        )
        failure = self._send_input(session, turn, text, (), stop)
        return turn if failure is None else failure

    def _send_input(
        self,
        session: _OpenSession,
        turn: _SentTurn,
        text: str,
        images: Sequence[TurnImage],
        stop: threading.Event,
    ) -> _SendFailure | None:
        """入力 (text と images) をセッションの send_turn_in_background で送り始め、送り終わるのを停止の合図と
        turn_timeout_s を確かめながら待つ。

        送れたら、セッションの送った入力の数と前の入力を送った時刻を進めて None を返す。停止の合図が立ったら
        _SendFailure.STOPPED、送れなかったか、turn.t_sent から turn_timeout_s 秒のうちに送り終わらなかったら
        _SendFailure.NOT_SENT を返す (クラスの docstring の「入力の送り方」)。
        """
        pending = session.stream.send_turn_in_background(text, images)
        clock = self.parts.clock
        while True:
            written = pending.wait(READ_POLL_S)
            if written is True:
                session.turn_count = turn.number
                session.previous_sent_t = turn.t_sent
                return None
            if written is False:
                return _SendFailure.NOT_SENT
            if stop.is_set():
                return _SendFailure.STOPPED
            if clock.now() - turn.t_sent >= self.settings.turn_timeout_s:
                logger.warning(
                    "writing to the stdin of claude did not finish in %s seconds",
                    self.settings.turn_timeout_s,
                )
                return _SendFailure.NOT_SENT

    def _wait_for_result(
        self, session: _OpenSession, turn: _SentTurn, stop: threading.Event
    ) -> _TurnEnd:
        """ターンの result を待つ (セッションの流れの 3)。届いた出来事から、最後の文・使用量・要約を読む。"""
        clock = self.parts.clock
        while True:
            if stop.is_set():
                return _TurnEnd.STOPPED
            if clock.now() - turn.t_sent >= self.settings.turn_timeout_s:
                return _TurnEnd.TIMEOUT
            event = session.stream.read(READ_POLL_S)
            if event is None:
                continue
            if isinstance(event, SessionEnded):
                return _TurnEnd.ENDED
            if isinstance(event, AssistantText):
                turn.last_text = event.text
                if self.parts.live is not None:
                    explanation = event.text.partition("```json")[0].strip()
                    if explanation:
                        self.parts.live.update(
                            message=f"ターン {session.number} / {turn.number}\n{explanation}"
                        )
            elif isinstance(event, ContextUsage):
                session.usage_tokens = event.tokens
            elif isinstance(event, AutoCompaction):
                self._records.write_session(
                    compact_row(clock.now(), session.number, event.metadata)
                )
            elif isinstance(event, TurnResult):
                turn.result_is_error = event.is_error
                return _TurnEnd.RESULT

    def _apply_result(self, session: _OpenSession, turn: _SentTurn) -> _AppliedResult:
        """ふつうのターンの result の後に、最後の文を parse_decision に渡して apply し、turns.jsonl に書く。

        JSON の誤りが続いた回数が max_decision_errors に達したら、on_stop を呼んで _AppliedResult.STOPPED を返す
        (セッションは呼び出し側が終える)。そうでなければ、空でない actions の Decision を apply したら
        _AppliedResult.ACTIONS、空の actions の Decision か JSON の誤りなら _AppliedResult.NO_ACTIONS を返す。
        """
        parts = self.parts
        decision = parts.parse_decision(turn.last_text or "")
        t_result = parts.clock.now()
        if isinstance(decision, Decision):
            parts.controller.apply(decision)
            self._decision_errors = 0
            self._previous_error = None
            self._write_turn(session, turn, t_result, None, TurnOutcome.APPLIED)
            if decision.actions:
                return _AppliedResult.ACTIONS
            return _AppliedResult.NO_ACTIONS
        self._decision_errors += 1
        self._previous_error = decision
        parts.run_log.write(
            EVENT_ORCHESTRATOR_DECISION_ERROR,
            session=session.number,
            count=self._decision_errors,
            errors=list(decision.messages),
        )
        self._write_turn(
            session, turn, t_result, decision.messages, TurnOutcome.INVALID
        )
        if self._decision_errors >= self.settings.max_decision_errors:
            parts.on_stop(decision_failure_reason(self._decision_errors))
            return _AppliedResult.STOPPED
        return _AppliedResult.NO_ACTIONS

    def _finish_cut_turn(
        self, session: _OpenSession, turn: _SentTurn, end: _TurnEnd
    ) -> bool:
        """result を受け取れずに打ち切ったターン (timeout か、子プロセスの終わり) を記録し、セッションを終える。

        ふつうのターンなら、その入力に載せた出来事を繰り越す。理由は timeout か exit (引き継ぎの依頼のターンなら
        rotate)。主ループを続けるなら True を返す。
        """
        outcome = TurnOutcome.TIMEOUT if end is _TurnEnd.TIMEOUT else TurnOutcome.EXITED
        self._write_turn(session, turn, None, None, outcome)
        if turn.kind is TurnKind.HANDOFF:
            reason = SessionEndReason.ROTATE
        else:
            self._carried_events = turn.events
            reason = (
                SessionEndReason.TIMEOUT
                if end is _TurnEnd.TIMEOUT
                else SessionEndReason.EXIT
            )
        return self._end_unfinished_session(session, reason)

    def _finish_error_result(self, session: _OpenSession, turn: _SentTurn) -> bool:
        """is_error が true の result を受け取ったターンを記録し、セッションを終える (セッションの流れの 4)。

        JSON は読まず、ターンを終えたものとも数えない。ふつうのターンなら、その入力に載せた出来事を繰り越す。
        理由は exit (引き継ぎの依頼のターンなら rotate)。主ループを続けるなら True を返す。
        """
        self._write_turn(
            session, turn, self.parts.clock.now(), None, TurnOutcome.ERROR_RESULT
        )
        if turn.kind is TurnKind.HANDOFF:
            reason = SessionEndReason.ROTATE
        else:
            self._carried_events = turn.events
            reason = SessionEndReason.EXIT
        return self._end_unfinished_session(session, reason)

    def _end_unfinished_session(
        self, session: _OpenSession, reason: SessionEndReason
    ) -> bool:
        """ターンを終えずにセッションを終え、最初のターンを終えていなければ失敗に数える。続けるなら True を返す。"""
        self._end_session(session, reason)
        if session.completed_turns == 0:
            return self._count_session_failure()
        return True

    def _count_session_failure(self) -> bool:
        """最初のターンを終えずに終わったセッションを数える。上限に達したら on_stop を呼んで False を返す。"""
        self._session_failures += 1
        if self._session_failures >= MAX_SESSION_FAILURES:
            self.parts.on_stop(session_failure_reason(self._session_failures))
            return False
        return True

    def _end_session(self, session: _OpenSession, reason: SessionEndReason) -> None:
        """セッションを close で終わらせ (子プロセスを終わらせ)、sessions.jsonl と run.jsonl に終了を書く。"""
        returncode = session.stream.close(self.settings.close_grace_s)
        self._open = None
        self._write_session_end(
            session.number,
            reason,
            usage_tokens=session.usage_tokens,
            returncode=returncode,
            completed_turns=session.completed_turns,
            error=None,
        )

    def _write_session_end(
        self,
        number: int,
        reason: SessionEndReason,
        usage_tokens: int,
        returncode: int | None,
        completed_turns: int,
        error: str | None,
    ) -> None:
        """sessions.jsonl に終了の行、run.jsonl に orchestrator_session_end を書く。"""
        self._records.write_session(
            session_end_row(
                self.parts.clock.now(),
                number,
                reason,
                usage_tokens,
                returncode,
                completed_turns,
                error,
            )
        )
        self.parts.run_log.write(
            EVENT_ORCHESTRATOR_SESSION_END,
            session=number,
            reason=reason.value,
            returncode=returncode,
            usage_tokens=usage_tokens,
        )

    def _write_turn(
        self,
        session: _OpenSession,
        turn: _SentTurn,
        t_result: float | None,
        errors: tuple[str, ...] | None,
        outcome: TurnOutcome,
    ) -> None:
        """turns.jsonl に、turn の行を書く。"""
        self._records.write_turn(
            TurnRecord(
                session=session.number,
                turn=turn.number,
                kind=turn.kind,
                t_wait_start=turn.t_wait_start,
                t_sent=turn.t_sent,
                t_result=t_result,
                input_text=turn.input_text,
                images=turn.images,
                response_json=last_json_block(turn.last_text),
                errors=errors,
                outcome=outcome,
                usage_tokens=session.usage_tokens,
            )
        )
