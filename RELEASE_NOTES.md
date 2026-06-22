# v0.1.0-alpha.1

Initial public-package extraction of Memorant.

## Included

- SQLite schema for claim units, FTS, digests, resonance log, arcs, and standing facts.
- Local deterministic lexical retrieval fallback; optional embedding backend can be added later.
- Claim insertion and FTS indexing.
- Temporal validity filters.
- Correction propagation from facts/claims to derived claim units.
- Standing State digest create/list/show/diff/promote/reject CLI.
- Sanitized resonance output with `[MEMORANT_RESONANCE]` blocks.
- Hermes and OpenClaw adapter helpers.
- Tests for schema, retrieval, correction propagation, digest review, temporal filtering, and leak guards.

## Not included yet

- Private memory data and personal facts.
- sqlite-vec production embedding table.
- Full LLM claim extraction pipeline.
- Published GitHub remote. This release is a local repo/artifact ready for review and publication.
