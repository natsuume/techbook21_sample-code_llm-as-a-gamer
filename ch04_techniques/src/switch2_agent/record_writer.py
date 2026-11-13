"""上限つきのキューと、専用の書き出しスレッドで、記録を 1 件ずつ書く。

記録する側のスレッド (フレームの読み取りスレッドや、PadOutput のロックの中) は put でキューに入れるだけにし、
I/O・符号化・縮小は書き出しスレッドで行う。記録する側を待たせないよう、キューが満杯なら新しい記録を捨て、
捨てた件数を数える。キューに入った記録は、入れた順 (古い順) に書く。

件数の規則: written は書けた件数、dropped は捨てた件数 (満杯・受け付けをやめた後・失敗で止めた後に届いた分、
失敗で止めたときにキューに残っていた分と失敗した 1 件、wait_until_written の時間切れで書けなかった分)。
wait_until_written が戻るまでに put を呼んだ回数 = written + dropped になる。wait_until_written が戻った後の put は
数えない (件数は、戻ったときの値から変えない。run の片付けでは、第 2 章の Capture.close がフレームの読み取りスレッドの
終わりを待たないため、記録を閉じた後に、片付けの途中のフレームが多くても 1 件届くことがある)。
"""

import logging
import queue
import threading
from collections.abc import Callable

from switch2_remote.error_message import describe_error

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)


class _NoMoreItems:
    """書き出しスレッドを終えるときに、_take_next_item が item の代わりに返す印。"""


_NO_MORE_ITEMS = _NoMoreItems()


class BoundedRecordWriter[T]:
    """capacity 件までのキューと、そのキューから取り出して書く書き出しスレッド (daemon) を持つ。

    kind         記録の種類 ("frames" か "pad")。on_error に渡し、record_error と record_end の件数のキーにする
    capacity     キューに入れておける件数の上限 (書いている途中の 1 件は含めない)
    write_item   1 件を書く関数。書き出しスレッドから、キューに入れた順に呼ぶ
    on_error     書き込みの失敗を (kind, 説明) で受け取る関数。書き出しスレッドから、1 つの writer につき最大 1 回呼ぶ
    on_finish    書き出しスレッドが終わるときに、書き出しスレッドから 1 回だけ呼ぶ関数 (write_item が開いたファイルを
                 閉じるのに使う)。None なら呼ばない

    書き出しスレッドは、キューから古い順に取り出して write_item(item) を呼び、戻ったら written を 1 増やす。
    write_item が例外 (Exception) を出したら、その種類の記録をそこで止める:
        - 先に記録を止め (以後の put は False を返す)、それから on_error(kind,
          switch2_remote.error_message.describe_error(e)) を 1 回だけ呼ぶ。on_error の例外
          (Exception) は捕まえて logging の警告にする (書き出しスレッドを例外で終わらせないため)
        - 失敗した 1 件、キューに残っていた分、以後に put された分を dropped に数え、write_item は呼ばない
        - OSError と ValueError 以外の例外 (書き込みの失敗として想定していない例外) は、原因を追えるよう
          logging でトレースバックも書く
    書き出しスレッドは、次のどれかで on_finish を呼んでから終わる (on_finish を呼ぶのは 1 回だけ):
        - stop_accepting の後に、キューの分を書き切ったとき
        - write_item の失敗で記録を止めたとき (on_error を呼んだ後)
        - wait_until_written の時間切れで手放した後に、書いている途中の 1 件 (write_item) が戻ったとき
    on_finish の例外 (Exception) は捕まえて logging の警告にする (外に出さない)。時間切れで手放した後に
    on_finish を呼んでも、written と dropped は変わらない。
    """

    def __init__(
        self,
        kind: str,
        capacity: int,
        write_item: Callable[[T], None],
        on_error: Callable[[str, str], None],
        *,
        on_finish: Callable[[], None] | None = None,
    ):
        """属性を入れ、空のキュー (queue.Queue(maxsize=capacity)) を作って、書き出しスレッド (daemon) を起動する。"""
        self.kind = kind
        self._queue: queue.Queue[T] = queue.Queue(maxsize=capacity)
        self._write_item = write_item
        self._on_error = on_error
        self._on_finish = on_finish
        # 状態と件数を守る小さなロック。書き出しスレッドは、キューが空の間これで待ち、put などが起こす。
        # write_item・on_error・on_finish・logging の間は持たない (put を待たせないため)
        self._condition = threading.Condition()
        self._written = 0
        self._dropped = 0
        self._accepting = True  # stop_accepting で False にする
        self._stopped_by_error = False  # write_item の失敗で記録を止めたら True にする
        self._given_up = False  # wait_until_written の時間切れで手放したら True にする
        self._counts_fixed = (
            False  # wait_until_written が戻ったら True にする (以後の put は数えない)
        )
        self._writing = False  # 書き出しスレッドが write_item を呼んでいる間 True
        # 書き出しスレッドが on_finish を呼び終えたら立てる
        self._finished = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"{kind}-record-writer", daemon=True
        )
        self._thread.start()

    def put(self, item: T) -> bool:
        """item をキューに入れ、入れられたら True を返す。例外を出さない。

        キューが満杯のとき、stop_accepting の後、write_item の失敗で記録を止めた後は、入れずに dropped を
        1 増やして False を返す。wait_until_written が戻った後は、dropped も増やさずに False を返す。
        フレームの読み取りスレッドや PadOutput のロックの中から呼ぶため、I/O をせず、書き出しスレッドを待たない
        (queue.Queue.put_nowait と、件数だけを守る小さなロックを使い、write_item の間に持つロックは取らない)。
        """
        with self._condition:
            if self._counts_fixed:
                return False
            if not self._accepting or self._stopped_by_error:
                self._dropped += 1
                return False
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._dropped += 1
                return False
            self._condition.notify()
            return True

    def stop_accepting(self) -> None:
        """以後の put を受け付けない (put は False を返し、wait_until_written が戻るまでは dropped に数える)。
        キューに入っている分は書き続ける。

        待たずに戻る。2 つの writer を並行に待つため、終わるのを待つのは wait_until_written で行う。
        2 回目以降の呼び出しは何もしない。
        """
        with self._condition:
            self._accepting = False
            # キューが空で待っている書き出しスレッドを起こし、終わってよいかを確かめさせる
            self._condition.notify()

    def wait_until_written(self, timeout_s: float) -> bool:
        """キューの分を書き切って書き出しスレッドが終わるまで、最大 timeout_s 秒待つ。終わったら True を返す。

        stop_accepting の後に呼ぶ。True を返したときには、on_finish も戻っている (書き出しスレッドは on_finish が
        戻ってから終わる)。write_item の失敗で記録を止めたときは、書き出しスレッドは stop_accepting を待たずに
        on_error と on_finish を呼んで終わるため、その後に呼べば True を返す。
        timeout_s 秒で終わらなければ、キューに残っていた分と書いている途中の 1 件を dropped に足し、以後は
        書き出しスレッドが新しく write_item を呼ばないようにして False を返す (書いている途中の 1 件は、ファイルに
        残ることがある。その 1 件が戻ったら、書き出しスレッドは on_finish を呼んで終わる)。
        戻った後は、written と dropped が変わらない (以後の put は数えずに False を返す)。
        """
        finished = self._finished.wait(max(timeout_s, 0.0))
        with self._condition:
            self._counts_fixed = True
            if finished or self._finished.is_set():
                return True
            if self._given_up:
                # 2 回目以降の時間切れでは、もう足した分を足し直さない
                return False
            self._given_up = True
            self._dropped += self._discard_queued_locked()
            if self._writing:
                self._dropped += 1
            # キューが空で待っている書き出しスレッドを起こし、終わらせる
            self._condition.notify()
        logger.warning("gave up writing the remaining %s records", self.kind)
        return False

    @property
    def written(self) -> int:
        """書けた (write_item が戻った) 件数。"""
        with self._condition:
            return self._written

    @property
    def dropped(self) -> int:
        """捨てた件数 (モジュールの docstring の件数の規則)。"""
        with self._condition:
            return self._dropped

    def _run(self) -> None:
        """書き出しスレッドの本体。キューの分を書き、終わるときに on_finish を 1 回呼ぶ。"""
        try:
            self._write_queued_items()
        finally:
            self._call_on_finish()
            self._finished.set()

    def _write_queued_items(self) -> None:
        """キューから古い順に取り出して write_item を呼ぶ。書き出しスレッドを終えるときに戻る。

        戻るのは、受け付けをやめてキューの分を書き切ったとき、時間切れで手放したとき、write_item が失敗したとき。
        """
        while True:
            item = self._take_next_item()
            if isinstance(item, _NoMoreItems):
                return
            try:
                self._write_item(item)
            except (OSError, ValueError) as e:
                # 書き込みの失敗として想定している例外。説明だけを書く
                logger.warning(
                    "stopped writing %s records: %s", self.kind, describe_error(e)
                )
                self._stop_after_write_failure(e)
                return
            except Exception as e:
                # 想定していない例外は、原因を追えるようトレースバックも書く
                logger.warning(
                    "stopped writing %s records after an unexpected error",
                    self.kind,
                    exc_info=True,
                )
                self._stop_after_write_failure(e)
                return
            with self._condition:
                self._writing = False
                if self._given_up:
                    # 時間切れで手放した後に戻った 1 件は、もう dropped に数えてある
                    return
                self._written += 1

    def _take_next_item(self) -> T | _NoMoreItems:
        """次に書く 1 件をキューから取り出して返す。キューが空なら、put か停止の知らせを待つ。

        受け付けをやめてキューが空になったか、時間切れで手放したなら、_NO_MORE_ITEMS を返す。
        取り出す・数えるのを 1 つのロックの中で行い、手放したときに、取り出した途中の 1 件を数え漏らさないようにする。
        """
        with self._condition:
            while True:
                if self._given_up:
                    return _NO_MORE_ITEMS
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    if not self._accepting:
                        return _NO_MORE_ITEMS
                    self._condition.wait()
                    continue
                self._writing = True
                return item

    def _stop_after_write_failure(self, error: Exception) -> None:
        """write_item の失敗 error で記録を止め、on_error に知らせる (書き出しスレッドから呼ぶ)。

        失敗した 1 件とキューに残っていた分を dropped に数えてから、on_error を呼ぶ。時間切れで手放した後の
        失敗なら、件数はもう決まっているため数えない (on_error には知らせる)。
        """
        with self._condition:
            self._writing = False
            if not self._given_up:
                self._stopped_by_error = True
                self._dropped += 1 + self._discard_queued_locked()
        try:
            self._on_error(self.kind, describe_error(error))
        except Exception:
            logger.warning(
                "failed to report the write error of %s records",
                self.kind,
                exc_info=True,
            )

    def _call_on_finish(self) -> None:
        """on_finish があれば呼ぶ。例外 (Exception) は外に出さず、logging で警告を書く。"""
        if self._on_finish is None:
            return
        try:
            self._on_finish()
        except Exception:
            logger.warning("on_finish of %s records failed", self.kind, exc_info=True)

    def _discard_queued_locked(self) -> int:
        """キューに残っている分をすべて取り出して捨て、その件数を返す。呼び出し側が _condition を持つ。"""
        count = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return count
            count += 1
