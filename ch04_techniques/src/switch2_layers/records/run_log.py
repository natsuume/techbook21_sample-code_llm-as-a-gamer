"""実行の記録 run.jsonl。1 件を {"t": 秒, "event": 名前, ...} の 1 行で追記する。

surface・middle・deep のスケジューラー・deep のジョブ・Orchestrator 型の主ループ・委任型の委任のループ・終了処理の
各スレッドから書くため、追記は 1 つのロックの中で行う。
"""

import threading
from collections.abc import Callable
from pathlib import Path

from switch2_layers.file_io import append_json_line
from switch2_layers.run_clock import RunClock

# event の名前
EVENT_START = "start"  # 起動。args (起動引数) と run_dir を含む
EVENT_STOP = "stop"  # 停止。reason (止まった理由) を含む
EVENT_SURFACE_ERROR = "surface_error"  # surface の decide の失敗
EVENT_MIDDLE_ERROR = "middle_error"  # middle の 1 回分の失敗
EVENT_DEEP_START = "deep_start"  # deep のジョブの開始
# deep のジョブに止める要求を出した (タイムアウト・終了時)
EVENT_DEEP_STOP = "deep_stop"
EVENT_DEEP_END = "deep_end"  # deep のジョブの終了 (kill を含む)
EVENT_DEEP_SKIPPED = "deep_skipped"  # deep を呼ぶ要求を捨てた
# 同じ文書の deep のジョブが実行中のため、deep を呼ぶ要求を保留した。kind・scene_id・trigger・since_last_start_s を含む
EVENT_DEEP_PENDING = "deep_pending"
# deep を要求したときの失敗。surface・middle・自動の呼び出し・画面の停滞の計測・本体の画面の段階の判定が deep を
# 要求したときに、要求の受け口 (DeepScheduler.request) が例外を出した (source は "surface" / "middle" / "auto" /
# "progress" / "system")、または保留中の要求を
# 起動するときにジョブのディレクトリーを用意できなかった (source は "pending")。source・kind・trigger・scene_id・error を
# 含む。どちらも例外を外に出さず、ループも止めない
EVENT_DEEP_REQUEST_ERROR = "deep_request_error"
EVENT_FRAMES_DROPPED = "frames_dropped"  # 画像の保存を捨てた件数 (終了時)
# run の段階が変わった。phase (移った後の段階) と reason (変化の理由) を含む (system_screen.run_phase)
EVENT_PHASE = "phase"
# 片付けが終わった。result (system_screen.run_phase.CleanUpResult の値) を含む
EVENT_CLEAN_UP_END = "clean_up_end"
# Orchestrator 型の主ループのセッションの開始。session (番号) を含む
EVENT_ORCHESTRATOR_SESSION_START = "orchestrator_session_start"
# 主ループのセッションの終了。session・reason・returncode・usage_tokens を含む
EVENT_ORCHESTRATOR_SESSION_END = "orchestrator_session_end"
# 主ループの JSON の誤り。session・count (続けて誤った回数)・errors (誤りの説明) を含む
EVENT_ORCHESTRATOR_DECISION_ERROR = "orchestrator_decision_error"
# 委任型の委任の始まり。tasks・cautions・choices ([{"name", "description"}, ...]) を含む
EVENT_DELEGATE_START = "delegate_start"
# 委任型の委任の終わり。reason (jev_return・main_stop・errors・stop) と detail (理由の説明) を含む
EVENT_DELEGATE_END = "delegate_end"


class RunLog:
    """run.jsonl に 1 件ずつ追記する。

    path    run.jsonl のパス (親のディレクトリーは呼び出し側が作る)
    clock   t を付ける時計
    echo    要約の 1 行 (`<t> <event> <キー>=<値> ...`) を受け取る関数。None (既定) なら画面に出さない
    close の後の write は何も書かずに戻る (例外にしない)。終了処理が stop を書いて close した後に、止まり切らなかった
    スレッドが書こうとしても、stop の後ろに行が増えないようにするため。
    """

    def __init__(
        self,
        path: Path,
        clock: RunClock,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        """属性を持つ。ファイルはまだ作らない。"""
        self.path = path
        self.clock = clock
        self.echo = echo
        self._lock = threading.Lock()
        self._closed = False

    def write(self, event: str, /, **fields: object) -> None:
        """{"t": clock.now(), "event": event, **fields} を 1 行追記する (file_io.append_json_line)。

        event は位置専用の引数にし、fields に "event" のキーを渡しても引数の名前と衝突しないようにする。
        複数のスレッドから呼んでよい (追記と echo はロックの中で行う)。
        fields に "t" か "event" のキーがあれば ValueError にする (何も書かない)。
        JSON にできない値 (Path など) は文字列にする。
        close の後は何も書かずに戻る (fields のキーの誤りは close の後も ValueError にする)。
        """
        reserved = {"t", "event"} & fields.keys()
        if reserved:
            raise ValueError(f"fields must not contain {sorted(reserved)}")
        with self._lock:
            if self._closed:
                return
            row: dict[str, object] = {"t": self.clock.now(), "event": event, **fields}
            append_json_line(self.path, row)
            if self.echo is not None:
                self.echo(summarize_row(row))

    def close(self) -> None:
        """以後の write を無視するようにする。2 回目以降の呼び出しは何もしない。

        ロックの中で切り替えるため、書いている途中の行は書き終えてから閉じる。
        """
        with self._lock:
            self._closed = True


def summarize_row(row: dict[str, object]) -> str:
    """run.jsonl の行を、画面に出す要約の 1 行 `<t> <event> <キー>=<値> ...` (改行を含まない) にする。"""
    parts = [f"{row['t']}", str(row["event"])]
    parts += [
        f"{key}={value}" for key, value in row.items() if key not in ("t", "event")
    ]
    return " ".join(" ".join(parts).split())
