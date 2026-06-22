"""Message compression logic for Context Tuner.

Provides token counting, key fact extraction, message chunking,
and summarization. Default summarizer uses truncation + key fact
extraction. Designed to be extensible for LLM-based summarizers.

Architecture:
- count_tokens(): approximate token count (4 chars ≈ 1 token)
- extract_key_facts(): regex-based key fact extraction
- chunk_messages(): split messages into keep/recent/old groups
- summarize_chunk(): truncate + extract facts for a chunk
- compress_messages(): main compression pipeline
"""

from __future__ import annotations

import re
from typing import Any, Callable


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


def count_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Count approximate tokens across all messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        total += count_tokens(content)
        # Add overhead for role marker (~4 tokens per message)
        total += 4
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


# ── Message chunking ────────────────────────────────────────────

def chunk_messages(
    messages: list[dict[str, Any]],
    keep_last_n: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split messages into three groups:

    Returns:
        system_msgs: System messages (always preserved)
        recent_msgs: Last N non-system messages (kept intact)
        old_msgs: Everything else (candidates for compression)
    """
    system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    non_system = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]

    if len(non_system) <= keep_last_n:
        return system_msgs, non_system, []

    recent_msgs = non_system[-keep_last_n:]
    old_msgs = non_system[:-keep_last_n]

    return system_msgs, recent_msgs, old_msgs


# ── Summarization ───────────────────────────────────────────────

# Type for pluggable summarizer functions.
# Summarizers receive the chunk of messages plus optional keyword args
# (max_chars, max_facts) for configuration. The default summarizer
# accepts these; custom summarizers should accept **kwargs to forward them
# or define matching keyword parameters.
Summarizer = Callable[..., str]


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

    # Concatenate all message contents
    full_text = " ".join(
        m.get("content", "") for m in messages
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


# ── Main compression pipeline ───────────────────────────────────

def compress_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 8000,
    compression_ratio: float = 0.5,
    keep_last_n: int = 3,
    summarizer: Summarizer | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Compress a list of messages to fit within a token budget.

    If the total token count is under max_tokens, returns messages
    unchanged. Otherwise, compresses older messages while preserving
    system messages and recent turns.

    Compression strategy:
    1. Count tokens; if under limit, return as-is
    2. Split into system, recent, and old messages
    3. Group old messages into chunks by role transitions
    4. Summarize each chunk using the configured summarizer
    5. Assemble: system + summarized chunks + recent messages

    Args:
        messages: List of message dicts with "role" and "content" keys
        max_tokens: Token threshold; compression triggers if exceeded
        compression_ratio: Target ratio of compressed to original (guidance)
        keep_last_n: Number of most recent messages to keep intact
        summarizer: Pluggable summarizer function (default: default_summarize_chunk)

    Returns:
        Tuple of (compressed_messages, original_token_count, compressed_token_count)
    """
    summarizer = summarizer or default_summarize_chunk

    original_tokens = count_message_tokens(messages)

    # Under threshold — no compression needed
    if original_tokens <= max_tokens:
        return list(messages), original_tokens, original_tokens

    # Split messages
    system_msgs, recent_msgs, old_msgs = chunk_messages(messages, keep_last_n=keep_last_n)

    # If nothing to compress, return as-is
    if not old_msgs:
        return list(messages), original_tokens, original_tokens

    # Group old messages into chunks (by role transitions for context)
    chunks: list[list[dict[str, Any]]] = []
    current_chunk: list[dict[str, Any]] = []
    current_role: str | None = None

    for msg in old_msgs:
        role = msg.get("role", "unknown") if isinstance(msg, dict) else "unknown"
        if role != current_role and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
        current_chunk.append(msg)
        current_role = role

    if current_chunk:
        chunks.append(current_chunk)

    # Calculate max characters per chunk based on compression ratio
    # Target: compressed tokens = original_tokens * compression_ratio
    target_compressed_tokens = int(original_tokens * compression_ratio)
    # Reserve tokens for system + recent messages
    system_recent_tokens = count_message_tokens(system_msgs + recent_msgs)
    available_for_old = max(100, target_compressed_tokens - system_recent_tokens)
    max_chars_per_chunk = max(100, (available_for_old // max(1, len(chunks))) * 4)

    # Summarize each chunk
    summarized_msgs: list[dict[str, Any]] = []
    for chunk in chunks:
        summary = summarizer(chunk, max_chars=max_chars_per_chunk)
        if summary:
            # Use the role from the first message in the chunk
            role = chunk[0].get("role", "user") if isinstance(chunk[0], dict) else "user"
            summarized_msgs.append({
                "role": role,
                "content": f"[COMPRESSED {len(chunk)} messages]\n{summary}",
            })

    # Assemble final message list
    compressed = list(system_msgs) + summarized_msgs + list(recent_msgs)
    compressed_tokens = count_message_tokens(compressed)

    return compressed, original_tokens, compressed_tokens
