"""骨組みのための deep のフェイク。何もせず成功を返す。"""

from pathlib import Path

from switch2_layers.deep.deep_types import DeepJob, DeepResult


class FakeDeepRunner:
    """DeepRunner のフェイク。run はすぐ戻るため、stop と kill で止めるものは無い。"""

    def run(self, job: DeepJob, job_dir: Path) -> DeepResult:
        """何もせず DeepResult(ok=True) を返す (文書も job_dir も変えない)。"""
        raise NotImplementedError

    def stop(self, job_dir: Path) -> None:
        """何もしない (run は既に戻っている)。例外は出さない。"""
        raise NotImplementedError

    def kill(self, job_dir: Path) -> None:
        """何もしない (run は既に戻っている)。例外は出さない。"""
        raise NotImplementedError
