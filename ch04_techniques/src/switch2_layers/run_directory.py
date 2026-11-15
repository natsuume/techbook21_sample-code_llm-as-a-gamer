"""実行ディレクトリー runs/<YYYYmmdd-HHMMSS>/ のパスの集約、作成、起動時の notes/ の初期ファイル。

配置 (実行ディレクトリーからの相対パス):
    run.jsonl                               実行の記録 (records.run_log)
    frames.jsonl                            保存した画像の一覧 (frames.frame_store)
    frames/interval/<tミリ秒9桁>.jpg          一定間隔のフレーム
    frames/surface/<tミリ秒9桁>.jpg           surface に送った画像
    surface.jsonl                           surface の 1 回ごとの記録
    operations.jsonl                        コントローラーに送った操作 (records.operation_log)
    notes/                                  文書 (notes.note_paths)
    middle/state.json                       middle の最新の出力
    middle/calls.jsonl                      middle の 1 回ごとの記録
    deep/<種類>-<連番3桁>/job.json・work/      deep のジョブ (deep.deep_job_directory)
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

RUN_DIRECTORY_NAME_FORMAT = "%Y%m%d-%H%M%S"
RUN_LOG_FILE = "run.jsonl"
FRAMES_LOG_FILE = "frames.jsonl"
FRAMES_DIR = "frames"
INTERVAL_FRAMES_DIR = "interval"
SURFACE_FRAMES_DIR = "surface"
SURFACE_LOG_FILE = "surface.jsonl"
OPERATIONS_LOG_FILE = "operations.jsonl"
NOTES_DIR = "notes"
MIDDLE_DIR = "middle"
MIDDLE_STATE_FILE = "state.json"
MIDDLE_CALLS_FILE = "calls.jsonl"
DEEP_DIR = "deep"
FRAME_FILE_DIGITS = 9  # 画像のファイル名の t のミリ秒の桁数
FRAME_FILE_SUFFIX = ".jpg"

# 起動時の notes/ の初期ファイルの中身。common/controls.md は initial_common_controls で作る
COMMON_CONTROLS_HEADING = "# 操作説明 (共通)"
UNKNOWN_BEHAVIOR = "unknown"  # 挙動をまだ確かめていない単ボタンの印
INITIAL_COMMON_TIPS = "# 攻略情報 (共通)\n"
INITIAL_STRATEGY = "# 大方針\n"
INITIAL_ADVICE = "# 助言\n"
INITIAL_SCENE_LABELS: dict[str, list[object]] = {"scenes": []}


@dataclass(frozen=True)
class RunPaths:
    """1 つの実行ディレクトリー root の中のパスをまとめる。パスを返すだけで、ファイルを作らない。"""

    root: Path

    @property
    def run_log(self) -> Path:
        """root / RUN_LOG_FILE"""
        raise NotImplementedError

    @property
    def frames_log(self) -> Path:
        """root / FRAMES_LOG_FILE"""
        raise NotImplementedError

    @property
    def interval_frames_dir(self) -> Path:
        """root / FRAMES_DIR / INTERVAL_FRAMES_DIR"""
        raise NotImplementedError

    @property
    def surface_frames_dir(self) -> Path:
        """root / FRAMES_DIR / SURFACE_FRAMES_DIR"""
        raise NotImplementedError

    @property
    def surface_log(self) -> Path:
        """root / SURFACE_LOG_FILE"""
        raise NotImplementedError

    @property
    def operations_log(self) -> Path:
        """root / OPERATIONS_LOG_FILE"""
        raise NotImplementedError

    @property
    def notes_dir(self) -> Path:
        """root / NOTES_DIR"""
        raise NotImplementedError

    @property
    def middle_dir(self) -> Path:
        """root / MIDDLE_DIR"""
        raise NotImplementedError

    @property
    def middle_state(self) -> Path:
        """root / MIDDLE_DIR / MIDDLE_STATE_FILE"""
        raise NotImplementedError

    @property
    def middle_calls(self) -> Path:
        """root / MIDDLE_DIR / MIDDLE_CALLS_FILE"""
        raise NotImplementedError

    @property
    def deep_dir(self) -> Path:
        """root / DEEP_DIR"""
        raise NotImplementedError

    def interval_frame(self, t: float) -> Path:
        """時刻 t の一定間隔のフレームの画像のパス interval_frames_dir / frame_file_name(t)。"""
        raise NotImplementedError

    def surface_frame(self, t: float) -> Path:
        """時刻 t の surface に送った画像のパス surface_frames_dir / frame_file_name(t)。"""
        raise NotImplementedError

    def relative(self, path: Path) -> str:
        """path の root からの相対パスを / 区切りの文字列で返す (記録の file に書く形)。root の外なら ValueError。"""
        raise NotImplementedError


def frame_file_name(t: float) -> str:
    """時刻 t の画像のファイル名 `<tミリ秒9桁>.jpg` を返す (例: t=12.345 → 000012345.jpg)。

    ミリ秒は run_clock.t_to_milliseconds(t)。9 桁に満たなければ先頭を 0 で埋める。t が負なら ValueError。
    """
    raise NotImplementedError


def run_directory_name(started_at: datetime) -> str:
    """実行を始めた時刻 started_at (ローカルの時刻) を RUN_DIRECTORY_NAME_FORMAT の名前にする (例: 20261115-142233)。"""
    raise NotImplementedError


def create_run_directory(runs_dir: Path, started_at: datetime) -> RunPaths:
    """runs_dir / run_directory_name(started_at) を作り、その RunPaths を返す。

    - runs_dir は親を含めて作る
    - 同じ名前のファイルかディレクトリーが既にあれば、末尾に -2、-3 … を付けた最初の無い名前で作る
      (例: 20261115-142233 → 20261115-142233-2 → 20261115-142233-3)
    - 作るのは mkdir(exist_ok=False) で行い、同時に同じ名前で作られても 2 つの実行で同じディレクトリーを使わない
    - 中に frames/interval/、frames/surface/、middle/、deep/ も作る。notes/ は write_initial_notes で作る
    """
    raise NotImplementedError


def open_run_directory(run_dir: Path) -> RunPaths:
    """保存済みの実行ディレクトリー run_dir の RunPaths を返す (middle-once・deep-once・publish-note で使う)。

    run_dir がディレクトリーでなければ FileNotFoundError にする。中のファイルは確かめない。
    """
    raise NotImplementedError


def initial_common_controls(allowed_buttons: Collection[str]) -> str:
    """起動時の notes/common/controls.md の中身を返す。

    1 行目が COMMON_CONTROLS_HEADING、空行の後に、押せるボタンを 1 行ずつ `- <ボタン名>: unknown` で並べる。
    並びは switch2_remote.controller_state.BUTTON_ORDER の順で、allowed_buttons に無いボタンは書かない。末尾は改行。
    例 (A と B だけのとき): "# 操作説明 (共通)\\n\\n- A: unknown\\n- B: unknown\\n"
    """
    raise NotImplementedError


def write_initial_notes(paths: RunPaths, allowed_buttons: Collection[str]) -> None:
    """起動時の notes/ の初期ファイルを書く (どれも置き換えの書き込み)。

        notes/common/controls.md   initial_common_controls(allowed_buttons)
        notes/common/tips.md       INITIAL_COMMON_TIPS (見出しだけ)
        notes/strategy.md          INITIAL_STRATEGY (見出しだけ)
        notes/advice.md            INITIAL_ADVICE (見出しだけ)
        notes/scene_labels.json    INITIAL_SCENE_LABELS ({"scenes": []})
        notes/map/                 空のディレクトリー
        notes/scenes/              空のディレクトリー
    notes/ と notes/common/ は無ければ作る。既にあるファイルは置き換える。
    """
    raise NotImplementedError
