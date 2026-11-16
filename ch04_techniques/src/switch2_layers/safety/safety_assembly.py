"""run の起動引数 --safety に合わせて、安全の監視の部品 (SafetyAssembly) を組み立てる。

app.LayersParts の safety には、部品の checker を渡す (cli.run_command)。surface の部品とは別に作り、HTTP の
クライアントも surface と共有しない (安全の監視を surface の実装に依らずに使うため)。
"""

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from switch2_layers.safety.safety_types import SafetyChecker
from switch2_layers.settings import SafetyKind


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
) -> SafetyAssembly:
    """kind (--safety の値) に合わせて SafetyAssembly を作る。

    - fake: checker は safety.fake_safety.FakeSafetyChecker()、close は何もしない関数。api_key は使わない
    - jev: client = surface.jev_client.JevClient(api_key, client=http_client)、
      checker = safety.jev_safety.JevSafetyChecker(client)、close は client.close。
      api_key が None か空なら ValueError (HTTP のクライアントを作る前)
    HTTP の要求は送らない (Jev を呼ぶのは check のとき)。
    """
    raise NotImplementedError
