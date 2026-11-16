"""Orchestrator 型の部品: 主ループの Claude Code が書く JSON を読み、操作の列・マクロ・コードの手順を実行し、
「画面の条件 → 操作」の Jev の監視を 1 つ動かす。

I/O を持たない部品:
    decision_types            主ループが書く JSON を読んだ結果の型 (操作・マクロ・監視の指示)
    decision_parser           応答の本文の最後の ```json のブロックを読み、確かめて Decision にする (parse_decision)
    json_values               JSON の値 (数・整数・秒数) の確かめ
    procedures                コードの手順 (tap_repeat・walk) の引数の確かめと、操作の列への展開
    macro_registry            実行中に登録するマクロの一覧
    monitor_questions         監視が Jev に問う choice 1 問と要求の state、答えから当たった条件を決める規則
    orchestrator_events       主ループが次の入力を作るときに取り出す出来事の型
操作の送信・Jev の呼び出し・ファイルの書き込みを行う部品:
    action_executor           procedure と macro を展開した操作の列を、1 つずつ PadOutput に送る
    monitor_loop              監視の Jev への要求 1 回と、今の監視の状態 (失敗が続いた回数、once・every の扱い)
    monitor_record            orchestrator/monitor.jsonl の 1 行とその書き込み
    orchestrator_controller   Decision を当て、操作の実行と監視を 1 つのループで動かす (OrchestratorController)
"""
