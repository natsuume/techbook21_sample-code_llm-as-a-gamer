"""作業ディレクトリーの設定ファイル harness.json の型と読み書き。

harness.json の形:
    {"game": str, "goal": str, "features": [str, ...], "created_at": str}
created_at は ISO 8601 の秒までの時刻で、タイムゾーンを含む (例: "2026-11-16T21:00:05+09:00")。
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from switch2_harness.features import FEATURES, Feature

# init で --goal を省略したときの目標
DEFAULT_GOAL = "できるだけ先へ進める"


@dataclass(frozen=True)
class HarnessConfig:
    """harness.json の内容。

    game: ゲームの名前
    goal: プレイの目標
    features: 有効な要素の名前 (FEATURES の順)
    created_at: 作業ディレクトリーを作った時刻 (format_created_at の形式)
    """

    game: str
    goal: str
    features: tuple[str, ...]
    created_at: str


def format_created_at(moment: datetime) -> str:
    """moment を ISO 8601 の秒までの文字列にする (datetime.isoformat(timespec="seconds"))。

    moment にはタイムゾーン付きの datetime を渡す。
    例: 2026-11-16 21:00:05.123456 (UTC+9) は "2026-11-16T21:00:05+09:00"。
    """
    raise NotImplementedError


def config_to_json_text(config: HarnessConfig) -> str:
    """config を harness.json の本文にする。

    キーは game / goal / features / created_at で、features は JSON の配列にする。
    json.dumps(..., ensure_ascii=False, indent=2) の結果に改行 1 つを足したものを返す。
    """
    raise NotImplementedError


def config_from_json_text(
    text: str, available: tuple[Feature, ...] = FEATURES
) -> HarnessConfig:
    """harness.json の本文を HarnessConfig にする。

    features は features.validate_feature_names(features, available) を通す。
    available に無い名前があれば、その ValueError をそのまま送出する。
    """
    raise NotImplementedError


def write_config(path: Path, config: HarnessConfig) -> None:
    """config を path に UTF-8 で書く (config_to_json_text の内容。既存のファイルは上書きする)。"""
    raise NotImplementedError


def read_config(path: Path, available: tuple[Feature, ...] = FEATURES) -> HarnessConfig:
    """path の harness.json を UTF-8 で読み、config_from_json_text で HarnessConfig にする。"""
    raise NotImplementedError
