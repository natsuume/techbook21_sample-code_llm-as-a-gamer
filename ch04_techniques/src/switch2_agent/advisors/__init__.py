"""advisor (戦略を考える上位のモデル) の呼び出し。

advisor はゲームの情報の 4 ファイルを書き換えるだけで、速い層 (Pipeline) は要求のたびにファイルを読む。
advisor の出力は戦略の受け渡しに使わない。

モジュール:
    base                   advisor の型 (Protocol) と、advisor に渡す文脈
    claude_code_headless   headless の Claude Code (claude -p) を裏のスレッドで実行する advisor
"""
