"""run の起動引数 --safety に合わせて、安全の監視の部品 (SafetyAssembly) を組み立てる。

app.LayersParts の safety には、部品の checker を渡す (cli.run_command)。surface の部品とは別に作り、HTTP の
クライアントも surface と共有しない (安全の監視を surface の実装に依らずに使うため)。
jev では、照合の見本の画像を組み立てるときに 1 回だけ読む (run の始め。確かめのたびには読まない)。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from switch2_layers.safety.fake_safety import FakeSafetyChecker
from switch2_layers.safety.jev_safety import JevSafetyChecker
from switch2_layers.safety.safety_types import SafetyChecker
from switch2_layers.settings import SafetyKind
from switch2_layers.surface.jev_client import JevClient
from switch2_layers.system_screen.system_screen_references import (
    load_reference_images,
)


@dataclass(frozen=True)
class SafetyAssembly:
    """安全の監視の部品。

    checker   画面を確かめるもの
    close     終了時に呼ぶ後片付けの関数 (HTTP のクライアントを閉じる)。2 回呼んでもよい
    """

    checker: SafetyChecker
    close: Callable[[], None]


def build_safety_assembly(
    kind: SafetyKind,
    api_key: str | None,
    http_client: httpx.Client | None = None,
    load_references: Callable[
        [], Mapping[str, tuple[bytes, ...]]
    ] = load_reference_images,
    on_game_over: Callable[[float], None] | None = None,
) -> SafetyAssembly:
    """kind (--safety の値) に合わせて SafetyAssembly を作る。

    - fake: checker は safety.fake_safety.FakeSafetyChecker()、close は何もしない関数。api_key と load_references は
      使わない
    - jev: api_key が None か空なら ValueError。続けて reference_images = load_references() で見本の画像を 1 回読み
      (既定 system_screen.system_screen_references.load_reference_images。ファイルが無ければその例外をそのまま出す)、
      client = surface.jev_client.JevClient(api_key, client=http_client)、
      checker = safety.jev_safety.JevSafetyChecker(client, reference_images)、close は client.close。
      ValueError と見本の画像の読み込みの例外は、HTTP のクライアントを作る前に出す
    HTTP の要求は送らない (Jev を呼ぶのは check のとき)。
    """
    if kind is SafetyKind.FAKE:
        return SafetyAssembly(checker=FakeSafetyChecker(), close=_do_nothing)
    if not api_key:
        raise ValueError("the jev safety monitor needs an API key")
    reference_images = load_references()
    client = JevClient(api_key, client=http_client)
    return SafetyAssembly(
        checker=JevSafetyChecker(client, reference_images, on_game_over=on_game_over),
        close=client.close,
    )


def _do_nothing() -> None:
    """何もしない後片付けの関数 (フェイクの安全の監視の close)。"""
