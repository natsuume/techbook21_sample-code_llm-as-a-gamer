"""骨組みのための surface のフェイク。中立の操作を返し、deep を呼ばない。"""

from switch2_layers.pad_action import NEUTRAL_ACTION
from switch2_layers.surface.surface_types import SurfaceInput, SurfaceOutput


class FakeSurfaceDecider:
    """SurfaceDecider のフェイク。"""

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        """SurfaceOutput(action=pad_action.NEUTRAL_ACTION) を返す。

        deep_kinds・info_labels・boxes は空、scene_changed は False、exchange は None。surface_input は使わない
        (request_sink も呼ばない)。
        """
        return SurfaceOutput(action=NEUTRAL_ACTION)
