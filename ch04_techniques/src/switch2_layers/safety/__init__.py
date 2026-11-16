"""安全のための部品: 3 つの層に渡す規則の文と、オンライン・購入・本体の設定などの画面を見つけて止める安全の監視。

    safety_rules       3 つの層に毎回渡す安全のための規則の文 (SAFETY_RULES)
    safety_questions   安全の監視が Jev に問う 3 つの問いと本体の画面の照合の問い、要求の state
    safety_types       確かめる部品 (SafetyChecker) の Protocol と答え・段階の型
    jev_safety         TypeSafe Jev に問う SafetyChecker (JevSafetyChecker)
    fake_safety        止める条件に当たらず、どの見本の画面でもない答えを返す SafetyChecker (FakeSafetyChecker)
    safety_assembly    --safety に合わせて SafetyChecker を作る (jev では照合の見本の画像を 1 回読む)
    safety_judge       確率と失敗から、止めるかと止まる理由を決める純粋な関数とクラス
    safety_record      safety.jsonl の 1 行
    safety_monitor     実行を始めるときの確かめと、surface とは別のスレッドの監視 (SafetyMonitor)
安全の監視は surface の実装に依らずに組み立て、app.LayersApp が起動する。同じ要求で本体の画面を見本の画像と照合し
(system_screen)、結果を記録して監視の外に渡す。照合の結果では止めない。
"""
