"""Memorant v1 — trustworthy local-first memory substrate for AI agents.

Primary API: MemorantStore
Deprecated alias: MemoryPalace (v0.1 compat)
"""

from .core import (
    MemorantStore,
    MemoryPalace,
    StoreConfig,
    Claim,
    ClaimSearchDebug,
    HygieneReport,
)
from .trust import TrustTier, TrustPolicy, redact_content

__all__ = [
    "MemorantStore",
    "MemoryPalace",
    "StoreConfig",
    "Claim",
    "ClaimSearchDebug",
    "HygieneReport",
    "TrustTier",
    "TrustPolicy",
    "redact_content",
]
__version__ = "1.0.0-rc.1"
