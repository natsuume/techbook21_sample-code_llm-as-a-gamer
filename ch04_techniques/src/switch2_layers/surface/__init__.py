"""surface 層: 型と Protocol (surface_types)、フェイク (fake_surface)、middle/state.json の読み込み
(middle_state_reader)、surface.jsonl の行 (surface_record)、1 回分の処理とループ (surface_loop)。

TypeSafe Jev を呼ぶ実装 (jev_surface) と、その部品:
    jev_client           Jev の HTTP の呼び出しと応答の読み取り
    surface_questions    問いの定義と、state に入れる役割と規則の説明
    action_rules         答えから操作などを決める規則
    box_targets          box で取る対象の一覧 (box_targets.md) の読み込み
    box_tracker          box を追う部品 (Protocol と NCC の実装)
    box_tracking_state   対象ごとの追跡の状態と、問い直す対象の選び方
    surface_assembly     起動引数に合わせた surface の部品一式の組み立て
"""
