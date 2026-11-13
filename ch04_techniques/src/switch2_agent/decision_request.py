"""Decision Model への要求 (API の種類によらない形) を組み立てる。

要求は、役割の説明と安全のための規則 (instructions)、ゲームの情報の 4 ファイルの内容 (state_text)、
ラベル付きの画像 (古い順)、問いの一覧、直近の応答と送った操作の履歴 (history_text)、
前回の操作の時点を表す区切りの文 (previous_operation_text) からなる。
前回の操作は OperationLog の最新の記録で、前回の判断で決めた操作か、その後にコントローラーを中立に戻したこと。
履歴には、中立に戻したことも 1 行として書く。
画像のラベルには、実行を始めてからの経過秒 (t) と何秒前の画像かを書き、直近の区間の画像には、
前回の操作の前の画像か、後の画像かも書く。
API ごとの形への変換は decision_models が行う。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.request_frames import RequestFrames
from switch2_agent.game_notes import format_game_notes
from switch2_agent.operation_log import (
    NeutralRecord,
    OperationLogRecord,
    format_record,
    record_time,
)
from switch2_agent.questions import Question
from switch2_agent.safety import SAFETY_RULES

# ラベルの先頭の語。意味のあるフレーム (直近の区間より前の場面) と、直近の区間のフレーム
MEANINGFUL_LABEL = "過去の場面"
RECENT_LABEL = "直近"
# 直近の区間のラベルの末尾に書く、前回の操作との前後
BEFORE_PREVIOUS_OPERATION_LABEL = "前回の操作の前"
AFTER_PREVIOUS_OPERATION_LABEL = "前回の操作の後"
DEFAULT_JPEG_CACHE_CAPACITY = 64  # JpegCache が持つ符号化の結果の数の既定値
DEFAULT_HISTORY_COUNT = 5  # 要求に入れる直近の応答と送った操作の数
# 操作の履歴の見出しと、記録が無いときの行
HISTORY_HEADING = f"## 直近の応答と送った操作 (古い順、最大 {DEFAULT_HISTORY_COUNT} 回)"
NO_OPERATION_TEXT = "(まだ操作を送っていない)"
# Decision Model に毎回渡す役割の説明
ROLE_DESCRIPTION = """\
あなたは Nintendo Switch 2 のゲームを操作するコントローラーの判断をする。
画像は時刻の古い順に並んでいる。各画像の前のラベルに、実行を始めてからの経過秒 (t) と、何秒前の画像かを書いてある。
「過去の場面」は今より前の場面の代表の画像、「直近」は前回の判断から今までの画像である。
「直近」のラベルの「前回の操作の前」「前回の操作の後」は、前回の操作 (操作の履歴の最後の行。前回の判断で決めた操作か、その後にコントローラーを中立に戻したこと) の前の画像か、後の画像かを表す。
操作が画面に出るまでには遅れがあるため、「前回の操作の後」の画像でも、操作の前の画面のことがある。
直近の応答と送った操作の履歴に、最近の判断で送った操作と、コントローラーを中立に戻したことを、その時刻とともに書いてある。
ゲームの情報 (操作方法・攻略情報・進行状況・戦略) を参考にし、各問いには、ほかの問いの答えと関係なく独立に答える。
"""


@dataclass(frozen=True)
class LabeledImage:
    """要求に載せる画像 1 枚。

    label                      画像の前に置く説明 (label_frames のラベル)
    jpeg                       JPEG のバイト列
    frame_time                 元のフレームの time (time.monotonic の値)
    after_previous_operation   直近の区間の画像で、前回の操作を送った後 (frame_time が前回の操作の時刻以上) なら True。
                               意味のあるフレームの画像と、前回の操作が無い (最初の要求の) 画像は False
    """

    label: str
    jpeg: bytes
    frame_time: float
    after_previous_operation: bool = False


@dataclass(frozen=True)
class DecisionRequest:
    """Decision Model への要求 1 回分。

    instructions              役割の説明と安全のための規則 (build_instructions)
    state_text                ゲームの情報の 4 ファイルの内容 (game_notes.format_game_notes)
    images                    ラベル付きの画像。意味のあるフレーム (古い順) → 直近の区間のフレーム (古い順) の順
    questions                 問いの一覧
    history_text              直近の応答と送った操作の履歴 (format_operation_history。中立に戻したことの行も含む)。
                              空文字なら履歴を入れない
    previous_operation_text   前回の操作 (送った操作か、中立に戻したこと) の時点を表す区切りの文
                              (format_previous_operation)。前回の操作が無ければ None
    """

    instructions: str
    state_text: str
    images: list[LabeledImage]
    questions: list[Question]
    history_text: str = ""
    previous_operation_text: str | None = None


def build_instructions() -> str:
    """Decision Model に毎回渡す役割の説明 (ROLE_DESCRIPTION) と、safety.SAFETY_RULES をつないだ文を返す。

    役割の説明には次を書く:
        - Nintendo Switch 2 のゲームを操作するコントローラーの判断をすること
        - 画像は時刻の古い順に並んでいること (ラベルに、実行を始めてからの経過秒 t と何秒前の画像かを書いてあること)
        - 「過去の場面」と「直近」の画像の違い
        - 「直近」のラベルの「前回の操作の前」「前回の操作の後」が、前回の操作 (前回の判断で決めた操作か、その後に
          コントローラーを中立に戻したこと) の前か後かを表すこと
        - 操作が画面に出るまでには遅れがあるため、「前回の操作の後」の画像でも操作の前の画面のことがあること
        - 直近の応答と送った操作の履歴に、最近の判断で送った操作と、コントローラーを中立に戻したことを、その時刻と
          ともに書いてあること
        - 各問いには、ほかの問いの答えと関係なく独立に答えること
    """
    return f"{ROLE_DESCRIPTION}\n{SAFETY_RULES}"


def label_frames(
    frames: RequestFrames,
    now: float,
    origin: float,
    previous_operation_at: float | None,
) -> list[tuple[str, TimedFrame, bool]]:
    """要求に載せるフレームを並べ、(ラベル, フレーム, 前回の操作の後か) のリストにする。

    並びは意味のあるフレーム (frames.meaningful、古い順) → 直近の区間のフレーム (frames.recent、古い順)。
    origin は経過秒の起点 (Pipeline.started_at)、previous_operation_at は前回の操作の時刻
    (OperationLog の最新の記録の operation_log.record_time。最新の記録が NeutralRecord なら中立に戻した時刻。
    記録が無ければ None)。
    k はその種類の中での 1 始まりの番号、n はその種類のフレームの数。t は frame.time - origin、
    秒前は now - frame.time で、どちらも小数第 2 位まで書く。
    - 意味のあるフレーム: `過去の場面 <k>/<n> (t=<t> 秒、<秒前> 秒前)`。3 つ目の値は常に False
      (例: `過去の場面 2/5 (t=115.80 秒、12.30 秒前)`)
    - 直近の区間のフレームで、previous_operation_at が None (最初の要求): `直近 <k>/<n> (t=<t> 秒、<秒前> 秒前)`。
      3 つ目の値は False
    - 直近の区間のフレームで、previous_operation_at があるとき: frame.time >= previous_operation_at なら
      `直近 <k>/<n> (t=<t> 秒、<秒前> 秒前、前回の操作の後)` で 3 つ目の値は True、
      frame.time < previous_operation_at なら `直近 <k>/<n> (t=<t> 秒、<秒前> 秒前、前回の操作の前)` で False
      (例: `直近 4/5 (t=127.89 秒、0.21 秒前、前回の操作の後)`)
    """
    labeled: list[tuple[str, TimedFrame, bool]] = []
    for number, frame in enumerate(frames.meaningful, start=1):
        timing = f"t={frame.time - origin:.2f} 秒、{now - frame.time:.2f} 秒前"
        label = f"{MEANINGFUL_LABEL} {number}/{len(frames.meaningful)} ({timing})"
        labeled.append((label, frame, False))
    for number, frame in enumerate(frames.recent, start=1):
        timing = f"t={frame.time - origin:.2f} 秒、{now - frame.time:.2f} 秒前"
        after = False
        if previous_operation_at is not None:
            after = frame.time >= previous_operation_at
            order = (
                AFTER_PREVIOUS_OPERATION_LABEL
                if after
                else BEFORE_PREVIOUS_OPERATION_LABEL
            )
            timing = f"{timing}、{order}"
        label = f"{RECENT_LABEL} {number}/{len(frames.recent)} ({timing})"
        labeled.append((label, frame, after))
    return labeled


def format_operation_history(
    records: Sequence[OperationLogRecord], now: float, origin: float
) -> str:
    """直近の応答と送った操作の履歴の文 (DecisionRequest.history_text) を返す。

    1 行目は見出し `## 直近の応答と送った操作 (古い順、最大 <DEFAULT_HISTORY_COUNT> 回)`。
    続けて records (古い順) を 1 行 1 件で `- t=<t> 秒 (<秒前> 秒前): <operation_log.format_record>` と書く
    (応答と送った操作の記録は format_action の文、中立に戻した記録は `中立に戻した (<理由>)`)。
    t は record_time(記録) - origin、秒前は now - record_time(記録) で、どちらも小数第 2 位まで書く。
    最後の行 (前回の操作) の末尾には ` (前回の操作)` を付ける。records が空なら、見出しの次に
    `(まだ操作を送っていない)` の 1 行を書く。行は \\n でつなぎ、末尾に改行を付けない。例:
        ## 直近の応答と送った操作 (古い順、最大 5 回)
        - t=128.10 秒 (2.75 秒前): 押下 none / 短押し UP
        - t=130.30 秒 (0.55 秒前): 押下 none / 短押し HOME (前回の操作)
    最新の記録が中立に戻した記録なら、最後の行は次のようになる:
        - t=130.30 秒 (0.55 秒前): 中立に戻した (Decision Model の失敗) (前回の操作)
    """
    if not records:
        return f"{HISTORY_HEADING}\n{NO_OPERATION_TEXT}"
    lines = [HISTORY_HEADING]
    for record in records:
        time = record_time(record)
        lines.append(
            f"- t={time - origin:.2f} 秒 "
            f"({now - time:.2f} 秒前): {format_record(record)}"
        )
    lines[-1] += " (前回の操作)"
    return "\n".join(lines)


def format_previous_operation(
    record: OperationLogRecord | None, now: float, origin: float
) -> str | None:
    """前回の操作の時点を表す区切りの文 (DecisionRequest.previous_operation_text) を返す。

    record (前回の操作の記録) が応答と送った操作の記録 (OperationRecord) なら
    `── t=<t> 秒 (<秒前> 秒前) に、前回の応答の操作を送った ──`、
    中立に戻した記録 (NeutralRecord) なら `── t=<t> 秒 (<秒前> 秒前) に、中立に戻した (<理由>) ──` を返す。
    t は record_time(record) - origin、秒前は now - record_time(record) で、どちらも小数第 2 位まで書く
    (例: `── t=130.30 秒 (0.55 秒前) に、前回の応答の操作を送った ──`、
    `── t=130.30 秒 (0.55 秒前) に、中立に戻した (Decision Model の失敗) ──`)。record が None なら None を返す。
    """
    if record is None:
        return None
    if isinstance(record, NeutralRecord):
        operation = format_record(record)
    else:
        operation = "前回の応答の操作を送った"
    time = record_time(record)
    return f"── t={time - origin:.2f} 秒 ({now - time:.2f} 秒前) に、{operation} ──"


def build_decision_request(
    frames: RequestFrames,
    now: float,
    notes: Mapping[str, str],
    questions: Sequence[Question],
    encode: Callable[[TimedFrame], bytes],
    origin: float,
    recent_operations: Sequence[OperationLogRecord],
) -> DecisionRequest:
    """要求を組み立てる。

    origin は経過秒の起点 (Pipeline.started_at)。recent_operations は直近の応答と送った操作の記録と、
    中立に戻した記録 (古い順。Pipeline は OperationLog.recent(DEFAULT_HISTORY_COUNT) を渡す)。
    前回の操作の時刻 previous_operation_at は recent_operations の最後の記録の operation_log.record_time
    (中立に戻した記録なら中立に戻した時刻。空なら None)。
    instructions = build_instructions()、state_text = game_notes.format_game_notes(notes)。
    images は label_frames(frames, now, origin, previous_operation_at) の順に、各フレームを encode で JPEG にした
    LabeledImage (frame_time はフレームの time、after_previous_operation は label_frames の 3 つ目の値)。
    questions はリストにして入れる。
    history_text = format_operation_history(recent_operations, now, origin)、
    previous_operation_text = format_previous_operation(recent_operations の最後の記録 (空なら None), now, origin)。
    """
    previous_operation = recent_operations[-1] if recent_operations else None
    previous_operation_at = (
        record_time(previous_operation) if previous_operation is not None else None
    )
    images = [
        LabeledImage(
            label=label,
            jpeg=encode(frame),
            frame_time=frame.time,
            after_previous_operation=after,
        )
        for label, frame, after in label_frames(
            frames, now, origin, previous_operation_at
        )
    ]
    return DecisionRequest(
        instructions=build_instructions(),
        state_text=format_game_notes(notes),
        images=images,
        questions=list(questions),
        history_text=format_operation_history(recent_operations, now, origin),
        previous_operation_text=format_previous_operation(
            previous_operation, now, origin
        ),
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
