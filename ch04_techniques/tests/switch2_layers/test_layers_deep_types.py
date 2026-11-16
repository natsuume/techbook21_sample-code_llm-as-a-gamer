from switch2_layers.deep.deep_types import (
    DOCUMENT_KINDS,
    SCENE_DOCUMENT_KINDS,
    DeepJob,
    DeepResult,
    DeepTrigger,
    DocumentKey,
    DocumentKind,
)


def test_result_is_not_stopped_by_default():
    assert DeepResult(ok=True).stopped is False
    assert DeepResult(ok=False, message="stopped", stopped=True).stopped is True


def test_document_kinds_follow_the_issue():
    assert [kind.value for kind in DOCUMENT_KINDS] == [
        "controls",
        "tips",
        "strategy",
        "advice",
        "map",
        "game",
    ]
    # ゲームの性質は場面に依らない種類
    assert SCENE_DOCUMENT_KINDS == {DocumentKind.CONTROLS, DocumentKind.TIPS}
    assert DocumentKind.GAME not in SCENE_DOCUMENT_KINDS
    assert DocumentKind("game") is DocumentKind.GAME


def test_triggers_follow_the_issue():
    assert {trigger.value for trigger in DeepTrigger} == {
        "surface",
        "new_scene",
        "auto",
        "manual",
        "progress",
    }


def test_job_key():
    job = DeepJob(
        kind=DocumentKind.TIPS,
        scene_id="scene-001",
        trigger=DeepTrigger.NEW_SCENE,
        requested_t=1.0,
        previous_latest_frame_t=None,
        latest_frame_t=0.9,
    )
    assert job.key == DocumentKey(DocumentKind.TIPS, "scene-001")
