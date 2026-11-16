"""短期タスクリストの進み具合を、最初のタスクの文が変わったかで測る。I/O を扱わない。

middle は、出力の短期タスクリスト (tasks) の最初のタスクの文と、その文になった時刻を middle/state.json に記録する
(middle_output_writer)。次の入力の「前回の middle の出力」の節で、最初のタスクが同じ文のまま何秒続いているかを
gpt-6-luna に渡し (luna_request)、詰まりの判定と待ち時間の決め方に使わせる (luna_instructions)。
最初のタスクの文は、空白文字の違いだけなら同じ文とみなす (gpt-6-luna が引き継いだ文の空白だけを変えても、
タスクが進んだことにしない)。
"""

from switch2_layers.middle.middle_types import MiddleState


def first_task_of(tasks: str) -> str:
    """短期タスクリスト tasks (Markdown) の最初のタスクの文を返す。

    tasks の行のうち、前後の空白を除いて「- 」で始まる最初の行の、「- 」より後ろを前後の空白を除いて返す
    (インデントされた行も、前後の空白を除いて「- 」で始まれば対象にする)。該当する行が無ければ空文字列。
    """
    raise NotImplementedError


def is_same_task_text(a: str, b: str) -> bool:
    """タスクの文 a と b が、空白文字の違いを除いて同じなら True を返す。

    空白文字 (半角・全角の空白、タブ、改行。str.split() が区切る文字) をすべて除いた文字列どうしを比べる
    (例: 「A で話しかける」と「Aで話しかける」は同じ)。両方空なら True。
    """
    raise NotImplementedError


def first_task_since(
    previous_state: MiddleState | None, first_task: str, written_t: float
) -> float:
    """最初のタスクが今の文 first_task になった時刻を返す。

    previous_state は前回の middle/state.json、written_t は今回の出力を書いた時刻 (MiddleState.generated_t)。
    previous_state が None でなく、previous_state.first_task_since_t が None でなく、
    is_same_task_text(previous_state.first_task, first_task) なら previous_state.first_task_since_t を返す
    (最初のタスクが前回と同じ文のまま)。それ以外は written_t を返す (今回、最初のタスクが今の文になった)。
    """
    raise NotImplementedError
