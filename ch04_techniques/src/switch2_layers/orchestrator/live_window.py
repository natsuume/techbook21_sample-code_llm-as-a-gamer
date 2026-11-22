"""委任型のライブ表示。Tk と画像の縮小・描画はメインスレッドだけで扱う。"""

import logging
import signal
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from queue import Empty, SimpleQueue

import cv2
import numpy as np

from switch2_layers.frames.image_encoding import nv12_to_bgr
from switch2_layers.orchestrator.live_trial import LiveSnapshot, LiveTrial

Play = Callable[[str | None, LiveTrial], int]

BG = "#11171d"
PANEL = "#1b242c"
FIELD = "#10171c"
WHITE = "#edf4f5"
MUTED = "#a9b8c2"
GREEN = "#78dfbb"
RED = "#ffad9f"
FONT = "TkDefaultFont"


def label(parent, text="", *, size=11, color=WHITE, bg=PANEL, bold=False):
    return tk.Label(
        parent,
        text=text,
        fg=color,
        bg=bg,
        anchor="w",
        font=(FONT, size, "bold" if bold else "normal"),
    )


def text_area(parent, height, *, expand=True):
    box = tk.Frame(parent, bg=FIELD)
    widget = tk.Text(
        box,
        height=height,
        width=1,
        wrap="word",
        bg=FIELD,
        fg=WHITE,
        relief="flat",
        padx=10,
        pady=7,
        insertbackground=WHITE,
        font=(FONT, 11),
    )
    scroll = tk.Scrollbar(box, command=widget.yview)
    widget.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    widget.pack(fill="both", expand=True)
    box.pack(fill="both", expand=expand)
    return widget


def photo(parent, bgr, width, height):
    """縦横比を保って画像領域に収め、Tk 標準の PPM で渡す。"""
    rows, cols = bgr.shape[:2]
    ratio = min(width / cols, height / rows)
    size = (max(1, round(cols * ratio)), max(1, round(rows * ratio)))
    rgb = cv2.cvtColor(
        cv2.resize(bgr, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB
    )
    header = f"P6\n{size[0]} {size[1]}\n255\n".encode()
    return tk.PhotoImage(master=parent, data=header + rgb.tobytes(), format="PPM")


class LiveWindow:
    def __init__(
        self, root: tk.Tk, instruction: str | None, play: Play, count_game_overs: bool
    ):
        self.root = root
        self.play = play
        self.count_game_overs = count_game_overs
        self.trial: LiveTrial | None = None
        self.worker: threading.Thread | None = None
        self.finished: SimpleQueue[tuple[float, str]] = SimpleQueue()
        self.ended: float | None = None
        self.closing = False
        self.previous: LiveSnapshot | None = None
        self.video_size = (0, 0)
        self.thumb_width = 0
        root.title("委任型 Orchestrator · ライブモニター")
        root.geometry("1200x780")
        root.minsize(760, 580)
        root.configure(bg=BG)
        bar = tk.Frame(root, bg=BG, padx=16, pady=12)
        bar.pack(fill="x")
        label(bar, "委任型モニター", size=16, bold=True, bg=BG).pack(side="left")
        self.metrics = label(bar, bg=BG, color=GREEN)
        self.metrics.pack(side="left", padx=18)
        self.stop_button = tk.Button(
            bar, text="試行を終了", command=self.stop, state="disabled"
        )
        self.stop_button.pack(side="right")
        self.start_button = tk.Button(
            bar, text="試行を開始", command=self.start, bg=GREEN, fg=BG
        )
        self.start_button.pack(side="right", padx=8)

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=12)
        body.columnconfigure((0, 1), weight=1, uniform="half")
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=PANEL, padx=12, pady=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        label(left, "キャプチャー映像", size=14, bold=True).pack(fill="x")
        self.video = tk.Canvas(
            left, bg="#080e12", highlightthickness=0, width=1, height=160
        )
        self.video.pack(fill="both", expand=True, pady=(8, 12))
        self.video_item = self.video.create_image(0, 0)
        self.video_hint = self.video.create_text(0, 0, text="映像待機中", fill=MUTED)
        label(left, "初期指示", bold=True).pack(fill="x", pady=(0, 4))
        self.input = text_area(left, 4, expand=False)
        self.input.insert("1.0", instruction or "")
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)
        right.columnconfigure(0, weight=1)
        main = tk.Frame(right, bg=PANEL, padx=12, pady=10)
        main.grid(row=0, column=0, sticky="nsew")
        self.main_status = label(main, size=14, bold=True)
        self.main_status.pack(fill="x")
        self.main_text = text_area(main, 6)
        self.main_text.tag_configure(
            "heading", foreground=MUTED, spacing1=8, spacing3=4
        )
        self.main_text.tag_configure("added", foreground=GREEN)
        self.main_text.tag_configure("removed", foreground=RED)
        self.main_text.configure(state="disabled")
        jev = tk.Frame(right, bg=PANEL, padx=12, pady=10)
        jev.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.jev_status = label(jev, size=14, bold=True)
        self.jev_status.pack(fill="x")
        self.image_heading = label(jev, color=MUTED)
        self.image_heading.pack(fill="x", pady=(6, 2))
        self.images = tk.Frame(jev, bg=PANEL)
        self.images.pack(fill="x")
        label(jev, "直近の決定", color=MUTED).pack(fill="x", pady=(10, 2))
        self.decision = label(jev)
        self.decision.configure(justify="left")
        self.decision.pack(fill="x")
        self.status = label(root, "開始待ち", bg=BG, color=MUTED)
        self.status.pack(fill="x", padx=16, pady=8)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def start(self):
        if self.worker is not None or self.closing:
            return
        trial = LiveTrial()
        instruction = self.input.get("1.0", "end-1c")
        self.trial = trial
        self.previous = None
        self.ended = None
        self.video.itemconfigure(self.video_item, image="")
        self.video.image = None
        self.input.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.configure(text="試行中")
        self.worker = threading.Thread(
            target=self.run_trial, args=(instruction, trial), name="delegate-trial"
        )
        self.worker.start()

    def run_trial(self, instruction: str, trial: LiveTrial):
        try:
            code = self.play(instruction if instruction.strip() else None, trial)
            reason = trial.snapshot().reason
            if code and not reason:
                reason = f"終了コード {code}"
        except Exception as error:
            logging.getLogger(__name__).exception("delegate trial failed")
            reason = str(error) or type(error).__name__
        self.finished.put((time.monotonic(), reason))

    def stop(self):
        if self.trial is not None and self.worker is not None:
            self.trial.stop_requested.set()
            self.stop_button.configure(state="disabled")
            self.status.configure(text="終了処理中")

    def close(self):
        self.closing = True
        self.stop()
        if self.worker is None:
            self.root.quit()

    def refresh(self):
        try:
            self.ended, reason = self.finished.get_nowait()
        except Empty:
            pass
        else:
            self.worker.join()
            self.worker = None
            self.stop_button.configure(state="disabled")
            self.start_button.configure(state="normal")
            self.input.configure(state="normal")
            self.status.configure(text=f"試行終了　{reason}")
            if self.closing:
                self.root.quit()
                return
        state = self.trial.snapshot() if self.trial else LiveSnapshot()
        active = self.worker is not None
        seconds = (
            int((self.ended or time.monotonic()) - self.trial.started)
            if self.trial
            else 0
        )
        elapsed = f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"
        count = state.game_overs if self.count_game_overs else "計測なし"
        self.metrics.configure(text=f"{elapsed}　｜　ゲームオーバー {count}")
        self.draw_agents(state, active)
        self.draw_images(state)
        self.previous = state
        self.root.after(33, self.refresh)

    def draw_agents(self, state, active):
        owner = active and bool(state.turn)
        for widget, name, thinking, control in (
            (
                self.main_status,
                "Orchestrator",
                state.main_thinking,
                owner and not state.delegated,
            ),
            (self.jev_status, "Jev", state.jev_thinking, active and state.delegated),
        ):
            status = "思考中" if active and thinking else "待機"
            title = f"{name}　 ·　{status}" + ("　 ·　操作権あり" if control else "")
            widget.configure(text=title, fg=GREEN if active and thinking else WHITE)
        decision = state.decision
        detail = "—"
        if decision is not None:
            outcome = {
                "started": "操作開始",
                "continued": "継続",
                "returned": "操作権を返却",
                "ignored": "適用せず",
            }[decision.handling]
            detail = f"{decision.error or decision.chosen}　({outcome})\n要求 {decision.t_sent:.2f} s → 応答 {decision.t_received:.2f} s"
        self.decision.configure(
            text=detail, wraplength=max(200, self.images.winfo_width())
        )
        old = self.previous
        if old and (old.message, old.instructions, old.changes) == (
            state.message,
            state.instructions,
            state.changes,
        ):
            return
        box = self.main_text
        position = box.yview()[0]
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", "状況と判断\n", "heading")
        box.insert("end", (state.message or "応答待ち") + "\n")
        box.insert("end", "委任指示の差分\n", "heading")
        for line in state.changes:
            box.insert(
                "end", line + "\n", "added" if line.startswith("+") else "removed"
            )
        if not state.changes:
            box.insert("end", "—\n")
        if state.instructions:
            box.insert("end", "現在のタスク・注意・選択肢\n", "heading")
            box.insert("end", "\n".join(state.instructions))
        box.configure(state="disabled")
        box.yview_moveto(position)

    def draw_images(self, state):
        width, height = self.video.winfo_width(), self.video.winfo_height()
        old = self.previous
        if (
            not old
            or state.frame is not old.frame
            or self.video_size != (width, height)
        ):
            self.video_size = (width, height)
            self.video.coords(self.video_hint, width // 2, height // 2)
            self.video.itemconfigure(
                self.video_hint, state="normal" if state.frame is None else "hidden"
            )
            if state.frame is not None:
                self.video.image = photo(
                    self.video, nv12_to_bgr(state.frame), width, height
                )
                self.video.coords(self.video_item, width // 2, height // 2)
                self.video.itemconfigure(self.video_item, image=self.video.image)
        width = self.images.winfo_width()
        if old and state.images is old.images and width == self.thumb_width:
            return
        self.thumb_width = width
        stamp = "" if state.request_t is None else f"　·　要求 {state.request_t:.2f} s"
        self.image_heading.configure(text="入力画像（古い順）" + stamp)
        for child in self.images.winfo_children():
            child.destroy()
        if not state.images:
            label(self.images, "委任要求を待機中", color=MUTED).pack(anchor="w")
            return
        for t, jpeg in state.images:
            bgr = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            item = label(self.images, f"{t:.2f} s", size=9)
            item.image = photo(item, bgr, max(1, width // len(state.images) - 6), 96)
            item.configure(image=item.image, compound="top")
            item.pack(side="left", padx=1)


def open_live_window(
    instruction: str | None, play: Play, count_game_overs: bool
) -> int:
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"ライブ画面を開けません: {error}", file=sys.stderr)
        return 1
    window = LiveWindow(root, instruction, play, count_game_overs)
    previous = signal.signal(signal.SIGINT, lambda *_: window.close())
    try:
        root.mainloop()
    finally:
        if window.worker is not None:
            window.trial.stop_requested.set()
            window.worker.join()
        signal.signal(signal.SIGINT, previous)
        root.destroy()
    return 0
