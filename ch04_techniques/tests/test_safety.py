import pytest

from switch2_agent.safety import SAFETY_RULES


@pytest.mark.parametrize(
    "phrase",
    [
        "オフラインの個人実験",
        "通信対戦",
        "オンラインに接続する機能",
        "ショップでの購入",
        "HOME メニュー",
        "セーブデータの削除",
        "本体の設定の変更",
        "迷ったら何も押さない",
    ],
)
def test_safety_rules_contain_each_rule(phrase):
    assert phrase in SAFETY_RULES
