# Memorant Auto-Ontology

Extract entities and relations from claim units using LLM-powered analysis.

## Installation

```bash
cd memorant-ontology
python -m pip install -e ".[test]"
python -m pip install -e ".[embeddings]"
python -m pip install litellm httpx pydantic
```

> Note: `.[test,embeddings]` does NOT work — install the two extras separately.

## Quick Start

```bash
# Initialize ontology schema
memorant-ontology --db ./memorant.db init

# Check health
memorant-ontology --db ./memorant.db status

# Run extraction worker (one tick)
memorant-ontology --db ./memorant.db worker --limit 10 --rpm 30
```

## Configuration

OntologyConfig is a frozen dataclass with sensible defaults:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | `False` | Enable/disable ontology extraction |
| `provider` | `"minimax"` | LLM provider |
| `model` | `"MiniMax-M3"` | LLM model |
| `timeout_seconds` | `30` | LLM call timeout |
| `max_attempts` | `3` | Queue retry attempts |
| `provider_rpm` | `30` | LLM requests per minute |
| `lease_seconds` | `600` | Queue lease timeout |
| `max_cost_usd_per_day` | `5.0` | Daily cost hard stop |
| `entity_types_allowed` | `[person, place, project, concept, tool, organization, date]` | Allowed entity types |
| `relations_allowed` | `[manages, uses, owns, works_on, ...]` | Allowed relations |
| `functional_relations` | `[located_in, is_a, instance_of, created_in]` | Relations that trigger contradiction review |
| `redact_pii` | `True` | Redact PII before LLM |
| `no_retention_mode` | `True` | Zero-retention mode |

## CLI Commands

```bash
memorant-ontology status                       # Health report
memorant-ontology find "name" --depth 2        # Find entities + graph walk
memorant-ontology invalidate "name"            # Invalidate entity
memorant-ontology merge <survivor_id> <loser_id>  # Merge entities
memorant-ontology purge --older-than 30        # Purge invalid entries
memorant-ontology rollback                     # Drop all ontology tables
memorant-ontology reextract --limit 100        # Re-queue claims
memorant-ontology sweep-dead-letter            # Re-enqueue dead-letter entries
memorant-ontology enable                       # Enable for DB
memorant-ontology disable                      # Disable for DB
memorant-ontology worker --db <path> --limit 10 --rpm 30  # Run worker
memorant-ontology init                         # Initialize schema
memorant-ontology import-jsonl <path>          # Bulk load claims
```

## Scheduling

### Windows Task Scheduler

```bash
schtasks /Create /TN "memorant-ontology-drain" /SC MINUTE /MO 5 /TR "\"C:\Python312\python.exe\" -m memorant_ontology.worker --db \"C:\mempalace\memorant_v1.db\" --limit 10 --rpm 30"
```

### Linux/VPS (crontab)

```
*/5 * * * * /usr/bin/python3 -m memorant_ontology.worker --db /home/user/.mempalace/memorant_v1.db --limit 10 --rpm 30
```

One schedule per DB. Two would be safe but wasteful.

## Privacy

- PII (email, phone, SSN, credit card) is redacted before LLM calls
- Zero-retention mode available (`no_retention_mode=True`)
- Soak testing uses synthetic data only — never real user data
- Dead-letter files are sibling to DB for visibility

## Architecture

```
Claim → add_claim() → hook → enqueue() → queue table
                                              ↓
Worker tick: dequeue() → LLM extract → write entities/relations
                                              ↓
Resonance hook: query → entities/relations → [ANDRE_ONTOLOGY] block
```

## Trust Tiers

| Tier | Description |
|------|-------------|
| `operator` | Manually assigned, highest trust |
| `verified` | Verified by a trusted process |
| `derived` | Computed from other claims |
| `untrusted` | Default for imported claims |

## Contradiction Detection

Only `functional_relations` (e.g., `located_in`, `is_a`) trigger contradiction review.
Multi-valued relations (e.g., `uses`, `manages`) do not.

## Testing

```bash
python -m pytest tests/ -q

# Offline evaluation (manual, not in CI)
python -m eval.run_gold_eval --gold tests/fixtures/gold_set.jsonl
```

Pass threshold: F1 ≥ 0.70.
