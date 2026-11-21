from pathlib import Path

import pytest

from switch2_layers.orchestrator.claude_session_command import (
    ROOT_ENTRY_ACCESS,
    RootEntryAccess,
)
from switch2_layers.orchestrator.codex_session_command import (
    CODEX_DISABLED_FEATURES,
    CODEX_PERMISSION_PROFILE,
    CODEX_SKILLS_RELATIVE_DIR,
    DEFAULT_CODEX_MODEL,
    build_codex_command,
    build_codex_env,
    build_initialize_params,
    build_thread_config,
    build_thread_start_params,
    codex_permission_profile,
    codex_skills_dir,
    skills_disable_override,
    toml_basic_string,
)
from switch2_layers.run_directory import RunPaths

ROOT = Path("/home/user/runs/20261121-120000")
NATIVE = Path(
    "/home/user/.asdf/installs/nodejs/24.22.1/lib/node_modules/@openai/codex/node_modules/"
    "@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin"
)
# 2026-11-21 (codex-cli 0.171.0) に、シェルの ls frames/latest と notes/ への apply_patch が通り、cat run.jsonl と
# orchestrator/ の読み取りが拒まれることを確かめたプロファイル (直下の隠す項目は ROOT_ENTRY_ACCESS のすべて)
VERIFIED_WORKSPACE_ROOTS = {
    ".": "read",
    "notes": "write",
    "run.jsonl": "deny",
    "frames.jsonl": "deny",
    "surface.jsonl": "deny",
    "surface_requests.jsonl": "deny",
    "safety.jsonl": "deny",
    "operations.jsonl": "deny",
    "tasks.jsonl": "deny",
    "middle": "deny",
    "deep": "deny",
    "progress": "deny",
    "orchestrator": "deny",
}


def test_default_model_is_gpt_6_2_sol():
    assert DEFAULT_CODEX_MODEL == "gpt-6.2-sol"


def test_codex_skills_dir_is_agents_skills_of_the_run_directory():
    assert CODEX_SKILLS_RELATIVE_DIR == Path(".agents/skills")
    assert codex_skills_dir(RunPaths(ROOT)) == ROOT / ".agents" / "skills"


# --- コマンド ---


def test_command_disables_features_and_overrides_the_config():
    assert build_codex_command("codex", []) == [
        "codex",
        "app-server",
        "--disable",
        "plugins",
        "--disable",
        "apps",
        "--disable",
        "multi_agent",
        "--disable",
        "image_generation",
        "--disable",
        "goals",
        "--disable",
        "computer_use",
        "--disable",
        "browser_use",
        "--disable",
        "memories",
        "-c",
        'web_search="disabled"',
        "-c",
        "mcp_servers={}",
        "-c",
        "skills.bundled.enabled=false",
        "-c",
        "project_doc_max_bytes=0",
    ]


def test_disabled_features_are_the_verified_ones():
    assert CODEX_DISABLED_FEATURES == (
        "plugins",
        "apps",
        "multi_agent",
        "image_generation",
        "goals",
        "computer_use",
        "browser_use",
        "memories",
    )


def test_command_disables_the_given_skill_files_last():
    files = [
        Path("/repo/.agents/skills/a/SKILL.md"),
        Path("/home/u/.agents/skills/b/SKILL.md"),
    ]
    command = build_codex_command("/opt/codex", files)
    assert command[0] == "/opt/codex"
    assert command[-2:] == [
        "-c",
        (
            'skills.config=[{path="/repo/.agents/skills/a/SKILL.md", enabled=false}, '
            '{path="/home/u/.agents/skills/b/SKILL.md", enabled=false}]'
        ),
    ]


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/a/b", '"/a/b"'),
        ('/a/"q"', '"/a/\\"q\\""'),
        ("/a\\b", '"/a\\\\b"'),
        ("/日本語/スキル", '"/日本語/スキル"'),
        ("/a\nb\tc\x7f", '"/a\\u000Ab\\u0009c\\u007F"'),
    ],
)
def test_toml_basic_string_escapes_quotes_backslashes_and_controls(value, expected):
    assert toml_basic_string(value) == expected


def test_skills_disable_override_with_one_file():
    assert (
        skills_disable_override([Path("/x/SKILL.md")])
        == 'skills.config=[{path="/x/SKILL.md", enabled=false}]'
    )


# --- 環境変数 ---


def test_env_drops_api_keys_parent_session_variables_and_codex_api_key():
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "CODEX_HOME": "/home/user/.codex",
        "OPENAI_API_KEY": "openai",
        "TYPESAFE_API_KEY": "typesafe",
        "CODEX_API_KEY": "codex-key",
        "CLAUDECODE": "1",
        "ANTHROPIC_API_KEY": "kept-for-nothing",
    }
    env = build_codex_env(environ, ("TYPESAFE_API_KEY", "OPENAI_API_KEY"))
    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "CODEX_HOME": "/home/user/.codex",
        "ANTHROPIC_API_KEY": "kept-for-nothing",
    }
    # 元の辞書は変えない
    assert "OPENAI_API_KEY" in environ


# --- 権限プロファイルとスレッドの開始 ---


def test_permission_profile_reads_the_workspace_writes_notes_and_denies_the_records():
    assert codex_permission_profile(NATIVE) == {
        "filesystem": {
            ":minimal": "read",
            str(NATIVE): "read",
            ":workspace_roots": VERIFIED_WORKSPACE_ROOTS,
        }
    }


def test_every_hidden_root_entry_is_denied_and_readable_ones_are_not_denied():
    workspace = codex_permission_profile(NATIVE)["filesystem"][":workspace_roots"]
    for name, access in ROOT_ENTRY_ACCESS.items():
        if access is RootEntryAccess.READABLE:
            # 読める項目は "." の read のまま (notes/ だけは write にする)
            assert workspace.get(name, "read") in ("read", "write")
        else:
            assert workspace[name] == "deny"
    assert workspace["notes"] == "write"


def test_thread_config_defines_and_selects_the_profile():
    assert build_thread_config(NATIVE) == {
        "default_permissions": CODEX_PERMISSION_PROFILE,
        "permissions": {CODEX_PERMISSION_PROFILE: codex_permission_profile(NATIVE)},
    }


def test_thread_start_params():
    assert build_thread_start_params(ROOT, "gpt-6.2-sol", "指示", NATIVE) == {
        "cwd": str(ROOT),
        "model": "gpt-6.2-sol",
        "developerInstructions": "指示",
        "approvalPolicy": "never",
        "ephemeral": True,
        "permissions": CODEX_PERMISSION_PROFILE,
        "config": build_thread_config(NATIVE),
    }


def test_thread_start_params_reject_empty_instructions():
    with pytest.raises(ValueError):
        build_thread_start_params(ROOT, "gpt-6.2-sol", "", NATIVE)


def test_initialize_params_enable_the_experimental_api():
    params = build_initialize_params()
    assert params["capabilities"] == {"experimentalApi": True}
    assert set(params["clientInfo"]) == {"name", "version"}
