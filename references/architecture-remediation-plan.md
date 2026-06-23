# Architecture Remediation Plan — v1 Release Path

From audit Round 3 (GPT 5.5 on commit 112a453) and the v2 design items
accumulated across three audit cycles. Line-fix bugs were addressed at
371528a; this document covers the items that need architectural change.

---

## Quick Line Fixes (≤ 1 hour)

### L1 — keep_last_n=0 disables compression

**Bug:** Python `list[-0:]` returns the entire list, `list[:-0]` returns empty.
`chunk_messages(messages, keep_last_n=0)` retains all messages as "recent,"
preventing compression entirely.

**Fix:** Guard `keep_last_n` at entry points. If `keep_last_n` is 0, set it to
`None` or treat it as "keep none." Alternative: reject 0 explicitly and raise
ValueError with a message suggesting `keep_last_n=1`.

**Location:** `src/context_tuner/compressor.py` — `chunk_messages()` and
`compress_messages()`.

**Risk:** Low. Semantic edge case, not a structural flaw.

### L2 — Version strings still inconsistent

**Remaining:** `context_tuner.__version__` = `"1.0.0"` (should be
`"1.0.0-rc.1"`). Event emission sites in `memorant/core.py` still emit
`component_version="1.0.0"`.

**Fix:** Search `"1.0.0"` across source, replace with version constant or
`"1.0.0-rc.1"` where appropriate.

**Location:** `src/context_tuner/__init__.py`, `src/memorant/core.py` (event
sites), any remaining hardcoded version strings.

**Risk:** Low. Metadata cleanup.

---

## Architectural Items

### A1 — max_tokens enforcement: top-down budgeting

**Problem:** The current allocator is **per-chunk**. Each role-transition chunk
gets `(available / len(chunks)) * 4` characters, with a floor of 50. With many
small chunks (e.g., 10 alternating old messages producing 10 chunks of 50 bytes
each = 500 chars ≈ 125 tokens), the sum exceeds `max_tokens`. Setting
`within_budget=False` is metadata, not enforcement.

Two failed fix attempts confirm this resists line-level patching.

**Approach:** Replace per-chunk allocation with top-down budgeting.

1. **Measure headroom first.** Count tokens for system + recent messages
   (these are kept verbatim). Subtract from `max_tokens`. The remainder is the
   hard budget for all compressed chunks combined.

2. **Budget from the top.** Instead of giving each chunk a fixed byte
   allowance, give all chunks a shared token pool. Each chunk gets an equal
   *target*, but the summarizer is told the absolute cap. After all chunks
   are summarized, check the total. If over budget, truncate chunks
   proportionally (drop the oldest/lowest-priority chunks first, then trim
   individual chunk content).

3. **Last resort: hard truncation.** If proportional trimming still exceeds
   budget, truncate the assembled output to `max_tokens` tokens. This is
   lossy but guarantees the contract.

4. **Return signal.** `compress_messages()` must return a clear indicator
   when the budget was enforced through truncation (vs. naturally under
   budget). The caller can decide whether to accept degraded compression or
   raise an error.

**Estimated complexity:** Medium. Requires refactoring `_allocate_chunks()`
into a `_budgeted_compress()` that operates on the full chunk list with a
single token cap.

**Depends on:** Nothing. Can be done independently.

---

### A2 — System message ordering: index-based preservation

**Problem:** `chunk_messages()` separates system messages into their own list.
`compress_messages()` places system messages first, then compressed chunks,
then recent messages. However, if a system message originally appeared between
user and assistant messages (e.g., a mid-conversation system prompt update),
this reordering changes semantics.

Two failed fix attempts: the docstring was updated to claim ordering is
preserved, but the code still separates and reorders.

**Approach:** Track original positions.

1. **Tag messages with original indices** before splitting. `(index, msg)`
   tuples.

2. **Split by role for compression** (system messages don't need
   summarization but DO need position tracking).

3. **Reassemble by original index.** After compression, sort all messages
   (system, compressed, recent) by their original index before returning.

4. **Remove the system-msgs-separate assumption** from the assembly step.
   Currently `compressed = system + summarized + recent`. Change to `sorted(
   all_messages, key=lambda x: x._original_index)`.

**Estimated complexity:** Medium. Touches `chunk_messages()`,
`compress_messages()`, and the message data model. A 20-line refactor with
careful test coverage.

**Depends on:** Nothing.

---

### A3 — Multimodal end-to-end: make list-valued content a first-class citizen

**Problem:** Token counting now handles list-valued content, but three other
code paths still assume `msg["content"]` is a string:

1. **Recovery.save()** — `searchable = " ".join(m.get("content", "") ...)`
   joins list items, producing `TypeError: sequence item 0: expected str
   instance, list found`.

2. **Default summarizer** — when old multimodal messages become compression
   candidates, the summarizer receives a chunk where `content` may be a list.

3. **Redaction** — `source_pointer` redaction is now applied (Round 1 fix),
   but the redaction only operates on string content.

**Approach:** Add a `_extract_text(content)` helper used everywhere content
is consumed as a string.

```python
def _extract_text(content: str | list[dict] | Any) -> str:
    """Extract plain text from message content, handling multimodal format."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, dict) and item.get("type") == "image_url":
                parts.append("[image]")
            elif isinstance(item, dict) and item.get("type") == "audio":
                parts.append("[audio]")
        return " ".join(parts)
    return str(content)
```

**Touch points:**
- `count_message_tokens()` — already handles list, but confirm
- `RecoveryStore.save()` — line 165: use `_extract_text()`
- `default_summarize_chunk()` — use `_extract_text()` on each message
- `compress_messages()` — any content-joining path
- Any redaction/sanitization function that reads `content`

**Estimated complexity:** Medium-Low. The helper is trivial. The real work is
auditing every content-reading path and routing through the helper. ~8–12
sites.

**Depends on:** Nothing.

---

### A4 — Recovery corruption handling: `RecoveryCorruptionError`

**Problem:** `_safe_json_load()` returns `None` for malformed JSON. Callers
that expect a list get `None`, which either crashes downstream (`.append()`
on `None`) or silently produces empty results. Corrupt data is
indistinguishable from genuinely empty data.

**Approach:**

1. **Define `RecoveryCorruptionError(Exception)`** in a new
   `src/context_tuner/errors.py`.

2. **`_safe_json_load()` raises `RecoveryCorruptionError`** when the stored
   JSON is malformed but the row itself is structurally valid (has an ID,
   has the column). This lets callers distinguish "row doesn't exist" from
   "row exists but is corrupt."

3. **`list()` and `search()` catch `RecoveryCorruptionError`** per-row and
   either skip the corrupt row (logging a warning) or return a partial
   result with a `corrupted_ids: list[str]` field.

4. **`load()` re-raises** — if you ask for a specific recovery by ID and
   it's corrupt, you need to know.

**Estimated complexity:** Low. ~30 lines of new code, + error handling in 4
methods.

**Depends on:** Nothing.

---

### A5 — Recovery storage bounding: retention policy

**Problem:** Recovery saves full original + compressed transcripts on every
call. No pruning, no age limits, no size caps. A chatty agent produces
unbounded storage growth.

Two dimensions to bound:

1. **Per-session:** A single session's original + compressed transcript is
   stored once. Duplicate saves of the same session should be idempotent
   (already handled by `recovery_id` parameter).

2. **Cross-session:** The recovery_sessions table grows without bound.

**Approach:**

1. **Add `max_sessions` config** to `RecoveryStore.__init__()` (default:
   1000). On `save()`, after inserting, count rows. If over `max_sessions`,
   delete the oldest `N - max_sessions` sessions (by `created_at`).

2. **Add `max_age_days` config** (default: 90). On `init()` or `save()`,
   delete sessions older than the cutoff. This prevents a long-running
   process from accumulating years of recovery data.

3. **Make both configurable** — agents with different retention needs
   (compliance vs. ephemeral) should be able to tune.

4. **Do NOT auto-prune without config.** Default to keeping everything
   unless the user sets a limit. The default is "unbounded" — explicit opt-in
   to pruning. This avoids surprise data loss.

**Estimated complexity:** Medium-Low. ~40 lines of SQL + config plumbing.

**Depends on:** Nothing.

---

### A6 — Connection lifecycle: explicit close or pool

**Problem:** 50+ sites use `with self.connect() as db:` which manages
transactions but does not explicitly close the underlying SQLite connection.
Python's `sqlite3` connections are reference-counted — they close when the
last reference is dropped. In practice this works, but:

- Long-lived connections accumulate WAL size.
- Testing teardown can leave connections open (Windows file-lock issues).
- No visibility into connection state.

**Approach:**

1. **Short term:** Add `close()` method to `MemorantStore`,
   `ExpectationLedger`, `ContextTuner`, and `RecoveryStore`. Callers that
   hold a long-lived store can explicitly close it.

2. **Medium term:** Wrap `self.connect()` in a context manager that closes
   the connection on exit (currently it only commits/rolls back). The
   simplest approach: change `return self._db_path` to `return
   contextlib.closing(sqlite3.connect(...))`. But this changes the existing
   cursor-based API — all 50+ `with self.connect() as db: db.execute(...)`
   sites would need adjustment.

3. **Best approach for v2:** Replace `connect()` with a `cursor()` context
   manager that yields a cursor from a shared connection, and add explicit
   `close()` for store teardown. The shared connection is opened once at
   `init()` time and reused. This is the stdlib `sqlite3` model.

**Decision:** Defer the full refactor to v2. For v1, add `close()` methods
and document that connections are not pooled. The current pattern is
functional and not a correctness bug — it's a resource-management concern.

**Estimated complexity:** High (if done fully). Low for the v1 `close()`
addition.

**Depends on:** Decide in v2 planning.

---

### A7 — Image/audio token estimation

**Problem:** Image and audio parts contribute 0 estimated tokens. A message
with 10 images attached has the same token count as an empty message. This
means compression decisions ignore the real cost of multimodal content.

**Approach:**

1. **Add `_estimate_multimodal_tokens(content)`** that assigns a fixed cost
   per non-text part: `[image]` = 85 tokens (roughly equivalent to a
   low-res OpenAI vision image), `[audio]` = 50 tokens.

2. **Make costs configurable** via `token_estimates` dict on `ContextTuner`
   and `RecoveryStore`. Different model families have different vision
   pricing (GPT-5 vision is cheaper per image than GPT-4).

3. **Use the same helper in `count_message_tokens()`** so token counting is
   consistent everywhere.

**Estimated complexity:** Low. ~15 lines in the token counter + config
plumbing.

**Depends on:** A3 (multimodal end-to-end) should be done first, since the
helper will be written there.

---

## Dependency Order

```
L1 (keep_last_n) ──┐
L2 (versions)    ──┤
A1 (max_tokens)  ──┤  All independent — can be done in parallel
A2 (ordering)    ──┤
A4 (corruption)  ──┤
A5 (retention)   ──┤
                   │
A3 (multimodal)  ──┼── Then A7 (image tokens) depends on A3's helper
                   │
A6 (connections) ──┘  Defer to v2 planning
```

## Items explicitly deferred to v2

- **Connection pooling / lifecycle refactor (A6)** — functional as-is;
  resource concern, not correctness.
- **Schema version baselines for Context Tuner and Expectation Ledger** —
  both still at user_version 0. After the init() fix (Round 2), they
  initialize correctly on empty DBs. A migration for existing v0 DBs needs
  careful rollout planning.
- **Pluggable tokenizer** — currently hardcoded to character-based
  estimation. A `tiktoken`-backed or model-aware tokenizer is a v2 feature.
- **Custom summarizer signature** — the default summarizer accepts
  `(chunk, max_chars)` but a pluggable summarizer might want different
  parameters. The contract should be formalized when real alternative
  summarizers exist.

---

## Success criteria

A v1 release is architecturally sound when:

- [ ] max_tokens is a hard budget, not a suggestion (A1)
- [ ] System messages preserve their original position (A2)
- [ ] Multimodal messages work end-to-end: compress, recover, search (A3)
- [ ] Corrupt recovery data is detectable, not silently empty (A4)
- [ ] Recovery storage has configurable bounds (A5)
- [ ] Version strings are consistent across all packages (L2)
- [ ] keep_last_n=0 works correctly (L1)
- [ ] All fixes have regression tests that encode the exact reproducer
