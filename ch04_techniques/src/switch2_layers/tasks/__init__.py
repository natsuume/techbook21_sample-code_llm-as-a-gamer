"""短期タスクのキュー: Advisor 型の短期タスクを場面ごとのキューで持ち、追加・完了・取り消しの要求・取り消しと、
キューに入ってからの続いた秒数の計算、ファイルへの書き出し、出来事の記録を受け持つ。

I/O を持たない部品:
    task_queue    場面ごとのキューの計算 (順番・上限・続いた秒数。SceneTaskQueues)
ファイルを読み書きする部品:
    task_files    場面のキューの状態の notes/scenes/<場面ID>/tasks.json と tasks.md の書き込み、保存済みの tasks.json の読み込み、
                  タスクの行き先の JSON の形との変換
    task_events   出来事の記録 tasks.jsonl の行と、記録の中で最も大きいタスクの ID の連番
    task_board    キューの操作とファイル・記録の書き込みを 1 つのロックの中で行う部品 (TaskBoard) と、保存済みの
                  実行ディレクトリーからの作り直し
"""
