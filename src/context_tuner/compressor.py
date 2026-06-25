"""Message compression logic for Context Tuner.

Provides token counting, key fact extraction, message chunking,
and summarization. Default summarizer uses truncation + key fact
extraction. Designed to be extensible for LLM-based summarizers.

Architecture:
- _extract_text(): unified text extraction for multimodal content
- count_tokens(): approximate token count (4 chars ≈ 1 token)
- extract_key_facts(): regex-based key fact extraction
- compress_messages(): deprecated three-tuple wrapper (v1 compat)
- compress_messages_detailed(): full compression pipeline returning CompressionOutcome
"""

from __future__ import annotations

import inspect
import re
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable


# ── Multimodal text extraction ─────────────────────────────────

def _extract_text(content: str | list[dict] | Any) -> str:
    """Extract plain text from message content, handling multimodal format.

    Supports:
    - str: returned as-is
    - list of parts (OpenAI multimodal): text parts joined, image/audio
      parts yield placeholder tokens
    - Other types: converted to str
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type == "text":
                parts.append(item.get("text", ""))
            elif item_type == "image_url":
                parts.append("[image]")
            elif item_type == "audio":
                parts.append("[audio]")
        return " ".join(parts)
    return str(content)


def _extract_image_count(content: str | list[dict] | Any) -> int:
    """Count image parts in multimodal content."""
    if not isinstance(content, list):
        return 0
    return sum(
        1 for item in content
        if isinstance(item, dict) and item.get("type") == "image_url"
    )


def _extract_audio_count(content: str | list[dict] | Any) -> int:
    """Count audio parts in multimodal content."""
    if not isinstance(content, list):
        return 0
    return sum(
        1 for item in content
        if isinstance(item, dict) and item.get("type") == "audio"
    )


# ── Token counting ──────────────────────────────────────────────

def count_tokens(text: str) -> int:
    """Approximate token count using character-based heuristic.

    Rough heuristic: ~4 characters per token for English text.
    This is a zero-dependency fallback. For production use,
    swap in tiktoken or a HuggingFace tokenizer.
    """
    if not text:
        return 0
    # Strip whitespace and count characters
    stripped = text.strip()
    # 4 chars ≈ 1 token is the standard rough approximation
    return max(1, len(stripped) // 4)


def count_message_tokens(
    messages: list[dict[str, Any]],
    *,
    image_token_cost: int = 85,
    audio_token_cost: int = 50,
) -> int:
    """Count approximate tokens across all messages.

    Handles string content, list-valued content (OpenAI multimodal),
    and non-dict messages gracefully. Accounts for image and audio
    parts using configurable per-part token costs.

    Args:
        messages: List of message dicts.
        image_token_cost: Estimated tokens per image part (default: 85).
        audio_token_cost: Estimated tokens per audio part (default: 50).
    """
    total = 0
    for msg in messages:
        if not isinstance(msg, dict):
            total += count_tokens(str(msg))
            total += 4
            continue
        content = msg.get("content", "")
        text = _extract_text(content)
        total += count_tokens(text)
        # Add multimodal costs for non-text parts
        total += _extract_image_count(content) * image_token_cost
        total += _extract_audio_count(content) * audio_token_cost
        total += 4  # Role marker overhead
    return total


# ── Key fact extraction ─────────────────────────────────────────

# Patterns for identifying sentences that likely contain key facts
_KEY_FACT_PATTERNS = [
    # Explicit markers
    (re.compile(r'(?:IMPORTANT|CRITICAL|DECISION|ACTION|TODO|NOTE|KEY)[\s:]+(.+?)(?=[.!?\n]|$)', re.IGNORECASE), 1),
    # Action verbs at sentence start
    (re.compile(r'(?:^|[.!?\n]\s*)((?:Decided|Agreed|Created|Approved|Rejected|Resolved|Assigned|Scheduled|Completed|Deployed|Merged|Fixed|Updated|Changed|Removed|Added)\s[^.!?\n]+)', re.MULTILINE), 1),
    # Named entity patterns (Capitalized phrases of 2+ words)
    (re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b'), 1),
    # Code/technical references
    (re.compile(r'(?:`[^`]+`|[\w]+\(\)|#[0-9]+|[\w]+\.py|[\w]+\.js|[\w]+\.ts)'), 0),
    # URLs
    (re.compile(r'https?://[^\s]+'), 0),
    # File paths
    (re.compile(r'(?:/[\w.-]+)+/[\w.-]+'), 0),
    # Numbers with context (dates, versions, counts)
    (re.compile(r'(?:v?\d+\.\d+(?:\.\d+)?|\d{4}-\d{2}-\d{2}|\b\d+\s*(?:items|files|lines|errors|tests)\b)'), 0),
    # Questions (often contain intent)
    (re.compile(r'(?:^|[.!?\n]\s*)((?:Can|could|would|should|will|how|what|why|where|when)\s[^.!?\n]+\?)', re.IGNORECASE | re.MULTILINE), 1),
]

# Stop words for fact deduplication
_FACT_STOP_WORDS = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                     "have", "has", "had", "do", "does", "did", "will", "would",
                     "could", "should", "may", "might", "can", "shall", "to", "of",
                     "in", "for", "on", "with", "at", "by", "from", "as", "into",
                     "through", "during", "before", "after", "above", "below",
                     "between", "under", "and", "but", "or", "nor", "not", "so",
                     "yet", "both", "either", "neither", "each", "every", "all",
                     "any", "few", "more", "most", "other", "some", "such", "no",
                     "only", "own", "same", "than", "too", "very", "just"}


def extract_key_facts(text: str, max_facts: int = 5) -> list[str]:
    """Extract key facts from text using regex patterns.

    Returns a deduplicated list of fact strings, up to max_facts.
    """
    if not text or not text.strip():
        return []

    facts: list[str] = []
    seen: set[str] = set()

    for pattern, group in _KEY_FACT_PATTERNS:
        for match in pattern.finditer(text):
            fact = match.group(group) if group else match.group(0)
            fact = fact.strip().rstrip(".,;:!?")
            # Filter: skip very short facts, pure stop words
            if len(fact) < 4:
                continue
            words = set(fact.lower().split())
            if words.issubset(_FACT_STOP_WORDS):
                continue
            # Normalize for dedup
            norm = fact.lower().strip()
            if norm not in seen:
                seen.add(norm)
                facts.append(fact)
            if len(facts) >= max_facts * 2:  # Collect more, then trim
                break
        if len(facts) >= max_facts * 2:
            break

    # Deduplicate by normalized form and limit
    unique: list[str] = []
    seen_norm: set[str] = set()
    for f in facts:
        norm = f.lower().strip()
        if norm not in seen_norm:
            seen_norm.add(norm)
            unique.append(f)
        if len(unique) >= max_facts:
            break

    return unique


# ── Summarization ───────────────────────────────────────────────

# Type for pluggable summarizer functions.
# Summarizers receive the chunk of messages plus optional keyword args
# (max_chars, max_facts) for configuration. The default summarizer
# accepts these; custom summarizers should accept **kwargs to forward them
# or define matching keyword parameters.
Summarizer = Callable[..., str]


def _call_summarizer(
    summarizer: Summarizer,
    messages: list[dict[str, Any]],
    *,
    max_chars: int,
) -> str:
    """Call old and new summarizer signatures without masking user errors."""
    try:
        parameters = inspect.signature(summarizer).parameters.values()
    except (TypeError, ValueError):
        return summarizer(messages)

    accepts_max_chars = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or parameter.name == "max_chars"
        for parameter in parameters
    )
    if accepts_max_chars:
        return summarizer(messages, max_chars=max_chars)
    return summarizer(messages)


def default_summarize_chunk(
    messages: list[dict[str, Any]],
    max_chars: int = 300,
    max_facts: int = 3,
) -> str:
    """Default summarizer: truncation + key fact extraction.

    Concatenates message contents, truncates to max_chars,
    and prepends extracted key facts.

    Args:
        messages: List of message dicts to summarize
        max_chars: Maximum characters for the truncated content
        max_facts: Maximum number of key facts to extract

    Returns:
        Summary string combining key facts and truncated content
    """
    if not messages:
        return ""

    # Concatenate all message contents (using _extract_text for multimodal)
    full_text = " ".join(
        _extract_text(m.get("content", ""))
        for m in messages
        if isinstance(m, dict) and m.get("content")
    )

    if not full_text.strip():
        return ""

    # Extract key facts from the full text
    facts = extract_key_facts(full_text, max_facts=max_facts)

    # Truncate content
    if len(full_text) > max_chars:
        truncated = full_text[:max_chars] + "..."
    else:
        truncated = full_text

    # Build summary
    parts: list[str] = []
    if facts:
        parts.append("[KEY FACTS] " + " | ".join(facts))
    parts.append("[CONTENT] " + truncated)

    return "\n".join(parts)


# ── CompressionOutcome ─────────────────────────────────────────

@dataclass
class CompressionOutcome:
    """Detailed result of a compress_messages_detailed() call.

    Attributes:
        messages: The compressed (or original) message list.
        original_tokens: Token count of the original input.
        compressed_tokens: Token count of the output.
        within_budget: True if compressed_tokens <= max_tokens.
        budget_enforced: True if budget trimming was applied.
        degradation_reason: Why budget enforcement occurred, if any.
    """

    messages: list[dict[str, Any]]
    original_tokens: int
    compressed_tokens: int
    within_budget: bool = True
    budget_enforced: bool = False
    degradation_reason: str | None = None


# ── Input validation ────────────────────────────────────────────

def _validate_compression_params(
    max_tokens: int,
    keep_last_n: int,
    compression_ratio: float,
) -> None:
    """Validate compression parameters, raising ValueError on invalid input."""
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be > 0, got {max_tokens}")
    if keep_last_n < 0:
        raise ValueError(f"keep_last_n must be >= 0, got {keep_last_n}")
    if not (0.0 <= compression_ratio <= 1.0):
        raise ValueError(
            f"compression_ratio must be between 0.0 and 1.0, got {compression_ratio}"
        )


# ── Indexed-segment compression pipeline ───────────────────────

def _compute_message_tokens(msg: dict[str, Any], *, image_token_cost: int = 85, audio_token_cost: int = 50) -> int:
    """Compute token cost for a single message."""
    content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
    text = _extract_text(content)
    tokens = count_tokens(text)
    tokens += _extract_image_count(content) * image_token_cost
    tokens += _extract_audio_count(content) * audio_token_cost
    tokens += 4  # role overhead
    return tokens


def compress_messages_detailed(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 8000,
    compression_ratio: float = 0.5,
    keep_last_n: int = 3,
    summarizer: Summarizer | None = None,
    image_token_cost: int = 85,
    audio_token_cost: int = 50,
) -> CompressionOutcome:
    """Compress messages using indexed-segment reassembly with shared budget.

    Uses an index-based approach to preserve the original position of all
    messages. Protected messages (system + recent) keep their exact positions;
    contiguous unprotected ranges are summarized and anchored at their first
    original index.

    Args:
        messages: List of message dicts with "role" and "content" keys.
        max_tokens: Hard token budget for the output.
        compression_ratio: Target ratio (guidance for summarizer budget).
        keep_last_n: Number of most recent non-system messages to protect.
        summarizer: Pluggable summarizer function.
        image_token_cost: Estimated tokens per image part.
        audio_token_cost: Estimated tokens per audio part.

    Returns:
        CompressionOutcome with detailed status.
    """
    _validate_compression_params(max_tokens, keep_last_n, compression_ratio)
    summarizer = summarizer or default_summarize_chunk

    original_tokens = count_message_tokens(
        messages, image_token_cost=image_token_cost, audio_token_cost=audio_token_cost
    )

    # Under threshold — no compression needed
    if original_tokens <= max_tokens:
        return CompressionOutcome(
            messages=list(messages),
            original_tokens=original_tokens,
            compressed_tokens=original_tokens,
            within_budget=True,
            budget_enforced=False,
        )

    # Nothing to compress
    if not messages:
        return CompressionOutcome(
            messages=[],
            original_tokens=0,
            compressed_tokens=0,
            within_budget=True,
            budget_enforced=False,
        )

    # ── Identify protected and unprotected indices ────────────
    non_system_indices = [
        i for i, m in enumerate(messages)
        if isinstance(m, dict) and m.get("role") != "system"
    ]

    # Protected: all system messages + last keep_last_n non-system messages
    protected_set: set[int] = set()
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "system":
            protected_set.add(i)

    if keep_last_n > 0 and len(non_system_indices) <= keep_last_n:
        # All non-system messages are protected (fewer than or equal to keep_last_n)
        protected_set.update(non_system_indices)
    elif keep_last_n > 0:
        recent_indices = non_system_indices[-keep_last_n:]
        protected_set.update(recent_indices)

    unprotected_indices = [
        i for i in range(len(messages)) if i not in protected_set
    ]

    # ── Compute protected token cost ─────────────────────────
    protected_tokens = sum(
        _compute_message_tokens(
            messages[i], image_token_cost=image_token_cost, audio_token_cost=audio_token_cost
        )
        for i in sorted(protected_set)
    )

    # If protected content alone exceeds budget, return with signal
    if protected_tokens > max_tokens:
        result_messages = [
            messages[i] for i in sorted(protected_set)
        ]
        compressed_tokens = count_message_tokens(
            result_messages, image_token_cost=image_token_cost, audio_token_cost=audio_token_cost
        )
        return CompressionOutcome(
            messages=result_messages,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            within_budget=False,
            budget_enforced=True,
            degradation_reason="protected_content_exceeds_budget",
        )

    # ── Group contiguous unprotected ranges ───────────────────
    ranges: list[tuple[int, list[int]]] = []  # (start_index, [indices])
    current_range: list[int] = []
    current_start: int | None = None

    for idx in unprotected_indices:
        if current_range and idx == current_range[-1] + 1:
            current_range.append(idx)
        else:
            if current_range:
                ranges.append((current_start, current_range))
            current_range = [idx]
            current_start = idx
    if current_range:
        ranges.append((current_start, current_range))

    # ── Shared summary budget ────────────────────────────────
    target_tokens = min(max_tokens, max(1, int(original_tokens * compression_ratio)))
    summary_budget = target_tokens - protected_tokens
    if summary_budget <= 0 or not ranges:
        result_messages = [messages[i] for i in sorted(protected_set)]
        compressed_tokens = count_message_tokens(
            result_messages, image_token_cost=image_token_cost, audio_token_cost=audio_token_cost
        )
        return CompressionOutcome(
            messages=result_messages,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            within_budget=compressed_tokens <= max_tokens,
            budget_enforced=True,
            degradation_reason="no_summary_budget",
        )

    # Account for message overhead (4 tokens per summary message)
    num_ranges = len(ranges)
    overhead_per_summary = 4
    available_for_content = max(0, summary_budget - (num_ranges * overhead_per_summary))
    max_chars_per_range = max(1, (available_for_content // max(1, num_ranges)) * 4)

    # ── Generate summaries ───────────────────────────────────
    summaries: list[tuple[int, dict[str, Any]]] = []  # (anchor_index, summary_msg)
    for start_idx, indices in ranges:
        chunk_msgs = [messages[i] for i in indices]
        summary_text = _call_summarizer(
            summarizer, chunk_msgs, max_chars=max_chars_per_range
        )
        if summary_text:
            # Use role from the first message in the range
            role = chunk_msgs[0].get("role", "user") if isinstance(chunk_msgs[0], dict) else "user"
            summary_msg = {
                "role": role,
                "content": f"[COMPRESSED {len(indices)} messages]\n{summary_text}",
            }
            summaries.append((start_idx, summary_msg))

    # ── Enforce shared budget ────────────────────────────────
    budget_enforced = False
    degradation_reason: str | None = None

    # Check total tokens with all summaries
    protected_msgs = [messages[i] for i in sorted(protected_set)]
    summary_msgs = [msg for _, msg in summaries]
    all_msgs = protected_msgs + summary_msgs
    total_tokens = count_message_tokens(
        all_msgs, image_token_cost=image_token_cost, audio_token_cost=audio_token_cost
    )

    # If over budget, remove oldest summaries (lowest anchor index) first
    if total_tokens > max_tokens and summaries:
        budget_enforced = True
        degradation_reason = "summaries_trimmed_to_fit_budget"

        # Sort by anchor index descending so we can pop the oldest (lowest index)
        summaries.sort(key=lambda x: x[0], reverse=True)

        while len(summaries) > 1 and total_tokens > max_tokens:
            summaries.pop()  # Remove oldest (last in descending sort = lowest index)
            summary_msgs = [msg for _, msg in summaries]
            all_msgs = protected_msgs + summary_msgs
            total_tokens = count_message_tokens(
                all_msgs, image_token_cost=image_token_cost, audio_token_cost=audio_token_cost
            )

        # If one summary remains and still over budget, trim it. Drop it if
        # even the summary message overhead cannot fit in the hard budget.
        if summaries and total_tokens > max_tokens:
            anchor, last_summary = summaries[-1]
            content = last_summary.get("content", "")
            target_chars = max(0, (summary_budget - 4) * 4)
            while content and total_tokens > max_tokens:
                content = content[:max(0, len(content) - 50)]
                if len(content) > target_chars:
                    content = content[:target_chars]
                last_summary["content"] = content
                all_msgs = protected_msgs + [last_summary]
                total_tokens = count_message_tokens(
                    all_msgs, image_token_cost=image_token_cost, audio_token_cost=audio_token_cost
                )
            if total_tokens > max_tokens or not content.strip():
                summaries.pop()
            else:
                summaries[-1] = (anchor, last_summary)

    # ── Reassemble by original index ─────────────────────────
    indexed_msgs: list[tuple[int, dict[str, Any]]] = []
    # Protected messages at their original positions
    for i in sorted(protected_set):
        indexed_msgs.append((i, messages[i]))
    # Summaries anchored at their range start positions
    for anchor_idx, summary_msg in summaries:
        indexed_msgs.append((anchor_idx, summary_msg))

    # Sort by original index to preserve ordering
    indexed_msgs.sort(key=lambda x: x[0])
    result_messages = [msg for _, msg in indexed_msgs]

    compressed_tokens = count_message_tokens(
        result_messages, image_token_cost=image_token_cost, audio_token_cost=audio_token_cost
    )

    return CompressionOutcome(
        messages=result_messages,
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        within_budget=compressed_tokens <= max_tokens,
        budget_enforced=budget_enforced,
        degradation_reason=degradation_reason,
    )


# ── Deprecated compatibility wrapper ────────────────────────────

def compress_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 8000,
    compression_ratio: float = 0.5,
    keep_last_n: int = 3,
    summarizer: Summarizer | None = None,
    image_token_cost: int = 85,
    audio_token_cost: int = 50,
) -> tuple[list[dict[str, Any]], int, int]:
    """Compress a list of messages to fit within a token budget.

    .. deprecated::
        Use ``compress_messages_detailed()`` instead, which returns a
        ``CompressionOutcome`` with full status information.

    Returns:
        Tuple of (compressed_messages, original_token_count, compressed_token_count)
    """
    warnings.warn(
        "compress_messages() is deprecated; use compress_messages_detailed() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    outcome = compress_messages_detailed(
        messages,
        max_tokens=max_tokens,
        compression_ratio=compression_ratio,
        keep_last_n=keep_last_n,
        summarizer=summarizer,
        image_token_cost=image_token_cost,
        audio_token_cost=audio_token_cost,
    )
    return outcome.messages, outcome.original_tokens, outcome.compressed_tokens


# ── Legacy helper (kept for backward compat) ────────────────────

def chunk_messages(
    messages: list[dict[str, Any]],
    keep_last_n: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split messages into three groups, preserving original ordering.

    Returns:
        system_msgs: System messages (always preserved, in original order)
        recent_msgs: Last N non-system messages (kept intact)
        old_msgs: Everything else (candidates for compression)
    """
    if keep_last_n < 0:
        keep_last_n = 0

    non_system = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]

    if len(non_system) <= keep_last_n or keep_last_n == 0:
        # keep_last_n=0 means no messages are kept intact — all non-system are old
        if keep_last_n == 0 and non_system:
            return (
                [m for m in messages if isinstance(m, dict) and m.get("role") == "system"],
                [],
                list(non_system),
            )
        return (
            [m for m in messages if isinstance(m, dict) and m.get("role") == "system"],
            non_system,
            [],
        )

    recent_msgs = non_system[-keep_last_n:]
    old_msgs = non_system[:-keep_last_n]

    # System messages in their original positions
    system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]

    return system_msgs, recent_msgs, old_msgs
