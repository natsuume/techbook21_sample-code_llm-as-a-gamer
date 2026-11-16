import httpx
import pytest

from switch2_layers.safety.fake_safety import FakeSafetyChecker
from switch2_layers.safety.jev_safety import JevSafetyChecker
from switch2_layers.safety.safety_assembly import build_safety_assembly
from switch2_layers.settings import SafetyKind


def must_not_send(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no request must be sent while assembling")


def test_fake_assembly_does_not_need_an_api_key():
    assembly = build_safety_assembly(SafetyKind.FAKE, None)
    assert isinstance(assembly.checker, FakeSafetyChecker)
    assembly.close()
    assembly.close()


def test_jev_assembly_uses_the_api_key_and_closes_its_client():
    http = httpx.Client(transport=httpx.MockTransport(must_not_send))
    assembly = build_safety_assembly(SafetyKind.JEV, "ts-test", http_client=http)
    checker = assembly.checker
    assert isinstance(checker, JevSafetyChecker)
    assert checker.client.api_key == "ts-test"
    assert checker.client.client is http
    assembly.close()
    assembly.close()
    # 渡された HTTP のクライアントは閉じない (作った側が閉じる)
    assert not http.is_closed


@pytest.mark.parametrize("api_key", [None, ""])
def test_jev_assembly_without_api_key_is_an_error(api_key):
    with pytest.raises(ValueError):
        build_safety_assembly(SafetyKind.JEV, api_key)
