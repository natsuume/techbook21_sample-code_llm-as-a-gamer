"""起動引数の値をまとめた dataclass と、値の範囲の検証。

サブコマンドごとに 1 つの dataclass を持つ。cli がコマンドラインの引数をこれに直し、
*_settings_errors で範囲を確かめてから使う。範囲の誤りのメッセージには、起動引数の名前 (--middle-interval 等) を含める。
キャプチャーのフレームレートは起動引数にせず、定数 CAPTURE_FFMPEG_FPS と CAPTURE_PROCESS_FPS に固定する。
middle の実装は --middle (MiddleKind) で選ぶ。luna に要る API キーは起動引数にせず、環境変数 OPENAI_API_KEY_ENV から
読む (openai_api_key_errors で有無を確かめる)。API キーは dataclass に持たせない (run.jsonl の start に起動引数を書くため)。
"""

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from switch2_layers.deep.deep_types import SCENE_DOCUMENT_KINDS, DocumentKind
from switch2_layers.notes.note_paths import is_scene_id


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

# switch2_remote.capture.Capture に渡すフレームレート。process_fps は一定間隔の保存のフレームレートの上限でもある
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
DEFAULT_DEEP_MIN_START_INTERVAL_S = 15.0
DEFAULT_DEEP_AUTO_SECONDS = 60.0
DEFAULT_DEEP_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class RunSettings:
    """run の起動引数。

    runs_dir                    実行ディレクトリーを作る親 (--runs-dir、既定 runs)
    interval_fps                一定間隔のフレームの保存のフレームレート (--interval-fps、既定 5。0 より大きく INTERVAL_FPS_MAX 以下)
    interval_width              一定間隔のフレームを縮小する幅 (--interval-width、既定 640。INTERVAL_WIDTH_MIN〜INTERVAL_WIDTH_MAX)
    max_surface_errors          surface の decide の失敗がこの回数続いたら全体を止める (--max-surface-errors、既定 5。1 以上)
    middle_interval_s           middle の前回の応答から次を始めるまでの秒数 (--middle-interval、既定 5.0。1〜10)
    middle_window_s             middle に渡す一定間隔のフレームの範囲 (直近の秒数) (--middle-window、既定 10。0 より大きい有限の値)
    max_middle_errors           middle の analyze の失敗がこの回数続いたら全体を止める (--max-middle-errors、既定 5。1 以上)
    deep_min_start_interval_s   同じ文書の deep のジョブを、前回の起動からこの秒数たつまで起動しない
                                (--deep-min-start-interval、既定 15。0 以上の有限の値)
    deep_auto_seconds           同じ文書の前回のジョブの終了からこの秒数たったら、自動で deep を呼ぶ
                                (--deep-auto-seconds、既定 60。0 より大きい有限の値で、deep_min_start_interval_s 以上。
                                最小間隔より短いと、自動の呼び出しが判定の周期ごとに最小間隔で捨てられ続けるため)
    deep_timeout_s              deep のジョブの実行時間の上限 (--deep-timeout、既定 300。0 より大きい有限の値)
    allow_home                  押せるボタンに HOME を足す (--allow-home、既定 False)
    middle                      middle の分析の実装 (--middle、既定 luna。fake か luna)。luna では環境変数
                                OPENAI_API_KEY が要る (openai_api_key_errors)
    """

    runs_dir: Path = DEFAULT_RUNS_DIR
    interval_fps: float = DEFAULT_INTERVAL_FPS
    interval_width: int = DEFAULT_INTERVAL_WIDTH
    max_surface_errors: int = DEFAULT_MAX_SURFACE_ERRORS
    middle_interval_s: float = DEFAULT_MIDDLE_INTERVAL_S
    middle_window_s: float = DEFAULT_MIDDLE_WINDOW_S
    max_middle_errors: int = DEFAULT_MAX_MIDDLE_ERRORS
    deep_min_start_interval_s: float = DEFAULT_DEEP_MIN_START_INTERVAL_S
    deep_auto_seconds: float = DEFAULT_DEEP_AUTO_SECONDS
    deep_timeout_s: float = DEFAULT_DEEP_TIMEOUT_S
    allow_home: bool = False
    middle: MiddleKind = DEFAULT_MIDDLE_KIND


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


@dataclass(frozen=True)
class PublishNoteSettings:
    """publish-note の起動引数。

    run_dir       実行ディレクトリー (--run-dir)
    note          置き換える文書の notes/ からの相対パス (--note)
    base_sha256   deep が編集を始めたときの文書の SHA-256。文書が無かったなら "none" (--base-sha256)
    source        編集したファイル (--from)
    """

    run_dir: Path
    note: str
    base_sha256: str
    source: Path


def run_settings_errors(settings: RunSettings) -> list[str]:
    """RunSettings の範囲の誤りを、起動引数の名前を含むメッセージのリストにする。誤りが無ければ空のリスト。

    範囲は RunSettings の docstring のとおり。nan はどの範囲にも入らないものとして扱い、
    「有限の値」とした引数では inf も誤りにする。1 つの引数につき最大 1 つのメッセージを返す。
    deep_auto_seconds < deep_min_start_interval_s は --deep-auto-seconds の誤りにする (2 つとも単独では範囲内のときだけ確かめる)。
    """
    errors: list[str] = []
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
    raise NotImplementedError


def run_settings_to_json(settings: RunSettings) -> dict[str, object]:
    """run.jsonl の start の行に書く起動引数の辞書を返す。

    キーは RunSettings の属性名、値は JSON にできる値 (runs_dir は文字列、middle は値の文字列 "fake" / "luna") にする。
    """
    value = dataclasses.asdict(settings)
    value["runs_dir"] = str(settings.runs_dir)
    return value
