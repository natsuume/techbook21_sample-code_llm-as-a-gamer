"""middle の 1 回分の処理 (run_middle_step) と、run のループ (MiddleLoop)、middle-once (run_middle_once)。

1 回分の流れ (run_middle_step):
    1. 実行ディレクトリーのファイルだけから入力を作る (middle_input_builder.build_middle_input)
    2. MiddleAnalyzer.analyze を呼ぶ
    3. 出力を書く (middle_output_writer.write_middle_output)
    4. 初めての場面に名前が付いたら、その場面の操作説明と攻略情報の deep を、きっかけ new_scene でスケジューラーに渡す
       (middle-once では渡さず、記録だけする)
    5. 1 行を middle/calls.jsonl に書く
ループ (MiddleLoop.run) は、最初のフレームが frames.jsonl に載るのを待ってから最初の実行を始め、
前回の応答 (失敗を含む) から interval_s 秒待って次を始める。analyze が例外を出したら、前回の出力を残したまま
次の間隔で再び呼ぶ。max_errors 回続けたら on_stop で全体を止める。
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.deep.deep_types import DeepRequestSink, DocumentKind
from switch2_layers.middle.middle_types import MiddleAnalyzer, MiddleState
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

FIRST_FRAME_POLL_S = 0.2  # 最初のフレームが frames.jsonl に載ったかを確かめる間隔 (秒)


@dataclass(frozen=True)
class MiddleStepResult:
    """middle の 1 回分の結果。

    ok              analyze と出力の書き込みに成功したら True
    state           書いた middle/state.json の値。失敗したら None
    new_scene       一覧に新しく足した場面。無ければ None
    deep_requests   初めての場面のために deep を呼ぶ文書の種類 (new_scene があれば controls と tips。無ければ空)
    error           失敗の説明。成功なら None
    """

    ok: bool
    state: MiddleState | None
    new_scene: SceneLabel | None
    deep_requests: tuple[DocumentKind, ...]
    error: str | None


def run_middle_step(
    paths: RunPaths,
    analyzer: MiddleAnalyzer,
    clock: RunClock,
    window_s: float,
    deep_requests: DeepRequestSink | None,
) -> MiddleStepResult:
    """middle の 1 回分を処理する (モジュールの docstring の 1〜5)。

    1. now_t = clock.now() で build_middle_input(paths, now_t, window_s)
    2. t_sent = clock.now() で analyze を呼び、戻ったら (例外でも) t_received = clock.now()
    3. write_middle_output(paths, 入力, 出力, generated_t=t_received)
    4. new_scene があり deep_requests が None でなければ、NEW_SCENE_DEEP_KINDS の順に
       deep_requests.request(種類, DeepTrigger.NEW_SCENE, new_scene.id) を呼ぶ
    5. middle/calls.jsonl に middle_call_record.middle_call_row の行を書く
       (deep_requests_sent は deep_requests が None でなく、要求を渡したら True)
    1〜3 の例外 (Exception) は外に出さず、error の行を calls.jsonl に書いて ok=False の結果を返す
    (middle/state.json は、書く前に失敗したなら前回の出力のまま)。
    """
    raise NotImplementedError


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
    raise NotImplementedError


class MiddleLoop:
    """run の middle のループ。run_once は 1 つのスレッドからだけ呼ぶ。"""

    def __init__(self, parts: MiddleLoopParts, settings: MiddleLoopSettings) -> None:
        """続けて失敗した回数 0 の状態で作る。"""
        raise NotImplementedError

    @property
    def consecutive_errors(self) -> int:
        """失敗が続いている回数 (成功で 0 に戻る)。"""
        raise NotImplementedError

    def run_once(self) -> MiddleStepResult:
        """run_middle_step(paths, analyzer, clock, window_s, deep_requests) を 1 回呼び、失敗を数える。

        - 成功したら、続けて失敗した回数を 0 にする
        - 失敗したら回数を 1 増やし、run_log に middle_error {"error": 説明, "count": 続けて失敗した回数} を書く。
          回数が max_errors に達したら on_stop(middle_failure_reason(回数)) を呼ぶ
        run_middle_step の結果をそのまま返す。
        """
        raise NotImplementedError

    def run(self, stop: threading.Event) -> None:
        """スレッドの本体。

        1. middle_input_builder.has_recorded_frame(paths) が True になるまで、stop.wait(FIRST_FRAME_POLL_S) で待つ
        2. stop が立つまで、run_once を呼んでから stop.wait(interval_s) で待つことを繰り返す
        run_once の想定外の例外 (Exception) は外に出さず、on_stop(説明を含む理由) を呼んでから戻る。
        """
        raise NotImplementedError


def run_middle_once(
    run_dir: Path, analyzer: MiddleAnalyzer, window_s: float
) -> MiddleStepResult:
    """保存済みの実行ディレクトリー run_dir に対して、middle を 1 回動かす (middle-once)。

    時計は、frames.jsonl の最新の t (frame_store.latest_frame_t。無ければ 0.0) に固定した run_clock.frozen_clock にする。
    run_middle_step(paths, analyzer, その時計, window_s, deep_requests=None) を呼んで結果を返す
    (初めての場面の deep の要求は calls.jsonl に記録するだけで、起動しない)。run.jsonl には書かない。
    run_dir がディレクトリーでなければ FileNotFoundError にする。
    """
    raise NotImplementedError
