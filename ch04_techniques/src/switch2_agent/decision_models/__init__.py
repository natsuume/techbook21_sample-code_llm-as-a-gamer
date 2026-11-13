"""Decision Model (問いごとに、選択肢ごとの確率か、真である確率を返すモデル) の呼び出し。

どちらも公式 SDK を使わず、httpx で JSON を送る。要求の形は純粋な関数で組み立て、テストで固定する。

モジュール:
    base            Decision Model の型 (Protocol) と、結果・例外の型
    decisions_api   OpenAI Decisions API (選択肢の問いは choice 型、確率の問いは predicate 型)
    jev             TypeSafe Jev (System One、選択肢の問いは choice 型、確率の問いは Noul 型)
"""
