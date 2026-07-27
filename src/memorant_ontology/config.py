"""Ontology configuration dataclass."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class OntologyConfig:
    """Configuration for Memorant Auto-Ontology extraction."""

    enabled: bool = False
    provider: str = "minimax"
    model: str = "MiniMax-M3"
    timeout_seconds: int = 30
    max_attempts: int = 3
    provider_rpm: int = 30
    lease_seconds: int = 600
    queue_max_pending: int = 10000
    prune_after_days: int = 30
    max_cost_usd_per_day: float = 5.0
    alert_failed_threshold: int = 100
    backlog_replay: Literal["auto", "on", "off"] = "auto"
    backlog_batch: int = 100
    backlog_rpm: int = 10
    prompt_cache: bool = True
    prompt_version: str = "v1"
    entity_types_allowed: list[str] = field(default_factory=lambda: [
        "person", "place", "project", "concept", "tool", "organization", "date"
    ])
    relations_allowed: list[str] = field(default_factory=lambda: [
        "manages", "uses", "owns", "works_on", "is_friend_of", "located_in",
        "created_in", "depends_on", "is_a", "part_of", "instance_of", "has_property"
    ])
    functional_relations: list[str] = field(default_factory=lambda: [
        "located_in", "is_a", "instance_of", "created_in"
    ])
    trust_propagation: Literal["conservative", "eager"] = "conservative"
    resonance_integration: bool = True
    resonance_max_depth: int = 2
    resonance_max_entities: int = 50
    redact_pii: bool = True
    no_retention_mode: bool = True
    dead_letter_path: str = ""  # default: <db_path>.dead-letter.jsonl

    def __post_init__(self) -> None:
        """Validate allowlists and cross-field constraints."""
        for name in ("entity_types_allowed", "relations_allowed", "functional_relations"):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{name} must be a list or tuple")
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(f"{name} entries must be strings")
                if not re.fullmatch(r"[a-z0-9_]+", item):
                    raise ValueError(
                        f"{name} entry {item!r} contains invalid characters; "
                        "only lowercase letters, digits, and underscores are allowed"
                    )

        # Functional relations must be a subset of allowed relations
        invalid = set(self.functional_relations) - set(self.relations_allowed)
        if invalid:
            raise ValueError(
                f"functional_relations contains entries not in relations_allowed: {sorted(invalid)}"
            )
