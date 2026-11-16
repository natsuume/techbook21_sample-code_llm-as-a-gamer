"""Advisor 型の短期タスクを、場面ごとのキューで持つ計算 (SceneTaskQueues)。I/O を扱わない。時計を読まない。

キューは場面 ID ごとに 1 本で、未完了のタスクをキューに入った順に並べる。先頭が実行中のタスク。
    追加              文の配列を、指定した場面のキューの末尾に、配列の順に足す (先頭への割り込みは無い)。
                      文は normalize_task_text で整える。空の文 (empty)、その場面の未完了のタスクと空白の違いを除いて
                      同じ文 (duplicate)、上限に達した後の文 (limit) は足さず、理由とともに返す。上限は、1 回の追加で
                      足せる数 MAX_ADDED_PER_CALL と、1 場面の未完了のタスクの数 MAX_OPEN_TASKS_PER_SCENE
    完了              その場面の先頭のタスクだけを完了にする (判定した後に先頭が変わっていたら何もしない)
    取り消しの要求    その場面の先頭のタスクに、要求の時刻を 1 回だけ付ける。要求は、完了か取り消しまで残る
    取り消し          ID で指定したタスクを、場面に依らず、キューのどの位置からでも外す
タスクの ID は `task-<連番3桁以上>` (task_id) で、場面をまたいで通しの連番にする。足せなかった文は ID を使わない。
タスクの続いた秒数は、キューに入ってから (作り直したタスクは、保存されていた秒数に足して)、そのタスクの場面が
現在の場面だった時間の合計。現在の場面でない間と、場面なし (None) の間は進まない。
返す秒数は run_clock.round_to_milliseconds で丸める。時刻 t は、どの操作も引数で受け取る。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from switch2_layers.run_clock import round_to_milliseconds

# 1 回の追加で足せるタスクの数
MAX_ADDED_PER_CALL = 3
# 1 場面の未完了のタスクの上限
MAX_OPEN_TASKS_PER_SCENE = 5
# タスクの ID は TASK_ID_PREFIX と TASK_ID_DIGITS 桁以上の連番 (例: task-001)
TASK_ID_PREFIX = "task-"
TASK_ID_DIGITS = 3
# タスクの ID の形 (TASK_ID_PREFIX と TASK_ID_DIGITS 桁以上の数字)。グループ 1 が連番の数字
_TASK_ID_PATTERN = re.compile(r"task-([0-9]{3,})")

# 足せなかった理由: empty (空の文)、duplicate (その場面の未完了のタスクと同じ文)、limit (上限に達した後の文)
DropReason = Literal["empty", "duplicate", "limit"]


@dataclass(frozen=True)
class TaskView:
    """ある時刻 t でのタスクの見え方。

    id                   タスクの ID (task_id の形)
    text                 タスクの文 (normalize_task_text を通した後の文)
    scene_id             タスクの場面 ID
    added_t              キューに入った時刻
    active_s             t までの続いた秒数 (run_clock.round_to_milliseconds で丸めた値)
    cancel_requested_t   取り消しの要求を付けた時刻。要求が無ければ None
    """

    id: str
    text: str
    scene_id: str
    added_t: float
    active_s: float
    cancel_requested_t: float | None


@dataclass(frozen=True)
class DroppedText:
    """追加で足せなかった文。

    text     normalize_task_text を通した後の文
    reason   足せなかった理由
    """

    text: str
    reason: DropReason


@dataclass(frozen=True)
class AddResult:
    """追加の結果。

    outcomes   入力の文ごとの結果を入力の順に並べたもの。足した文は、足したタスクの追加の時刻での見え方 (TaskView)、
               足せなかった文は DroppedText
    """

    outcomes: tuple[TaskView | DroppedText, ...]

    @property
    def added(self) -> tuple[TaskView, ...]:
        """outcomes のうち足したタスク (入力の順)。"""
        return tuple(item for item in self.outcomes if isinstance(item, TaskView))

    @property
    def dropped(self) -> tuple[DroppedText, ...]:
        """outcomes のうち足せなかった文 (入力の順)。"""
        return tuple(item for item in self.outcomes if isinstance(item, DroppedText))


def task_id(number: int) -> str:
    """連番 number のタスクの ID `task-<連番>` を返す。

    連番は TASK_ID_DIGITS 桁に満たなければ先頭を 0 で埋める。例: 1 → task-001、12 → task-012、1000 → task-1000。
    number が 1 未満なら ValueError。
    """
    if number < 1:
        raise ValueError(f"task number must be 1 or more: {number}")
    return f"{TASK_ID_PREFIX}{number:0{TASK_ID_DIGITS}d}"


def task_id_number(value: object) -> int | None:
    """value がタスクの ID の形 (str で、`task-` と 3 桁以上の数字に fullmatch) なら、その連番の int を返す。

    例: "task-001" → 1、"task-1000" → 1000。形が違う値と str でない値 (None を含む) は None。
    """
    if not isinstance(value, str):
        return None
    match = _TASK_ID_PATTERN.fullmatch(value)
    if match is None:
        return None
    return int(match.group(1))


def normalize_task_text(text: str) -> str:
    """タスクの文 text を整える。

    前後の空白を除き (str.strip())、その後の改行 (`\\r\\n`・`\\r`・`\\n` のそれぞれ 1 つ) を空白 1 つに置き換える。
    例: " 宿屋に\\r\\n入る " → "宿屋に 入る"。
    """
    # \r\n を先に置き換え、\r と \n の 2 つにしない
    return text.strip().replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def is_same_task_text(a: str, b: str) -> bool:
    """タスクの文 a と b が、空白文字の違いを除いて同じなら True を返す。

    空白文字 (半角・全角の空白、タブ、改行。str.split() が区切る文字) をすべて除いた文字列どうしを比べる
    (例: 「A で話しかける」と「Aで話しかける」は同じ)。
    """
    return _without_whitespace(a) == _without_whitespace(b)


def _without_whitespace(text: str) -> str:
    """text から空白文字 (str.split() が区切る文字) をすべて除いた文字列を返す。"""
    return "".join(text.split())


@dataclass
class _QueuedTask:
    """キューの中の未完了のタスク (状態を書き換えるため frozen にしない)。

    active_base   キューに入った時点の、そのタスクの場面の累計 (現在の場面だった秒数)。作り直したタスクは
                  累計 − 保存されていた active_s。続いた秒数は「t での累計 − active_base」
    ほかは TaskView の同じ名前の値
    """

    id: str
    text: str
    scene_id: str
    added_t: float
    active_base: float
    cancel_requested_t: float | None


def _drop_reason(
    queue: Sequence[_QueuedTask], text: str, added_count: int
) -> DropReason | None:
    """整えた後の文 text を場面のキュー queue に足せない理由を返す。足せるなら None。

    added_count は、同じ呼び出しで既に足した数。理由が重なるときは empty → duplicate → limit の順で最初のもの。
    """
    if not text:
        return "empty"
    if any(is_same_task_text(task.text, text) for task in queue):
        return "duplicate"
    if added_count >= MAX_ADDED_PER_CALL or len(queue) >= MAX_OPEN_TASKS_PER_SCENE:
        return "limit"
    return None


class SceneTaskQueues:
    """場面ごとのタスクのキューと、現在の場面と、次に振る ID の連番を持つ。1 つのスレッドからだけ呼ぶ。

    ロックは持たない (複数のスレッドから使うときは tasks.task_board.TaskBoard がロックの中で呼ぶ)。
    続いた秒数は、場面ごとに現在の場面だった秒数の累計を持ち、各タスクはキューに入った時点の累計
    (作り直したタスクは累計 − active_s) を覚え、その差で出す。
    """

    def __init__(self, restored: Sequence[TaskView] = (), next_number: int = 1) -> None:
        """キューを作る。

        restored のタスクを、それぞれの scene_id のキューに restored の順で入れる。各タスクの続いた秒数は
        restored の active_s から始め、added_t と cancel_requested_t もそのまま持つ。
        現在の場面は場面なし (None)。次に振る ID の連番は next_number。
        """
        # 場面 ID → 未完了のタスク (キューの順)
        self._queues: dict[str, list[_QueuedTask]] = {}
        # 場面 ID → 現在の場面だった秒数の累計 (今の現在の場面になってからの分は含まない)
        self._scene_seconds: dict[str, float] = {}
        self._current_scene_id: str | None = None
        # 今の現在の場面になった時刻 (場面なしの間は使わない)
        self._current_since_t = 0.0
        self._next_number = next_number
        for task in restored:
            # 作り直した時点では、どの場面の累計も 0 のため、累計 − active_s は −active_s
            queued = _QueuedTask(
                id=task.id,
                text=task.text,
                scene_id=task.scene_id,
                added_t=task.added_t,
                active_base=-task.active_s,
                cancel_requested_t=task.cancel_requested_t,
            )
            self._queues.setdefault(task.scene_id, []).append(queued)

    @property
    def current_scene_id(self) -> str | None:
        """現在の場面 ID。場面なしなら None。"""
        return self._current_scene_id

    def add(self, scene_id: str, texts: Sequence[str], t: float) -> AddResult:
        """文の配列 texts を、場面 scene_id のキューの末尾に、配列の順に時刻 t で足す。

        各文を normalize_task_text で整えてから、次の順で判定する (理由が重なるときは最初に当たったもの):
            1. 空 (整えた後が空文字列) → empty
            2. その場面の未完了のタスク (同じ呼び出しで先に足したものを含む) に is_same_task_text で同じ文がある
               → duplicate (ほかの場面のタスクとは比べない)
            3. この呼び出しで足した数が MAX_ADDED_PER_CALL に達している、またはその場面の未完了のタスクが
               MAX_OPEN_TASKS_PER_SCENE に達している → limit
        どれにも当たらなければ、ID を task_id(次の連番) で振って連番を 1 進め、キューの末尾に足す
        (added_t = t、cancel_requested_t = None、続いた秒数は 0 から)。足せなかった文は ID を使わない。
        戻り値の outcomes は、入力の文ごとに、足したタスクの t での見え方か、足せなかった文と理由を、入力の順に並べたもの。
        """
        queue = self._queues.setdefault(scene_id, [])
        outcomes: list[TaskView | DroppedText] = []
        added_count = 0
        for raw_text in texts:
            text = normalize_task_text(raw_text)
            reason = _drop_reason(queue, text, added_count)
            if reason is not None:
                outcomes.append(DroppedText(text=text, reason=reason))
                continue
            queued = _QueuedTask(
                id=task_id(self._next_number),
                text=text,
                scene_id=scene_id,
                added_t=t,
                active_base=self._scene_total(scene_id, t),
                cancel_requested_t=None,
            )
            self._next_number += 1
            queue.append(queued)
            added_count += 1
            outcomes.append(self._view(queued, t))
        return AddResult(outcomes=tuple(outcomes))

    def complete(self, scene_id: str, task_id: str, t: float) -> TaskView | None:
        """task_id が場面 scene_id のキューの先頭なら、キューから外し (完了)、外したタスクの t での見え方を返す。

        先頭でない、その場面にキューが無い、未知の ID のときは何もせず None を返す。
        """
        head = self._head(scene_id, task_id)
        if head is None:
            return None
        self._queues[scene_id].pop(0)
        return self._view(head, t)

    def request_cancel(self, scene_id: str, task_id: str, t: float) -> TaskView | None:
        """task_id が場面 scene_id のキューの先頭で、取り消しの要求がまだ無ければ、要求の時刻 t を付ける。

        付けたら、付けた後のタスクの t での見え方を返す。それ以外 (先頭でない、その場面にキューが無い、未知の ID、
        既に要求がある) は何もせず None を返す。要求は、タスクが完了するか取り消されるまで残る。
        """
        head = self._head(scene_id, task_id)
        if head is None or head.cancel_requested_t is not None:
            return None
        head.cancel_requested_t = t
        return self._view(head, t)

    def cancel(self, task_ids: Sequence[str], t: float) -> tuple[TaskView, ...]:
        """task_ids の順に、未完了のタスクのうち ID が当たるものを、場面に依らずキューから外す (取り消し)。

        外したタスクの t での見え方を、外した順に返す。未完了のタスクに無い ID (同じ ID の 2 回目を含む) は無視する。
        """
        canceled: list[TaskView] = []
        for target in task_ids:
            removed = self._remove(target)
            if removed is not None:
                canceled.append(self._view(removed, t))
        return tuple(canceled)

    def set_current_scene(self, scene_id: str | None, t: float) -> None:
        """現在の場面を、時刻 t から scene_id (None は場面なし) にする。今と同じ場面なら何もしない。"""
        if scene_id == self._current_scene_id:
            return
        previous = self._current_scene_id
        if previous is not None:
            # 今の現在の場面だった区間の秒数を、その場面の累計に入れる
            self._scene_seconds[previous] = self._scene_total(previous, t)
        self._current_scene_id = scene_id
        self._current_since_t = t

    def oldest_active_s(self, scene_id: str, t: float) -> float | None:
        """場面 scene_id の最も古いタスクの、t での続いた秒数を返す。

        最も古いタスクは、未完了のタスクのうち added_t が最も小さいもの (同じ値ならキューの前のもの)。
        キューの先頭とは限らない。未完了のタスクが無ければ None。
        """
        queue = self._queues.get(scene_id)
        if not queue:
            return None
        # min は、最小の値が複数あれば最初のもの (キューの前のもの) を返す
        oldest = min(queue, key=lambda queued: queued.added_t)
        return self._view(oldest, t).active_s

    def tasks(self, scene_id: str, t: float) -> tuple[TaskView, ...]:
        """場面 scene_id の未完了のタスクの t での見え方を、キューの順に返す。無ければ空のタプル。"""
        return tuple(self._view(queued, t) for queued in self._queues.get(scene_id, []))

    def _scene_total(self, scene_id: str, t: float) -> float:
        """場面 scene_id が時刻 t までに現在の場面だった秒数の累計 (丸めない)。"""
        total = self._scene_seconds.get(scene_id, 0.0)
        if scene_id == self._current_scene_id:
            total += t - self._current_since_t
        return total

    def _view(self, queued: _QueuedTask, t: float) -> TaskView:
        """キューの中のタスク queued の、時刻 t での見え方を返す。続いた秒数はミリ秒に丸める。"""
        return TaskView(
            id=queued.id,
            text=queued.text,
            scene_id=queued.scene_id,
            added_t=queued.added_t,
            active_s=round_to_milliseconds(
                self._scene_total(queued.scene_id, t) - queued.active_base
            ),
            cancel_requested_t=queued.cancel_requested_t,
        )

    def _head(self, scene_id: str, target: str) -> _QueuedTask | None:
        """場面 scene_id のキューの先頭の ID が target なら、先頭のタスクを返す。そうでなければ None。"""
        queue = self._queues.get(scene_id)
        if not queue or queue[0].id != target:
            return None
        return queue[0]

    def _remove(self, target: str) -> _QueuedTask | None:
        """ID が target の未完了のタスクを、どの場面のキューからでも外して返す。無ければ None。"""
        for queue in self._queues.values():
            for index, queued in enumerate(queue):
                if queued.id == target:
                    return queue.pop(index)
        return None
