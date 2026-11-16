"""短期タスクリストの進み具合を、最初のタスクの文が変わったかで測る。I/O を扱わない。

middle は、出力の短期タスクリスト (tasks) の最初のタスクの文と、その文になった時刻を middle/state.json に記録する
(middle_output_writer)。次の入力の「前回の middle の出力」の節で、最初のタスクが同じ文のまま何秒続いているかを
gpt-6-luna に渡し (luna_request)、詰まりの判定と待ち時間の決め方に使わせる (luna_instructions)。
最初のタスクの文は、空白文字の違いだけなら同じ文とみなす (gpt-6-luna が引き継いだ文の空白だけを変えても、
タスクが進んだことにしない)。
最初のタスクが無い (文が空の) 回は時刻を記録しない (タスクが無いまま続いた秒数を、同じタスクが続いた秒数として
渡さないため)。
"""

from switch2_layers.middle.middle_types import MiddleState

# 短期タスクリストのタスクの行の先頭 (Markdown の箇条書きの記号と空白)
TASK_BULLET = "- "


def first_task_of(tasks: str) -> str:
    """短期タスクリスト tasks (Markdown) の最初のタスクの文を返す。

    tasks の行のうち、前後の空白を除いて「- 」で始まる最初の行の、「- 」より後ろを前後の空白を除いて返す
    (インデントされた行も、前後の空白を除いて「- 」で始まれば対象にする)。該当する行が無ければ空文字列。
    """
    for line in tasks.splitlines():
        stripped = line.strip()
        if stripped.startswith(TASK_BULLET):
            return stripped[len(TASK_BULLET) :].strip()
    return ""


def is_same_task_text(a: str, b: str) -> bool:
    """タスクの文 a と b が、空白文字の違いを除いて同じなら True を返す。

    空白文字 (半角・全角の空白、タブ、改行。str.split() が区切る文字) をすべて除いた文字列どうしを比べる
    (例: 「A で話しかける」と「Aで話しかける」は同じ)。両方空なら True。
    """
    return _without_whitespace(a) == _without_whitespace(b)


def first_task_since(
    previous_state: MiddleState | None,
    first_task: str,
    written_t: float,
    scene_id: str | None = None,
) -> float | None:
    """最初のタスクが今の文 first_task になった時刻を返す。最初のタスクが無ければ None を返す。

    previous_state は前回の middle/state.json、written_t は今回の出力を書いた時刻 (MiddleState.generated_t)、
    scene_id は今回の出力の場面 ID (場面が決まらなければ None)。
    - first_task が空 (空白文字だけの場合を含む) なら None (最初のタスクが無いので、時刻を記録しない)
    - previous_state が None でなく、previous_state.first_task_since_t が None でなく、
      is_same_task_text(previous_state.first_task, first_task) で、場面が変わっていなければ
      previous_state.first_task_since_t (最初のタスクが前回と同じ文のまま)。場面が変わったとみなすのは、
      previous_state.scene_id と scene_id がどちらも None でなく、違うときだけ (暗転などで場面が決まらない回を
      挟んでも、時刻は引き継ぐ)
    - それ以外は written_t (今回、最初のタスクが今の文になった)
    """
    if not _without_whitespace(first_task):
        return None
    if (
        previous_state is not None
        and previous_state.first_task_since_t is not None
        and is_same_task_text(previous_state.first_task, first_task)
        and not _scene_changed(previous_state.scene_id, scene_id)
    ):
        return previous_state.first_task_since_t
    return written_t


def _scene_changed(previous_scene_id: str | None, scene_id: str | None) -> bool:
    """前回と今回の場面 ID がどちらも決まっていて違えば True を返す。"""
    return (
        previous_scene_id is not None
        and scene_id is not None
        and previous_scene_id != scene_id
    )


def _without_whitespace(text: str) -> str:
    """text から空白文字 (str.split() が区切る文字) をすべて除いた文字列を返す。"""
    return "".join(text.split())
