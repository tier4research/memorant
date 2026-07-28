"""Worker for ontology extraction — dequeue, extract, write results.

Usage:
    python -m memorant_ontology.worker --db <path> --limit 10 --rpm 30
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from .config import OntologyConfig
from .store import OntologyStore

logger = logging.getLogger(__name__)


def run_worker(db_path: str, limit: int = 10, rpm: int = 30) -> None:
    """Run one tick of the ontology extraction worker.

    1. Recover expired leases
    2. Dequeue up to `limit` claims
    3. Extract entities/relations via LLM
    4. Write results to DB
    5. Track cost against max_cost_usd_per_day
    """
    config = OntologyConfig()
    store = OntologyStore(db_path, config)
    store.init()

    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    delay = 60.0 / max(rpm, 1)  # seconds between LLM calls

    # 1. Recover expired leases
    from .queue import lease_recover
    recovered = lease_recover(store, config.lease_seconds)
    if recovered:
        print(f"[{worker_id}] Recovered {recovered} expired leases")

    # Check cost budget
    daily_cost = _get_daily_cost(store)
    if daily_cost >= config.max_cost_usd_per_day:
        print(f"[{worker_id}] Daily cost limit reached (${daily_cost:.2f}/${config.max_cost_usd_per_day:.2f}). Skipping.")
        return

    # 2. Dequeue and process
    from .queue import dequeue, complete, fail
    from .extractor import OntologyExtractor

    extractor = OntologyExtractor(store)
    processed = 0
    total_cost = 0.0
    consecutive_failures = 0

    for _ in range(limit):
        # Check if ontology is disabled for this database
        from . import _is_db_enabled
        if not _is_db_enabled(str(store.db_path), True):
            print(f"[{worker_id}] Ontology disabled for this database — stopping worker")
            break

        # Check cost budget
        if daily_cost + total_cost >= config.max_cost_usd_per_day:
            print(f"[{worker_id}] Cost limit reached mid-batch. Processed {processed}.")
            break

        row = dequeue(store, worker_id)
        if not row:
            break

        queue_id = row["id"]
        claim_id = row["claim_id"]

        try:
            # Get claim content and trust tier
            with store.connect() as db:
                claim = db.execute(
                    "SELECT content, trust_tier FROM claim_units WHERE id = ?", (claim_id,)
                ).fetchone()

            if not claim:
                fail(store, queue_id, "Claim not found", config.max_attempts)
                # Still delay on failure to avoid hammering the queue
                time.sleep(delay * _backoff_multiplier(consecutive_failures))
                consecutive_failures += 1
                continue

            # Extract
            result = extractor.extract_claim(claim["content"])
            cost = result.get("cost_usd", 0.0)
            total_cost += cost

            # Record cost immediately — if _write_results fails, the spend is
            # still tracked in the daily budget (the cost_usd column is updated
            # again in complete() on success).
            with store.connect() as db:
                db.execute(
                    "UPDATE memorant_ontology_queue SET cost_usd = ? WHERE id = ?",
                    (cost, queue_id),
                )
                db.commit()

            # Write results
            _write_results(store, claim_id, result, config, claim["trust_tier"])

            # Mark complete
            complete(store, queue_id, cost)
            processed += 1
            consecutive_failures = 0

        except Exception as exc:
            logger.exception("Worker failed processing queue_id=%s claim_id=%s: %s", queue_id, claim_id, exc)
            fail(store, queue_id, repr(exc), config.max_attempts)
            consecutive_failures += 1
        finally:
            # Rate limit applies regardless of success/failure — failing fast
            # on a broken provider without delay burns through the retry budget.
            time.sleep(delay * _backoff_multiplier(consecutive_failures))

    # Sweep dead letter
    from .queue import sweep_dead_letter
    swept = sweep_dead_letter(store)

    # Check escalation
    from .queue import check_escalation
    if check_escalation(store, config):
        print(f"[{worker_id}] ALERT: Escalation threshold reached!")

    print(f"[{worker_id}] Processed {processed} claims, cost ${total_cost:.4f}, swept {swept} dead-letter")


def _get_daily_cost(store: OntologyStore) -> float:
    """Get total cost for today from queue table."""
    with store.connect() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) as total FROM memorant_ontology_queue "
            "WHERE completed_at > datetime('now', '-24 hours')"
        ).fetchone()
    return float(row["total"])


def _backoff_multiplier(consecutive_failures: int, base_delay: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff multiplier for failure bursts.

    Returns a multiplier such that effective delay = base * multiplier.
    At 0 failures: 1.0 (no backoff).
    At 1 failure: ~2.0
    At N failures: min(2**N, cap/base).
    """
    if consecutive_failures <= 0:
        return 1.0
    multiplier = min(2 ** consecutive_failures, cap / max(base_delay, 0.001))
    return multiplier


def _write_results(
    store: OntologyStore,
    claim_id: str,
    result: dict,
    config: OntologyConfig,
    claim_trust_tier: str | None = None,
) -> None:
    """Write extracted entities and relations to the database."""
    from .normalizer import normalize_name

    entities = result.get("entities", [])
    relations = result.get("relations", [])
    prompt_version = result.get("prompt_version", "v1")
    inherited_tier = claim_trust_tier or "derived"
    eager_trust = config.trust_propagation == "eager"

    with store.connect() as db:
        entity_id_map = {}  # name -> id

        for e in entities:
            eid = e.get("id", str(uuid.uuid4()))
            name = e["name"]
            name_norm = normalize_name(name)
            entity_type = e["entity_type"]
            desc = e.get("description", "")

            # Upsert: idempotent entity write (M3 retry fix)
            existing = db.execute(
                "SELECT id, reinforcement_count, last_seen_claim_id FROM memorant_ontology_entities "
                "WHERE tenant_id = 'default' AND name_normalized = ? COLLATE NOCASE AND entity_type = ?",
                (name_norm, entity_type),
            ).fetchone()

            if existing and existing["last_seen_claim_id"] == claim_id:
                # Idempotency guard: already written for this claim on a prior retry.
                # Don't double-bump reinforcement_count. Still map for relation wiring.
                entity_id_map[name_norm] = existing["id"]
                db.execute(
                    "UPDATE memorant_ontology_entities SET updated_at = datetime('now') WHERE id = ?",
                    (existing["id"],),
                )
            elif existing:
                # Reinforce existing entity (first time seen from this claim)
                if eager_trust:
                    db.execute(
                        "UPDATE memorant_ontology_entities SET "
                        "reinforcement_count = reinforcement_count + 1, "
                        "last_seen_claim_id = ?, last_seen_at = datetime('now'), "
                        "updated_at = datetime('now'), trust_tier = ? WHERE id = ?",
                        (claim_id, inherited_tier, existing["id"]),
                    )
                else:
                    db.execute(
                        "UPDATE memorant_ontology_entities SET "
                        "reinforcement_count = reinforcement_count + 1, "
                        "last_seen_claim_id = ?, last_seen_at = datetime('now'), "
                        "updated_at = datetime('now') WHERE id = ?",
                        (claim_id, existing["id"]),
                    )
                entity_id_map[name_norm] = existing["id"]
            else:
                db.execute(
                    "INSERT INTO memorant_ontology_entities "
                    "(id, name, name_normalized, entity_type, description, "
                    "first_seen_claim_id, last_seen_claim_id, last_seen_at, prompt_version, trust_tier) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)",
                    (eid, name, name_norm, entity_type, desc,
                     claim_id, claim_id, prompt_version, inherited_tier),
                )
                entity_id_map[name_norm] = eid

        for r in relations:
            src_norm = normalize_name(r["source"])
            tgt_norm = normalize_name(r["target"])
            src_id = entity_id_map.get(src_norm)
            tgt_id = entity_id_map.get(tgt_norm)

            if not src_id or not tgt_id:
                continue

            rid = str(uuid.uuid4())
            db.execute(
                "INSERT OR IGNORE INTO memorant_ontology_relations "
                "(id, source_entity_id, target_entity_id, relation, "
                "source_claim_id, confidence, prompt_version, trust_tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, src_id, tgt_id, r["relation"],
                 claim_id, r.get("confidence", 0.5), prompt_version, inherited_tier),
            )

        # Check contradictions for each written relation (H3 fix)
        try:
            from .contradict import check_contradictions
            for r in relations:
                src_norm = normalize_name(r.get("source", ""))
                tgt_norm = normalize_name(r.get("target", ""))
                rel = r.get("relation", "")
                src_id = entity_id_map.get(src_norm)
                tgt_id = entity_id_map.get(tgt_norm)
                if src_id and tgt_id and rel:
                    check_contradictions(store, src_id, rel, tgt_id, config, db=db)
        except Exception:
            logger.exception("Contradiction check failed for claim %s — continuing", claim_id)
            # contradiction check must never break _write_results

        # Mark claim as processed
        db.execute(
            "UPDATE claim_units SET ontology_processed_at = datetime('now'), "
            "ontology_prompt_version = ?, ontology_cost_usd = ? WHERE id = ?",
            (prompt_version, result.get("cost_usd", 0.0), claim_id),
        )
        db.commit()
