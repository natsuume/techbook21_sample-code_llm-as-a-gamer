"""要求ごとの入出力を、実行のディレクトリーに JSON Lines と JPEG のファイルで残す (起動引数 --save-requests)。

後から「どの入力にどう答えたか」を調べられるよう、Decision Model への要求 1 回ごとに、送った画像・ゲームの情報・
直近の応答と送った操作の履歴・問いごとの確率・応答の本文・決めた操作を書く。

書くファイル (run_dir は実行のディレクトリー runs/<YYYYmmdd-HHMMSS>/):
    run_dir/requests.jsonl      write_header の header の行 (モデル・役割の説明と安全のための規則・問いの一覧) と、
                                要求 1 回ごとの write の request の行。1 行 1 件の JSON
    run_dir/frames/<名前>.jpg   要求に載せた画像。名前は経過秒 (origin からの秒) をミリ秒にして 9 桁の 0 埋めにしたもの
                                (frame_file_name)。意味のあるフレームは要求をまたいで同じものが載るため、同じ名前の
                                ファイルは 1 回だけ書き、request の行からは名前で参照する
Pipeline のスレッドだけから呼ぶため、ロックは持たない。書き込みの OSError はそのまま呼び出し側に出す
(Pipeline が捕まえて run.jsonl に記録し、判断のループは止めない)。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from switch2_agent.action_decision import PadAction
from switch2_agent.decision_models.base import Decision
from switch2_agent.decision_request import DecisionRequest
from switch2_agent.questions import Question

REQUEST_LOG_FILE_NAME = "requests.jsonl"
FRAMES_DIR_NAME = (
    "frames"  # 要求に載せた画像を書くディレクトリー (実行のディレクトリーの下)
)


def frame_file_name(t: float) -> str:
    """経過秒 t (origin からの秒) の画像のファイル名 `<t をミリ秒に丸めて 9 桁の 0 埋め>.jpg` を返す。

    ミリ秒は round(t * 1000) (例: t=1.5 なら 000001500.jpg、t=128.1 なら 000128100.jpg)。
    """
    raise NotImplementedError


@dataclass(frozen=True)
class RequestLogEntry:
    """要求 1 回分の記録。

    number             要求の通し番号 (1 始まり。Pipeline.request_count)。run.jsonl の decision の number と同じ
    origin             経過秒の起点 (Pipeline.started_at)
    sent_at            要求の時刻 (clock の値。Pipeline.run_once の now で、フレームを選ぶ前の時刻。
                       OperationRecord.sent_at と同じ)
    encoded_at         要求を組み立て終えて model.decide を呼ぶ直前の時刻 (clock の値。Pipeline.run_once の encoded_at)
    received_at        応答を受け取った時刻 (clock の値)。応答が無い (model.decide が失敗した) ときは None
    request            送った要求
    decision           Decision Model の応答。応答が無いときは None
    action             応答から決めた操作。決める前に終わったときは None
    advisor_accepted   advisor が戦略の更新の要求を受け付けたか (呼ばなかったときは False)
    result             "ok" (操作を送った)、"error" (model.decide の失敗か、操作の送信の失敗)、
                       "discarded" (キャプチャーが止まったため、応答を捨てた)
    error              result が "ok" でないときの説明。"ok" なら None
    """

    number: int
    origin: float
    sent_at: float
    encoded_at: float
    received_at: float | None
    request: DecisionRequest
    decision: Decision | None
    action: PadAction | None
    advisor_accepted: bool
    result: Literal["ok", "error", "discarded"]
    error: str | None


class RequestLog:
    """実行のディレクトリー run_dir に、要求ごとの入出力を書く。

    requests.jsonl の行は json.dumps(..., ensure_ascii=False) で書き、書くたびに flush する。
    行の "time" は壁時計の時刻 (run_log.format_wall_time(time.time()) と同じ `HH:MM:SS.mmm` の形)。
    画像を書いたファイルの名前は、この RequestLog が覚えておき、同じ名前のファイルは 1 回だけ書く。
    """

    def __init__(self, run_dir: Path):
        """run_dir / FRAMES_DIR_NAME を作り (既にあってもよい)、run_dir / REQUEST_LOG_FILE_NAME に追記で書く準備をする。

        run_dir は呼び出し側が作る。requests.jsonl に既にある行は残す。
        """
        raise NotImplementedError

    def write_header(
        self, model: str, instructions: str, questions: Sequence[Question]
    ) -> None:
        """要求に共通の内容を、header の行として requests.jsonl に 1 行追記する。

        行は {"event": "header", "time": 壁時計の時刻, "model": model, "instructions": instructions,
        "questions": [{"name": Question.name, "text": Question.text, "kind": Question.kind}, ...] (questions の順)}。
        run は、Pipeline を作った後に 1 回、model に起動引数 --model の値、instructions に
        decision_request.build_instructions()、questions に Pipeline に渡した問いの一覧を渡して呼ぶ。
        """
        raise NotImplementedError

    def write(self, entry: RequestLogEntry) -> None:
        """entry の画像の JPEG を frames/ に書き、entry を request の行として requests.jsonl に 1 行追記する。

        画像: entry.request.images の各画像の JPEG を、run_dir / FRAMES_DIR_NAME /
        frame_file_name(image.frame_time - entry.origin) に書く。この RequestLog で既に書いた名前なら書かない。
        行は次のキーを持つ JSON の object:
            event                     "request"
            number                    entry.number
            time                      壁時計の時刻
            t_sent                    要求の時刻の経過秒 entry.sent_at - entry.origin (フレームを選ぶ前の時刻)
            t_received                応答を受け取った時刻の経過秒 entry.received_at - entry.origin
                                      (received_at が None なら null)
            decide_s                  model.decide の時間 (HTTP を含む) entry.received_at - entry.encoded_at。
                                      フレームの選択と符号化の時間は含まず、run.jsonl の decision の decide_s と同じ
                                      (received_at が None なら null)
            result / error            entry.result / entry.error
            images                    entry.request.images の順に {"label": label, "t": frame_time - origin,
                                      "file": "frames/<frame_file_name の名前>", "bytes": JPEG のバイト数,
                                      "after_previous_operation": after_previous_operation}
            state_text / history_text / previous_operation_text   entry.request の同名の値
            probabilities / refusals / raw_response               entry.decision の同名の値 (decision があれば)
            action                    operation_log.format_action(entry.action) (action が None なら null)
            advisor_requested / advisor_probability               entry.action の同名の値 (action があれば)
            advisor_accepted          entry.advisor_accepted
        書き込みの OSError はそのまま出す。
        """
        raise NotImplementedError
