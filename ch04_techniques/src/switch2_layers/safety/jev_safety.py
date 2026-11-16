"""TypeSafe Jev に安全の問いを送る SafetyChecker (JevSafetyChecker)。

要求の送り方と応答の読み方は surface と同じ surface.jev_client.JevClient を使う (モデル jev-1.14.0、API キーは
環境変数 TYPESAFE_API_KEY)。JevClient.ask の答えは応答の usage も持つので、それを safety.jsonl に書く。
"""

from collections.abc import Callable

from switch2_layers.safety.safety_questions import (
    SAFETY_QUESTION_NAMES,
    build_safety_questions,
    build_safety_state,
)
from switch2_layers.safety.safety_types import SafetyAnswer
from switch2_layers.surface.jev_client import (
    JevClient,
    JevImage,
    new_asset_reference,
)


class JevSafetyChecker:
    """SafetyChecker の実装。1 回の check で Jev に要求を 1 回送る (再試行しない)。

    client           Jev の HTTP の呼び出し (close は作った側が行う)
    make_reference   画像の参照文字列を作る関数 (既定 jev_client.new_asset_reference。呼ぶたびに異なる文字列を返す)
    """

    def __init__(
        self,
        client: JevClient,
        make_reference: Callable[[], str] = new_asset_reference,
    ) -> None:
        """属性を持つ。要求はまだ送らない。"""
        self.client = client
        self.make_reference = make_reference

    def check(self, jpeg: bytes) -> SafetyAnswer:
        """jpeg を 1 枚の画像として、安全の問いを Jev に送り、答えを返す。

        1. reference = make_reference()
        2. client.ask(safety_questions.build_safety_state(reference), safety_questions.build_safety_questions(),
           [JevImage(reference, jpeg)]) で送る (画像を含むので TypeSafe-Danger-Zone-Images のヘッダーが付く)
        3. 答えの nouls から、SAFETY_QUESTION_NAMES の順の (名前 → 確率) を作る
        SafetyAnswer(probabilities, usage=答えの usage (応答に無ければ None)) を返す。
        HTTP のエラー・JSON として読めない応答・答えの欠け・確率が数でないか 0〜1 の外は、jev_client.JevError をそのまま出す。
        """
        reference = self.make_reference()
        answers = self.client.ask(
            build_safety_state(reference),
            build_safety_questions(),
            [JevImage(reference, jpeg)],
        )
        return SafetyAnswer(
            probabilities={name: answers.nouls[name] for name in SAFETY_QUESTION_NAMES},
            usage=answers.usage,
        )
