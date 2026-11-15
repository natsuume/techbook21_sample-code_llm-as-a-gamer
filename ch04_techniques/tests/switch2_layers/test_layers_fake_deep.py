from switch2_layers.deep.deep_types import (
    DeepJob,
    DeepResult,
    DeepTrigger,
    DocumentKind,
)
from switch2_layers.deep.fake_deep import FakeDeepRunner


def test_fake_deep_does_nothing_and_succeeds(tmp_path):
    job_dir = tmp_path / "deep" / "advice-001"
    (job_dir / "work").mkdir(parents=True)
    (job_dir / "job.json").write_text("{}", encoding="utf-8")
    before = sorted(str(p.relative_to(job_dir)) for p in job_dir.rglob("*"))
    job = DeepJob(
        kind=DocumentKind.ADVICE,
        scene_id=None,
        trigger=DeepTrigger.MANUAL,
        requested_t=1.0,
        previous_latest_frame_t=None,
        latest_frame_t=0.8,
    )
    result = FakeDeepRunner().run(job, job_dir)
    assert result == DeepResult(ok=True)
    assert result.stopped is False
    assert sorted(str(p.relative_to(job_dir)) for p in job_dir.rglob("*")) == before
    assert (job_dir / "job.json").read_text(encoding="utf-8") == "{}"


def test_fake_deep_stop_and_kill_do_nothing(tmp_path):
    job_dir = tmp_path / "deep" / "advice-001"
    (job_dir / "work").mkdir(parents=True)
    runner = FakeDeepRunner()
    # 止めるものが無いため、どちらも例外を出さずにすぐ戻る (終わったジョブ・知らないジョブでも)
    runner.stop(job_dir)
    runner.kill(job_dir)
    runner.stop(tmp_path / "deep" / "missing-001")
    runner.kill(tmp_path / "deep" / "missing-001")
    assert sorted(p.name for p in job_dir.iterdir()) == ["work"]
