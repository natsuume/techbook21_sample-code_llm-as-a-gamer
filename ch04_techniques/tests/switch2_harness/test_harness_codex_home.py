"""codex_home のテスト。本物の codex・~/.codex・~/.cache には触れない (HOME を tmp_path の下にする)。"""

import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from harness_fakes import make_paths

from switch2_harness.codex_command import (
    PLAYER_PERMISSION_PROFILE,
    codex_home_dir,
    codex_rules_text,
)
from switch2_harness.codex_home import (
    DEFAULT_HARNESS_EXECUTABLE,
    HARNESS_DIRS,
    NATIVE_PROBE_POLL_S,
    NATIVE_PROBE_TIMEOUT_S,
    PROTECTED_ROOT_NAMES,
    CodexSetupError,
    ensure_auth_link,
    find_codex_skill_files_to_disable,
    find_native_codex_dir,
    native_codex_dir_of,
    prepare_codex_home,
    probe_native_codex_dir,
    skill_files_in,
)
from switch2_harness.workspace_pin import PINNED_WORKSPACE_ENV

NATIVE = Path("/opt/codex/vendor/x86_64-unknown-linux-musl/bin")


def test_harness_dirs():
    assert HARNESS_DIRS == ("frames", "multi-frames", "records")


# --- 本体のディレクトリーを探す ---


def test_native_codex_dir_of_picks_the_first_codex():
    executables = [Path("/usr/bin/env"), NATIVE / "codex", Path("/other/codex")]
    assert native_codex_dir_of(executables) == NATIVE
    assert native_codex_dir_of([Path("/usr/bin/node")]) is None


def write_proc_entry(proc: Path, pid: int, ppid: int, exe: Path | None) -> None:
    directory = proc / str(pid)
    directory.mkdir()
    rest = " ".join(["0"] * 30)
    (directory / "stat").write_text(f"{pid} (x) S {ppid} {pid} {pid} {rest}\n")
    if exe is not None:
        (directory / "exe").symlink_to(exe)


def test_find_native_codex_dir_looks_at_the_root_and_its_descendants(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    write_proc_entry(proc, 100, 1, Path("/usr/bin/node"))
    write_proc_entry(proc, 101, 100, None)
    write_proc_entry(proc, 102, 101, NATIVE / "codex")
    write_proc_entry(proc, 200, 1, Path("/elsewhere/codex"))
    assert find_native_codex_dir(100, proc) == NATIVE
    assert find_native_codex_dir(102, proc) == NATIVE
    assert find_native_codex_dir(101, proc) == NATIVE


def test_find_native_codex_dir_without_codex_is_none(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    write_proc_entry(proc, 100, 1, Path("/usr/bin/node"))
    assert find_native_codex_dir(100, proc) is None


@dataclass
class FakeAppServer:
    pid: int = 4242
    exits_after_polls: int | None = None
    polls: int = 0
    closed: int = 0

    def poll(self) -> int | None:
        self.polls += 1
        if self.exits_after_polls is not None and self.polls >= self.exits_after_polls:
            return 0
        return None

    def close(self) -> None:
        self.closed += 1


@dataclass
class ProbeScenario:
    server: FakeAppServer = field(default_factory=FakeAppServer)
    found_at: int | None = None
    now: float = 0.0
    finds: list[int] = field(default_factory=list)
    starts: list[tuple[dict[str, str], Path]] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)

    def start(self, env: dict[str, str], cwd: Path) -> FakeAppServer:
        self.starts.append((env, cwd))
        return self.server

    def find(self, pid: int) -> Path | None:
        self.finds.append(pid)
        if self.found_at is not None and len(self.finds) >= self.found_at:
            return NATIVE
        return None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def probe(self) -> Path | None:
        return probe_native_codex_dir(
            {"PATH": "/usr/bin"},
            Path("/w"),
            start=self.start,
            find=self.find,
            monotonic=self.monotonic,
            sleep=self.sleep,
        )


def test_probe_returns_the_directory_once_found_and_closes_the_server():
    scenario = ProbeScenario(found_at=3)
    assert scenario.probe() == NATIVE
    assert scenario.starts == [({"PATH": "/usr/bin"}, Path("/w"))]
    assert scenario.finds == [4242, 4242, 4242]
    assert scenario.sleeps == [NATIVE_PROBE_POLL_S, NATIVE_PROBE_POLL_S]
    assert scenario.server.closed == 1


def test_probe_gives_up_when_the_server_exits():
    scenario = ProbeScenario(server=FakeAppServer(exits_after_polls=2))
    assert scenario.probe() is None
    assert scenario.server.closed == 1


def test_probe_gives_up_after_the_timeout():
    scenario = ProbeScenario()
    assert scenario.probe() is None
    assert scenario.now >= NATIVE_PROBE_TIMEOUT_S
    assert scenario.server.closed == 1


def test_probe_closes_the_server_when_finding_fails():
    scenario = ProbeScenario()

    def broken_find(pid: int) -> Path | None:
        raise OSError("cannot read /proc")

    with pytest.raises(OSError):
        probe_native_codex_dir(
            {},
            Path("/w"),
            start=scenario.start,
            find=broken_find,
            monotonic=scenario.monotonic,
            sleep=scenario.sleep,
        )
    assert scenario.server.closed == 1


# --- スキル ---


def write_skill(skills_dir: Path, name: str) -> Path:
    path = skills_dir / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: x\n---\n", encoding="utf-8")
    return path


def test_skill_files_in_lists_skills_except_hidden_ones(tmp_path):
    skills = tmp_path / "skills"
    b = write_skill(skills, "b")
    a = write_skill(skills, "a")
    write_skill(skills, ".system")
    (skills / "empty").mkdir()
    assert skill_files_in(skills) == [a, b]
    assert skill_files_in(tmp_path / "missing") == []


def test_find_codex_skill_files_to_disable_collects_parent_and_user_skills(tmp_path):
    repo = tmp_path / "repo"
    workspace = repo / "ch04" / "plays" / "game"
    workspace.mkdir(parents=True)
    in_workspace = write_skill(workspace / ".agents" / "skills", "w")
    in_repo = write_skill(repo / ".agents" / "skills", "r")
    home = tmp_path / "home"
    in_home = write_skill(home / ".agents" / "skills", "h")
    codex_home = tmp_path / "codex-home"
    in_codex_home = write_skill(codex_home / "skills", "c")
    write_skill(codex_home / "skills", ".system")
    env = {"HOME": str(home), "CODEX_HOME": str(codex_home)}
    assert find_codex_skill_files_to_disable(workspace, env) == (
        in_workspace,
        in_repo,
        in_home,
        in_codex_home,
    )


# --- 認証のリンク ---


def test_ensure_auth_link_creates_a_symbolic_link(tmp_path):
    source = tmp_path / "user" / "auth.json"
    source.parent.mkdir()
    source.write_text("{}", encoding="utf-8")
    link = tmp_path / "home" / "auth.json"
    link.parent.mkdir()
    ensure_auth_link(link, source)
    assert link.is_symlink()
    assert Path(os.readlink(link)) == source
    # 2 回目はそのまま
    ensure_auth_link(link, source)
    assert Path(os.readlink(link)) == source


def test_ensure_auth_link_replaces_a_link_to_another_file(tmp_path):
    source = tmp_path / "auth.json"
    source.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "old.json")
    ensure_auth_link(link, source)
    assert Path(os.readlink(link)) == source


def test_ensure_auth_link_keeps_a_regular_file_and_fails(tmp_path):
    source = tmp_path / "auth.json"
    source.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.write_text('{"refreshed": true}', encoding="utf-8")
    with pytest.raises(CodexSetupError) as error:
        ensure_auth_link(link, source)
    assert "is not a symbolic link" in str(error.value)
    assert link.read_text(encoding="utf-8") == '{"refreshed": true}'


def test_ensure_auth_link_without_login_fails(tmp_path):
    with pytest.raises(CodexSetupError) as error:
        ensure_auth_link(tmp_path / "link.json", tmp_path / "auth.json")
    assert "codex login" in str(error.value)
    assert not (tmp_path / "link.json").exists()


# --- prepare_codex_home ---


@dataclass
class ProbeRecorder:
    result: Path | None = NATIVE
    error: Exception | None = None
    calls: list[tuple[dict[str, str], Path]] = field(default_factory=list)

    def __call__(self, env: dict[str, str], cwd: Path) -> Path | None:
        self.calls.append((env, cwd))
        if self.error is not None:
            raise self.error
        return self.result


def make_environ(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    return {"HOME": str(home), "PATH": "/usr/bin", "OPENAI_API_KEY": "secret"}


def make_executable(tmp_path: Path) -> Path:
    """ハーネスの実行ファイルの代わり (中身は使わない)。"""
    executable = tmp_path / "venv" / "bin" / "switch2-harness"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    return executable


def make_workspace(tmp_path: Path):
    paths = make_paths(tmp_path / "play")
    paths.harness_file.write_text("{}", encoding="utf-8")
    (paths.root / "AGENTS.md").write_text("# 指示\n", encoding="utf-8")
    (paths.root / "notes.md").write_text("# メモ\n", encoding="utf-8")
    return paths


def read_workspace_profile(home: Path) -> dict[str, str]:
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    return config["permissions"][PLAYER_PERMISSION_PROFILE]["filesystem"][
        ":workspace_roots"
    ]


def test_protected_root_names():
    assert PROTECTED_ROOT_NAMES == (
        "AGENTS.md",
        "harness.json",
        "frames",
        "multi-frames",
        "records",
    )


def test_default_harness_executable_is_in_this_virtual_environment():
    assert DEFAULT_HARNESS_EXECUTABLE == Path(sys.prefix) / "bin" / "switch2-harness"


def test_prepare_codex_home_writes_the_config_rules_and_auth_link(tmp_path):
    environ = make_environ(tmp_path)
    paths = make_workspace(tmp_path)
    executable = make_executable(tmp_path)
    probe = ProbeRecorder()
    home = prepare_codex_home(
        paths, ("look", "press"), ("notes.md",), environ, executable, probe=probe
    )
    assert home == codex_home_dir(paths.root, environ)
    assert home.is_relative_to(tmp_path / "home" / ".cache")
    assert Path(os.readlink(home / "auth.json")) == (
        tmp_path / "home" / ".codex" / "auth.json"
    )
    [(env, cwd)] = probe.calls
    assert cwd == paths.root
    assert env["CODEX_HOME"] == str(home)
    assert env[PINNED_WORKSPACE_ENV] == str(paths.root)
    assert "OPENAI_API_KEY" not in env
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    profile = config["permissions"][PLAYER_PERMISSION_PROFILE]["filesystem"]
    assert profile[str(NATIVE)] == "read"
    # 書き換えられるファイルがあるので、ハーネスのディレクトリーを先に作り、ハーネスが置くものを読み取り専用にする
    assert profile[":workspace_roots"] == {
        ".": "write",
        "AGENTS.md": "read",
        "frames": "read",
        "harness.json": "read",
        "multi-frames": "read",
        "records": "read",
    }
    assert (paths.root / "multi-frames").is_dir()
    # rules に書くのは、ハーネスの実行ファイルの絶対パスの形だけ
    assert (home / "rules" / "switch2-harness.rules").read_text(
        encoding="utf-8"
    ) == codex_rules_text(executable, ("look", "press"))
    assert not list(home.glob("*.tmp"))


def test_prepare_codex_home_does_not_protect_what_the_agent_made(tmp_path):
    # エージェントが作ったファイルと、作業ディレクトリーの外を指すリンクは、権限プロファイルに書かない
    environ = make_environ(tmp_path)
    paths = make_workspace(tmp_path)
    (paths.root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (paths.root / "leak").symlink_to(tmp_path / "home")
    home = prepare_codex_home(
        paths,
        ("look",),
        ("notes.md",),
        environ,
        make_executable(tmp_path),
        probe=ProbeRecorder(),
    )
    profile = read_workspace_profile(home)
    assert "pyproject.toml" not in profile
    assert "leak" not in profile
    assert str(tmp_path / "home") not in (home / "config.toml").read_text("utf-8")


@pytest.mark.parametrize("name", ["records", "multi-frames", "harness.json"])
def test_prepare_codex_home_refuses_a_symbolic_link_in_place_of_a_harness_entry(
    tmp_path, name
):
    environ = make_environ(tmp_path)
    paths = make_workspace(tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    entry = paths.root / name
    if entry.is_dir():
        entry.rmdir()
    else:
        entry.unlink(missing_ok=True)
    entry.symlink_to(target)
    with pytest.raises(CodexSetupError) as error:
        prepare_codex_home(
            paths,
            ("look",),
            ("notes.md",),
            environ,
            make_executable(tmp_path),
            probe=ProbeRecorder(),
        )
    assert "is a symbolic link" in str(error.value)
    assert list(target.iterdir()) == []


def test_prepare_codex_home_without_writable_files_makes_nothing_writable(tmp_path):
    environ = make_environ(tmp_path)
    paths = make_workspace(tmp_path)
    home = prepare_codex_home(
        paths, ("look",), (), environ, make_executable(tmp_path), probe=ProbeRecorder()
    )
    assert read_workspace_profile(home) == {".": "read"}
    assert not (paths.root / "multi-frames").exists()


def test_prepare_codex_home_rewrites_on_the_next_run(tmp_path):
    environ = make_environ(tmp_path)
    paths = make_workspace(tmp_path)
    executable = make_executable(tmp_path)
    home = prepare_codex_home(
        paths, ("look",), (), environ, executable, probe=ProbeRecorder()
    )
    prepare_codex_home(
        paths, ("look", "status"), (), environ, executable, probe=ProbeRecorder()
    )
    assert (home / "rules" / "switch2-harness.rules").read_text(
        encoding="utf-8"
    ) == codex_rules_text(executable, ("look", "status"))


def test_prepare_codex_home_fails_without_the_harness_executable(tmp_path):
    environ = make_environ(tmp_path)
    paths = make_workspace(tmp_path)
    probe = ProbeRecorder()
    with pytest.raises(CodexSetupError) as error:
        prepare_codex_home(
            paths, ("look",), (), environ, tmp_path / "missing", probe=probe
        )
    assert "switch2-harness executable not found" in str(error.value)
    assert probe.calls == []


@pytest.mark.parametrize(
    ("probe", "message"),
    [
        (ProbeRecorder(result=None), "could not find the codex executable"),
        (ProbeRecorder(error=FileNotFoundError("codex")), "cannot start codex"),
    ],
)
def test_prepare_codex_home_fails_without_codex(tmp_path, probe, message):
    environ = make_environ(tmp_path)
    paths = make_workspace(tmp_path)
    with pytest.raises(CodexSetupError) as error:
        prepare_codex_home(
            paths, ("look",), (), environ, make_executable(tmp_path), probe=probe
        )
    assert message in str(error.value)


def test_prepare_codex_home_fails_without_home_or_login(tmp_path):
    paths = make_workspace(tmp_path)
    executable = make_executable(tmp_path)
    with pytest.raises(CodexSetupError):
        prepare_codex_home(paths, ("look",), (), {}, executable, probe=ProbeRecorder())
    with pytest.raises(CodexSetupError) as error:
        prepare_codex_home(
            paths,
            ("look",),
            (),
            {"HOME": str(tmp_path / "nobody")},
            executable,
            probe=ProbeRecorder(),
        )
    assert "codex login" in str(error.value)


# --- ハーネスの実行ファイルが、作業ディレクトリーに置かれたものを読まないこと ---

PLANTED_PYTHON = """from pathlib import Path
Path({marker!r}).write_text("planted code ran", encoding="utf-8")
"""


@pytest.mark.skipif(
    not DEFAULT_HARNESS_EXECUTABLE.is_file(),
    reason="the switch2-harness console script is not installed",
)
def test_harness_executable_ignores_files_planted_in_the_workspace(tmp_path):
    """rules がサンドボックスの外で動かす実行ファイルが、cwd (作業ディレクトリー) のファイルでコードを動かさないこと。

    エージェントが作業ディレクトリーに置きうる、Python と uv が読むファイルを置いて、本物の console script を
    cwd を作業ディレクトリーにして動かし、目印のファイルが作られないことを確かめる。デーモンに触れない report を使う。
    """
    paths = make_workspace(tmp_path)
    marker = tmp_path / "marker"
    code = PLANTED_PYTHON.format(marker=str(marker))
    for name in (
        "sitecustomize.py",
        "usercustomize.py",
        "json.py",
        "planted_backend.py",
    ):
        (paths.root / name).write_text(code, encoding="utf-8")
    package = paths.root / "switch2_harness"
    package.mkdir()
    (package / "__init__.py").write_text(code, encoding="utf-8")
    (package / "cli.py").write_text(
        code + "def main():\n    return 0\n", encoding="utf-8"
    )
    (paths.root / "planted.pth").write_text(f"import json; {code.splitlines()[1]}\n")
    (paths.root / "pyvenv.cfg").write_text("home = /nonexistent\n", encoding="utf-8")
    (paths.root / "pyproject.toml").write_text(
        '[project]\nname = "planted"\nversion = "0"\n'
        '[project.scripts]\nswitch2-harness = "planted_backend:main"\n'
        '[build-system]\nrequires = []\nbuild-backend = "planted_backend"\n'
        'backend-path = ["."]\n',
        encoding="utf-8",
    )
    (paths.root / "uv.toml").write_text('python-preference = "system"\n')
    (paths.root / ".python-version").write_text("3.0\n", encoding="utf-8")
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("PYTHON", "VIRTUAL_ENV", "UV_"))
    }
    result = subprocess.run(
        [str(DEFAULT_HARNESS_EXECUTABLE), "report", "--workspace", str(paths.root)],
        cwd=paths.root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("記録の時間:")
    assert not marker.exists()
