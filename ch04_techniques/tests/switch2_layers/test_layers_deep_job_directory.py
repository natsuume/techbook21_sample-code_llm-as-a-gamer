import json
import os
from pathlib import Path

import pytest

from switch2_layers.deep.deep_job_directory import (
    create_job_directory,
    find_previous_latest_frame_t,
    job_directory_name,
    next_job_number,
    parse_job_directory_name,
    read_job_file,
    run_directory_of_job,
)
from switch2_layers.deep.deep_types import (
    DeepJob,
    DeepTrigger,
    DocumentKey,
    DocumentKind,
)
from switch2_layers.run_directory import RunPaths

COMMON_CONTROLS = "# 操作説明 (共通)\n\n- A: unknown\n"
SCENE_CONTROLS = "# 操作説明 (町)\n\n- A: 話しかける\n"
STRATEGY = "# 大方針\n"


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    notes = root / "notes"
    (notes / "common").mkdir(parents=True)
    (notes / "map").mkdir()
    (root / "deep").mkdir()
    (notes / "common" / "controls.md").write_text(COMMON_CONTROLS, encoding="utf-8")
    (notes / "strategy.md").write_text(STRATEGY, encoding="utf-8")
    return root


def job(
    kind: DocumentKind,
    scene_id: str | None = None,
    latest: float | None = 8.0,
    previous: float | None = None,
    trigger: DeepTrigger = DeepTrigger.SURFACE,
) -> DeepJob:
    return DeepJob(
        kind=kind,
        scene_id=scene_id,
        trigger=trigger,
        requested_t=9.5,
        previous_latest_frame_t=previous,
        latest_frame_t=latest,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def job_directory_entries(job_dir: Path) -> list[str]:
    """ジョブのディレクトリーの中のファイルとディレクトリーの、ジョブのディレクトリーからの相対パスを返す。"""
    return sorted(path.relative_to(job_dir).as_posix() for path in job_dir.rglob("*"))


def notes_snapshot(root: Path) -> dict[str, bytes]:
    """notes/ の下の通常のファイルの中身を、notes/ からの相対パスごとに返す。"""
    notes = root / "notes"
    return {
        path.relative_to(notes).as_posix(): path.read_bytes()
        for path in notes.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


# --- 名前と連番 ---


def test_job_directory_name():
    assert job_directory_name(DocumentKind.CONTROLS, 1) == "controls-001"
    assert job_directory_name(DocumentKind.MAP, 42) == "map-042"
    assert job_directory_name(DocumentKind.TIPS, 1000) == "tips-1000"


@pytest.mark.parametrize(
    "name, expected",
    [
        ("tips-012", (DocumentKind.TIPS, 12)),
        ("strategy-1000", (DocumentKind.STRATEGY, 1000)),
        ("tips-1", None),
        ("tips-001x", None),
        ("unknown-001", None),
        ("tips", None),
    ],
)
def test_parse_job_directory_name(name, expected):
    assert parse_job_directory_name(name) == expected


def test_next_job_number_counts_per_kind(tmp_path):
    deep_dir = tmp_path / "deep"
    assert next_job_number(deep_dir, DocumentKind.CONTROLS) == 1
    deep_dir.mkdir()
    for name in ["controls-001", "controls-003", "tips-005", "notes"]:
        (deep_dir / name).mkdir()
    assert next_job_number(deep_dir, DocumentKind.CONTROLS) == 4
    assert next_job_number(deep_dir, DocumentKind.TIPS) == 6
    assert next_job_number(deep_dir, DocumentKind.MAP) == 1


# --- job.json (documents は notes/ からの相対パスだけ) ---


def test_scene_controls_job_lists_scene_and_common_documents(tmp_path):
    root = make_run(tmp_path)
    scene_dir = root / "notes" / "scenes" / "scene-001"
    scene_dir.mkdir(parents=True)
    (scene_dir / "controls.md").write_text(SCENE_CONTROLS, encoding="utf-8")
    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.CONTROLS, "scene-001", previous=3.0)
    )
    assert job_dir == root / "deep" / "controls-001"
    assert read_json(job_dir / "job.json") == {
        "kind": "controls",
        "scene_id": "scene-001",
        "trigger": "surface",
        "requested_t": 9.5,
        "previous_latest_frame_t": 3.0,
        "latest_frame_t": 8.0,
        "documents": ["scenes/scene-001/controls.md", "common/controls.md"],
    }


def test_job_directory_has_only_job_json(tmp_path):
    # 文書のコピーや作業用のディレクトリー (work/) を作らない
    root = make_run(tmp_path)
    paths = RunPaths(root)
    for kind, scene_id in [
        (DocumentKind.CONTROLS, "scene-001"),
        (DocumentKind.TIPS, None),
        (DocumentKind.STRATEGY, None),
        (DocumentKind.MAP, None),
    ]:
        job_dir = create_job_directory(paths, job(kind, scene_id))
        assert job_directory_entries(job_dir) == ["job.json"], kind
        assert not (job_dir / "work").exists()


def test_documents_have_no_sha256(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.STRATEGY))
    documents = read_json(job_dir / "job.json")["documents"]
    assert documents == ["strategy.md"]
    assert all(isinstance(note, str) for note in documents)
    assert "sha256" not in (job_dir / "job.json").read_text(encoding="utf-8")


def test_missing_documents_are_listed(tmp_path):
    # まだ無い文書も対象に並べる (deep が Write で作る)
    root = make_run(tmp_path)
    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.CONTROLS, "scene-002", latest=None)
    )
    value = read_json(job_dir / "job.json")
    assert value["documents"] == ["scenes/scene-002/controls.md", "common/controls.md"]
    assert value["latest_frame_t"] is None
    assert not (root / "notes" / "scenes").exists()


def test_common_tips_job_without_any_document(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.TIPS))
    assert job_dir.name == "tips-001"
    assert read_json(job_dir / "job.json")["documents"] == ["common/tips.md"]


def test_strategy_job(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.STRATEGY, trigger=DeepTrigger.AUTO)
    )
    value = read_json(job_dir / "job.json")
    assert value["scene_id"] is None
    assert value["trigger"] == "auto"
    assert value["documents"] == ["strategy.md"]


def test_scene_tips_job_lists_box_targets(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.TIPS, "scene-001"))
    assert read_json(job_dir / "job.json")["documents"] == [
        "scenes/scene-001/tips.md",
        "scenes/scene-001/box_targets.md",
        "common/tips.md",
    ]


def test_listed_documents_are_written_as_given(tmp_path):
    root = make_run(tmp_path)
    (root / "notes" / "map" / "town").mkdir()

    def fixed_listing(notes_dir: Path, key: DocumentKey) -> list[str]:
        # 無いファイルやディレクトリーを含んでも、中身を読まずにそのまま並べる
        return ["map/gone.md", "map/town", "map/places.md"]

    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.MAP), list_documents=fixed_listing
    )
    assert read_json(job_dir / "job.json")["documents"] == [
        "map/gone.md",
        "map/town",
        "map/places.md",
    ]
    assert job_directory_entries(job_dir) == ["job.json"]


def test_preparation_does_not_change_notes(tmp_path):
    root = make_run(tmp_path)
    (root / "notes" / "map" / "places.md").write_text("# 場所\n", encoding="utf-8")
    before = notes_snapshot(root)
    paths = RunPaths(root)
    for kind in DocumentKind:
        create_job_directory(paths, job(kind))
    create_job_directory(paths, job(DocumentKind.TIPS, "scene-001"))
    assert notes_snapshot(root) == before
    assert not (root / "notes" / "scenes").exists()


def test_failed_preparation_removes_partial_job_directory(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)

    def failing_listing(notes_dir: Path, key: DocumentKey) -> list[str]:
        raise OSError("listing failed")

    with pytest.raises(OSError, match="listing failed"):
        create_job_directory(paths, job(DocumentKind.CONTROLS), failing_listing)
    # 作りかけのディレクトリーを残さない
    assert list((root / "deep").iterdir()) == []
    # 消したディレクトリーの連番は、次のジョブがもう一度使う
    assert create_job_directory(paths, job(DocumentKind.CONTROLS)).name == (
        "controls-001"
    )


def test_map_job_skips_temporary_files(tmp_path):
    root = make_run(tmp_path)
    (root / "notes" / "map" / "places.md").write_text("# 場所\n", encoding="utf-8")
    (root / "notes" / "map" / ".places.md.x7k2q.tmp").write_text(
        "書きかけ", encoding="utf-8"
    )
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.MAP))
    assert read_json(job_dir / "job.json")["documents"] == ["map/places.md"]


def test_map_job_lists_all_map_files(tmp_path):
    root = make_run(tmp_path)
    empty_job = create_job_directory(RunPaths(root), job(DocumentKind.MAP))
    assert read_json(empty_job / "job.json")["documents"] == []
    (root / "notes" / "map" / "town").mkdir()
    (root / "notes" / "map" / "places.md").write_text("# 場所\n", encoding="utf-8")
    (root / "notes" / "map" / "town" / "house.md").write_text(
        "# 家\n", encoding="utf-8"
    )
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.MAP))
    assert job_dir.name == "map-002"
    assert read_json(job_dir / "job.json")["documents"] == [
        "map/places.md",
        "map/town/house.md",
    ]


def test_map_file_symlinked_to_outside_of_notes_is_not_listed(tmp_path):
    root = make_run(tmp_path)
    secret = tmp_path / "secret.md"
    secret.write_text("secret\n", encoding="utf-8")
    (root / "notes" / "map" / "places.md").write_text("# 場所\n", encoding="utf-8")
    (root / "notes" / "map" / "linked.md").symlink_to(secret)
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.MAP))
    assert read_json(job_dir / "job.json")["documents"] == ["map/places.md"]
    assert job_directory_entries(job_dir) == ["job.json"]


def test_numbers_are_per_kind(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    names = [
        create_job_directory(paths, job(kind)).name
        for kind in [
            DocumentKind.CONTROLS,
            DocumentKind.CONTROLS,
            DocumentKind.TIPS,
            DocumentKind.CONTROLS,
        ]
    ]
    assert names == ["controls-001", "controls-002", "tips-001", "controls-003"]


# --- job.json の読み込みと前回のジョブ ---


def test_read_job_file(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.ADVICE))
    assert read_job_file(job_dir)["kind"] == "advice"
    with pytest.raises(ValueError):
        read_job_file(tmp_path / "missing")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "job.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError):
        read_job_file(broken)


def test_find_previous_latest_frame_t_matches_the_same_document(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    scene_key = DocumentKey(DocumentKind.CONTROLS, "scene-001")
    common_key = DocumentKey(DocumentKind.CONTROLS, None)
    assert find_previous_latest_frame_t(paths, scene_key) is None
    create_job_directory(paths, job(DocumentKind.CONTROLS, "scene-001", latest=5.0))
    create_job_directory(paths, job(DocumentKind.CONTROLS, None, latest=7.0))
    create_job_directory(paths, job(DocumentKind.TIPS, "scene-001", latest=7.5))
    assert find_previous_latest_frame_t(paths, scene_key) == 5.0
    assert find_previous_latest_frame_t(paths, common_key) == 7.0
    create_job_directory(paths, job(DocumentKind.CONTROLS, "scene-001", latest=9.0))
    assert find_previous_latest_frame_t(paths, scene_key) == 9.0
    create_job_directory(paths, job(DocumentKind.CONTROLS, "scene-001", latest=None))
    assert find_previous_latest_frame_t(paths, scene_key) is None


# --- 実行ディレクトリー ---


def test_run_directory_of_job():
    run_dir = Path("/home/user/runs/20261115-120000")
    assert run_directory_of_job(run_dir / "deep" / "tips-003") == run_dir
    assert run_directory_of_job(Path("runs/x/deep/map-012")) == Path("runs/x")


@pytest.mark.parametrize(
    "job_dir",
    [
        Path("/home/user/runs/x/notes/tips-003"),  # 親が deep でない
        Path("/home/user/runs/x/deep/notes"),  # ジョブのディレクトリー名でない
        Path("/home/user/runs/x/deep/tips-003/work"),
    ],
)
def test_run_directory_of_job_rejects_other_paths(job_dir):
    with pytest.raises(ValueError):
        run_directory_of_job(job_dir)


def test_run_directory_of_job_does_not_resolve_links(tmp_path):
    real = tmp_path / "real" / "deep" / "advice-001"
    real.mkdir(parents=True)
    os.symlink(tmp_path / "real", tmp_path / "linked")
    linked = tmp_path / "linked" / "deep" / "advice-001"
    assert run_directory_of_job(linked) == tmp_path / "linked"
