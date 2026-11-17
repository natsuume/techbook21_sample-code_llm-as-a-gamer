"""middle/calls.jsonl の 1 行 (middle の 1 回の実行の記録) を作る。

行の形:
    {"t_sent", "t_received", "based_on_t", "frames": [{"t", "file"}], "surface_rows": 行数, "operation_rows": 行数,
     "scene_change_reported", "scene_id", "scene_name", "new_scene", "surface_wait_s", "press_home",
     "deep_requests": [種類...], "deep_requests_sent", "error"}
失敗した回は scene_id・scene_name・surface_wait_s・press_home を null、new_scene を false、deep_requests を空にし、
error に説明を書く。
MiddleAnalyzer が MiddleCallDetailsSource でもあれば (gpt-6-luna の実装)、その回の記録 (usage・概算の費用・応答など) を
上のキーの後ろに足す (middle_call_row_with_details)。フェイクの MiddleAnalyzer では上のキーだけにする。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.middle.middle_output_writer import MiddleWriteResult
from switch2_layers.middle.middle_types import MiddleInput


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
    """

    t_sent: float
    t_received: float
    middle_input: MiddleInput | None
    write_result: MiddleWriteResult | None
    deep_requests: Sequence[DocumentKind]
    deep_requests_sent: bool
    error: str | None


def middle_call_row(record: MiddleCallRecord) -> dict[str, object]:
    """middle/calls.jsonl の 1 行の辞書を、モジュールの docstring のキーの順で返す。

    based_on_t は middle_input.t、frames は middle_input.frames の [{"t", "file"}]、surface_rows と operation_rows は
    行数、scene_id・scene_name・surface_wait_s・press_home は write_result.state の値、new_scene は write_result.new_scene が
    あれば True、deep_requests は種類の値 (文字列) のリスト。
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
