import ast
from pathlib import Path

import switch2_layers

PACKAGE_DIR = Path(switch2_layers.__file__).parent


def imported_modules(path: Path) -> set[str]:
    """path の Python ファイルが import するモジュール名 (import と from ... import の両方) の集合を返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_package_is_the_source_package():
    # tests/switch2_layers/ (テストのディレクトリー) ではなく、src のパッケージを読んでいる
    assert (PACKAGE_DIR / "cli.py").is_file()


def test_no_module_imports_switch2_agent():
    files = sorted(PACKAGE_DIR.rglob("*.py"))
    assert files
    for path in files:
        for name in imported_modules(path):
            assert name.split(".")[0] != "switch2_agent", f"{path} imports {name}"
