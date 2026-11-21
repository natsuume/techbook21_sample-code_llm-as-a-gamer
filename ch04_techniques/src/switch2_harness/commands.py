"""switch2-harness の各サブコマンド (init / look / press / status / finish / report / run) の処理本体。

デーモンへの送信 (send)・時計 (clock / now)・待機 (sleep)・プロセスの起動 (start_process)・
テンプレートの読み込み (read_template)・画面の変化の判定 (frames_changed)・画面の記録 (screen_recording)・
最後の新しい画面の時刻の読み込み (last_new_screen_time)・Codex 用の CODEX_HOME の用意 (prepare_codex) は引数で受け取る。
各処理は表示する行と終了コードを CommandResult で返し、表示は cli が行う。

表示の規則:
    - デーモンへの送信の結果 (send が返した 1 行。接続できないときの error 行を含む) と ok 行は標準出力に出す
    - ハーネス自身の検証で出す error 行 (init の検証、press の拒否、run の --minutes など) は標準エラー出力に出す
    - 終了コードは成功 0、失敗 1

look / press / status / finish は、成否によらず records/events.jsonl にイベントを 1 つずつ (finish は 2 つ) 記録する。
複数フレームの要素で続けて撮る look も、look のイベントは 1 つにする。
ただし停滞の知らせを出した look は、look のイベントの後に command が stagnation-alert のイベントも記録し、
振り返りの知らせを出した look は、さらにその後に command が reflection-notice のイベントも記録する
(どちらも time は look のイベントと同じ、elapsed_s は 0.0)。
イベントの time は送信の直前の clock()、elapsed_s は送信の直後の clock() との差、ok は応答が error で始まらないこと。
複数フレームの look では、time は 1 回目の送信の直前の clock()、elapsed_s は最後に送った撮影の応答の直後の clock() との差
(成功なら 6 回の撮影と、その間の待ちの全体) にする。
これらの送信には待ち時間の上限を付けない (send に timeout_s を渡さない。長い tap はデーモンの応答まで時間がかかるため)。
"""

import contextlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from pathlib import Path

from switch2_harness.action_history import FramesChanged, build_history_lines
from switch2_harness.agent_kind import (
    CODEX_AGENT,
    DEFAULT_AGENT,
    AgentKind,
    default_model,
    instructions_file_name,
)
from switch2_harness.agent_launcher import (
    CLAUDE_PLAYER,
    LaunchOptions,
    PlayerAgent,
    StartProcess,
    read_agent_log_lines,
    run_agent_until,
    validate_max_budget_usd,
    validate_minutes,
)
from switch2_harness.codex_critic import CodexCritic
from switch2_harness.codex_home import (
    DEFAULT_HARNESS_EXECUTABLE,
    CodexSetupError,
    PrepareCodex,
    prepare_codex_home,
)
from switch2_harness.codex_instructions import codex_read_template
from switch2_harness.codex_player import CodexPlayer
from switch2_harness.daemon_client import SendCommand
from switch2_harness.event_log import CommandEvent, append_event, read_events
from switch2_harness.features import (
    FEATURES,
    REFLECTION_FEATURE,
    STAGNATION_ALERT_FEATURE,
    Feature,
    agent_commands,
    agent_file_appends,
    agent_writable_files,
    validate_feature_names,
)
from switch2_harness.frame_files import reserve_next_frame_path
from switch2_harness.harness_config import (
    HarnessConfig,
    format_created_at,
    read_config,
)
from switch2_harness.instructions import (
    ReadTemplate,
    read_package_template,
    render_instructions,
)
from switch2_harness.multi_frame import (
    MULTI_FRAMES_DIR_NAME,
    multi_frame_line,
    shoot_burst,
    shot_paths,
    tile_path_for,
    write_tile,
)
from switch2_harness.press_args import press_args_error, split_press_args
from switch2_harness.reflection import NOTICE_LINE, build_notice_event, notice_pending
from switch2_harness.reflection_critic import (
    CLAUDE_CRITIC,
    CriticAgent,
    Reflecting,
    StartCritic,
    reflecting_with_critic,
    start_critic_process,
)
from switch2_harness.screen_change import load_sample
from switch2_harness.screen_recorder import ScreenRecording, recording_screens
from switch2_harness.session_report import (
    format_summary,
    summarize_session,
    summary_to_dict,
)
from switch2_harness.signal_guard import RunTerminated, interrupts_as_exceptions
from switch2_harness.stagnation_alert import (
    alert_line,
    alert_seconds,
    build_alert_event,
)
from switch2_harness.workspace import (
    WorkspaceNotEmptyError,
    WorkspacePaths,
    create_workspace,
    workspace_paths,
)

# SIGTERM・SIGHUP で run が終わったときに出す error 行
RUN_TERMINATED_MESSAGE = "error run terminated by signal"

# Ctrl-C (SIGINT・KeyboardInterrupt) で run が終わったときに出す error 行
RUN_INTERRUPTED_MESSAGE = "error run interrupted"

# codex の作業ディレクトリーで run --max-budget-usd を指定したときのメッセージ (codex exec は費用を出さないため)
CODEX_BUDGET_MESSAGE = (
    "--max-budget-usd cannot be used with agent codex "
    "(codex exec does not report the cost)"
)


@dataclass(frozen=True)
class CommandResult:
    """サブコマンドの結果。

    exit_code: 終了コード (成功 0、失敗 1)
    stdout_lines: 標準出力に 1 行ずつ出す行 (改行を含まない)
    stderr_lines: 標準エラー出力に 1 行ずつ出す行 (改行を含まない)
    """

    exit_code: int
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()


def is_error_reply(reply: str) -> bool:
    """応答が error で始まれば True を返す。"""
    return reply.startswith("error")


def release_not_confirmed_message(reply: str) -> str:
    """run の後片付けの up ALL を確かめられなかったときの error 行を返す。

    例: `error up ALL after the run was not confirmed: error cannot connect to daemon: ...`。
    """
    return f"error up ALL after the run was not confirmed: {reply}"


def _error_result(message: object) -> CommandResult:
    """`error <message>` を stderr に出す、終了コード 1 の結果を返す。"""
    return CommandResult(exit_code=1, stderr_lines=(f"error {message}",))


def _send_and_record(
    paths: WorkspacePaths,
    command: str,
    event_args: tuple[str, ...],
    request: list[str],
    *,
    send: SendCommand,
    clock: Callable[[], float],
    frame_on_success: str | None = None,
) -> CommandEvent:
    """request を送り、イベントを記録して、記録したイベントを返す (応答は .reply)。

    frame は応答が error でないときだけ frame_on_success にする。
    """
    started = clock()
    reply = send(request)
    elapsed_s = clock() - started
    ok = not is_error_reply(reply)
    event = CommandEvent(
        time=started,
        command=command,
        args=event_args,
        reply=reply,
        ok=ok,
        elapsed_s=elapsed_s,
        frame=frame_on_success if ok else None,
    )
    append_event(paths.events_file, event)
    return event


def _stagnation_alert_lines(
    paths: WorkspacePaths,
    look: CommandEvent,
    frame: str,
    last_new_screen_time: Callable[[], float | None] | None,
) -> tuple[str, ...]:
    """成功した look のイベント look について、停滞の知らせを出すなら記録して、知らせの行を返す。

    last_new_screen_time が None、それが None を返す、
    または stagnation_alert.alert_seconds(look.time, <最後の新しい画面の時刻>) が None なら、何も記録せず () を返す。
    そうでなければ stagnation_alert.build_alert_event(look.time, frame, <秒数>) を paths.events_file に追記し、
    (stagnation_alert.alert_line(<秒数>),) を返す。
    """
    if last_new_screen_time is None:
        return ()
    last_new_time = last_new_screen_time()
    if last_new_time is None:
        return ()
    seconds = alert_seconds(look.time, last_new_time)
    if seconds is None:
        return ()
    append_event(paths.events_file, build_alert_event(look.time, frame, seconds))
    return (alert_line(seconds),)


def _reflection_notice_lines(
    paths: WorkspacePaths, look: CommandEvent, frame: str
) -> tuple[str, ...]:
    """成功した look のイベント look について、振り返りの知らせを出すなら記録して、知らせの行を返す。

    event_log.read_events(paths.events_file) を読み、reflection.notice_pending が False なら、何も記録せず () を返す。
    True なら reflection.build_notice_event(look.time, frame) を paths.events_file に追記し、
    (reflection.NOTICE_LINE,) を返す。
    """
    if not notice_pending(read_events(paths.events_file)):
        return ()
    append_event(paths.events_file, build_notice_event(look.time, frame))
    return (NOTICE_LINE,)


def _shoot_multi_frame_and_record(
    paths: WorkspacePaths,
    frame_path: Path,
    frame: str,
    *,
    send: SendCommand,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[CommandEvent, tuple[str, ...]]:
    """複数フレームの look で 6 回撮って記録し、(look のイベント, 応答の後に出す行) を返す。

    handle_look の「複数フレーム」の手順 2〜6 を行う。frame_path は確保した 6 回目の保存先、frame はその
    作業ディレクトリーからの相対パス。
    成功なら、並べた画像を書いて 1〜5 回目の一時ファイルを消し、出す行は (multi_frame_line(<並べた画像>),)。
    失敗 (応答が error) なら、1〜5 回目の一時ファイルを消し、出す行は ()。
    frame_path は消さない (失敗のときは、呼び出し側が一時ファイルの後に消す)。
    """
    multi_frames_dir = paths.root / MULTI_FRAMES_DIR_NAME
    multi_frames_dir.mkdir(exist_ok=True)
    burst_paths = shot_paths(frame_path, multi_frames_dir)
    started = clock()
    reply = shoot_burst(burst_paths, send=send, clock=clock, sleep=sleep)
    elapsed_s = clock() - started
    ok = not is_error_reply(reply)
    event = CommandEvent(
        time=started,
        command="look",
        args=(),
        reply=reply,
        ok=ok,
        elapsed_s=elapsed_s,
        frame=frame if ok else None,
    )
    append_event(paths.events_file, event)
    lines: tuple[str, ...] = ()
    if ok:
        tile_path = tile_path_for(frame_path, multi_frames_dir)
        write_tile(burst_paths, tile_path)
        lines = (multi_frame_line(tile_path),)
    # 6 回目 (frame_path) を除いた 1〜5 回目の一時ファイルを消す
    for temporary_path in burst_paths[:-1]:
        temporary_path.unlink(missing_ok=True)
    return event, lines


def _reply_result(*replies: str) -> CommandResult:
    """応答を stdout に出し、どれかが error なら終了コード 1 にした結果を返す。"""
    failed = any(is_error_reply(reply) for reply in replies)
    return CommandResult(exit_code=1 if failed else 0, stdout_lines=replies)


def handle_init(
    directory: str | Path,
    game: str,
    goal: str,
    enable: Sequence[str],
    *,
    now: Callable[[], datetime],
    read_template: ReadTemplate,
    available: tuple[Feature, ...] = FEATURES,
    agent: AgentKind = DEFAULT_AGENT,
    harness_executable: Path = DEFAULT_HARNESS_EXECUTABLE,
) -> CommandResult:
    """init: 作業ディレクトリーを作る。agent はプレイするエージェントの種類 (既定は claude)。

    harness_executable は、codex の指示ファイルに書く、ハーネスの実行ファイルの絶対パス (claude では使わない)。

    1. enable を features.validate_feature_names(enable, available) で検証する。
       ValueError なら `error <メッセージ>` を stderr に出し、終了コード 1 (ファイルシステムには触れない)
    2. HarnessConfig(game, goal, 検証した名前, format_created_at(now()), agent) を作る
    3. instructions.render_instructions(config, read_template, available) で指示ファイルの本文を作る。
       指示ファイルは作業ディレクトリーの直下の agent_kind.instructions_file_name(agent) (claude は CLAUDE.md)。
       agent が codex なら、read_template の代わりに codex_instructions.codex_read_template(read_template,
       harness_executable) を渡し、Claude Code のツールの名前の箇所とサブコマンドの書き方 (`uv run switch2-harness` を
       harness_executable に) だけを置き換えた本文 (AGENTS.md) にする
    4. features.agent_writable_files(検証した名前, available) の各ファイルについて read_template(<template_file>) を読み
       (置換はしない)、{<name>: <本文>} を作る。続けて、features.agent_file_appends(検証した名前, available) の順に、
       各 FileAppend について read_template(<template_file>) を読み (置換はしない)、辞書の <name> の本文を
       `<本文の末尾の改行を除いたもの>\\n\\n<足す内容の末尾の改行を除いたもの>\\n` にする
       (例: "# メモ\\n" に "## 振り返り\\n" を足すと "# メモ\\n\\n## 振り返り\\n")
    5. workspace.create_workspace で作る。4. の辞書を initial_files に渡す。
       WorkspaceNotEmptyError なら `error <メッセージ>` を stderr に出し、終了コード 1
    6. `ok <作業ディレクトリーの絶対パス> features=<名前を "," でつないだもの。無ければ none>` を stdout に出し、終了コード 0。
       agent が claude でなければ、行の末尾に ` agent=<agent>` を足す
    """
    try:
        names = validate_feature_names(enable, available)
    except ValueError as e:
        return _error_result(e)
    config = HarnessConfig(
        game=game,
        goal=goal,
        features=names,
        created_at=format_created_at(now()),
        agent=agent,
    )
    root_paths = workspace_paths(directory)
    paths = replace(
        root_paths,
        instructions_file=root_paths.root / instructions_file_name(agent),
    )
    instructions_reader = (
        codex_read_template(read_template, harness_executable)
        if agent == CODEX_AGENT
        else read_template
    )
    instructions_text = render_instructions(config, instructions_reader, available)
    # 要素のファイルの最初の内容と、その末尾に足す内容は、テンプレートを置換せずにそのまま書く
    initial_files = {
        writable_file.name: read_template(writable_file.template_file)
        for writable_file in agent_writable_files(names, available)
    }
    for file_append in agent_file_appends(names, available):
        text = initial_files[file_append.name].rstrip("\n")
        addition = read_template(file_append.template_file).rstrip("\n")
        initial_files[file_append.name] = f"{text}\n\n{addition}\n"
    try:
        create_workspace(paths, config, instructions_text, initial_files)
    except WorkspaceNotEmptyError as e:
        return _error_result(e)
    features_text = ",".join(names) or "none"
    agent_text = "" if agent == DEFAULT_AGENT else f" agent={agent}"
    return CommandResult(
        exit_code=0,
        stdout_lines=(f"ok {paths.root} features={features_text}{agent_text}",),
    )


def handle_look(
    paths: WorkspacePaths,
    *,
    send: SendCommand,
    clock: Callable[[], float],
    frames_changed: FramesChanged | None = None,
    last_new_screen_time: Callable[[], float | None] | None = None,
    reflection_notice: bool = False,
    multi_frame: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> CommandResult:
    """look: 画面を撮影して frames/ に保存する。

    multi_frame が False なら、下のとおり 1 回だけ撮る (送信は 1 回で、sleep は呼ばず、multi-frames/ も作らない)。
    multi_frame が True の場合は、後ろの「複数フレーム」の段落のとおり続けて撮る。

    frame_files.reserve_next_frame_path(paths.frames_dir) で次の連番のパスを確保し (空のファイルを排他的に作る。
    Claude Code はツールを並行して呼ぶことがあり、重なった look が同じパスに上書きしないようにするため)、
    その絶対パスで send(["shot", <絶対パス>]) を送り、応答を stdout に出す。
    応答が error で始まれば終了コード 1 にし、確保した空のファイルを消す (FileNotFoundError は無視する) ので、
    frames/ に空の PNG を残さない。そうでなければ終了コード 0。
    イベント: command="look"、args=()、frame=<作業ディレクトリーからの相対パス (例: "frames/000001.png")>。
    応答が error なら frame は None。

    last_new_screen_time は停滞の知らせの要素 (stagnation-alert) が有効なときに渡す、最後の新しい画面の時刻
    (epoch 秒。分からなければ None) を返す関数。None なら呼ばず、知らせを出さない。
    応答が error なら呼ばず、知らせを出さない。応答が error でなければ last = last_new_screen_time() を呼び、
    last が None なら知らせを出さない。None でなければ
    seconds = stagnation_alert.alert_seconds(<今回の look のイベントの time>, last) を求め、None なら知らせを出さない。
    seconds があれば、event_log.append_event(paths.events_file,
    stagnation_alert.build_alert_event(<今回の look のイベントの time>, <今回の frame>, seconds)) で、
    look の行の後に知らせを記録し、stagnation_alert.alert_line(seconds) の行を応答の行の直後に stdout に出す。

    frames_changed は行動履歴の要素 (action-history) が有効なときに渡す。None なら行動履歴を出さない。
    None でなく応答が error でなければ、イベントを記録した後に event_log.read_events(paths.events_file) を読み、
    今回の frame を持つ look のイベントを後ろから探して今回の look (current) とし、それより前のイベントを previous として、
    action_history.build_history_lines(previous, current.time, <今回の frame>, frames_changed) の行を stdout に続ける
    (並行に走ったコマンドのイベントが今回の look の後ろに追記されていることがあるため、
    最後のイベントを今回の look とはみなさない)。previous は今回の look より前のイベントだけなので、今回の知らせの行は入らない。
    応答が error なら履歴は出さない。

    reflection_notice は振り返りの要素 (reflection) が有効なときに True にする。False なら振り返りの知らせを出さない。
    True で応答が error でなければ、停滞の知らせを記録した後に event_log.read_events(paths.events_file) を読み、
    reflection.notice_pending(<イベント>) が True なら、event_log.append_event(paths.events_file,
    reflection.build_notice_event(<今回の look のイベントの time>, <今回の frame>)) で記録し、
    reflection.NOTICE_LINE の行を出す。応答が error なら知らせを出さず、記録もしない (次の成功した look で出す)。

    stdout の行の順は、応答、停滞の知らせの行 (出すときだけ)、振り返りの知らせの行 (出すときだけ)、
    行動履歴の行 (frames_changed を渡したときだけ)。どの場合も終了コードは上と同じ。

    複数フレーム: multi_frame は複数フレームの要素 (multi-frame) が有効なときに True にする。sleep は撮影の間の待ちに使う
    (本番は time.sleep)。下の multi_frame.<名前> は、モジュール switch2_harness.multi_frame の定数と関数を指す。
    True なら、上の 1 回の撮影の代わりに次のとおり撮る:
        1. frame_path を、上と同じく frame_files.reserve_next_frame_path(paths.frames_dir) で確保する
        2. multi_frames_dir = paths.root / multi_frame.MULTI_FRAMES_DIR_NAME を mkdir(exist_ok=True) で作る
        3. started = clock()、
           reply = multi_frame.shoot_burst(multi_frame.shot_paths(frame_path, multi_frames_dir),
           send=send, clock=clock, sleep=sleep)、elapsed_s = clock() - started で、6 回撮る
           (1〜5 回目は multi_frames_dir の一時ファイル、6 回目は frame_path に保存させる)
        4. look のイベントを 1 つだけ記録する: time=started、command="look"、args=()、reply=reply、
           ok=<reply が error で始まらないこと>、elapsed_s、frame=<成功なら上と同じ相対パス (例: "frames/000001.png")。
           失敗なら None>
        5. reply が error で始まれば、1〜5 回目のパスを unlink(missing_ok=True) で消し、最後に frame_path を
           unlink(missing_ok=True) で消す (frames/ と multi-frames/ に画像を残さない。frame_path を最後に消すのは、
           確保した番号を、その番号から名付けた一時ファイルを消す前に手放さないため)。
           上の失敗と同じく reply の 1 行を stdout に出して終了コード 1。停滞の知らせと行動履歴は出さない
        6. 成功なら、multi_frame.write_tile(<6 つのパス>, multi_frame.tile_path_for(frame_path, multi_frames_dir)) で
           並べた画像を書き、1〜5 回目のパスを unlink(missing_ok=True) で消す
        7. stdout の行の順は、応答、multi_frame.multi_frame_line(<並べた画像の絶対パス>)、
           停滞の知らせの行 (出すときだけ)、振り返りの知らせの行 (出すときだけ)、
           行動履歴の行 (frames_changed を渡したときだけ)。終了コードは 0。
           停滞の知らせ・振り返りの知らせ・行動履歴の扱いは上と同じ (look のイベントは 1 つなので、
           今回の frame を持つ look のイベントを後ろから探す処理がそのまま使える)
    """
    frame_path = reserve_next_frame_path(paths.frames_dir)
    frame = frame_path.relative_to(paths.root).as_posix()
    multi_frame_lines: tuple[str, ...] = ()
    if multi_frame:
        look, multi_frame_lines = _shoot_multi_frame_and_record(
            paths, frame_path, frame, send=send, clock=clock, sleep=sleep
        )
    else:
        look = _send_and_record(
            paths,
            "look",
            (),
            ["shot", str(frame_path)],
            send=send,
            clock=clock,
            frame_on_success=frame,
        )
    reply = look.reply
    if is_error_reply(reply):
        # 複数フレームの一時ファイルは _shoot_multi_frame_and_record が先に消しているので、確保したパスは最後に消す
        frame_path.unlink(missing_ok=True)
        return _reply_result(reply)
    alert_lines = _stagnation_alert_lines(paths, look, frame, last_new_screen_time)
    notice_lines = (
        _reflection_notice_lines(paths, look, frame) if reflection_notice else ()
    )
    if frames_changed is None:
        return CommandResult(
            exit_code=0,
            stdout_lines=(reply, *multi_frame_lines, *alert_lines, *notice_lines),
        )
    events = read_events(paths.events_file)
    index = next(
        i
        for i in range(len(events) - 1, -1, -1)
        if events[i].command == "look" and events[i].frame == frame
    )
    history = build_history_lines(
        events[:index], events[index].time, frame, frames_changed
    )
    return CommandResult(
        exit_code=0,
        stdout_lines=(
            reply,
            *multi_frame_lines,
            *alert_lines,
            *notice_lines,
            *history,
        ),
    )


def handle_press(
    paths: WorkspacePaths,
    action: str,
    args: Sequence[str],
    *,
    send: SendCommand,
    clock: Callable[[], float],
) -> CommandResult:
    """press: ボタン入力をデーモンに送る。

    まず args を press_args.split_press_args で分け直す (デーモンと同じく空白で分けたトークン列 tokens にする)。
    press_args.press_args_error(action, tokens) が error 行を返したら、送らずにその行を stderr に出し、終了コード 1。
    このときもイベントを記録する (reply=<その error 行>、ok=False、time=clock() を 1 回呼んだ値、elapsed_s=0.0)。
    通れば send([action, *tokens]) を送り、応答を stdout に出す。応答が error で始まれば終了コード 1。
    イベント: command="press"、args=(action, *tokens) (拒否したときも同じ)。
    例: args が ["RIGHT RIGHT"] なら send(["tap", "RIGHT", "RIGHT"]) を送る (action が "tap" のとき)。
    """
    tokens = split_press_args(args)
    event_args = (action, *tokens)
    rejection = press_args_error(action, tokens)
    if rejection is not None:
        append_event(
            paths.events_file,
            CommandEvent(
                time=clock(),
                command="press",
                args=event_args,
                reply=rejection,
                ok=False,
                elapsed_s=0.0,
            ),
        )
        return CommandResult(exit_code=1, stderr_lines=(rejection,))
    event = _send_and_record(
        paths, "press", event_args, list(event_args), send=send, clock=clock
    )
    return _reply_result(event.reply)


def handle_status(
    paths: WorkspacePaths, *, send: SendCommand, clock: Callable[[], float]
) -> CommandResult:
    """status: send(["status"]) を送り、応答を stdout に出す。応答が error で始まれば終了コード 1。

    イベント: command="status"、args=()。
    """
    event = _send_and_record(paths, "status", (), ["status"], send=send, clock=clock)
    return _reply_result(event.reply)


def handle_finish(
    paths: WorkspacePaths, *, send: SendCommand, clock: Callable[[], float]
) -> CommandResult:
    """finish: 押したままの入力を離してから HOME を押す。

    send(["up", "ALL"]) を送り、続けて (up ALL の応答が error でも) send(["tap", "HOME"]) を送る。
    2 つの応答をこの順に 1 行ずつ stdout に出す。どちらかが error で始まれば終了コード 1。
    イベントは送信ごとに 2 つ: command="finish"、args=("up", "ALL") と args=("tap", "HOME")。
    """
    replies = [
        _send_and_record(
            paths, "finish", request, list(request), send=send, clock=clock
        ).reply
        for request in (("up", "ALL"), ("tap", "HOME"))
    ]
    return _reply_result(*replies)


def handle_report(
    paths: WorkspacePaths, as_json: bool, agent: AgentKind = DEFAULT_AGENT
) -> CommandResult:
    """report: 実験の集計を stdout に出し、終了コード 0。

    event_log.read_events(paths.events_file) のイベントと、paths.agent_log_file のすべての行
    (agent_launcher.read_agent_log_lines(paths.agent_log_file, 0)。UTF-8 の errors="replace" でデコードするため、
    強制終了で途中で切れたマルチバイト文字は置換文字になり、JSON として読めない行は集計で読み飛ばされる。
    ファイルが無ければ空の列) を、session_report.summarize_session で集計する。
    agent (harness.json のエージェントの種類) は summarize_session に渡し、agent.jsonl の読み方を決める。
    look の画像は screen_change.load_sample(paths.root / frame) で、比較用の大きさに縮小して読む
    (look が多い記録でも、元の大きさの画像をすべて持たないようにするため)。
    as_json が False なら session_report.format_summary の各行を、
    True なら json.dumps(session_report.summary_to_dict(summary), ensure_ascii=False) の 1 行を出す。
    """
    summary = summarize_session(
        read_events(paths.events_file),
        lambda frame: load_sample(paths.root / frame),
        read_agent_log_lines(paths.agent_log_file, 0),
        agent=agent,
    )
    if as_json:
        lines = (json.dumps(summary_to_dict(summary), ensure_ascii=False),)
    else:
        lines = tuple(format_summary(summary).split("\n"))
    return CommandResult(exit_code=0, stdout_lines=lines)


def _codex_agents(
    paths: WorkspacePaths,
    commands: Sequence[str],
    writable_files: Sequence[str],
    base_env: Mapping[str, str],
    prepare_codex: PrepareCodex,
    harness_executable: Path,
) -> tuple[CodexPlayer, CodexCritic]:
    """Codex のプレイヤーと批評役を作る (handle_run の 2a.)。

    作業ディレクトリーの AGENTS.md を読み (読めなければ OSError)、harness_executable の絶対パスが書かれていなければ
    codex_home.CodexSetupError にする (rules がサンドボックスの外で動かすのはその形だけのため。仮想環境を動かした後など)。
    prepare_codex で専用の CODEX_HOME を用意する (用意できなければ codex_home.CodexSetupError)。
    """
    instructions_path = paths.root / instructions_file_name(CODEX_AGENT)
    instructions = instructions_path.read_text(encoding="utf-8")
    if str(harness_executable) not in instructions:
        raise CodexSetupError(
            f"{instructions_path} does not use {harness_executable}; "
            "create the workspace again with init"
        )
    codex_home = prepare_codex(
        paths, commands, writable_files, base_env, harness_executable
    )
    return CodexPlayer(codex_home, instructions), CodexCritic(codex_home)


def handle_run(
    paths: WorkspacePaths,
    minutes: float,
    options: LaunchOptions,
    *,
    start_process: StartProcess,
    clock: Callable[[], float],
    deadline_clock: Callable[[], float],
    sleep: Callable[[float], None],
    send: SendCommand,
    base_env: Mapping[str, str],
    available: tuple[Feature, ...] = FEATURES,
    screen_recording: ScreenRecording | None = None,
    reflecting: Reflecting | None = None,
    start_critic: StartCritic = start_critic_process,
    read_template: ReadTemplate = read_package_template,
    prepare_codex: PrepareCodex = prepare_codex_home,
    harness_executable: Path = DEFAULT_HARNESS_EXECUTABLE,
) -> CommandResult:
    """run: 作業ディレクトリーでエージェント (claude -p か codex exec) を起動し、minutes 分たったら止める。

    clock は記録の時刻、deadline_clock は期限と経過時間を計る時計 (本番はどちらも time.time) で、
    どちらもそのまま run_agent_until に渡す。
    screen_recording は、停滞の知らせの要素 (stagnation-alert) が有効なときに使う、画面の記録を始めて終える
    コンテキストマネージャーを作る関数 (screen_recorder.ScreenRecording)。None なら
    screen_recorder.recording_screens(paths, send=send, clock=clock) を使う (本番は None。テストで本物の撮影の代わりを渡す)。

    1. agent_launcher.validate_minutes(minutes) と agent_launcher.validate_max_budget_usd(options.max_budget_usd) を
       この順に呼ぶ。ValueError なら `error <メッセージ>` を stderr に出し、終了コード 1
       (何も起動せず、デーモンにも送らない)
    2. harness_config.read_config(paths.harness_file, available) で設定を読む。
       ValueError なら `error <メッセージ>` を stderr に出し、終了コード 1
       config.agent が codex で options.max_budget_usd が None でなければ、`error <CODEX_BUDGET_MESSAGE>` を stderr に出し、
       終了コード 1 (codex exec は費用を出さないため。何も起動しない)。
       options.model が None なら、config.agent の既定のモデル (agent_kind.default_model) にする
    2a. config.agent が codex なら、作業ディレクトリーの AGENTS.md を読み、harness_executable (ハーネスの実行ファイルの
       絶対パス) が書かれていることを確かめて、prepare_codex(paths, <エージェント用のサブコマンド>,
       <書き換えられるファイルの名前>, base_env, harness_executable) で専用の CODEX_HOME を用意する
       (本番は codex_home.prepare_codex_home)。AGENTS.md を読めない (OSError) か codex_home.CodexSetupError なら、
       `error <メッセージ>` を stderr に出し、終了コード 1。プレイヤーは codex_player.CodexPlayer(<CODEX_HOME>,
       <AGENTS.md の本文>)、批評役は codex_critic.CodexCritic(<CODEX_HOME>) にする。
       claude なら、プレイヤーは agent_launcher.CLAUDE_PLAYER、批評役は reflection_critic.CLAUDE_CRITIC
    3. agent_launcher.run_agent_until を、game=config.game、agent=<プレイヤー>、
       agent_commands=features.agent_commands(config.features, available)、
       writable_files=<features.agent_writable_files(config.features, available) の各 name のタプル>、
       record=<event_log.append_event(paths.events_file, ...) をする関数> で、
       with signal_guard.interrupts_as_exceptions(): の中で呼ぶ。
       設定の features に features.STAGNATION_ALERT_FEATURE があれば、interrupts_as_exceptions の with の内側で
       画面の記録 (screen_recording(paths)。None なら screen_recorder.recording_screens(paths, send=send, clock=clock)) の
       with に入り、その中で run_agent_until を呼ぶ。無ければ記録の代わりに contextlib.nullcontext() に入る。
       記録は records/screen-samples/run-<NNN>/ に、裏で一定の間隔で撮った画面の縮小画像と時刻を書き、
       撮影ごとに新しい画面かを判定して状態ファイルを書く (look がそれを読んで停滞の知らせを出す)。
       記録は最初の起動の前に始まり、run_agent_until が返った後
       (後片付けの up ALL の後) か、例外で抜けるときに止まる。
       設定の features に features.REFLECTION_FEATURE があれば、画面の記録の with の内側で、
       振り返り (reflecting(paths, config)。None なら reflection_critic.reflecting_with_critic(paths, config, clock=clock,
       base_env=base_env, start_critic=start_critic, read_template=read_template, critic=<批評役>)) の with にも入り、その中で
       run_agent_until を呼ぶ。無ければ contextlib.nullcontext() に入る。
       start_critic と read_template は reflecting が None のときだけ使う (本番は cli が CliDependencies の値を渡す)。
       振り返りは、run の開始から 5 分ごとに批評役を起動して notes.md の「振り返り」の節を書き換え、
       画面の記録と同じく、最初の起動の前に始まり、後片付けの up ALL の後か、例外で抜けるときに止まる。
       後片付けは run_agent_until の finally で済んでいるので、
       RunTerminated (SIGTERM・SIGHUP) で抜けたら RUN_TERMINATED_MESSAGE を、KeyboardInterrupt (Ctrl-C) で抜けたら
       RUN_INTERRUPTED_MESSAGE を stderr に出し、終了コード 1 (例外は外に出さない)
    4. `ok run finished reason=<理由> launches=<起動した回数> log=<paths.agent_log_file>` を stdout に出し、終了コード 0。
       理由は agent_launcher.StopReason のいずれか (deadline / budget_exhausted / no_session / resumes_exhausted)
    5. ただし、後片付けの up ALL の応答 (RunOutcome.release_reply) が error で始まるときは、4. の行に加えて
       `error up ALL after the run was not confirmed: <応答>` を stderr に出し、終了コード 1
       (押したままの入力が残っているかもしれないことを、起動した人に知らせるため)
    """
    try:
        validate_minutes(minutes)
        validate_max_budget_usd(options.max_budget_usd)
    except ValueError as e:
        return _error_result(e)
    try:
        config = read_config(paths.harness_file, available)
    except ValueError as e:
        return _error_result(e)
    if config.agent == CODEX_AGENT and options.max_budget_usd is not None:
        return _error_result(CODEX_BUDGET_MESSAGE)
    if options.model is None:
        options = replace(options, model=default_model(config.agent))
    commands = agent_commands(config.features, available)
    writable_files = tuple(
        writable_file.name
        for writable_file in agent_writable_files(config.features, available)
    )
    player: PlayerAgent = CLAUDE_PLAYER
    critic: CriticAgent = CLAUDE_CRITIC
    if config.agent == CODEX_AGENT:
        try:
            player, critic = _codex_agents(
                paths,
                commands,
                writable_files,
                base_env,
                prepare_codex,
                harness_executable,
            )
        except (OSError, CodexSetupError) as e:
            return _error_result(e)
    recording: contextlib.AbstractContextManager[object]
    if STAGNATION_ALERT_FEATURE not in config.features:
        recording = contextlib.nullcontext()
    elif screen_recording is not None:
        recording = screen_recording(paths)
    else:
        recording = recording_screens(paths, send=send, clock=clock)
    reflection: contextlib.AbstractContextManager[object]
    if REFLECTION_FEATURE not in config.features:
        reflection = contextlib.nullcontext()
    elif reflecting is not None:
        reflection = reflecting(paths, config)
    else:
        reflection = reflecting_with_critic(
            paths,
            config,
            clock=clock,
            base_env=base_env,
            start_critic=start_critic,
            read_template=read_template,
            critic=critic,
        )
    try:
        # 振り返りは、画面の記録の with の内側で動かす
        with interrupts_as_exceptions(), recording, reflection:
            outcome = run_agent_until(
                minutes,
                paths,
                config.game,
                commands,
                options,
                start_process=start_process,
                clock=clock,
                deadline_clock=deadline_clock,
                sleep=sleep,
                send=send,
                record=partial(append_event, paths.events_file),
                base_env=base_env,
                writable_files=writable_files,
                agent=player,
            )
    except RunTerminated:
        return CommandResult(exit_code=1, stderr_lines=(RUN_TERMINATED_MESSAGE,))
    except KeyboardInterrupt:
        return CommandResult(exit_code=1, stderr_lines=(RUN_INTERRUPTED_MESSAGE,))
    finished = (
        f"ok run finished reason={outcome.reason} launches={outcome.launches} "
        f"log={paths.agent_log_file}"
    )
    if is_error_reply(outcome.release_reply):
        return CommandResult(
            exit_code=1,
            stdout_lines=(finished,),
            stderr_lines=(release_not_confirmed_message(outcome.release_reply),),
        )
    return CommandResult(exit_code=0, stdout_lines=(finished,))
