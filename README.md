# Memorant

[![Tests](https://img.shields.io/badge/tests-490%20passing-brightgreen)](https://github.com/tier4research/memorant/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Status: RC](https://img.shields.io/badge/status-rc-yellow.svg)](RELEASE_NOTES.md)

**Agent memory that knows where every fact came from — one SQLite file, zero dependencies.**

Memorant is an open-source Python library that stores an AI agent's long-term memory as
individual claims in a single SQLite database, each tagged with its source, a trust tier,
and a validity window — with no vector database, embedding model, or external service
required.

Memorant stores what your agent knows as individual **claims**. Each claim records its
source, how far you trust that source, and the window of time it was true. When a fact
turns out to be wrong, you correct *that claim*: Memorant invalidates the old version,
links the two, and flags everything derived from it for review.

Memorant installs on a bare Python interpreter. No embedding model, no vector server, no GPU,
no 400 MB dependency tree — SQLite with FTS5 and the standard library.

```bash
pip install memorant

memorant init --db ./memorant.db
memorant add "Deploys go out Thursdays." --source manual --trust verified --db ./memorant.db
memorant search "deploy schedule" --min-trust verified --db ./memorant.db
```

## Why not just use a vector store?

For pure recall, use one — Memorant isn't trying to beat a vector index at similarity
search. It solves the problem that shows up *after* recall works: what to do when a
stored fact is wrong, stale, or was never reliable to begin with.

A vector store has one move for a bad fact: add a contradicting chunk and hope the
embedding wins. Nothing marks the old chunk as retracted. Nothing records that three
other memories were built on top of it. Nothing distinguishes "the user told me this"
from "the model guessed this at 2am."

Here's the same situation in Memorant:

```python
from memorant import MemorantStore

store = MemorantStore("memory.db")
store.init()

# The agent infers something. It's a guess, and it's stored as one.
guess = store.add_claim(
    "The user deploys on Fridays.",
    source_pointer="inferred:session-41",
    trust_tier="derived",
)

# Later, the user says otherwise. That's operator-tier truth.
store.correct_claim(guess, "The user deploys on Thursdays.")
```

After the correction:

- the Friday claim is invalidated, not deleted — the history stays auditable
- a `corrects` relation links old to new
- anything with `derived_from` pointing at the old claim is flagged for review
- background retrieval stops surfacing the retracted version immediately

That's the whole pitch. Everything below is detail.

---

## What a claim looks like

```json
{
  "id": "clm_7a3f",
  "content": "Deploys go out Thursdays.",
  "trust_tier": "operator",
  "source_type": "manual",
  "source_pointer": "chat:2026-03-04",
  "valid_from": "2026-03-04",
  "valid_until": null,
  "reinforcement": 3,
  "relations": [
    {"type": "corrects", "target": "clm_2b91"}
  ]
}
```

## Trust tiers

Four tiers, highest to lowest: `operator`, `verified`, `derived`, `untrusted`.

Trust is assigned by *provenance*, not by fact-checking. You declare a policy mapping
sources to tiers, and Memorant enforces it consistently everywhere afterward.

```python
from memorant import TrustPolicy

policy = TrustPolicy(rules=[
    {"source_type": "manual",     "tier": "verified"},
    {"source_type": "correction", "tier": "operator"},
])
```

That distinction matters in practice. Background context injection (*resonance* — an
FTS5 query run against the claim store on every turn, filtered to `operator` +
`verified`, returning the top N results) only pulls from the top two tiers. A guess
the model made three sessions ago stays searchable if you go looking for it, but it
never quietly reappears as though it were established fact.

---

## At a glance

| Feature | What it does |
|---------|-------------|
| Trust tiers | `operator` > `verified` > `derived` > `untrusted` — resonance auto-injects only top tiers |
| Field-aware redaction | Secret values replaced with `[REDACTED:...]` |
| Atomic dedup | Identical claims increment a reinforcement counter instead of duplicating |
| FTS5 scoring | Rank × log(reinforcement) — trust-filtered at query time |
| Temporal validity | `valid_from` / `valid_until` — query "as of" any date |
| Correction propagation | `correct_claim()` → invalidates old, creates new, records `corrects` relation — atomic |
| Relation tracking | `supersedes`, `corrects`, `derived_from` — full audit trail |
| Digest governance | `pending` → `promoted` / `rejected` — atomic promotion |
| Doctor contract | `doctor --json` → exit code 0/1/2, component status, check list |
| Hygiene reports | Stale claims, broken derivation chains, contradiction candidates |
| SQLite steward | Vendored schema migration manager — pre-migration integrity checks, canary-based recovery |
| Zero dependencies | Pure Python + stdlib + bundled steward |
| Optional encryption | SQLCipher support via `pip install memorant[encryption]` |
| Expectation Ledger | Behavioral contracts, deterministic evaluation, violation recording, fail-closed option |

---

## Quick start

```bash
# Install
pip install memorant

# CLI
memorant init --db ./memorant.db
memorant add "The user prefers concise technical summaries." --db ./memorant.db --source manual --trust verified
memorant search "technical summaries" --db ./memorant.db --min-trust verified
memorant resonate "How should I answer this?" --db ./memorant.db
memorant doctor --json --db ./memorant.db
```

```python
from memorant import MemorantStore, StoreConfig, TrustPolicy

policy = TrustPolicy(rules=[
    {"source_type": "manual", "tier": "verified"},
    {"source_type": "correction", "tier": "operator"},
])

store = MemorantStore("memorant.db", StoreConfig(trust_policy=policy))
store.init()

# Add a claim
cid = store.add_claim("The user prefers direct answers.", source_pointer="manual", trust_tier="operator")

# Search with trust filtering
results = store.search("user preference", min_trust="verified")
for r in results:
    print(f"[{r.trust_tier}] {r.score:.3f} | {r.content}")

# Resonance — claims surface on their own
context = store.resonate("What style should I use?", session_id="sess-1")

# Correct a claim — atomic, propagates
new_id = store.correct_claim(cid, "The user prefers thorough, evidence-backed answers.")

# Health check
store.doctor(json_output=True)
```

---

> **v1.0.0-rc.1** ships three coordinated tools that work together or standalone:
>
> - **Memorant** — long-term claim store with trust tiers, provenance, corrections, and temporal validity
> - **Context Tuner** — recoverable compression and token-budget control for long-running conversations (also [available separately](https://github.com/tier4research/hermes-context-tuner))
> - **Expectation Ledger** — behavioral contracts, run tracking, and violation evidence for agent governance

## Suite workflow

The three packages work together for end-to-end agent memory governance:

```python
from memorant import MemorantStore
from memorant.suite import MemoryCycle
from context_tuner import ContextTuner
from expectation_ledger import ExpectationLedger

cycle = MemoryCycle(
    memory=MemorantStore("memory.db"),
    tuner=ContextTuner("context.db"),
    ledger=ExpectationLedger("expectations.db"),
)

prepared = cycle.prepare(
    "What should I remember before answering?",
    messages=[{"role": "user", "content": "Long conversation..."}],
)
```

This keeps short-term compression (Context Tuner) separate from trusted long-term memory (Memorant), with the Expectation Ledger governing both.

---

## Memorant-Ontology

The `memorant-ontology` package adds automatic entity and relation extraction from
claims. It runs as a background worker that processes claims through an LLM,
extracting structured entities (people, projects, tools, concepts) and the
relationships between them.

**Important:** the core memorant store *never* calls a model. `memorant-ontology` is
an optional background worker — it's opt-in and runs off the hot path.

```bash
pip install memorant-ontology
memorant-ontology --db ./memorant.db init
memorant-ontology --db ./memorant.db worker --limit 10 --rpm 30
```

Key features:
- **Entity extraction** — identifies people, places, projects, concepts, tools, organizations, and dates from claim text
- **Relationship discovery** — builds a knowledge graph of how entities relate
- **Trust propagation** — entities inherit trust tiers from source claims
- **Contradiction detection** — flags claims that conflict with existing knowledge
| **Cost control** — per-claim cost tracking with daily hard stop ($5/day default) |



## Comparison

| | Memorant | Mem0 | Zep | pgvector / Chroma |
|---|---|---|---|---|
| Runs fully local | ✓ | partial | partial | ✓ |
| Zero required dependencies | ✓ | ✗ | ✗ | ✗ |
| Provenance per fact | ✓ | | | ✗ |
| Trust tiering on retrieval | ✓ | | | ✗ |
| Atomic correction + propagation | ✓ | | | ✗ |
| Query "as of" a date | ✓ | | ✓ | ✗ |
| No LLM call on the memory path | ✓ | ✗ | | ✓ |

Trust tiering on the retrieval path is rare, and I haven't found another zero-dependency system that does it. Some cells above are empty because I haven't verified them — corrections welcome via PR.

Memorant also ships as a pluggable storage backend for [MemPalace](https://github.com/MemPalace/mempalace) (v3.5+). See [MemPalace integration](#mempalace-integration) below.

---

## MemPalace integration

As a MemPalace storage backend, every write lands in the Memorant claim store — trust-tiered, deduplicated, with full provenance:

```bash
pip install "mempalace[memorant]"
mempalace mine ~/projects/myapp --backend memorant   # or MEMPALACE_BACKEND=memorant
```

Migrate existing palaces:

```bash
mempalace repair --mode migrate-to-memorant
```

This replaces the retired `scripts/patch_mcp_for_memorant.py` hack. See [RELEASE_NOTES.md](RELEASE_NOTES.md).

---

## Encryption

Optional SQLCipher support:

```bash
pip install memorant[encryption]
```

```python
from memorant import MemorantStore, StoreConfig
store = MemorantStore("encrypted.db", StoreConfig(encryption_key="your-strong-passphrase"))
```

Fail-closed: wrong key → can't open. No key → standard SQLite. Zero overhead unless you opt in.

---

## Evaluation

No comparative benchmark against vector-based memory systems yet — planned before v1.0.
The project's 490 unit tests (90%+ coverage on migration, correction, trust, and
redaction paths) verify correctness, but they say nothing about whether claim-based
memory produces better answers than vector RAG in practice. A 50-question benchmark
with deliberately poisoned stale facts is the next priority.

Pull requests and discussion are welcome.

---

## Project status

**Release candidate** (v1.0.0-rc.1). APIs are stable; expect minor adjustments before 1.0.
490 tests, 90%+ coverage on the migration, correction, trust, and redaction paths.
Not yet benchmarked against vector-based memory systems.
Deferred to v1.1: embedding backend, advanced policy config, repair workflows.

---

## FAQ

### What is Memorant?
Memorant is an open-source Python library that stores AI agent memory as individual claims in a single SQLite file, each with provenance, a trust tier, and a validity window. It runs entirely locally and requires no external services.

### Does Memorant need a vector database or an embedding model?
No. Memorant uses SQLite's built-in FTS5 full-text index for retrieval. There are no required pip dependencies, no embedding model, and no GPU.

### How is Memorant different from Mem0, Zep, or Letta?
Those systems focus on storing and retrieving memory at scale, generally backed by a vector store and often a hosted service. Memorant focuses on the trustworthiness of each stored fact — where it came from, whether it's still valid, and what happens to downstream memories when it turns out to be wrong.

### Can you delete or correct a wrong memory in Memorant?
Yes. `correct_claim()` invalidates the original claim, creates the corrected version, records a `corrects` relation between them, and flags any claim derived from the original for review — atomically, in one transaction.

### What are trust tiers?
Four levels — `operator`, `verified`, `derived`, `untrusted` — assigned by provenance policy rather than by fact-checking. Background context injection draws only from the top two tiers, so a model's earlier guess stays searchable but never resurfaces as established fact.

### Does Memorant work offline?
Yes. The core library never makes a network call and never invokes an LLM. The optional `memorant-ontology` worker does call a model, but it's opt-in and runs off the hot path.

### What Python versions does Memorant support?
Python 3.10 and later. `pip install memorant` installs no transitive dependencies.

### Is Memorant production-ready?
It's a release candidate (v1.0.0-rc.1) with 490 passing tests and 90%+ coverage on the migration, correction, trust, and redaction paths. APIs are stable with minor changes expected before 1.0. It has not yet been benchmarked against vector-based memory systems.

---

## License

Apache License 2.0. See `LICENSE` and `NOTICE.md`.
