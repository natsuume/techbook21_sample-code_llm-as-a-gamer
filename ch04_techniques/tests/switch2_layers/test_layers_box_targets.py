from pathlib import Path

import pytest

from switch2_layers.surface.box_targets import (
    BOX_TARGETS_HEADING,
    DEFAULT_BOX_TARGETS,
    MAX_BOX_TARGETS,
    BoxTarget,
    common_box_targets_note,
    initial_box_targets_text,
    parse_box_targets,
    read_box_targets,
    scene_box_targets_note,
)


def write_note(notes_dir: Path, relative: str, text: str | bytes) -> None:
    path = notes_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def names(targets: tuple[BoxTarget, ...]) -> list[str]:
    return [target.name for target in targets]


# --- 定数と既定の対象 ---


def test_constants_follow_the_issue():
    assert MAX_BOX_TARGETS == 8
    assert names(DEFAULT_BOX_TARGETS) == ["自機", "敵", "敵の攻撃", "目標地点"]
    assert all(target.description for target in DEFAULT_BOX_TARGETS)


def test_note_paths():
    assert common_box_targets_note() == "common/box_targets.md"
    assert scene_box_targets_note("scene-001") == "scenes/scene-001/box_targets.md"


@pytest.mark.parametrize("scene_id", ["../x", "scene-1", ""])
def test_scene_note_path_rejects_invalid_scene_id(scene_id):
    with pytest.raises(ValueError):
        scene_box_targets_note(scene_id)


def test_initial_text_lists_default_targets_and_reads_back():
    text = initial_box_targets_text()
    lines = text.splitlines()
    assert lines[0] == BOX_TARGETS_HEADING
    assert lines[1] == ""
    assert lines[2:] == [
        f"- {target.name}: {target.description}" for target in DEFAULT_BOX_TARGETS
    ]
    assert text.endswith("\n")
    assert parse_box_targets(text) == DEFAULT_BOX_TARGETS


# --- 書式 ---


def test_parse_reads_name_and_description():
    text = "# box で取るもの\n\n- 自機: 赤い帽子の人\n- 扉:  木の扉  \n"
    assert parse_box_targets(text) == (
        BoxTarget("自機", "赤い帽子の人"),
        BoxTarget("扉", "木の扉"),
    )


def test_parse_ignores_lines_that_do_not_match():
    lines = [
        "# 見出し",
        "",
        "自機: 行頭に - が無い",
        "* 敵: 記号が違う",
        "  - 字下げ: 字下げした行",
        "- 区切りが無い",
        "- : 名前が空",
        "- 説明が空:",
        "- 鍵: 黄色い鍵",
        "-宝箱: - の後に空白が無い",
    ]
    assert parse_box_targets("\n".join(lines)) == (BoxTarget("鍵", "黄色い鍵"),)


def test_parse_ignores_second_line_with_the_same_name():
    text = "- 自機: 1 つ目\n- 自機: 2 つ目\n- 敵: スライム\n"
    assert parse_box_targets(text) == (
        BoxTarget("自機", "1 つ目"),
        BoxTarget("敵", "スライム"),
    )


def test_parse_uses_at_most_eight_targets_from_the_top():
    text = "".join(f"- 対象{i}: 説明{i}\n" for i in range(1, 11))
    targets = parse_box_targets(text)
    assert len(targets) == MAX_BOX_TARGETS
    assert names(targets) == [f"対象{i}" for i in range(1, 9)]


def test_parse_counts_only_matching_lines_toward_the_limit():
    lines = ["- 対象1: 説明"] + ["書式に合わない行"] * 10
    lines += [f"- 対象{i}: 説明" for i in range(2, 10)]
    targets = parse_box_targets("\n".join(lines))
    assert names(targets) == [f"対象{i}" for i in range(1, 9)]


def test_parse_of_text_without_targets_is_empty():
    assert parse_box_targets("") == ()
    assert parse_box_targets("# box で取るもの\n\n") == ()


def test_parse_accepts_crlf_line_endings():
    assert parse_box_targets("- 自機: 勇者\r\n- 敵: 魔物\r\n") == (
        BoxTarget("自機", "勇者"),
        BoxTarget("敵", "魔物"),
    )


# --- 読む順 (場面・共通・固定の 4 つ) ---


def test_scene_targets_are_used_when_present(tmp_path):
    write_note(tmp_path, "common/box_targets.md", "- 共通: 共通の対象\n")
    write_note(tmp_path, "scenes/scene-002/box_targets.md", "- 扉: 場面の対象\n")
    assert read_box_targets(tmp_path, "scene-002") == (BoxTarget("扉", "場面の対象"),)


@pytest.mark.parametrize(
    "scene_text", [None, "", "# box で取るもの\n\n", "書式に合わない行だけ\n"]
)
def test_common_targets_are_used_when_scene_file_is_missing_or_empty(
    tmp_path, scene_text
):
    write_note(tmp_path, "common/box_targets.md", "- 共通: 共通の対象\n")
    if scene_text is not None:
        write_note(tmp_path, "scenes/scene-002/box_targets.md", scene_text)
    assert read_box_targets(tmp_path, "scene-002") == (BoxTarget("共通", "共通の対象"),)


def test_common_targets_are_used_without_scene(tmp_path):
    write_note(tmp_path, "common/box_targets.md", "- 共通: 共通の対象\n")
    assert read_box_targets(tmp_path, None) == (BoxTarget("共通", "共通の対象"),)


def test_invalid_scene_id_is_treated_as_no_scene(tmp_path):
    write_note(tmp_path, "common/box_targets.md", "- 共通: 共通の対象\n")
    write_note(tmp_path, "x/box_targets.md", "- 外: notes の外を指す対象\n")
    assert read_box_targets(tmp_path, "../x") == (BoxTarget("共通", "共通の対象"),)


@pytest.mark.parametrize("common_text", [None, "", "# box で取るもの\n"])
def test_default_targets_are_used_when_common_file_is_missing_or_empty(
    tmp_path, common_text
):
    if common_text is not None:
        write_note(tmp_path, "common/box_targets.md", common_text)
    assert read_box_targets(tmp_path, "scene-001") == DEFAULT_BOX_TARGETS
    assert read_box_targets(tmp_path, None) == DEFAULT_BOX_TARGETS


def test_unreadable_files_are_treated_as_empty(tmp_path):
    write_note(tmp_path, "scenes/scene-001/box_targets.md", b"\xff\xfe- \x80: x\n")
    write_note(tmp_path, "common/box_targets.md", "- 共通: 共通の対象\n")
    assert read_box_targets(tmp_path, "scene-001") == (BoxTarget("共通", "共通の対象"),)


def test_symlink_to_outside_of_notes_is_treated_as_missing(tmp_path):
    # notes/ の外を指すシンボリックリンクの文書は読まず、無い文書として扱う (notes.note_reader)
    notes_dir = tmp_path / "notes"
    outside = tmp_path / "outside.md"
    outside.write_text("- 外: notes の外の対象\n", encoding="utf-8")
    write_note(notes_dir, "common/box_targets.md", "- 共通: 共通の対象\n")
    scene_dir = notes_dir / "scenes" / "scene-001"
    scene_dir.mkdir(parents=True)
    (scene_dir / "box_targets.md").symlink_to(outside)
    assert read_box_targets(notes_dir, "scene-001") == (
        BoxTarget("共通", "共通の対象"),
    )
    (notes_dir / "common" / "box_targets.md").unlink()
    (notes_dir / "common" / "box_targets.md").symlink_to(outside)
    assert read_box_targets(notes_dir, None) == DEFAULT_BOX_TARGETS
