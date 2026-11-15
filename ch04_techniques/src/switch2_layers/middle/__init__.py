"""middle 層: 型と Protocol (middle_types)、フェイク (fake_middle)、入力の作成 (middle_input_builder)、
出力の書き込み (middle_output_writer)、middle/calls.jsonl の行 (middle_call_record)、1 回分の処理とループ (middle_loop)、
マップの記録 middle/map_records.jsonl の行 (map_records)、場所の名前の一覧 (place_names)。

gpt-6-luna の実装 (luna_middle) は次の部品でできている: 変わらない指示 (luna_instructions)、出力のスキーマと読み取り
(luna_output_schema)、要求の組み立て (luna_request)、surface と操作の記録の要約 (record_summary)、HTTP と応答の読み取り
(luna_client)、usage と概算の費用 (luna_usage)。"""
