"""TypeSafe Jev に安全の問いを送る SafetyChecker (JevSafetyChecker)。

要求の送り方と応答の読み方は surface と同じ surface.jev_client.JevClient を使う (モデル jev-1.14.0、API キーは
環境変数 TYPESAFE_API_KEY)。JevClient.ask の答えは応答の usage も持つので、それを safety.jsonl に書く。
1 回の要求に、今のフレーム 1 枚と見本の画像 (見本 1 つに 1 枚) を入れる。見本の画像のバイト列は作るときに受け取り
(run の始めに 1 回読む。safety.safety_assembly)、参照文字列は要求ごとに新しく振る。
"""

from collections.abc import Callable, Mapping

from switch2_layers.safety.safety_questions import (
    SAFETY_QUESTION_NAMES,
    SYSTEM_SCREEN_QUESTION_NAME,
    build_safety_questions,
    build_safety_state,
)
from switch2_layers.safety.safety_types import SafetyAnswer
from switch2_layers.surface.jev_client import (
    JevClient,
    JevImage,
    new_asset_reference,
)
from switch2_layers.system_screen.system_screen_references import (
    SYSTEM_SCREEN_NAMES,
)


class JevSafetyChecker:
    """SafetyChecker の実装。1 回の check で Jev に要求を 1 回送る (再試行しない)。

    client             Jev の HTTP の呼び出し (close は作った側が行う)
    reference_images   見本の名前 → 見本の画像の JPEG のバイト列のタプル
                       (system_screen.system_screen_references.load_reference_images の値)。
                       SYSTEM_SCREEN_NAMES のすべての名前に、画像が 1 枚ずつあること
    make_reference     画像の参照文字列を作る関数 (既定 jev_client.new_asset_reference。呼ぶたびに異なる文字列を返す)
    """

    def __init__(
        self,
        client: JevClient,
        reference_images: Mapping[str, tuple[bytes, ...]],
        make_reference: Callable[[], str] = new_asset_reference,
    ) -> None:
        """属性を持つ。要求はまだ送らず、reference_images もまだ確かめない (check で確かめる)。"""
        self.client = client
        self.reference_images = reference_images
        self.make_reference = make_reference

    def check(self, jpeg: bytes) -> SafetyAnswer:
        """jpeg を今のフレームとして、安全の問いと照合の問いを Jev に送り、答えを返す。

        1. reference_images に SYSTEM_SCREEN_NAMES のどれかの名前が無いか、どれかの名前の画像が 1 枚でなければ、
           要求を送らずに ValueError
        2. frame_reference = make_reference()。続けて SYSTEM_SCREEN_NAMES の順に、見本ごとに make_reference() で
           参照文字列を作る (要求ごとに新しく作る)
        3. client.ask(safety_questions.build_safety_state(frame_reference, 見本の名前 → 参照文字列),
           safety_questions.build_safety_questions(), 画像) で送る。画像は JevImage(frame_reference, jpeg) を先頭に、
           SYSTEM_SCREEN_NAMES の順の JevImage(見本の参照文字列, reference_images[名前][0])
           (画像を含むので TypeSafe-Danger-Zone-Images のヘッダーが付く)
        4. SafetyAnswer(probabilities=答えの nouls の SAFETY_QUESTION_NAMES の順の (名前 → 確率),
           system_screen=答えの choices[SYSTEM_SCREEN_QUESTION_NAME] (SYSTEM_SCREEN_CHOICE_NAMES の順),
           usage=答えの usage (応答に無ければ None)) を返す
        HTTP のエラー・JSON として読めない応答・答えの欠け (system_screen の答えが無い、選択肢の確率が無いを含む)・
        答えの型の違い・確率が数でないか 0〜1 の外は、jev_client.JevError をそのまま出す。
        """
        reference_jpegs = self._reference_jpegs()
        frame_reference = self.make_reference()
        reference_references = {
            name: self.make_reference() for name in SYSTEM_SCREEN_NAMES
        }
        images = [
            JevImage(frame_reference, jpeg),
            *(
                JevImage(reference_references[name], reference_jpegs[name])
                for name in SYSTEM_SCREEN_NAMES
            ),
        ]
        answers = self.client.ask(
            build_safety_state(frame_reference, reference_references),
            build_safety_questions(),
            images,
        )
        return SafetyAnswer(
            probabilities={name: answers.nouls[name] for name in SAFETY_QUESTION_NAMES},
            system_screen=dict(answers.choices[SYSTEM_SCREEN_QUESTION_NAME]),
            usage=answers.usage,
        )

    def _reference_jpegs(self) -> dict[str, bytes]:
        """reference_images から、見本の名前 → その見本の 1 枚の JPEG を SYSTEM_SCREEN_NAMES の順で返す (check の 1)。

        どれかの名前が無いか、画像が 1 枚でなければ ValueError。
        """
        jpegs: dict[str, bytes] = {}
        for name in SYSTEM_SCREEN_NAMES:
            if name not in self.reference_images:
                raise ValueError(f"no reference image for the system screen: {name}")
            images = self.reference_images[name]
            if len(images) != 1:
                raise ValueError(
                    f"the system screen {name} needs exactly one reference image, "
                    f"got {len(images)}"
                )
            jpegs[name] = images[0]
        return jpegs
