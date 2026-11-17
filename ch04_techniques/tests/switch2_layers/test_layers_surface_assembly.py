import httpx
import pytest

from switch2_layers.buttons import allowed_buttons
from switch2_layers.settings import RunSettings, SurfaceKind
from switch2_layers.surface.box_tracking_state import BoxTrackingState
from switch2_layers.surface.fake_surface import FakeSurfaceDecider
from switch2_layers.surface.jev_surface import JevSurfaceDecider
from switch2_layers.surface.surface_assembly import build_surface_assembly


def unused_transport(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no request must be sent while assembling")


def test_fake_surface_has_no_hooks():
    assembly = build_surface_assembly(RunSettings(surface=SurfaceKind.FAKE), None)
    assert isinstance(assembly.decider, FakeSurfaceDecider)
    assert assembly.on_frame is None
    assert assembly.boxes_at is None
    assert assembly.on_run_directory is None
    assembly.close()
    assembly.close()


def test_jev_surface_connects_tracking_and_run_directory():
    http = httpx.Client(transport=httpx.MockTransport(unused_transport))
    settings = RunSettings(threshold_advisor=0.7, box_requery_every=3)
    assembly = build_surface_assembly(settings, "test-key", http_client=http)
    decider = assembly.decider
    assert isinstance(decider, JevSurfaceDecider)
    assert decider.settings.threshold_advisor == 0.7
    assert decider.settings.box_requery_every == 3
    assert decider.settings.allowed_buttons == allowed_buttons()
    assert isinstance(decider.tracking, BoxTrackingState)
    # キャプチャーのフレームと、送る画像の box は、decider と同じ追跡の状態につなぐ
    assert assembly.on_frame == decider.tracking.on_frame
    assert assembly.boxes_at == decider.tracking.boxes_at
    assert assembly.on_run_directory == decider.use_run_directory
    assembly.close()
    assembly.close()
    # 渡した HTTP のクライアントは閉じない
    assert not http.is_closed


def test_jev_surface_connects_plan_boxes_and_fake_has_none():
    http = httpx.Client(transport=httpx.MockTransport(unused_transport))
    assembly = build_surface_assembly(RunSettings(), "test-key", http_client=http)
    assert assembly.plan_boxes == assembly.decider.plan_boxes
    assembly.close()
    fake = build_surface_assembly(RunSettings(surface=SurfaceKind.FAKE), None)
    assert fake.plan_boxes is None


def test_jev_surface_never_asks_about_home():
    http = httpx.Client(transport=httpx.MockTransport(unused_transport))
    assembly = build_surface_assembly(RunSettings(), "test-key", http_client=http)
    assert "HOME" not in assembly.decider.settings.allowed_buttons
    assembly.close()


@pytest.mark.parametrize("key", [None, ""])
def test_jev_surface_needs_api_key(key):
    with pytest.raises(ValueError):
        build_surface_assembly(RunSettings(), key)
