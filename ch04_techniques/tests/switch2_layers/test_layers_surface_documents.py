import json
from pathlib import Path

import pytest

from switch2_layers.notes.surface_documents import is_blank, read_surface_documents
from switch2_layers.surface.surface_types import SurfaceDocuments

COMMON_CONTROLS = "# 操作説明 (共通)\n\n- A: unknown\n"
COMMON_TIPS = "# 攻略情報 (共通)\n"
ADVICE = "# 助言\n\n扉の前で止まっている\n"
SCENE_CONTROLS = "# 操作説明 (町)\n\n- A: 話しかける\n"
SCENE_TIPS = "# 攻略情報 (町)\n\n宿屋は北\n"
# 場面のキューの状態 (tasks.json の tasks。キューの順)
SCENE_TASK_ITEMS = [
    {
        "id": "task-003",
        "text": "宿屋に入っている",
        "added_t": 1.0,
        "active_s": 2.5,
        "cancel_requested_t": None,
        "destination": {"place": "町", "tile": {"x": 3, "y": 4}},
    },
    {
        "id": "task-004",
        "text": "主人と話している",
        "added_t": 1.5,
        "active_s": 2.0,
        "cancel_requested_t": None,
        "destination": None,
    },
]


def make_notes(tmp_path: Path) -> Path:
    """共通の文書と助言だけがある notes/ を作る (パッケージのコードは使わない)。"""
    notes = tmp_path / "notes"
    (notes / "common").mkdir(parents=True)
    (notes / "common" / "controls.md").write_text(COMMON_CONTROLS, encoding="utf-8")
    (notes / "common" / "tips.md").write_text(COMMON_TIPS, encoding="utf-8")
    (notes / "advice.md").write_text(ADVICE, encoding="utf-8")
    return notes


def write_scene(notes: Path, scene_id: str, files: dict[str, str]) -> None:
    scene_dir = notes / "scenes" / scene_id
    scene_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (scene_dir / name).write_text(text, encoding="utf-8")


def write_tasks_json(notes: Path, scene_id: str, items: list[dict]) -> None:
    """場面の tasks.json に、items を tasks に持つキューの状態を書く。"""
    write_scene(
        notes,
        scene_id,
        {"tasks.json": json.dumps({"t": 3.0, "tasks": items}, ensure_ascii=False)},
    )


@pytest.mark.parametrize(
    "text, expected", [("", True), (" \n\t", True), ("# x", False)]
)
def test_is_blank(text, expected):
    assert is_blank(text) is expected


def test_no_scene_uses_common_documents(tmp_path):
    notes = make_notes(tmp_path)
    assert read_surface_documents(notes, None) == SurfaceDocuments(
        tasks="",
        task_id=None,
        tips=COMMON_TIPS,
        controls=COMMON_CONTROLS,
        advice=ADVICE,
        scene_id=None,
        uses_common_controls=True,
        uses_common_tips=True,
    )


def test_scene_without_scene_documents_uses_common_documents(tmp_path):
    notes = make_notes(tmp_path)
    assert read_surface_documents(notes, "scene-001") == SurfaceDocuments(
        tasks="",
        task_id=None,
        tips=COMMON_TIPS,
        controls=COMMON_CONTROLS,
        advice=ADVICE,
        scene_id="scene-001",
        uses_common_controls=True,
        uses_common_tips=True,
    )


@pytest.mark.parametrize("empty_text", ["", "  \n\n"])
def test_empty_scene_documents_fall_back_to_common(tmp_path, empty_text):
    notes = make_notes(tmp_path)
    write_scene(notes, "scene-001", {"controls.md": empty_text, "tips.md": empty_text})
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == COMMON_CONTROLS
    assert documents.tips == COMMON_TIPS
    assert documents.uses_common_controls is True
    assert documents.uses_common_tips is True


def test_scene_documents_are_used_when_present(tmp_path):
    notes = make_notes(tmp_path)
    write_scene(
        notes, "scene-001", {"controls.md": SCENE_CONTROLS, "tips.md": SCENE_TIPS}
    )
    write_tasks_json(notes, "scene-001", SCENE_TASK_ITEMS)
    assert read_surface_documents(notes, "scene-001") == SurfaceDocuments(
        tasks="宿屋に入っている",
        task_id="task-003",
        tips=SCENE_TIPS,
        controls=SCENE_CONTROLS,
        advice=ADVICE,
        scene_id="scene-001",
        uses_common_controls=False,
        uses_common_tips=False,
    )


def test_each_kind_falls_back_independently(tmp_path):
    notes = make_notes(tmp_path)
    write_scene(notes, "scene-001", {"controls.md": SCENE_CONTROLS})
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == SCENE_CONTROLS
    assert documents.uses_common_controls is False
    assert documents.tips == COMMON_TIPS
    assert documents.uses_common_tips is True


def test_documents_of_another_scene_are_not_used(tmp_path):
    notes = make_notes(tmp_path)
    write_scene(notes, "scene-002", {"controls.md": SCENE_CONTROLS})
    write_tasks_json(notes, "scene-002", SCENE_TASK_ITEMS)
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == COMMON_CONTROLS
    assert documents.tasks == ""
    assert documents.task_id is None


def test_invalid_scene_id_is_treated_as_no_scene(tmp_path):
    notes = make_notes(tmp_path)
    documents = read_surface_documents(notes, "../common")
    assert documents.scene_id is None
    assert documents.uses_common_controls is True
    assert documents.tasks == ""
    assert documents.task_id is None


def test_missing_documents_are_empty(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    assert read_surface_documents(notes, None) == SurfaceDocuments(
        tasks="",
        task_id=None,
        tips="",
        controls="",
        advice="",
        scene_id=None,
        uses_common_controls=True,
        uses_common_tips=True,
    )


# --- 今実行中のタスク (場面のキューの先頭) ---


def test_head_task_of_the_scene_queue(tmp_path):
    notes = make_notes(tmp_path)
    write_tasks_json(notes, "scene-001", SCENE_TASK_ITEMS)
    documents = read_surface_documents(notes, "scene-001")
    # キューの先頭のタスクの文と ID だけ
    assert documents.tasks == "宿屋に入っている"
    assert documents.task_id == "task-003"


def test_empty_scene_queue_has_no_head_task(tmp_path):
    notes = make_notes(tmp_path)
    write_tasks_json(notes, "scene-001", [])
    documents = read_surface_documents(notes, "scene-001")
    assert documents.tasks == ""
    assert documents.task_id is None


def test_tasks_markdown_is_not_read(tmp_path):
    # 読むのはキューの状態 (tasks.json) で、一覧 (tasks.md) は読まない
    notes = make_notes(tmp_path)
    write_scene(notes, "scene-001", {"tasks.md": "- 宿屋に入っている\n"})
    documents = read_surface_documents(notes, "scene-001")
    assert documents.tasks == ""
    assert documents.task_id is None


# --- notes/ の外を指す文書 ---

SECRET = "text outside of notes\n"


def write_secret(tmp_path: Path) -> Path:
    """notes/ の外の、surface に読ませたくないファイルを作って返す。"""
    secret = tmp_path / "secret.env"
    secret.write_text(SECRET, encoding="utf-8")
    return secret


def test_scene_document_symlinked_to_outside_falls_back_to_common(tmp_path):
    notes = make_notes(tmp_path)
    secret = write_secret(tmp_path)
    write_scene(notes, "scene-001", {"tips.md": SCENE_TIPS})
    (notes / "scenes" / "scene-001" / "controls.md").symlink_to(secret)
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == COMMON_CONTROLS
    assert documents.uses_common_controls is True
    assert documents.tips == SCENE_TIPS


def test_documents_symlinked_to_outside_are_empty(tmp_path):
    notes = make_notes(tmp_path)
    secret = write_secret(tmp_path)
    (notes / "advice.md").unlink()
    (notes / "advice.md").symlink_to(secret)
    (notes / "common" / "tips.md").unlink()
    (notes / "common" / "tips.md").symlink_to(secret)
    documents = read_surface_documents(notes, "scene-001")
    assert documents.advice == ""
    assert documents.tips == ""


def test_scene_directory_symlinked_to_outside_is_not_read(tmp_path):
    notes = make_notes(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "controls.md").write_text(SECRET, encoding="utf-8")
    (notes / "scenes").mkdir()
    (notes / "scenes" / "scene-001").symlink_to(outside)
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == COMMON_CONTROLS


# --- ゲームの性質 (notes/game.md。場面に依らない) ---

GAME = "# ゲームの性質\n\n## ジャンル\nRPG (推測)\n"


@pytest.mark.parametrize("scene_id", [None, "scene-001", "../common"])
def test_game_document_is_read_for_any_scene(tmp_path, scene_id):
    notes = make_notes(tmp_path)
    (notes / "game.md").write_text(GAME, encoding="utf-8")
    write_scene(notes, "scene-001", {"controls.md": SCENE_CONTROLS})
    documents = read_surface_documents(notes, scene_id)
    assert documents.game == GAME


def test_game_document_in_scene_directory_is_not_used(tmp_path):
    # 場面ごとの文書は無い。scenes/<場面ID>/game.md や common/game.md があっても読まない
    notes = make_notes(tmp_path)
    write_scene(notes, "scene-001", {"game.md": GAME})
    (notes / "common" / "game.md").write_text(GAME, encoding="utf-8")
    assert read_surface_documents(notes, "scene-001").game == ""


def test_missing_game_document_is_empty(tmp_path):
    notes = make_notes(tmp_path)
    documents = read_surface_documents(notes, "scene-001")
    assert documents.game == ""
    # ほかの文書は変わらない
    assert documents.advice == ADVICE
    assert documents.controls == COMMON_CONTROLS


def test_blank_game_document_is_passed_as_it_is(tmp_path):
    # surface にはほかの文書と同じく読んだままを渡す (空白だけの文書を「(無い)」にするのは middle)
    notes = make_notes(tmp_path)
    (notes / "game.md").write_text("  \n\n", encoding="utf-8")
    assert read_surface_documents(notes, None).game == "  \n\n"


def test_unreadable_game_document_is_empty(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "game.md").write_bytes(b"\xff\xfe not utf-8")
    assert read_surface_documents(notes, None).game == ""
    (notes / "game.md").unlink()
    (notes / "game.md").mkdir()
    assert read_surface_documents(notes, None).game == ""


def test_game_document_symlinked_to_outside_is_empty(tmp_path):
    notes = make_notes(tmp_path)
    secret = write_secret(tmp_path)
    (notes / "game.md").symlink_to(secret)
    assert read_surface_documents(notes, None).game == ""


def test_game_document_symlinked_inside_notes_is_read(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "game-real.md").write_text(GAME, encoding="utf-8")
    (notes / "game.md").symlink_to(notes / "game-real.md")
    assert read_surface_documents(notes, None).game == GAME
