"""safety.jsonl の 1 行 (安全の確かめ 1 回の記録) を作る。

行の形:
    {"t_sent", "t_received", "frame_t", "phase": "start" か "monitor", "probabilities": {問いの名前: 確率} か null,
     "usage", "error"}
"""

from collections.abc import Mapping
from dataclasses import dataclass

from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.safety.safety_types import SafetyPhase


@dataclass(frozen=True)
class SafetyRecord:
    """安全の確かめ 1 回の記録。

    t_sent          SafetyChecker.check を呼んだ時刻 (実行の経過秒)
    t_received      check が戻った (または例外を出した) 時刻
    frame_t         確かめたフレームの時刻
    phase           段階 (実行を始めるときか、監視か)
    probabilities   問いの名前 → 確率 (safety_judge.validate_probabilities の値)。失敗したら None
    usage           API の usage。無いか、失敗したら None
    error           失敗の説明 (error_text.describe_exception)。成功したら None
    """

    t_sent: float
    t_received: float
    frame_t: float
    phase: SafetyPhase
    probabilities: Mapping[str, float] | None
    usage: object | None
    error: str | None


def safety_row(record: SafetyRecord) -> dict[str, object]:
    """safety.jsonl の 1 行の辞書を、モジュールの docstring のキーの順で返す。

    phase は段階の値の文字列、probabilities は safety_questions.SAFETY_QUESTION_NAMES の順の辞書 (None なら null)、
    usage はそのまま (None なら null)。
    """
    probabilities = (
        None
        if record.probabilities is None
        else {name: record.probabilities[name] for name in SAFETY_QUESTION_NAMES}
    )
    return {
        "t_sent": record.t_sent,
        "t_received": record.t_received,
        "frame_t": record.frame_t,
        "phase": record.phase.value,
        "probabilities": probabilities,
        "usage": record.usage,
        "error": record.error,
    }
