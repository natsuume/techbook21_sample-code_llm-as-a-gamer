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
        tasks="",
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
