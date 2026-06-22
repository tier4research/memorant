# Notices and lineage

Memorant is an independent implementation, but its design is directly indebted to the
open-source memory-palace ecosystem for AI agents — in particular the idea of a
persistent, searchable, auditable memory store, and temporal validity windows that
mark facts as ended rather than deleting them.

Projects credited:

- **MemPalace** — https://github.com/mempalace/mempalace — for deterministic,
  no-LLM-on-the-memory-path retrieval and a temporal knowledge graph in local SQLite
  with per-fact validity windows.
- **AGI-is-going-to-arrive/Memory-Palace** — https://github.com/AGI-is-going-to-arrive/Memory-Palace
  — for the searchable, auditable cross-session store and write-time governance.

These GitHub repositories are the only sources this project credits; other similarly
named domains and packages are unaffiliated.

This repository is a clean, sanitized release artifact. It does not include private
memory data, personal facts, diary content, API keys, or machine-specific runtime
databases.

If future work copies code directly from an upstream memory-palace repository,
preserve that upstream project's license headers and license terms in the copied
files. Memorant currently credits lineage/inspiration and ships a sanitized,
independent implementation of the architecture: claim units, validity windows,
Standing State, digest promotion/review, correction propagation, and agent adapters.
