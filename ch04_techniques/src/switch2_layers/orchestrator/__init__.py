"""Orchestrator 型: 主ループの Claude Code が画面を見て判断し、各ターンの最後に JSON で操作・マクロ・監視を書く。
その JSON を読み、操作の列・マクロ・コードの手順を実行し、「画面の条件 → 操作」の Jev の監視を 1 つ動かす。
委任型 (--mode delegate) は、監視の代わりに委任を書く。主ループはリアルタイムの場面の操作を、意味のある行動 (マクロ) の
選択肢とタスク・注意事項とともに Jev に委任し、Jev が返すか主ループが止めたら操作権が主ループに戻る。

主ループ (Claude Code を stream-json の入出力で開いたままにし、使用量がしきい値を超えたらセッションを入れ替える):
    main_loop                   主ループ (OrchestratorMainLoop)。セッションの開始・ターンの送受信・入れ替え・停止
run --mode orchestrator と run --mode delegate の組み立て (app と cli が使う):
    orchestrator_assembly       主ループと OrchestratorController を作ってつなぎ、監視のループと委任のループのスレッドの
                                本体を持つ (app.LayersApp が Orchestrator 型と委任型の start で使う)
    initial_instruction         人の初期指示を --instruction か --instruction-file から読む (cli が使う)
I/O を持たない部品:
    decision_types              主ループが書く JSON を読んだ結果の型 (操作・マクロ・監視・委任の指示)
    decision_parser             応答の本文の最後の ```json のブロックを読み、確かめて Decision にする (parse_decision)
    json_values                 JSON の値 (数・整数・秒数) の確かめ
    procedures                  コードの手順 (tap_repeat・walk) の引数の確かめと、操作の列への展開
    macro_registry              実行中に登録するマクロの一覧
    monitor_questions           監視が Jev に問う choice 1 問と要求の state、答えから当たった条件を決める規則
    delegate_questions          委任のループが Jev に問う choice 1 問と要求の state、答えから選んだ選択肢を決める規則
    orchestrator_events         主ループが次の入力を作るときに取り出す出来事の型 (委任の状況を含む)
    claude_session_command      claude -p の起動の引数・--settings・許すツールと拒むルール・子プロセスの環境変数
    orchestrator_instructions   変わらない指示 (--append-system-prompt。監視型と委任型の版) と、引き継ぎの依頼の文
    turn_input                  1 ターンの入力のテキストと画像の選び方 (画像と notes/ の一覧を読む関数も置く)
    stream_messages             stream-json の行の読み方 (使用量・自動の要約の印) と、標準入力に送る行の形
操作の送信・Jev の呼び出し・ファイルとプロセスを扱う部品:
    action_executor             procedure と macro を展開した操作の列を、1 つずつ PadOutput に送る
    monitor_loop                監視の Jev への要求 1 回と、今の監視の状態 (失敗が続いた回数、once・every の扱い)
    monitor_record              orchestrator/monitor.jsonl の 1 行とその書き込み
    delegate_loop               委任の Jev への要求 1 回と、今の委任の状態 (直近の行動、「操作を返す」と失敗の続いた回数)
    delegate_record             orchestrator/delegate.jsonl の 1 行とその書き込み
    orchestrator_controller     Decision を当て、操作の実行と監視 (委任型では委任) を 1 つのループで動かす
                                (OrchestratorController)
    parent_skill_names          実行ディレクトリーの親のディレクトリーのスキルの名前を集める (skillOverrides で off にする)
    skill_copy                  本体の操作のスキルと、notes/skills/ のゲームの内容のスキルを .claude/skills/ に写す
    claude_stream_session       claude の子プロセス 1 つの起動・標準入力への書き込み・標準出力の読み取り・終了
    session_record              orchestrator/sessions.jsonl と orchestrator/turns.jsonl の行の形と書き込み
"""
