# Why the Architectural Issues Happened — Root Cause Analysis

Analysis of why nine issues across three audit rounds were architectural
(not line-fix) failures, and what in the development process allowed them.

---

## The chain of causation

### 1. Subagents scaffolded without design review

Context Tuner and Expectation Ledger were built by subagents (delegate_task).
The instructions were feature-level: "build compression with max_tokens,"
"build expectation tracking with counters." The subagents chose their own
architecture, and their code was accepted after surface-level review.

No design document was written, reviewed, or bounced. The subagent's
internal architecture became the de facto architecture without any external
critique.

**Result:** Every architectural issue traces to a subagent's initial design
decision:
- Per-chunk budgeting → max_tokens cannot be enforced (A1)
- Splitting system messages into separate lists → ordering lost (A2)
- `content: str` assumption throughout → multimodal broken (A3)
- `except IntegrityError: return existing` → false success (Round 1, Bug 3)
- Manual counter increment → drift (Round 2, Bug 4)

### 2. The doctrine was implementation-only

The coding doctrine (v1.0.0–v1.1.0 at the time of scaffolding) governed
*how to write code*, not *how to design systems*. It covered:

- Correctness of individual changes (smallest precise change)
- Premise verification (is the environment what you think?)
- Outcome verification (did the fix work?)
- Discriminating tests

It did NOT cover:

- Design contract verification (does the architecture satisfy the
  requirements?)
- Data model design (what shapes can content take? What types exist?)
- Constraint architecture (is max_tokens a suggestion or a guarantee?)
- Error model design (what errors can occur, and how are they signaled?)
- Derived vs. stored state (do we store something we could compute?)

### 3. "Smallest change" bias worked against architectural fixes

The doctrine's EXECUTE step says "smallest precise change." When the bug
is architectural, the smallest change is a patch that doesn't fix the
problem:

| Bug | Smallest change applied | Why it failed |
|-----|------------------------|---------------|
| max_tokens | Added `within_budget` flag | Flag tracks failure; doesn't enforce budget |
| System ordering | Updated docstring | Comment changed; logic unchanged |
| Counter drift | Derived count from data | Now correct, but only after two failed manual-increment attempts |
| Multimodal | Patched token counter only | Recovery and summarizer still assume strings |

The doctrine's bias toward minimal change correctly prevents over-engineering
for line-fix bugs. But for architectural bugs, it produces cosmetic fixes
that the next audit round catches.

### 4. Feature-driven, not contract-driven

The subagent prompts were "add feature X" not "satisfy contract X." The
difference:

- **Feature-driven:** "Add compression with a max_tokens parameter." →
  Subagent builds compression first, then tries to fit max_tokens around it.
  The budget is a constraint on the output, not the organizing principle.

- **Contract-driven:** "max_tokens is a hard budget. Design compression that
  never exceeds it." → Subagent starts with the budget and builds compression
  inside it. The budget is the architecture.

Every architectural bug maps to this pattern:
- max_tokens: feature (compression) + constraint (budget) → constraint lost
- System ordering: feature (compression) + constraint (ordering) → constraint lost
- Multimodal: feature (string-based) + edge case (list content) → edge case broken
- Counter: feature (tracking) + constraint (accuracy) → constraint drifted

### 5. Plan-bounce existed but wasn't used for scaffolding

The doctrine's Plan-Bounce Methodology was available but was scoped to
"non-trivial self-dev only — new subsystems, architectural changes, anything
where being wrong costs more than the bounce overhead." The subagent
scaffolding qualified — two new packages, shared vendor layer, monorepo
structure — but the plan-bounce wasn't applied because subagent delegation
felt like "task execution" rather than "architecture design."

The subagent was treated as an executor of a clear task, when in reality it
was making architectural decisions: how to structure compression, how to
track counters, what data model content uses.

### 6. The timing gap

The subagent adversarial review doctrine (v1.2.0) was added AFTER the
subagent code was accepted. The seven-category checklist would have caught
multimodal type assumptions and counter correctness, but it didn't exist
yet when the code was written and accepted.

Similarly, the "comment-fix masquerade" and "proxy condition trap" rules
(v1.3.0) were added after their respective failures. The doctrine is always
one round behind the bugs it prevents.

---

## What would have prevented each issue

| Issue | Prevention that existed at the time | Prevention that should have existed |
|-------|-------------------------------------|-------------------------------------|
| max_tokens per-chunk | Nothing — no design review gate | Contract-first design: "budget is the architecture" |
| System ordering lost | Nothing — subagent review was surface-level | Design premise: "splitting by role loses position" |
| Multimodal broken | Nothing — type diversity not in checklist yet | Data model definition before implementation |
| Counter drift | Nothing — idempotency not in checklist yet | Derived-state rule: never store what you can compute |
| IntegrityError scope | Nothing | Error-type discrimination in design phase |
| FTS5 operator crash | Nothing — escaping not in checklist yet | Query-builder contract: all inputs escaped |
| Corruption → None | Nothing — defensive parsing not in checklist | Error model design: domain errors over None |
| keep_last_n=0 | Zero not in boundary checklist | Boundary contract: define what 0 means before coding |
| Version inconsistency | Nothing — metadata not in checklist | Single source of truth for version strings |

**Pattern:** In every case, a design decision made during initial scaffolding
created the vulnerability. The implementation was correct *for the design*;
the design was wrong for the requirements.

---

## The doctrine's structural gap

The seven-step loop is:

```
FRAME → MAP → VERIFY PREMISES → PLAN → EXECUTE → VERIFY OUTCOME → ACCEPTANCE
```

There is no DESIGN step. VERIFY PREMISES checks environmental assumptions
(port is open, DB has column, binary exists). It does not check design
premises (is per-chunk allocation correct for a hard budget? Should we store
or derive this state?).

The PLAN step says "write 1–3 bullets" — it's about sequencing, not about
validating the design against the requirements.

What's missing: **a step between PLAN and EXECUTE that verifies the design
itself satisfies the contract.**

---

## What the doctrine needs (proposed v1.4.0)

### DESIGN VERIFY — a new step between PLAN and EXECUTE

Before writing code for any feature that involves a constraint, a data
model, or state tracking:

1. **State the contract.** What must be true after this code runs? Write it
   as a testable assertion: "max_tokens is a hard ceiling — compressed output
   NEVER exceeds it." "System messages preserve their original position."
   "Content can be a string or a list of part dicts."

2. **Verify the design against the contract.** Before implementing, ask:
   - Does this design naturally satisfy the constraint, or does it try to
     enforce it afterward?
   - If the constraint were removed, would the design change? If not, the
     constraint is cosmetic.
   - What input diversity does this design handle? What does it assume?

3. **Contract-first ordering.** Constraints are the architecture. Features
   fit inside them. Not the other way around.

4. **Derived-state rule.** Never store a value you can compute from primary
   data. If you're storing a counter and also storing the rows it counts,
   you have a synchronization bug waiting to happen.

5. **Error model.** For every operation, enumerate what can go wrong and
   design the error type before writing the happy path. "Return None" is
   not an error type.

6. **Data model before code.** If content can be `str | list[dict]`, define
   that type and write the extraction helper before any function that reads
   content.

### Subagent design review (not just code review)

When a subagent is asked to build a new package or subsystem, the process
should be:

1. **Write a 5-bullet design brief** (not a full plan, just the contract
   and architecture sketch).
2. **Review the design before the subagent writes code.** Does the
   architecture satisfy the contract? Where are the likely failure points?
3. **Subagent implements against the reviewed design.**
4. **Post-implementation adversarial review** (existing doctrine).

The current process skips steps 1–2: the subagent designs AND implements,
and only the implementation is reviewed.

---

## The deeper pattern

Every architectural bug in this codebase follows the same shape:

> A constraint was specified. The implementation was built without the
> constraint as its organizing principle. The constraint was then applied
> as a patch. The patch didn't hold.

This is not a coding failure. It's not a testing failure. It's a design
failure that happened before any code was written.

The doctrine's job is to prevent those failures. Currently it prevents
implementation failures well. It does not prevent design failures. The v1.4.0
addition — DESIGN VERIFY — is meant to close that gap.
