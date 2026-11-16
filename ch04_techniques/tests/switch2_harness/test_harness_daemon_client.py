"""daemon_client のテスト。

本物のデーモン ("\\0switch2-remote") には接続せず、テストごとに一意な抽象名前空間のアドレスで
1 回だけ応答するサーバーをスレッドで立てる。
"""

import inspect
import socket
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from switch2_harness.daemon_client import send_command

SERVER_TIMEOUT_S = 5.0


def unique_address() -> str:
    return "\0switch2-harness-test-" + uuid.uuid4().hex


@dataclass
class OneShotServer:
    address: str
    thread: threading.Thread
    received: list[str] = field(default_factory=list)


@contextmanager
def one_shot_server(reply: bytes | None) -> Iterator[OneShotServer]:
    """1 つの接続から 1 行を受け取り、reply を送って (None なら何も送らずに) 閉じるサーバー。"""
    address = unique_address()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(address)
    listener.listen(1)
    listener.settimeout(SERVER_TIMEOUT_S)
    received: list[str] = []

    def serve() -> None:
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            try:
                conn.settimeout(SERVER_TIMEOUT_S)
                received.append(conn.makefile("r", encoding="utf-8").readline())
                if reply is not None:
                    conn.sendall(reply)
            except OSError:
                pass  # 相手が先に閉じた場合 (後片付けの接続など)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    server = OneShotServer(address=address, thread=thread, received=received)
    try:
        yield server
    finally:
        if thread.is_alive():
            # テストが接続しないまま終わった場合も、accept で待つスレッドを起こして終わらせる
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as poke:
                    poke.connect(address)
            except OSError:
                pass
        thread.join(timeout=SERVER_TIMEOUT_S)
        listener.close()


def test_default_address_is_the_chapter2_daemon():
    default = inspect.signature(send_command).parameters["address"].default
    assert default == "\0switch2-remote"


def test_send_command_sends_one_line_and_returns_the_reply():
    with one_shot_server(b"ok tap B+RIGHT\n") as server:
        reply = send_command(["tap", "B+RIGHT"], server.address)
        server.thread.join(timeout=SERVER_TIMEOUT_S)
    assert reply == "ok tap B+RIGHT"
    assert server.received == ["tap B+RIGHT\n"]


def test_send_command_sends_shot_with_absolute_path():
    with one_shot_server(b"ok /w/frames/000001.png\n") as server:
        reply = send_command(["shot", "/w/frames/000001.png"], server.address)
        server.thread.join(timeout=SERVER_TIMEOUT_S)
    assert reply == "ok /w/frames/000001.png"
    assert server.received == ["shot /w/frames/000001.png\n"]


def test_send_command_strips_the_reply():
    with one_shot_server(b"  ok held=none \r\n") as server:
        reply = send_command(["up", "ALL"], server.address)
    assert reply == "ok held=none"


def test_send_command_returns_only_the_first_line():
    with one_shot_server(b"ok first\nsecond\n") as server:
        reply = send_command(["status"], server.address)
    assert reply == "ok first"


def test_send_command_returns_daemon_error_as_is():
    with one_shot_server(b"error unknown command: fps\n") as server:
        reply = send_command(["fps"], server.address)
    assert reply == "error unknown command: fps"


def test_send_command_without_reply_is_an_error():
    with one_shot_server(None) as server:
        reply = send_command(["status"], server.address)
        server.thread.join(timeout=SERVER_TIMEOUT_S)
    assert reply == "error no reply from daemon"
    assert server.received == ["status\n"]


def test_send_command_without_daemon_is_an_error():
    reply = send_command(["status"], unique_address())
    assert reply.startswith("error cannot connect to daemon: ")


def test_send_command_rejects_line_breaks_before_connecting():
    # 接続を試みると "cannot connect" になるアドレスで、検証が接続より先であることを確かめる
    reply = send_command(["tap", "A\nquit"], unique_address())
    assert reply == "error arguments must not contain line breaks"
