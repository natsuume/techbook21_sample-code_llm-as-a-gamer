from pathlib import Path

from switch2_layers.orchestrator.codex_skill_paths import (
    ADMIN_SKILLS_DIR,
    find_codex_skill_files_to_disable,
    skill_files_in,
    user_skill_dirs,
)


def write_skill(skills_dir: Path, name: str) -> Path:
    path = skills_dir / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")
    return path


def test_skill_files_in_lists_skill_files_by_name_and_skips_hidden_directories(
    tmp_path,
):
    skills = tmp_path / ".agents" / "skills"
    second = write_skill(skills, "b-skill")
    first = write_skill(skills, "a-skill")
    write_skill(skills / ".system", "imagegen")
    (skills / "no-skill-file").mkdir()
    (skills / "file.txt").write_text("x", encoding="utf-8")
    assert skill_files_in(skills) == [first, second]


def test_skill_files_in_follows_a_linked_skill_directory(tmp_path):
    real = write_skill(tmp_path / "elsewhere", "linked")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "linked").symlink_to(real.parent, target_is_directory=True)
    assert skill_files_in(skills) == [skills / "linked" / "SKILL.md"]


def test_skill_files_in_of_a_missing_directory_is_empty(tmp_path):
    assert skill_files_in(tmp_path / "missing") == []


def test_user_skill_dirs_use_home_and_codex_home():
    assert user_skill_dirs({"HOME": "/home/u", "CODEX_HOME": "/srv/codex"}) == [
        Path("/home/u/.agents/skills"),
        Path("/srv/codex/skills"),
        ADMIN_SKILLS_DIR,
    ]


def test_user_skill_dirs_default_codex_home_is_dot_codex():
    assert user_skill_dirs({"HOME": "/home/u"}) == [
        Path("/home/u/.agents/skills"),
        Path("/home/u/.codex/skills"),
        ADMIN_SKILLS_DIR,
    ]


def test_user_skill_dirs_without_home():
    assert user_skill_dirs({}) == [ADMIN_SKILLS_DIR]


def test_parent_and_user_skills_are_collected_but_not_the_run_directory_ones(tmp_path):
    repo = tmp_path / "repo"
    run_dir = repo / "runs" / "20261121-120000"
    own = write_skill(run_dir / ".agents" / "skills", "switch-select-user")
    near = write_skill(repo / "runs" / ".agents" / "skills", "near")
    far = write_skill(repo / ".agents" / "skills", "far")
    home = tmp_path / "home"
    user = write_skill(home / ".agents" / "skills", "user-skill")
    codex_user = write_skill(home / ".codex" / "skills", "codex-user-skill")
    write_skill(home / ".codex" / "skills" / ".system", "imagegen")
    found = find_codex_skill_files_to_disable(run_dir, {"HOME": str(home)})
    assert found[:2] == (near, far)
    assert user in found
    assert codex_user in found
    assert own not in found
    assert all(".system" not in path.parts for path in found)


def test_a_skill_reached_twice_is_listed_once(tmp_path):
    home = tmp_path / "home"
    run_dir = home / "runs" / "20261121-120000"
    run_dir.mkdir(parents=True)
    skill = write_skill(home / ".agents" / "skills", "both")
    found = find_codex_skill_files_to_disable(run_dir, {"HOME": str(home)})
    assert found.count(skill) == 1
