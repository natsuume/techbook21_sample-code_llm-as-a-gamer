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

from collections.abc import Callable


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
        raise NotImplementedError

    def put(self, item: T) -> bool:
        """item をキューに入れ、入れられたら True を返す。例外を出さない。

        キューが満杯のとき、stop_accepting の後、write_item の失敗で記録を止めた後は、入れずに dropped を
        1 増やして False を返す。wait_until_written が戻った後は、dropped も増やさずに False を返す。
        フレームの読み取りスレッドや PadOutput のロックの中から呼ぶため、I/O をせず、書き出しスレッドを待たない
        (queue.Queue.put_nowait と、件数だけを守る小さなロックを使い、write_item の間に持つロックは取らない)。
        """
        raise NotImplementedError

    def stop_accepting(self) -> None:
        """以後の put を受け付けない (put は False を返し、wait_until_written が戻るまでは dropped に数える)。
        キューに入っている分は書き続ける。

        待たずに戻る。2 つの writer を並行に待つため、終わるのを待つのは wait_until_written で行う。
        2 回目以降の呼び出しは何もしない。
        """
        raise NotImplementedError

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
        raise NotImplementedError

    @property
    def written(self) -> int:
        """書けた (write_item が戻った) 件数。"""
        raise NotImplementedError

    @property
    def dropped(self) -> int:
        """捨てた件数 (モジュールの docstring の件数の規則)。"""
        raise NotImplementedError
