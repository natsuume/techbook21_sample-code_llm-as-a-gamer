import httpx
import pytest

from switch2_layers.safety.fake_safety import FakeSafetyChecker
from switch2_layers.safety.jev_safety import JevSafetyChecker
from switch2_layers.safety.safety_assembly import build_safety_assembly
from switch2_layers.settings import SafetyKind
from switch2_layers.system_screen.system_screen_references import (
    SYSTEM_SCREEN_NAMES,
    load_reference_images,
)

REFERENCE_IMAGES = {name: (f"jpeg-{name}".encode(),) for name in SYSTEM_SCREEN_NAMES}


def must_not_send(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no request must be sent while assembling")


class ReferenceLoader:
    """見本の画像の読み込みの偽物。呼ばれた回数を数え、決めた画像 (または例外) を返す。"""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.count = 0

    def __call__(self) -> dict[str, tuple[bytes, ...]]:
        self.count += 1
        if self.error is not None:
            raise self.error
        return REFERENCE_IMAGES


def test_fake_assembly_does_not_need_an_api_key_or_the_reference_images():
    loader = ReferenceLoader()
    assembly = build_safety_assembly(SafetyKind.FAKE, None, load_references=loader)
    assert isinstance(assembly.checker, FakeSafetyChecker)
    assert loader.count == 0
    assembly.close()
    assembly.close()


def test_jev_assembly_uses_the_api_key_and_closes_its_client():
    http = httpx.Client(transport=httpx.MockTransport(must_not_send))
    assembly = build_safety_assembly(
        SafetyKind.JEV, "ts-test", http_client=http, load_references=ReferenceLoader()
    )
    checker = assembly.checker
    assert isinstance(checker, JevSafetyChecker)
    assert checker.client.api_key == "ts-test"
    assert checker.client.client is http
    assembly.close()
    assembly.close()
    # 渡された HTTP のクライアントは閉じない (作った側が閉じる)
    assert not http.is_closed


def test_jev_assembly_reads_the_reference_images_once():
    loader = ReferenceLoader()
    http = httpx.Client(transport=httpx.MockTransport(must_not_send))
    assembly = build_safety_assembly(
        SafetyKind.JEV, "ts-test", http_client=http, load_references=loader
    )
    assert loader.count == 1
    assert assembly.checker.reference_images == REFERENCE_IMAGES
    assembly.close()


def test_jev_assembly_reads_the_package_reference_images_by_default():
    http = httpx.Client(transport=httpx.MockTransport(must_not_send))
    assembly = build_safety_assembly(SafetyKind.JEV, "ts-test", http_client=http)
    assert assembly.checker.reference_images == load_reference_images()
    assembly.close()


def test_jev_assembly_with_missing_reference_images_is_an_error():
    loader = ReferenceLoader(FileNotFoundError("software_close.jpg"))
    with pytest.raises(FileNotFoundError):
        build_safety_assembly(SafetyKind.JEV, "ts-test", load_references=loader)


@pytest.mark.parametrize("api_key", [None, ""])
def test_jev_assembly_without_api_key_is_an_error(api_key):
    loader = ReferenceLoader()
    with pytest.raises(ValueError):
        build_safety_assembly(SafetyKind.JEV, api_key, load_references=loader)
    # API キーの誤りは、見本の画像を読む前に分かる
    assert loader.count == 0
