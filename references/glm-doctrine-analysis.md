# Andre Coding Doctrine v1.4.0 — Exhaustive Analysis

*Analysis conducted 2026-06-22 against the full doctrine text at `_glm-doctrine-full.md`*

---

## CRITICAL GAPS — Bugs the Doctrine Still Wouldn't Prevent

### Gap 1: Python List Slicing Edge Cases (CRITICAL)

**Bugs:** `keep_last_n=0` returned all messages (`list[-0:] == list[:]` in Python); `keep_last_n=0` disabled compression entirely. Two separate bugs, same root cause.

**What the doctrine says:** The Subagent Review Checklist has "Boundary conditions: limit=0" — but this only tests the parameter, not the slicing behavior. The pitfall section has nothing on Python's `list[-0:]` trap.

**Missing rule:** A pitfall entry for Python slicing edge cases. Specifically: `seq[-0:]`, `seq[:-0]`, and `seq[0:0]` all behave counterintuitively. When a parameter controls slice bounds, test with the exact value `0` (not just "small") and confirm the slice produces the expected length, not just that it doesn't crash.

**Suggested addition to Pitfalls:**
> **Pitfall — Python slicing with computed negative indices:** `seq[-n:]` with `n=0` returns the ENTIRE sequence (because `-0 == 0`, so `seq[-0:] == seq[0:]`). This is the most common source of "limit=0 returns everything" bugs. Fix: guard with an explicit `if n <= 0: return []` before slicing, or use `seq[max(0, len(seq)-n):]` for "keep last n." Never pass an unbounded computed value directly to a negative slice index. Test with exactly `n=0`, `n=1`, and `n > len(seq)`.

### Gap 2: New-Field Propagation (HIGH)

**Bug:** Redaction applied to `content` but not `source_pointer` — a new field was added to the data model, and processing functions weren't updated.

**What the doctrine says:** DESIGN VERIFY §6 (Data model before code) says "define that type and write the extraction/coercion helper before any function that reads content. Every content-consuming function routes through the helper. One helper, one place to get it right." This is correct for *reading* data, but the bug is about *processing/writing* — redaction is a transformation that must touch every field.

**Missing rule:** When adding a field to a data structure, every function that transforms, copies, sanitizes, or serializes that structure must be updated. The doctrine needs a "structural propagation rule": enumerate all transformation passes over a data structure and verify each handles the new field.

**Suggested addition to DESIGN VERIFY §6 or Pitfalls:**
> **Corollary — structural propagation:** When adding a field to a core data structure (message dict, claim record, event payload), grep for every function that constructs, copies, transforms, redacts, or serializes that structure. Each must explicitly handle the new field — either pass it through, transform it, or document why it's excluded. A field that silently disappears in one processing pass is data corruption. After adding a field, write a test that round-trips the full structure through every transformation pipeline and asserts the field survives.

### Gap 3: Per-Unit Allocation ≠ Global Cap (HIGH)

**Bug:** `max_tokens` was enforced per-chunk, not globally — the global budget was never checked because the architecture allocated tokens to chunks independently.

**What the doctrine says:** DESIGN VERIFY §1 says "output token count ≤ max_tokens in every case, including adversarial inputs." §2 says "If removing the constraint wouldn't change the design, the constraint is cosmetic." §3 says "Constraints are the architecture. Features fit inside them."

**Why it's still a gap:** The stress-test (§2) asks "what's the maximum possible output?" but doesn't explicitly warn that *distributing a global budget across subunits without a global check is a per-unit allocation, not a global cap.* An agent might stress-test each chunk independently and conclude each stays under budget, missing that the sum exceeds the cap. The pattern is common enough to deserve explicit enumeration.

**Suggested addition to DESIGN VERIFY §2:**
> **Anti-pattern — distributed budget without global cap:** When a global constraint (max_tokens, max_requests, max_memory) is implemented by allocating portions to subunits (chunks, workers, windows) without a cross-unit enforcement mechanism, the global constraint is not enforced. Every subunit can be within its local budget while the total violates the global cap. Fix: the constraint must be enforced at the level it's specified. If the user says "max_tokens=4000," there must be ONE place that counts all tokens and halts when 4000 is reached — not N places that each count up to 4000/N.

### Gap 4: Verify Constraint Holds, Not That Mechanism Exists (HIGH)

**Bug:** `max_tokens` fix added a `within_budget` flag but didn't change enforcement behavior. The flag existed; the constraint didn't hold.

**What the doctrine says:** The "Comment-fix masquerade" drift guard says "verify that the BEHAVIOR changed, not just the documentation around the behavior." But this targets docstring/comment changes, not structural mechanism-additions that don't affect behavior.

**Why it's still a gap:** Adding a flag/variable/field named after the constraint, without the logic that enforces it, is a distinct class of false fix — more subtle than a comment change because it looks like real code. The agent sees `within_budget = True` and thinks "the budget is now tracked" even though nothing reads the flag.

**Missing rule:** After adding any enforcement mechanism (flag, counter, check), verify the mechanism actually gates behavior. The test must: (1) trigger the violation condition, (2) confirm the mechanism fires, (3) confirm the user-visible output changes. A flag that is set but never read is metadata, not enforcement.

**Suggested addition to drift guards:**
> **Mechanism-without-enforcement trap:** Adding a flag, counter, or tracking variable *named* after a constraint is not the same as *enforcing* the constraint. After implementing enforcement, test: (a) trigger the violation, (b) confirm the output changes (not just that a flag was set), (c) confirm the output in the non-violation case is unchanged. A `within_budget` flag that is set but never gates output is a comment stored in a variable. The test must prove the gate closes.

### Gap 5: Counter Increment Idempotency Pattern Not Specified (MEDIUM)

**Bug:** Counters drifted from manual `read → increment → write` without idempotency check.

**What the doctrine says:** DESIGN VERIFY §4 (Derived-state rule) says "Never store a value you can compute from primary data." The Subagent Review Checklist has "Repeat the same operation twice: counters must not double-increment." This identifies the *symptom* but doesn't teach the *pattern* to avoid it.

**Missing rule:** The doctrine should specify the correct idempotent counter pattern. The checklist says "test for double-increment" but the agent implementing the counter doesn't know HOW to make it idempotent.

**Suggested addition to DESIGN VERIFY §4 or Pitfalls:**
> **Idempotent counter pattern:** Never implement a counter as `current = read(); current += 1; write(current)`. This is a race condition even in single-threaded code if the operation can be retried. Use one of: (1) SQL `INSERT ... ON CONFLICT DO UPDATE SET count = count + 1` (atomic increment), (2) idempotency key — store a unique operation ID and increment only if the ID hasn't been seen, (3) compute from primary data — `SELECT COUNT(*) FROM source_table WHERE condition` instead of a stored counter. If you must use read-increment-write, wrap it in a transaction with a version check (optimistic locking).

### Gap 6: Silently Returning Empty/None on Parse Failure (MEDIUM)

**Bug:** Malformed JSON was patched but now silently returns empty instead of raising.

**What the doctrine says:** DESIGN VERIFY §5 says "'Return None' is not an error type — it makes 'empty' indistinguishable from 'corrupt.'" This is correct for the initial implementation but doesn't prevent the *second-order* bug: after the crash is fixed, the function now silently returns empty, which is also wrong.

**Why it's still a gap:** The doctrine prevents the first wrong fix (return None) but not the second wrong fix (return empty). The pattern "crash → silent empty → proper error" is a common three-step journey. The doctrine should explicitly warn that *any* silent degradation on invalid input is wrong — not just None, but also `[]`, `{}`, `""`, or default values.

**Suggested addition to DESIGN VERIFY §5:**
> **Silent degradation is corruption.** When input is invalid (malformed JSON, corrupt DB row, truncated data), the ONLY correct responses are: (1) raise a domain-specific exception, or (2) log-and-skip with explicit tracking of skipped items. Returning `[]`, `{}`, `""`, `None`, or any default/empty value is data corruption — it erases the evidence that something went wrong. After fixing a crash-on-invalid-input bug, verify the fix doesn't introduce silent degradation. The test: supply known-invalid input and assert a specific exception type is raised (or the skip is logged), not that the function returns without error.

---

## STRUCTURAL ISSUES

### Issue 1: DESIGN VERIFY (4.5) Should Precede PLAN (4)

**Current ordering:** FRAME → MAP → VERIFY PREMISES → PLAN → DESIGN VERIFY → EXECUTE

**Problem:** PLAN is step 4, DESIGN VERIFY is step 4.5. But DESIGN VERIFY governs whether the *design* is correct. If the design fails verification, the plan (which assumes the design) is wasted. The ordering should be:

FRAME → MAP → VERIFY PREMISES → DESIGN VERIFY → PLAN → EXECUTE

Or, more precisely: DESIGN VERIFY should happen at two points:
1. **Before PLAN** — validate the *approach/architecture* satisfies constraints (is this even the right design?)
2. **After PLAN** — validate the *implementation plan* follows the validated design

The current 4.5 numbering implies "plan first, then check if the plan's design is valid" — which is backwards for constraint-driven work.

**Recommendation:** Renumber DESIGN VERIFY to step 4, move PLAN to step 5, EXECUTE to step 6, VERIFY OUTCOME to step 7, ACCEPTANCE to step 8. The loop becomes:

1. FRAME
2. MAP
3. VERIFY PREMISES
4. DESIGN VERIFY (validate contract satisfaction)
5. PLAN (plan the implementation of the validated design)
6. EXECUTE
7. VERIFY OUTCOME
8. ACCEPTANCE

### Issue 2: VERIFY PREMISES and DESIGN VERIFY — Naming Ambiguity

**The names are too similar for an LLM.** Both contain "VERIFY" and both are about checking things before acting. An LLM might conflate them or skip one thinking it already verified. Specifically:

- VERIFY PREMISES: checks *environmental facts* (binary exists, path exists, port serves)
- DESIGN VERIFY: checks *design correctness* (does the architecture satisfy the constraint?)

**Recommendation:** Rename for clarity:
- VERIFY PREMISES → **PROVE ENVIRONMENT** (or "CHECK REALITY")
- DESIGN VERIFY → **STRESS-TEST CONTRACT** (or "VALIDATE DESIGN")

### Issue 3: Drift Guards Are Semi-Redundant with Main Loop

The Lean-Model Drift Guards section restates several principles already in the main loop:

| Drift Guard | Main Loop Equivalent |
|---|---|
| "Do not stop after the first subtask" | VERIFY OUTCOME + ACCEPTANCE (check all items) |
| "Multi-file means all files" | EXECUTE rule: "search all references and update all affected files" |
| "Current source beats session memory" | MAP: "Never assume project structure from memory" |
| "Three-fix-attempts rule" | DESIGN VERIFY §2 stress-test (catches wrong design earlier) |
| "Comment-fix masquerade" | VERIFY OUTCOME: "If the output is identical to pre-fix, you haven't fixed it" |
| "Proxy condition trap" | VERIFY PREMISES: "prove assumptions before acting" |
| "Code comments are not source evidence" | VERIFY PREMISES (semantic direction gate) |

**Problem:** Redundancy is not inherently bad for lean models (repetition helps), but the drift guards are presented as a separate "Drift Guards" section rather than integrated into the steps they reinforce. This means the agent might read the loop and miss the guards, or read the guards and miss the loop.

**Recommendation:** Either (a) integrate each drift guard as a sub-bullet under the corresponding loop step, removing the separate section, or (b) keep the separate section but add explicit cross-references: "This reinforces Step 3 (VERIFY PREMISES)."

### Issue 4: The Loop Numbering Breaks with 4.5

Having step "4.5" is structurally awkward. It signals that DESIGN VERIFY was added after the original 7-step loop was designed, and it's not a first-class step. This undermines its authority — the numbering itself says "this is an afterthought."

**Recommendation:** Promote DESIGN VERIFY to a full step (see Issue 1).

### Issue 5: CONTRADICTION — "Smallest Precise Change" vs "Redesign"

**The doctrine says:**
- Step 5 (EXECUTE): "smallest precise change" and "one concern at a time"
- Step 4.5 (DESIGN VERIFY): "If the design doesn't obviously satisfy the contract, redesign before writing a single line"
- Drift Guards: "After two failed fix attempts... redesign"

**The triggers are:** DESIGN VERIFY catches wrong designs BEFORE code is written; the three-fix-attempts rule catches wrong designs AFTER two failed patches. This is logically consistent.

**But there's a tension:** For a bug fix that seems small (a one-line change), should the agent run DESIGN VERIFY? The answer depends on whether the bug involves a constraint, data model, or state — but the agent might not recognize a constraint bug as a constraint bug until after the fix fails.

**Recommendation:** Add an explicit heuristic: "Before any fix, ask: does this bug involve a constraint (budget, ordering, correctness guarantee), a data model diversity (multiple shapes), or stored state? If yes, run DESIGN VERIFY steps 1-3 before executing. If no, proceed with smallest precise change." This gates design verification on bug characteristics, not on the agent's intuition about fix complexity.

### Issue 6: "One Concern at a Time" vs "Multi-File Means All Files"

**The doctrine says:**
- EXECUTE: "one concern at a time"
- EXECUTE: "for multi-file changes, search all references and update all affected files"
- Drift Guards: "Multi-file means all files; verify with search across references."

**Analysis:** These don't actually contradict. "One concern" = one logical change (e.g., "rename `get_document` to `get_claim`"). "Multi-file means all files" = that one logical change may touch many files. The tension is resolved by the clarification: one concern can span many files.

**Recommendation:** Make this explicit in the EXECUTE rule: "One concern at a time (one logical change — which may touch many files)."

---

## CONSOLIDATION — Redundancies to Merge

### Merge 1: Subagent Review Checklist + Subagent Adversarial Review (VERIFY OUTCOME)

The Subagent Output Review Checklist (under Drift Guards) and the "Subagent adversarial review" (under VERIFY OUTCOME step 6) overlap significantly. Both cover:
- Boundary conditions
- Type diversity
- Defensive parsing
- Ordering preservation
- Metadata consistency

**Recommendation:** Merge into a single Subagent Review section. Remove the checklist from Drift Guards and expand the VERIFY OUTCOME subagent section to include the checklist items with brief rationale. The Drift Guards section should just reference it: "For subagent output, apply the adversarial review checklist in Step 7 (VERIFY OUTCOME)."

### Merge 2: Pitfalls Section + FTS5/Ranking Pitfalls in Drift Guards

The "Pitfall — ranking/scoring formulas" entry in Drift Guards overlaps with the Semantic Direction Gate in VERIFY PREMISES. The specific FTS5 empirical data (rank values, corpus size dependence) is valuable but belongs in the Pitfalls section, not Drift Guards.

**Recommendation:** Move the FTS5 ranking pitfall (lines 462-463) to the Common Pitfalls section. Keep a one-line reference in VERIFY PREMISES: "For ranking/scoring changes, see Pitfall: FTS5 rank direction and normalization."

### Merge 3: "Code comments are not source evidence" Appears Twice

This concept appears in:
- Drift Guards: "Code comments are not source evidence. Comments... are crystallized assumptions, not verified facts."
- Drift Guards (later): "Generated reports, scratchpads, changelogs, model notes, and memory are not source evidence."

**Recommendation:** Consolidate into one statement under VERIFY PREMISES: "Source evidence = live command output + current file contents. Not source evidence = comments, docs, memory, model notes, generated reports, scratchpads."

### Merge 4: Plan-Bounce Methodology Redundancy

The Plan-Bounce Methodology section has a duplicated paragraph (lines 610-612 repeat lines 588-592 almost verbatim). The "Miguel's framing" quote and the shell command appear twice.

**Recommendation:** Remove the duplicate paragraph.

---

## VAGUENESS — Rules Too Vague to Be Actionable

### Vague Rule 1: "Non-trivial or irreversible work"

> **Current (PLAN step):** "For non-trivial or irreversible work, write a 1–3 bullet plan."

**Problem:** "Non-trivial" is entirely subjective. A lean model will classify almost everything as trivial to avoid the planning step. "Irreversible" is clearer but misses "complex" work that is reversible but error-prone.

**Replacement:**
> "Write a 1–3 bullet plan when the change: (a) touches 3+ files, (b) modifies a database schema or migration, (c) changes a public API/interface, (d) alters data at rest, (e) involves a new algorithm or data structure, or (f) could cause data loss if wrong. For single-file, single-function changes with no schema/api/data impact, planning may be omitted."

### Vague Rule 2: "Reduce scope"

> **Current (Operating Rules):** "When uncertain about something, reduce the task scope instead of guessing broadly."

**Problem:** "Reduce scope" is a directive without a method. An uncertain agent doesn't know HOW to reduce scope — it needs a concrete algorithm.

**Replacement:**
> "When uncertain about something, reduce scope by: (1) identify the uncertain sub-decision, (2) ask the user that specific question (don't guess), (3) proceed with the parts you ARE certain about. If you can't isolate the uncertainty, implement the simplest version that satisfies the core constraint and note what's deferred. Example: 'I'm unsure whether the rate limiter should use a sliding or fixed window. I'll implement a fixed window with the window size as a parameter, and note that sliding window can be added later.'"

### Vague Rule 3: "Preserve local style and behavior"

> **Current (EXECUTE rule):** "preserve local style and behavior"

**Problem:** "Local style" is ambiguous — does it mean the file's existing style, the project's style, or the ecosystem's conventions? "Behavior" is also ambiguous — does it mean don't change behavior, or preserve existing behavior patterns?

**Replacement:**
> "Match the surrounding code's conventions: same indentation, same quoting style, same import pattern (relative vs absolute), same error-handling approach (exceptions vs return codes), same logging style. Do not introduce new patterns in existing files unless the file itself is being refactored. When adding a new file, follow the closest neighboring file's conventions."

### Vague Rule 4: "Prefer structured output"

> **Current (Operating Rules):** "When producing results, use structured formats (markdown lists, code blocks, labeled key: value pairs) for clarity and verification."

**Problem:** "Prefer" is weak. When should the agent NOT use structured output? What counts as structured?

**Replacement:**
> "All final output must be structured for machine verification. Use: (a) `##`-headed sections for multi-part responses, (b) `- [ ]` checklists for tracked items, (c) `key: value` pairs for status/proof reporting, (d) fenced code blocks for commands and output. The only exceptions: conversational acknowledgments under one line, and verbatim user-requested prose."

### Vague Rule 5: "Inspect a target before overwriting/deleting it"

> **Current (EXECUTE rule):** "inspect a target before overwriting/deleting it"

**Problem:** What kind of inspection? Read the file? Check permissions? Verify it's the right target? This is too vague to execute.

**Replacement:**
> "Before overwriting or deleting a file: (1) `read_file` the target to confirm its current contents, (2) verify the path is the intended target (not a similarly-named file), (3) check that the file is not open in another process or editor, (4) if the file contains user data, back it up with a timestamped copy first."

### Vague Rule 6: Semantic Direction Gate — Missing Cases

> **Current:** "Applies equally to: FTS5 rank, BM25 scores, cosine distance, similarity metrics, sort keys, percentile thresholds, time-comparison directions"

**Problem:** This list is illustrative but incomplete. Missing: `min()`/`max()` direction (is smaller better?), boolean flag semantics (`enabled=True` means active or bypassed?), sign conventions (`-1` = error or success?), comparison operators in filter conditions (`<` vs `<=` at boundary values).

**Replacement:**
> "Applies to ANY directional decision: sort order (ASC/DESC), comparison operators (<, <=, >, >=), min/max selection, boolean semantics (True=enabled or True=bypass?), sign conventions (negative=better or positive=better?), threshold directions (reject above or below?), and any formula where multiplying by -1 would invert the result. Before changing any of these, run a 5-line test with known inputs and observe actual values."

---

## MISSING PITFALLS — Bug Classes Not Covered

### 1. Concurrency and Thread Safety (CRITICAL ABSENCE)

The doctrine has ZERO mention of concurrency, threading, multiprocessing, async safety, or race conditions. This is the single largest blind spot given that Python SQLite in WAL mode has specific concurrency semantics.

**Missing coverage:**
- SQLite WAL: `SQLITE_BUSY` on concurrent writes; `busy_timeout` configuration
- Connection-per-thread requirement: `sqlite3` connections cannot be shared across threads
- `check_same_thread=False` dangers and when it's actually needed
- `BEGIN IMMEDIATE` vs `BEGIN DEFERRED` for write contention
- `asyncio` + sqlite3: the lack of true async support and workarounds
- File-system-level races: two processes writing the same file
- `WAL` checkpointing under concurrent access

**Suggested addition:** A "Concurrency" subsection in Common Pitfalls covering SQLite thread safety, WAL bus handling, and the connection-per-thread rule.

### 2. Serialization Round-Trip Fidelity

The doctrine covers JSON parsing failures but not round-trip fidelity issues.

**Missing coverage:**
- `datetime` objects: JSON doesn't have a native datetime type — serialization/deserialization can lose timezone info
- `Decimal` vs `float`: precision loss on JSON round-trip
- `bytes`: base64 encoding assumptions
- `set` → JSON: sets aren't JSON-serializable
- `tuple` → JSON → `list`: type mutation on round-trip
- `None` vs missing key: semantic difference lost in JSON
- Custom objects with `__dict__`: unintended attribute exposure
- Pickle: security implications, cross-version incompatibility

**Suggested addition:** A "Serialization" subsection: "Every serialization boundary must have round-trip tests. Serialize → deserialize → assert equality with original. Test with: empty collections, None values, special floats (nan, inf, -inf), non-ASCII strings, and maximum-size objects."

### 3. Encoding and Character Handling

The doctrine mentions nothing about text encoding, which is a persistent source of cross-platform bugs.

**Missing coverage:**
- UTF-8 BOM handling: `\ufeff` at start of files
- Encoding detection vs explicit encoding
- Binary vs text mode in `open()`: `'wb'` vs `'w'` on Windows
- Surrogate pairs and emoji in path names
- Unicode normalization forms (NFC vs NFD) — macOS uses NFD, Windows/Linux use NFC
- `\r\n` vs `\n` line ending handling
- Null bytes in strings

**Suggested addition:** "Always specify `encoding='utf-8'` explicitly when opening text files. Never rely on system default encoding. For file paths, use `pathlib.Path` which handles encoding correctly."

### 4. Path Handling Cross-Platform

The doctrine has one Windows pitfall (`Path.suffix`) but is otherwise Linux-centric.

**Missing coverage:**
- Windows MAX_PATH (260 character) limit
- UNC paths (`\\?\C:\...`)
- Path traversal attacks (`../../etc/passwd`)
- Case sensitivity: `Path('File.txt') == Path('file.txt')` is True on Windows, False on Linux
- Symlink vs junction vs shortcut on Windows
- `/` vs `\` — `pathlib` handles this, but string concatenation doesn't
- Drive letters (`C:`) vs Unix root (`/`)
- Reserved Windows filenames (CON, NUL, AUX, PRN, COM1-9, LPT1-9)

**Suggested addition:** "Always use `pathlib.Path` for path manipulation — never string concatenation with `/` or `\`. When validating user-supplied paths, resolve and verify they stay within the intended directory tree (path traversal check)."

### 5. Idempotency Beyond Counters

The Subagent Review Checklist covers counter idempotency but not general operation idempotency.

**Missing coverage:**
- File creation: `open(path, 'x')` vs `open(path, 'w')` — the latter silently overwrites
- Network calls: retry with exponential backoff, but watch for duplicate side effects (idempotency keys)
- Database migrations: running the same migration twice (already partially covered)
- Configuration updates: setting the same value twice shouldn't duplicate entries
- Message sending: "at least once" vs "exactly once" delivery semantics

**Suggested addition to the checklist:**
> "- [ ] File creation uses `open(path, 'x')` or checks existence first — never silently overwrites
> - [ ] Network retries use idempotency keys or are naturally idempotent (GET, PUT with full body)
> - [ ] Configuration updates are UPSERT, not INSERT — don't create duplicates on re-run"

### 6. Time/Date Handling

**Missing coverage:**
- `time.time()` vs `time.monotonic()` — wall clock can go backwards (NTP, DST)
- Timezone-aware vs naive datetime comparison — crashes on comparison
- DST transitions: `timestamp + 86400` may not be "same time tomorrow"
- `datetime.now()` vs `datetime.utcnow()` — the latter is deprecated in Python 3.12
- Clock skew between machines in distributed systems
- ISO 8601 parsing variance (`Z` vs `+00:00` vs no timezone)

**Suggested addition:** "For duration measurement (deadlines, timeouts, rate limiting), use `time.monotonic()` — never `time.time()`. For wall-clock timestamps, always store UTC with explicit timezone. Compare timezone-aware datetimes only; comparing aware to naive raises `TypeError`."

### 7. Floating Point and Numeric Edge Cases

**Missing coverage:**
- `float == float` comparison: use `math.isclose()` or epsilon
- Integer division vs float division: `3/2 = 1.5` but `3//2 = 1`
- `float('nan')` — `nan != nan` is True, breaks dict lookups and sets
- `float('inf')` — arithmetic overflow/underflow
- Large integers in JSON: JavaScript can't represent integers > 2^53 precisely
- Division by zero guards
- `sum([])` returns `0` (int) but `sum([], start=0.0)` returns `0.0` — type stability

### 8. Memory and Resource Leaks

**Missing coverage:**
- File handles not closed: `open()` without `with`
- Database connections not returned to pool / not closed
- Generator/iterator exhaustion: calling `list()` twice on an iterator
- Circular references preventing garbage collection
- Large objects held in closure scope longer than needed

### 9. Default Mutable Arguments

Classic Python trap: `def f(items=[])` creates a single list shared across all calls.

### 10. Late-Binding Closure Gotcha

```python
funcs = [lambda: i for i in range(3)]
# All three return 2, not 0,1,2
```

### 11. `is` vs `==` Confusion

`is` checks identity, `==` checks equality. String interning and small-integer caching make `is` appear to work in tests but fail in production.

### 12. Import Side Effects and Circular Imports

Module-level code that executes on import (database connections, config loading, logging setup) can cause circular import deadlocks or unintended side effects.

---

## TEN-RULE RANKING — The Load-Bearing Minimum

If only 10 rules survived from this doctrine, ranked by how many historical bugs each would prevent:

### Rank 1: DESIGN VERIFY — Contract as Testable Assertion + Stress-Test + Contract-First
> "Write what must be true after the code runs — precise enough to encode as a test. Stress-test the design against the contract. Constraints are the architecture."

**Bugs prevented:** Per-chunk budget allocation (would catch "no global cap"), system message reordering (would catch "position not preserved"), counter drift design (would catch "derived state stored separately"), malformed JSON cascading (would catch "one corrupt record breaks everything"), proxy condition trap (would catch "user_version doesn't mean empty"), silent empty on failure (would catch "empty indistinguishable from corrupt").

**Score: 6+ bug classes.** THE single highest-leverage rule in the doctrine.

### Rank 2: VERIFY PREMISES — Semantic Direction Gate
> "Before changing any sort order, ranking formula, scoring coefficient, comparison operator, or threshold direction: run a minimal live test that produces known-good and known-bad inputs and observe the ACTUAL values."

**Bugs prevented:** FTS5 ranking inverted (2 fix attempts), scoring direction bugs, time-comparison direction bugs, min/max direction confusion, any formula where sign convention matters.

**Score: 3+ bug classes.** The most specific and actionable verification rule.

### Rank 3: DESIGN VERIFY — Data Model Before Code
> "If content can be `str | list[dict]`, define that type and write the extraction/coercion helper before any function that reads content. Every content-consuming function routes through the helper. One helper, one place to get it right."

**Bugs prevented:** Multimodal content crashes (str assumed, list received), type assumption bugs across components, new field missed (redaction source_pointer — if there was one helper for all redaction), patchwork fixes that miss some consumers.

**Score: 3+ bug classes.**

### Rank 4: VERIFY OUTCOME — Bug Fix Regression Test + Exact Reproducer
> "A bug fix without at least one regression test is a claim, not a fix. Encode the exact edge case from the audit reproducer. FAIL on unfixed, PASS on fixed. Verify against the EXACT reproducer."

**Bugs prevented:** Comment-fix masquerade (docstring change ≠ fix), max_tokens flag-not-enforcement (reproducer would fail), system ordering docstring fix (reproducer would fail), counters still drifting (test would catch).

**Score: 4+ bug classes.**

### Rank 5: DESIGN VERIFY — Error Model Before Happy Path
> "For every operation, enumerate failure modes and design error types FIRST. 'Return None' is not an error type — it makes 'empty' indistinguishable from 'corrupt.'"

**Bugs prevented:** Broad `except IntegrityError` swallowing non-duplicate errors, malformed JSON silently returning empty, deadline gated on result emptiness.

**Score: 3+ bug classes.**

### Rank 6: Three-Fix-Attempts Rule
> "After two failed fix attempts on the same bug — confirmed by test failure — do not attempt a third line-level fix. Stop and write a 5-bullet analysis."

**Bugs prevented:** FTS5 ranking (2 failed attempts before correct fix), max_tokens enforcement (flag attempt + docstring attempt = 2 failed before behavioral fix needed), counters still drifting (repeated patchwork). Reduces wasted effort and forces architectural thinking.

**Score: 2+ bug classes (but saves enormous time).**

### Rank 7: FRAME — Done as One Concrete Check
> "done = <observable effect> verified by <specific command/probe/test>"

**Bugs prevented:** Deadline gated on result emptiness (done check would reveal the gate), vague success criteria that allow incomplete verification.

**Score: 2+ bug classes.** Foundation for all verification.

### Rank 8: DESIGN VERIFY — Derived-State Rule
> "Never store a value you can compute from primary data. Stored derived state is a synchronization bug waiting to happen."

**Bugs prevented:** Counter drift (if counter was computed from primary data), any stored-computed-value synchronization bug.

**Score: 1-2 bug classes.** Specific but high-impact for data integrity.

### Rank 9: Proxy Condition Trap
> "Never use a version number, flag, or label as a proxy for ground truth when you can inspect the thing itself."

**Bugs prevented:** `user_version==0` as empty database proxy, `is_compressed` flag as compression status proxy, `schema_version` string as schema state proxy.

**Score: 1+ bug classes.** Covers an entire class of shortcut-thinking bugs.

### Rank 10: MAP — Inspect Before Editing
> "Read 1-2 nearby files/configs/tests first. Never assume project structure from memory. Let the codebase teach the pattern."

**Bugs prevented:** Foundation for all correct fixes — without MAP, every other rule operates on wrong assumptions. Wrong file boundaries, wrong service boundaries, wrong schema assumptions.

**Score: Foundational (prevents wrong-context errors in all other bugs).**

---

## ULTIMATE TEST — Rate Limiter Walkthrough

**Scenario:** A subagent is told: "Build a rate limiter with `max_requests` parameter."

### What the subagent WOULD produce without the doctrine:

1. A simple class with a counter and a timer
2. `time.time()` for wall clock
3. `counter += 1` without idempotency
4. Return `False` when limit exceeded (no error type)
5. No thread safety
6. Assumes `max_requests` means "per second" (ambiguous window)
7. Counter stored as a variable (not computed from request timestamps)
8. No burst handling — if 2*max_requests arrive in 1ms, all pass because the check happens before any timestamps are recorded

### What the doctrine WOULD enforce:

**FRAME:** "done = rate limiter rejects exactly the (max_requests+1)th request in any window of duration W, and the rejection includes a `RateLimitExceededError` with a `retry_after` timestamp"

**MAP:** Inspect existing rate limiting code, understand threading model, identify clock source

**VERIFY PREMISES:** Check `time.monotonic()` availability, check if asyncio or threading is in use, check Python version for `time.monotonic()` vs `time.monotonic_ns()`

**DESIGN VERIFY:**

- §1 **Contract:** "In any window of duration W, at most max_requests are accepted. Each rejection MUST raise `RateLimitExceededError(retry_after=<timestamp>)`. The counter MUST be accurate under concurrent access."
- §2 **Stress-test:** 
  - Worst case: 2*max_requests arrive in 0ms. Does the counter gate them? (With read-increment-write: race condition — all might pass.)
  - Window boundary: 1 request at t=W-1ms, next at t=W+1ms. Is the first request correctly aged out?
  - Clock goes backwards: `time.time()` can go backwards (NTP adjustment). What happens? (Use `time.monotonic()` — this is a MISSING PITFALL!)
  - Empty: max_requests=0. Should all requests be rejected? (Boundary condition.)
- §3 **Contract-first:** The budget is the architecture. Design the limiter so the budget is checked BEFORE any request is processed, not after. If the budget check can be bypassed by a burst, it's not a budget.
- §4 **Derived-state:** Consider computing the current request count from a sliding window of timestamps rather than storing a counter. If a counter is used, it must be atomically incremented and checked in one operation.
- §5 **Error model:** `RateLimitExceededError(retry_after: float)` — not `None`, not `False`.
- §6 **Data model:** `RateLimitRequest(timestamp: float, key: str)` — define before code.
- §7 **Subagent design review:** Review the blueprint BEFORE any code is written.

**EXECUTE:** One concern at a time — implement the core algorithm, then error types, then thread safety.

**VERIFY OUTCOME:**
- Test: 2*max_requests in one burst → only max_requests accepted
- Test: max_requests=0 → all rejected with proper error
- Test: request at t=W-1ms and t=W+1ms → second accepted (window boundary)
- Test: clock goes backwards (mock `time.monotonic()`) → limiter doesn't break
- Test: concurrent access (threaded test) → counter accurate
- Adversarial review: check for the subagent bug classes (missing boundary, type assumptions, etc.)

### What the doctrine WOULD STILL MISS:

1. **Clock monotonicity:** The doctrine's semantic direction gate mentions "time-comparison directions" but doesn't explicitly warn to use `time.monotonic()` instead of `time.time()` for duration measurement. A subagent would likely use `time.time()` — and on a machine with NTP adjustment, the rate limiter would fail silently when the clock jumps backwards.

2. **Thread safety patterns:** The doctrine has ZERO mention of locks, atomic operations, or thread-safe data structures. The subagent would implement `counter += 1` in Python, which is not atomic (it's `LOAD_FAST`, `BINARY_ADD`, `STORE_FAST` — three bytecodes, not one). Under concurrent access, the counter would drift.

3. **Window semantics ambiguity:** The doctrine's stress-test (§2) asks "walk through the worst case" but doesn't prompt: "What time window does `max_requests` apply to? Per second? Per minute? Per hour?" The subagent would assume "per second" but the user might mean "per minute." The contract §1 says "in any window of duration W" — but W is unspecified. The agent must ASK or choose a default.

4. **Fixed vs sliding window:** The subagent might implement a fixed window (reset counter every W seconds), which allows 2*max_requests at the boundary. The doctrine's stress-test catches the boundary burst, but doesn't explicitly enumerate "this is why you need a sliding window, not a fixed window." The agent could catch the issue but not know the correct fix.

**Verdict:** The doctrine would catch 70% of the bugs, fail on clock monotonicity and thread safety, and be ambiguous on window semantics. Adding the missing pitfalls (concurrency, time handling) would raise coverage to ~90%.

---

## OVERALL ASSESSMENT

### Is this doctrine ready to prevent the next audit round?

**No — but it's close.** The doctrine is strong on constraint verification, design validation, and anti-pattern recognition for bugs that have already happened. It would prevent most *repeats* of historical bug classes. However, it has a structural weakness: it's reactive to past bugs rather than proactively comprehensive. The next audit round will find bugs in areas the doctrine doesn't cover (concurrency, encoding, clock handling) — not because the doctrine is wrong, but because it's silent on those domains.

### The ONE change that would most improve the doctrine:

**Add a "CONSTRAINT ARCHITECTURE PATTERNS" appendix** that enumerates specific anti-patterns with their correct replacements. The doctrine currently teaches *process* (verify the contract, stress-test the design) but not *patterns* (here's what a global budget that actually works looks like, here's the idempotent counter pattern, here's why you use `time.monotonic()` for deadlines). An LLM can follow a process but may not *generate* the correct pattern from first principles. The patterns appendix bridges this gap:

```markdown
## Appendix A: Constraint Architecture Patterns

### Pattern 1: Global Budget Enforcement
**Wrong:** Allocate budget portions to subunits, sum assumed to stay under cap.
**Right:** One global counter/tracker. Every unit checks the global tracker 
BEFORE consuming budget. The tracker is the single source of truth.
**Test:** Sum of all allocations across all units ≤ global cap after any operation.

### Pattern 2: Idempotent Counter
**Wrong:** `current = read(); current += 1; write(current)`
**Right:** `UPDATE counters SET value = value + 1 WHERE id = ?` (atomic in SQLite)
or `INSERT ... ON CONFLICT DO UPDATE SET count = count + 1`
**Test:** Run operation twice, assert counter incremented by exactly 1.

### Pattern 3: Ordering Preservation
**Wrong:** Separate messages by role, process each role group, concatenate.
**Right:** Preserve original list order through all transformations. 
If grouping is needed, store original indices and restore order after processing.
**Test:** message[i].id < message[j].id in output iff i < j in input.

### Pattern 4: Structural Propagation
**Wrong:** Add field to data class, forget to update serializers/redactors/copiers.
**Right:** After adding a field, grep for every function that constructs, copies, 
or transforms the class. Update each. Write a round-trip test through all pipelines.
**Test:** Serialize → full pipeline → deserialize, assert new field survives.

### Pattern 5: Clock Selection
**Wrong:** `time.time()` for duration measurement (wall clock can jump).
**Right:** `time.monotonic()` for durations, `time.time()` for wall-clock timestamps only.
**Test:** Mock clock to jump backwards, assert deadline/timeout still fires correctly.

### Pattern 6: Error Over Silence
**Wrong:** `try: parse(); except: return []` (corrupt data silently dropped).
**Right:** `try: parse(); except ParseError as e: raise CorruptDataError(e) from e` 
or log-and-skip with explicit tracking.
**Test:** Supply corrupt input, assert specific exception raised (not empty return).

### Pattern 7: Slicing with Zero
**Wrong:** `seq[-n:]` where n can be 0 (returns entire sequence).
**Right:** `seq[-n:] if n > 0 else []` or explicit guard.
**Test:** n=0 → empty result; n=1 → single element; n > len(seq) → entire sequence.
```

### Second-priority improvement: Add concurrency section

The complete absence of concurrency/thread-safety coverage is the single biggest blind spot. Add to Common Pitfalls:
- SQLite connection-per-thread rule
- `SQLITE_BUSY` handling
- `time.monotonic()` vs `time.time()`
- Atomic counter patterns
- WAL checkpointing under concurrent access

### Summary Judgment

The v1.4.0 doctrine is a **7/10.** It's excellent at what it covers (constraint verification, design validation, bug fix quality) but has three structural weaknesses:

1. **Domain gaps** (concurrency, encoding, clock handling, path portability) — silent on entire categories of bugs
2. **Process without patterns** — teaches verification process but not the correct implementation patterns to use after verification catches the problem
3. **Reactive scope** — covers the bugs that have happened, not the bugs that will happen next

The doctrine would prevent ~70% of the historical bugs if followed perfectly. Adding the constraint patterns appendix and concurrency coverage would raise this to ~90%. The remaining 10% would be novel bug classes that no static doctrine can anticipate — caught only by the adversarial review and verification steps.

**Final recommendation:** Ship this analysis as the v1.5.0 revision brief. Implement the critical additions (patterns appendix, concurrency pitfalls, gap fixes), renumber the loop, and consolidate redundancies. Target v1.5.0 as the "proactive" release.
