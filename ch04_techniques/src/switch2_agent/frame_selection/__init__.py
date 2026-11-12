"""Decision Model への要求に載せるフレームの選び方。

モジュール:
    base                選び方の型 (Protocol)
    even_interval       直近の区間のフレームから等間隔に選ぶ
    meaningful_frames   鮮明度の谷で場面を区切り、各場面の代表のフレームを残す
    request_frames      1 回の要求に載せるフレーム (直近の区間 + 過去の場面) を選ぶ
"""
