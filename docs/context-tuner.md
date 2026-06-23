# Context Tuner

Context Tuner provides recoverable context compression and token-budget control
for long-running AI agent conversations.

It is designed for the short-term side of agent memory: trimming, compressing,
and recovering conversation history without confusing that compressed summary
with trusted long-term memory. The default tokenizer is dependency-free, and
callers can use `compress_debug()` to inspect budget pressure, protected message
counts, degradation reasons, and recovery IDs.

## GitHub Description

Recoverable context compression and token-budget control for long-running AI
agent conversations.

## When To Use It

- A conversation is too large for the target model window.
- System and recent messages must be preserved exactly.
- Older turns can be summarized but must remain recoverable.
- Compression quality needs diagnostics for tests or production monitoring.

## Relationship To Memorant

Context Tuner does not automatically write summaries into Memorant. Compressed
content can become a review candidate, but trusted long-term claims should still
flow through Memorant's provenance, trust, and correction policy.
