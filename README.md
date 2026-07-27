# Memorant

[![Tests](https://github.com/tier4research/memorant/actions/workflows/tests.yml/badge.svg)](https://github.com/tier4research/memorant/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Status: RC](https://img.shields.io/badge/status-rc-yellow.svg)](RELEASE_NOTES.md)

**Your agent's memory shouldn't be a search index.**

Most "AI memory" systems are just vector databases with a chat wrapper. You ask, they fetch, they return. No provenance. No trust. No way to tell a remembered fact from a hallucinated one. And when the model gets something wrong, you can't fix it — you can only add *more* text and hope it drowns out the mistake.

Memorant is different. It stores what your agent knows as **individual claims** — each one tagged with where it came from, how trustworthy it is, and when it's valid. Claims surface on their own when they're relevant (not just when queried). When a fact changes, you correct *that claim* — and the correction propagates atomically to everything that depends on it.

The result is an agent that remembers *better* the longer it runs, instead of accumulating noise.

> **v1.0.0-rc.1** ships three coordinated tools that work together or standalone:
>
> - **Memorant** — long-term claim store with trust tiers, provenance, corrections, and temporal validity
> - **Context Tuner** — recoverable compression and token-budget control for long-running conversations (also [available separately](https://github.com/tier4research/hermes-context-tuner))
> - **Expectation Ledger** — behavioral contracts, run tracking, and violation evidence for agent governance

---

## The problem with agent memory today

Most agent memory isn't memory — it's *retrieval*. You dump everything into a vector store, query by similarity, and hope the right chunk comes back. The model doesn't distinguish between a confirmed fact and a guess. Stale information sits forever. Corrections don't propagate. And no one can audit what the agent actually *knows* versus what it's just statistically predicting.

- **No trust model.** Every piece of memory is treated equally. A user's offhand comment carries the same weight as a verified setting. There's no way to say "this is confirmed" vs "this is a guess."
- **No corrections.** The model can't unlearn. You can add contradictory information, but the old wrong fact is still there. How does the agent decide which to trust?
- **No governance.** Want to enforce "this agent must never store API keys"? You'd need to add that logic yourself, in your own application layer. There's no contract system.
- **No provenance.** Where did a piece of memory come from? Was it observed, inferred, or explicitly told? Good luck figuring that out from a vector embedding.
- **No temporal awareness.** When was this fact learned? Is it still valid? Memory systems that don't track time can't age out stale information.

## How Memorant fixes it

Memorant treats memory like a **knowledge base**, not a search index. Every claim is a first-class entity with metadata that governs how it's used:

| Problem | How Memorant solves it |
|---------|----------------------|
| **No trust model** | Every claim has an explicit tier: `operator`, `verified`, `derived`, or `untrusted`. Resonance auto-injects only operator + verified — untrusted claims stay available for search but never leak into background context. |
| **No corrections** | `correct_claim()` atomically invalidates the old claim, creates the corrected one, and records the `corrects` relation. Supersession chains let you trace the full history. |
| **No governance** | Expectation Ledger stores behavioral contracts and records violations with evidence. Deterministic expectations can even reject violating writes (fail-closed mode). |
| **No provenance** | Every claim carries a `source_pointer`, `source_type`, and timestamp. You know exactly where each fact came from. |
| **No temporal awareness** | `valid_from` / `valid_until` on every claim. Queries can be scoped to "what was true at this point in time." |
| **No dedup** | Identical claims auto-merge via `INSERT ON CONFLICT`, incrementing a reinforcement counter. Same fact from multiple sources = stronger signal, not duplicate rows. |
| **No audits** | `doctor --json` exposes health checks. `hygiene` reports stale claims, broken derivation chains, and contradiction candidates. Every operation leaves a trace. |

---

## What makes Memorant unique

**Trust tiers.** No other open-source memory system has them. You can literally say "this claim was verified by a human, that one was inferred by the model, and those three were overheard in a noisy conversation — treat them accordingly." Resonance respects these tiers automatically. A claim has to earn its way into your agent's active context.

**Zero dependencies.** Memorant has *no* required pip dependencies. Not numpy, not sentence-transformers, not chromadb. It's a single SQLite file with FTS5. You can `pip install memorant` on a bare Python install and it works. No GPU. No vector server. No 400MB dependency tree.

**Dependency-free doesn't mean primitive.** FTS5 ranking, atomic dedup, trust-tiered retrieval, field-aware secret redaction, relation tracking (supersedes/corrects/derived_from), temporal scope, hygiene reports, a doctor/health contract, and safe schema migrations — all in a zero-dependency package.

**Correction propagation.** When you correct a claim, the old one is invalidated, the new one is created, and all relations are updated in a single atomic operation. Anything that `derived_from` the old claim is flagged for review. This is how memory should work: fix one fact, and the system knows the downstream effects need attention.

**The Expectation Ledger.** Not just memory — governance. Define behavioral contracts ("no storage of secrets without redaction", "every API call must be logged"), evaluate them deterministically, and record violations with evidence. Your agent doesn't just remember; it's accountable.

**First-class MemPalace backend.** Memorant ships as a pluggable storage backend for [MemPalace](https://github.com/MemPalace/mempalace) (v3.5+). Every drawer write lands in the claim store with trust tiers and provenance, while MemPalace keeps its own vector index for search speed. Best of both worlds.

---

## At a glance

| Feature | What it does |
|---------|-------------|
| Trust tiers | `operator` > `verified` > `derived` > `untrusted` — resonance auto-injects only top tiers |
| Field-aware redaction | Secret values replaced with `[REDACTED:...]`; benign terms like `tokenization` preserved |
| Atomic dedup | `INSERT ON CONFLICT` — identical claims increment a reinforcement counter instead of duplicating |
| FTS5 scoring | Rank × log(reinforcement) — stable tie-break by claim ID; trust-filtered at query time |
| Temporal validity | `valid_from` / `valid_until` — query "as of" any date |
| Correction propagation | `correct_claim()` → invalidates old, creates new, records `corrects` relation — atomic |
| Relation tracking | `supersedes`, `corrects`, `derived_from` — full audit trail |
| Digest governance | `pending` → `promoted` / `rejected` — atomic promotion with temp-file + state update |
| Doctor contract | `doctor --json` → exit code 0/1/2, component status, check list |
| Hygiene reports | Stale claims, broken derivation chains, contradiction candidates, untrusted claims needing review |
| SQLite steward | Vendored schema migration manager — pre-migration integrity checks, backups, canary-based recovery |
| Zero dependencies | Pure Python + stdlib + bundled vendored steward |
| Optional encryption | SQLCipher support via `pip install memorant[encryption]` — fail-closed, wrong key = can't open |
| MemPalace backend | `--backend memorant` — writes go through trust tiers + provenance; vector index in sidecar (no memorant deps on chromadb) |
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

## Memorant-Ontology

The `memorant-ontology` package adds automatic entity and relation extraction from
claims. It runs as a background worker that processes claims through an LLM,
extracting structured entities (people, projects, tools, concepts) and the
relationships between them.

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
- **Cost control** — per-claim cost tracking with daily hard stop ($5/day default)

236 tests passing across the extraction, worker, queue, retriever, and normalizer modules.

---

## Comparison

| Capability | Memorant | Holographic (Hermes) | MemPalace | Cloud memory |
|---|---|---|---|---|
| Runs entirely local | ✓ | ✓ | ✓ | ✗ |
| Zero required dependencies | ✓ | — | — | ✗ |
| Trust tiers | ✓ | — | — | — |
| Field-aware secret redaction | ✓ | — | — | — |
| Atomic dedup | ✓ | — | — | — |
| Temporal validity | ✓ | — | ✓ | — |
| Query "as of" date | ✓ | — | — | — |
| Correction propagation | ✓ | — | — | — |
| Reviewable digests | ✓ | — | — | — |
| Doctor/health contract | ✓ | — | — | — |
| Expectation Ledger | ✓ | — | — | — |
| FTS5 composite scoring | ✓ | ✓ | ✓ | ✗ |
| No LLM on memory path | ✓ | ✓ | ✓ | ✗ |
| Single-file SQLite | ✓ | ✓ | ✓ | — |

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

## Project status

**Release candidate** (v1.0.0-rc.1). 308 tests passing, 90%+ coverage on migration/correction/trust/redaction paths. MemPalace backend integration ships from the MemPalace side. APIs stable with minor adjustments expected before v1.0.0.

Deferred to v1.1: full embedding backend, advanced policy configuration, polished repair workflows.

---

## License

Apache License 2.0. See `LICENSE` and `NOTICE.md`.
