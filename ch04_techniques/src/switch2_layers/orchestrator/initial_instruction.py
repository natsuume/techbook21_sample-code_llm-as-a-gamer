"""Orchestrator 型の人の初期指示を、run の起動引数 (--instruction TEXT か --instruction-file PATH) から読む。

初期指示は任意で、渡さなければ None (主ループには「初期指示が無いこと」を伝える)。渡した初期指示は、app が
orchestrator/instruction.md に写して残す (run_directory.write_initial_instruction)。初期指示には、起動するソフトの名前
(ゲーム名) を書いてよい。
"""

from pathlib import Path


class InitialInstructionError(ValueError):
    """初期指示の誤り。メッセージに起動引数の名前 (--instruction か --instruction-file) を含む。"""


def load_initial_instruction(text: str | None, file: Path | None) -> str | None:
    """--instruction の値 text と --instruction-file の値 file から、人の初期指示を返す。

    - 両方 None: None
    - text だけ: 空か空白だけなら InitialInstructionError (メッセージに --instruction)。そうでなければ text をそのまま
      返す (前後の空白も変えない)
    - file だけ: UTF-8 で読む。読めない (OSError・UnicodeDecodeError) なら InitialInstructionError (メッセージに
      --instruction-file とパス)。中身が空か空白だけでも InitialInstructionError (メッセージに --instruction-file)。
      そうでなければ中身をそのまま返す
    - 両方: InitialInstructionError (cli では argparse の相互排他のグループが先に誤りにする)
    """
    if text is not None and file is not None:
        raise InitialInstructionError(
            "--instruction and --instruction-file cannot be used together"
        )
    if text is not None:
        if not text.strip():
            raise InitialInstructionError("--instruction must not be empty or blank")
        return text
    if file is None:
        return None
    try:
        content = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise InitialInstructionError(
            f"--instruction-file cannot be read as UTF-8 text: {file} ({error})"
        ) from error
    if not content.strip():
        raise InitialInstructionError(
            f"--instruction-file must not be empty or blank: {file}"
        )
    return content
