"""本体の画面 (HOME メニュー・ユーザー選択など) で middle が操作の列を決めるときの型。

本体の画面の middle (middle.system_screen_middle.SystemScreenMiddle) は、目的 (SystemGoal) と今の画面 (画像と照合の結果) と
これまでの呼び出しの履歴 (SystemStepRecord) から、操作の列 (SystemScreenOutput.operations) を決める。列は
system_screen.system_operation_runner.SystemOperationRunner が、照合の結果を確かめながら前から順に送り、送った数と
打ち切った位置と理由を SystemRunResult で返す。

画面の名前は、照合の見本の名前 (呼び出し側が渡す一覧。home_menu・controllers・controller_order・user_select など) と
NO_SCREEN_NAME ("none"。見本のどの画面でもない。ゲームの画面など) を使う。照合の結果の None (見本のどの画面でもない) は、
操作の screen では NO_SCREEN_NAME で表す。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

# 見本のどの画面でもないこと (ゲームの画面・切り替わりの途中の画面など) を表す、画面の名前
NO_SCREEN_NAME = "none"
# 入力の履歴に入れる、これまでの呼び出しの最大の数 (新しい方から数える)
MAX_HISTORY_ENTRIES = 10
# 決定のボタン。操作の列の先頭の操作にだけ置ける。入力の「この段階で送った決定」には、このボタンを含む press を並べる
DECISION_BUTTON = "A"

# 操作の種類: press (ボタンを押す)、wait (何も送らずに待つ)
SystemOperationKind = Literal["press", "wait"]


class SystemGoal(StrEnum):
    """本体の画面での目的。目的ごとの手順の文は middle.system_screen_instructions.goal_text が返す。"""

    PREPARE = "prepare"  # 準備: コントローラーを 1P に登録し、指定のソフトを指定のユーザーで起動する
    RECOVER = "recover"  # 復帰: 指定のソフトのゲームの画面に戻る
    CLEAN_UP = "clean_up"  # 片付け: HOME メニューに戻り、指定のソフトを終了する


class SystemCutReason(StrEnum):
    """操作の列の実行を打ち切った理由 (SystemRunResult.cut_reason の値)。

    SEND_FAILED 以外は、ある操作を送る前に打ち切った理由 (その操作から後は送らない)。SEND_FAILED は、送った press の
    送信が失敗して打ち切った理由で、失敗した press が列の最後の操作でも入る。
    """

    # 操作の screen が、送る前の照合の結果の画面と違った
    SCREEN_MISMATCH = "screen_mismatch"
    # 2 つ目以降の操作の前に、待っても照合の結果が届かなかった
    NO_JUDGMENT = "no_judgment"
    # 2 つ目以降の操作に決定のボタン (A) があった
    DECISION_NOT_FIRST = "decision_not_first"
    # 止める合図が立った (全体を止めるときに出力が止められた場合を含む)
    STOPPED = "stopped"
    # press の送信 (PadOutput.apply の送信か、PadOutput のタイマーが送る離すレポートなど) が失敗した。失敗した press は
    # 届いたかもしれないものとして数える
    SEND_FAILED = "send_failed"


@dataclass(frozen=True)
class SystemOperation:
    """本体の画面での操作 1 つ (middle の出力の operations の要素)。

    screen    この操作を送る前に映っているはずの画面の名前 (見本の名前か NO_SCREEN_NAME)
    kind      press (ボタンを押す) か wait (何も送らずに待つ)
    buttons   press で押すボタン名 (switch2_remote.controller_state.BUTTON_ORDER の名前。出力に書かれた順)。
              press では 1 個以上で、十字キーは 1 方向まで。wait では空
    seconds   press では、None なら短押し (hardware.pad_output.TAP_PRESS_S だけ押して離す)、数なら押し続けの秒数
              (0 より大きく hardware.pad_output.MAX_HOLD_SECONDS 以下)。wait では待つ秒数
              (0 より大きく middle.system_screen_schema.MAX_WAIT_SECONDS 以下)
    値の範囲は middle.system_screen_schema.parse_system_screen_output が確かめる。この型自体は値を検証しない。
    """

    screen: str
    kind: SystemOperationKind
    buttons: tuple[str, ...]
    seconds: float | None


@dataclass(frozen=True)
class SystemStepRecord:
    """同じ段階 (目的) でのこれまでの呼び出し 1 回分の履歴 (SystemScreenInput.history の要素)。

    t               呼び出しの時刻 (その回の SystemScreenInput.t)
    judged_screen   その回に渡した照合の結果の画面の名前。見本のどの画面でもなければ None
    operations      その回に middle が返した操作の列
    sent_count      そのうち送った操作の数 (SystemRunResult.sent_count)
    cut_reason      打ち切ったときはその理由 (SystemRunResult.cut_reason)。最後まで送ったら None (SEND_FAILED は、列の
                    最後の操作の送信が失敗したときにも入る)
    """

    t: float
    judged_screen: str | None
    operations: tuple[SystemOperation, ...]
    sent_count: int
    cut_reason: SystemCutReason | None


@dataclass(frozen=True)
class SystemScreenInput:
    """SystemScreenMiddle.decide の入力 (1 回の呼び出しの入力)。

    t                入力を作った時刻 (run_clock.RunClock の t)
    goal             目的
    software_name    指定のソフトの名前 (起動・再開するソフト。呼び出し側が渡す)
    user_name        指定のユーザーの名前 (ユーザー選択で選ぶユーザー。呼び出し側が渡す)
    frame_jpeg       今の画像 (キャプチャーの最新のフレーム) の JPEG のバイト列
    frame_t          今の画像のフレームの時刻
    judged_screen    照合の結果の画面の名前。見本のどの画面でもない (または照合の結果がまだ無い) なら None
    judged_frame_t   照合したフレームの時刻。照合の結果がまだ無ければ None
    history          この段階でのこれまでの呼び出しの履歴 (古い順)。要求の履歴の節には新しい方から MAX_HISTORY_ENTRIES 件を
                     入れ、送った決定の節には履歴のすべてを使う
    """

    t: float
    goal: SystemGoal
    software_name: str
    user_name: str
    frame_jpeg: bytes
    frame_t: float
    judged_screen: str | None
    judged_frame_t: float | None
    history: tuple[SystemStepRecord, ...]


@dataclass(frozen=True)
class SystemScreenOutput:
    """SystemScreenMiddle.decide の出力。

    observation   画面の様子とカーソルの位置 (middle が書いた文)
    operations    送る操作の列 (0〜middle.system_screen_schema.MAX_OPERATIONS 個。前から順に送る)
    done          目的を果たした (準備・復帰ではゲームの画面が映った、片付けでは指定のソフトを終了して HOME メニューに
                  戻った) なら True
    """

    observation: str
    operations: tuple[SystemOperation, ...]
    done: bool


@dataclass(frozen=True)
class SystemRunResult:
    """操作の列の実行 (SystemOperationRunner.run) の結果。

    sent_count        送った操作の数 (wait は待ち始めたもの、押せないボタンを外して何も押さなかった press も数える)。
                      打ち切ったときは cut_index と同じ値。ただし cut_reason が SEND_FAILED のときは cut_index + 1
                      (送信に失敗した press を含む)
    cut_index         打ち切った操作の位置 (0 から数える。その操作から後は送っていない)。最後まで送ったら None。
                      cut_reason が SEND_FAILED のときは送信に失敗した press の位置 (その press は届いたかもしれず、
                      その後の操作は送っていない)。その press が列の最後の操作でも None にしない
    cut_reason        打ち切った理由。最後まで送ったら None (SEND_FAILED は、列の最後の操作の送信が失敗したときにも入る)
    blocked_buttons   押せないボタンとして hardware.pad_output.PadOutput.apply が外したボタン名 (送った操作のすべての分)
    """

    sent_count: int
    cut_index: int | None
    cut_reason: SystemCutReason | None
    blocked_buttons: frozenset[str]
