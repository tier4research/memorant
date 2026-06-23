You are reviewing a coding doctrine for an AI agent (Andre). This doctrine
governs how the agent writes code, reviews code, fixes bugs, designs systems,
and audits its own work. It was developed across three external audit cycles
of a Python monorepo — each round found bugs the doctrine should have caught.

Your task: provide a rigorous critique. Find every weakness. Be specific.

=== THE DOCTRINE (v1.4.0) ===

[FULL DOCTRINE TEXT BELOW]

=== ANALYSIS QUESTIONS ===

1. STRUCTURAL CRITIQUE: Is the 8-step loop ordered correctly? Should DESIGN
VERIFY come before or after PLAN? Is VERIFY PREMISES (environmental checks)
distinct enough from DESIGN VERIFY (design contract checks)? Are the Lean-Model
Drift Guards redundant with the main loop?

2. CONTRADICTIONS: "Smallest precise change" (step 5) vs "redesign when
architecture is wrong" (step 4.5, three-fix-attempts rule). Are the triggers
for each clear enough? When should the agent patch vs redesign?

3. VAGUENESS: Which rules are too vague to be actionable by an LLM? Quote the
vague text and suggest a concrete replacement.

4. MISSING COVERAGE: Here are real bugs from three audit rounds. For each,
does the current doctrine prevent it? If not, what rule is missing?
- Per-chunk budget allocation couldn't enforce max_tokens
- System messages separated by role lost their original position
- Multimodal content (list) assumed to be string — token counter patched but
  summarizer + recovery still broken
- Counter drift: manual increment without idempotency check
- "Fix" that only changed comments/docstring, not behavior
- Proxy condition: user_version==0 treated as "empty database"
- Broad except IntegrityError swallowed CHECK/FK failures
- Redaction applied to content but not source_pointer (new field missed)
- keep_last_n=0 silently returned all messages (Python list[-0:])

5. MISSING PITFALLS: What common Python/SQLite/agent bug classes are missing
from the pitfalls section? Consider: concurrency/WAL races, serialization
round-trips, encoding edge cases, path handling across OSes, idempotency
beyond counters, rate limiting.

6. THE "ULTIMATE DOCTRINE" TEST: After all fixes, would this prevent the next
subagent-scaffolded feature from having architectural bugs? Walk through:
subagent is told "build a rate limiter with max_requests parameter." What
design mistakes would it make, and does the doctrine catch them?

7. TEN-RULE TEST: If only 10 rules survived, which ones? Rank by how many of
the above bugs each would prevent.

=== OUTPUT ===

Structure your response as:
- CRITICAL GAPS: bugs the doctrine still wouldn't prevent
- STRUCTURAL ISSUES: contradictions, vagueness, ordering problems
- CONSOLIDATION: redundancies to merge
- MISSING PITFALLS: bug classes not covered
- TEN-RULE RANKING: the load-bearing minimum
- ULTIMATE TEST: rate-limiter walkthrough
- OVERALL: one change that would most improve the doctrine

Be specific. Quote the doctrine text you're critiquing. Suggest exact
replacements. The goal is to find every remaining weakness.

=== FULL DOCTRINE TEXT ===

