"""ffmpeg.exe を起動したまま保持し、処理レートに間引いたフレームを直近の一定時間分保持して撮影する。

デバイスから DEVICE_FPS で受け取った映像を ffmpeg の出力側で ffmpeg_fps に間引き、
受信したフレームをさらに process_fps に間引いて (frame_buffer.next_keep)、
直近 buffer_seconds 秒分を FrameBuffer に保持する。ffmpeg_fps は起動後に変えない。
shot はバッファーの最新フレームを待たずに PNG 保存する。
"""

DEVICE_FPS = 60  # デバイスから受け取るフレームレート (dshow の -framerate)
WIDTH, HEIGHT = 1280, 720


class Capture:
    """ffmpeg.exe からフレームを読み続け、間引いたフレームのリングバッファーと受信状況を保持する。

    フレームの読み取りと ffmpeg の stderr の読み捨ては、それぞれバックグラウンドのスレッドで行う。
    状態の読み書きは 1 つのロックの中で行う。

    主な属性:
        ffmpeg_fps    ffmpeg の出力のフレームレート。起動後は変えない (commands.parse_fps の上限に使う)
        process_fps   バッファーに残すフレームレート。set_process_fps で変える
    """

    def __init__(self, ffmpeg_fps: int, process_fps: int, buffer_seconds: int):
        """ffmpeg.exe を起動し、フレームの読み取りと stderr の読み捨てのスレッドを始める。

        バッファーの時刻には time.monotonic() を使う。
        """
        raise NotImplementedError

    def _read_frames(self):
        """フレームを受信するたびに受信数を数え、間引きで残すフレームだけを NV12 の配列としてバッファーに追加する。

        出力が終わったら ended を True にする。
        """
        raise NotImplementedError

    def _read_stderr(self):
        # 読み捨てないとパイプが詰まり ffmpeg が止まる。フレーム落ちの警告だけ数える
        for line in self.proc.stderr:
            if b"frame dropped" in line:
                with self.lock:
                    self.ffmpeg_drops += 1

    def set_process_fps(self, fps: int) -> str:
        """処理レートを fps に変え、累積値を initial_accumulator(ffmpeg_fps, fps) に戻す。

        fps の範囲 (1〜ffmpeg_fps) は呼び出し側 (commands.parse_fps) で確かめる。
        応答: ok fps=<fps>
        """
        raise NotImplementedError

    def shot(self, path: str) -> str:
        """バッファーの最新フレームを待たずに path へ PNG 保存する。

        応答: 成功時は `ok <path>`。ffmpeg が終了していれば `error capture ended`、
        バッファーが空なら `error no frame yet`、書き込みに失敗したら `error failed to write <path>`
        """
        raise NotImplementedError

    def status(self) -> str:
        """受信状況を 1 行で返す。

        応答: ok frames=<受信フレーム数> buffered=<バッファー内の枚数> ffmpeg_fps=<N> process_fps=<N>
        ffmpeg_drops=<N> ended=<bool>
        """
        raise NotImplementedError

    def close(self):
        self.proc.kill()
        self.proc.wait()
