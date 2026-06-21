# Smarter Memory Palace

**Smarter Memory Palace** is a local-first memory substrate for AI agents. It turns raw memory into reviewable, temporally valid claim units with three usable surfaces:

1. **Standing State** — a compact digest of what should stay true across turns.
2. **Associative Surface** — ambient resonance cues that can be injected into an agent's per-turn context without exposing raw memory internals.
3. **Deep Query** — explicit evidence-backed lookup for direct memory questions.

It is designed for Hermes Agent, OpenClaw-style agents, and any Python agent that can add context before an LLM call.

This is an **alpha release artifact** extracted from Andre's private smarter memory palace. Private data and personal paths are intentionally absent.

## Why this exists

Most agent memory is either vector search over chat logs or manually curated profile facts. Smarter Memory Palace adds missing governance primitives: claim-level memory units, temporal validity, correction propagation, digest review before Standing State rewrites, ambient resonance with leak guards, local SQLite storage, and adapter boundaries for Hermes/OpenClaw.

## Install locally

```bash
pip install -e .
```

Optional embedding backend:

```bash
pip install -e '.[embeddings]'
```

## Quick start

```bash
smp init --db ./demo-palace.db
smp add "The user prefers concise technical summaries." --db ./demo-palace.db --source demo
smp resonate "How should I answer this task?" --db ./demo-palace.db
smp digest create --db ./demo-palace.db --state ./standing_state.md
smp digest list --db ./demo-palace.db
smp digest promote 1 --db ./demo-palace.db --state ./standing_state.md --yes
```

## Python usage

```python
from pathlib import Path
from smarter_memory_palace import MemoryPalace

palace = MemoryPalace(Path("palace.db"))
palace.init()
claim_id = palace.add_claim("The user prefers direct answers.", source_pointer="manual")
print(palace.resonate("Answer style?"))
```

## Agent adapters

- `smarter_memory_palace.adapters.hermes` exposes a `pre_llm_call_context(...)` helper suitable for a Hermes plugin hook.
- `smarter_memory_palace.adapters.openclaw` exposes a generic `build_context_packet(...)` helper for OpenClaw-style context assembly.

Both adapters emit internal-only context and avoid raw SQL/debug/embedding data.

## Safety / privacy defaults

The package does **not** ship memory data. Runtime databases are user-created. Resonance output is sanitized and intended for model context, not direct display.

## License and lineage

MIT licensed. See `NOTICE.md` for original Memory Palace / MemPalace lineage and credit notes.
