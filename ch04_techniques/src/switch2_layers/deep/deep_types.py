"""deep 層の型: 文書の種類、deep を呼ぶきっかけ、文書のキー、ジョブと結果、DeepRunner と要求の受け口の Protocol。

文書 (deep のジョブの単位) は、種類と場面の組 (DocumentKey) で区別する。
操作説明 (controls) と攻略情報 (tips) は場面ごとの文書と共通の文書 (場面 None) があり、
大方針 (strategy)・助言 (advice)・マップ (map)・ゲームの性質 (game) は場面に依らない (場面はいつも None)。
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class DocumentKind(StrEnum):
    """deep が作る文書の種類。値は deep/<種類>-<連番3桁>/ のディレクトリー名と、deep-once の --doc に使う。"""

    CONTROLS = "controls"  # 操作説明
    TIPS = "tips"  # 攻略情報
    STRATEGY = "strategy"  # 大方針
    ADVICE = "advice"  # 詰まったときの助言
    MAP = "map"  # マップ
    GAME = "game"  # ゲームの性質 (ジャンル・進み方・マップの形式・ダメージとゲームオーバーの条件など)


# 文書の種類の並び。自動の呼び出しの判定と、記録の並びはこの順にする
DOCUMENT_KINDS: tuple[DocumentKind, ...] = tuple(DocumentKind)
# 場面ごとの文書を持つ種類。これ以外の種類の文書は、場面に依らない
SCENE_DOCUMENT_KINDS: frozenset[DocumentKind] = frozenset(
    {DocumentKind.CONTROLS, DocumentKind.TIPS}
)


class DeepTrigger(StrEnum):
    """deep のジョブを起動したきっかけ。job.json と run.jsonl に書く。"""

    SURFACE = "surface"  # surface の advisor 判定
    NEW_SCENE = "new_scene"  # middle が初めての場面に名前を付けた
    AUTO = "auto"  # 同じ文書の前回のジョブの終了から決まった秒数がたった
    MANUAL = "manual"  # deep-once で手で起動した
    # 画面の停滞の計測で、新しい画面が出ていない秒数がしきい値を超えた (advice だけ)
    PROGRESS = "progress"


@dataclass(frozen=True)
class DocumentKey:
    """deep のジョブの単位になる文書 (種類と場面の組)。

    kind       文書の種類
    scene_id   場面 ID (scene-001 の形)。共通の文書と、場面に依らない種類では None
    作るときは notes.note_paths.document_key で、種類に応じて場面をそろえる。
    """

    kind: DocumentKind
    scene_id: str | None


@dataclass(frozen=True)
class DeepJob:
    """deep の 1 回のジョブの依頼。job.json に書く値でもある。

    kind                      文書の種類
    scene_id                  場面 ID。共通の文書と、場面に依らない種類では None
    trigger                   起動したきっかけ
    requested_t               要求の時刻 (実行の経過秒 t)
    previous_latest_frame_t   前回の、正常に終わった同じ文書のジョブに渡した最新のフレームの時刻。正常に終わった前回が
                              無い、または前回にフレームが無ければ None
    latest_frame_t            今回の最新のフレームの時刻 (frames.jsonl の最後の t)。まだフレームが無ければ None
    """

    kind: DocumentKind
    scene_id: str | None
    trigger: DeepTrigger
    requested_t: float
    previous_latest_frame_t: float | None
    latest_frame_t: float | None

    @property
    def key(self) -> DocumentKey:
        """このジョブの文書のキー DocumentKey(kind, scene_id) を返す。"""
        return DocumentKey(self.kind, self.scene_id)


@dataclass(frozen=True)
class DeepResult:
    """DeepRunner.run の結果。

    ok        文書の更新を終えたら True、更新できなかったら False
    message   結果の説明 (run.jsonl の deep_end に書く)。無ければ空文字列
    stopped   DeepRunner.stop で止められて戻ったら True (このとき ok は False)。それ以外は False
    """

    ok: bool
    message: str = ""
    stopped: bool = False


class DeepRunner(Protocol):
    """deep の 1 回のジョブを実行する。実装は、Claude Code を起動する deep.claude_code_runner.ClaudeCodeDeepRunner と、
    何もしないフェイク deep.fake_deep.FakeDeepRunner。

    run はジョブのスレッドから、stop と kill はスケジューラーのスレッド (タイムアウトの判定と終了処理) から呼ばれる。
    同じ job_dir の run と stop・kill が同時に呼ばれてもよいようにする。
    """

    def run(self, job: DeepJob, job_dir: Path) -> DeepResult:
        """job_dir (deep/<種類>-<連番3桁>/。job.json を用意済み) で job を実行し、結果を返す。

        別スレッドから呼ばれる。文書は notes/ の本体を直接編集する (job.json の documents の文書だけ)。
        stop で止められたら、DeepResult(ok=False, stopped=True) を返して戻る。
        """
        ...

    def stop(self, job_dir: Path) -> None:
        """job_dir のジョブに止める要求を出し、止まるのを待たずにすぐ戻る。

        止められた run は DeepResult(ok=False, stopped=True) を返して戻る。そのジョブの run が既に戻っていれば
        何もしない。例外は出さない。
        """
        ...

    def kill(self, job_dir: Path) -> None:
        """job_dir のジョブを強制終了し、終わるのを待たずにすぐ戻る。

        stop の後、決まった秒数たっても run が戻らないときに呼ばれる。kill の後の run の戻り値は使われない。
        そのジョブの run が既に戻っていれば何もしない。例外は出さない。
        """
        ...


@dataclass(frozen=True)
class DeepRequestResult:
    """deep を呼ぶ要求の扱いの結果。

    started   ジョブを起動したら True、捨てたら False
    job_dir   起動したジョブのディレクトリー。捨てたら None
    reason    捨てた理由 (run.jsonl の deep_skipped の reason)。起動したら None
    """

    started: bool
    job_dir: Path | None = None
    reason: str | None = None


class DeepRequestSink(Protocol):
    """deep を呼ぶ要求の受け口。run では deep_scheduler.DeepScheduler を使う。"""

    def request(
        self, kind: DocumentKind, trigger: DeepTrigger, scene_id: str | None
    ) -> DeepRequestResult:
        """種類 kind の文書の deep を、きっかけ trigger で要求する。

        scene_id は呼んだ側が知っている現在の場面 (無ければ None)。controls と tips ではその場面の文書
        (None なら共通の文書) を対象にし、ほかの種類では無視する。
        """
        ...
