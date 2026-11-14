"""系列情報。速い層の要求に、直近の数回分の履歴より長い時間の流れ (同じ操作を続けた時間、画面が変わっていない時間、
ゲームの情報や小目標がいつ変わったか、advisor をいつ呼んだか) を文で足す。

節 (builder.SECTION_NAMES) ごとに部品が分かれていて、起動引数 --timeline で使う節を選べる:
    actions        操作のまとめ (action_runs)
    screen         画面の変化 (screen_change)
    notes          ゲームの情報の 4 ファイルの更新時刻 (note_changes)
    advisor_calls  役割ごとに advisor を最後に呼んだ時刻 (advisor_calls)
    subgoals       小目標ごとの、立てた時刻と状態が最後に変わった時刻 (subgoals)
builder.TimelineBuilder が、選んだ節の文をつないで 1 つの文にする。
"""
