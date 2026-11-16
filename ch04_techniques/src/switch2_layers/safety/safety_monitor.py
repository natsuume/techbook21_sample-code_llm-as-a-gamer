"""安全の監視 (SafetyMonitor): 実行を始めるときの確かめと、surface とは別のスレッドの定期の確かめ。

surface の判断に依らず、キャプチャーの最新のフレーム 1 枚を SafetyChecker に送り、HOME メニュー・ユーザー選択・
オンライン・購入・本体の設定などの画面かを確かめる。止めるかの判定は safety.safety_judge で行い、止めるときは
on_stop(理由) を呼ぶ (app.LayersApp.request_stop。操作を中立にして以後の操作を送らず、3 層を止める)。

スレッドの流れ (run):
    1. 最初のフレームが届くまで、要求を送らずに待つ
    2. 実行を始めるときの確かめを 1 回行う。止まる条件に当たったら on_stop を呼んで終わる。当たらなければ
       on_start_passed を呼ぶ (app は、これを合図に surface と middle のループを始める)
    3. 止める合図が立つまで、settings.interval_s 秒ごとに監視の確かめを行う。前の確かめの応答を待ってから次を送る
       (safety_judge.next_check_wait_s)。止まる条件に当たったら on_stop を呼んで終わる
ブリッジの状態は見ない (ブリッジの復旧中も監視を続ける)。
スレッドが想定外の例外で終わるときも、on_stop(`safety monitor failed: <例外の説明>`) で全体を止める
(安全の監視が動かないまま操作を続けないため。safety_monitor_failure_reason)。
確かめのたびに safety.jsonl に 1 行書く (safety.safety_record)。止める合図は SafetyChecker.check が戻ったときに確かめ、
立っていたら、その回の行を書かず、判定もせずに抜ける (止めた後に記録を増やさない)。
テストでは step を 1 回ずつ呼び、待ちは戻り値の wait_s で確かめる。
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY
from switch2_layers.run_clock import RunClock
from switch2_layers.safety.safety_types import SafetyChecker

FIRST_FRAME_POLL_S = 0.05  # 最初のフレームがまだ無いときに、確かめ直すまで待つ秒数
# 確かめに送る画像の幅の上限 (これより広いフレームは縮小する)
SAFETY_IMAGE_WIDTH = 1280


def safety_monitor_failure_reason(error: BaseException) -> str:
    """安全の監視のスレッドが想定外の例外 error で止まるときの理由 `safety monitor failed: <例外の説明>` を返す。"""
    raise NotImplementedError


def build_safety_image(
    frame: CapturedFrame, image_width: int, jpeg_quality: int
) -> bytes:
    """frame を、確かめに送る JPEG のバイト列にする。時刻や box は書き込まない。

    frames.image_encoding の nv12_to_bgr → shrink_to_width(image_width) → encode_jpeg(jpeg_quality) の jpeg。
    """
    raise NotImplementedError


class SafetyStepOutcome(StrEnum):
    """1 回の step の結果の種類。"""

    NO_FRAME = "no_frame"  # まだフレームが無く、確かめなかった。行は書かない
    PASSED = "passed"  # 答えがあり、止まる条件に当たらなかった。行を書いた
    FAILED = (
        "failed"  # 監視の確かめが失敗したが、止まる条件に当たらなかった。行を書いた
    )
    STOP = "stop"  # 止まる条件に当たり、on_stop を呼んだ。行を書いた
    STOPPED = "stopped"  # 止める合図を見て途中で抜けた。行を書かず、判定もしていない


@dataclass(frozen=True)
class SafetyStepResult:
    """1 回の step の結果。

    outcome       結果の種類
    wait_s        次の step までに待つ秒数
    row           safety.jsonl に書いた行。書かなかったら None
    stop_reason   on_stop に渡した理由。呼ばなかったら None
    """

    outcome: SafetyStepOutcome
    wait_s: float
    row: dict[str, object] | None
    stop_reason: str | None


@dataclass(frozen=True)
class SafetyMonitorSettings:
    """安全の監視の設定。

    interval_s     監視の確かめを送る間隔 (秒) (--safety-interval)
    threshold      安全の問いのしきい値 (--threshold-safety)
    max_errors     監視の確かめの失敗がこの回数続いたら止める (--max-safety-errors)
    allow_home     --allow-home。True なら home_menu と user_select の問いでは止めない
    image_width    送る画像の幅の上限
    jpeg_quality   送る画像の JPEG の品質
    """

    interval_s: float
    threshold: float
    max_errors: int
    allow_home: bool
    image_width: int = SAFETY_IMAGE_WIDTH
    jpeg_quality: int = DEFAULT_JPEG_QUALITY


@dataclass(frozen=True)
class SafetyMonitorParts:
    """安全の監視が使う部品。

    checker           画面を確かめるもの
    latest_frame      キャプチャーの最新のフレームを返す関数 (run では FrameBuffer.latest)。まだ無ければ None
    clock             実行の経過秒の時計
    safety_log        safety.jsonl のパス (run_directory.RunPaths.safety_log)
    on_stop           全体を止める関数 (理由の文字列を受け取る)
    on_start_passed   実行を始めるときの確かめで止まる条件に当たらなかったときに、1 回だけ呼ぶ関数
    """

    checker: SafetyChecker
    latest_frame: Callable[[], CapturedFrame | None]
    clock: RunClock
    safety_log: Path
    on_stop: Callable[[str], None]
    on_start_passed: Callable[[], None]


class SafetyMonitor:
    """実行を始めるときの確かめと、監視の確かめを行う。step は 1 つのスレッドからだけ呼ぶ。"""

    def __init__(
        self, parts: SafetyMonitorParts, settings: SafetyMonitorSettings
    ) -> None:
        """実行を始めるときの確かめをまだ終えていない状態で作る。監視の判定は safety_judge.MonitorJudge で行う。"""
        self.parts = parts
        self.settings = settings

    @property
    def start_passed(self) -> bool:
        """実行を始めるときの確かめで、止まる条件に当たらなかったら True。"""
        raise NotImplementedError

    def step(self, stop: threading.Event | None = None) -> SafetyStepResult:
        """確かめを 1 回行い、結果を返す (待ちはしない)。

        stop は止める合図 (run が渡す)。None なら合図を確かめない。

        1. frame = latest_frame()。None なら NO_FRAME (wait_s = FIRST_FRAME_POLL_S) を返す
        2. 段階は、start_passed が False なら START、True なら MONITOR
        3. jpeg = build_safety_image(frame, image_width, jpeg_quality)。t_sent = clock.now() で checker.check(jpeg) を
           呼び、safety_judge.validate_probabilities(答えの probabilities) で確かめる。戻ったら (例外でも)
           t_received = clock.now()。check か確かめが例外 (Exception) を出したら、その回は失敗とする
        4. stop が立っていたら STOPPED (wait_s = 0.0) を返す (行を書かず、判定もしない)
        5. safety.jsonl に SafetyRecord(t_sent, t_received, frame.t, 段階, 確率 (失敗なら None), usage (失敗なら None),
           error (成功なら None)) の行を書く (safety_record.safety_row。file_io.append_json_line)
        6. 判定:
           START    safety_judge.start_stop_reason(確率 (失敗なら None), threshold, allow_home)。理由があれば
                    on_stop(理由) を呼んで STOP を返す。無ければ start_passed を True にし、on_start_passed() を呼んで
                    PASSED を返す
           MONITOR  成功なら MonitorJudge.record_answer(確率)、失敗なら MonitorJudge.record_failure()。理由があれば
                    on_stop(理由) を呼んで STOP を返す。無ければ PASSED (成功) か FAILED (失敗) を返す
        STOP の wait_s は 0.0、PASSED と FAILED の wait_s は safety_judge.next_check_wait_s(interval_s, t_sent, t_received)。
        画像の作成・行の書き込み・on_stop・on_start_passed の例外は外に出す (run が全体を止める)。
        """
        raise NotImplementedError

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで step を繰り返し、各 step の後に stop.wait(wait_s) で待つ (スレッドの本体)。

        step が STOP か STOPPED を返したら、待たずに戻る。
        step の想定外の例外 (Exception) は外に出さず、on_stop(safety_monitor_failure_reason(例外)) を呼んでから戻る
        (安全の監視が動かないまま操作を続けないため)。
        """
        raise NotImplementedError
