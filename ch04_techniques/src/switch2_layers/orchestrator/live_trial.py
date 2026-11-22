"""委任型の試行 1 回分の表示状態。ワーカーは最新値を渡し、GUI のスレッドが描画する。"""

from __future__ import annotations

import difflib
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from switch2_layers.orchestrator.decision_types import DelegateStart

if TYPE_CHECKING:
    from switch2_layers.orchestrator.delegate_record import DelegateRecord


@dataclass(frozen=True, eq=False)
class LiveSnapshot:
    frame: np.ndarray | None = None
    delegated: bool = False
    main_thinking: bool = False
    jev_thinking: bool = False
    reason: str = ""
    turn: str = ""
    message: str = ""
    instructions: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()
    images: tuple[tuple[float, bytes], ...] = ()
    request_t: float | None = None
    decision: DelegateRecord | None = None
    game_overs: int = 0


class LiveTrial:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = LiveSnapshot()
        self.started = time.monotonic()
        self._game_over_screen = False
        self.stop_requested = threading.Event()

    def snapshot(self) -> LiveSnapshot:
        with self._lock:
            return self._state

    def update(self, **fields: object) -> None:
        with self._lock:
            self._state = replace(self._state, **fields)

    def preview(self, frame: np.ndarray) -> None:
        self.update(frame=frame)

    def observe_game_over(self, probability: float) -> None:
        if self.stop_requested.is_set():
            return
        with self._lock:
            if probability >= 0.8 and not self._game_over_screen:
                self._game_over_screen = True
                self._state = replace(
                    self._state, game_overs=self._state.game_overs + 1
                )
            elif probability <= 0.2:
                self._game_over_screen = False

    def set_instructions(self, values: DelegateStart) -> None:
        lines = (
            *(f"タスク　{line}" for line in values.tasks),
            *(f"注意　{line}" for line in values.cautions),
            *(
                f"行動　{choice.name}　{choice.description}"
                for choice in values.choices
            ),
        )
        # 委任指示の変更元は controller だけ。差分の計算中も映像は更新できる。
        previous = self.snapshot().instructions
        if previous != lines:
            changes = tuple(
                line
                for line in difflib.ndiff(previous, lines)
                if line.startswith(("+ ", "- "))
            )
            self.update(instructions=lines, changes=changes)
