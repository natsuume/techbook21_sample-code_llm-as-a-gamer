"""主ループ (orchestrator.main_loop.OrchestratorMainLoop) と、主ループのエージェントのセッションとの境界の型。

主ループは、エージェント (Claude Code など) の入出力の形を知らずに、このモジュールの型だけを通してセッションを使う。
エージェントごとの実装は、この型を満たすクラスを作る (Claude Code の実装は orchestrator.claude_main_loop_session)。
このモジュールは型だけを置き、I/O を扱わない。

    MainLoopSessionFactory   セッションを 1 つ作るもの。主ループは、セッションを始めるたびに create を呼ぶ
    MainLoopSession          セッション 1 つ (エージェントの子プロセス 1 つ)。start で起動し、send_turn_in_background で
                             1 ターンの入力を送り、read で出来事を受け取り、close で終える
    出来事 (MainLoopSessionEvent。read が返す、エージェントの出力を主ループが使う形に直したもの):
        AssistantText    アシスタントの文が届いた (主ループは、ターンの最後の文から JSON を読む)
        ContextUsage     セッションの使用量 (今のコンテキストの大きさ) が分かった
        AutoCompaction   エージェントの自動の要約が起きた (セッションは続く)
        TurnResult       ターン (送った入力 1 つへの応答) が終わった
        SessionEnded     セッションの出力が終わった (子プロセスが終わった)
    1 つの出力 (Claude Code では stream-json の 1 行) から複数の出来事ができるときは、AssistantText → ContextUsage →
    AutoCompaction → TurnResult の順に、read が 1 つずつ返す。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from switch2_layers.orchestrator.pending_send import PendingSend
from switch2_layers.orchestrator.turn_input import TurnImage


@dataclass(frozen=True)
class AssistantText:
    """アシスタント (主ループのエージェント) の文が届いた。

    text   届いた文。主ループは、そのターンで最後に届いた文から JSON を読む
    """

    text: str


@dataclass(frozen=True)
class ContextUsage:
    """セッションの使用量 (今のコンテキストの大きさ) が分かった。

    tokens   コンテキストの大きさ (トークン)。主ループは、最後に届いた値をそのセッションの使用量にし (足し合わせない)、
             --rotate-tokens を超えたらセッションを入れ替える
    """

    tokens: int


@dataclass(frozen=True)
class AutoCompaction:
    """エージェントの自動の要約 (コンテキストの圧縮) が起きた。セッションは続く。

    metadata   要約の情報 (orchestrator/sessions.jsonl の compact の行の metadata にそのまま書く)。無ければ None
    """

    metadata: object | None


@dataclass(frozen=True)
class TurnResult:
    """ターン (送った入力 1 つへの応答) が終わった。

    is_error   ターンが誤り (API の誤りなど) で終わったなら True。主ループは JSON を読まずにセッションを終える
    """

    is_error: bool


@dataclass(frozen=True)
class SessionEnded:
    """セッションの出力が終わった (子プロセスが終わった)。read はこれを返した後も、毎回これを返す。"""


# read が返す出来事 1 つ
MainLoopSessionEvent = (
    AssistantText | ContextUsage | AutoCompaction | TurnResult | SessionEnded
)


class MainLoopSession(Protocol):
    """主ループのエージェントのセッション 1 つ (エージェントの子プロセス 1 つ)。

    start・send_turn_in_background・read・close は 1 つのスレッド (主ループ) から呼ぶ。
    """

    def start(self) -> None:
        """セッションを起動する。起動できなければ OSError を出す。"""
        ...

    def send_turn_in_background(
        self, text: str, images: Sequence[TurnImage]
    ) -> PendingSend:
        """1 ターンの入力 (テキスト text と、その後に並べる画像 images。画像が無ければ空) を送り始め、送り終わるのを
        待たずに戻る。

        戻り値の PendingSend.wait で、送り終わったか (送れたら True、送れなかったら False) を待つ。start の前と close の
        後は、送れなかった (False) として終わった PendingSend を返す。前の入力を送り終わる前に呼んではいけない。
        """
        ...

    def read(self, timeout_s: float) -> MainLoopSessionEvent | None:
        """次の出来事を、最大 timeout_s 秒待って返す。

        timeout_s 秒のうちに何も届かなければ None を返す。出来事にならない出力 (Claude Code の system/init の行など) が
        届いたときも、待ち続けずに None を返す (呼び出し側が、停止の合図などを確かめてから読み直せるように)。
        1 つの出力から複数の出来事ができたら、モジュールの docstring の順にため、2 つ目からは待たずに 1 つずつ返す。
        出力が終わったら SessionEnded を返す (ためた出来事を先に返す)。start の前は SessionEnded を返す。
        """
        ...

    def close(self, grace_s: float) -> int | None:
        """セッションを終わらせ、子プロセスの終了コードを返す。終わらせられなければ None。

        grace_s は、終わるのを待つ 1 回の上限の秒数 (Claude Code の実装では、標準入力を閉じてから SIGTERM まで・SIGTERM
        から SIGKILL までに待つ秒数)。start の前に呼んだら何もせずに None を返す。2 回目以降の呼び出しは、1 回目と同じ
        値を返す (何もしない)。
        """
        ...


class MainLoopSessionFactory(Protocol):
    """主ループのセッションを作るもの。主ループは、セッションを始めるたびに create を呼ぶ。"""

    def create(self, number: int, stream_path: Path) -> MainLoopSession:
        """番号 number (1 始まり) のセッションを、起動せずに作って返す。

        stream_path は、セッションの出力を 1 つずつ書き残すファイル (orchestrator/session-<NNN>/stream.jsonl。親の
        ディレクトリーは主ループが作る)。起動の組み立てに使えない値 (指示の文・実行ディレクトリーのパスなど) は、
        例外 (ValueError など) で知らせる。主ループは create の例外を (OSError も) 起動の失敗としては扱わず、想定外の
        例外として止まる。起動の失敗は MainLoopSession.start の OSError で知らせる。
        """
        ...
