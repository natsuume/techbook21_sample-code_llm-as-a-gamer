"""3 つの層 (surface・middle・deep) に毎回渡す、安全のための規則 (SAFETY_RULES)。

文は 2 層の構成のパッケージ switch2_agent の safety.SAFETY_RULES と同じにする (switch2_agent は import しない)。
規則を入れる場所:
    surface   要求の state の instructions (surface.surface_questions.SURFACE_INSTRUCTIONS)
    middle    変わらない指示 (middle.luna_instructions.LUNA_DEVELOPER_INSTRUCTIONS)
    deep      プロンプトの共通部分 (deep/prompts/common.md)。Markdown のファイルなので、同じ文を書き写す
"""

SAFETY_RULES = """\
安全のための規則 (毎回必ず守る):
- これはオフラインの個人実験である。
- 通信対戦のソフト、オンラインに接続する機能、ショップでの購入を選ばない。
- HOME メニューでは、試行しているソフト以外を起動しない。
- セーブデータの削除と、本体の設定の変更をしない。
- 迷ったら何も押さない。
"""
