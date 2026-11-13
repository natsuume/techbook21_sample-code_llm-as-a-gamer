from pathlib import Path

import pytest

from run_dashboard.run_directory import (
    ADVISOR_FILE_TOP_DIR_PATTERN,
    ADVISOR_RUN_DIR_PATTERN,
    FILE_TOP_DIRS,
    AdvisorRunDir,
    find_run_dir,
    list_advisor_files,
    list_advisor_run_dirs,
    list_run_names,
    resolve_run_file,
)


@pytest.fixture
def runs_dir(tmp_path) -> Path:
    """実行のディレクトリー 3 つと、一覧に入れないもの (ファイル、. 始まり、シンボリックリンク) を置いた runs_dir。"""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    for name in ["20261112-100000", "20261113-235950", "20261110-090000", ".hidden"]:
        (runs_dir / name).mkdir()
    (runs_dir / "20261114-000000").write_text("not a directory", encoding="utf-8")
    outside = tmp_path / "outside-run"
    outside.mkdir()
    (runs_dir / "20261115-000000").symlink_to(outside, target_is_directory=True)
    return runs_dir


# --- list_run_names / find_run_dir ---


def test_list_run_names_is_newest_first_and_skips_files_dot_dirs_and_symlinks(runs_dir):
    assert list_run_names(runs_dir) == [
        "20261113-235950",
        "20261112-100000",
        "20261110-090000",
    ]


def test_find_run_dir_returns_path_of_listed_run(runs_dir):
    assert find_run_dir(runs_dir, "20261112-100000") == runs_dir / "20261112-100000"


@pytest.mark.parametrize(
    "run",
    [
        "..",
        ".",
        "",
        "a/b",
        "20261112-100000/..",
        "../runs/20261112-100000",
        "20261199-000000",
        ".hidden",
        "20261114-000000",
        "20261115-000000",
    ],
)
def test_find_run_dir_rejects_names_not_in_list(runs_dir, run):
    assert find_run_dir(runs_dir, run) is None


# --- ADVISOR_RUN_DIR_PATTERN / list_advisor_run_dirs ---


def test_advisor_run_dir_pattern_matches_role_and_index():
    match = ADVISOR_RUN_DIR_PATTERN.fullmatch("advisor-strategy-001")
    assert match is not None
    assert match.groups() == ("strategy", "001")
    assert ADVISOR_RUN_DIR_PATTERN.fullmatch("advisor-tips-1000") is not None
    for name in ["advisor-001", "advisor-unknown-001", "advisor-strategy-01"]:
        assert ADVISOR_RUN_DIR_PATTERN.fullmatch(name) is None


def test_list_advisor_run_dirs_orders_by_role_then_index_and_skips_old_names(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in [
        "advisor-tips-001",
        "advisor-strategy-1000",
        "advisor-strategy-002",
        "advisor-strategy-999",
        "advisor-strategy-001",
        "advisor-controls-001",
        # 一覧に入れないもの: 役割を持たない形、知らない役割、連番が 3 桁に満たない、ほかのディレクトリー
        "advisor-001",
        "advisor-unknown-001",
        "advisor-strategy-01",
        "thumbs",
    ]:
        (run_dir / name).mkdir()
    # ファイルは入れない
    (run_dir / "advisor-advice-001").write_text("not a directory", encoding="utf-8")
    assert list_advisor_run_dirs(run_dir) == [
        AdvisorRunDir(
            "advisor-controls-001", "controls", 1, run_dir / "advisor-controls-001"
        ),
        AdvisorRunDir(
            "advisor-strategy-001", "strategy", 1, run_dir / "advisor-strategy-001"
        ),
        AdvisorRunDir(
            "advisor-strategy-002", "strategy", 2, run_dir / "advisor-strategy-002"
        ),
        # 連番は数として並べる (999 の次が 1000)
        AdvisorRunDir(
            "advisor-strategy-999", "strategy", 999, run_dir / "advisor-strategy-999"
        ),
        AdvisorRunDir(
            "advisor-strategy-1000", "strategy", 1000, run_dir / "advisor-strategy-1000"
        ),
        AdvisorRunDir("advisor-tips-001", "tips", 1, run_dir / "advisor-tips-001"),
    ]


def test_list_advisor_run_dirs_is_empty_without_advisor_dirs(tmp_path):
    assert list_advisor_run_dirs(tmp_path) == []


# --- list_advisor_files ---


def test_list_advisor_files_lists_files_recursively_sorted_by_path(tmp_path):
    path = tmp_path / "advisor-strategy-001"
    (path / "sub").mkdir(parents=True)
    (path / "stream.jsonl").write_bytes(b"12345")
    (path / "context.md").write_bytes(b"abc")
    (path / "sub" / "a.txt").write_bytes(b"x")
    advisor_dir = AdvisorRunDir("advisor-strategy-001", "strategy", 1, path)
    assert list_advisor_files(advisor_dir) == [
        {"path": "advisor-strategy-001/context.md", "bytes": 3},
        {"path": "advisor-strategy-001/stream.jsonl", "bytes": 5},
        {"path": "advisor-strategy-001/sub/a.txt", "bytes": 1},
    ]


def test_list_advisor_files_is_empty_for_missing_dir(tmp_path):
    advisor_dir = AdvisorRunDir(
        "advisor-tips-001", "tips", 1, tmp_path / "advisor-tips-001"
    )
    assert list_advisor_files(advisor_dir) == []


# --- resolve_run_file ---


def test_file_top_dirs_and_advisor_pattern():
    assert FILE_TOP_DIRS == ("thumbs", "frames")
    assert ADVISOR_FILE_TOP_DIR_PATTERN.fullmatch("advisor-strategy-001") is not None
    assert ADVISOR_FILE_TOP_DIR_PATTERN.fullmatch("advisor-001") is not None
    assert ADVISOR_FILE_TOP_DIR_PATTERN.fullmatch("advisor-") is None


@pytest.fixture
def run_dir(tmp_path) -> Path:
    """files の API で返してよいファイルと、返さないファイルを置いた実行のディレクトリー。"""
    run_dir = tmp_path / "runs" / "20261113-235950"
    for relative in [
        "thumbs/x.jpg",
        "frames/x.jpg",
        "advisor-strategy-001/context.md",
        "advisor-strategy-001/sub/deep.txt",
        "advisor-001/result.json",
        "run.jsonl",
        "game_notes/x.md",
    ]:
        path = run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
    (run_dir / "thumbs" / "sub").mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    # 実行のディレクトリーの外を指すシンボリックリンクと、中を指すシンボリックリンク
    (run_dir / "thumbs" / "outside.jpg").symlink_to(outside)
    (run_dir / "thumbs" / "inside.jpg").symlink_to(run_dir / "frames" / "x.jpg")
    return run_dir


@pytest.mark.parametrize(
    "relative",
    [
        "thumbs/x.jpg",
        "frames/x.jpg",
        "advisor-strategy-001/context.md",
        "advisor-strategy-001/sub/deep.txt",
        "advisor-001/result.json",
    ],
)
def test_resolve_run_file_allows_files_under_allowed_dirs(run_dir, relative):
    assert resolve_run_file(run_dir, relative) == (run_dir / relative).resolve()


def test_resolve_run_file_follows_symlink_that_stays_inside(run_dir):
    assert (
        resolve_run_file(run_dir, "thumbs/inside.jpg")
        == (run_dir / "frames" / "x.jpg").resolve()
    )


@pytest.mark.parametrize(
    "relative",
    [
        # 空、絶対パス、\ を含む、NUL を含む
        "",
        "/etc/passwd",
        "\\thumbs\\x.jpg",
        "thumbs\\x.jpg",
        "thumbs/x.jpg\x00",
        # .. や . や空の部分
        "../run.jsonl",
        "thumbs/../run.jsonl",
        "thumbs/../../secret.txt",
        "./thumbs/x.jpg",
        "thumbs/./x.jpg",
        "thumbs//x.jpg",
        "thumbs/x.jpg/",
        # 許していない最初の部分
        "run.jsonl",
        "game_notes/x.md",
        "advisor-/x.md",
        # ディレクトリーそのもの
        "thumbs",
        "advisor-strategy-001",
        "thumbs/sub",
        # 無いファイル
        "thumbs/missing.jpg",
        # 外を指すシンボリックリンク
        "thumbs/outside.jpg",
    ],
)
def test_resolve_run_file_rejects_other_paths(run_dir, relative):
    assert resolve_run_file(run_dir, relative) is None
