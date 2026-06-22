"""JSONL flight recorder — dependency-free event logging with session/trace correlation.

Example usage:
    recorder = FlightRecorder("events.jsonl")
    recorder.record(event.to_dict())
    recorder.record(another_event.to_dict())

    # Query events for a session
    events = recorder.query(session_id="sess-abc")

    # Query events across a trace
    events = recorder.query(trace_id="trace-xyz")
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class FlightRecorder:
    """Append-only JSONL event recorder with query support."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._ensure_file()

    def _ensure_file(self) -> None:
        """Create the file and parent directory if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def record(self, event: dict[str, Any]) -> int:
        """Append an event to the JSONL file.

        Returns the byte offset of the recorded event.
        """
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)
            offset = f.tell()
            f.write(line)
            f.flush()
        return offset

    def record_many(self, events: list[dict[str, Any]]) -> None:
        """Record multiple events in a single write."""
        lines = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(lines)
            f.flush()

    def query(
        self,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
        component: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query events by filter criteria.

        At least one filter must be provided. Results are returned in insertion order.
        """
        results: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Apply filters
                if session_id and event.get("session_id") != session_id:
                    continue
                if trace_id and event.get("trace_id") != trace_id:
                    continue
                if component and event.get("component") != component:
                    continue
                if event_type and event.get("event_type") != event_type:
                    continue
                if severity and event.get("severity") != severity:
                    continue

                results.append(event)

                if len(results) >= limit:
                    break

        return results

    def count(self, **filters: str) -> int:
        """Count events matching filters without returning them."""
        filters.setdefault("limit", 10_000_000)
        return len(self.query(**filters))

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        """Return the last n events."""
        events: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events[-n:]

    def replay(self) -> list[dict[str, Any]]:
        """Return all events in order (for replay)."""
        events: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events
