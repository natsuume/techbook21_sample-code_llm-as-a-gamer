"""middle の 1 回分の処理 (run_middle_step) と、run のループ (MiddleLoop)、middle-once (run_middle_once)。

1 回分の流れ (run_middle_step):
    1. 実行ディレクトリーのファイルだけから入力を作る (middle_input_builder.build_middle_input)
    2. MiddleAnalyzer.analyze を呼ぶ
    3. 出力を書く (middle_output_writer.write_middle_output)
    4. 初めての場面に名前が付いたら、その場面の操作説明と攻略情報の deep を、きっかけ new_scene でスケジューラーに渡す
       (middle-once では渡さず、記録だけする)
    5. 1 行を middle/calls.jsonl に書く。MiddleAnalyzer が middle_call_record.MiddleCallDetailsSource でもあれば
       (gpt-6-luna の実装)、その回の記録 (usage・概算の費用・応答など) を行に足す (失敗した回も同じ)
ループ (MiddleLoop.run) は、最初のフレームが frames.jsonl に載るのを待ってから最初の実行を始め、
前回の応答 (失敗を含む) から interval_s 秒待って次を始める。analyze が例外を出したら、前回の出力を残したまま
次の間隔で再び呼ぶ。max_errors 回続けたら on_stop で全体を止める。
止める合図は、analyze が戻ったとき (例外を含む)、出力のファイル (tasks.md・middle/state.json・notes/scene_labels.json) と
middle/calls.jsonl のそれぞれを書く直前、deep を要求する直前 (種類ごと) に確かめる。立っていたら、残りのファイル・
middle/calls.jsonl・run.jsonl に書かず、deep も呼ばずにループを抜ける (止めた後に記録を増やさない。書く順は変えず、
それまでに書いたファイルは戻さない)。
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.deep.deep_types import DeepRequestSink, DeepTrigger, DocumentKind
from switch2_layers.error_text import describe_exception
from switch2_layers.file_io import append_json_line
from switch2_layers.frames.frame_store import latest_frame_t
from switch2_layers.middle.middle_call_record import MiddleCallRecord, middle_call_row
from switch2_layers.middle.middle_input_builder import (
    build_middle_input,
    has_recorded_frame,
)
from switch2_layers.middle.middle_output_writer import (
    NEW_SCENE_DEEP_KINDS,
    write_middle_output,
)
from switch2_layers.middle.middle_types import MiddleAnalyzer, MiddleInput, MiddleState
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.records.run_log import (
    EVENT_DEEP_REQUEST_ERROR,
    EVENT_MIDDLE_ERROR,
    RunLog,
)
from switch2_layers.run_clock import RunClock, frozen_clock
from switch2_layers.run_directory import RunPaths, open_run_directory

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


def run_middle_step(
    paths: RunPaths,
    analyzer: MiddleAnalyzer,
    clock: RunClock,
    window_s: float,
    deep_requests: DeepRequestSink | None,
    stop: threading.Event | None = None,
) -> MiddleStepResult:
    """middle の 1 回分を処理する (モジュールの docstring の 1〜5)。

    stop は止める合図 (MiddleLoop.run が渡す)。None なら合図を確かめない。

    1. now_t = clock.now() で build_middle_input(paths, now_t, window_s)
    2. t_sent = clock.now() で analyze を呼び、戻ったら (例外でも) t_received = clock.now()。ここで stop を確かめる
    3. write_middle_output(paths, 入力, 出力, generated_t=t_received, stop) で出力を書く:
       notes/scenes/<場面ID>/tasks.md → middle/state.json → notes/scene_labels.json (新しい場面のときだけ)。
       それぞれのファイルを書く直前に stop を確かめる (立っていたら write_middle_output が None を返す)
    4. new_scene があり deep_requests が None でなければ、NEW_SCENE_DEEP_KINDS の順に、request の直前に stop を確かめてから
       deep_requests.request(種類, DeepTrigger.NEW_SCENE, new_scene.id) を呼ぶ。request が例外 (Exception) を出したら、
       外に出さずに (種類, 説明) を deep_request_errors に足し、残りの種類の要求を続ける
    5. middle/calls.jsonl に middle_call_record.middle_call_row_with_details(記録, analyze の記録) の行を書く
       (書く直前に stop を確かめる。deep_requests_sent は deep_requests が None でなく、要求を渡したら True。
       要求が例外を出した場合も True)
    analyze の記録は、analyze が戻った直後 (例外を含む) に middle_call_record.take_analyzer_call_details(analyzer) で
    受け取る (analyzer が MiddleCallDetailsSource でなければ空。空なら行は middle_call_row と同じ)。
    1〜3 の例外 (Exception) は外に出さず、error の行を calls.jsonl に書いて (書く直前に stop を確かめる)
    ok=False の結果を返す (middle/state.json は、書く前に失敗したなら前回の出力のまま)。error の行にも analyze の記録を
    足す (analyze の失敗と、analyze の後の 3 の失敗。1 で失敗して analyze を呼ばなかった回は、記録を受け取らずに空とする)。
    stop を確かめたときに立っていたら、そこで STOPPED_STEP_RESULT を返し、残りのファイル (tasks.md・middle/state.json・
    notes/scene_labels.json のうちまだ書いていないもの)・deep の要求・calls.jsonl の行を書かない。書く順は変えず、
    それまでに書いたファイルは戻さない。analyze が戻ったときに立っていたら、何も書かず deep も呼ばない。
    """
    now_t = clock.now()
    t_sent = now_t
    middle_input: MiddleInput | None = None
    try:
        middle_input = build_middle_input(paths, now_t, window_s)
        t_sent = clock.now()
        output = analyzer.analyze(middle_input)
    except Exception as error:
        t_received = clock.now()
        if _is_stopped(stop):
            return STOPPED_STEP_RESULT
        logger.debug("middle analyze failed", exc_info=True)
        return _record_failure(paths, t_sent, t_received, middle_input, error, stop)
    t_received = clock.now()
    if _is_stopped(stop):
        return STOPPED_STEP_RESULT
    try:
        # tasks.md・middle/state.json・notes/scene_labels.json のそれぞれを書く直前に stop を確かめる
        write_result = write_middle_output(
            paths, middle_input, output, generated_t=t_received, stop=stop
        )
    except Exception as error:
        logger.debug("writing the middle output failed", exc_info=True)
        return _record_failure(paths, t_sent, t_received, middle_input, error, stop)
    if write_result is None:
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
    )
    if _is_stopped(stop):
        return STOPPED_STEP_RESULT
    append_json_line(paths.middle_calls, middle_call_row(record))
    return MiddleStepResult(
        ok=True,
        state=write_result.state,
        new_scene=new_scene,
        deep_requests=tuple(deep_kinds),
        error=None,
        deep_request_errors=tuple(request_errors),
    )


def _record_failure(
    paths: RunPaths,
    t_sent: float,
    t_received: float,
    middle_input: MiddleInput | None,
    error: Exception,
    stop: threading.Event | None,
) -> MiddleStepResult:
    """失敗した回の行を calls.jsonl に書き、ok=False の結果を返す。

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
    )
    append_json_line(paths.middle_calls, middle_call_row(record))
    return MiddleStepResult(
        ok=False, state=None, new_scene=None, deep_requests=(), error=description
    )


def _is_stopped(stop: threading.Event | None) -> bool:
    """止める合図 stop が立っていれば True。stop が None (合図を確かめない) なら False。"""
    return stop is not None and stop.is_set()


@dataclass(frozen=True)
class MiddleLoopSettings:
    """middle のループの設定。

    interval_s   前回の応答から次を始めるまでの秒数 (--middle-interval)
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
    """

    paths: RunPaths
    analyzer: MiddleAnalyzer
    clock: RunClock
    run_log: RunLog
    deep_requests: DeepRequestSink
    on_stop: Callable[[str], None]


def middle_failure_reason(count: int) -> str:
    """middle の失敗が count 回続いて止まるときの理由 `middle failed <count> times in a row` を返す。"""
    return f"middle failed {count} times in a row"


class MiddleLoop:
    """run の middle のループ。run_once は 1 つのスレッドからだけ呼ぶ。"""

    def __init__(self, parts: MiddleLoopParts, settings: MiddleLoopSettings) -> None:
        """続けて失敗した回数 0 の状態で作る。"""
        self.parts = parts
        self.settings = settings
        self._consecutive_errors = 0

    @property
    def consecutive_errors(self) -> int:
        """失敗が続いている回数 (成功で 0 に戻る)。"""
        return self._consecutive_errors

    def run_once(self, stop: threading.Event | None = None) -> MiddleStepResult:
        """run_middle_step(paths, analyzer, clock, window_s, deep_requests, stop) を 1 回呼び、失敗を数える。

        - 成功したら、続けて失敗した回数を 0 にする
        - 失敗したら回数を 1 増やし、run_log に middle_error {"error": 説明, "count": 続けて失敗した回数} を書く。
          回数が max_errors に達したら on_stop(middle_failure_reason(回数)) を呼ぶ
        - 結果の deep_request_errors ごとに、run_log に deep_request_error {"source": "middle", "kind", "trigger": "new_scene",
          "scene_id": new_scene の ID, "error": 説明} を書く (失敗の回数には数えず、ループも止めない)
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
            stop,
        )
        if result.stopped:
            return result
        if result.ok:
            self._consecutive_errors = 0
        else:
            self._consecutive_errors += 1
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
        return result

    def run(self, stop: threading.Event) -> None:
        """スレッドの本体。

        1. middle_input_builder.has_recorded_frame(paths) が True になるまで、stop.wait(FIRST_FRAME_POLL_S) で待つ
        2. stop が立つまで、run_once(stop) を呼んでから stop.wait(interval_s) で待つことを繰り返す
        run_once の想定外の例外 (Exception) は外に出さず、on_stop(説明を含む理由) を呼んでから戻る。
        run_once の結果の stopped が True なら、待たずに戻る。
        """
        try:
            while not stop.is_set() and not has_recorded_frame(self.parts.paths):
                stop.wait(FIRST_FRAME_POLL_S)
            while not stop.is_set():
                result = self.run_once(stop)
                if result.stopped:
                    return
                stop.wait(self.settings.interval_s)
        except Exception as error:
            logger.warning("middle loop failed", exc_info=True)
            self.parts.on_stop(f"middle loop failed: {describe_exception(error)}")


def run_middle_once(
    run_dir: Path, analyzer: MiddleAnalyzer, window_s: float
) -> MiddleStepResult:
    """保存済みの実行ディレクトリー run_dir に対して、middle を 1 回動かす (middle-once)。

    時計は、frames.jsonl の最新の t (frame_store.latest_frame_t。無ければ 0.0) に固定した run_clock.frozen_clock にする。
    run_middle_step(paths, analyzer, その時計, window_s, deep_requests=None) を呼んで結果を返す
    (初めての場面の deep の要求は calls.jsonl に記録するだけで、起動しない)。run.jsonl には書かない。
    run_dir がディレクトリーでなければ FileNotFoundError にする。
    """
    paths = open_run_directory(run_dir)
    latest = latest_frame_t(paths)
    clock = frozen_clock(latest if latest is not None else 0.0)
    return run_middle_step(paths, analyzer, clock, window_s, deep_requests=None)
