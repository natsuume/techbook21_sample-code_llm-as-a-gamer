"""3 層をすべて依存注入で組み立てて、1 つのプロセスのスレッドとして起動・停止する (LayersApp)。

実機の部品 (キャプチャーの読み取り元・ブリッジ) と各層 (surface・middle・deep) は LayersParts で受け取る。
cli の run は実機の部品とフェイクの層を渡し、テストはフェイクのキャプチャー・ブリッジ・層を渡して数秒動かす。

止まる条件 (どれも、操作を中立にして出力を止め、全体を止めて、run.jsonl の stop に理由を書く):
    - Ctrl+C (wait の中の KeyboardInterrupt)                          REASON_INTERRUPTED
    - キャプチャーの最新フレームが 2 秒以上古い・出力が終わった・起動から 10 秒たっても最初のフレームが無い
                                                                    hardware.capture_watcher の理由
    - ブリッジの復旧が失敗した (bridge_state() が "failed")             REASON_BRIDGE_FAILED
    - surface か middle が続けて失敗した                                surface_loop・middle_loop の理由
ブリッジの復旧中は、surface が要求を送らず、操作を中立にして待つ (surface_loop)。

スレッド:
    キャプチャーの読み取り (CaptureSource)、画像の書き出し (FrameStore)、短押しのタイマー (PadOutput)、
    surface のループ、middle のループ、deep の自動の呼び出し (DeepScheduler) とジョブ。
    wait を呼んだスレッド (run ではメインスレッド) が、止まる条件を MONITOR_INTERVAL_S ごとに確かめる。
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from switch2_layers.deep.deep_types import DeepRunner
from switch2_layers.hardware.capture_source import LatestFrameReader
from switch2_layers.hardware.pad_output import ReportSender
from switch2_layers.middle.middle_types import MiddleAnalyzer
from switch2_layers.run_directory import RunPaths
from switch2_layers.settings import RunSettings
from switch2_layers.surface.surface_types import SurfaceDecider

REASON_INTERRUPTED = "interrupted"
REASON_BRIDGE_FAILED = "bridge recovery failed"
REASON_CLOSED = "closed"  # 止まる条件より前に close が呼ばれた
MONITOR_INTERVAL_S = 0.1  # 止まる条件を確かめる間隔 (秒)
# 終了時に surface と middle のループのスレッドの終わりを待つ上限 (秒)
LOOP_JOIN_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class LayersParts:
    """LayersApp に注入する部品。

    frame_reader    キャプチャーの最新フレームの読み取り元 (run では hardware.capture_source.SwitchCaptureReader)
    report_sender   レポートの送信先 (run では switch2_remote.bridge_supervisor.BridgeSupervisor)
    bridge_state    ブリッジの状態 ("ok" / "recovering" / "failed") を返す関数 (run では BridgeSupervisor.state を読む)
    surface         surface の判断
    middle          middle の分析
    deep            deep のジョブの実行
    monotonic       単調な時計 (既定 time.monotonic)。キャプチャーのフレームの時刻と同じ時計にする
    local_now       実行ディレクトリーの名前に使うローカルの時刻を返す関数 (既定 datetime.now)
    """

    frame_reader: LatestFrameReader
    report_sender: ReportSender
    bridge_state: Callable[[], str]
    surface: SurfaceDecider
    middle: MiddleAnalyzer
    deep: DeepRunner
    monotonic: Callable[[], float] = time.monotonic
    local_now: Callable[[], datetime] = datetime.now


class LayersApp:
    """3 層を組み立てて動かす。start → wait → close の順に呼ぶ (run はこれをまとめたもの)。

    settings   run の起動引数
    parts      注入する部品
    sleep      wait の見張りの間隔の待ちに使う関数 (既定 time.sleep)。テストでは KeyboardInterrupt を出す関数を渡せる
    """

    def __init__(
        self,
        settings: RunSettings,
        parts: LayersParts,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """属性を持つ。ディレクトリーとスレッドは start で作る。"""
        raise NotImplementedError

    def start(self) -> RunPaths:
        """実行ディレクトリーを作り、部品を組み立ててスレッドを起動し、実行ディレクトリーの RunPaths を返す。

        1. RunClock(parts.monotonic) を作る (ここが t = 0)
        2. run_directory.create_run_directory(settings.runs_dir, parts.local_now()) と
           write_initial_notes(paths, buttons.allowed_buttons(settings.allow_home))
        3. RunLog を作り、start {"run_dir": 実行ディレクトリー, "args": settings.run_settings_to_json(settings)} を書く
        4. OperationLog と PadOutput(report_sender, 押せるボタン, on_operation=OperationLog.write) を作る
        5. FrameBuffer、FrameStore(interval_fps, interval_width)、CaptureSource(frame_reader, …)、CaptureWatcher を作る
        6. DeepScheduler (current_scene_id は middle/state.json の scene_id、latest_frame_t は frames.jsonl の最後の t、
           timeout_s は deep_timeout_s) を作って start する (タイムアウトと自動の呼び出しの判定のスレッド)
        7. SurfaceLoop (max_errors = max_surface_errors) と MiddleLoop (interval_s・window_s・max_errors) を作り、
           それぞれのスレッドを起動する。on_stop には request_stop を渡す
        2 回目以降の呼び出しは RuntimeError にする。
        """
        raise NotImplementedError

    @property
    def paths(self) -> RunPaths | None:
        """実行ディレクトリーの RunPaths。start の前は None。"""
        raise NotImplementedError

    @property
    def stop_reason(self) -> str | None:
        """止まった理由 (最初に request_stop に渡された理由)。まだ止まっていなければ None。"""
        raise NotImplementedError

    def request_stop(self, reason: str) -> None:
        """全体を止めるよう求める。どのスレッドから呼んでもよい。

        最初の呼び出しだけが理由を決める (2 回目以降の理由は無視する)。最初の呼び出しで、すぐに PadOutput.stop_output()
        (操作を中立にし、以後の操作を送らない) を呼び、ループの停止の合図を立てる。後片付けは close で行う。
        """
        raise NotImplementedError

    def wait(self) -> str:
        """止まるまで待ち、止まった理由を返す。

        request_stop が呼ばれるまで、MONITOR_INTERVAL_S ごとに (sleep で待って) 次を確かめる:
            - CaptureWatcher.check_once() が理由を返したら request_stop(その理由)
            - bridge_state() が "failed" なら request_stop(REASON_BRIDGE_FAILED)
        待っている間の KeyboardInterrupt (Ctrl+C) は外に出さず、request_stop(REASON_INTERRUPTED) にする。
        """
        raise NotImplementedError

    def close(self) -> None:
        """全体を止めて後片付けをする。止まる条件より前に呼ばれたら、request_stop(REASON_CLOSED) と同じに扱う。

        1. ループの停止の合図を立て、surface と middle のループのスレッドの終わりを最大 LOOP_JOIN_TIMEOUT_S 秒ずつ待つ
        2. DeepScheduler.close() (以後の要求を捨て、実行中の deep のジョブを stop し、5 秒たっても終わらなければ kill する)
        3. CaptureSource.close()、FrameStore.close()
        4. run_log に frames_dropped {"count": FrameStore.dropped} を書き、続けて stop {"reason": 止まった理由} を書く
        5. PadOutput.close()
        実機の部品 (キャプチャー・ブリッジ) は閉じない (作った側が閉じる)。2 回目以降の呼び出しは何もしない。
        """
        raise NotImplementedError

    def run(self) -> str:
        """start → wait → close を行い、止まった理由を返す。wait の途中の例外でも close を呼ぶ。"""
        raise NotImplementedError
