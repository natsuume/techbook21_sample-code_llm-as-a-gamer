"""switch2-harness の各サブコマンド (init / look / press / status / finish / report / run) の処理本体。

デーモンへの送信 (send)・時計 (clock / now)・待機 (sleep)・プロセスの起動 (start_process)・
テンプレートの読み込み (read_template) は引数で受け取る。各処理は表示する行と終了コードを
CommandResult で返し、表示は cli が行う。

表示の規則:
    - デーモンへの送信の結果 (send が返した 1 行。接続できないときの error 行を含む) と ok 行は標準出力に出す
    - ハーネス自身の検証で出す error 行 (init の検証、press の拒否、run の --minutes など) は標準エラー出力に出す
    - 終了コードは成功 0、失敗 1

look / press / status / finish は、成否によらず records/events.jsonl にイベントを 1 つずつ (finish は 2 つ) 記録する。
イベントの time は送信の直前の clock()、elapsed_s は送信の直後の clock() との差、ok は応答が error で始まらないこと。
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path

from switch2_harness.agent_launcher import (
    LaunchOptions,
    StartProcess,
    read_agent_log_lines,
    run_agent_until,
    validate_max_budget_usd,
    validate_minutes,
)
from switch2_harness.daemon_client import SendCommand
from switch2_harness.event_log import CommandEvent, append_event, read_events
from switch2_harness.features import (
    FEATURES,
    Feature,
    agent_commands,
    validate_feature_names,
)
from switch2_harness.frame_files import next_frame_path
from switch2_harness.harness_config import (
    HarnessConfig,
    format_created_at,
    read_config,
)
from switch2_harness.instructions import ReadTemplate, render_instructions
from switch2_harness.press_args import press_args_error, split_press_args
from switch2_harness.screen_change import load_gray
from switch2_harness.session_report import (
    format_summary,
    summarize_session,
    summary_to_dict,
)
from switch2_harness.signal_guard import RunTerminated, terminate_signals_as_exception
from switch2_harness.workspace import (
    WorkspaceNotEmptyError,
    WorkspacePaths,
    create_workspace,
    workspace_paths,
)

# SIGTERM・SIGHUP で run が終わったときに出す error 行
RUN_TERMINATED_MESSAGE = "error run terminated by signal"


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
) -> str:
    """request を送り、イベントを記録して応答を返す。frame は応答が error でないときだけ frame_on_success にする。"""
    started = clock()
    reply = send(request)
    elapsed_s = clock() - started
    ok = not is_error_reply(reply)
    append_event(
        paths.events_file,
        CommandEvent(
            time=started,
            command=command,
            args=event_args,
            reply=reply,
            ok=ok,
            elapsed_s=elapsed_s,
            frame=frame_on_success if ok else None,
        ),
    )
    return reply


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
) -> CommandResult:
    """init: 作業ディレクトリーを作る。

    1. enable を features.validate_feature_names(enable, available) で検証する。
       ValueError なら `error <メッセージ>` を stderr に出し、終了コード 1 (ファイルシステムには触れない)
    2. HarnessConfig(game, goal, 検証した名前, format_created_at(now())) を作る
    3. instructions.render_instructions(config, read_template, available) で CLAUDE.md の本文を作る
    4. workspace.create_workspace で作る。WorkspaceNotEmptyError なら `error <メッセージ>` を stderr に出し、終了コード 1
    5. `ok <作業ディレクトリーの絶対パス> features=<名前を "," でつないだもの。無ければ none>` を stdout に出し、終了コード 0
    """
    try:
        names = validate_feature_names(enable, available)
    except ValueError as e:
        return _error_result(e)
    config = HarnessConfig(
        game=game, goal=goal, features=names, created_at=format_created_at(now())
    )
    paths = workspace_paths(directory)
    instructions_text = render_instructions(config, read_template, available)
    try:
        create_workspace(paths, config, instructions_text)
    except WorkspaceNotEmptyError as e:
        return _error_result(e)
    features_text = ",".join(names) or "none"
    return CommandResult(
        exit_code=0, stdout_lines=(f"ok {paths.root} features={features_text}",)
    )


def handle_look(
    paths: WorkspacePaths, *, send: SendCommand, clock: Callable[[], float]
) -> CommandResult:
    """look: 画面を撮影して frames/ に保存する。

    frame_files.next_frame_path(paths.frames_dir) の絶対パスで send(["shot", <絶対パス>]) を送り、応答を stdout に出す。
    応答が error で始まれば終了コード 1、そうでなければ 0。
    イベント: command="look"、args=()、frame=<作業ディレクトリーからの相対パス (例: "frames/000001.png")>。
    応答が error なら frame は None。
    """
    frame_path = next_frame_path(paths.frames_dir)
    reply = _send_and_record(
        paths,
        "look",
        (),
        ["shot", str(frame_path)],
        send=send,
        clock=clock,
        frame_on_success=frame_path.relative_to(paths.root).as_posix(),
    )
    return _reply_result(reply)


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
    reply = _send_and_record(
        paths, "press", event_args, list(event_args), send=send, clock=clock
    )
    return _reply_result(reply)


def handle_status(
    paths: WorkspacePaths, *, send: SendCommand, clock: Callable[[], float]
) -> CommandResult:
    """status: send(["status"]) を送り、応答を stdout に出す。応答が error で始まれば終了コード 1。

    イベント: command="status"、args=()。
    """
    reply = _send_and_record(paths, "status", (), ["status"], send=send, clock=clock)
    return _reply_result(reply)


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
        )
        for request in (("up", "ALL"), ("tap", "HOME"))
    ]
    return _reply_result(*replies)


def handle_report(paths: WorkspacePaths, as_json: bool) -> CommandResult:
    """report: 実験の集計を stdout に出し、終了コード 0。

    event_log.read_events(paths.events_file) のイベントと、paths.agent_log_file のすべての行
    (agent_launcher.read_agent_log_lines(paths.agent_log_file, 0)。UTF-8 の errors="replace" でデコードするため、
    強制終了で途中で切れたマルチバイト文字は置換文字になり、JSON として読めない行は集計で読み飛ばされる。
    ファイルが無ければ空の列) を、session_report.summarize_session で集計する。
    look の画像は screen_change.load_gray(paths.root / frame) で読む。
    as_json が False なら session_report.format_summary の各行を、
    True なら json.dumps(session_report.summary_to_dict(summary), ensure_ascii=False) の 1 行を出す。
    """
    summary = summarize_session(
        read_events(paths.events_file),
        lambda frame: load_gray(paths.root / frame),
        read_agent_log_lines(paths.agent_log_file, 0),
    )
    if as_json:
        lines = (json.dumps(summary_to_dict(summary), ensure_ascii=False),)
    else:
        lines = tuple(format_summary(summary).split("\n"))
    return CommandResult(exit_code=0, stdout_lines=lines)


def handle_run(
    paths: WorkspacePaths,
    minutes: float,
    options: LaunchOptions,
    *,
    start_process: StartProcess,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    send: SendCommand,
    base_env: Mapping[str, str],
    available: tuple[Feature, ...] = FEATURES,
) -> CommandResult:
    """run: 作業ディレクトリーで claude -p を起動し、minutes 分たったら止める。

    1. agent_launcher.validate_minutes(minutes) と agent_launcher.validate_max_budget_usd(options.max_budget_usd) を
       この順に呼ぶ。ValueError なら `error <メッセージ>` を stderr に出し、終了コード 1
       (何も起動せず、デーモンにも送らない)
    2. harness_config.read_config(paths.harness_file, available) で設定を読む。
       ValueError なら `error <メッセージ>` を stderr に出し、終了コード 1
    3. agent_launcher.run_agent_until を、game=config.game、
       agent_commands=features.agent_commands(config.features, available)、
       record=<event_log.append_event(paths.events_file, ...) をする関数> で、
       with signal_guard.terminate_signals_as_exception(): の中で呼ぶ。
       RunTerminated (SIGTERM・SIGHUP) で抜けたら (後片付けは run_agent_until の finally で済んでいる)、
       RUN_TERMINATED_MESSAGE を stderr に出し、終了コード 1
    4. `ok run finished reason=<理由> launches=<起動した回数> log=<paths.agent_log_file>` を stdout に出し、終了コード 0。
       理由は agent_launcher.StopReason のいずれか (deadline / budget_exhausted / no_session / resumes_exhausted)
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
    try:
        with terminate_signals_as_exception():
            outcome = run_agent_until(
                minutes,
                paths,
                config.game,
                agent_commands(config.features, available),
                options,
                start_process=start_process,
                clock=clock,
                sleep=sleep,
                send=send,
                record=partial(append_event, paths.events_file),
                base_env=base_env,
            )
    except RunTerminated:
        return CommandResult(exit_code=1, stderr_lines=(RUN_TERMINATED_MESSAGE,))
    finished = (
        f"ok run finished reason={outcome.reason} launches={outcome.launches} "
        f"log={paths.agent_log_file}"
    )
    return CommandResult(exit_code=0, stdout_lines=(finished,))
