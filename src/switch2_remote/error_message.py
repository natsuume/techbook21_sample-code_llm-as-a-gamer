"""例外を、応答の 1 行に埋め込める短い説明文にする。

デーモンの応答は 1 行 1 応答なので、例外のメッセージに含まれる改行や連続する空白は 1 つの空白にまとめる。
メッセージが空の例外 (引数なしの OSError() など) は、何が起きたかが残るようにクラス名で表す。
"""


def describe_error(error: BaseException) -> str:
    """`error already pressed: B` の `already pressed: B` の部分にあたる説明文を返す。"""
    message = " ".join(str(error).split())
    return message or type(error).__name__
