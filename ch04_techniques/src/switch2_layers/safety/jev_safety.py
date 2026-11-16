"""TypeSafe Jev に安全の問いを送る SafetyChecker (JevSafetyChecker)。

要求の送り方と応答の読み方は surface と同じ surface.jev_client を使う (モデル jev-1.14.0、API キーは環境変数
TYPESAFE_API_KEY)。応答の usage を safety.jsonl に書くため、JevClient.ask (答えだけを返す) ではなく、
jev_client.build_payload で作った本文を JevClient.post で送り、応答の本文を jev_client.parse_answers で読む。
"""

from collections.abc import Callable

from switch2_layers.safety.safety_types import SafetyAnswer
from switch2_layers.surface.jev_client import JevClient, new_asset_reference


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
        2. 本文 = jev_client.build_payload(safety_questions.build_safety_state(reference),
           safety_questions.build_safety_questions(), [JevImage(reference, jpeg)], client.model)
        3. client.post(本文, with_images=jev_client.needs_images_header(問い, 画像)) で送る
        4. jev_client.parse_answers(応答の本文, 問い) の nouls から、SAFETY_QUESTION_NAMES の順の (名前 → 確率) を作る
        SafetyAnswer(probabilities, usage=応答の本文の "usage" (無ければ None)) を返す。
        HTTP のエラー・JSON として読めない応答・答えの欠け・確率が数でないか 0〜1 の外は、jev_client.JevError をそのまま出す。
        """
        raise NotImplementedError
