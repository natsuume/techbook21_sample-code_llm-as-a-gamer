"""Switch 2 のゲームを、型付きの判断を返すモデル (速い層) と Claude Code の advisor で遊ぶパッケージ。

1 つのプロセス (switch2-agent) が、キャプチャー、判断のループ (Pipeline)、押下状態の送信 (PadOutput)、
advisor (headless の Claude Code) を持つ。映像の受け取りと入力の送信には、第2章のパッケージ switch2_remote を使う。

モジュール:
    image_metrics          画像の縮小・鮮明度・差分・JPEG への変換 (純粋な関数)
    frame_history          時刻つきのフレームの履歴
    capture_with_history   第2章の Capture に、フレームの履歴と鮮明度の計算を足したもの
    frame_selection        要求に載せるフレームの選び方 (等間隔の選択、意味のあるフレームの判定と保管)
    questions              Decision Model に送る問いの一覧
    safety                 Decision Model と advisor に毎回入れる安全のための規則
    game_notes             ゲームの情報の 4 ファイル (操作方法・攻略情報・進行状況・戦略) の用意と読み取り
    decision_request       Decision Model への要求の組み立てと、JPEG の符号化の使い回し
    decision_models        Decision Model (OpenAI Decisions API と TypeSafe Jev) の呼び出し
    action_decision        問いごとの確率から、押下状態・短押し・スティック・advisor の呼び出しを決める
    pad_output             押下状態と短押しを合わせたレポートの送信
    operation_log          応答と送った操作の記録
    advisors               advisor (headless の Claude Code) の呼び出し
    run_log                実行のログ (JSON Lines)
    pipeline               判断のループ
    run                    起動 (switch2-agent)
"""
