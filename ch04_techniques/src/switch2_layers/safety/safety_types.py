"""安全の監視の型: 画面を確かめる部品 (SafetyChecker) の Protocol と答え (SafetyAnswer)、要求の段階 (SafetyPhase)。"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class SafetyPhase(StrEnum):
    """安全の確かめの段階。値は safety.jsonl の phase に書く。"""

    START = "start"  # 実行を始めるときの確かめ (最初のフレームが届いた後に 1 回)
    MONITOR = "monitor"  # 監視のスレッドの定期の確かめ


@dataclass(frozen=True)
class SafetyAnswer:
    """1 回の確かめの答え。

    probabilities   問いの名前 → その画面である確率 (0〜1)。safety_questions.SAFETY_QUESTION_NAMES のすべてを含む
    usage           API の usage (Jev の応答の本文の usage をそのまま)。無ければ None
    """

    probabilities: Mapping[str, float]
    usage: object | None = None


class SafetyChecker(Protocol):
    """画面が安全の問いのそれぞれの画面である確率を返すもの。

    実装は safety.jev_safety.JevSafetyChecker (TypeSafe Jev に問う) と safety.fake_safety.FakeSafetyChecker (フェイク)。
    """

    def check(self, jpeg: bytes) -> SafetyAnswer:
        """画面の画像 jpeg (JPEG 1 枚) の答えを返す。失敗したら例外を出す (安全の監視が失敗として数える)。"""
        ...
