"""主ループの claude -p の stream-json の行の読み方と、標準入力に送る行の形 (どれも I/O を扱わない)。

標準入力に送る 1 行 (user_message_line):
    {"type": "user", "message": {"role": "user", "content": [テキストと画像のブロック]}, "parent_tool_use_id": null}
    画像のブロックは {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": <base64>}}。
    この形で、テキストと base64 の JPEG を送れることを確かめた (2026-11-16、Claude Code 2.1.325)
標準出力の行のうち、主ループが使うもの:
    system/init        各ユーザーのターンの最初に出る (1 つのセッションに何度も出る)
    assistant          モデルの応答。1 つの API の呼び出しの応答が、内容のブロックごとの複数の行に分かれて届き、
                       それらの行の message.usage は同じ値になる。文の取り出しは deep.claude_stream_log.assistant_text
    result             ターンの終わり。result の行の usage は、そのターンの API の呼び出しの合計 (主ループの使用量には
                       使わない)
    system/compact_boundary
                       Claude Code の自動の要約が起きた印
ほかの行 (thinking_tokens・rate_limit_event・permission_denied・commands_changed・command_lifecycle など) は
記録するだけで、主ループの判断には使わない。
"""

from collections.abc import Mapping, Sequence

# 自動の要約の印の行の type と subtype
SYSTEM_MESSAGE_TYPE = "system"
COMPACT_BOUNDARY_SUBTYPE = "compact_boundary"
# セッションの使用量に足す、assistant の行の message.usage のキー
USAGE_TOKEN_KEYS: tuple[str, ...] = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def user_message_line(content: Sequence[Mapping[str, object]]) -> str:
    """標準入力に送る 1 行 (モジュールの docstring の形の JSON に改行を 1 つ足したもの) を返す。

    json.dumps(…, ensure_ascii=False)。content のブロックは渡された順のまま入れる。content が空なら ValueError。
    """
    raise NotImplementedError


def assistant_usage_tokens(data: object) -> int | None:
    """data が assistant の行で message.usage があれば、USAGE_TOKEN_KEYS の値の和 (コンテキストの大きさ) を返す。

    usage に無いキーと、整数でない値 (bool を含む) は 0 として足す。data が assistant の行でない、message か usage が
    object でないときは None を返す。主ループは、最後に読んだ None でない値を、そのセッションの使用量にする
    (足し合わせない)。
    """
    raise NotImplementedError


def is_compact_boundary(data: object) -> bool:
    """data が object で、type が SYSTEM_MESSAGE_TYPE、subtype が COMPACT_BOUNDARY_SUBTYPE なら True を返す。"""
    raise NotImplementedError


def compact_metadata(data: object) -> object | None:
    """compact_boundary の行 data の compact_metadata の値を返す。無ければ (行の形が合わないときを含む) None。"""
    raise NotImplementedError
