"""Decision Model への要求 (API の種類によらない形) を組み立てる。

要求は、役割の説明と安全のための規則 (instructions)、ゲームの情報の 4 ファイルの内容 (state_text)、
ラベル付きの画像 (古い順)、問いの一覧からなる。API ごとの形への変換は decision_models が行う。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.request_frames import RequestFrames
from switch2_agent.game_notes import format_game_notes
from switch2_agent.questions import Question
from switch2_agent.safety import SAFETY_RULES

# ラベルの先頭の語。意味のあるフレーム (直近の区間より前の場面) と、直近の区間のフレーム
MEANINGFUL_LABEL = "過去の場面"
RECENT_LABEL = "直近"
DEFAULT_JPEG_CACHE_CAPACITY = 64  # JpegCache が持つ符号化の結果の数の既定値
# Decision Model に毎回渡す役割の説明
ROLE_DESCRIPTION = """\
あなたは Nintendo Switch 2 のゲームを操作するコントローラーの判断をする。
画像は時刻の古い順に並んでいる。各画像の前のラベルに、何秒前の画像かを書いてある。
「過去の場面」は今より前の場面の代表の画像、「直近」は前回の判断から今までの画像である。
ゲームの情報 (操作方法・攻略情報・進行状況・戦略) を参考にし、各問いには、ほかの問いの答えと関係なく独立に答える。
"""


@dataclass(frozen=True)
class LabeledImage:
    """要求に載せる画像 1 枚。

    label        画像の前に置く説明 (label_frames のラベル)
    jpeg         JPEG のバイト列
    frame_time   元のフレームの time (time.monotonic の値)
    """

    label: str
    jpeg: bytes
    frame_time: float


@dataclass(frozen=True)
class DecisionRequest:
    """Decision Model への要求 1 回分。

    instructions   役割の説明と安全のための規則 (build_instructions)
    state_text     ゲームの情報の 4 ファイルの内容 (game_notes.format_game_notes)
    images         ラベル付きの画像。意味のあるフレーム (古い順) → 直近の区間のフレーム (古い順) の順
    questions      問いの一覧
    """

    instructions: str
    state_text: str
    images: list[LabeledImage]
    questions: list[Question]


def build_instructions() -> str:
    """Decision Model に毎回渡す役割の説明と、safety.SAFETY_RULES をつないだ文を返す。

    役割の説明には次を書く:
        - Nintendo Switch 2 のゲームを操作するコントローラーの判断をすること
        - 画像は時刻の古い順に並んでいること (ラベルに何秒前の画像かを書いてあること)
        - 各問いには、ほかの問いの答えと関係なく独立に答えること
    """
    return f"{ROLE_DESCRIPTION}\n{SAFETY_RULES}"


def label_frames(frames: RequestFrames, now: float) -> list[tuple[str, TimedFrame]]:
    """要求に載せるフレームを並べ、(ラベル, フレーム) のリストにする。

    並びは意味のあるフレーム (frames.meaningful、古い順) → 直近の区間のフレーム (frames.recent、古い順)。
    ラベルは、意味のあるフレームが `過去の場面 <k>/<n> (<秒> 秒前)`、直近の区間のフレームが
    `直近 <k>/<n> (<秒> 秒前)`。k はその種類の中での 1 始まりの番号、n はその種類のフレームの数。
    秒は now - frame.time を小数第 2 位まで書く (例: `過去の場面 2/5 (12.30 秒前)`、`直近 4/5 (0.21 秒前)`)。
    """
    labeled: list[tuple[str, TimedFrame]] = []
    for prefix, group in [
        (MEANINGFUL_LABEL, frames.meaningful),
        (RECENT_LABEL, frames.recent),
    ]:
        for number, frame in enumerate(group, start=1):
            seconds_ago = now - frame.time
            label = f"{prefix} {number}/{len(group)} ({seconds_ago:.2f} 秒前)"
            labeled.append((label, frame))
    return labeled


def build_decision_request(
    frames: RequestFrames,
    now: float,
    notes: Mapping[str, str],
    questions: Sequence[Question],
    encode: Callable[[TimedFrame], bytes],
) -> DecisionRequest:
    """要求を組み立てる。

    instructions = build_instructions()、state_text = game_notes.format_game_notes(notes)。
    images は label_frames(frames, now) の順に、各フレームを encode で JPEG にした LabeledImage
    (frame_time はフレームの time)。questions はリストにして入れる。
    """
    images = [
        LabeledImage(label=label, jpeg=encode(frame), frame_time=frame.time)
        for label, frame in label_frames(frames, now)
    ]
    return DecisionRequest(
        instructions=build_instructions(),
        state_text=format_game_notes(notes),
        images=images,
        questions=list(questions),
    )


class JpegCache:
    """フレームの JPEG への符号化の結果を、frame.time をキーにして使い回す。

    意味のあるフレームは要求をまたいで同じものが載るため、同じフレームを何度も符号化しないようにする。
    Pipeline の encode にはこのインスタンスを渡す。Pipeline のスレッドだけから呼ぶため、ロックは持たない。

    属性:
        encode     実際に符号化する関数
        capacity   持つ結果の数の上限
        cache      frame.time → JPEG のバイト列 (入れた順)
    """

    def __init__(
        self,
        encode: Callable[[TimedFrame], bytes],
        capacity: int = DEFAULT_JPEG_CACHE_CAPACITY,
    ):
        """空のキャッシュを作る。"""
        self.encode = encode
        self.capacity = capacity
        # dict は入れた順を保つため、先頭のキーが最も古い
        self.cache: dict[float, bytes] = {}

    def __call__(self, frame: TimedFrame) -> bytes:
        """frame.time の結果を持っていればそれを返し、無ければ encode(frame) の結果を入れて返す。

        入れた後に capacity を超えたら、入れた順に最も古いキーから捨てる。
        """
        cached = self.cache.get(frame.time)
        if cached is not None:
            return cached
        jpeg = self.encode(frame)
        self.cache[frame.time] = jpeg
        while len(self.cache) > self.capacity:
            del self.cache[next(iter(self.cache))]
        return jpeg
