"""テストの fixture。合成の実行のディレクトリー (synthetic_runs) を pytest の tmp_path の下に書く。"""

import pytest
from synthetic_runs import SyntheticRuns, write_all_runs


@pytest.fixture
def runs(tmp_path) -> SyntheticRuns:
    """tmp_path / "runs" に 6 つの合成の実行を書き、期待値とともに返す。"""
    return write_all_runs(tmp_path / "runs")
