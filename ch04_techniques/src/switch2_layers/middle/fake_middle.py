"""骨組みのための middle のフェイク。固定の場面名と surface_wait_s 0.2 を返す。"""

from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput

FAKE_SCENE_NAME = "fake scene"
FAKE_SURFACE_WAIT_S = 0.2


class FakeMiddleAnalyzer:
    """MiddleAnalyzer のフェイク。"""

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        """MiddleOutput(scene_name=FAKE_SCENE_NAME, is_stuck=False, stuck_description="",
        surface_wait_s=FAKE_SURFACE_WAIT_S, timeline="", tasks="") を返す。middle_input は使わない。
        """
        return MiddleOutput(
            scene_name=FAKE_SCENE_NAME,
            is_stuck=False,
            stuck_description="",
            surface_wait_s=FAKE_SURFACE_WAIT_S,
            timeline="",
            tasks="",
        )
