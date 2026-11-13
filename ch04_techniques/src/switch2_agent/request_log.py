"""要求ごとの入出力を、実行のディレクトリーに JSON Lines と JPEG のファイルで残す (起動引数 --save-requests か --record-dashboard)。

後から「どの入力にどう答えたか」を調べられるよう、Decision Model への要求 1 回ごとに、送った画像・ゲームの情報・
直近の応答と送った操作の履歴・問いごとの答え (選択肢ごとの確率と、真である確率)・応答の本文・決めた操作を書く。

書くファイル (run_dir は実行のディレクトリー runs/<YYYYmmdd-HHMMSS>/):
    run_dir/requests.jsonl      write_header の header の行 (モデル・役割の説明と安全のための規則・問いの一覧) と、
                                要求 1 回ごとの write の request の行。1 行 1 件の JSON
    run_dir/frames/<名前>.jpg   要求に載せた画像 (save_frames が True のときだけ。run では --save-requests を付けたとき)。
                                名前は経過秒 (origin からの秒) をマイクロ秒にして 12 桁の 0 埋めにしたもの
                                (frame_file_name)。同じフレームが複数の要求に載ることがあるため、同じ名前の
                                ファイルは 1 回だけ書き、request の行からは名前で参照する
record_thumbs が True のとき (run では --record-dashboard を付けたとき) は、request の行の images の各要素に、
その画像のフレームの縮小画像のファイル名 thumb (dashboard_records.thumb_file_name) を足す。縮小画像は
dashboard_recorder.DashboardRecorder が書く (記録を捨てた場合は、ファイルが無いことがある)。
Pipeline のスレッドだけから呼ぶため、ロックは持たない。書き込みの OSError はそのまま呼び出し側に出す
(Pipeline が捕まえて run.jsonl に記録し、判断のループは止めない)。
"""

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from switch2_agent.action_decision import PadAction
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.dashboard_records import thumb_file_name
from switch2_agent.decision_models.base import Decision
from switch2_agent.decision_request import DecisionRequest
from switch2_agent.operation_log import format_action
from switch2_agent.questions import Question
from switch2_agent.run_log import format_wall_time

REQUEST_LOG_FILE_NAME = "requests.jsonl"
FRAMES_DIR_NAME = (
    "frames"  # 要求に載せた画像を書くディレクトリー (実行のディレクトリーの下)
)


def frame_file_name(t: float) -> str:
    """経過秒 t (origin からの秒) の画像のファイル名 `<t をマイクロ秒に丸めて 12 桁の 0 埋め>.jpg` を返す。

    マイクロ秒は round(t * 1_000_000) (例: t=1.5 なら 000001500000.jpg、t=128.1 なら 000128100000.jpg)。
    キャプチャーの読み取りが遅れて溜まったフレームを続けて処理すると、別のフレームの時刻が 1 ミリ秒以内に
    並ぶことがあるため、ファイル名で別のフレームを区別できるようマイクロ秒まで使う。
    """
    return f"{round(t * 1_000_000):012d}.jpg"


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
    advisor_accepted   要求を受け付けた advisor の役割の name の集合 (どの役割も呼ばなかったときは空の集合)
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
    advisor_accepted: frozenset[str]
    result: Literal["ok", "error", "discarded"]
    error: str | None


class RequestLog:
    """実行のディレクトリー run_dir に、要求ごとの入出力を書く。

    requests.jsonl の行は json.dumps(..., ensure_ascii=False) で書く。1 行を書くたびにファイルを追記のモード
    (UTF-8) で開いて閉じるため、書いた行はすぐにファイルに書き出される (flush)。UTF-8 で書けない単独のサロゲートは
    `\\udXXX` の escape として書く (_append_line)。
    行の "time" は壁時計の時刻 (run_log.format_wall_time(time.time()) と同じ `HH:MM:SS.mmm` の形)。
    画像を書いたファイルの名前は、この RequestLog が覚えておき、同じ名前のファイルは 1 回だけ書く。

    属性:
        frames_dir       画像を書くディレクトリー (run_dir / FRAMES_DIR_NAME。save_frames が False なら作らない)
        path             要求のログのファイル (run_dir / REQUEST_LOG_FILE_NAME)
        written_frames   この RequestLog で書いた画像のファイル名の集合
        save_frames      要求に載せた画像を frames/ に書くか
        record_thumbs    request の行の images に、縮小画像の名前 thumb を足すか
    """

    def __init__(
        self, run_dir: Path, save_frames: bool = True, record_thumbs: bool = False
    ):
        """run_dir / REQUEST_LOG_FILE_NAME に追記で書く準備をする。

        save_frames が True なら run_dir / FRAMES_DIR_NAME を作り (既にあってもよい)、write で要求に載せた画像を書く。
        False なら frames/ を作らず、画像を書かず、request の行の images の file を null にする。
        record_thumbs が True なら、request の行の images の各要素に thumb を足す。
        どちらも build_request_record に同じ値を渡して行を組み立てる。
        run_dir は呼び出し側が作る。requests.jsonl に既にある行は残す。
        """
        self.frames_dir = run_dir / FRAMES_DIR_NAME
        self.path = run_dir / REQUEST_LOG_FILE_NAME
        self.save_frames = save_frames
        self.record_thumbs = record_thumbs
        if save_frames:
            self.frames_dir.mkdir(exist_ok=True)
        self.written_frames: set[str] = set()

    def write_header(
        self, model: str, instructions: str, questions: Sequence[Question]
    ) -> None:
        """要求に共通の内容を、header の行として requests.jsonl に 1 行追記する。

        行は {"event": "header", "time": 壁時計の時刻, "model": model, "instructions": instructions,
        "questions": [{"name": Question.name, "text": Question.text, "kind": Question.kind,
        "answer_type": Question.answer_type, "choices": [{"name": Choice.name, "description": Choice.description}, ...]},
        ...] (questions の順。choices は Question.choices の順で、確率の問いは空のリスト)}。
        run は、Pipeline を作った後に 1 回、model に起動引数 --model の値、instructions に
        decision_request.build_instructions()、questions に Pipeline に渡した問いの一覧を渡して呼ぶ。
        """
        self._append_line(
            {
                "event": "header",
                "time": format_wall_time(time.time()),
                "model": model,
                "instructions": instructions,
                "questions": [
                    {
                        "name": question.name,
                        "text": question.text,
                        "kind": question.kind,
                        "answer_type": question.answer_type,
                        "choices": [
                            {"name": choice.name, "description": choice.description}
                            for choice in question.choices
                        ],
                    }
                    for question in questions
                ],
            }
        )

    def write(self, entry: RequestLogEntry) -> None:
        """entry の画像の JPEG を frames/ に書き、entry を request の行として requests.jsonl に 1 行追記する。

        画像: save_frames が True なら、entry.request.images の各画像の JPEG を、run_dir / FRAMES_DIR_NAME /
        frame_file_name(image.frame_time - entry.origin) に書く。この RequestLog で既に書いた名前なら書かない。
        save_frames が False なら画像を書かない。
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
                                      "file": "frames/<frame_file_name の名前>" (save_frames が False なら null),
                                      "bytes": JPEG のバイト数, "after_previous_operation": after_previous_operation}。
                                      record_thumbs が True なら、各要素に "thumb":
                                      dashboard_records.thumb_file_name(frame_time) (frame_time は clock
                                      (time.monotonic) の値のまま。frames.jsonl の thumb と同じ名前) を足す
            state_text / history_text / previous_operation_text   entry.request の同名の値
            probabilities / choices / refusals / raw_response     entry.decision の同名の値 (decision が None なら null)。
                                                                  probabilities は確率の問いの確率、choices は選択肢の
                                                                  問いの選択肢ごとの確率
            action                    operation_log.format_action(entry.action) (action が None なら null)
            advisor_probabilities     entry.action.advisor_probabilities (役割の name → その役割の問いの確率の object。
                                      action が None なら null)
            advisor_requested         entry.action.advisor_requested の役割の name のリスト (action が None なら null)
            advisor_accepted          entry.advisor_accepted の役割の name のリスト (受け付けた役割が無ければ空のリスト)
            役割の name のリストは advisor_roles.ADVISOR_ROLES の順に並べる
        書き込みの OSError はそのまま出す。
        """
        if self.save_frames:
            self._write_frames(entry)
        self._append_line(
            build_request_record(
                entry,
                format_wall_time(time.time()),
                save_frames=self.save_frames,
                record_thumbs=self.record_thumbs,
            )
        )

    def _write_frames(self, entry: RequestLogEntry) -> None:
        """entry の画像の JPEG を frames/ に書く。この RequestLog で既に書いた名前なら書かない (write の docstring)。"""
        for image in entry.request.images:
            name = frame_file_name(image.frame_time - entry.origin)
            if name not in self.written_frames:
                (self.frames_dir / name).write_bytes(image.jpeg)
                # 書けたときだけ覚える (書けなかった画像は、次に載ったときに書き直す)
                self.written_frames.add(name)

    def _append_line(self, record: dict[str, object]) -> None:
        """record を JSON の 1 行として requests.jsonl に追記する (ファイルを開いて書き、閉じる)。

        ファイルは errors="backslashreplace" で開く。UTF-8 で書けない単独のサロゲート (応答の JSON の `\\ud800` の
        escape を読んだ値など) は、JSON の文字列の中で有効な `\\udXXX` の escape として書くため、json.loads で
        読み直すと同じ値になる。
        """
        line = json.dumps(record, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8", errors="backslashreplace") as file:
            file.write(line + "\n")


def build_request_record(
    entry: RequestLogEntry,
    wall_time: str,
    save_frames: bool = True,
    record_thumbs: bool = False,
) -> dict[str, object]:
    """entry を、requests.jsonl の request の行の辞書 (RequestLog.write の docstring のキー) にする (純粋な関数)。

    wall_time は行の "time" に入れる壁時計の時刻。画像の file は frame_file_name で決める (書くのは RequestLog.write)。
    save_frames が False なら画像の file を None にする。record_thumbs が True なら、画像の各要素に
    "thumb": dashboard_records.thumb_file_name(image.frame_time) を足す。
    """
    request = entry.request
    decision = entry.decision
    action = entry.action
    received = entry.received_at
    images = []
    for image in request.images:
        t = image.frame_time - entry.origin
        image_record: dict[str, object] = {
            "label": image.label,
            "t": t,
            "file": f"{FRAMES_DIR_NAME}/{frame_file_name(t)}" if save_frames else None,
            "bytes": len(image.jpeg),
            "after_previous_operation": image.after_previous_operation,
        }
        if record_thumbs:
            image_record["thumb"] = thumb_file_name(image.frame_time)
        images.append(image_record)
    return {
        "event": "request",
        "number": entry.number,
        "time": wall_time,
        "t_sent": entry.sent_at - entry.origin,
        "t_received": received - entry.origin if received is not None else None,
        "decide_s": received - entry.encoded_at if received is not None else None,
        "result": entry.result,
        "error": entry.error,
        "images": images,
        "state_text": request.state_text,
        "history_text": request.history_text,
        "previous_operation_text": request.previous_operation_text,
        "probabilities": decision.probabilities if decision is not None else None,
        "choices": decision.choices if decision is not None else None,
        "refusals": decision.refusals if decision is not None else None,
        "raw_response": (
            dict(decision.raw_response)
            if decision is not None and decision.raw_response is not None
            else None
        ),
        "action": format_action(action) if action is not None else None,
        "advisor_probabilities": (
            dict(action.advisor_probabilities) if action is not None else None
        ),
        "advisor_requested": (
            [
                role.name
                for role in ADVISOR_ROLES
                if role.name in action.advisor_requested
            ]
            if action is not None
            else None
        ),
        "advisor_accepted": [
            role.name for role in ADVISOR_ROLES if role.name in entry.advisor_accepted
        ],
    }
