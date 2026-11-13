"""Switch 2 のゲームを、型付きの判断を返すモデル (速い層) と Claude Code の advisor で遊ぶパッケージ。

1 つのプロセス (switch2-agent) が、キャプチャー、判断のループ (Pipeline)、押下状態の送信 (PadOutput)、
advisor (headless の Claude Code) を持つ。映像の受け取りと入力の送信には、第2章のパッケージ switch2_remote を使う。

モジュール:
    image_metrics          画像の縮小・鮮明度・差分・JPEG への変換 (純粋な関数)
    frame_history          時刻つきのフレームの履歴
    capture_with_history   第2章の Capture に、フレームの履歴と鮮明度の計算を足したもの
    capture_watcher        キャプチャーが止まったことを別のスレッドで見張り、押下状態を中立に戻す
    frame_selection        advisor に渡すフレームの選び方 (等間隔の選択、意味のあるフレームの判定と保管)
    questions              Decision Model に送る問いの一覧 (選択肢の問いと確率の問い)
    safety                 Decision Model と advisor に毎回入れる安全のための規則
    game_notes             ゲームの情報の 4 ファイル (操作方法・攻略情報・目標と進行状況と戦略・問題の解決の助言) の用意と読み取り
    decision_request       Decision Model への要求の組み立てと、JPEG の符号化の使い回し
    advisor_images         advisor に渡す画像 (ラベル付きの JPEG) の組み立て
    decision_models        Decision Model (OpenAI Decisions API と TypeSafe Jev) の呼び出し
    action_decision        問いごとの答えから、押下状態・短押し・スティック・advisor の呼び出しを決める
    pad_output             押下状態と短押しを合わせたレポートの送信
    operation_log          応答と送った操作の記録
    advisor_roles          advisor の役割の一覧 (役割ごとの問い・書き込めるファイル・Skill)
    advisors               役割ごとの advisor (headless の Claude Code) の呼び出し
    run_log                実行のログ (JSON Lines)
    request_log            要求ごとの入出力のログ (JSON Lines と画像。--save-requests か --record-dashboard のときだけ書く)
    dashboard_records      ダッシュボードが読む記録 (フレームの判定・送ったレポート) の行の組み立て (純粋な関数)
    record_writer          上限つきのキューと専用の書き出しスレッドで、記録を 1 件ずつ書く
    dashboard_recorder     ダッシュボードが読む記録 (frames.jsonl・thumbs/・pad.jsonl) を書く (--record-dashboard のときだけ)
    pipeline               判断のループ
    run                    起動 (switch2-agent)
"""
