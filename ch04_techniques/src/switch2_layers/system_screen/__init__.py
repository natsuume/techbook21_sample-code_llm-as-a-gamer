"""本体の画面の見分け: 本体の UI の部分だけを残した見本の画像と、今のフレームとの照合の結果。

    system_screen_references   見本の名前・照合の選択肢の説明の文・画像のファイル名 (SYSTEM_SCREEN_REFERENCES) と、
                               見本の画像の読み込み (load_reference_images)
    screen_judgment            照合の確率から結果を決める関数 (judge_system_screen) と、最新の照合の結果を持つもの
                               (LatestSystemScreen)
    system_operation_runner    本体の画面の middle の操作の列を、照合の結果を確かめながら送るもの
                               (SystemOperationRunner)
    run_phase                  run の段階 (準備・プレイ・復帰・片付け) と、段階の移り変わりの判定 (PhaseTracker)
    system_screen_loop         段階が準備・復帰・片付けの間、本体の画面の middle を呼んで操作の列を送るループ
                               (SystemScreenLoop)
    references/                見本の画像 (<名前>.jpg)
照合は、安全の監視 (safety.safety_monitor) が要求に見本の画像を入れて Jev に問い、結果を LatestSystemScreen に渡す。
照合の結果では実行を止めない (止める条件は safety.safety_judge の 3 つの問いだけで決める)。照合の結果は、段階の
移り変わりの判定 (run_phase) に使う。
"""
