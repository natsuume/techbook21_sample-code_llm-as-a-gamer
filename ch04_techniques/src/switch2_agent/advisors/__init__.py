"""役割ごとの advisor (ゲームの情報をまとめ、目標と戦略を考え、問題の解決を助ける上位のモデル) の呼び出し。

advisor は役割 (advisor_roles.ADVISOR_ROLES) ごとに 1 つずつあり、それぞれがゲームの情報の 4 ファイルのうち
受け持ちの 1 ファイルを書き換えるだけで、速い層 (Pipeline) は要求のたびにファイルを読む。
advisor の出力は戦略の受け渡しに使わない。

モジュール:
    base                   advisor の型 (Protocol) と、advisor に渡す文脈
    claude_code_headless   headless の Claude Code (claude -p) を裏のスレッドで実行する advisor
"""
