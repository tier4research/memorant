# Memorant v0.1.0-alpha.1

Memorant's first public alpha introduces a local-first memory substrate for AI
agents. It represents memory as individually addressable claims with provenance,
temporal validity, correction propagation, reviewable standing-state digests,
and leak-guarded ambient retrieval.

This release is intended for developers experimenting with long-running agents.
The API and schema may change before a stable release.

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

## Install

```bash
git clone https://github.com/tier4research/memorant.git
cd memorant
python -m pip install .
memorant --help
```

## Compatibility

- Python 3.10 or newer
- SQLite with FTS5 enabled (included in standard CPython builds)
- No required runtime dependencies or network services
- Licensed under Apache-2.0 with explicit patent terms

## Not included yet

- Private memory data and personal facts.
- sqlite-vec production embedding table.
- Full LLM claim extraction pipeline.

## Known limitations

- Retrieval is lexical; an embedding backend is not implemented yet.
- Claim extraction from unstructured conversations is not included.
- This is alpha software and is not yet covered by a backwards-compatibility promise.

## Verification

- Six automated tests pass.
- Source distribution and universal Python wheel build successfully.
- The release tree contains no runtime memory databases, credentials, or private facts.

Full documentation is available in the [README](README.md) and
[architecture overview](docs/ARCHITECTURE.md).
