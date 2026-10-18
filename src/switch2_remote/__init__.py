"""Switch 2 の実機を 1 コマンドずつ操作するためのパッケージ。

映像は Windows 側の ffmpeg.exe (Elgato 4K X, 720p NV12) から標準出力パイプで受け取り、
入力は Windows 側の controller_bridge.ps1 から Bluetooth 経由で Pico 2 W に送る。

モジュール:
    ffmpeg_locator       Windows 側にある ffmpeg.exe の探索
    capture_command      ffmpeg.exe のコマンド組み立てとフレームの読み取り
    frame_buffer         フレームの間引きと直近の一定時間分の保持 (純粋なロジック)
    capture              キャプチャの常駐と撮影
    controller_state     押下状態の解釈・合成・レポート化 (純粋関数)
    controller           押下状態の保持とレポートの送信
    controller_bridge    controller_bridge.ps1 の常駐とレポートの送信
    commands             1 行コマンドの解釈と実行
    daemon               Unix ソケットでコマンドを受け付ける常駐デーモン (switch2-remote-daemon)
    client               デーモンにコマンドを送るクライアント (switch2-remote)
"""
