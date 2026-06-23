# Claude Analysis Prompt — Andre Coding Doctrine v1.4.0

Save this as a text file and provide to Claude with the doctrine file attached,
or paste both into a Claude conversation.

---

You are reviewing a coding doctrine for an AI agent named Andre. This doctrine
governs how Andre writes code, reviews code, fixes bugs, audits code, and
designs systems. It was developed across three external audit cycles of a
Python monorepo (Memorant, Expectation Ledger, Context Tuner), with each round
finding bugs the doctrine should have prevented — including bugs introduced by
previous "fixes."

Your task: analyze the doctrine in exhaustive detail and provide feedback to
make it as rigorous and complete as possible. This is meant to become the
**ultimate** coding doctrine for an AI agent — it should prevent not just the
bugs we've seen, but the next class of bugs we haven't.

## The Doctrine

Read the attached file `andre-coding-doctrine-SKILL.md` (v1.4.0). This is a
YAML-frontmatter Markdown file used as a Hermes Agent skill. The frontmatter
metadata (name, version, tags) is structural; focus on the markdown body.

## Bug History — Testing the Doctrine's Coverage

The doctrine evolved through three audit rounds. Here are the bugs found in
each round, so you can test whether the current doctrine would prevent them.

### Round 1 — Audit on initial v1-dev (6 bugs found, 5 fixed in first attempt)

1. **Encrypted search broken** — `MemorantStore.connect()` used SQLCipher but
   `FTSRetriever` opened the DB through standard `sqlite3`. Encrypted databases
   failed during search/resonance. **Root:** Encryption propagated to one
   connection path but not all.

2. **retention_mode="none" still stores on timeout** — Timeout path
   unconditionally wrote session context. **Root:** Guard condition was missing
   on one code path.

3. **Failed claim inserts report success** — `add_claim()` caught every
   `sqlite3.IntegrityError` as a duplicate. CHECK/FK failures returned a claim
   ID for a non-existent claim. **Root:** Broad exception handler, no
   verification that the error was actually a duplicate.

4. **FTS5 relevance ranking inverted** — FTS5 BM25 ranks better matches with
   more negative values. The code treated values nearer zero as better.
   **Root:** Semantic direction not verified with live data; trusted mental
   model over SQLite output. Required **two** fix attempts — first fix still
   had the wrong direction.

5. **Resonance deadline not enforced for successful searches** — Deadline
   check was gated on `if elapsed > deadline and not results:`. Slow successful
   queries slipped through. **Root:** Timeout condition bound to result
   emptiness.

6. **Secrets in source_pointer leak into resonance** — Redaction applied to
   claim content but not `source_pointer`. **Root:** Redaction coverage
   incomplete; new field added without updating sanitization.

### Round 2 — Audit after Expectation Ledger + Context Tuner scaffolding (8 bugs)

The Expectation Ledger and Context Tuner packages were scaffolded by AI
subagents (delegate_task). The audit found:

1. **Pre-created empty DB init fails** — `init()` treated every version-0
   database as new, including pre-created databases with existing tables.
   Produced "duplicate column name" errors. **Root:** `user_version == 0` used
   as proxy for "empty" without schema inspection.

2. **max_tokens not enforced** — Compression exceeded `max_tokens` (observed
   322 tokens when limit=10). A `within_budget` flag was set but no truncation
   occurred. **Root:** Budget was tracked, not enforced. Per-chunk allocation
   had no hard ceiling.

3. **FTS queries crash on operator words** — Searching for "alpha OR beta"
   produced `sqlite3.OperationalError`. **Root:** User terms passed directly to
   FTS5 MATCH without quoting; uppercase OR/NOT/AND interpreted as operators.

4. **Expectation counter drifts** — `check(True)` twice doubled the count.
   **Root:** Manual counter increment without idempotency check.

5. **Multimodal content crashes token counting** — `content: list[dict]`
   (OpenAI format) hit `.strip()` on a list. **Root:** `content` assumed to be
   string; type diversity not handled.

6. **Compression reorders system messages** — System messages separated into
   their own list and placed first, breaking interleaved ordering.
   **Root:** Role-based list splitting without position tracking.

7. **Malformed recovery JSON cascades** — Corrupt JSON in one recovery row
   broke list/search for all rows. **Root:** No defensive JSON parsing.

8. **Schema versions inconsistent** — Memorant said `1.0.0-rc.1`, other
   packages said `0`. **Root:** Packages scaffolded independently without
   version coordination.

### Round 2 "Fixes" — Applied, then re-audited (Round 3 found them incomplete)

The fixes applied for Round 2 were themselves audited and found wanting:

1. **Init fix bypassed legitimate migrations and downgraded newer DBs.** The
   fix checked `existing_version > 0` before treating as legacy. But a legacy
   v0 database has version 0. And the broad `else` silently downgraded
   databases with version 99 → 7. **Root of the failed fix:** "Version > 0"
   was another proxy condition. Schema inspection was the correct approach but
   wasn't applied.

2. **max_tokens still not enforced.** `within_budget` flag recorded the
   violation but didn't enforce. Per-chunk allocation still had no global cap.
   And `compress_messages()` returned no failure indicator at all.
   **Root of the failed fix:** The fix added metadata about the bug rather than
   changing behavior.

3. **keep_last_n=0 completely disables compression.** Python `list[-0:]`
   returns all elements. Zero was never tested as a boundary value.
   **Root of the failed fix:** Boundary condition not in the test suite.

4. **Multimodal still broken end-to-end.** Token counting was patched but
   `RecoveryStore.save()` and the default summarizer still assumed string
   content. **Root of the failed fix:** Patchwork — each site patched
   independently instead of a shared helper.

5. **System ordering still not preserved.** The docstring was updated to claim
   ordering was preserved, but the code still separated system messages and
   placed them first. **Root of the failed fix:** Comment changed; behavior
   unchanged. The "fix" was cosmetic.

6. **Expectation counters still drift.** Manual increment replaced with derived
   COUNT — correct, but only after the second failed fix attempt.
   **Root of the failed fix:** Derived state was stored when it should have
   been computed.

7. **Malformed recovery now silently returns empty instead of crashing.** The
   parser no longer crashes, but returns `None` for malformed required fields.
   Callers can't distinguish "row is corrupt" from "row is genuinely empty."
   **Root of the failed fix:** Defensive coding without domain error types.

8. **Version strings still inconsistent** in some locations.

### Patterns across all three rounds

- **"Fixed" meant comments changed, not behavior** (system ordering, max_tokens flag)
- **Proxy conditions replaced proxy conditions** (`user_version > 0` for the
  same broken check)
- **Patchwork fixes missed parallel code paths** (multimodal: token counter
  fixed, recovery missed; encryption: store fixed, retriever missed)
- **Constraints were tracked, not enforced** (max_tokens flag, deadline bound
  to result emptiness)
- **No regression tests were added for any fix in Round 2** — the audit
  explicitly called this out

## Analysis Questions

Please address each of these in your response:

### 1. Coverage gaps
For each of the ~22 bugs above, determine: would the current doctrine (v1.4.0)
have prevented it at the time the code was written? If not, what rule is
missing? Be specific: name the bug and state whether the doctrine catches it
at design time, implementation time, or review time — or misses it entirely.

### 2. Contradictions and tensions
- "Smallest precise change" (EXECUTE) vs. "Redesign when architecture is
  wrong" (DESIGN VERIFY, three-fix-attempts rule). Are the triggers clear
  enough to avoid misapplication? When should the agent patch vs. redesign?
- "One concern at a time" vs. "multi-file means all files." When a fix
  requires touching 5 files, is that one concern or five?
- "Don't ask, just do" (FRAME: "choose the obvious check and proceed") vs.
  "Ask the user before" (PLAN: deleting data, pushing, etc.). Is the boundary
  crisp?

### 3. Vagueness audit
Which rules are stated too vaguely to be actionable by an LLM? For each:
- Quote the vague text
- Explain why it's unactionable (e.g., "verify the design" — verify HOW?)
- Suggest a concrete replacement

### 4. Redundancy
Do multiple rules cover the same ground? Examples may include:
- "Code comments are not source evidence" + "Comment-fix masquerade"
- "Discriminating tests only" + "MANDATORY regression test for every bug fix"
- "Subagent adversarial review" + the seven-category checklist
Consolidation makes the doctrine easier to internalize and harder to
selectively ignore.

### 5. Missing pitfall categories
The pitfalls section covers specific patterns (FTS5 ranking, IntegrityError
scope, deadline gating, redaction traversal, etc.). What common Python/SQLite/
agent bug classes are missing? Consider:
- Concurrency/race conditions (WAL, multiple connections)
- Serialization round-trips (JSON → DB → JSON losing types like `datetime`)
- Character encoding edge cases
- Path handling across OSes (Windows backslashes, WSL paths, case sensitivity)
- Rate limiting / retry storms
- Idempotency of write operations beyond counters (file writes, DB inserts)
- Python-specific: mutable default arguments, `is` vs `==`, `Path` quirks

### 6. Structural critique
- Is the loop ordering correct? Should DESIGN VERIFY come before or after
  PLAN? Should VERIFY PREMISES be before or after DESIGN VERIFY?
- Is the distinction between VERIFY PREMISES (environmental checks) and
  DESIGN VERIFY (design contract checks) clear and correct?
- Does the "Lean-Model Drift Guards" section belong inside the loop or as a
  standalone section?
- The doctrine has grown to ~400 lines. Is it too long to be internalized by
  an agent that reads it every session? What should be trimmed or moved to
  reference files?

### 7. The "ten-rule test"
If the doctrine could only have 10 rules — the absolute load-bearing minimum —
which survive? Rank them by how many of the 22 historical bugs each rule would
have prevented. A rule that prevents 8 bugs is more load-bearing than one that
prevents 1.

### 8. The "ultimate doctrine" test
After all your suggested changes, would this doctrine prevent the next round
of bugs a subagent produces when scaffolded with a feature prompt like "build
a rate limiter with a max_requests parameter"? Walk through the scenario: what
design mistakes would a subagent make, and which doctrine rules would catch
them? What class of failure does the doctrine still NOT catch?

### 9. Missing meta-rules
The doctrine governs how Andre writes code. But what rules govern how the
doctrine itself evolves? Consider:
- When should a new pitfall be added vs. an existing one refined?
- How do you prevent the doctrine from growing without bound?
- What's the process for verifying that a doctrine change actually prevents
  the bug class it targets?

## Output Format

Structure your response as:

**CRITICAL GAPS** — Bugs the doctrine still wouldn't prevent, with specific
missing rules.

**STRUCTURAL ISSUES** — Contradictions, vague rules, ordering problems, with
specific fixes.

**CONSOLIDATION** — Redundancies to merge, with specific merge suggestions.

**TEN-RULE RANKING** — The load-bearing minimum, ranked by historical impact.

**ULTIMATE DOCTRINE TEST** — Walkthrough of the rate-limiter scenario and
remaining blind spots.

**OVERALL ASSESSMENT** — Is this doctrine ready to prevent the next audit
round? What's the one change that would most improve it?

Be specific. Quote the doctrine text you're critiquing. Suggest exact
replacement text where possible. This is not a gentle review — the goal is
to find every remaining weakness before the next round of bugs does.
