"""作業ディレクトリーの設定ファイル harness.json の型と読み書き。

harness.json の形:
    {"game": str, "goal": str, "features": [str, ...], "created_at": str[, "agent": "codex"]}
created_at は ISO 8601 の秒までの時刻で、タイムゾーンを含む (例: "2026-11-16T21:00:05+09:00")。
agent はプレイするエージェントの種類 (agent_kind.AgentKind)。既定の claude のときは書かず、無ければ claude として読む。
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from switch2_harness.agent_kind import DEFAULT_AGENT, AgentKind, parse_agent_kind
from switch2_harness.features import FEATURES, Feature, validate_feature_names

# init で --goal を省略したときの目標
DEFAULT_GOAL = "できるだけ先へ進める"

# harness.json の、エージェントの種類のキー
AGENT_KEY = "agent"


@dataclass(frozen=True)
class HarnessConfig:
    """harness.json の内容。

    game: ゲームの名前
    goal: プレイの目標
    features: 有効な要素の名前 (FEATURES の順)
    created_at: 作業ディレクトリーを作った時刻 (format_created_at の形式)
    agent: プレイするエージェントの種類 (既定は claude)
    """

    game: str
    goal: str
    features: tuple[str, ...]
    created_at: str
    agent: AgentKind = DEFAULT_AGENT


def format_created_at(moment: datetime) -> str:
    """moment を ISO 8601 の秒までの文字列にする (datetime.isoformat(timespec="seconds"))。

    moment にはタイムゾーン付きの datetime を渡す。
    例: 2026-11-16 21:00:05.123456 (UTC+9) は "2026-11-16T21:00:05+09:00"。
    """
    return moment.isoformat(timespec="seconds")


def config_to_json_text(config: HarnessConfig) -> str:
    """config を harness.json の本文にする。

    キーは game / goal / features / created_at で、features は JSON の配列にする。
    config.agent が既定 (claude) でなければ、最後に agent のキーを足す。
    json.dumps(..., ensure_ascii=False, indent=2) の結果に改行 1 つを足したものを返す。
    """
    data: dict[str, object] = {
        "game": config.game,
        "goal": config.goal,
        "features": list(config.features),
        "created_at": config.created_at,
    }
    if config.agent != DEFAULT_AGENT:
        data[AGENT_KEY] = config.agent
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _require_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if isinstance(value, str):
        return value
    raise ValueError(f"harness.json: {key} must be a string")


def _require_str_list(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"harness.json: {key} must be a list of strings")
    return [str(item) for item in value]


def agent_from_data(data: dict[str, object]) -> AgentKind:
    """harness.json のオブジェクト data のエージェントの種類を返す。

    agent のキーが無ければ DEFAULT_AGENT (claude)。あれば agent_kind.parse_agent_kind で読み、
    知らない値なら `harness.json: unknown agent: <値> (available: claude, codex)` の ValueError にする。
    """
    if AGENT_KEY not in data:
        return DEFAULT_AGENT
    try:
        return parse_agent_kind(data[AGENT_KEY])
    except ValueError as e:
        raise ValueError(f"harness.json: {e}") from None


def config_from_json_text(
    text: str, available: tuple[Feature, ...] = FEATURES
) -> HarnessConfig:
    """harness.json の本文を HarnessConfig にする。

    features は features.validate_feature_names(features, available) を通す。
    available に無い名前があれば、その ValueError をそのまま送出する。
    agent は agent_from_data で読む (無ければ claude)。
    JSON として読めない、JSON のオブジェクトでない、キーが無い、値の型が違う
    (game・goal・created_at が文字列でない、features が文字列の配列でない、agent が知らない値) ときも ValueError にする。
    """
    data: object = json.loads(text)
    if isinstance(data, dict):
        return HarnessConfig(
            game=_require_str(data, "game"),
            goal=_require_str(data, "goal"),
            features=validate_feature_names(
                _require_str_list(data, "features"), available
            ),
            created_at=_require_str(data, "created_at"),
            agent=agent_from_data(data),
        )
    raise ValueError("harness.json must be a JSON object")


def write_config(path: Path, config: HarnessConfig) -> None:
    """config を path に UTF-8 で書く (config_to_json_text の内容。既存のファイルは上書きする)。"""
    path.write_text(config_to_json_text(config), encoding="utf-8")


def read_config(path: Path, available: tuple[Feature, ...] = FEATURES) -> HarnessConfig:
    """path の harness.json を UTF-8 で読み、config_from_json_text で HarnessConfig にする。"""
    return config_from_json_text(path.read_text(encoding="utf-8"), available)


def read_agent(path: Path) -> AgentKind:
    """path の harness.json から、エージェントの種類だけを読む (report が使う)。

    要素の名前は検証しない。JSON として読めない・オブジェクトでない・agent が知らない値なら ValueError、
    path が無ければ OSError (FileNotFoundError) をそのまま送出する。
    """
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return agent_from_data(data)
    raise ValueError("harness.json must be a JSON object")
