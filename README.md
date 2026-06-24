# Memorant

[![Tests](https://github.com/tier4research/memorant/actions/workflows/tests.yml/badge.svg)](https://github.com/tier4research/memorant/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](RELEASE_NOTES.md)

**Memorant gives your AI agent a memory that works more like a mind than a database.**

Most agent memory is a search index or a profile file — you ask, it fetches,
nothing surfaces on its own. Memorant is different. It stores memory as individual
claims with provenance and temporal validity, but the real shift is in how that
memory *behaves*:

- **Resonance, not retrieval.** Background context is quietly surfaced based on
  what's happening right now — not because you asked, but because it's relevant.
  Like how your name catches your ear across a noisy room.
- **Arcs.** Related memories thread into ongoing narratives with their own lifecycle
  — active, dormant, closed. Not just "facts about X" but the *story of X*.
- **Emotional charge.** Memories carry emotional markers that influence how quickly
  and often they surface. A memory you felt strongly about comes back faster.
- **Consolidation.** Raw experiences get extracted, deduplicated, and distilled into
  a standing state — the way sleep consolidates short-term experiences into
  long-term understanding.
- **Habituation.** The same memory doesn't dominate every conversation. The system
  learns to quiet repeated signals, surfacing them only when genuinely relevant.

Local-first, zero cloud dependencies, single SQLite file. Your agent's memory
stays accurate the longer it runs.

Memorant also provides three operational interfaces for working with memory:

1. **Standing State** — the agent's reliable "what's true right now" notes: a short,
   readable summary it carries into every conversation. It never changes silently —
   updates are proposed, shown to you as a diff, and saved only when you approve them.
2. **Resonance** — gentle background reminders, quietly added to the model's input as
   it answers so it stays on track. Built-in safety filters strip out anything internal
   (raw database queries, tokens, error traces) before it can reach the model.
3. **Query** — direct, evidence-backed lookups: ask the memory a question and every
   answer cites its source and a relevance score, so you can always see *why* the agent
   knows something.

## Why it exists

Today, agent memory usually comes in one of two flavors: search over old chat
transcripts, or a single hand-edited profile file. Both fall apart on the thing that
matters most for an agent you run for weeks or months — **keeping its memory correct
as the facts change.** That's the gap Memorant is built around:

- **Facts, not transcripts** — memory is stored as individual claims, so one can be
  corrected, replaced, or retired without disturbing everything around it.
- **Memory with a sense of time** (`valid_from` / `valid_until`) — every fact knows
  when it became true and when it stopped. You can ask "what was true back then?" and
  get the right answer; retired facts stop showing up but are never destroyed.
- **Corrections that ripple** — fix one source fact and anything derived from it
  updates automatically.
- **No silent rewrites** — the agent's always-on memory only changes through an
  explicit, reviewable diff-and-approve step.
- **Safe by default** — background context is sanitized before it can reach the model.
- **Yours, on your machine** — one local SQLite file with fast full-text search (FTS5).
  Nothing leaves your computer.

## How memory actually works

Most agent memory systems are retrieval engines: you ask a question, they search
for matching text, they return results. Memorant starts from a different premise
— that memory isn't something you search, it's something that *surfaces*.

### Resonance

Instead of waiting for a query, Memorant maintains a low-level background hum.
On every turn, the current context is compared against the memory store, and
relevant claims surface gently — not as answers, but as context the model can use
to stay grounded. This is *resonance*: context-triggered, not query-triggered.

The system tracks what surfaced and how recently, applying cooldown penalties so
the same strong memory doesn't dominate every conversation — the way your brain
habituates to a repeated sound.

### Arcs (narrative threads)

Isolated facts are useful but incomplete. Human memory organizes experience into
*ongoing stories* — threads that connect related moments over time. Memorant's
arcs do this: a claim about your project's architecture, a session where you
changed direction, a decision that resolved an open question — these can all
belong to the same arc.

Arcs have a lifecycle. Active arcs are being revisited. Dormant arcs haven't been
touched in a while. Closed arcs are resolved stories. The system manages these
transitions automatically, surfacing active arcs more often and letting closed
ones rest.

### Emotional charge

Every claim can carry emotional markers — not as metadata tags, but as signals
that influence retrieval. Emotionally charged memories surface faster and with
higher salience, just as they do in human cognition. The system doesn't care
*what* the emotion is — it uses the presence of emotional charge as a relevance
signal.

### Consolidation

Raw memory (diary entries, session logs, conversation fragments) gets processed
through a consolidation pipeline that extracts individual claims, deduplicates
them, detects emotional markers, links them to arcs, and regenerates a standing
state — a distilled summary of what's true right now. This happens periodically,
the way sleep consolidates short-term experiences into long-term memory.

### Novelty detection

The system actively looks for *surprise* — claims that are relevant to the
current context but haven't been consolidated into the standing state yet. High
novelty means "this matters and it's not already understood." This is the
system's equivalent of the hippocampal novelty signal — the brain's way of
flagging new information that needs attention.

## Who it's for

- **Builders of long-running or personal agents** that need memory which survives
  across sessions and stays trustworthy instead of drifting.
- **Privacy-conscious and self-hosted setups** — there's no server to send data to;
  the memory is a file you own and can inspect.
- **Anyone tired of vector-search guesswork** who wants memory they can read, audit,
  and correct by hand.

## How Memorant compares

Most agent memory falls into one of a few buckets. Here's how Memorant is different.

| Capability | Memorant | Holographic (Hermes built-in) | MemPalace (upstream) | Mem0 / Zep (cloud) | Vector search over chat logs |
|---|---|---|---|---|---|
| **Runs entirely local** | ✓ | ✓ | ✓ | ✗ | ✓ |
| **Zero required dependencies** | ✓ | — | — | ✗ | — |
| **Claim-level memory units** | ✓ | — | — | — | ✗ |
| **Temporal validity windows** (valid_from / valid_until) | ✓ | — | ✓ | — | ✗ |
| **Queries respect "as of" date** (what was true then?) | ✓ | — | — | — | ✗ |
| **Correction propagation** (fix source → derived claims update) | ✓ | — | — | — | ✗ |
| **Reviewable memory rewrites** (diff → approve/reject) | ✓ | — | — | — | ✗ |
| **Leak-guarded context injection** (sanitized before model sees it) | ✓ | — | — | — | ✗ |
| **No LLM on the memory path** (deterministic retrieval) | ✓ | ✓ | ✓ | ✗ | ✓ |
| **Single-file SQLite** (inspect with any SQLite tool) | ✓ | ✓ | ✓ | ✗ | — |

### Memorant vs. Holographic (Hermes)

Holographic is Hermes Agent's built-in memory provider. It uses HRR phase vectors
over FTS5 and stores facts in local SQLite. It's a solid default for Hermes users.

Memorant adds several things Holographic doesn't currently do: **temporal validity**
(every fact knows when it was true), **correction propagation** (fix one fact and
derived claims update), **reviewable Standing State** (diffs shown before promotion),
and **leak-guarded resonance** (sanitized before model injection). If you need
memory that stays correct over weeks or months of active use, Memorant fills the
governance gap.

### Memorant vs. MemPalace (upstream)

Memorant is directly inspired by the MemPalace ecosystem and borrows its best ideas:
temporal validity on individual facts, local SQLite storage, and deterministic
retrieval with no model on the memory path.

Memorant extends that foundation with **claim units** (smaller, individually
addressable memory atoms), **correction propagation** that cascades from source
facts to derived claims, **digest governance** with reviewable promote/reject,
and **agent adapters** that make the memory surface directly usable inside a
running agent loop. If MemPalace is the filing cabinet, Memorant adds the review
process, the correction chain, and the direct wire into the agent's ear.

### Memorant vs. cloud memory (Mem0, Zep, etc.)

Cloud memory services handle extraction, dedup, and search for you — but your
agent's memory lives on someone else's machine. Memorant is intentionally local:
one file, your disk, no network egress on the memory path. It also keeps retrieval
deterministic (no LLM deciding what to recall) so you can audit and reproduce
exactly what the agent remembered and why.

### Memorant vs. vector search over chat transcripts

Searching old chat logs works fine for "what did we talk about last Tuesday." It
breaks down for "is this fact still true?" because there's no mechanism for
correction or temporal validity — old wrong statements and new corrections look
the same to a vector index. Memorant stores memory as structured claims with
validity windows and correction tracking, so the agent can tell the difference
between "this was said" and "this is currently true."

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

Wiring it in is deliberately simple: Memorant runs in the step just before your agent
calls its model, and hands back a small, sanitized block of context. Two ready-made
adapters ship in the box:

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

### A note on storage encryption

The local SQLite database is stored as a plaintext file. If you discuss sensitive
topics with your agent — health information, finances, private correspondence —
those facts will be written to the database in the clear.

**The first and most important defense is full-disk encryption** (BitLocker on
Windows, FileVault on macOS, LUKS on Linux). With full-disk encryption enabled,
the database file is ciphertext whenever the machine is powered off or locked.
Full-disk encryption is built into every modern operating system and protects
against the most common real-world threat: someone gaining physical access to a
powered-off or locked device.

Application-level SQLite encryption (e.g., SQLCipher) is on the roadmap as an
optional feature. It would add defense-in-depth for the narrower scenario where
the database file is exfiltrated separately from the machine's keyring — but it
cannot protect against an attacker who already has access to a running, unlocked
system. We recommend full-disk encryption regardless of any future app-level
encryption support, and we commit to documenting the threat model honestly as
the project evolves.

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
