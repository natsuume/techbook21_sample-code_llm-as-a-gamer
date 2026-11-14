"""実行中の記録を送るサーバー送信イベント (SSE。GET /api/runs/{run}/live) のイベントの型と、data の JSON への符号化。

イベントの種類と data:
    request       要求の区間 API の 1 件 (run_views.build_request_item)
    frame         フレームの判定の区間 API の 1 件 (run_views.build_frame_item)
    pad           送ったレポートの区間 API の 1 件 (run_views.build_pad_item)
    run           run.jsonl の出来事の区間 API の 1 件 (run_views.build_event_item)
    note_version  summary の note_versions の 1 件 (run_views.summary_note_version)
    advisor_run   summary の advisor_runs の 1 件 (run_views.summary_advisor_run)
    advisor_step  advisor の実行の API の steps の 1 件に、advisor の実行の名前 name を足したもの
    stats         {"t_end", "counts", "decide_s_median"} (summary の同名の値)
    end           {"record_end": run.jsonl に record_end の行があるか}
I/O を持たない。
"""

import json
import math
from dataclasses import dataclass
from typing import Literal

LiveEventKind = Literal[
    "request",
    "frame",
    "pad",
    "run",
    "note_version",
    "advisor_run",
    "advisor_step",
    "stats",
    "end",
]
# イベントの種類 (SSE の event の名前) の一覧
LIVE_EVENT_KINDS: tuple[LiveEventKind, ...] = (
    "request",
    "frame",
    "pad",
    "run",
    "note_version",
    "advisor_run",
    "advisor_step",
    "stats",
    "end",
)


@dataclass(frozen=True)
class LiveEvent:
    """SSE で送るイベント 1 件。

    kind   イベントの種類 (SSE の event の名前)
    data   SSE の data の JSON にする値 (記録から読んだ値のまま。有限でない数 (NaN など) を含みうる)
    """

    kind: LiveEventKind
    data: dict


def json_compatible(value: object) -> object:
    """value のうち、有限でない float (NaN・inf・-inf) を None にした値を返す。

    dict は値を、list と tuple は要素を、入れ子の中まで同じく直す (tuple は list にする)。ほかの値 (bool・int・
    str・None など) はそのまま返す (True を 1 にしない)。渡した value は変えない。
    区間 API の応答の直列化が有限でない数を null にするのと同じ値を、SSE の data でも返すために使う。
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_compatible(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_compatible(item) for item in value]
    return value


def encode_live_event_data(data: object) -> str:
    """data を SSE の data に入れる 1 行の JSON の文字列にして返す。

    json.dumps(json_compatible(data), ensure_ascii=True, allow_nan=False, separators=(",", ":"))。
    ensure_ascii=True にするのは、記録の文字列にある対になっていないサロゲートを `\\udXXX` のエスケープにして、
    UTF-8 への符号化 (fastapi.sse.format_sse_event) で失敗させないため。
    """
    return json.dumps(
        json_compatible(data),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
