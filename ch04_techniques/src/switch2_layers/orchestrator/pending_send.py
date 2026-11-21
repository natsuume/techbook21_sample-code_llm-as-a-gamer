"""書き込み用のスレッドで始めた、セッションへの入力の書き込み 1 つ (PendingSend)。

書き込み用のスレッドが、書き終えたら finish を呼ぶ。主ループのスレッドは wait で、書き終わったかを短い間隔で
確かめる (セッションが入力を受け取らなくなって書き込みが戻らなくても、主ループのスレッドを止めないため)。
orchestrator.main_loop_session.MainLoopSession.send_turn_in_background の戻り値の型で、どのエージェントの実装でも使う
(Claude Code の実装では orchestrator.claude_stream_session.ClaudeStreamSession.send_in_background が作る)。
"""

import threading


class PendingSend:
    """send_in_background で書き始めた 1 行の書き込み。"""

    def __init__(self) -> None:
        """書き終えていない状態で作る。"""
        self._finished = threading.Event()
        self._written = False

    def finish(self, written: bool) -> None:
        """書き込みが終わったことを記録する (written は書けたら True)。書き込み用のスレッドが呼ぶ。"""
        self._written = written
        self._finished.set()

    def wait(self, timeout_s: float) -> bool | None:
        """書き込みの終わりを最大 timeout_s 秒待つ。書けたら True、書けなかったら False、まだ終わっていなければ None。"""
        if not self._finished.wait(max(timeout_s, 0.0)):
            return None
        return self._written

    @property
    def finished(self) -> bool:
        """書き込みが終わっていれば (書けたかに依らず) True。"""
        return self._finished.is_set()
