"""安全の監視のフェイク (FakeSafetyChecker)。--safety fake で使い、API キーは要らない。"""

from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.safety.safety_types import SafetyAnswer


class FakeSafetyChecker:
    """SafetyChecker の実装。画像を見ずに、すべての問いの確率を 0 として返す (止まる条件に当たらない)。"""

    def check(self, jpeg: bytes) -> SafetyAnswer:
        """SafetyAnswer(probabilities={SAFETY_QUESTION_NAMES の各名前: 0.0} (この順), usage=None) を返す。"""
        return SafetyAnswer(
            probabilities=dict.fromkeys(SAFETY_QUESTION_NAMES, 0.0), usage=None
        )
