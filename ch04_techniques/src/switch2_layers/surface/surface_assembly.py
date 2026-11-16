"""run の起動引数 (--surface など) に合わせて、surface の部品一式 (SurfaceAssembly) を組み立てる。

app.LayersParts には、部品一式の値を次のように渡す (cli.run_command):
    surface              decider
    on_frame             on_frame          (キャプチャーの新しいフレームを box の追跡に渡す)
    surface_boxes_at     boxes_at          (surface に送る画像に描く box)
    surface_plan_boxes   plan_boxes        (要求ごとの box の書き込み方: 色の番号と、最新のフレームに描かない対象)
    on_run_directory     on_run_directory  (実行ディレクトリーの notes/ を decider に渡す)
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from switch2_layers.buttons import allowed_buttons
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.surface_frames import BoxDrawingPlan
from switch2_layers.run_directory import RunPaths
from switch2_layers.settings import RunSettings, SurfaceKind
from switch2_layers.surface.box_tracking_state import BoxTrackingState
from switch2_layers.surface.fake_surface import FakeSurfaceDecider
from switch2_layers.surface.jev_client import JevClient
from switch2_layers.surface.jev_surface import JevSurfaceDecider, JevSurfaceSettings
from switch2_layers.surface.surface_types import Box, SurfaceDecider


@dataclass(frozen=True)
class SurfaceAssembly:
    """surface の部品一式。

    decider            surface の判断
    on_frame           キャプチャーの新しいフレームを受け取る関数。使わなければ None
    boxes_at           時刻 t のフレームに描く box を返す関数。使わなければ None
    on_run_directory   実行ディレクトリーを作った後に呼ぶ関数。使わなければ None
    close              終了時に呼ぶ後片付けの関数 (HTTP のクライアントを閉じる)。2 回呼んでもよい
    plan_boxes         送る画像を作る前に、場面 ID からその要求の box の書き込み方を返す関数
                       (surface_loop.SurfaceLoopParts.plan_boxes)。使わなければ None (既定)
    """

    decider: SurfaceDecider
    on_frame: Callable[[CapturedFrame], None] | None
    boxes_at: Callable[[float], Sequence[Box]] | None
    on_run_directory: Callable[[RunPaths], None] | None
    close: Callable[[], None]
    plan_boxes: Callable[[str | None], BoxDrawingPlan] | None = None


def build_surface_assembly(
    settings: RunSettings,
    api_key: str | None,
    http_client: httpx.Client | None = None,
) -> SurfaceAssembly:
    """settings.surface に合わせて SurfaceAssembly を作る。

    - fake: decider は FakeSurfaceDecider()、on_frame・boxes_at・on_run_directory・plan_boxes は None、close は
      何もしない関数。api_key は使わない
    - jev: tracking = BoxTrackingState()、client = JevClient(api_key, http_client)、
      decider = JevSurfaceDecider(client, tracking, JevSurfaceSettings(buttons.allowed_buttons(settings.allow_home),
      settings.threshold_advisor, settings.box_requery_every, settings.hold_margin))。on_frame は tracking.on_frame、boxes_at は
      tracking.boxes_at、on_run_directory は decider.use_run_directory、plan_boxes は decider.plan_boxes、close は
      client.close。api_key が None か空なら ValueError (HTTP のクライアントを作る前)
    HTTP の要求は送らない (Jev を呼ぶのは decide のとき)。
    """
    if settings.surface is SurfaceKind.FAKE:
        return SurfaceAssembly(
            decider=FakeSurfaceDecider(),
            on_frame=None,
            boxes_at=None,
            on_run_directory=None,
            close=_do_nothing,
        )
    if not api_key:
        raise ValueError("the jev surface needs an API key")
    tracking = BoxTrackingState()
    client = JevClient(api_key, client=http_client)
    decider = JevSurfaceDecider(
        client,
        tracking,
        JevSurfaceSettings(
            allowed_buttons=allowed_buttons(settings.allow_home),
            threshold_advisor=settings.threshold_advisor,
            box_requery_every=settings.box_requery_every,
            hold_margin=settings.hold_margin,
        ),
    )
    return SurfaceAssembly(
        decider=decider,
        on_frame=tracking.on_frame,
        boxes_at=tracking.boxes_at,
        on_run_directory=decider.use_run_directory,
        close=client.close,
        plan_boxes=decider.plan_boxes,
    )


def _do_nothing() -> None:
    """何もしない後片付けの関数 (フェイクの surface の close)。"""
