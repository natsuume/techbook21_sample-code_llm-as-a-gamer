"""画面の停滞の計測: 一定間隔のフレームの画面のハッシュから、画面が変わっていない秒数と新しい画面が出ていない秒数を
コードで測り、各層に渡す。

I/O を持たない部品:
    screen_hash            画面全体の知覚ハッシュ (dHash) とハミング距離
    screen_progress        ハッシュの並びから値を計算する (ScreenProgressTracker)
    progress_text          値を surface と middle に渡す文にする
    stuck_advice_trigger   新しい画面が出ていない秒数から、advice の deep を要求するかを決める (deep のスケジューラーは
                           本体の画面の段階が続いた秒数にも同じ規則を当てる)
ファイルを読み書きする部品:
    progress_state         値 (ProgressState) と progress/state.json の読み書き
    progress_loop          frames.jsonl に足された一定間隔のフレームを読み、値を計算して progress/state.json に書くループ
"""
