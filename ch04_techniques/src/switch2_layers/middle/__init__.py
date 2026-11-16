"""middle 層: 型と Protocol (middle_types)、フェイク (fake_middle)、入力の作成 (middle_input_builder)、
出力の書き込み (middle_output_writer)、middle/calls.jsonl の行 (middle_call_record)、1 回分の処理とループ (middle_loop)、
マップの記録 middle/map_records.jsonl の行 (map_records)、場所の名前の一覧 (place_names)、
短期タスクリストの最初のタスクの文とその文になった時刻 (task_progress)。

gpt-6-luna の実装 (luna_middle) は次の部品でできている: 変わらない指示 (luna_instructions)、出力のスキーマと読み取り
(luna_output_schema)、要求の組み立て (luna_request)、現在地からの経路の節 (route_section)、surface と操作の記録の要約
(record_summary)、HTTP と応答の読み取り (luna_client)、usage と概算の費用 (luna_usage)。

本体の画面 (HOME メニューなど) で操作の列を決める middle は次の部品でできている: 型 (system_screen_types)、変わらない指示と
目的ごとの手順の文 (system_screen_instructions)、出力のスキーマと読み取り (system_screen_schema)、要求の組み立て
(system_screen_request)、Protocol と gpt-6-luna の実装とフェイク (system_screen_middle)、middle/system_calls.jsonl の行
(system_call_record)。列を送る部品は system_screen.system_operation_runner にある。"""
