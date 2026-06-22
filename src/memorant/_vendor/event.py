"""Agent event model — typed event construction and serialization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class AgentEvent:
    """Canonical agent integrity event."""

    component: str
    component_version: str
    event_type: str
    severity: str
    session_id: str
    trace_id: str

    # Auto-populated
    schema_version: str = "1.0.0"
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Optional
    payload: dict[str, Any] | None = None
    correlation_id: str | None = None
    parent_event_id: str | None = None
    tags: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-ready dict, excluding None fields."""
        d: dict[str, Any] = {}
        d["schema_version"] = self.schema_version
        d["event_id"] = self.event_id
        d["timestamp"] = self.timestamp
        d["session_id"] = self.session_id
        d["trace_id"] = self.trace_id
        d["component"] = self.component
        d["component_version"] = self.component_version
        d["event_type"] = self.event_type
        d["severity"] = self.severity

        if self.payload is not None:
            d["payload"] = self.payload
        if self.correlation_id is not None:
            d["correlation_id"] = self.correlation_id
        if self.parent_event_id is not None:
            d["parent_event_id"] = self.parent_event_id
        if self.tags is not None:
            d["tags"] = self.tags

        return d
