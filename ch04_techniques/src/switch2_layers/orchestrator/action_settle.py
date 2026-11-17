"""主ループの操作の列が終わって画面が落ち着いたかの決め方 (actions_settled) と、それを待つ処理 (wait_for_settled_actions)。

主ループは、前のターンで空でない actions を当てたら、次の 2 つがそろってから次の入力を作る (操作の結果が映った画面で
判断するため):
    時計の条件      その操作の列 (orchestrator_events.MainActionsRun) が終わり、最後にボタンとスティックを離してから
                    ACTIONS_SETTLE_S 秒たった (settled_time)
    フレームの条件  最後に離した時刻 + ACTIONS_SETTLE_S (ミリ秒に丸めた値) より厳密に後に撮った一定間隔のフレームが、
                    frames/latest/ (frames.latest_frames.LatestFrames) に入っている (settled_frame_after)。
                    監視が Jev に問うフレームの条件 (orchestrator_controller.MONITOR_FRAME_SETTLE_S) と同じ考え方で、
                    キャプチャーとゲームの反応の遅れの分、操作の前の画面のフレームで判断しないため。
                    押す操作の無い列 (released_t が None。wait だけの列) では、この条件を使わない
待つのは主ループの操作の列だけで、監視の操作の列は待たない (MainActionsRun は主ループの列だけの時刻)。
時刻はすべて実行の経過秒 (run_clock.RunClock の t)。待ちは時計と停止の合図で作り、実時間の長さに頼らない (テストでは
時間を進められる時計と、フレームの時刻を返す偽の関数を渡す)。
"""

import threading
from collections.abc import Callable

from switch2_layers.orchestrator.orchestrator_events import MainActionsRun
from switch2_layers.run_clock import RunClock, round_to_milliseconds

# 操作の列の最後にボタンとスティックを離してから、次の入力を作るまでに置く秒数 (画面が操作に反応して落ち着くまで)
ACTIONS_SETTLE_S = 0.3


def settled_time(run: MainActionsRun) -> float | None:
    """時計の条件: run の列が終わって落ち着く時刻 (この時刻から次の入力を作ってよい) を返す。列がまだ終わっていなければ None。

    - run.ended_t が None (まだ終わっていない): None
    - run.released_t が None (押す操作の無い列): run.ended_t
    - そうでなければ max(run.ended_t, round_to_milliseconds(run.released_t + ACTIONS_SETTLE_S))
      (最後の操作が ACTIONS_SETTLE_S 秒より長い wait なら、列が終わった時刻)
    足した値はミリ秒に丸める (時計の t はミリ秒に丸めた値なので、丸めずに比べると境界ちょうどの時刻を逃すことがある)。
    """
    if run.ended_t is None:
        return None
    if run.released_t is None:
        return run.ended_t
    return max(run.ended_t, round_to_milliseconds(run.released_t + ACTIONS_SETTLE_S))


def settled_frame_after(run: MainActionsRun) -> float | None:
    """フレームの条件: 次の入力を作る前に、この時刻より厳密に後に撮ったフレームが要る時刻を返す。

    run.released_t があれば round_to_milliseconds(run.released_t + ACTIONS_SETTLE_S)。run.released_t が None (押す操作の
    無い列) なら None (フレームの条件を使わない)。列が終わったか (ended_t) は見ない。
    """
    if run.released_t is None:
        return None
    return round_to_milliseconds(run.released_t + ACTIONS_SETTLE_S)


def actions_settled(
    run: MainActionsRun, now_t: float, latest_frame_t: float | None
) -> bool:
    """run の列が終わって画面が落ち着き、次の入力を作ってよいなら True を返す (I/O なし)。

    now_t は今の時刻、latest_frame_t は frames/latest/ の最新のフレームの時刻 (フレームが無ければ None)。
    次のすべてに当たるときだけ True:
        - settled_time(run) が None でなく、now_t 以上 (時計の条件)
        - settled_frame_after(run) が None (押す操作の無い列) か、latest_frame_t が None でなく settled_frame_after(run)
          より大きい (フレームの条件。ちょうど同じ時刻のフレームは通さない)
    """
    settle_t = settled_time(run)
    if settle_t is None or now_t < settle_t:
        return False
    frame_after = settled_frame_after(run)
    if frame_after is None:
        return True
    return latest_frame_t is not None and latest_frame_t > frame_after


def wait_for_settled_actions(
    read_run: Callable[[], MainActionsRun | None],
    read_latest_frame_t: Callable[[], float | None],
    clock: RunClock,
    stop: threading.Event,
    poll_s: float,
) -> MainActionsRun | None:
    """主ループの操作の列が終わって画面が落ち着くまで待ち、落ち着いた列の時刻を返す。

    read_run は OrchestratorController.main_actions_run、read_latest_frame_t は frames/latest/ の最新のフレームの時刻を
    返す関数 (主ループでは frames.latest_frames.newest_frame_t(latest_frames()))。次の 1〜4 を繰り返す:
        1. stop が立っていれば、None を返す (待つのをやめる。フレームが来ないまま待っているときも同じ。呼び出し側は
           stop を確かめて止まる)
        2. run = read_run()。None (主ループの列が無い) なら、待たずに None を返す
        3. actions_settled(run, clock.now(), read_latest_frame_t()) なら、run を返す
        4. stop.wait(poll_s) で最大 poll_s 秒待つ
    run・時計・フレームの時刻は、回りごとに読み直す。
    """
    while True:
        if stop.is_set():
            return None
        run = read_run()
        if run is None:
            return None
        if actions_settled(run, clock.now(), read_latest_frame_t()):
            return run
        stop.wait(poll_s)
