import hashlib
import tomllib
from pathlib import Path

import pytest

from switch2_harness.codex_command import (
    CODEX_CRITIC_EFFORT,
    CODEX_CRITIC_MODEL,
    CRITIC_PERMISSION_PROFILE,
    DISABLED_FEATURES,
    PLAYER_PERMISSION_PROFILE,
    build_codex_critic_args,
    build_codex_env,
    build_codex_exec_args,
    codex_auth_source,
    codex_config_toml,
    codex_home_dir,
    codex_rules_text,
    critic_permission_profile,
    is_kept_codex_env_name,
    player_permission_profile,
    toml_basic_string,
    toml_value,
)
from switch2_harness.workspace_pin import PINNED_WORKSPACE_ENV

NATIVE = Path("/opt/codex/vendor/bin")
ROOT = Path("/home/player/ch04_techniques/plays/game-1")
HOME = Path("/home/player/.cache/switch2-harness/codex-home/game-1-abc")


def test_constants():
    assert CODEX_CRITIC_MODEL == "gpt-6.2-sol"
    assert CODEX_CRITIC_EFFORT == "medium"
    assert PLAYER_PERMISSION_PROFILE == "switch2-harness-player"
    assert CRITIC_PERMISSION_PROFILE == "switch2-harness-critic"
    assert DISABLED_FEATURES == (
        "plugins",
        "apps",
        "image_generation",
        "goals",
        "computer_use",
        "browser_use",
        "memories",
    )


# --- TOML ---


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("abc", '"abc"'),
        ('a"b\\c', '"a\\"b\\\\c"'),
        ("一\n二\t", '"一\\u000A二\\u0009"'),
        ("\x7f", '"\\u007F"'),
    ],
)
def test_toml_basic_string(value, text):
    assert toml_basic_string(value) == text
    assert tomllib.loads(f"v = {text}")["v"] == value


def test_toml_value_writes_nested_tables_and_arrays():
    value = {
        "plain": True,
        "with space": 0,
        ":special": ["a", False],
        "empty": {},
        "nested": {"x-y_z": "w"},
    }
    text = toml_value(value)
    assert text == (
        '{ plain = true, "with space" = 0, ":special" = ["a", false], '
        'empty = {}, nested = { x-y_z = "w" } }'
    )
    assert tomllib.loads(f"v = {text}")["v"] == value


def test_toml_value_rejects_other_types():
    with pytest.raises(TypeError):
        toml_value(1.5)


# --- 場所と環境変数 ---


def test_codex_home_dir_uses_the_cache_directory_and_a_hash_of_the_workspace():
    digest = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    assert codex_home_dir(ROOT, {"HOME": "/home/player"}) == Path(
        f"/home/player/.cache/switch2-harness/codex-home/game-1-{digest}"
    )
    assert codex_home_dir(ROOT, {"HOME": "/h", "XDG_CACHE_HOME": "/c"}) == Path(
        f"/c/switch2-harness/codex-home/game-1-{digest}"
    )


def test_codex_home_dir_differs_for_workspaces_with_the_same_name():
    other = Path("/home/player/elsewhere/game-1")
    environ = {"HOME": "/home/player"}
    assert codex_home_dir(ROOT, environ) != codex_home_dir(other, environ)


def test_codex_home_dir_without_home_fails():
    with pytest.raises(ValueError):
        codex_home_dir(ROOT, {})


def test_codex_auth_source():
    assert codex_auth_source({"HOME": "/home/player"}) == Path(
        "/home/player/.codex/auth.json"
    )
    assert codex_auth_source({"HOME": "/h", "CODEX_HOME": "/codex"}) == Path(
        "/codex/auth.json"
    )
    with pytest.raises(ValueError):
        codex_auth_source({})


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("PATH", True),
        ("HOME", True),
        ("OPENAI_BASE_URL", True),
        ("OPENAI_API_KEY", False),
        ("CODEX_API_KEY", False),
        ("TYPESAFE_API_KEY", False),
        ("ANTHROPIC_API_KEY", False),
        ("CLAUDECODE", False),
        ("CLAUDE_CODE_SESSION_ID", False),
    ],
)
def test_is_kept_codex_env_name(name, kept):
    assert is_kept_codex_env_name(name) is kept


def test_build_codex_env_sets_the_codex_home_and_pins_the_workspace():
    base = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "secret",
        "CODEX_HOME": "/home/player/.codex",
        "CLAUDECODE": "1",
        PINNED_WORKSPACE_ENV: "/elsewhere",
    }
    env = build_codex_env(base, HOME, ROOT)
    assert env == {
        "PATH": "/usr/bin",
        "CODEX_HOME": str(HOME),
        PINNED_WORKSPACE_ENV: str(ROOT),
    }
    assert base["CODEX_HOME"] == "/home/player/.codex"


# --- 権限プロファイル ---


def test_player_profile_without_writable_files_reads_only():
    assert player_permission_profile(NATIVE, ["harness.json", "frames"], ()) == {
        "filesystem": {
            ":minimal": "read",
            str(NATIVE): "read",
            ":workspace_roots": {".": "read"},
        }
    }


def test_player_profile_with_writable_files_writes_the_root_and_reads_the_rest():
    entries = ["records", "notes.md", "AGENTS.md", "frames", "harness.json"]
    profile = player_permission_profile(NATIVE, entries, ("notes.md",))
    assert profile["filesystem"] == {
        ":minimal": "read",
        str(NATIVE): "read",
        ":workspace_roots": {
            ".": "write",
            "AGENTS.md": "read",
            "frames": "read",
            "harness.json": "read",
            "records": "read",
        },
    }


def test_player_profile_reads_only_entries_that_exist():
    # 無いパスを read にすると、サンドボックスが作るマウントの的が作業ディレクトリーに残るため書かない
    profile = player_permission_profile(NATIVE, ["notes.md"], ("notes.md",))
    assert profile["filesystem"][":workspace_roots"] == {".": "write"}


def test_critic_profile_reads_only():
    assert critic_permission_profile(NATIVE) == {
        "filesystem": {
            ":minimal": "read",
            str(NATIVE): "read",
            ":workspace_roots": {".": "read"},
        }
    }


# --- config.toml と rules ---


def test_codex_config_toml_parses_to_the_settings():
    text = codex_config_toml(NATIVE, ["notes.md", "frames"], ("notes.md",))
    assert text.startswith("# ")
    assert text.endswith("\n")
    assert tomllib.loads(text) == {
        "approval_policy": "never",
        "web_search": "disabled",
        "project_doc_max_bytes": 0,
        "mcp_servers": {},
        "features": {feature: False for feature in DISABLED_FEATURES},
        "agents": {"enabled": False},
        "skills": {"bundled": {"enabled": False}},
        "default_permissions": PLAYER_PERMISSION_PROFILE,
        "permissions": {
            PLAYER_PERMISSION_PROFILE: player_permission_profile(
                NATIVE, ["notes.md", "frames"], ("notes.md",)
            ),
            CRITIC_PERMISSION_PROFILE: critic_permission_profile(NATIVE),
        },
    }


def test_codex_config_toml_disables_the_given_skills():
    skills = [Path("/repo/.agents/skills/a/SKILL.md"), Path('/h/"q"/SKILL.md')]
    config = tomllib.loads(codex_config_toml(NATIVE, [], (), skills))
    assert config["skills"] == {
        "bundled": {"enabled": False},
        "config": [
            {"path": "/repo/.agents/skills/a/SKILL.md", "enabled": False},
            {"path": '/h/"q"/SKILL.md', "enabled": False},
        ],
    }


def test_codex_rules_text_allows_only_the_agent_commands():
    assert codex_rules_text(("look", "press", "status", "finish")) == (
        "# switch2-harness が run の始めに書き直す規則。エージェント用のサブコマンドだけを、サンドボックスの外で動かす\n"
        "prefix_rule(\n"
        '    pattern = ["uv", "run", "switch2-harness", ["look", "press", "status", "finish"]],\n'
        '    decision = "allow",\n'
        ")\n"
    )


# --- 起動引数 ---


def test_build_codex_exec_args_for_the_first_launch():
    assert build_codex_exec_args("始めて", "m", None, "指示\n2 行目") == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-m",
        "m",
        "-c",
        'developer_instructions="指示\\u000A2 行目"',
        "始めて",
    ]


def test_build_codex_exec_args_with_effort_and_resume():
    args = build_codex_exec_args("続けて", "m", "low", "指示", "thread-1")
    assert args == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "-m",
        "m",
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        'developer_instructions="指示"',
        "thread-1",
        "続けて",
    ]


def test_developer_instructions_value_is_valid_toml():
    instructions = '# 指示\n\n- `look` は "PNG" を \\ 表示する\n'
    args = build_codex_exec_args("p", "m", None, instructions)
    value = args[args.index("-c") + 1]
    assert tomllib.loads(value) == {"developer_instructions": instructions}


def test_build_codex_critic_args_attaches_images_and_reads_the_prompt_from_stdin():
    images = [Path("/w/frames/000001.png"), Path("/w/frames/000002.png")]
    assert build_codex_critic_args(images) == [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-rules",
        "-m",
        "gpt-6.2-sol",
        "-c",
        'model_reasoning_effort="medium"',
        "-c",
        'default_permissions="switch2-harness-critic"',
        "--disable",
        "shell_tool",
        "--disable",
        "unified_exec",
        "--disable",
        "view_image",
        "-i",
        "/w/frames/000001.png",
        "-i",
        "/w/frames/000002.png",
    ]


def test_build_codex_critic_args_without_images():
    args = build_codex_critic_args([])
    assert "-i" not in args
    assert args[-1] == "view_image"
