# Memorant

[![Tests](https://github.com/tier4research/memorant/actions/workflows/tests.yml/badge.svg)](https://github.com/tier4research/memorant/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Status: RC](https://img.shields.io/badge/status-rc-yellow.svg)](RELEASE_NOTES.md)

**Memorant is a local-first memory suite for AI agents: trusted long-term
claims, recoverable context compression, and expectation tracking.**

Most assistants either forget everything between sessions or remember too much —
hauling around stale, contradictory notes that quietly push answers in the wrong
direction. Memorant stores what your agent learns as individual claims, each tagged
with provenance, an explicit trust tier, and a temporal validity window. When
something changes, you correct that single fact — so the agent's memory stays
accurate the longer it runs.

**v1 (release candidate):** ships three coordinated projects:

- **Memorant:** a trustworthy long-term claim store with provenance, trust tiers,
  corrections, temporal validity, retrieval diagnostics, and memory hygiene.
- **Context Tuner:** recoverable context compression and token-budget control for
  long-running agent conversations.
- **Expectation Ledger:** local-first behavioral contracts, run tracking, and
  violation evidence for agent governance.

The core Memorant store adds trust tiers (operator > verified > derived >
untrusted), field-aware secret redaction, atomic deduplication, FTS5-scored
retrieval, relation tracking (supersedes/corrects/derived_from), a `doctor --json`
health contract, and a vendored SQLite steward for safe schema migrations — all
with zero required dependencies and a single local SQLite file.

## GitHub page descriptions

Use these descriptions for the GitHub About text and any package landing pages:

- **Memorant:** Local-first trusted memory for AI agents: claims, provenance,
  trust tiers, corrections, and retrieval diagnostics.
- **Context Tuner:** Recoverable context compression and token-budget control for
  long-running AI agent conversations.
- **Expectation Ledger:** Local-first contract and violation ledger for AI agents,
  with expectation search, run tracking, and evidence.

## v1 at a glance

### Trust tiers
Every claim gets a trust tier: `operator` (manually curated), `verified`
(cross-referenced), `derived` (computed from other claims), or `untrusted`
(default). Resonance auto-injects only operator + verified claims — untrusted
claims stay available for explicit search but never leak into background context.

### Field-aware redaction
API keys, tokens, passwords, and private keys are redacted with surgical
precision — only the secret portion is replaced with `[REDACTED:...]`,
leaving surrounding text intact. Benign terms like "SQL", "debug", and
"tokenization" are explicitly preserved.

### Atomic deduplication
`INSERT ... ON CONFLICT DO UPDATE` ensures concurrent writes never create
duplicates. Identical claims increment a reinforcement counter instead of
creating new rows — safe under multiple readers/writers (WAL mode).

### FTS5 retrieval with scoring
Composite scoring: FTS5 rank × log-scaled reinforcement bonus. Stable tie-break
by claim ID. Trust-filtered at query time — you decide the minimum tier per search.

### Retrieval diagnostics and hygiene
`search_debug` exposes raw FTS rank, normalized relevance, reinforcement,
recency, and final score. `hygiene` reports stale claims, broken derivation
chains, duplicate groups, simple contradiction candidates, and untrusted claims
that deserve review.

### Relation tracking
`supersedes`, `corrects`, and `derived_from` tables with enforced foreign keys.
Correction propagation is transactional — invalidate an old claim, create the new
one, and record the relation in one atomic operation.

### Digest governance
Digest states are explicit `TEXT` enums: `pending`, `promoted`, `rejected`.
Promotion uses temp-file → flush → atomic replace → state update — no partial writes.

### Doctor contract
Every Tier 4 component implements `doctor --json`:
```json
{"component": "memorant", "status": "healthy", "checks": [...], "timestamp": "..."}
```
Exit codes: 0 = healthy, 1 = degraded, 2 = unhealthy.

### SQLite steward (vendored)
Dependency-free schema migration manager bundled in `_vendor/`:
pre-migration integrity check, timestamped backup, ordered transactional
migrations, canary-based interrupted-migration recovery.

---

## How Memorant compares

| Capability | Memorant v1 | Holographic (Hermes) | MemPalace (upstream) | Cloud memory |
|---|---|---|---|---|
| Runs entirely local | ✓ | ✓ | ✓ | ✗ |
| Zero required dependencies | ✓ | — | — | ✗ |
| Trust tiers (operator/verified/derived/untrusted) | ✓ | — | — | — |
| Field-aware secret redaction | ✓ | — | — | — |
| Atomic dedup (INSERT ON CONFLICT) | ✓ | — | — | — |
| Temporal validity (valid_from/valid_until) | ✓ | — | ✓ | — |
| Queries respect "as of" date | ✓ | — | — | — |
| Correction propagation (supersedes/corrects/derived_from) | ✓ | — | — | — |
| Reviewable digests (diff → approve/reject) | ✓ | — | — | — |
| Doctor --json health contract | ✓ | — | — | — |
| SQLite steward (safe migrations) | ✓ | — | — | — |
| FTS5 composite scoring | ✓ | ✓ | ✓ | ✗ |
| No LLM on the memory path | ✓ | ✓ | ✓ | ✗ |
| Single-file SQLite | ✓ | ✓ | ✓ | — |

---

## Install

```bash
git clone https://github.com/tier4research/memorant.git
cd memorant
pip install .
```

For development: `pip install -e ".[test]"`.

## Quick start (CLI)

```bash
memorant init --db ./memorant.db
memorant add "The user prefers concise technical summaries." --db ./memorant.db --source demo --trust verified
memorant search "technical summaries" --db ./memorant.db --min-trust verified
memorant resonate "How should I answer this?" --db ./memorant.db
memorant stats --db ./memorant.db
memorant doctor --json --db ./memorant.db
memorant backup --db ./memorant.db
```

## Python usage

```python
from memorant import MemorantStore, StoreConfig, TrustPolicy

policy = TrustPolicy(rules=[
    {"source_type": "manual", "tier": "verified"},
    {"source_type": "correction", "tier": "operator"},
])

store = MemorantStore("memorant.db", StoreConfig(trust_policy=policy))
store.init()

# Add a claim with explicit trust tier
cid = store.add_claim("The user prefers direct answers.", source_pointer="manual", trust_tier="operator")

# Search with trust filtering
results = store.search("user preference", min_trust="verified")
for r in results:
    print(f"[{r.trust_tier}] {r.score:.3f} | {r.content}")

# Resonance (auto-injects only operator + verified claims)
context = store.resonate("What style should I use?", session_id="sess-1")

# Correct a claim
new_id = store.correct_claim(cid, "The user prefers thorough, evidence-backed answers.")

# Health check
store.doctor(json_output=True)
```

## Using it with your agent

Memorant runs in the step just before your agent calls its model, and hands back
a small, sanitized block of context. The Hermes adapter is one line:

```python
from memorant.adapters.hermes import pre_llm_call_context

result = pre_llm_call_context(user_message, session_id="sess-abc")
# → {"context": "[MEMORANT_RESONANCE]\n- ..."}
```

A complete Hermes plugin example ships in `examples/hermes_plugin/`.

## Suite workflow

Use the three packages together when an agent needs both continuity and
governance:

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

This keeps short-term compression separate from trusted long-term memory:
compression can produce review candidates, but it does not automatically promote
summaries into Memorant claims.

## Encryption (optional)

Memorant supports SQLCipher encryption for the local database. It's off by
default — standard SQLite is used unless you opt in.

```bash
pip install memorant[encryption]
```

```python
from memorant import MemorantStore, StoreConfig

store = MemorantStore(
    "encrypted.db",
    StoreConfig(encryption_key="your-strong-passphrase")
)
```

- **Fail-closed**: if `encryption_key` is set but `sqlcipher3` isn't installed,
  Memorant raises `ImportError` rather than silently writing in plaintext.
- **Wrong key → can't open**: SQLCipher rejects incorrect keys at the database
  level. No fallback to unencrypted mode.
- **No key → standard SQLite**: backward-compatible, zero overhead.

We recommend **full-disk encryption** (BitLocker/FileVault/LUKS) as the first
line of defense regardless. SQLCipher adds defense-in-depth for scenarios where
the database file is exfiltrated separately from the machine's keyring.

## Project status

Release candidate (`v1.0.0-rc.1`). Trust tiers, field-aware redaction, atomic
dedup, FTS5 scoring, relation tracking, digest governance, doctor contract,
SQLite steward, and optional SQLCipher encryption are all implemented and tested
(68 tests, 4 skipped pending sqlcipher3 install; 90%+ coverage target on
migration/correction/trust/redaction paths). APIs may still see minor adjustments
before stable v1.0.0.

Deferred to v1.1: full embedding backend, advanced policy configuration, polished
repair/uninstall workflows.

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
