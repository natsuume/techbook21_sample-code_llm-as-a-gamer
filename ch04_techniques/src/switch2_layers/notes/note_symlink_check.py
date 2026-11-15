"""deep を起動する前に、そのジョブの対象の文書のパスにシンボリックリンクが無いことを確かめる。

確かめるのは、notes/ 自体と、文書ごとに notes/ から文書までの途中のディレクトリーと、文書自体。
パスのシンボリックリンクは解決せずに、要素ごとに os.lstat で確かめる (解決してしまうとリンクが見えなくなるため)。
notes/ より上 (実行ディレクトリーとその親) は確かめない。呼び出し側は、実行ディレクトリーのシンボリックリンクを解決した
絶対パスの下の notes/ を渡す。
まだ無い文書と、まだ無い途中のディレクトリーはリンクではない (deep が Write で作る)。リンクの先が無いシンボリックリンク
(壊れたリンク) はリンクとして扱う。
"""

from collections.abc import Iterable
from pathlib import Path


def find_symlink_in_note_paths(notes_dir: Path, notes: Iterable[str]) -> Path | None:
    """notes_dir と、notes の各文書のパスの要素のうち、最初に見つけたシンボリックリンクのパスを返す。無ければ None。

    確かめる順:
        1. notes_dir 自体
        2. notes の順に、文書ごとに notes_dir に近い要素から順に (scenes/scene-001/tips.md なら
           notes_dir/scenes、notes_dir/scenes/scene-001、notes_dir/scenes/scene-001/tips.md)
    それぞれのパスを os.lstat で調べ (シンボリックリンクをたどらない)、stat.S_ISLNK ならそのパス
    (notes_dir / 要素までの相対パス。解決しない) を返す。
    os.lstat が FileNotFoundError か NotADirectoryError を出したら、そのパスとその下はまだ無いものとして、
    その文書の残りの要素を確かめずに次の文書へ進む。ほかの OSError (権限が無い・名前が長すぎるなど) はそのまま出す。
    notes のどれかが notes/ からの / 区切りの相対パスでない (空・/ で始まる・`.` か `..` か空の要素を含む) ときは、
    ファイルシステムを調べる前に ValueError にする。
    notes_dir 自体が無ければ (FileNotFoundError)、確かめるものが無いため None を返す。
    """
    raise NotImplementedError
