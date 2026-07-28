"""Ontology configuration dataclass."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal


# Map of OntologyConfig field -> environment variable name
_ENV_VAR_MAP: dict[str, str] = {
    "enabled": "ONTOLOGY_ENABLED",
    "provider": "ONTOLOGY_PROVIDER",
    "model": "ONTOLOGY_MODEL",
    "timeout_seconds": "ONTOLOGY_TIMEOUT",
    "max_attempts": "ONTOLOGY_MAX_ATTEMPTS",
    "provider_rpm": "ONTOLOGY_RPM",
    "lease_seconds": "ONTOLOGY_LEASE_SECONDS",
    "queue_max_pending": "ONTOLOGY_QUEUE_MAX_PENDING",
    "prune_after_days": "ONTOLOGY_PRUNE_AFTER_DAYS",
    "max_cost_usd_per_day": "ONTOLOGY_DAILY_COST_CAP",
    "alert_failed_threshold": "ONTOLOGY_ALERT_FAILED_THRESHOLD",
    "prompt_version": "ONTOLOGY_PROMPT_VERSION",
    "trust_propagation": "ONTOLOGY_TRUST_PROPAGATION",
    "resonance_integration": "ONTOLOGY_RESONANCE_ENABLED",
    "redact_pii": "ONTOLOGY_REDACT_PII",
    "dead_letter_path": "ONTOLOGY_DEAD_LETTER_PATH",
}


def _parse_bool(v: str) -> bool:
    """Parse a string as a boolean."""
    return v.strip().lower() in ("1", "true", "yes", "on")


def _parse_string_list(v: str) -> list[str]:
    """Parse a comma-separated string into a list of trimmed strings."""
    return [x.strip() for x in v.split(",") if x.strip()]


def _apply_env_overrides(kwargs: dict) -> None:
    """Mutate *kwargs* with values from matching environment variables.

    Type inference: for fields whose current value is bool, use _parse_bool;
    for fields whose current value is float, use float(); for int, int();
    for str, str().
    """
    for field_name, env_var in _ENV_VAR_MAP.items():
        raw = os.environ.get(env_var)
        if raw is None:
            continue
        current = kwargs.get(field_name)
        if current is None:
            default_value = getattr(OntologyConfig, field_name, None)
            if isinstance(default_value, bool) or field_name == "enabled":
                kwargs[field_name] = _parse_bool(raw)
            elif isinstance(default_value, float):
                kwargs[field_name] = float(raw)
            elif isinstance(default_value, int):
                kwargs[field_name] = int(raw)
            elif isinstance(default_value, (list, tuple)):
                kwargs[field_name] = _parse_string_list(raw)
            else:
                kwargs[field_name] = raw
        elif isinstance(current, bool):
            kwargs[field_name] = _parse_bool(raw)
        elif isinstance(current, float):
            kwargs[field_name] = float(raw)
        elif isinstance(current, int):
            kwargs[field_name] = int(raw)
        elif isinstance(current, (list, tuple)):
            kwargs[field_name] = _parse_string_list(raw)
        else:
            kwargs[field_name] = raw


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
        """Apply env-var overrides, then validate allowlists and cross-field constraints.

        Uses object.__setattr__ to bypass the frozen-dataclass guard.
        """
        # Apply environment variable overrides (ONTOLOGY_MODEL, ONTOLOGY_DAILY_COST_CAP, etc.)
        _apply_env_overrides(self.__dict__)

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
