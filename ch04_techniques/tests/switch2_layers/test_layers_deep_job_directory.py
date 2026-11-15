import hashlib
import json
from pathlib import Path

import pytest

from switch2_layers.deep.deep_job_directory import (
    create_job_directory,
    find_previous_latest_frame_t,
    job_directory_name,
    next_job_number,
    parse_job_directory_name,
    read_job_file,
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


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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


# --- job.json と work/ ---


def test_scene_controls_job_copies_scene_and_common_documents(tmp_path):
    root = make_run(tmp_path)
    scene_dir = root / "notes" / "scenes" / "scene-001"
    scene_dir.mkdir(parents=True)
    (scene_dir / "controls.md").write_text(SCENE_CONTROLS, encoding="utf-8")
    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.CONTROLS, "scene-001", previous=3.0)
    )
    assert job_dir == root / "deep" / "controls-001"
    work = job_dir / "work"
    assert (work / "scenes" / "scene-001" / "controls.md").read_text(
        encoding="utf-8"
    ) == SCENE_CONTROLS
    assert (work / "common" / "controls.md").read_text(
        encoding="utf-8"
    ) == COMMON_CONTROLS
    assert read_json(job_dir / "job.json") == {
        "kind": "controls",
        "scene_id": "scene-001",
        "trigger": "surface",
        "requested_t": 9.5,
        "previous_latest_frame_t": 3.0,
        "latest_frame_t": 8.0,
        "documents": [
            {"note": "scenes/scene-001/controls.md", "sha256": sha(SCENE_CONTROLS)},
            {"note": "common/controls.md", "sha256": sha(COMMON_CONTROLS)},
        ],
    }


def test_missing_document_is_marked_none_and_not_copied(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.CONTROLS, "scene-002", latest=None)
    )
    documents = read_json(job_dir / "job.json")["documents"]
    assert documents == [
        {"note": "scenes/scene-002/controls.md", "sha256": "none"},
        {"note": "common/controls.md", "sha256": sha(COMMON_CONTROLS)},
    ]
    assert not (job_dir / "work" / "scenes").exists()
    assert (job_dir / "work" / "common" / "controls.md").exists()
    assert read_json(job_dir / "job.json")["latest_frame_t"] is None


def test_common_tips_job_without_any_document(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.TIPS))
    assert job_dir.name == "tips-001"
    assert (job_dir / "work").is_dir()
    assert list((job_dir / "work").iterdir()) == []
    assert read_json(job_dir / "job.json")["documents"] == [
        {"note": "common/tips.md", "sha256": "none"}
    ]


def test_strategy_job(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.STRATEGY, trigger=DeepTrigger.AUTO)
    )
    value = read_json(job_dir / "job.json")
    assert value["scene_id"] is None
    assert value["trigger"] == "auto"
    assert value["documents"] == [{"note": "strategy.md", "sha256": sha(STRATEGY)}]
    assert (job_dir / "work" / "strategy.md").read_text(encoding="utf-8") == STRATEGY


def test_document_gone_after_listing_is_treated_as_missing(tmp_path):
    root = make_run(tmp_path)
    (root / "notes" / "map" / "places.md").write_text("# 場所\n", encoding="utf-8")

    def list_with_vanished_file(notes_dir: Path, key: DocumentKey) -> list[str]:
        # 列挙した時点ではあった map/gone.md が、読む前に消えた (publish-note の置き換えの途中など)
        return ["map/gone.md", "map/places.md"]

    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.MAP), list_documents=list_with_vanished_file
    )
    assert read_json(job_dir / "job.json")["documents"] == [
        {"note": "map/gone.md", "sha256": "none"},
        {"note": "map/places.md", "sha256": sha("# 場所\n")},
    ]
    assert not (job_dir / "work" / "map" / "gone.md").exists()
    assert (job_dir / "work" / "map" / "places.md").exists()


def test_listed_path_that_is_not_a_regular_file_is_treated_as_missing(tmp_path):
    root = make_run(tmp_path)
    (root / "notes" / "map" / "town").mkdir()

    def list_directory(notes_dir: Path, key: DocumentKey) -> list[str]:
        return ["map/town"]

    job_dir = create_job_directory(
        RunPaths(root), job(DocumentKind.MAP), list_documents=list_directory
    )
    assert read_json(job_dir / "job.json")["documents"] == [
        {"note": "map/town", "sha256": "none"}
    ]


def test_map_job_does_not_copy_temporary_files(tmp_path):
    root = make_run(tmp_path)
    (root / "notes" / "map" / "places.md").write_text("# 場所\n", encoding="utf-8")
    (root / "notes" / "map" / ".places.md.x7k2q.tmp").write_text(
        "書きかけ", encoding="utf-8"
    )
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.MAP))
    assert [doc["note"] for doc in read_json(job_dir / "job.json")["documents"]] == [
        "map/places.md"
    ]
    assert sorted(p.name for p in (job_dir / "work" / "map").iterdir()) == ["places.md"]


def test_map_job_copies_all_map_files_and_always_has_work_map(tmp_path):
    root = make_run(tmp_path)
    empty_job = create_job_directory(RunPaths(root), job(DocumentKind.MAP))
    assert (empty_job / "work" / "map").is_dir()
    assert read_json(empty_job / "job.json")["documents"] == []
    (root / "notes" / "map" / "town").mkdir()
    (root / "notes" / "map" / "places.md").write_text("# 場所\n", encoding="utf-8")
    (root / "notes" / "map" / "town" / "house.md").write_text(
        "# 家\n", encoding="utf-8"
    )
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.MAP))
    assert job_dir.name == "map-002"
    assert (job_dir / "work" / "map" / "town" / "house.md").read_text(
        encoding="utf-8"
    ) == "# 家\n"
    assert [doc["note"] for doc in read_json(job_dir / "job.json")["documents"]] == [
        "map/places.md",
        "map/town/house.md",
    ]


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


def test_copies_do_not_follow_later_edits(tmp_path):
    root = make_run(tmp_path)
    job_dir = create_job_directory(RunPaths(root), job(DocumentKind.STRATEGY))
    (root / "notes" / "strategy.md").write_text(
        "# 大方針\n\n変わった\n", encoding="utf-8"
    )
    assert (job_dir / "work" / "strategy.md").read_text(encoding="utf-8") == STRATEGY


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
