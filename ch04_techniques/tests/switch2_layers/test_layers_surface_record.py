import json

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction
from switch2_layers.surface.surface_record import (
    SentFrame,
    SurfaceRecord,
    box_to_json,
    info_label_to_json,
    surface_row,
)
from switch2_layers.surface.surface_types import (
    Box,
    BoxSource,
    InfoLabel,
    SurfaceDocuments,
)

DOCUMENTS = SurfaceDocuments(
    tasks="- 宿屋に入る",
    tips="",
    controls="",
    advice="",
    scene_id="scene-001",
    uses_common_controls=False,
    uses_common_tips=True,
)
SURFACE_KEYS = [
    "t_sent",
    "t_received",
    "frames",
    "scene_id",
    "uses_common",
    "middle_generated_t",
    "action",
    "blocked_buttons",
    "deep_requests",
    "info_labels",
    "boxes",
    "scene_changed",
    "wait_s",
    "error",
]


def test_info_label_and_box_to_json():
    assert info_label_to_json(InfoLabel("menu_open", 0.75)) == {
        "name": "menu_open",
        "probability": 0.75,
    }
    assert box_to_json(Box("door", 0.1, 0.2, 0.3, 0.4, 0.9)) == {
        "label": "door",
        "x_min": 0.1,
        "y_min": 0.2,
        "x_max": 0.3,
        "y_max": 0.4,
        "confidence": 0.9,
        "source": None,
    }


def test_box_to_json_writes_source_and_null_confidence():
    jev_box = Box("自機", 0.1, 0.2, 0.3, 0.4, None, BoxSource.JEV)
    assert box_to_json(jev_box)["confidence"] is None
    assert box_to_json(jev_box)["source"] == "jev"
    tracker_box = Box("敵", 0.5, 0.5, 0.6, 0.7, 0.82, BoxSource.TRACKER)
    assert box_to_json(tracker_box)["source"] == "tracker"
    assert list(box_to_json(tracker_box)) == [
        "label",
        "x_min",
        "y_min",
        "x_max",
        "y_max",
        "confidence",
        "source",
    ]


def test_surface_row_has_the_keys_of_the_issue():
    record = SurfaceRecord(
        t_sent=10.0,
        t_received=10.4,
        frames=[
            SentFrame(9.0, "frames/surface/000009000.jpg"),
            SentFrame(10.0, None),
        ],
        documents=DOCUMENTS,
        middle_generated_t=8.5,
        action=PadAction(held=frozenset({"A"})),
        blocked_buttons=["HOME"],
        deep_requests=[DocumentKind.TIPS, DocumentKind.ADVICE],
        info_labels=[InfoLabel("menu_open", 0.75)],
        boxes=[Box("door", 0.1, 0.2, 0.3, 0.4, 0.9)],
        scene_changed=True,
        wait_s=0.5,
        error=None,
    )
    row = surface_row(record)
    assert list(row) == SURFACE_KEYS
    assert row == {
        "t_sent": 10.0,
        "t_received": 10.4,
        "frames": [
            {"t": 9.0, "file": "frames/surface/000009000.jpg"},
            {"t": 10.0, "file": None},
        ],
        "scene_id": "scene-001",
        "uses_common": {"controls": False, "tips": True},
        "middle_generated_t": 8.5,
        "action": {
            "held": ["A"],
            "taps": [],
            "sticks": {"LS": [0.0, 0.0], "RS": [0.0, 0.0]},
        },
        "blocked_buttons": ["HOME"],
        "deep_requests": ["tips", "advice"],
        "info_labels": [{"name": "menu_open", "probability": 0.75}],
        "boxes": [
            {
                "label": "door",
                "x_min": 0.1,
                "y_min": 0.2,
                "x_max": 0.3,
                "y_max": 0.4,
                "confidence": 0.9,
                "source": None,
            }
        ],
        "scene_changed": True,
        "wait_s": 0.5,
        "error": None,
    }
    # そのまま JSON にできる
    assert json.loads(json.dumps(row)) == row


def test_surface_row_of_failed_request():
    record = SurfaceRecord(
        t_sent=1.0,
        t_received=1.2,
        frames=[SentFrame(0.9, "frames/surface/000000900.jpg")],
        documents=DOCUMENTS,
        middle_generated_t=None,
        action=None,
        blocked_buttons=[],
        deep_requests=[],
        info_labels=[],
        boxes=[],
        scene_changed=False,
        wait_s=1.0,
        error="RuntimeError: model failed",
    )
    row = surface_row(record)
    assert row["action"] is None
    assert row["error"] == "RuntimeError: model failed"
    assert row["deep_requests"] == []
