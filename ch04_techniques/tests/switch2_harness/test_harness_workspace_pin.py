import pytest

from switch2_harness.workspace_pin import (
    PINNED_WORKSPACE_ENV,
    WorkspacePinError,
    pin_violation_message,
    resolve_agent_workspace,
)


def test_pinned_workspace_env_name():
    assert PINNED_WORKSPACE_ENV == "SWITCH2_HARNESS_WORKSPACE"


def test_pin_violation_message():
    assert pin_violation_message("/plays/one") == (
        "this command is pinned to the workspace: /plays/one"
    )


@pytest.mark.parametrize(
    ("requested", "resolved"),
    [(None, "."), ("plays/one", "plays/one"), ("/elsewhere", "/elsewhere")],
)
def test_without_pin_the_requested_workspace_is_used(requested, resolved):
    assert resolve_agent_workspace(requested, {"PATH": "/usr/bin"}) == resolved


def test_pinned_workspace_is_used_when_option_is_omitted(tmp_path):
    environ = {PINNED_WORKSPACE_ENV: str(tmp_path)}
    assert resolve_agent_workspace(None, environ) == str(tmp_path)


def test_same_workspace_written_differently_is_accepted(tmp_path, monkeypatch):
    (tmp_path / "play").mkdir()
    monkeypatch.chdir(tmp_path)
    environ = {PINNED_WORKSPACE_ENV: str(tmp_path / "play")}
    for written in ("play", "play/", "./play", str(tmp_path / "play") + "/"):
        assert resolve_agent_workspace(written, environ) == str(tmp_path / "play")


def test_another_workspace_is_rejected(tmp_path):
    environ = {PINNED_WORKSPACE_ENV: str(tmp_path / "play")}
    with pytest.raises(WorkspacePinError) as error:
        resolve_agent_workspace(str(tmp_path / "other"), environ)
    assert str(error.value) == (
        f"this command is pinned to the workspace: {tmp_path / 'play'}"
    )


@pytest.mark.parametrize("requested", [None, ".", "/plays/one"])
def test_empty_pin_is_rejected(requested):
    with pytest.raises(WorkspacePinError) as error:
        resolve_agent_workspace(requested, {PINNED_WORKSPACE_ENV: ""})
    assert str(error.value) == "this command is pinned to the workspace: "
