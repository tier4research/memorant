# Memorant

[![Tests](https://github.com/tier4research/memorant/actions/workflows/tests.yml/badge.svg)](https://github.com/tier4research/memorant/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](RELEASE_NOTES.md)

**Memorant** is a local-first memory substrate for AI agents. Instead of storing raw
chat logs and hoping vector search surfaces the right thing, it records memory as
**claim units** — small, individually addressable statements with provenance and a
temporal validity window — and exposes them through three deliberately separate
surfaces:

1. **Standing State** — a compact, human-reviewable digest of what should stay true
   across turns. Rewrites go through a create → diff → promote/reject review step, so
   the agent's always-on memory never changes silently.
2. **Resonance** — sanitized, ambient cues injected into an agent's per-turn context.
   Internal-only by design: leak guards strip SQL, embeddings, tokens, and tracebacks
   before anything reaches the model.
3. **Query** — explicit, evidence-backed lookup for direct memory questions, with each
   result carrying its source pointer and score.

It runs on a single local SQLite file, has **zero required dependencies**, and is
built to be called from any Python agent that can add context before an LLM call.

## Why it exists

Most agent memory is one of two things: vector search over chat transcripts, or a
hand-curated profile file. Both struggle with the part that matters most in
long-running agents — keeping memory *correct over time*. Memorant focuses on that gap:

- **Claim-level units**, not opaque document chunks — so a single fact can be
  corrected, superseded, or invalidated without rewriting everything around it.
- **Temporal validity** (`valid_from` / `valid_until`) — queries can ask "what was
  true as of this date," and retracted facts drop out of retrieval without being deleted.
- **Correction propagation** — invalidating a source fact cascades to the claims
  derived from it.
- **Reviewable Standing State** — persistent memory is rewritten through an explicit,
  diffable promotion step, not in place.
- **Leak-guarded resonance** — ambient context is sanitized before injection.
- **Local-first** — one SQLite file with full-text search (FTS5). Nothing leaves the machine.

## Install

From the repository:

```bash
git clone https://github.com/tier4research/memorant.git
cd memorant
python -m pip install .
```

For an editable development install, use `python -m pip install -e ".[test]"`.

Retrieval today is deterministic and lexical (FTS5 + token overlap), with no model
calls on the memory path. A pluggable embedding backend is on the roadmap; see
**Project status**.

## Quick start (CLI)

```bash
memorant init --db ./memorant.db
memorant add "The user prefers concise technical summaries." --db ./memorant.db --source demo
memorant resonate "How should I answer this task?" --db ./memorant.db
memorant digest create --db ./memorant.db
memorant digest list --db ./memorant.db
memorant digest promote 1 --db ./memorant.db --state ./standing_state.md --yes
```

Promotion and rejection require an explicit `--yes` — Standing State never changes by accident.

## Python usage

```python
from pathlib import Path
from memorant import MemoryPalace

palace = MemoryPalace(Path("memorant.db"))
palace.init()
palace.add_claim("The user prefers direct answers.", source_pointer="manual")
print(palace.resonate("Answer style?"))
```

## Using it with your agent

Memorant sits in the pre-LLM step of an agent loop and returns a small, sanitized
context string. Two thin adapters ship in the box:

- **Hermes** — `memorant.adapters.hermes.pre_llm_call_context(...)` returns a
  `{"context": ...}` packet suitable for a `pre_llm_call` plugin hook. A runnable
  example plugin lives in `examples/hermes_plugin/`.
- **OpenClaw-style agents** — `memorant.adapters.openclaw.build_context_packet(...)`
  returns a plain string for generic context assembly.

Both adapters emit **internal-only** context and never expose raw SQL, debug output,
or embedding data. The adapters are deliberately tiny — if your framework isn't one
of these, copying one is a few lines.

**For local-first / self-hosted agents:** there's no service to run, no external API
on the memory path, and no network egress. The store is a file you own, retrieval is
deterministic, and the same database works from the CLI, the Python API, and an agent
hook simultaneously (WAL mode).

## Safety & privacy

- The package ships **no memory data**. Runtime databases are created by you.
- Resonance output is sanitized and intended for model context, not direct display to users.
- The `resonance_log` table retains recent turn context locally for debugging; it
  stays on your machine in your SQLite file.

## Project status

Alpha (`v0.1.0-alpha.x`). The schema, retrieval, correction propagation, digest
review, temporal filtering, and leak guards are tested. The optional embedding backend
and a full LLM-based claim-extraction pipeline are not yet implemented. APIs may change.

## Lineage & credit

Memorant is an independent implementation, but its design is directly indebted to the
open-source memory-palace ecosystem for AI agents. See `NOTICE.md` for the upstream
projects credited and the specific concepts borrowed.

## License

Apache License 2.0. See `LICENSE` and `NOTICE.md`.

## Contributing and security

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for
the development workflow. Please report security-sensitive problems using the
private process in [SECURITY.md](SECURITY.md), not a public issue.
