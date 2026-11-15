"""例外を、記録 (run.jsonl・surface.jsonl・operations.jsonl など) の 1 行に書く説明文にする。"""

from switch2_remote.error_message import describe_error


def describe_exception(error: BaseException) -> str:
    """`<例外のクラス名>: <説明>` の形の 1 行の説明文を返す (例: `RuntimeError: model failed`)。

    説明は switch2_remote.error_message.describe_error (改行と連続する空白を 1 つの空白にまとめる) の値。
    メッセージが空の例外では、クラス名だけにする。
    """
    message = " ".join(str(error).split())
    name = type(error).__name__
    return f"{name}: {describe_error(error)}" if message else name
