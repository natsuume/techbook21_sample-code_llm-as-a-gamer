"""Claude Code が Nintendo Switch 2 をプレイするための補助コマンド (switch2-harness) と指示ファイルを提供するパッケージ。

第2章のデーモン (switch2-remote-daemon) に 1 行コマンドを送って撮影とボタン入力を行い、
すべてのコマンドを作業ディレクトリーの records/events.jsonl に記録する。
先行研究の手法を「要素 (feature)」として有効/無効を切り替えられるようにし、
要素の有効/無効で変わるのは、CLAUDE.md の節と、エージェントが使えるサブコマンドと、
エージェント用のサブコマンドの出力だけにする。
記録 (records/) は要素の有効/無効によらず常に取る。

モジュール:
    cli               argparse でサブコマンドを解釈し、各処理へ振り分ける (switch2-harness)
    daemon_client     デーモンに 1 行コマンドを送り、応答の 1 行を返す
    harness_config    harness.json の型 HarnessConfig と読み書き
    workspace         作業ディレクトリーのパスの組み立てと、init での作成
    workspace_pin     エージェント用のサブコマンドを、run が起動した作業ディレクトリーに固定する
    features          要素の一覧 (Feature と FEATURES) と名前の検証
    instructions      有効な要素から CLAUDE.md の本文を組み立てる
    press_args        press の引数の検証 (純粋関数)
    frame_files       撮影した PNG の連番のパスを決める
    event_log         records/events.jsonl への追記と読み込み、イベントの型
    screen_change     2 枚の画像の変化の割合と PNG の読み込み
    session_report    イベントから実験の集計を作り、表示用のテキストにする
    action_history    行動履歴の要素: look の後に表示する、直近の行動 (look と press) の行を作る
    agent_launcher    claude -p の起動引数の組み立てと、時間を区切った起動
    process_tree      SIGKILL で止める claude の子孫の控えと、pid の再利用を見分けた停止
    signal_guard     run の間の SIGTERM・SIGHUP の例外化と、後片付けの間の割り込みの無視
    commands          look / press / status / finish / init / report / run の処理本体
"""
