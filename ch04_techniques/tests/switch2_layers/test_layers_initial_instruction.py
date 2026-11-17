import pytest

from switch2_layers.orchestrator.initial_instruction import (
    InitialInstructionError,
    load_initial_instruction,
)

INSTRUCTION = "RPG のソフトをユーザー 1 で起動し、主人公の名前は自由に決めて冒険を始めてください\n"


def test_initial_instruction_error_is_a_value_error():
    assert issubclass(InitialInstructionError, ValueError)


def test_no_instruction_is_none():
    assert load_initial_instruction(None, None) is None


def test_text_is_returned_as_written():
    # 前後の空白も変えずに返す
    text = "  冒険を始める  \n"
    assert load_initial_instruction(text, None) == text


@pytest.mark.parametrize("text", ["", "   ", " \n\t"], ids=["empty", "spaces", "mixed"])
def test_blank_text_is_an_error(text):
    with pytest.raises(InitialInstructionError) as error_info:
        load_initial_instruction(text, None)
    assert "--instruction" in str(error_info.value)


def test_file_content_is_returned_as_written(tmp_path):
    path = tmp_path / "instruction.md"
    path.write_text(INSTRUCTION, encoding="utf-8")
    assert load_initial_instruction(None, path) == INSTRUCTION


def test_missing_file_is_an_error_with_its_path(tmp_path):
    path = tmp_path / "missing.md"
    with pytest.raises(InitialInstructionError) as error_info:
        load_initial_instruction(None, path)
    message = str(error_info.value)
    assert "--instruction-file" in message
    assert str(path) in message


def test_file_that_is_not_utf8_is_an_error_with_its_path(tmp_path):
    path = tmp_path / "instruction.md"
    path.write_bytes(b"\xff\xfe\x00broken")
    with pytest.raises(InitialInstructionError) as error_info:
        load_initial_instruction(None, path)
    message = str(error_info.value)
    assert "--instruction-file" in message
    assert str(path) in message


def test_blank_file_is_an_error(tmp_path):
    path = tmp_path / "instruction.md"
    path.write_text("  \n\t\n", encoding="utf-8")
    with pytest.raises(InitialInstructionError) as error_info:
        load_initial_instruction(None, path)
    assert "--instruction-file" in str(error_info.value)


def test_both_text_and_file_are_an_error(tmp_path):
    path = tmp_path / "instruction.md"
    path.write_text(INSTRUCTION, encoding="utf-8")
    with pytest.raises(InitialInstructionError):
        load_initial_instruction("冒険を始める", path)
