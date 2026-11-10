import pytest

from switch2_remote.controller_bridge import UnconfirmedInputError
from switch2_remote.error_message import describe_error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError("already pressed: B"), "already pressed: B"),
        (RuntimeError("bridge exited"), "bridge exited"),
        (UnconfirmedInputError("no ack"), "no ack"),
    ],
)
def test_describe_error_returns_message(error, expected):
    assert describe_error(error) == expected


def test_describe_error_joins_lines_into_one():
    error = RuntimeError("OpenCV(4.10.0) imwrite\n  error: (-215:Assertion failed)\n")
    assert (
        describe_error(error) == "OpenCV(4.10.0) imwrite error: (-215:Assertion failed)"
    )


@pytest.mark.parametrize("error", [OSError(), RuntimeError(""), RuntimeError(" \n")])
def test_describe_error_falls_back_to_class_name_when_message_is_empty(error):
    assert describe_error(error) == type(error).__name__
