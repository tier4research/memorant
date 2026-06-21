# v0.1.0-alpha.1

Initial public-package extraction of Smarter Memory Palace.

## Included

- SQLite schema for claim units, FTS, digests, resonance log, arcs, and standing facts.
- Local deterministic lexical retrieval fallback; optional embedding backend can be added later.
- Claim insertion and FTS indexing.
- Temporal validity filters.
- Correction propagation from facts/claims to derived claim units.
- Standing State digest create/list/show/diff/promote/reject CLI.
- Sanitized resonance output with `[SMARTER_MEMORY_RESONANCE]` blocks.
- Hermes and OpenClaw adapter helpers.
- Tests for schema, retrieval, correction propagation, digest review, temporal filtering, and leak guards.

## Not included yet

- Private Andre/Miguel/Elle memory data.
- sqlite-vec production embedding table.
- Full LLM claim extraction pipeline.
- Published GitHub remote. This release is a local repo/artifact ready for review and publication.
