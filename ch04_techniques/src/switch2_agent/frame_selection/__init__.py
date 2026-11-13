"""advisor に渡すフレームの選び方。

Decision Model への要求には、フレームの履歴の最新の 1 枚だけを載せる (Pipeline.run_once)。

モジュール:
    base                選び方の型 (Protocol)
    even_interval       直近の区間のフレームから等間隔に選ぶ
    meaningful_frames   鮮明度の谷で場面を区切り、各場面の代表のフレームを残す
    advisor_frames      advisor に渡すフレーム (フレームの履歴から等間隔に選んだもの + それより前の過去の場面) を選ぶ
"""
