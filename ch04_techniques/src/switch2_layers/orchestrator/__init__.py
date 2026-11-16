"""Orchestrator 型: 主ループの Claude Code が画面を見て判断し、各ターンの最後に JSON で操作・マクロ・監視を書く。

主ループと操作の実行・監視の部品の間の型:
    decision_types              主ループが書く JSON を読んだ結果 (Decision・DecisionError と操作の型)
    orchestrator_events         操作の実行と監視の部品が主ループに渡す知らせ (OrchestratorEvents)
主ループ (Claude Code を stream-json の入出力で開いたままにし、使用量がしきい値を超えたらセッションを入れ替える):
    main_loop                   主ループ (OrchestratorMainLoop)。セッションの開始・ターンの送受信・入れ替え・停止
I/O を持たない部品:
    claude_session_command      claude -p の起動の引数・--settings・許すツールと拒むルール・子プロセスの環境変数
    orchestrator_instructions   変わらない指示 (--append-system-prompt) と、引き継ぎの依頼の文
    turn_input                  1 ターンの入力のテキストと画像の選び方 (画像と notes/ の一覧を読む関数も置く)
    stream_messages             stream-json の行の読み方 (使用量・自動の要約の印) と、標準入力に送る行の形
ファイル・プロセスを扱う部品:
    parent_skill_names          実行ディレクトリーの親のディレクトリーのスキルの名前を集める (skillOverrides で off にする)
    skill_copy                  本体の操作のスキルと、notes/skills/ のゲームの内容のスキルを .claude/skills/ に写す
    claude_stream_session       claude の子プロセス 1 つの起動・標準入力への書き込み・標準出力の読み取り・終了
    session_record              orchestrator/sessions.jsonl と orchestrator/turns.jsonl の行の形と書き込み
"""
