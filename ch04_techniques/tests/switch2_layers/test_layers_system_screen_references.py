"""本体の画面の見本 (名前・選択肢の説明・ファイル名) と、パッケージに置いた見本の画像を確かめる。"""

import dataclasses
import re
from importlib import resources

import cv2
import numpy as np
import pytest

from switch2_layers.system_screen.system_screen_references import (
    NONE_CHOICE,
    NONE_CHOICE_DESCRIPTION,
    REFERENCES_DIR,
    REFERENCES_PACKAGE,
    SYSTEM_SCREEN_CHOICE_NAMES,
    SYSTEM_SCREEN_NAMES,
    SYSTEM_SCREEN_REFERENCES,
    SystemScreenReference,
    load_reference_images,
)

ID_PATTERN = re.compile(r"[A-Za-z0-9_]+")
GRAY = 128
# JPEG の圧縮で、塗りつぶした灰色がずれる幅の上限
GRAY_TOLERANCE = 3
# 塗りつぶした灰色の画素が、画像全体に占める割合の下限 (本体の UI の部分だけを残したことの確かめ)
MIN_GRAY_FRACTION = 0.2


def reference_directory():
    return resources.files(REFERENCES_PACKAGE).joinpath(REFERENCES_DIR)


def reference_file_names() -> list[str]:
    return [file for reference in SYSTEM_SCREEN_REFERENCES for file in reference.files]


# --- 見本の定義 ---


def test_names_follow_the_issue_in_order():
    assert SYSTEM_SCREEN_NAMES == (
        "home_menu",
        "controllers",
        "controller_order",
        "user_select",
        "software_close",
        "software_list",
    )
    assert SYSTEM_SCREEN_NAMES == tuple(
        reference.name for reference in SYSTEM_SCREEN_REFERENCES
    )
    assert all(ID_PATTERN.fullmatch(name) for name in SYSTEM_SCREEN_NAMES)
    assert len(set(SYSTEM_SCREEN_NAMES)) == len(SYSTEM_SCREEN_NAMES)


def test_choices_put_none_first_and_then_the_names():
    assert NONE_CHOICE == "none"
    assert NONE_CHOICE not in SYSTEM_SCREEN_NAMES
    assert SYSTEM_SCREEN_CHOICE_NAMES == (NONE_CHOICE, *SYSTEM_SCREEN_NAMES)
    assert "`frame`" in NONE_CHOICE_DESCRIPTION


@pytest.mark.parametrize("reference", SYSTEM_SCREEN_REFERENCES, ids=lambda r: r.name)
def test_each_reference_has_one_jpeg_named_after_it(reference):
    assert isinstance(reference, SystemScreenReference)
    assert reference.files == (f"{reference.name}.jpg",)


@pytest.mark.parametrize("reference", SYSTEM_SCREEN_REFERENCES, ids=lambda r: r.name)
def test_each_description_points_to_its_image_by_state_path(reference):
    # 選択肢の説明は、見本の画像を state の中のパス references.<名前> で指す
    assert f"`references.{reference.name}`" in reference.description


def test_reference_is_frozen():
    reference = SYSTEM_SCREEN_REFERENCES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        reference.name = "other"


# --- パッケージに置いた見本の画像 ---


def test_every_reference_file_is_in_the_package():
    directory = reference_directory()
    missing = [
        name
        for name in reference_file_names()
        if not directory.joinpath(name).is_file()
    ]
    assert missing == []


def test_reference_directory_has_only_the_reference_files():
    # 元の撮影など、見本ではない画像をリポジトリーに入れない
    names = sorted(entry.name for entry in reference_directory().iterdir())
    assert names == sorted(reference_file_names())


@pytest.mark.parametrize("file_name", reference_file_names())
def test_each_reference_image_is_a_1280x720_jpeg_masked_with_gray(file_name):
    data = reference_directory().joinpath(file_name).read_bytes()
    assert data[:2] == b"\xff\xd8"  # JPEG の SOI
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape == (720, 1280, 3)
    gray = np.all(np.abs(image.astype(np.int16) - GRAY) <= GRAY_TOLERANCE, axis=2)
    assert gray.mean() >= MIN_GRAY_FRACTION


# --- 読み込み ---


def test_load_reference_images_reads_every_reference_in_order():
    images = load_reference_images()
    assert list(images) == list(SYSTEM_SCREEN_NAMES)
    directory = reference_directory()
    for reference in SYSTEM_SCREEN_REFERENCES:
        assert images[reference.name] == tuple(
            directory.joinpath(file).read_bytes() for file in reference.files
        )


def test_load_reference_images_reads_only_the_given_references():
    home_menu = SYSTEM_SCREEN_REFERENCES[0]
    images = load_reference_images([home_menu])
    assert list(images) == ["home_menu"]
    assert images["home_menu"] == (
        reference_directory().joinpath("home_menu.jpg").read_bytes(),
    )


def test_load_reference_images_with_a_missing_file_is_an_error():
    missing = SystemScreenReference(
        name="missing", description="`references.missing`", files=("missing.jpg",)
    )
    with pytest.raises(FileNotFoundError):
        load_reference_images([missing])
