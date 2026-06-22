"""Hermes adapter for Memorant v1 — injects resonance context via pre_llm_call hook."""
from __future__ import annotations
from pathlib import Path
from memorant import MemorantStore, StoreConfig, TrustPolicy

# Default policy: extraction and manual sources are verified
_DEFAULT_POLICY = TrustPolicy(rules=[
    {"source_type": "extraction", "tier": "verified"},
    {"source_type": "fact", "tier": "verified"},
    {"source_type": "diary", "tier": "verified"},
    {"source_type": "manual", "tier": "verified"},
    {"source_type": "correction", "tier": "operator"},
])

def pre_llm_call_context(
    user_message: str,
    *,
    db_path: str | Path | None = None,
    session_id: str = "",
) -> dict[str, str] | None:
    """Return resonance context for injection before an LLM call.

    Args:
        user_message: The user's message to resonate against
        db_path: Path to Memorant v1 database (default: ~/.mempalace/memorant_v1.db)
        session_id: Session identifier for logging

    Returns:
        dict with 'context' key containing resonance block, or None if no resonance
    """
    if not user_message.strip():
        return None

    if db_path is None:
        db_path = Path.home() / ".mempalace" / "memorant_v1.db"

    store = MemorantStore(db_path, StoreConfig(trust_policy=_DEFAULT_POLICY))
    text = store.resonate(user_message, session_id=session_id)

    return {"context": text} if text else None
