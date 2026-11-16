"""安全の監視のフェイク (FakeSafetyChecker)。--safety fake で使い、API キーは要らない。"""

from switch2_layers.safety.safety_types import SafetyAnswer


class FakeSafetyChecker:
    """SafetyChecker の実装。画像を見ずに、止める条件に当たらず、どの見本の画面でもない答えを返す。"""

    def check(self, jpeg: bytes) -> SafetyAnswer:
        """次の SafetyAnswer を返す (usage は None)。

        probabilities   {safety_questions.SAFETY_QUESTION_NAMES の各名前: 0.0} (この順)
        system_screen   {system_screen_references.SYSTEM_SCREEN_CHOICE_NAMES の各名前: 確率} (この順)。
                        確率は NONE_CHOICE が 1.0、ほかの選択肢が 0.0
        """
        raise NotImplementedError
