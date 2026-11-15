"""notes/ の文書を、notes/ の外のファイルを読まずに読む。

文書のパス (notes/ からの / 区切りの相対パス) のシンボリックリンクを解決した先が、notes/ を解決した先の下にある
通常のファイルのときだけ読む。そうでないとき (無い・ディレクトリー・notes/ の外を指すシンボリックリンク・
途中のディレクトリーが notes/ の外へのシンボリックリンク・パスの確認や読み込みで OSError) は「無い文書」として扱う。
notes/ の中を指すシンボリックリンクは、その先の文書として読む。
surface と middle の文書の読み込み (surface_documents・middle_input_builder) は、このモジュールの関数で読む。
deep のジョブの用意で使う map の文書の列挙 (note_paths.deep_document_notes) は、resolve_note_file で確かめる。
"""

from pathlib import Path


def resolve_note_file(notes_dir: Path, note: str) -> Path | None:
    """notes_dir / note のシンボリックリンクを解決した先 (Path.resolve) を、文書として読めるなら返す。

    解決した先が、notes_dir を解決した先の下 (notes_dir 自体は除く) にある通常のファイルなら、そのパスを返す。
    次のときは None を返す (例外は出さない):
        - note が空・絶対パス・.. を含む
        - 解決した先が無い、通常のファイルでない (ディレクトリーなど)、notes_dir を解決した先の下に無い
        - パスの確認で OSError が出た (名前が長すぎる、シンボリックリンクの循環など)
    """
    raise NotImplementedError


def read_note_bytes(notes_dir: Path, note: str) -> bytes | None:
    """文書 note のバイト列を返す。resolve_note_file が None なら None。

    resolve_note_file が返したパスを読む。読むときの OSError (確かめた後に消えた・置き換えられた等) も None にする。
    """
    raise NotImplementedError


def read_note_text(notes_dir: Path, note: str) -> str:
    """文書 note を UTF-8 で読んだ文字列を返す。read_note_bytes が None か、UTF-8 として読めなければ空文字列。"""
    raise NotImplementedError
