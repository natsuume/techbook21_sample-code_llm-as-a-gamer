"""安全のための規則の文。2 層の構成 (switch2_agent) と同じ文にする。"""

from switch2_agent.safety import SAFETY_RULES as TWO_LAYER_SAFETY_RULES
from switch2_layers.safety.safety_rules import SAFETY_RULES

RULE_LINES = [
    "- これはオフラインの個人実験である。",
    "- 通信対戦のソフト、オンラインに接続する機能、ショップでの購入を選ばない。",
    "- HOME メニューでは、試行しているソフト以外を起動しない。",
    "- セーブデータの削除と、本体の設定の変更をしない。",
    "- 迷ったら何も押さない。",
]


def test_rules_are_the_same_as_the_two_layer_agent():
    # テストからは 2 層の構成のパッケージを読んで比べる (switch2_layers のコードは switch2_agent を import しない)
    assert SAFETY_RULES == TWO_LAYER_SAFETY_RULES


def test_rules_have_every_rule_line():
    lines = SAFETY_RULES.splitlines()
    for line in RULE_LINES:
        assert line in lines, line
