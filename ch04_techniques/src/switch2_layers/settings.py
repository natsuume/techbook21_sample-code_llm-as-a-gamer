"""起動引数の値をまとめた dataclass と、値の範囲の検証。

サブコマンドごとに 1 つの dataclass を持つ。cli がコマンドラインの引数をこれに直し、
*_settings_errors で範囲を確かめてから使う。範囲の誤りのメッセージには、起動引数の名前 (--middle-interval 等) を含める。
キャプチャーの ffmpeg の出力と処理のフレームレートは起動引数にせず、定数 CAPTURE_FFMPEG_FPS と CAPTURE_PROCESS_FPS に
固定する。デバイスから受け取るフレームレートは、run の --device-fps (switch2_remote.capture.DEVICE_FPS_CHOICES の
30 か 60) で選ぶ。
middle の実装は --middle (MiddleKind) で選ぶ。luna に要る API キーは起動引数にせず、環境変数 OPENAI_API_KEY_ENV から
読む (openai_api_key_errors で有無を確かめる)。run のゲーム用の middle (luna) の reasoning effort は --middle-effort
(MiddleEffort) で選ぶ。surface の実装は --surface (SurfaceKind) で選ぶ。jev に要る API キーは
環境変数 TYPESAFE_API_KEY_ENV から読む (typesafe_api_key_errors で有無を確かめる)。deep のジョブを実行するものは
--deep (DeepRunnerChoice) で選ぶ。安全の監視の実装は --safety (SafetyKind) で選ぶ。jev に要る API キーは surface と
同じ環境変数 TYPESAFE_API_KEY_ENV から読む (safety_api_key_errors で有無を確かめる)。
API キーは dataclass に持たせない (run.jsonl の start に起動引数を書くため)。
run の型は --mode (RunMode) で選ぶ。advisor は surface・middle・deep の 3 層、orchestrator は主ループ
(orchestrator.main_loop) と、操作の実行と監視 (orchestrator.orchestrator_controller) を動かす。delegate は主ループと、
操作の実行と委任のループ (orchestrator_controller の委任型と orchestrator.delegate_loop) を動かす。orchestrator と
delegate の主ループのエージェントは --agent (AgentKind) で選ぶ (Claude Code か Codex。Codex はユーザーのログインで
動くため、API キーを確かめない)。orchestrator の監視と
delegate の委任は常に Jev を使うため、--safety に依らず環境変数 TYPESAFE_API_KEY_ENV が要る
(orchestrator_api_key_errors と delegate_api_key_errors で有無を確かめる)。
run の指定のソフトの名前 (--software) と指定のユーザーの名前 (--user) は、advisor では cli が必須の引数にして、空か
空白だけの値を引数の誤りにする。orchestrator と delegate では使わず、無ければ空文字列にする (RunSettings の既定値の
空文字列は、テストで作るときと orchestrator・delegate のため)。
"""

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from switch2_remote.capture import DEFAULT_DEVICE_FPS, DEVICE_FPS_CHOICES
from switch2_remote.controller_bridge import DEFAULT_COM_PORT, is_valid_com_port

from switch2_layers.deep.deep_types import SCENE_DOCUMENT_KINDS, DocumentKind
from switch2_layers.notes.note_paths import is_scene_id
from switch2_layers.orchestrator.claude_session_command import (
    AUTOCOMPACT_MAX_TOKENS,
    ORCHESTRATOR_EFFORT,
)
from switch2_layers.orchestrator.codex_session_command import DEFAULT_CODEX_MODEL
from switch2_layers.orchestrator.delegate_loop import (
    DEFAULT_DELEGATE_IMAGE_WIDTH,
    DEFAULT_MAX_DELEGATE_ERRORS,
    DELEGATE_IMAGE_WIDTH_MAX,
    DELEGATE_IMAGE_WIDTH_MIN,
)
from switch2_layers.orchestrator.main_loop import (
    DEFAULT_MAX_DECISION_ERRORS,
    DEFAULT_ROTATE_TOKENS,
    DEFAULT_TURN_TIMEOUT_S,
)
from switch2_layers.orchestrator.orchestrator_controller import (
    DEFAULT_MAX_MONITOR_ERRORS,
    DEFAULT_MONITOR_THRESHOLD,
)
from switch2_layers.progress.screen_progress import DEFAULT_PROGRESS_WINDOW_S
from switch2_layers.progress.stuck_advice_trigger import (
    DEFAULT_STUCK_ADVICE_SECONDS,
    DEFAULT_SYSTEM_ADVICE_SECONDS,
)
from switch2_layers.surface.jev_client import API_KEY_ENV as JEV_API_KEY_ENV


class MiddleKind(StrEnum):
    """--middle の値 (middle の分析の実装)。

    FAKE   骨組みのフェイク (middle.fake_middle.FakeMiddleAnalyzer)。API を呼ばない
    LUNA   gpt-6-luna を Responses API で呼ぶ (middle.luna_middle.LunaMiddleAnalyzer)。環境変数 OPENAI_API_KEY が要る
    """

    FAKE = "fake"
    LUNA = "luna"


DEFAULT_MIDDLE_KIND = MiddleKind.LUNA
# --middle luna の API キーを読む環境変数の名前
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


class MiddleEffort(StrEnum):
    """run の --middle-effort の値 (ゲーム用の middle (luna) の要求の reasoning の effort)。

    値の文字列を、そのまま要求の reasoning.effort に入れる (middle.luna_request.build_luna_payload)。
    --middle fake では使わない。本体の画面の middle の effort (middle.system_screen_request の
    SYSTEM_SCREEN_REASONING_EFFORT) には効かない。
    """

    NONE = "none"
    LOW = "low"


# 既定は middle.luna_request.REASONING_EFFORT と同じ値
DEFAULT_MIDDLE_EFFORT = MiddleEffort.NONE

# switch2_remote.capture.Capture に渡すフレームレート。process_fps は一定間隔の保存のフレームレートの上限でもある。
# ffmpeg の出力のフレームレートは、--device-fps のどちらの値 (30 か 60) でも、それ以下に収まる値にする
CAPTURE_FFMPEG_FPS = 30
CAPTURE_PROCESS_FPS = 10

DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_INTERVAL_FPS = 5.0
INTERVAL_FPS_MAX = float(CAPTURE_PROCESS_FPS)
DEFAULT_INTERVAL_WIDTH = 640
INTERVAL_WIDTH_MIN = 64
INTERVAL_WIDTH_MAX = 1280  # switch2_remote.capture.DEVICE_WIDTH
DEFAULT_MAX_SURFACE_ERRORS = 5
DEFAULT_MIDDLE_INTERVAL_S = 5.0
MIDDLE_INTERVAL_MIN_S = 1.0
MIDDLE_INTERVAL_MAX_S = 10.0
DEFAULT_MIDDLE_WINDOW_S = 10.0
DEFAULT_MAX_MIDDLE_ERRORS = 5
DEFAULT_DEEP_MIN_START_INTERVAL_S = 30.0
DEFAULT_DEEP_AUTO_SECONDS = 60.0
DEFAULT_DEEP_TIMEOUT_S = 300.0
DEFAULT_THRESHOLD_ADVISOR = 0.5
DEFAULT_BOX_REQUERY_EVERY = 5
# surface の押し方の選び方で、押し続けにするのに要る hold と tap の確率の差
DEFAULT_HOLD_MARGIN = 0.1


class SurfaceKind(StrEnum):
    """run の surface の実装 (--surface の値)。"""

    JEV = "jev"  # TypeSafe Jev を呼ぶ (surface.jev_surface.JevSurfaceDecider)
    FAKE = "fake"  # 骨組みのフェイク (surface.fake_surface.FakeSurfaceDecider)


DEFAULT_SURFACE = SurfaceKind.JEV
# --surface jev の API キーを読む環境変数の名前
TYPESAFE_API_KEY_ENV = JEV_API_KEY_ENV


class SurfaceMapMode(StrEnum):
    """surface に渡すマップ (--surface-map の値)。"""

    TEXT = "text"  # 現在地の場所のマップを文字で渡す (notes.surface_map_text)
    NONE = "none"  # 渡さない


DEFAULT_SURFACE_MAP = SurfaceMapMode.TEXT


class DeepRunnerChoice(StrEnum):
    """deep のジョブを実行するもの (run と deep-once の --deep)。値は --deep に書く名前。

    run では RunSettings.deep に持たせ、run.jsonl の start の args に値の文字列で書く (どちらで動かしたかを記録に残す)。
    deep-once では、cli の処理の関数が Namespace の deep から作る。
    """

    CLAUDE = "claude"  # Claude Code (deep.claude_code_runner.ClaudeCodeDeepRunner)
    FAKE = "fake"  # 何もせず成功を返すフェイク (deep.fake_deep.FakeDeepRunner)


DEFAULT_DEEP_RUNNER = DeepRunnerChoice.CLAUDE


class SafetyKind(StrEnum):
    """run の安全の監視の実装 (--safety の値)。"""

    JEV = "jev"  # TypeSafe Jev に問う (safety.jev_safety.JevSafetyChecker)
    FAKE = "fake"  # すべての確率を 0 として返すフェイク (safety.fake_safety.FakeSafetyChecker)


DEFAULT_SAFETY = SafetyKind.JEV
DEFAULT_SAFETY_INTERVAL_S = 1.0
SAFETY_INTERVAL_MIN_S = 0.2
SAFETY_INTERVAL_MAX_S = 10.0
DEFAULT_THRESHOLD_SAFETY = 0.7
DEFAULT_MAX_SAFETY_ERRORS = 3
# surface の先頭のタスクの 2 つの問い (task_done・task_cancel) のしきい値
DEFAULT_THRESHOLD_TASK = 0.5
# 現在の場面の最も古いタスクの続いた秒数がこれを超えたら、middle を呼ぶきっかけ (stuck) にする
DEFAULT_TASK_STUCK_SECONDS = 30.0


class RunMode(StrEnum):
    """run の型 (--mode の値)。"""

    ADVISOR = "advisor"  # surface・middle・deep の 3 層 (app.LayersParts を組み立てる)
    # 主ループと、操作の実行と監視 (app.OrchestratorLayersParts を組み立てる)
    ORCHESTRATOR = "orchestrator"
    # 主ループと、操作の実行と委任のループ (app.OrchestratorLayersParts を組み立てる)
    DELEGATE = "delegate"


DEFAULT_RUN_MODE = RunMode.ADVISOR
# --orchestrator-effort で選べる値 (主ループの Claude Code の --effort と、Codex の turn/start の effort に渡す。
# Codex の既定のモデル gpt-6.2-sol は、この 5 つに対応している)
ORCHESTRATOR_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


class AgentKind(StrEnum):
    """run --mode orchestrator と --mode delegate の主ループのエージェント (--agent の値)。"""

    # Claude Code (orchestrator.claude_main_loop_session)
    CLAUDE = "claude"
    # OpenAI Codex の codex app-server (orchestrator.codex_main_loop_session)。モデルは --codex-model で選ぶ
    CODEX = "codex"


DEFAULT_AGENT = AgentKind.CLAUDE


@dataclass(frozen=True)
class RunSettings:
    """run の起動引数。

    software_name               指定のソフトの名前 (--software。advisor では必須)。本体の画面の middle の入力に入れる。
                                orchestrator では使わない
    user_name                   指定のユーザーの名前 (--user。advisor では必須)。本体の画面の middle の入力に入れる。
                                orchestrator では使わない
    runs_dir                    実行ディレクトリーを作る親 (--runs-dir、既定 runs)
    device_fps                  デバイスから受け取るフレームレート (--device-fps、既定 60。
                                switch2_remote.capture.DEVICE_FPS_CHOICES の 30 か 60)。PC との USB の接続で 60fps を
                                受け取れないときは 30 にする
    com_port                    ブリッジが開く COM ポート (--com-port、既定 COM3。Pico 2 W の Bluetooth の発信側)。
                                switch2_remote.controller_bridge.is_valid_com_port の形 (COM と 1 以上の整数。
                                大文字小文字を区別しない)。最初の起動にも復旧での起動し直しにも使う
    interval_fps                一定間隔のフレームの保存のフレームレート (--interval-fps、既定 5。0 より大きく INTERVAL_FPS_MAX 以下)
    interval_width              一定間隔のフレームを縮小する幅 (--interval-width、既定 640。INTERVAL_WIDTH_MIN〜INTERVAL_WIDTH_MAX)
    max_surface_errors          surface の decide の失敗がこの回数続いたら全体を止める (--max-surface-errors、既定 5。1 以上)
    middle_interval_s           middle の前回の応答から次を始めるまでの秒数 (--middle-interval、既定 5.0。1〜10)
    middle_window_s             middle に渡す一定間隔のフレームの範囲 (直近の秒数) (--middle-window、既定 10。0 より大きい有限の値)
    max_middle_errors           middle の analyze の失敗がこの回数続いたら全体を止める (--max-middle-errors、既定 5。1 以上)
    deep_min_start_interval_s   同じ文書の deep のジョブを、前回の起動からこの秒数たつまで、起動も保留もしない
                                (--deep-min-start-interval、既定 30。0 以上の有限の値)
    deep_auto_seconds           同じ文書の前回のジョブの終了からこの秒数たったら、自動で deep を呼ぶ
                                (--deep-auto-seconds、既定 60。0 より大きい有限の値で、deep_min_start_interval_s 以上。
                                最小間隔より短いと、自動の呼び出しが判定の周期ごとに最小間隔で捨てられ続けるため)
    deep_timeout_s              deep のジョブの実行時間の上限 (--deep-timeout、既定 300。0 より大きい有限の値)
    middle                      middle の分析の実装 (--middle、既定 luna。fake か luna)。luna では環境変数
                                OPENAI_API_KEY が要る (openai_api_key_errors)
    middle_effort               ゲーム用の middle (luna) の要求の reasoning の effort (--middle-effort、既定 none。
                                MiddleEffort の none か low)。--middle fake と本体の画面の middle には効かない
    surface                     surface の実装 (--surface、既定 jev)。jev のときは環境変数 TYPESAFE_API_KEY が要る
    threshold_advisor           advisor 判定のしきい値。確率がこれを超えた種類の deep を呼ぶ
                                (--threshold-advisor、既定 0.5。0 以上 1 以下)
    box_requery_every           前回の問い直しからこの回数の要求がたった対象の box を Jev に問い直す
                                (--box-requery-every、既定 5。1 以上)
    hold_margin                 surface のボタンと十字キーで、hold の確率が tap の確率をこの値より大きく上回ったときだけ
                                押し続けにする (--hold-margin、既定 0.1。0 以上 1 以下)
    surface_map                 surface に現在地の場所のマップを渡すか (--surface-map、既定 text。text は文字で渡し、
                                none は渡さない)。jev の surface だけで使う
    deep                        deep のジョブを実行するもの (--deep、既定 claude。claude か fake)
    safety                      安全の監視の実装 (--safety、既定 jev)。jev のときは環境変数 TYPESAFE_API_KEY が要る
                                (safety_api_key_errors)
    safety_interval_s           安全の監視が要求を送る間隔 (秒) (--safety-interval、既定 1.0。
                                SAFETY_INTERVAL_MIN_S 以上 SAFETY_INTERVAL_MAX_S 以下)
    threshold_safety            安全の問いのしきい値。確率がこれを超えた画面で止める
                                (--threshold-safety、既定 0.7。0 より大きく 1 より小さい値)
    threshold_task              surface の先頭のタスクの 2 つの問い (task_done・task_cancel) のしきい値。確率がこれを
                                超えた要求が、同じタスクについて続けて 2 回あったら成立にする
                                (--threshold-task、既定 0.5。0 以上 1 以下)
    max_safety_errors           安全の監視の失敗がこの回数続いたら全体を止める (--max-safety-errors、既定 3。1 以上)
    progress_window_s           画面の停滞の計測で、新しい画面かを決めるときに見る範囲 (直近の秒数)
                                (--progress-window、既定 300。0 より大きい有限の値)
    stuck_advice_seconds        新しい画面が出ていない秒数がこれを超えたら、advice の deep をきっかけ progress で求める。
                                超えたまま続く間は、この秒数ごとに求める (--stuck-advice-seconds、既定 60。
                                0 より大きい有限の値)
    task_stuck_seconds          現在の場面の最も古いタスクの続いた秒数がこれを超えたら、間隔を待たずに middle を呼ぶ
                                (きっかけ stuck)。超えたまま続く間は、この秒数ごとに呼ぶ (--task-stuck-seconds、既定 30。
                                0 より大きい有限の値)
    system_advice_seconds       本体の画面の段階 (prepare・recover) がこの秒数を超えて続いたら、advice の deep を
                                きっかけ system で求める。超えたまま続く間は、この秒数ごとに求める
                                (--system-advice-seconds、既定 90。0 より大きい有限の値)
    mode                        run の型 (--mode、既定 advisor。advisor・orchestrator・delegate のどれか)
    orchestrator_effort         主ループの Claude Code の --effort の値 (--orchestrator-effort、既定
                                claude_session_command.ORCHESTRATOR_EFFORT の medium。ORCHESTRATOR_EFFORTS のどれか)
    rotate_tokens               主ループのセッションの使用量がこれを超えたら、引き継ぎを書かせてセッションを入れ替える
                                (--rotate-tokens、既定 main_loop.DEFAULT_ROTATE_TOKENS の 120000。1 以上で
                                claude_session_command.AUTOCOMPACT_MAX_TOKENS 未満)
    turn_timeout_s              主ループの 1 ターンの上限の秒数 (--turn-timeout、既定 main_loop.DEFAULT_TURN_TIMEOUT_S の
                                120。0 より大きい有限の値)
    max_decision_errors         主ループの JSON の誤りが続けてこの回数になったら全体を止める (--max-decision-errors、
                                既定 main_loop.DEFAULT_MAX_DECISION_ERRORS の 3。1 以上)
    monitor_threshold           監視の当たりのしきい値 (--monitor-threshold、既定
                                orchestrator_controller.DEFAULT_MONITOR_THRESHOLD の 0.5。0 より大きく 1 未満)
    max_monitor_errors          監視の Jev の失敗がこの回数続いたら監視を止める (--max-monitor-errors、既定
                                orchestrator_controller.DEFAULT_MAX_MONITOR_ERRORS の 5。1 以上)
    delegate_image_width        委任のループが Jev に送る画像の幅 (--delegate-image-width、既定
                                delegate_loop.DEFAULT_DELEGATE_IMAGE_WIDTH の 1280。
                                delegate_loop.DELEGATE_IMAGE_WIDTH_MIN (498)〜DELEGATE_IMAGE_WIDTH_MAX (1280) の整数)
    max_delegate_errors         委任のループの Jev の失敗がこの回数続いたら、操作権を主ループに返す
                                (--max-delegate-errors、既定 delegate_loop.DEFAULT_MAX_DELEGATE_ERRORS の 5。1 以上)
    agent                       主ループのエージェント (--agent、既定 claude。AgentKind の claude か codex)
    codex_model                 主ループの Codex のモデル (--codex-model、既定
                                codex_session_command.DEFAULT_CODEX_MODEL の gpt-6.2-sol。空か空白だけでない文字列)。
                                agent が codex のときだけ使う
    orchestrator_effort から max_decision_errors までの 4 つと agent・codex_model は orchestrator と delegate で使い、
    monitor_threshold と max_monitor_errors は orchestrator だけ、delegate_image_width と max_delegate_errors は
    delegate だけで使う。使わない型でも、範囲は mode に依らず確かめる。
    """

    software_name: str = ""
    user_name: str = ""
    runs_dir: Path = DEFAULT_RUNS_DIR
    device_fps: int = DEFAULT_DEVICE_FPS
    com_port: str = DEFAULT_COM_PORT
    interval_fps: float = DEFAULT_INTERVAL_FPS
    interval_width: int = DEFAULT_INTERVAL_WIDTH
    max_surface_errors: int = DEFAULT_MAX_SURFACE_ERRORS
    middle_interval_s: float = DEFAULT_MIDDLE_INTERVAL_S
    middle_window_s: float = DEFAULT_MIDDLE_WINDOW_S
    max_middle_errors: int = DEFAULT_MAX_MIDDLE_ERRORS
    deep_min_start_interval_s: float = DEFAULT_DEEP_MIN_START_INTERVAL_S
    deep_auto_seconds: float = DEFAULT_DEEP_AUTO_SECONDS
    deep_timeout_s: float = DEFAULT_DEEP_TIMEOUT_S
    middle: MiddleKind = DEFAULT_MIDDLE_KIND
    middle_effort: MiddleEffort = DEFAULT_MIDDLE_EFFORT
    surface: SurfaceKind = DEFAULT_SURFACE
    threshold_advisor: float = DEFAULT_THRESHOLD_ADVISOR
    box_requery_every: int = DEFAULT_BOX_REQUERY_EVERY
    hold_margin: float = DEFAULT_HOLD_MARGIN
    surface_map: SurfaceMapMode = DEFAULT_SURFACE_MAP
    deep: DeepRunnerChoice = DEFAULT_DEEP_RUNNER
    safety: SafetyKind = DEFAULT_SAFETY
    safety_interval_s: float = DEFAULT_SAFETY_INTERVAL_S
    threshold_safety: float = DEFAULT_THRESHOLD_SAFETY
    threshold_task: float = DEFAULT_THRESHOLD_TASK
    max_safety_errors: int = DEFAULT_MAX_SAFETY_ERRORS
    progress_window_s: float = DEFAULT_PROGRESS_WINDOW_S
    stuck_advice_seconds: float = DEFAULT_STUCK_ADVICE_SECONDS
    task_stuck_seconds: float = DEFAULT_TASK_STUCK_SECONDS
    system_advice_seconds: float = DEFAULT_SYSTEM_ADVICE_SECONDS
    mode: RunMode = DEFAULT_RUN_MODE
    orchestrator_effort: str = ORCHESTRATOR_EFFORT
    rotate_tokens: int = DEFAULT_ROTATE_TOKENS
    turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S
    max_decision_errors: int = DEFAULT_MAX_DECISION_ERRORS
    monitor_threshold: float = DEFAULT_MONITOR_THRESHOLD
    max_monitor_errors: int = DEFAULT_MAX_MONITOR_ERRORS
    delegate_image_width: int = DEFAULT_DELEGATE_IMAGE_WIDTH
    max_delegate_errors: int = DEFAULT_MAX_DELEGATE_ERRORS
    agent: AgentKind = DEFAULT_AGENT
    codex_model: str = DEFAULT_CODEX_MODEL


@dataclass(frozen=True)
class MiddleOnceSettings:
    """middle-once の起動引数。

    run_dir           保存済みの実行ディレクトリー (--run-dir)
    middle_window_s   middle に渡す一定間隔のフレームの範囲 (--middle-window、既定 10。0 より大きい有限の値)
    middle            middle の分析の実装 (--middle、既定 luna。fake か luna)。luna では環境変数
                      OPENAI_API_KEY が要る (openai_api_key_errors)
    """

    run_dir: Path
    middle_window_s: float = DEFAULT_MIDDLE_WINDOW_S
    middle: MiddleKind = DEFAULT_MIDDLE_KIND


@dataclass(frozen=True)
class DeepOnceSettings:
    """deep-once の起動引数。

    run_dir    保存済みの実行ディレクトリー (--run-dir)
    kind       文書の種類 (--doc)
    scene_id   場面 ID (--scene)。controls と tips だけに付けられる。
               付けなければ、controls と tips は middle/state.json の現在の場面 (無ければ共通の文書) を対象にする
    """

    run_dir: Path
    kind: DocumentKind
    scene_id: str | None = None


def run_settings_errors(settings: RunSettings) -> list[str]:
    """RunSettings の範囲の誤りを、起動引数の名前を含むメッセージのリストにする。誤りが無ければ空のリスト。

    範囲は RunSettings の docstring のとおり。nan はどの範囲にも入らないものとして扱い、
    「有限の値」とした引数では inf も誤りにする。1 つの引数につき最大 1 つのメッセージを返す。
    deep_auto_seconds < deep_min_start_interval_s は --deep-auto-seconds の誤りにする (2 つとも単独では範囲内のときだけ確かめる)。
    メッセージは引数の定義の順 (RunSettings の属性の順) に並べる。--device-fps は DEVICE_FPS_CHOICES のどれか
    (30 か 60)。--com-port は switch2_remote.controller_bridge.is_valid_com_port の形 (cli の parse_com_port と同じ判定)。
    --threshold-advisor は 0 以上 1 以下 (nan は誤り)、
    --box-requery-every は 1 以上、--hold-margin は 0 以上 1 以下 (nan と無限大は誤り)。
    middle_effort は値の型 (MiddleEffort) で決まるため確かめない。
    surface は値の型 (SurfaceKind) で、surface_map は値の型 (SurfaceMapMode) で決まるため確かめない。
    安全の監視の引数: --safety-interval は SAFETY_INTERVAL_MIN_S 以上 SAFETY_INTERVAL_MAX_S 以下 (nan は誤り)、
    --threshold-safety は 0 より大きく 1 より小さい値 (0・1・nan は誤り)、--max-safety-errors は 1 以上。
    safety は値の型 (SafetyKind) で決まるため確かめない。
    --threshold-task は 0 以上 1 以下 (nan は誤り)。
    --progress-window・--stuck-advice-seconds・--task-stuck-seconds・--system-advice-seconds は 0 より大きい有限の値。
    Orchestrator 型の 6 つの値 (_orchestrator_errors) は、mode に依らず確かめる: --orchestrator-effort は
    ORCHESTRATOR_EFFORTS のどれか、--rotate-tokens は 1 以上で AUTOCOMPACT_MAX_TOKENS 未満、--turn-timeout は 0 より
    大きい有限の値、--max-decision-errors と --max-monitor-errors は 1 以上、--monitor-threshold は 0 より大きく 1 より
    小さい値 (0・1・nan は誤り)。mode は値の型 (RunMode) で決まるため確かめない。
    委任型の 2 つの値も、mode に依らず確かめる: --delegate-image-width は delegate_loop.DELEGATE_IMAGE_WIDTH_MIN 以上
    delegate_loop.DELEGATE_IMAGE_WIDTH_MAX 以下、--max-delegate-errors は 1 以上。メッセージは Orchestrator 型の
    6 つの値の後に、この順に並べる。
    主ループのエージェントの値も、mode と agent に依らず確かめる: --codex-model は空か空白だけでない文字列。agent は
    値の型 (AgentKind) で決まるため確かめない。メッセージは委任型の 2 つの値の後に並べる。
    """
    errors: list[str] = []
    if settings.device_fps not in DEVICE_FPS_CHOICES:
        errors.append(
            "--device-fps must be one of "
            f"{', '.join(str(fps) for fps in DEVICE_FPS_CHOICES)}: {settings.device_fps}"
        )
    if not is_valid_com_port(settings.com_port):
        errors.append(f"--com-port must be COM1, COM2, ...: {settings.com_port!r}")
    if not 0 < settings.interval_fps <= INTERVAL_FPS_MAX:
        errors.append(
            f"--interval-fps must be more than 0 and at most {INTERVAL_FPS_MAX:g}: "
            f"{settings.interval_fps}"
        )
    if not INTERVAL_WIDTH_MIN <= settings.interval_width <= INTERVAL_WIDTH_MAX:
        errors.append(
            f"--interval-width must be {INTERVAL_WIDTH_MIN}..{INTERVAL_WIDTH_MAX}: "
            f"{settings.interval_width}"
        )
    if settings.max_surface_errors < 1:
        errors.append(
            f"--max-surface-errors must be 1 or more: {settings.max_surface_errors}"
        )
    if not MIDDLE_INTERVAL_MIN_S <= settings.middle_interval_s <= MIDDLE_INTERVAL_MAX_S:
        errors.append(
            f"--middle-interval must be {MIDDLE_INTERVAL_MIN_S:g}..{MIDDLE_INTERVAL_MAX_S:g}: "
            f"{settings.middle_interval_s}"
        )
    errors += _middle_window_errors(settings.middle_window_s)
    if settings.max_middle_errors < 1:
        errors.append(
            f"--max-middle-errors must be 1 or more: {settings.max_middle_errors}"
        )
    min_interval_ok = _is_finite_at_least_zero(settings.deep_min_start_interval_s)
    if not min_interval_ok:
        errors.append(
            "--deep-min-start-interval must be a finite number of 0 or more: "
            f"{settings.deep_min_start_interval_s}"
        )
    auto_ok = _is_finite_positive(settings.deep_auto_seconds)
    if not auto_ok:
        errors.append(
            "--deep-auto-seconds must be a finite number more than 0: "
            f"{settings.deep_auto_seconds}"
        )
    elif min_interval_ok and (
        settings.deep_auto_seconds < settings.deep_min_start_interval_s
    ):
        errors.append(
            f"--deep-auto-seconds ({settings.deep_auto_seconds:g}) must not be less than "
            f"--deep-min-start-interval ({settings.deep_min_start_interval_s:g})"
        )
    if not _is_finite_positive(settings.deep_timeout_s):
        errors.append(
            "--deep-timeout must be a finite number more than 0: "
            f"{settings.deep_timeout_s}"
        )
    if not 0.0 <= settings.threshold_advisor <= 1.0:
        errors.append(f"--threshold-advisor must be 0..1: {settings.threshold_advisor}")
    if settings.box_requery_every < 1:
        errors.append(
            f"--box-requery-every must be 1 or more: {settings.box_requery_every}"
        )
    if not 0.0 <= settings.hold_margin <= 1.0:
        errors.append(f"--hold-margin must be 0..1: {settings.hold_margin}")
    if not SAFETY_INTERVAL_MIN_S <= settings.safety_interval_s <= SAFETY_INTERVAL_MAX_S:
        errors.append(
            f"--safety-interval must be {SAFETY_INTERVAL_MIN_S:g}..{SAFETY_INTERVAL_MAX_S:g}: "
            f"{settings.safety_interval_s}"
        )
    if not 0.0 < settings.threshold_safety < 1.0:
        errors.append(
            "--threshold-safety must be more than 0 and less than 1: "
            f"{settings.threshold_safety}"
        )
    if not 0.0 <= settings.threshold_task <= 1.0:
        errors.append(f"--threshold-task must be 0..1: {settings.threshold_task}")
    if settings.max_safety_errors < 1:
        errors.append(
            f"--max-safety-errors must be 1 or more: {settings.max_safety_errors}"
        )
    if not _is_finite_positive(settings.progress_window_s):
        errors.append(
            "--progress-window must be a finite number more than 0: "
            f"{settings.progress_window_s}"
        )
    if not _is_finite_positive(settings.stuck_advice_seconds):
        errors.append(
            "--stuck-advice-seconds must be a finite number more than 0: "
            f"{settings.stuck_advice_seconds}"
        )
    if not _is_finite_positive(settings.task_stuck_seconds):
        errors.append(
            "--task-stuck-seconds must be a finite number more than 0: "
            f"{settings.task_stuck_seconds}"
        )
    if not _is_finite_positive(settings.system_advice_seconds):
        errors.append(
            "--system-advice-seconds must be a finite number more than 0: "
            f"{settings.system_advice_seconds}"
        )
    errors += _orchestrator_errors(settings)
    errors += _delegate_errors(settings)
    if not settings.codex_model.strip():
        errors.append(
            f"--codex-model must not be empty or blank: {settings.codex_model!r}"
        )
    return errors


def _delegate_errors(settings: RunSettings) -> list[str]:
    """委任型の 2 つの値の範囲の誤りを、RunSettings の属性の順のメッセージのリストにする。"""
    errors: list[str] = []
    if (
        not DELEGATE_IMAGE_WIDTH_MIN
        <= settings.delegate_image_width
        <= DELEGATE_IMAGE_WIDTH_MAX
    ):
        errors.append(
            f"--delegate-image-width must be {DELEGATE_IMAGE_WIDTH_MIN}.."
            f"{DELEGATE_IMAGE_WIDTH_MAX}: {settings.delegate_image_width}"
        )
    if settings.max_delegate_errors < 1:
        errors.append(
            f"--max-delegate-errors must be 1 or more: {settings.max_delegate_errors}"
        )
    return errors


def _orchestrator_errors(settings: RunSettings) -> list[str]:
    """Orchestrator 型の 6 つの値の範囲の誤りを、RunSettings の属性の順のメッセージのリストにする。"""
    errors: list[str] = []
    if settings.orchestrator_effort not in ORCHESTRATOR_EFFORTS:
        errors.append(
            f"--orchestrator-effort must be one of {', '.join(ORCHESTRATOR_EFFORTS)}: "
            f"{settings.orchestrator_effort!r}"
        )
    if not 1 <= settings.rotate_tokens < AUTOCOMPACT_MAX_TOKENS:
        errors.append(
            f"--rotate-tokens must be 1 or more and less than {AUTOCOMPACT_MAX_TOKENS}: "
            f"{settings.rotate_tokens}"
        )
    if not _is_finite_positive(settings.turn_timeout_s):
        errors.append(
            "--turn-timeout must be a finite number more than 0: "
            f"{settings.turn_timeout_s}"
        )
    if settings.max_decision_errors < 1:
        errors.append(
            f"--max-decision-errors must be 1 or more: {settings.max_decision_errors}"
        )
    if not 0.0 < settings.monitor_threshold < 1.0:
        errors.append(
            "--monitor-threshold must be more than 0 and less than 1: "
            f"{settings.monitor_threshold}"
        )
    if settings.max_monitor_errors < 1:
        errors.append(
            f"--max-monitor-errors must be 1 or more: {settings.max_monitor_errors}"
        )
    return errors


def _is_finite_positive(value: float) -> bool:
    """value が 0 より大きい有限の値なら True (nan と inf は False)。"""
    return math.isfinite(value) and value > 0


def _is_finite_at_least_zero(value: float) -> bool:
    """value が 0 以上の有限の値なら True (nan と inf は False)。"""
    return math.isfinite(value) and value >= 0


def _middle_window_errors(middle_window_s: float) -> list[str]:
    """--middle-window の誤り (0 より大きい有限の値でない) のメッセージのリストを返す。"""
    if _is_finite_positive(middle_window_s):
        return []
    return [f"--middle-window must be a finite number more than 0: {middle_window_s}"]


def middle_once_settings_errors(settings: MiddleOnceSettings) -> list[str]:
    """MiddleOnceSettings の範囲の誤り (--middle-window) を、run_settings_errors と同じ形のメッセージのリストにする。"""
    return _middle_window_errors(settings.middle_window_s)


def deep_once_settings_errors(settings: DeepOnceSettings) -> list[str]:
    """DeepOnceSettings の誤りのメッセージのリストを返す。誤りが無ければ空のリスト。

    - scene_id が notes.note_paths.is_scene_id に合わない → --scene の誤り
    - kind が controls・tips 以外なのに scene_id がある → --scene は controls と tips だけに付けられるという誤り
    """
    if settings.scene_id is None:
        return []
    if not is_scene_id(settings.scene_id):
        return [f"--scene must be a scene id like scene-001: {settings.scene_id!r}"]
    if settings.kind not in SCENE_DOCUMENT_KINDS:
        return [
            f"--scene can be used only with --doc controls or tips: --doc {settings.kind}"
        ]
    return []


def openai_api_key_errors(middle: MiddleKind, environ: Mapping[str, str]) -> list[str]:
    """--middle の実装に要る API キーが環境変数 environ にあるかを確かめ、無ければメッセージのリストを返す。

    middle が LUNA で、environ の OPENAI_API_KEY_ENV が無いか、空か空白だけなら、OPENAI_API_KEY と
    --middle fake (API キーなしで動かす方法) を含むメッセージを 1 つ返す。それ以外 (キーがある、FAKE) は空のリスト。
    メッセージに API キーの値は含めない。cli は実機と実行ディレクトリーに触れる前にこれを確かめ、誤りがあれば
    終了コード 2 で終わる。
    """
    if middle is not MiddleKind.LUNA:
        return []
    if environ.get(OPENAI_API_KEY_ENV, "").strip():
        return []
    return [
        (
            f"--middle {MiddleKind.LUNA.value} needs the environment variable "
            f"{OPENAI_API_KEY_ENV}. Set it, or use --middle {MiddleKind.FAKE.value} "
            "to run without an API key"
        )
    ]


def typesafe_api_key_errors(
    surface: SurfaceKind, environ: Mapping[str, str]
) -> list[str]:
    """--surface の実装に要る API キーが環境変数 environ にあるかを確かめ、無ければメッセージのリストを返す。

    surface が JEV で、environ の TYPESAFE_API_KEY_ENV が無いか、空か空白だけなら、TYPESAFE_API_KEY と
    --surface fake (API キーなしで動かす方法) を含むメッセージを 1 つ返す。それ以外 (キーがある、FAKE) は空のリスト。
    メッセージに API キーの値は含めない。cli は実機に触れる前にこれを確かめ、誤りがあれば終了コード 2 で終わる。
    """
    if surface is not SurfaceKind.JEV:
        return []
    if environ.get(TYPESAFE_API_KEY_ENV, "").strip():
        return []
    return [
        (
            f"--surface {SurfaceKind.JEV.value} needs the environment variable "
            f"{TYPESAFE_API_KEY_ENV}. Set it, or use --surface {SurfaceKind.FAKE.value} "
            "to run without an API key"
        )
    ]


def safety_api_key_errors(safety: SafetyKind, environ: Mapping[str, str]) -> list[str]:
    """--safety の実装に要る API キーが環境変数 environ にあるかを確かめ、無ければメッセージのリストを返す。

    safety が JEV で、environ の TYPESAFE_API_KEY_ENV が無いか、空か空白だけなら、TYPESAFE_API_KEY と
    --safety fake (API キーなしで動かす方法) を含むメッセージを 1 つ返す。それ以外 (キーがある、FAKE) は空のリスト。
    メッセージに API キーの値は含めない。cli は実機と実行ディレクトリーに触れる前にこれを確かめ、誤りがあれば
    終了コード 2 で終わる。
    """
    if safety is not SafetyKind.JEV:
        return []
    if environ.get(TYPESAFE_API_KEY_ENV, "").strip():
        return []
    return [
        (
            f"--safety {SafetyKind.JEV.value} needs the environment variable "
            f"{TYPESAFE_API_KEY_ENV}. Set it, or use --safety {SafetyKind.FAKE.value} "
            "to run without an API key"
        )
    ]


def orchestrator_api_key_errors(environ: Mapping[str, str]) -> list[str]:
    """--mode orchestrator に要る API キーが環境変数 environ にあるかを確かめ、無ければメッセージのリストを返す。

    environ の TYPESAFE_API_KEY_ENV が無いか、空か空白だけなら、--mode orchestrator と TYPESAFE_API_KEY を含む
    メッセージを 1 つ返す (監視は常に Jev を使うため、--safety fake でも要る。キーなしで動かす方法は示さない)。
    キーがあれば空のリスト。メッセージに API キーの値は含めない。cli は --mode orchestrator のとき、実機と実行
    ディレクトリーに触れる前にこれを確かめ、誤りがあれば終了コード 2 で終わる。
    """
    if environ.get(TYPESAFE_API_KEY_ENV, "").strip():
        return []
    return [
        (
            f"--mode {RunMode.ORCHESTRATOR.value} needs the environment variable "
            f"{TYPESAFE_API_KEY_ENV} for the monitor, which always uses Jev "
            "(even with --safety fake)"
        )
    ]


def delegate_api_key_errors(environ: Mapping[str, str]) -> list[str]:
    """--mode delegate に要る API キーが環境変数 environ にあるかを確かめ、無ければメッセージのリストを返す。

    environ の TYPESAFE_API_KEY_ENV が無いか、空か空白だけなら、--mode delegate と TYPESAFE_API_KEY を含む
    メッセージを 1 つ返す (委任は常に Jev を使うため、--safety fake でも要る。キーなしで動かす方法は示さない)。
    キーがあれば空のリスト。メッセージに API キーの値は含めない。OPENAI_API_KEY は確かめない。cli は --mode delegate
    のとき、実機と実行ディレクトリーに触れる前にこれを確かめ、誤りがあれば終了コード 2 で終わる。
    """
    if environ.get(TYPESAFE_API_KEY_ENV, "").strip():
        return []
    return [
        (
            f"--mode {RunMode.DELEGATE.value} needs the environment variable "
            f"{TYPESAFE_API_KEY_ENV} for the delegation, which always uses Jev "
            "(even with --safety fake)"
        )
    ]


def run_settings_to_json(settings: RunSettings) -> dict[str, object]:
    """run.jsonl の start の行に書く起動引数の辞書を返す。

    キーは RunSettings の属性名、値は JSON にできる値 (runs_dir は文字列、middle・middle_effort・surface・surface_map・
    deep・safety・mode・agent は値の文字列 (型も str)。middle は "fake" / "luna"、middle_effort は "none" / "low"、
    surface は "jev" / "fake"、surface_map は "text" / "none"、deep は "claude" / "fake"、safety は "jev" / "fake"、
    mode は "advisor" / "orchestrator" / "delegate"、agent は "claude" / "codex") にする。middle_effort は
    --middle fake でも入れる。software_name と user_name も、ほかの起動引数と同じく書かれたままの文字列で入れる。
    Orchestrator 型の 6 つの値と委任型の 2 つの値 (delegate_image_width・max_delegate_errors) と、主ループの
    エージェントの 2 つの値 (agent・codex_model) は、mode に依らず入れる (codex_model は agent が claude でも入れる)。
    """
    value = dataclasses.asdict(settings)
    value["runs_dir"] = str(settings.runs_dir)
    value["middle"] = settings.middle.value
    value["middle_effort"] = settings.middle_effort.value
    value["surface"] = settings.surface.value
    value["surface_map"] = settings.surface_map.value
    value["deep"] = settings.deep.value
    value["safety"] = settings.safety.value
    value["mode"] = settings.mode.value
    value["agent"] = settings.agent.value
    return value
