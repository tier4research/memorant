"""Suite helpers that compose Memorant, Context Tuner, and Expectation Ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from context_tuner import ContextTuner, CompressionDebug
from expectation_ledger import Expectation, ExpectationLedger

from .core import Claim, MemorantStore


@dataclass(frozen=True)
class MemoryCycleResult:
    resonance: str
    claims: list[Claim]
    compressed: CompressionDebug | None
    expectations: list[Expectation]


class MemoryCycle:
    """Coordinate the suite's pre-model memory and policy steps."""

    def __init__(
        self,
        *,
        memory: MemorantStore,
        tuner: ContextTuner | None = None,
        ledger: ExpectationLedger | None = None,
    ) -> None:
        self.memory = memory
        self.tuner = tuner
        self.ledger = ledger

    def prepare(
        self,
        user_message: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        session_id: str = "",
        claim_limit: int = 3,
        expectation_limit: int = 5,
        min_trust: str = "verified",
    ) -> MemoryCycleResult:
        """Run retrieval, optional compression, and expectation lookup."""
        claims = self.memory.search(
            user_message,
            limit=claim_limit,
            min_trust=min_trust,
        )
        resonance = self.memory.resonate(
            user_message,
            session_id=session_id,
            limit=claim_limit,
        )
        compressed = self.tuner.compress_debug(messages) if self.tuner and messages else None
        expectations = (
            self.ledger.search(
                user_message,
                limit=expectation_limit,
                min_trust=min_trust,
            )
            if self.ledger
            else []
        )
        return MemoryCycleResult(
            resonance=resonance,
            claims=claims,
            compressed=compressed,
            expectations=expectations,
        )
