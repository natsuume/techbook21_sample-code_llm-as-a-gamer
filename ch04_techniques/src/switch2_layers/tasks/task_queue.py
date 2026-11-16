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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# 1 回の追加で足せるタスクの数
MAX_ADDED_PER_CALL = 3
# 1 場面の未完了のタスクの上限
MAX_OPEN_TASKS_PER_SCENE = 5
# タスクの ID は TASK_ID_PREFIX と TASK_ID_DIGITS 桁以上の連番 (例: task-001)
TASK_ID_PREFIX = "task-"
TASK_ID_DIGITS = 3

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
    raise NotImplementedError


def task_id_number(value: object) -> int | None:
    """value がタスクの ID の形 (str で、`task-` と 3 桁以上の数字に fullmatch) なら、その連番の int を返す。

    例: "task-001" → 1、"task-1000" → 1000。形が違う値と str でない値 (None を含む) は None。
    """
    raise NotImplementedError


def normalize_task_text(text: str) -> str:
    """タスクの文 text を整える。

    前後の空白を除き (str.strip())、その後の改行 (`\\r\\n`・`\\r`・`\\n` のそれぞれ 1 つ) を空白 1 つに置き換える。
    例: " 宿屋に\\r\\n入る " → "宿屋に 入る"。
    """
    raise NotImplementedError


def is_same_task_text(a: str, b: str) -> bool:
    """タスクの文 a と b が、空白文字の違いを除いて同じなら True を返す。

    空白文字 (半角・全角の空白、タブ、改行。str.split() が区切る文字) をすべて除いた文字列どうしを比べる
    (例: 「A で話しかける」と「Aで話しかける」は同じ)。
    """
    raise NotImplementedError


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
        raise NotImplementedError

    @property
    def current_scene_id(self) -> str | None:
        """現在の場面 ID。場面なしなら None。"""
        raise NotImplementedError

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
        raise NotImplementedError

    def complete(self, scene_id: str, task_id: str, t: float) -> TaskView | None:
        """task_id が場面 scene_id のキューの先頭なら、キューから外し (完了)、外したタスクの t での見え方を返す。

        先頭でない、その場面にキューが無い、未知の ID のときは何もせず None を返す。
        """
        raise NotImplementedError

    def request_cancel(self, scene_id: str, task_id: str, t: float) -> TaskView | None:
        """task_id が場面 scene_id のキューの先頭で、取り消しの要求がまだ無ければ、要求の時刻 t を付ける。

        付けたら、付けた後のタスクの t での見え方を返す。それ以外 (先頭でない、その場面にキューが無い、未知の ID、
        既に要求がある) は何もせず None を返す。要求は、タスクが完了するか取り消されるまで残る。
        """
        raise NotImplementedError

    def cancel(self, task_ids: Sequence[str], t: float) -> tuple[TaskView, ...]:
        """task_ids の順に、未完了のタスクのうち ID が当たるものを、場面に依らずキューから外す (取り消し)。

        外したタスクの t での見え方を、外した順に返す。未完了のタスクに無い ID (同じ ID の 2 回目を含む) は無視する。
        """
        raise NotImplementedError

    def set_current_scene(self, scene_id: str | None, t: float) -> None:
        """現在の場面を、時刻 t から scene_id (None は場面なし) にする。今と同じ場面なら何もしない。"""
        raise NotImplementedError

    def oldest_active_s(self, scene_id: str, t: float) -> float | None:
        """場面 scene_id の最も古いタスクの、t での続いた秒数を返す。

        最も古いタスクは、未完了のタスクのうち added_t が最も小さいもの (同じ値ならキューの前のもの)。
        キューの先頭とは限らない。未完了のタスクが無ければ None。
        """
        raise NotImplementedError

    def tasks(self, scene_id: str, t: float) -> tuple[TaskView, ...]:
        """場面 scene_id の未完了のタスクの t での見え方を、キューの順に返す。無ければ空のタプル。"""
        raise NotImplementedError
