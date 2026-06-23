from context_tuner import ContextTuner
from expectation_ledger import ExpectationLedger
from memorant import MemorantStore
from memorant.suite import MemoryCycle


def test_memory_cycle_composes_three_projects(tmp_path):
    memory = MemorantStore(tmp_path / "memory.db")
    memory.add_claim(
        "Agents should answer with source-backed evidence.",
        source_pointer="manual",
        trust_tier="verified",
    )
    tuner = ContextTuner(tmp_path / "context.db")
    ledger = ExpectationLedger(tmp_path / "ledger.db")
    ledger.add_expectation(
        "Agent must cite evidence for factual claims.",
        trust_tier="verified",
    )

    cycle = MemoryCycle(memory=memory, tuner=tuner, ledger=ledger)
    result = cycle.prepare(
        "Please provide source-backed evidence.",
        messages=[
            {"role": "system", "content": "Be factual."},
            {"role": "user", "content": "source backed evidence " * 40},
        ],
        min_trust="verified",
    )

    assert result.claims
    assert "MEMORANT_RESONANCE" in result.resonance
    assert result.compressed is not None
    assert result.expectations
