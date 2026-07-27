"""Memorant Auto-Ontology — extract entities and relations from claim units.

Public API:
    patch_memorant_store(): monkey-patch MemorantStore for ontology extraction
"""

from __future__ import annotations

import functools
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import OntologyConfig
    from .extractor import OntologyExtractor
    from .store import OntologyStore

_PATCHED: list[bool] = [False]  # per-process guard, NOT multi-process (m12)
_PER_DB_ENABLED: dict[str, bool | None] = {}  # cache: db_path -> enabled (M1 fix)


def _is_db_enabled(db_path: str, config_enabled: bool) -> bool:
    """Check if ontology is enabled for this database (reads meta table).

    Cached per db_path per process. CLI enable/disable writes to this table.
    If the meta table has no entry, falls back to config_enabled (backward compatible).
    Returns False on any error (safe default for read path).
    """
    if db_path in _PER_DB_ENABLED:
        return _PER_DB_ENABLED[db_path] is True
    try:
        from .store import OntologyStore
        from .config import OntologyConfig
        store = OntologyStore(db_path, OntologyConfig())
        with store.connect() as db:
            row = db.execute(
                "SELECT value FROM memorant_ontology_meta WHERE key = 'enabled'"
            ).fetchone()
        if row is not None:
            # Meta table explicitly set — use it
            enabled = row["value"] == "1"
        else:
            # No meta entry — fall back to config (backward compatible)
            enabled = config_enabled
        _PER_DB_ENABLED[db_path] = enabled
        return enabled
    except Exception:
        _PER_DB_ENABLED[db_path] = config_enabled
        return config_enabled


@functools.lru_cache(maxsize=8)
def _ext_for_db_path(db_path: str) -> "OntologyExtractor":
    """Process-local cache. LRU bounded; survives long-running gateway."""
    from .extractor import OntologyExtractor
    from .store import OntologyStore
    from .config import OntologyConfig

    return OntologyExtractor(OntologyStore(db_path, OntologyConfig()))


def _dead_letter(self, cid: str, exc: Exception) -> None:
    """Write a dead-letter entry for a failed ontology operation."""
    try:
        dl = Path(self.db_path).with_suffix(".dead-letter.jsonl")
        with dl.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "claim_id": cid,
                "error": repr(exc),
                "ts": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
    except Exception:
        pass  # never break the original operation


def _invalidate_entities_for_claim(ext: "OntologyExtractor", claim_id: str) -> None:
    """Invalidate ontology entities tied to a claim. Safe to call on any claim_id."""
    try:
        ext.store.invalidate_entity_from_claim(claim_id)
    except Exception as exc:
        # Dead-letter fallback — entity invalidation must never break the
        # correction/invalidation that triggered it.
        pass


def _enqueue_new_claim(ext: "OntologyExtractor", cid: str) -> None:
    """Enqueue a newly created claim for ontology extraction."""
    try:
        ext.enqueue(cid)
    except sqlite3.IntegrityError:
        pass  # Already queued
    except Exception as exc:
        pass  # Dead-letter is handled by enqueue internals


def patch_memorant_store(config: "OntologyConfig | None" = None) -> None:
    """Monkey-patch MemorantStore to hook claims into the ontology pipeline.

    Patches: add_claim, invalidate_claim, supersede_claim, correct_claim,
    and invalidate_claims_for_fact.

    Per-process (m12). Multiple processes are safe: each gets its own cache.
    The dead-letter file is shared (sibling of DB), so failures are visible.

    Args:
        config: OntologyConfig to use. If omitted, the default config is used
            (ontology is disabled by default; pass enabled=True to opt in).
    """
    from memorant import MemorantStore
    from .config import OntologyConfig

    if config is None:
        config = OntologyConfig()

    if _PATCHED[0]:
        return
    _PATCHED[0] = True

    # ── Patch add_claim ──────────────────────────────────────

    original_add = MemorantStore.add_claim

    def hooked_add(self, content, *args, **kwargs):
        cid = original_add(self, content, *args, **kwargs)
        try:
            if not config.enabled or not _is_db_enabled(str(self.db_path), config.enabled):
                return cid
            ext = _ext_for_db_path(str(self.db_path))
            ext.enqueue(cid)
        except sqlite3.IntegrityError:
            pass  # Already queued
        except Exception as exc:
            _dead_letter(self, cid, exc)
        return cid

    MemorantStore.add_claim = hooked_add

    # ── Patch invalidate_claim ────────────────────────────────
    # H5 fix: wire to invalidate ontology entities from the retracted claim

    original_invalidate = MemorantStore.invalidate_claim

    def hooked_invalidate(self, claim_id, *, reason="retraction"):
        result = original_invalidate(self, claim_id, reason=reason)
        if not config.enabled or not _is_db_enabled(str(self.db_path), config.enabled) or result == 0:
            return result
        ext = _ext_for_db_path(str(self.db_path))
        _invalidate_entities_for_claim(ext, claim_id)
        return result

    MemorantStore.invalidate_claim = hooked_invalidate

    # ── Patch supersede_claim ─────────────────────────────────
    # H5 fix: invalidate old claim's entities + enqueue new claim for extraction

    original_supersede = MemorantStore.supersede_claim

    def hooked_supersede(self, claim_id, new_content, *,
                         source_pointer="correction", reason="superseded"):
        new_id = original_supersede(self, claim_id, new_content,
                                    source_pointer=source_pointer, reason=reason)
        if not config.enabled or not _is_db_enabled(str(self.db_path), config.enabled):
            return new_id
        ext = _ext_for_db_path(str(self.db_path))
        _invalidate_entities_for_claim(ext, claim_id)
        _enqueue_new_claim(ext, new_id)
        return new_id

    MemorantStore.supersede_claim = hooked_supersede

    # ── Patch correct_claim ───────────────────────────────────
    # H5 fix: same pattern as supersede — invalidate old, enqueue new

    original_correct = MemorantStore.correct_claim

    def hooked_correct(self, claim_id, corrected_content, *,
                       source_pointer="correction", reason="correction"):
        new_id = original_correct(self, claim_id, corrected_content,
                                  source_pointer=source_pointer, reason=reason)
        if not config.enabled or not _is_db_enabled(str(self.db_path), config.enabled):
            return new_id
        ext = _ext_for_db_path(str(self.db_path))
        _invalidate_entities_for_claim(ext, claim_id)
        _enqueue_new_claim(ext, new_id)
        return new_id

    MemorantStore.correct_claim = hooked_correct

    # ── Patch invalidate_claims_for_fact ──────────────────────
    # H5 partial fix: pre-query claim IDs before invalidation, then
    # invalidate entities. Full fix would need the original method to
    # return IDs; pre-query is a best-effort approximation.

    original_invalidate_fact = MemorantStore.invalidate_claims_for_fact

    def hooked_invalidate_fact(self, fact_id):
        # Pre-query: collect claim IDs that will be invalidated
        pre_ids = []
        try:
            if config.enabled and _is_db_enabled(str(self.db_path), config.enabled):
                with self.connect() as db:
                    pre_ids = [
                        r["id"] for r in db.execute(
                            "SELECT id FROM claim_units WHERE is_valid = 1 AND "
                            "(fact_refs LIKE ? OR source_pointer LIKE ?)",
                            (f'%"{fact_id}"%', f"fact:{fact_id}%"),
                        ).fetchall()
                    ]
        except Exception:
            pass  # Pre-query failure must not block the original operation

        result = original_invalidate_fact(self, fact_id)
        if config.enabled and _is_db_enabled(str(self.db_path), config.enabled) and result > 0 and pre_ids:
            ext = _ext_for_db_path(str(self.db_path))
            for cid in pre_ids:
                _invalidate_entities_for_claim(ext, cid)
        return result

    MemorantStore.invalidate_claims_for_fact = hooked_invalidate_fact

    # ── Patch resonate ────────────────────────────────────────
    # H4 fix: append ontology resonance block to MemorantStore.resonate output

    original_resonate = MemorantStore.resonate

    def hooked_resonate(self, query, *args, **kwargs):
        result = original_resonate(self, query, *args, **kwargs)
        try:
            if not config.enabled or not _is_db_enabled(str(self.db_path), config.enabled):
                return result
            # Append ontology resonance
            from .resonance import render_ontology_block
            from .store import OntologyStore
            from .config import OntologyConfig
            ostore = OntologyStore(str(self.db_path), OntologyConfig())
            oblock = render_ontology_block(query, ostore, OntologyConfig())
            if oblock:
                if result:
                    result += "\n" + oblock
                else:
                    result = oblock
        except Exception:
            pass
        return result

    MemorantStore.resonate = hooked_resonate


__all__ = ["patch_memorant_store"]
__version__ = "1.0.0-rc.1"
