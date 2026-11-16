"""安全のための部品: 3 つの層に渡す規則の文と、HOME メニュー・オンライン・通報などの画面を見つけて止める安全の監視。

    safety_rules       3 つの層に毎回渡す安全のための規則の文 (SAFETY_RULES)
    safety_questions   安全の監視が Jev に問う 5 つの問いと、要求の state
    safety_types       確かめる部品 (SafetyChecker) の Protocol と答え・段階の型
    jev_safety         TypeSafe Jev に問う SafetyChecker (JevSafetyChecker)
    fake_safety        すべての確率を 0 として返す SafetyChecker (FakeSafetyChecker)
    safety_assembly    --safety に合わせて SafetyChecker を作る
    safety_judge       確率と失敗から、止めるかと止まる理由を決める純粋な関数とクラス
    safety_record      safety.jsonl の 1 行
    safety_monitor     実行を始めるときの確かめと、surface とは別のスレッドの監視 (SafetyMonitor)
安全の監視は surface の実装に依らずに組み立て、app.LayersApp が起動する。
"""
