"""surface・middle・deep の 3 層のエージェントで Nintendo Switch 2 のゲームを遊ぶ (コマンド switch2-layers)。

層ごとの担当:
    surface   Decision Model で 0.2〜2 秒ごとにコントローラーを操作する (surface/)
    middle    数秒ごとに場面を特定し、短期タスクリストと surface の待ち時間を決める (middle/)
    deep      文書 (操作説明・攻略情報・大方針・助言・マップ) を作る (deep/)

層の間の受け渡しは、実行ごとのディレクトリー runs/<YYYYmmdd-HHMMSS>/ のファイルで行う (run_directory)。
置き換えて書くファイル (JSON・Markdown・画像) は一時ファイルに書いてから os.replace で置き換え、
1 行ずつ追記するファイル (JSON Lines) は 1 行分を書いてから flush する (file_io)。
時刻は、実行を始めてからの経過秒 t (run_clock) で記録する。

映像は switch2_remote.capture.Capture から受け取り (hardware/capture_source)、ボタンは
switch2_remote.bridge_supervisor.BridgeSupervisor に送る (hardware/pad_output)。
switch2_agent は import しない。
"""
