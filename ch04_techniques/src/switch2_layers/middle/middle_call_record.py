"""middle/calls.jsonl の 1 行 (middle の 1 回の実行の記録) を作る。

行の形:
    {"t_sent", "t_received", "based_on_t", "frames": [{"t", "file"}], "surface_rows": 行数, "operation_rows": 行数,
     "scene_change_reported", "scene_id", "scene_name", "new_scene", "surface_wait_s", "press_home",
     "deep_requests": [種類...], "deep_requests_sent", "error",
     "reasons": [理由...], "new_tasks": [{"text", "destination"}, ...], "destination_fixes": [直した内容, ...],
     "cancel_task_ids": [ID...], "cancel_used": 真偽, "new_tasks_used": 真偽}
reasons はその回に middle を呼んだ理由 (middle.middle_trigger.MIDDLE_CALL_REASON_ORDER の順。失敗した回も書く)。
new_tasks は行き先を直した後の出力の new_tasks (destination は tasks.task_files.destination_to_json の形)、
destination_fixes は直した内容 (middle.task_destination_fix.destination_fix_to_json の形。無ければ空の配列)、
cancel_task_ids は出力の cancel_task_ids、cancel_used と new_tasks_used は取り消しと追加を行ったか
(middle_output_writer.write_middle_output の 2b と 2c)。
失敗した回は scene_id・scene_name・surface_wait_s・press_home・new_tasks・destination_fixes・cancel_task_ids・
cancel_used・new_tasks_used を null、new_scene を false、deep_requests を空にし、error に説明を書く。
MiddleAnalyzer が MiddleCallDetailsSource でもあれば (gpt-6-luna の実装)、その回の記録 (usage・概算の費用・応答など) を
上のキーの後ろに足す (middle_call_row_with_details)。フェイクの MiddleAnalyzer では上のキーだけにする。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.middle.middle_output_writer import MiddleWriteResult
from switch2_layers.middle.middle_trigger import MiddleCallReason
from switch2_layers.middle.middle_types import MiddleInput
from switch2_layers.middle.task_destination_fix import destination_fix_to_json
from switch2_layers.tasks.task_files import destination_to_json


@dataclass(frozen=True)
class MiddleCallRecord:
    """middle の 1 回の実行の記録。

    t_sent               analyze を呼んだ時刻
    t_received           analyze が戻った (または例外を出した) 時刻
    middle_input         渡した入力。入力を作れずに失敗したら None (行の based_on_t は null、frames は空のリスト、
                         surface_rows と operation_rows は 0、scene_change_reported は false にする)
    write_result         出力を書いた結果。失敗したら None
    deep_requests        初めての場面のために deep を呼ぶ文書の種類 (無ければ空)
    deep_requests_sent   deep のスケジューラーに渡したら True (middle-once では渡さず False)
    error                失敗の説明。成功なら None
    reasons              その回に middle を呼んだ理由 (入力を作れずに失敗した回も、呼んだ理由を持つ)
    """

    t_sent: float
    t_received: float
    middle_input: MiddleInput | None
    write_result: MiddleWriteResult | None
    deep_requests: Sequence[DocumentKind]
    deep_requests_sent: bool
    error: str | None
    reasons: Sequence[MiddleCallReason]


def middle_call_row(record: MiddleCallRecord) -> dict[str, object]:
    """middle/calls.jsonl の 1 行の辞書を、モジュールの docstring のキーの順で返す。

    based_on_t は middle_input.t、frames は middle_input.frames の [{"t", "file"}]、surface_rows と operation_rows は
    行数、scene_id・scene_name・surface_wait_s・press_home は write_result.state の値、new_scene は write_result.new_scene が
    あれば True、deep_requests は種類の値 (文字列) のリスト。
    reasons は record.reasons のリスト。new_tasks は write_result.new_tasks の [{"text": 文, "destination":
    tasks.task_files.destination_to_json(行き先)}]、destination_fixes は write_result.destination_fixes の
    middle.task_destination_fix.destination_fix_to_json のリスト、cancel_task_ids は write_result.cancel_task_ids の
    リスト、cancel_used と new_tasks_used は write_result の同じ名前の値。write_result が None (失敗した回) なら、
    この 5 つは null。
    """
    middle_input = record.middle_input
    state = None if record.write_result is None else record.write_result.state
    return {
        "t_sent": record.t_sent,
        "t_received": record.t_received,
        "based_on_t": None if middle_input is None else middle_input.t,
        "frames": []
        if middle_input is None
        else [{"t": frame.t, "file": frame.file} for frame in middle_input.frames],
        "surface_rows": 0 if middle_input is None else len(middle_input.surface_rows),
        "operation_rows": 0
        if middle_input is None
        else len(middle_input.operation_rows),
        "scene_change_reported": False
        if middle_input is None
        else middle_input.scene_change_reported,
        "scene_id": None if state is None else state.scene_id,
        "scene_name": None if state is None else state.scene_name,
        "new_scene": record.write_result is not None
        and record.write_result.new_scene is not None,
        "surface_wait_s": None if state is None else state.surface_wait_s,
        "press_home": None if state is None else state.press_home,
        "deep_requests": [kind.value for kind in record.deep_requests],
        "deep_requests_sent": record.deep_requests_sent,
        "error": record.error,
        "reasons": list(record.reasons),
        **_task_fields(record.write_result),
    }


def _task_fields(write_result: MiddleWriteResult | None) -> dict[str, object]:
    """行の new_tasks・destination_fixes・cancel_task_ids・cancel_used・new_tasks_used (この順) を返す。

    write_result が None (失敗した回) なら、どれも None。
    """
    if write_result is None:
        return {
            "new_tasks": None,
            "destination_fixes": None,
            "cancel_task_ids": None,
            "cancel_used": None,
            "new_tasks_used": None,
        }
    return {
        "new_tasks": [
            {"text": task.text, "destination": destination_to_json(task.destination)}
            for task in write_result.new_tasks
        ],
        "destination_fixes": [
            destination_fix_to_json(fix) for fix in write_result.destination_fixes
        ],
        "cancel_task_ids": list(write_result.cancel_task_ids),
        "cancel_used": write_result.cancel_used,
        "new_tasks_used": write_result.new_tasks_used,
    }


@runtime_checkable
class MiddleCallDetailsSource(Protocol):
    """analyze の 1 回ごとに、middle/calls.jsonl の行に足す記録を持つ MiddleAnalyzer (gpt-6-luna の実装など)。

    MiddleAnalyzer の Protocol とは別に、必要な実装だけが持つ。
    """

    def take_call_details(self) -> dict[str, object]:
        """直前の analyze の記録 (行に足すキーと値) を返し、持っている記録を消す。無ければ空の辞書を返す。"""
        ...


def take_analyzer_call_details(analyzer: object) -> dict[str, object]:
    """analyzer が MiddleCallDetailsSource なら take_call_details() の値を返し、そうでなければ空の辞書を返す。"""
    if isinstance(analyzer, MiddleCallDetailsSource):
        return analyzer.take_call_details()
    return {}


def middle_call_row_with_details(
    record: MiddleCallRecord, details: Mapping[str, object]
) -> dict[str, object]:
    """middle_call_row(record) のキーの後ろに、details のキーと値を details の順で足した辞書を返す。

    details のキーのうち middle_call_row のキーと同じ名前のものは足さない (骨組みの値を残す)。
    details が空なら middle_call_row(record) と同じ辞書になる。
    """
    row = middle_call_row(record)
    skeleton_keys = set(row)
    for key, value in details.items():
        if key not in skeleton_keys:
            row[key] = value
    return row
