"""run の始めに、Codex 用の専用の CODEX_HOME を用意する (ファイルとプロセスに触れる部分)。

prepare_codex_home が、作業ディレクトリーごとの CODEX_HOME (codex_command.codex_home_dir。作業ディレクトリーの外の
${XDG_CACHE_HOME:-~/.cache}/switch2-harness/codex-home/<名前>) に、次のものを置く:
    auth.json   ユーザーの ${CODEX_HOME:-~/.codex}/auth.json へのシンボリックリンク (写しは作らない)。
                既にリンクがあれば張り直し、リンクでないファイルがあれば止める (Codex がトークンの更新で
                置き換えた可能性があり、上書きするとその内容を失うため)
    config.toml codex_command.codex_config_toml の本文 (run のたびに書き直す)。書き換えられるファイルがあるときは、
                ハーネスが run の間に書くディレクトリー (HARNESS_DIRS) を、無ければ先に作業ディレクトリーに作り、
                直下にあるハーネスが置くもの (PROTECTED_ROOT_NAMES) を読み取り専用にする (無いパスを権限プロファイルに
                書かないため。codex_command の説明を参照)。エージェントが作ったものは読み取り専用にしない
    rules/switch2-harness.rules  codex_command.codex_rules_text の本文 (run のたびに書き直す)。サンドボックスの外で
                動かすのは、この Python の仮想環境のハーネスの実行ファイル (DEFAULT_HARNESS_EXECUTABLE) の絶対パスの形だけ
config.toml の権限プロファイルには Codex 本体のディレクトリーが要る。`codex` のコマンドは Node の包みで、その子孫に
本体 (実行ファイルの名前が codex) がいる。codex exec は短命なので、`codex app-server` を短く起動して子孫を /proc で探し
(probe_native_codex_dir)、見つけたら標準入力を閉じて終わらせる (2026-11-21、codex-cli 0.171.0 で、起動から約 0.05 秒で
見つかり、標準入力を閉じると終了コード 0 で終わることを確かめた)。
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from switch2_harness.agent_kind import CODEX_AGENT, instructions_file_name
from switch2_harness.codex_command import (
    CODEX_AUTH_FILE_NAME,
    CODEX_COMMAND,
    CODEX_CONFIG_FILE_NAME,
    CODEX_HOME_ENV,
    CODEX_RULES_DIR_NAME,
    CODEX_RULES_FILE_NAME,
    build_codex_env,
    codex_auth_source,
    codex_config_toml,
    codex_home_dir,
    codex_rules_text,
    harness_executable,
)
from switch2_harness.multi_frame import MULTI_FRAMES_DIR_NAME
from switch2_harness.process_tree import PROC_DIR, read_descendants
from switch2_harness.workspace import (
    FRAMES_DIR_NAME,
    HARNESS_FILE_NAME,
    RECORDS_DIR_NAME,
    WorkspacePaths,
)

# Codex 本体の実行ファイルの名前
NATIVE_CODEX_NAME = "codex"
# codex app-server を起動してから、本体を探すのをあきらめるまでの秒数
NATIVE_PROBE_TIMEOUT_S = 10.0
# 本体を探す間隔 (秒)
NATIVE_PROBE_POLL_S = 0.05
# 標準入力を閉じてから、codex app-server が終わるのを待つ秒数 (過ぎたらプロセスグループに SIGKILL を送る)
PROBE_CLOSE_TIMEOUT_S = 5.0

# ハーネスが run の間に書き、エージェントには読ませるだけのディレクトリー (作業ディレクトリーの直下)
HARNESS_DIRS: tuple[str, ...] = (
    FRAMES_DIR_NAME,
    MULTI_FRAMES_DIR_NAME,
    RECORDS_DIR_NAME,
)

# 書き換えられるファイルがあるとき (作業ディレクトリーの直下を write にするとき)、読み取り専用にするもの
PROTECTED_ROOT_NAMES: tuple[str, ...] = (
    instructions_file_name(CODEX_AGENT),
    HARNESS_FILE_NAME,
    *HARNESS_DIRS,
)

# この Python の仮想環境のハーネスの実行ファイル (run と init が使う。uv run で起動したときは
# <ch04_techniques>/.venv/bin/switch2-harness)
DEFAULT_HARNESS_EXECUTABLE = harness_executable(Path(sys.prefix))

# スキルのディレクトリーの、各階層からの相対パスと、スキルの本文のファイル
AGENTS_SKILLS_RELATIVE_DIR = Path(".agents") / "skills"
SKILL_FILE = "SKILL.md"
# CODEX_HOME の下のスキルのディレクトリーと、管理者のスキルのディレクトリー
CODEX_HOME_SKILLS_DIR = "skills"
ADMIN_SKILLS_DIR = Path("/etc/codex/skills")


class CodexSetupError(Exception):
    """専用の CODEX_HOME を用意できなかった (メッセージに理由を書く)。run は起動せずに error 行を出す。"""


class AppServerProcess(Protocol):
    """本体を探すために起動した codex app-server。本番は AppServerProbe。"""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def close(self) -> None:
        """標準入力を閉じて終わらせる (終わらなければ止める)。"""
        ...


class AppServerProbe:
    """start_app_server が起動した codex app-server (subprocess.Popen を包む)。"""

    def __init__(self, popen: subprocess.Popen[bytes]) -> None:
        self._popen = popen

    @property
    def pid(self) -> int:
        return self._popen.pid

    def poll(self) -> int | None:
        return self._popen.poll()

    def close(self) -> None:
        """標準入力を閉じ、PROBE_CLOSE_TIMEOUT_S 秒待っても終わらなければプロセスグループに SIGKILL を送って待つ。"""
        if self._popen.stdin is not None:
            self._popen.stdin.close()
        try:
            self._popen.wait(timeout=PROBE_CLOSE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self._popen.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._popen.wait()


def start_app_server(env: dict[str, str], cwd: Path) -> AppServerProbe:
    """`codex app-server` を cwd で起動する。標準入力は PIPE、標準出力と標準エラー出力は捨てる。

    新しいセッションで起動する (止めるときにプロセスグループにシグナルを送れるようにする)。
    codex が見つからなければ OSError (FileNotFoundError) をそのまま送出する。
    """
    popen = subprocess.Popen(
        [CODEX_COMMAND, "app-server"],
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return AppServerProbe(popen)


def native_codex_dir_of(executables: Iterable[Path]) -> Path | None:
    """実行ファイルのパスのうち、名前が NATIVE_CODEX_NAME の最初のもののディレクトリーを返す。無ければ None。"""
    for executable in executables:
        if executable.name == NATIVE_CODEX_NAME:
            return executable.parent
    return None


def find_native_codex_dir(root_pid: int, proc_dir: Path = PROC_DIR) -> Path | None:
    """プロセス root_pid とその子孫 (process_tree.read_descendants) の実行ファイル (proc_dir/<pid>/exe のリンク先) から、
    native_codex_dir_of で Codex 本体のディレクトリーを探す。読めないプロセスは飛ばす。"""
    pids = [root_pid, *(entry.pid for entry in read_descendants(root_pid, proc_dir))]
    executables: list[Path] = []
    for pid in pids:
        try:
            executables.append(Path(os.readlink(proc_dir / str(pid) / "exe")))
        except OSError:
            continue
    return native_codex_dir_of(executables)


def probe_native_codex_dir(
    env: dict[str, str],
    cwd: Path,
    *,
    start: Callable[[dict[str, str], Path], AppServerProcess] = start_app_server,
    find: Callable[[int], Path | None] = find_native_codex_dir,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Path | None:
    """codex app-server を start(env, cwd) で起動し、Codex 本体のディレクトリーを find(<pid>) で探して返す。

    NATIVE_PROBE_POLL_S 秒ごとに探し、見つからないまま NATIVE_PROBE_TIMEOUT_S 秒たつか、プロセスが終わったら None。
    どの場合も (例外のときも) 最後に close で終わらせる。
    """
    process = start(env, cwd)
    try:
        deadline = monotonic() + NATIVE_PROBE_TIMEOUT_S
        while True:
            found = find(process.pid)
            if found is not None:
                return found
            if process.poll() is not None or monotonic() >= deadline:
                return None
            sleep(NATIVE_PROBE_POLL_S)
    finally:
        process.close()


def skill_files_in(skills_dir: Path) -> list[Path]:
    """skills_dir の直下の <名前>/SKILL.md のうち、ファイルとしてあるものを名前の順に返す。

    名前が . で始まるディレクトリー (同梱のスキルの .system など。skills.bundled.enabled = false で止める) は含めない。
    skills_dir が無いか、一覧を読めなければ空のリスト。
    """
    try:
        if not skills_dir.is_dir():
            return []
        children = sorted(skills_dir.iterdir())
    except OSError:
        return []
    files: list[Path] = []
    for child in children:
        if child.name.startswith("."):
            continue
        skill_file = child / SKILL_FILE
        try:
            if skill_file.is_file():
                files.append(skill_file)
        except OSError:
            continue
    return files


def find_codex_skill_files_to_disable(
    workspace_root: Path, env: Mapping[str, str]
) -> tuple[Path, ...]:
    """Codex に読ませないスキルの SKILL.md のパスを、重複を除いて見つけた順に返す。

    Codex は、作業ディレクトリーからリポジトリーのルートまでの各階層の .agents/skills/<名前>/SKILL.md と、
    ユーザーのスキル ($HOME/.agents/skills と $CODEX_HOME/skills) と、管理者のスキル (/etc/codex/skills) を読む。
    workspace_root とその親 (根まで) の .agents/skills/、env の HOME があれば $HOME/.agents/skills、
    env の CODEX_HOME (専用の CODEX_HOME) があれば $CODEX_HOME/skills、ADMIN_SKILLS_DIR の順に skill_files_in を並べる。
    """
    dirs = [
        directory / AGENTS_SKILLS_RELATIVE_DIR
        for directory in (workspace_root, *workspace_root.parents)
    ]
    home = env.get("HOME")
    if home:
        dirs.append(Path(home) / AGENTS_SKILLS_RELATIVE_DIR)
    codex_home = env.get(CODEX_HOME_ENV)
    if codex_home:
        dirs.append(Path(codex_home) / CODEX_HOME_SKILLS_DIR)
    dirs.append(ADMIN_SKILLS_DIR)
    found: dict[Path, None] = {}
    for skills_dir in dirs:
        for path in skill_files_in(skills_dir):
            found[path] = None
    return tuple(found)


def ensure_auth_link(link: Path, source: Path) -> None:
    """link を source (ユーザーの auth.json) へのシンボリックリンクにする。

    - source がファイルでなければ CodexSetupError (codex にログインしていない)
    - link がシンボリックリンクなら、リンク先が source でなければ張り直す
    - link がシンボリックリンクでないファイルなどなら、変えずに CodexSetupError
      (Codex がトークンの更新で置き換えた可能性があり、上書きすると新しい内容を失うため。人が確かめて消す)
    - link が無ければ作る
    """
    if not source.is_file():
        raise CodexSetupError(
            f"codex login not found: {source} (run `codex login` first)"
        )
    if link.is_symlink():
        if Path(os.readlink(link)) == source:
            return
        link.unlink()
    elif link.exists():
        raise CodexSetupError(
            f"{link} is not a symbolic link to {source}; codex may have replaced it. "
            "Check its content, remove it, and run again"
        )
    link.symlink_to(source)


def _write_text_atomically(path: Path, text: str) -> None:
    """path に ".tmp" を足したファイルに UTF-8 で書いてから、os.replace で path に置き換える。"""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


# probe_native_codex_dir と同じ形の関数 (prepare_codex_home の probe で差し替える)
ProbeNativeCodexDir = Callable[[dict[str, str], Path], Path | None]

# prepare_codex_home と同じ形の関数 (作業ディレクトリー, エージェント用のサブコマンド, 書き換えられるファイル,
# 起動した人の環境変数, ハーネスの実行ファイル) → CODEX_HOME。commands.handle_run が受け取り、テストでは偽物を渡す
PrepareCodex = Callable[
    [WorkspacePaths, Sequence[str], Sequence[str], Mapping[str, str], Path], Path
]


def protected_root_entries(root: Path) -> list[str]:
    """作業ディレクトリー root の直下にある、ハーネスが置くもの (PROTECTED_ROOT_NAMES) の名前を返す (その順)。

    無いものは含めない。どれかがシンボリックリンクなら CodexSetupError にする (リンク先を読み取り専用にして
    サンドボックスから読めるようにしたり、ハーネスがリンク先に書いたりしないため)。
    エージェントが作ったものは含めない (読み取り専用にせず、作業ディレクトリーの外を指すリンクも権限プロファイルに書かない)。
    """
    names: list[str] = []
    for name in PROTECTED_ROOT_NAMES:
        path = root / name
        if path.is_symlink():
            raise CodexSetupError(f"{path} is a symbolic link; remove it and run again")
        if path.exists():
            names.append(name)
    return names


def prepare_codex_home(
    paths: WorkspacePaths,
    agent_commands: Sequence[str],
    writable_files: Sequence[str],
    environ: Mapping[str, str],
    executable: Path,
    *,
    probe: ProbeNativeCodexDir = probe_native_codex_dir,
) -> Path:
    """paths の作業ディレクトリーの専用の CODEX_HOME を用意し、そのパスを返す (モジュールの説明を参照)。

    executable はハーネスの実行ファイルの絶対パス (codex_command.harness_executable。rules に書く)。
    1. executable がファイルでなければ CodexSetupError
    2. home = codex_command.codex_home_dir(paths.root, environ) を親も含めて作る
    3. ensure_auth_link(home / "auth.json", codex_command.codex_auth_source(environ))
    4. env = codex_command.build_codex_env(environ, home, paths.root) で、probe(env, paths.root) により本体の
       ディレクトリーを探す
    5. writable_files が空でなければ、paths.root の下の HARNESS_DIRS のうち無いものを作る
    6. home / "config.toml" に codex_config_toml(<本体>, protected_root_entries(paths.root), writable_files,
       find_codex_skill_files_to_disable(paths.root, env)) を、home / "rules" / "switch2-harness.rules" に
       codex_rules_text(executable, agent_commands) を書く (どちらも一時ファイルに書いてから置き換える)
    HOME が無い・ログインしていない・auth.json がリンクでない・codex を起動できない (OSError)・本体が見つからない・
    ハーネスが置くものがシンボリックリンクのときは CodexSetupError にする。
    """
    if not executable.is_file():
        raise CodexSetupError(f"switch2-harness executable not found: {executable}")
    try:
        home = codex_home_dir(paths.root, environ)
        source = codex_auth_source(environ)
    except ValueError as e:
        raise CodexSetupError(str(e)) from None
    home.mkdir(parents=True, exist_ok=True)
    ensure_auth_link(home / CODEX_AUTH_FILE_NAME, source)
    env = build_codex_env(environ, home, paths.root)
    try:
        native_dir = probe(env, paths.root)
    except OSError as e:
        raise CodexSetupError(f"cannot start codex: {e}") from None
    if native_dir is None:
        raise CodexSetupError("could not find the codex executable under `codex`")
    if writable_files:
        protected_root_entries(paths.root)
        for name in HARNESS_DIRS:
            (paths.root / name).mkdir(exist_ok=True)
    config_text = codex_config_toml(
        native_dir,
        protected_root_entries(paths.root),
        writable_files,
        find_codex_skill_files_to_disable(paths.root, env),
    )
    _write_text_atomically(home / CODEX_CONFIG_FILE_NAME, config_text)
    rules_dir = home / CODEX_RULES_DIR_NAME
    rules_dir.mkdir(exist_ok=True)
    _write_text_atomically(
        rules_dir / CODEX_RULES_FILE_NAME, codex_rules_text(executable, agent_commands)
    )
    return home
