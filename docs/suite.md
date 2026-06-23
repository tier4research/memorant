# Memorant Suite

The Memorant suite separates agent memory into three responsibilities:

- Memorant stores trusted long-term claims with provenance, trust tiers,
  correction history, and retrieval diagnostics.
- Context Tuner keeps short-term conversation context inside model limits while
  preserving recoverability.
- Expectation Ledger tracks behavioral contracts, run checks, and violation
  evidence.

This separation prevents compressed summaries, untrusted observations, and
agent behavior rules from collapsing into one opaque memory bucket.

## Memory Cycle

The default cycle is:

1. Search Memorant for trusted claims relevant to the user message.
2. Compress conversation history only if it exceeds budget.
3. Search Expectation Ledger for active expectations relevant to the run.
4. Call the model with the selected context.
5. Record new claims, compression recovery records, and expectation evaluations
   through their own package APIs.

The `memorant.suite.MemoryCycle` helper demonstrates this flow without requiring
callers to adopt a framework.
