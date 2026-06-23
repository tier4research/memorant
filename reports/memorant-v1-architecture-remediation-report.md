# Memorant v1 Architecture Remediation — Implementation Report

**Date:** June 22, 2026  
**Commit baseline:** `371528a`  
**Plan source:** `C:\Users\Admin\plan.md`  
**Project root:** `C:\Users\Admin\Documents\tier4-infra\memorant`

---

## Executive Summary

Implemented the Memorant v1 Architecture Remediation and completed a post-implementation audit/fix pass. The current suite collects 282 tests: 278 pass and 4 expected SQLCipher tests skip. The clean repository command `python -m pytest -q` works without setting `PYTHONPATH`, and the wheel build (`memorant-1.0.0rc1-py3-none-any.whl`) includes all three packages (`memorant`, `context_tuner`, `expectation_ledger`) plus `_vendor`.

> **Revision status:** `371528a` is the pre-remediation baseline, not a commit containing this implementation. The remediation and audit fixes are currently working-tree changes and must be committed before release/deployment provenance is complete.

---

## Files Changed/Created

| File | Action | Lines Added | Lines Removed | Net |
|---|---|---|---|---|
| `src/context_tuner/errors.py` | **Created** | 35 | — | +35 |
| `src/context_tuner/compressor.py` | **Rewritten** | 522 | 180 | +342 |
| `src/context_tuner/core.py` | **Updated** | 23 | 0 | +23 |
| `src/context_tuner/recovery.py` | **Rewritten** | 271 | 0 | +271 |
| `src/context_tuner/__init__.py` | **Updated** | 6 | 0 | +6 |
| `tests/test_context_tuner.py` | **Rewritten** | 940 | 0 | +940 |
| **Total** | | **1,797** | **180** | **+1,617** |

---

## Pass 1 — Compression Pipeline

### 1.1 `_extract_text(content)` Helper

**File:** `src/context_tuner/compressor.py`

Added a unified text extraction helper that handles all content types:
- `str`: returned as-is
- `list` of parts (OpenAI multimodal): text parts joined, `image_url` → `[image]`, `audio` → `[audio]`
- Other types: converted via `str()`

Also added companion helpers:
- `_extract_image_count(content)` — counts `image_url` parts
- `_extract_audio_count(content)` — counts `audio` parts

### 1.2 Configurable Multimodal Token Costs

**File:** `src/context_tuner/compressor.py`, `src/context_tuner/core.py`

Added to `TunerConfig`:
```python
image_token_cost: int = 85    # Estimated tokens per image part
audio_token_cost: int = 50    # Estimated tokens per audio part
```

`count_message_tokens()` now accepts `image_token_cost` and `audio_token_cost` keyword arguments and applies them to non-text parts.

### 1.3 Indexed-Segment Reassembly

**File:** `src/context_tuner/compressor.py` — `compress_messages_detailed()`

Replaced the old role-separated reassembly with an index-based approach:

1. **Identify protected indices**: All system messages + the last `keep_last_n` non-system messages
2. **Edge case fix**: When `keep_last_n >= len(non_system_indices)`, ALL non-system messages are protected (matching `chunk_messages` behavior)
3. **Group unprotected ranges**: Contiguous unprotected indices are grouped into ranges
4. **Summarize & anchor**: Each range is summarized and anchored at its first original index
5. **Reassemble**: Protected messages and summaries are sorted by original index

### 1.4 `CompressionOutcome` Dataclass

**File:** `src/context_tuner/compressor.py`

```python
@dataclass
class CompressionOutcome:
    messages: list[dict[str, Any]]
    original_tokens: int
    compressed_tokens: int
    within_budget: bool = True
    budget_enforced: bool = False
    degradation_reason: str | None = None
```

### 1.5 Shared Summary Budget Enforcement

**File:** `src/context_tuner/compressor.py`

- Subtracts protected-message cost from `max_tokens`
- Allocates remaining pool across all summaries (including 4-token message overhead per summary)
- If still over budget: removes oldest summaries (lowest anchor index) first
- If one summary remains and still over: progressively trims its content
- If protected content alone exceeds budget: returns `within_budget=False`, `degradation_reason="protected_content_exceeds_budget"`

### 1.6 `compress_messages()` Deprecated Wrapper

**File:** `src/context_tuner/compressor.py`

Retained as a three-tuple compatibility wrapper with `DeprecationWarning`:
```python
def compress_messages(...) -> tuple[list, int, int]:
    warnings.warn("compress_messages() is deprecated; use compress_messages_detailed() instead.", ...)
    outcome = compress_messages_detailed(...)
    return outcome.messages, outcome.original_tokens, outcome.compressed_tokens
```

### 1.7 Input Validation

**File:** `src/context_tuner/compressor.py`

```python
def _validate_compression_params(max_tokens, keep_last_n, compression_ratio):
    if max_tokens <= 0: raise ValueError(...)
    if keep_last_n < 0: raise ValueError(...)
    if not (0.0 <= compression_ratio <= 1.0): raise ValueError(...)
```

### 1.8 Core.py Updates

**File:** `src/context_tuner/core.py`

- `TunerConfig` extended with `image_token_cost`, `audio_token_cost`, `max_sessions`, `max_age_days`
- `ContextTuner.compress()` now calls `compress_messages_detailed()` instead of `compress_messages()`
- `RecoveryStore` constructor receives `max_sessions` and `max_age_days` from config

---

## Pass 2 — Recovery Integrity and Retention

### 2.1 `RecoveryCorruptionError`

**File:** `src/context_tuner/errors.py` (new)

```python
class RecoveryCorruptionError(Exception):
    def __init__(self, recovery_id: str, field: str, original_error: Exception | None = None):
        self.recovery_id = recovery_id
        self.field = field
        self.original_error = original_error
```

### 2.2 `_extract_text()` in Recovery FTS Content

**File:** `src/context_tuner/recovery.py`

FTS searchable content now uses `_extract_text()` (imported from `compressor.py`) so multimodal messages are indexed correctly:
```python
searchable = " ".join(
    _extract_text(m.get("content", ""))
    for m in compressed_messages
    if isinstance(m, dict) and m.get("content")
)
```

### 2.3 Corruption Handling in load/list/search

**File:** `src/context_tuner/recovery.py`

- `_decode_message_list(raw, recovery_id, field)` — raises `RecoveryCorruptionError` on invalid JSON or non-list data
- `_validate_message_list(raw, recovery_id, field)` — warns and returns `None` on corruption (used by list/search)
- `load()` — **raises** `RecoveryCorruptionError` for corrupt records
- `list_recent()` — **warns and skips** corrupt rows
- `search()` — **warns and skips** corrupt rows
- Missing records continue returning `None`

### 2.4 Retention Configuration

**File:** `src/context_tuner/recovery.py`, `src/context_tuner/core.py`

Added to `RecoveryStore.__init__()` and `TunerConfig`:
```python
max_sessions: int | None = None    # Max recovery sessions (None = unbounded)
max_age_days: int | None = None    # Max age in days (None = unbounded)
```

### 2.5 Atomic Pruning in `save()` Transaction

**File:** `src/context_tuner/recovery.py`

`_prune_impl(db)` is called inside the `save()` method's `with self.connect() as db:` block, ensuring atomicity:

1. **Age limit first**: Delete sessions older than `max_age_days`
2. **Count limit second**: Delete oldest sessions exceeding `max_sessions`
3. **Atomic FTS cleanup**: FTS rows deleted in same transaction as recovery rows
4. **Deterministic ordering**: `ORDER BY created_at ASC, id ASC`

### 2.6 Public `prune()` Method

**File:** `src/context_tuner/recovery.py`

```python
def prune(self) -> int:
    """Prune recovery sessions per retention policy. Returns count removed."""
    self.init()
    with self.connect() as db:
        pruned = self._prune_impl(db)
        db.commit()
    return pruned
```

---

## Exports Updated

**File:** `src/context_tuner/__init__.py`

New exports added to `__all__`:
- `RecoveryCorruptionError`
- `CompressionOutcome`
- `compress_messages_detailed`

---

## Bug Found and Fixed During Testing

### Protected-Set Off-by-One

**Location:** `compress_messages_detailed()` — protected-set identification

**Bug:** The condition `if keep_last_n > 0 and len(non_system_indices) > keep_last_n:` used `>` instead of `>=`, causing edge cases where `keep_last_n` equaled the number of non-system messages to incorrectly treat them as unprotected.

**Example:** With 1 non-system message and `keep_last_n=1`, the message should be protected but was being treated as unprotected and summarized.

**Fix:**
```python
if keep_last_n > 0 and len(non_system_indices) <= keep_last_n:
    # All non-system messages are protected
    protected_set.update(non_system_indices)
elif keep_last_n > 0:
    recent_indices = non_system_indices[-keep_last_n:]
    protected_set.update(recent_indices)
```

This now matches `chunk_messages` behavior where `len(non_system) <= keep_last_n` means all non-system messages are recent (protected).

---

## Post-Implementation Audit Fixes

The June 22 audit identified and corrected three runtime defects plus two verification/reporting defects:

1. **Retention timestamp comparison:** age pruning now compares timestamps through SQLite `julianday()` instead of lexicographically comparing SQLite and ISO-8601 text formats. This prevents records inside the retention window from being deleted early at the cutoff-date boundary.
2. **Configured multimodal costs:** `ContextTuner.count_tokens()` and `needs_compression()` now use `TunerConfig.image_token_cost` and `audio_token_cost`, matching the compression pipeline.
3. **Summarizer backward compatibility:** the compression pipeline inspects the callable signature and supports both the original `summarizer(messages)` contract and summarizers accepting `max_chars`/`**kwargs`. Exceptions raised inside the summarizer are not masked.
4. **Clean-checkout tests:** `pyproject.toml` now configures `pythonpath = ["src"]`; `python -m pytest -q` uses the checkout rather than an unrelated installed package.
5. **Report accuracy:** test totals and baseline/revision wording were corrected.

Regression tests cover all three runtime defects.

---

## Test Coverage

### Test File: `tests/test_context_tuner.py`

**Context Tuner file: 144 tests collected**

| Test Class | Tests | Description |
|---|---|---|
| `TestExtractText` | 9 | `_extract_text()` with strings, lists, images, audio, mixed, edge cases |
| `TestTokenCounting` | 8 | Token counting with multimodal costs, custom costs, non-dict messages |
| `TestKeyFactExtraction` | 6 | Key fact extraction, deduplication, URL detection, stop-word filtering |
| `TestMessageChunking` | 3 | Chunk splitting, fewer-than-keep, no-system edge cases |
| `TestDefaultSummarizer` | 5 | Empty, short, with-facts, truncation, multimodal content |
| `TestCompressMessages` | 6 | Deprecated wrapper tests (no-compression, triggers, system/recent preserved, markers, empty) |
| `TestCompressDetailed` | 8 | `compress_messages_detailed()`: under budget, compression, protected exceeds budget, indexed reassembly, keep_last_n=0, many chunks, multimodal, custom costs |
| `TestInputValidation` | 7 | max_tokens=0, negative, keep_last_n<0, ratio out of range, valid params, detailed validates |
| `TestRecoveryStore` | 14 | CRUD, list, search, stats, metadata, custom ID, multimodal FTS |
| `TestRecoveryCorruptionError` | 7 | Error attributes, load raises on corrupt original/compressed/non-list, list/search skip corrupt, missing returns None |
| `TestRetention` | 9 | Defaults don't prune, max_sessions, deterministic ordering, max_age, prune API, FTS atomicity, config propagation, age-then-count order |
| `TestContextTuner` | 18 | Full API: init, connect, compress, round-trip, decompress, list, delete, search, tokens, needs_compression, stats, doctor, integrity, metadata, within_budget |
| `TestTunerConfig` | 8 | Defaults, multimodal costs, retention, custom config, path override |
| `TestCompressedMessages` | 2 | Dataclass fields, within_budget default |
| `TestCompressionOutcome` | 2 | Dataclass fields, defaults |
| `TestRecoveryRecord` | 1 | Dataclass fields |
| `TestMultipleCompressions` | 2 | Sequential, same-content different sessions |
| `TestStewardIntegration` | 3 | Steward accessible, backup, migrate |
| `TestInitEmptyDB` | 2 | Pre-created empty DB, fresh DB |
| `TestMaxTokensEnforcement` | 2 | Within-budget flag, keep_last_n=0 |
| `TestCompatWrapper` | 3 | Tuple unpacking, same-as-detailed, deprecation warning |
| `TestEdgeCases` | 14 | System-only, all-protected, single message, mid-conversation system, non-contiguous ranges, budget trimming, exact boundary, empty content, ordering, multimodal round-trip, max_tokens contract, multiple system messages |

### Other Test Files (Unchanged, All Passing)

| Test File | Passed | Skipped |
|---|---|---|
| `tests/test_context_tuner.py` | 144 | 0 |
| `tests/test_core.py` | 76 | 4 (SQLCipher) |
| `tests/test_expectation_ledger.py` | 58 | 0 |
| **Total across all files** | **278** | **4** |

---

## Wheel Build Verification

**Wheel:** `dist/memorant-1.0.0rc1-py3-none-any.whl`

**Contents verified:**
- `memorant/` — core package (core.py, cli.py, retriever.py, schema.py, trust.py)
- `memorant/_vendor/` — vendored dependencies (doctor.py, event.py, steward.py, etc.)
- `context_tuner/` — compression package (compressor.py, core.py, errors.py, recovery.py, schema.py)
- `expectation_ledger/` — behavioral tracking package (core.py, schema.py, trust.py)

All three packages plus `_vendor` are correctly included in the wheel. ✅

---

## Code Review Summary

The implementation was reviewed by the code-reviewer-mimo-pro agent. Key findings:

1. **`_extract_text` duplication resolved**: Originally duplicated between `compressor.py` and `recovery.py`; fixed to import from `compressor.py`
2. **Deprecated wrapper warnings**: `TestCompressMessages` tests emit `DeprecationWarning` as expected; `TestCompatWrapper` tests explicitly verify the warning
3. **Edge case coverage**: All critical edge cases are tested (system-only, all-protected, single message, budget trimming, exact boundary)
4. **Pruning atomicity**: FTS + recovery rows deleted in same transaction — verified by `test_fts_deleted_atomically`
5. **Deterministic ordering**: Pruning uses `ORDER BY created_at ASC, id ASC` — verified by deterministic-ID tests

---

## Plan Compliance Checklist

| Requirement | Status |
|---|---|
| `_extract_text(content)` helper for strings, text parts, images, audio | ✅ |
| Configurable multimodal token costs in `TunerConfig` (image=85, audio=50) | ✅ |
| Indexed-segment reassembly preserving system positions | ✅ |
| `CompressionOutcome` with `within_budget`, `budget_enforced`, `degradation_reason` | ✅ |
| `compress_messages_detailed() -> CompressionOutcome` | ✅ |
| `compress_messages()` as deprecated three-tuple wrapper | ✅ |
| Shared summary budget with oldest-first trimming | ✅ |
| Input validation: `max_tokens > 0`, `keep_last_n >= 0`, `0 <= ratio <= 1` | ✅ |
| `_extract_text()` used in recovery FTS content | ✅ |
| `RecoveryCorruptionError` with recovery_id, field, original_error | ✅ |
| `load()` raises for corruption | ✅ |
| `list_recent()` and `search()` warn and skip corrupt rows | ✅ |
| Missing records return `None` | ✅ |
| `max_sessions` and `max_age_days` on `RecoveryStore` and `TunerConfig` | ✅ |
| Pruning in same transaction as `save()` | ✅ |
| Age limit first, count limit second | ✅ |
| FTS rows deleted atomically | ✅ |
| Deterministic ordering by `created_at, id` | ✅ |
| Public `prune() -> int` method | ✅ |
| No schema migration required | ✅ (schema.py unchanged) |
| No `close()` methods added (deferred to v2) | ✅ |
| Existing tuple-unpacking callers remain functional | ✅ |
| All 282 tests resolve (278 pass, 4 expected SQLCipher skips) | ✅ |
| Wheel contains all three packages plus `_vendor` | ✅ |

---

## Deferred to v2

Per the plan:
- Connection pooling / lifecycle refactor (A6)
- Schema version baselines for Context Tuner and Expectation Ledger
- Pluggable tokenizer (currently character-based estimation)
- Custom summarizer signature formalization
