import dataclasses
from collections.abc import Iterator, Mapping

from switch2_layers.deep.deep_scheduler import DeepDocumentStatus
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import NEUTRAL_ACTION
from switch2_layers.surface.fake_surface import FakeSurfaceDecider
from switch2_layers.surface.surface_types import (
    SurfaceDocuments,
    SurfaceFrame,
    SurfaceInput,
    SurfaceOutput,
)


def surface_input() -> SurfaceInput:
    documents = SurfaceDocuments(
        tasks="宿屋に入っている",
        task_id="task-001",
        tips="",
        controls="",
        advice="",
        scene_id=None,
        uses_common_controls=True,
        uses_common_tips=True,
    )
    return SurfaceInput(
        t=1.0,
        frames=(SurfaceFrame(t=0.9, jpeg=b"\xff\xd8", label="t=0.900"),),
        documents=documents,
        timeline="",
        timeline_generated_t=None,
        recent_actions=(),
    )


def test_fake_surface_returns_neutral_action_without_deep_requests():
    output = FakeSurfaceDecider().decide(surface_input())
    assert output == SurfaceOutput(action=NEUTRAL_ACTION)
    assert output.deep_kinds == frozenset()
    assert output.info_labels == ()
    assert output.boxes == ()
    assert output.scene_changed is False
    # 先頭のタスクがあっても、2 つの問いを送らない (完了も取り消しの要求もしない)
    assert output.task_done_p is None
    assert output.task_cancel_p is None


class UntouchableStatuses(Mapping[DocumentKind, DeepDocumentStatus | None]):
    """advisor_statuses の偽物。読まれたらテストを失敗させる。"""

    def __getitem__(self, kind: DocumentKind) -> DeepDocumentStatus | None:
        raise AssertionError("the fake surface read advisor_statuses")

    def __iter__(self) -> Iterator[DocumentKind]:
        raise AssertionError("the fake surface read advisor_statuses")

    def __len__(self) -> int:
        raise AssertionError("the fake surface read advisor_statuses")


def test_fake_surface_does_not_use_advisor_statuses():
    surface = dataclasses.replace(
        surface_input(), advisor_statuses=UntouchableStatuses()
    )
    assert FakeSurfaceDecider().decide(surface) == SurfaceOutput(action=NEUTRAL_ACTION)


def test_fake_surface_records_no_request():
    received = []
    surface = dataclasses.replace(surface_input(), request_sink=received.append)
    output = FakeSurfaceDecider().decide(surface)
    # Jev を呼ばないので、送る要求の書き込み先を呼ばず、記録のための情報も無い
    assert received == []
    assert output.exchange is None
