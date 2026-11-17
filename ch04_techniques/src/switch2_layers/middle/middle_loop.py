"""middle の 1 回分の処理 (run_middle_step) と、run のループ (MiddleLoop)、middle-once (run_middle_once)。

1 回分の流れ (run_middle_step):
    1. 実行ディレクトリーのファイルとタスクのキューの部品から入力を作る (middle_input_builder.build_middle_input)
    2. MiddleAnalyzer.analyze を呼ぶ。入力の request_sink には、その回の middle/requests.jsonl の書き込み先
       (MiddleRequestWriter) を入れる。gpt-6-luna の実装は、要求を送る直前に 1 行書く。追記の失敗は middle の失敗として
       数えず、logging で警告を書いて要求を送る (run.jsonl には書かない)。フェイクの MiddleAnalyzer は書かない
    3. 出力を書く (middle_output_writer.write_middle_output。キューの部品の操作を含む)
    4. マップの記録の行 (middle.map_records.build_map_record) があれば、middle/map_records.jsonl に追記する。
       追記の失敗は middle の失敗として数えず、logging で警告を書いて続ける (run.jsonl には書かない)
    5. 初めての場面に名前が付いたら、その場面の操作説明と攻略情報と、ゲームの性質 (場面に依らない) の deep を、
       きっかけ new_scene でスケジューラーに渡す
       (middle-once では渡さず、記録だけする)
    6. 1 行を middle/calls.jsonl に書く。行の request には、2 で送った要求の番号 (送らなかった回は null) を書く。
       MiddleAnalyzer が middle_call_record.MiddleCallDetailsSource でもあれば
       (gpt-6-luna の実装)、その回の記録 (usage・概算の費用・応答など) を行に足す (失敗した回も同じ)
要求の番号は MiddleRequestLog が、要求を送るたびに 1 増やして数える (追記に失敗した回も増やす)。run では MiddleLoop が
1 つ作って 1 から数え、middle-once は保存済みの middle/requests.jsonl の最後の行の番号の次から数える。
成功した回の出力の press_home が真なら、MiddleLoop.run_once が部品の on_press_home で app に知らせる (app は段階が
play のときだけ HOME を短押しし、復帰の段階に移る)。
ループ (MiddleLoop.run) は、最初のフレームが frames.jsonl に載るのを待ってから、理由 interval で最初の実行を始め
(middle.middle_trigger.MiddleTrigger.first_reasons)、その後は MiddleTrigger.wait_next で、前回の応答 (失敗を含む) の
後のきっかけ (キューが空になった・取り消しの要求・詰まり・場面転換・一定の間隔) を待って次を始める。analyze が例外を
出したら、前回の出力を残したまま、その回の理由を次の回に持ち越して (MiddleTrigger.carry_over)、前回の応答から
interval_s 秒たってから再び呼ぶ (持ち越した理由やほかのきっかけがあっても、失敗の後の間隔は縮めない)。max_errors 回
続けたら on_stop で全体を止める。
止める合図は、analyze が戻ったとき (例外を含む)、出力の書き込み (キューの部品の操作の前・middle/state.json・
notes/scene_labels.json)・middle/map_records.jsonl・middle/calls.jsonl のそれぞれの直前、deep を要求する直前 (種類ごと)
に確かめる。
立っていたら、残りのファイル・middle/map_records.jsonl・middle/calls.jsonl・run.jsonl に書かず、deep も呼ばずにループを
抜ける (止めた後に記録を増やさない。書く順は変えず、それまでに書いたファイルは戻さない)。
"""

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from switch2_layers.deep.deep_types import DeepRequestSink, DeepTrigger, DocumentKind
from switch2_layers.error_text import describe_exception
from switch2_layers.file_io import append_json_line, read_json_lines
from switch2_layers.frames.frame_store import latest_frame_t
from switch2_layers.middle.map_records import build_map_record
from switch2_layers.middle.middle_call_record import (
    MiddleCallRecord,
    middle_call_row_with_details,
    middle_request_row,
    take_analyzer_call_details,
)
from switch2_layers.middle.middle_input_builder import (
    build_middle_input,
    has_recorded_frame,
)
from switch2_layers.middle.middle_output_writer import (
    NEW_SCENE_DEEP_KINDS,
    write_middle_output,
)
from switch2_layers.middle.middle_trigger import MiddleCallReason, MiddleTrigger
from switch2_layers.middle.middle_types import (
    MiddleAnalyzer,
    MiddleInput,
    MiddleOutput,
    MiddleRequest,
    MiddleState,
)
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.records.run_log import (
    EVENT_DEEP_REQUEST_ERROR,
    EVENT_MIDDLE_ERROR,
    RunLog,
)
from switch2_layers.run_clock import RunClock, frozen_clock
from switch2_layers.run_directory import RunPaths, open_run_directory
from switch2_layers.tasks.task_board import TaskBoard, restore_task_board

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

FIRST_FRAME_POLL_S = 0.2  # 最初のフレームが frames.jsonl に載ったかを確かめる間隔 (秒)


@dataclass(frozen=True)
class MiddleStepResult:
    """middle の 1 回分の結果。

    ok                    analyze と出力の書き込みに成功したら True
    state                 書いた middle/state.json の値。失敗したときと、止める合図で抜けたときは None
    new_scene             一覧に新しく足した場面。無ければ None
    deep_requests         初めての場面のために deep を呼ぶ文書の種類 (new_scene があれば controls と tips。無ければ空)
    error                 失敗の説明。成功なら None
    deep_request_errors   deep の要求で受け口が出した例外の (種類, 説明)。要求の順。無ければ空
                          (要求の例外は ok に影響しない)
    stopped               止める合図を見て途中で抜けたら True。このときの結果は常に STOPPED_STEP_RESULT
                          (ok は False、error は None、ほかの値も空。途中まで書いたファイルがあっても同じ。失敗には数えない)
    """

    ok: bool
    state: MiddleState | None
    new_scene: SceneLabel | None
    deep_requests: tuple[DocumentKind, ...]
    error: str | None
    deep_request_errors: tuple[tuple[DocumentKind, str], ...] = ()
    stopped: bool = False


# 止める合図で抜けたときの結果
STOPPED_STEP_RESULT = MiddleStepResult(
    ok=False, state=None, new_scene=None, deep_requests=(), error=None, stopped=True
)


class MiddleRequestLog:
    """middle/requests.jsonl への追記と、要求の番号の数え方。回をまたいで同じものを使う。

    run では MiddleLoop が 1 つ作り (last_request は 0)、middle-once は read_last_request_number で読んだ保存済みの
    最後の番号から作る。1 つのスレッドから使う。

    path           middle/requests.jsonl のパス (RunPaths.middle_requests)
    last_request   最後に割り当てた要求の番号 (作ったときは、渡した値)
    """

    def __init__(self, path: Path, last_request: int = 0) -> None:
        """まだ番号を割り当てていない状態 (last_request は渡した値) で作る。ここではファイルを読み書きしない。"""
        self.path = path
        self.last_request = last_request

    def append(self, t_sent: float, request: MiddleRequest) -> int:
        """last_request を 1 増やし、その番号の行 middle_call_record.middle_request_row(番号, t_sent, request) を
        file_io.append_json_line で path に追記して、番号を返す。

        追記が例外 (Exception) を出したら、外に出さずに logging で警告を書き、増やした番号を返す (middle の失敗に
        数えず、run.jsonl にも書かない。requests.jsonl にはその番号の行が無くなる)。
        """
        # 追記に失敗した回も番号を進める (calls.jsonl の行には、この番号を書く)
        self.last_request += 1
        try:
            append_json_line(
                self.path, middle_request_row(self.last_request, t_sent, request)
            )
        except Exception:
            logger.warning("failed to append to %s", self.path, exc_info=True)
        return self.last_request


class MiddleRequestWriter:
    """middle/requests.jsonl の 1 回分の書き込み先。run_middle_step が回ごとに作り、MiddleInput.request_sink として
    analyze に渡す。

    log       要求の番号を数えて追記する MiddleRequestLog (回をまたいで同じもの)
    t_sent    その回の t_sent (run_middle_step が analyze を呼ぶ直前に読んだ時刻。行の t_sent に書く)
    request   その回に割り当てた要求の番号。まだ呼ばれていなければ None
    """

    def __init__(self, log: MiddleRequestLog, t_sent: float) -> None:
        """まだ呼ばれていない状態 (request が None) で作る。"""
        self.log = log
        self.t_sent = t_sent
        self.request: int | None = None

    def __call__(self, request: MiddleRequest) -> None:
        """log.append(t_sent, request) を呼び、返った番号を self.request にする。例外は出さない (追記の失敗は
        MiddleRequestLog.append が警告にする)。"""
        self.request = self.log.append(self.t_sent, request)


def read_last_request_number(path: Path) -> int:
    """保存済みの middle/requests.jsonl (path) の最後の行の request の値を返す (middle-once で番号を続けるため)。

    行は file_io.read_json_lines で読む。ファイルが無いか、行が無ければ 0 を返す。
    """
    rows = read_json_lines(path)
    if not rows:
        return 0
    last = rows[-1].get("request")
    return last if isinstance(last, int) else 0


def run_middle_step(
    paths: RunPaths,
    analyzer: MiddleAnalyzer,
    clock: RunClock,
    window_s: float,
    deep_requests: DeepRequestSink | None,
    board: TaskBoard,
    reasons: Sequence[MiddleCallReason],
    trigger_notify: Callable[[MiddleCallReason], None] | None,
    stop: threading.Event | None = None,
    request_log: MiddleRequestLog | None = None,
) -> MiddleStepResult:
    """middle の 1 回分を処理する (モジュールの docstring の 1〜6)。

    board はタスクのキューの部品、reasons はこの回に middle を呼んだ理由、trigger_notify は middle を呼ぶきっかけを
    知らせる関数 (MiddleTrigger.notify。None なら知らせない)。stop は止める合図 (MiddleLoop が渡す)。None なら合図を
    確かめない。request_log は middle/requests.jsonl への追記と要求の番号 (MiddleLoop と run_middle_once が渡す)。
    None なら要求を記録しない (calls.jsonl の行の request は null)。

    1. now_t = clock.now() で build_middle_input(paths, now_t, window_s, board, reasons)
    2. t_sent = clock.now()。request_log が None でなければ writer = MiddleRequestWriter(request_log, t_sent) を作り、
       入力の request_sink を writer にした入力 (dataclasses.replace) を、この回の入力にする (3〜6 もこの入力を使う)。
       その入力で analyze を呼び、戻ったら (例外でも) t_received = clock.now()。ここで stop を確かめる
    3. write_middle_output(paths, 入力, 出力, generated_t=t_received, board, reasons, trigger_notify, stop) で出力を書く:
       キューの部品の操作 → middle/state.json → notes/scene_labels.json (新しい場面のときだけ)。
       キューの部品の操作の前と、それぞれのファイルを書く直前に stop を確かめる (立っていたら write_middle_output が
       None を返す)
    4. middle.map_records.build_map_record(入力, 書いた middle/state.json の scene_id, 出力) が None でなければ、
       追記の直前に stop を確かめてから、file_io.append_json_line で middle/map_records.jsonl に追記する。
       追記が例外 (Exception) を出したら、外に出さずに logging で警告を書いて続ける (middle の失敗に数えず、
       run.jsonl にも calls.jsonl にも書かない)
    5. new_scene があり deep_requests が None でなければ、NEW_SCENE_DEEP_KINDS の順に、request の直前に stop を確かめてから
       deep_requests.request(種類, DeepTrigger.NEW_SCENE, new_scene.id) を呼ぶ。request が例外 (Exception) を出したら、
       外に出さずに (種類, 説明) を deep_request_errors に足し、残りの種類の要求を続ける
    6. middle/calls.jsonl に middle_call_record.middle_call_row_with_details(記録, analyze の記録) の行を書く
       (書く直前に stop を確かめる。記録の reasons は reasons。deep_requests_sent は deep_requests が None でなく、要求を
       渡したら True。要求が例外を出した場合も True。記録の request は writer.request (request_log が None なら None))
    analyze の記録は、analyze が戻った直後 (例外を含む) に middle_call_record.take_analyzer_call_details(analyzer) で
    受け取る (analyzer が MiddleCallDetailsSource でなければ空。空なら行は middle_call_row と同じ)。
    1〜3 の例外 (Exception。キューの部品の操作の例外を含む) は外に出さず、error の行 (reasons は書き、
    new_tasks・destination_fixes・cancel_task_ids・cancel_used・new_tasks_used は null) を calls.jsonl に書いて
    (書く直前に stop を確かめる) ok=False の結果を返す (middle/state.json は、書く前に失敗したなら前回の出力のまま)。
    error の行にも analyze の記録を足す (analyze の失敗と、analyze の後の 3 の失敗。1 で失敗して analyze を
    呼ばなかった回は、記録を受け取らずに空とする)。
    error の行の request は、analyze を呼んだ回は writer.request (送った後の失敗なら送った要求の番号。要求を送らなかった
    なら None)、1 で失敗して analyze を呼ばなかった回は None。
    stop を確かめたときに立っていたら、そこで STOPPED_STEP_RESULT を返し、残りの書き込み (キューの部品の操作・
    middle/state.json・notes/scene_labels.json のうちまだ行っていないもの)・マップの記録の行・deep の要求・calls.jsonl の
    行を書かない。書く順は変えず、それまでに書いたものは戻さない。analyze が戻ったときに立っていたら、analyze が送る
    直前に書いた middle/requests.jsonl の行のほかは何も書かず、deep も呼ばない。
    """
    now_t = clock.now()
    t_sent = now_t
    middle_input: MiddleInput | None = None
    # analyze の 1 回の記録 (usage など)。入力を作れずに analyze を呼ばなかった回は空のまま
    call_details: dict[str, object] = {}
    # その回の middle/requests.jsonl の書き込み先。入力を作れなかった回と、request_log が None の回は None のまま
    writer: MiddleRequestWriter | None = None
    try:
        middle_input = build_middle_input(paths, now_t, window_s, board, reasons)
        t_sent = clock.now()
        if request_log is not None:
            writer = MiddleRequestWriter(request_log, t_sent)
            middle_input = replace(middle_input, request_sink=writer)
        try:
            output = analyzer.analyze(middle_input)
        finally:
            # analyze が戻った直後 (例外を含む) に、その回の記録を受け取る
            call_details = take_analyzer_call_details(analyzer)
    except Exception as error:
        t_received = clock.now()
        if _is_stopped(stop):
            return STOPPED_STEP_RESULT
        logger.debug("middle analyze failed", exc_info=True)
        return _record_failure(
            paths,
            t_sent,
            t_received,
            middle_input,
            error,
            stop,
            call_details,
            reasons,
            _sent_request(writer),
        )
    t_received = clock.now()
    if _is_stopped(stop):
        return STOPPED_STEP_RESULT
    try:
        # キューの部品の操作の前と、middle/state.json・notes/scene_labels.json のそれぞれを書く直前に stop を確かめる
        write_result = write_middle_output(
            paths,
            middle_input,
            output,
            generated_t=t_received,
            board=board,
            reasons=reasons,
            trigger_notify=trigger_notify,
            stop=stop,
        )
    except Exception as error:
        logger.debug("writing the middle output failed", exc_info=True)
        return _record_failure(
            paths,
            t_sent,
            t_received,
            middle_input,
            error,
            stop,
            call_details,
            reasons,
            _sent_request(writer),
        )
    if write_result is None:
        return STOPPED_STEP_RESULT
    if not _append_map_record(
        paths, middle_input, write_result.state.scene_id, output, stop
    ):
        return STOPPED_STEP_RESULT
    new_scene = write_result.new_scene
    deep_kinds = NEW_SCENE_DEEP_KINDS if new_scene is not None else ()
    request_errors: list[tuple[DocumentKind, str]] = []
    requests_sent = False
    if new_scene is not None and deep_requests is not None:
        requests_sent = True
        for kind in deep_kinds:
            if _is_stopped(stop):
                return STOPPED_STEP_RESULT
            try:
                deep_requests.request(kind, DeepTrigger.NEW_SCENE, new_scene.id)
            except Exception as error:
                logger.debug("middle deep request failed", exc_info=True)
                request_errors.append((kind, describe_exception(error)))
    record = MiddleCallRecord(
        t_sent=t_sent,
        t_received=t_received,
        middle_input=middle_input,
        write_result=write_result,
        deep_requests=deep_kinds,
        deep_requests_sent=requests_sent,
        error=None,
        reasons=reasons,
        request=_sent_request(writer),
    )
    if _is_stopped(stop):
        return STOPPED_STEP_RESULT
    append_json_line(
        paths.middle_calls, middle_call_row_with_details(record, call_details)
    )
    return MiddleStepResult(
        ok=True,
        state=write_result.state,
        new_scene=new_scene,
        deep_requests=tuple(deep_kinds),
        error=None,
        deep_request_errors=tuple(request_errors),
    )


def _append_map_record(
    paths: RunPaths,
    middle_input: MiddleInput,
    scene_id: str | None,
    output: MiddleOutput,
    stop: threading.Event | None,
) -> bool:
    """その回のマップの記録の行を middle/map_records.jsonl に追記する (run_middle_step の 4)。

    行が無い回 (build_map_record が None) は何も書かない。追記の直前に stop を確かめ、立っていれば書かずに False を
    返す (呼び出し側は STOPPED_STEP_RESULT を返す)。それ以外は True を返す。追記の例外 (Exception) は外に出さず、
    logging で警告を書く (middle の失敗に数えない)。
    """
    row = build_map_record(middle_input, scene_id, output)
    if row is None:
        return True
    if _is_stopped(stop):
        return False
    try:
        append_json_line(paths.middle_map_records, row)
    except Exception:
        logger.warning(
            "failed to append to %s", paths.middle_map_records, exc_info=True
        )
    return True


def _record_failure(
    paths: RunPaths,
    t_sent: float,
    t_received: float,
    middle_input: MiddleInput | None,
    error: Exception,
    stop: threading.Event | None,
    call_details: Mapping[str, object],
    reasons: Sequence[MiddleCallReason],
    request: int | None,
) -> MiddleStepResult:
    """失敗した回の行 (理由 reasons と、analyze の記録 call_details と、送った要求の番号 request を足したもの) を
    calls.jsonl に書き、ok=False の結果を返す。

    書く直前に stop を確かめ、立っていれば書かずに STOPPED_STEP_RESULT を返す。
    """
    if _is_stopped(stop):
        return STOPPED_STEP_RESULT
    description = describe_exception(error)
    record = MiddleCallRecord(
        t_sent=t_sent,
        t_received=t_received,
        middle_input=middle_input,
        write_result=None,
        deep_requests=(),
        deep_requests_sent=False,
        error=description,
        reasons=reasons,
        request=request,
    )
    append_json_line(
        paths.middle_calls, middle_call_row_with_details(record, call_details)
    )
    return MiddleStepResult(
        ok=False, state=None, new_scene=None, deep_requests=(), error=description
    )


def _sent_request(writer: MiddleRequestWriter | None) -> int | None:
    """その回に送った要求の番号 (writer.request) を返す。writer が None (要求を記録しない回と、入力を作れなかった回)
    なら None。"""
    return None if writer is None else writer.request


def _is_stopped(stop: threading.Event | None) -> bool:
    """止める合図 stop が立っていれば True。stop が None (合図を確かめない) なら False。"""
    return stop is not None and stop.is_set()


@dataclass(frozen=True)
class MiddleLoopSettings:
    """middle のループの設定。

    interval_s   前回の応答から次を始めるまでの秒数 (--middle-interval)。待ちは parts.trigger が行う (この値は
                 MiddleTrigger を作るときの interval_s と同じ値にする)
    window_s     入力に使う一定間隔のフレームの範囲 (--middle-window)
    max_errors   続けて失敗したら止める回数 (--max-middle-errors)
    """

    interval_s: float
    window_s: float
    max_errors: int


@dataclass(frozen=True)
class MiddleLoopParts:
    """middle のループが使う部品。

    paths           実行ディレクトリー
    analyzer        middle の分析
    clock           実行の経過秒の時計
    run_log         run.jsonl (middle_error を書く)
    deep_requests   deep を呼ぶ要求の受け口 (run では DeepScheduler)
    on_stop         全体を止める関数 (理由の文字列を受け取る)
    board           タスクのキューの部品 (run では surface のループと同じ TaskBoard)
    trigger         middle を呼ぶきっかけの待ち (run では surface のループが同じ MiddleTrigger の notify で知らせる)
    on_press_home   成功した回の出力の press_home が真のときに呼ぶ関数 (run では app が HOME を短押しする関数)。
                    None (既定) なら呼ばない
    """

    paths: RunPaths
    analyzer: MiddleAnalyzer
    clock: RunClock
    run_log: RunLog
    deep_requests: DeepRequestSink
    on_stop: Callable[[str], None]
    board: TaskBoard
    trigger: MiddleTrigger
    on_press_home: Callable[[], None] | None = None


def middle_failure_reason(count: int) -> str:
    """middle の失敗が count 回続いて止まるときの理由 `middle failed <count> times in a row` を返す。"""
    return f"middle failed {count} times in a row"


class MiddleLoop:
    """run の middle のループ。run_once は 1 つのスレッドからだけ呼ぶ。"""

    def __init__(self, parts: MiddleLoopParts, settings: MiddleLoopSettings) -> None:
        """続けて失敗した回数 0 の状態で作る。

        要求の記録 MiddleRequestLog(parts.paths.middle_requests) (番号を 1 から数える) を 1 つ作り、run_once の回を
        またいで使う。
        """
        self.parts = parts
        self.settings = settings
        self._consecutive_errors = 0
        self._request_log = MiddleRequestLog(parts.paths.middle_requests)

    @property
    def consecutive_errors(self) -> int:
        """失敗が続いている回数 (成功で 0 に戻る)。"""
        return self._consecutive_errors

    def run_once(
        self,
        reasons: Sequence[MiddleCallReason],
        stop: threading.Event | None = None,
    ) -> MiddleStepResult:
        """run_middle_step(paths, analyzer, clock, window_s, deep_requests, board, reasons, trigger.notify, stop,
        request_log=__init__ で作った要求の記録) を 1 回呼び、失敗を数える。reasons はこの回に middle を呼んだ理由。

        - 成功したら、続けて失敗した回数を 0 にする
        - 失敗したら回数を 1 増やし、trigger.carry_over(reasons) で理由を次の回に持ち越し、run_log に middle_error
          {"error": 説明, "count": 続けて失敗した回数} を書く。回数が max_errors に達したら
          on_stop(middle_failure_reason(回数)) を呼ぶ
        - 結果の deep_request_errors ごとに、run_log に deep_request_error {"source": "middle", "kind", "trigger": "new_scene",
          "scene_id": new_scene の ID, "error": 説明} を書く (失敗の回数には数えず、ループも止めない)
        - 成功して、結果の state の press_home が真なら、最後に on_press_home() を呼ぶ (on_press_home が None なら呼ばない)
        結果の stopped が True なら (止める合図を見て途中で抜けた)、数えず、run_log にも書かない。
        run_middle_step の結果をそのまま返す。
        """
        parts = self.parts
        result = run_middle_step(
            parts.paths,
            parts.analyzer,
            parts.clock,
            self.settings.window_s,
            parts.deep_requests,
            parts.board,
            reasons,
            parts.trigger.notify,
            stop,
            request_log=self._request_log,
        )
        if result.stopped:
            return result
        if result.ok:
            self._consecutive_errors = 0
        else:
            self._consecutive_errors += 1
            parts.trigger.carry_over(reasons)
            parts.run_log.write(
                EVENT_MIDDLE_ERROR, error=result.error, count=self._consecutive_errors
            )
            if self._consecutive_errors >= self.settings.max_errors:
                parts.on_stop(middle_failure_reason(self._consecutive_errors))
        scene_id = None if result.new_scene is None else result.new_scene.id
        for kind, description in result.deep_request_errors:
            parts.run_log.write(
                EVENT_DEEP_REQUEST_ERROR,
                source="middle",
                kind=kind.value,
                trigger=DeepTrigger.NEW_SCENE.value,
                scene_id=scene_id,
                error=description,
            )
        if (
            result.ok
            and result.state is not None
            and result.state.press_home
            and parts.on_press_home is not None
        ):
            parts.on_press_home()
        return result

    def run(self, stop: threading.Event) -> None:
        """スレッドの本体 (テストで使う。run では app が同じ流れを段階に合わせて行う)。

        1. middle_input_builder.has_recorded_frame(paths) が True になるまで、stop.wait(FIRST_FRAME_POLL_S) で待つ
        2. reasons = trigger.first_reasons() で最初の実行の理由を決める
        3. stop が立つまで、run_once(reasons, stop) を呼び、戻った時刻 (clock.now()) を前回の応答の時刻として
           reasons = trigger.wait_next(stop, その時刻) で次の理由を待つことを繰り返す。wait_next が None を返したら戻る
        run_once の想定外の例外 (Exception) は外に出さず、on_stop(説明を含む理由) を呼んでから戻る。
        run_once の結果の stopped が True なら、待たずに戻る。
        """
        parts = self.parts
        try:
            while not stop.is_set() and not has_recorded_frame(parts.paths):
                stop.wait(FIRST_FRAME_POLL_S)
            reasons: tuple[MiddleCallReason, ...] | None = parts.trigger.first_reasons()
            while reasons is not None and not stop.is_set():
                result = self.run_once(reasons, stop)
                if result.stopped:
                    return
                reasons = parts.trigger.wait_next(stop, parts.clock.now())
        except Exception as error:
            logger.warning("middle loop failed", exc_info=True)
            parts.on_stop(f"middle loop failed: {describe_exception(error)}")


def run_middle_once(
    run_dir: Path, analyzer: MiddleAnalyzer, window_s: float
) -> MiddleStepResult:
    """保存済みの実行ディレクトリー run_dir に対して、middle を 1 回動かす (middle-once)。

    時計は、frames.jsonl の最新の t (frame_store.latest_frame_t。無ければ 0.0) に固定した run_clock.frozen_clock にする。
    キューは tasks.task_board.restore_task_board(paths) で、保存済みの tasks.json から作り直す。
    要求の記録は MiddleRequestLog(paths.middle_requests, read_last_request_number(paths.middle_requests)) にする
    (保存済みの middle/requests.jsonl の最後の番号の次から数え、同じファイルに追記する)。
    run_middle_step(paths, analyzer, その時計, window_s, deep_requests=None, board=作り直したキュー,
    reasons=("interval",), trigger_notify=None, request_log=その要求の記録) を呼んで結果を返す (run と同じ規則で
    取り消しと追加を行い、キューの部品が保存済みの実行ディレクトリーの tasks.json・tasks.md・tasks.jsonl に書き戻す。
    初めての場面の deep の要求は calls.jsonl に記録するだけで、起動しない)。run.jsonl には書かない。
    run_dir がディレクトリーでなければ FileNotFoundError にする。
    """
    paths = open_run_directory(run_dir)
    latest = latest_frame_t(paths)
    clock = frozen_clock(latest if latest is not None else 0.0)
    return run_middle_step(
        paths,
        analyzer,
        clock,
        window_s,
        deep_requests=None,
        board=restore_task_board(paths),
        reasons=("interval",),
        trigger_notify=None,
        request_log=MiddleRequestLog(
            paths.middle_requests, read_last_request_number(paths.middle_requests)
        ),
    )
