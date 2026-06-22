"""JSON Schema 2020-12 validation for agent integrity events.

Validates against the canonical agent-event.schema.json bundled with this package.
Additive-only compatibility within major version 1 — unknown fields are ignored
within major 1, but major-version mismatches are reported as incompatible.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schemas" / "agent-event.schema.json"


def load_schema() -> dict[str, Any]:
    """Load the bundled JSON Schema."""
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _validate_required(schema: dict, event: dict) -> list[str]:
    """Check all required fields are present. Returns list of errors."""
    errors = []
    for field in schema.get("required", []):
        if field not in event:
            errors.append(f"Missing required field: '{field}'")
    return errors


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"  # basic: YYYY-MM-DDTHH:MM:SS
)


def _validate_types(schema: dict, event: dict) -> list[str]:
    """Check field types and formats against schema properties. Returns list of errors."""
    errors = []
    for field, spec in schema.get("properties", {}).items():
        if field not in event:
            continue
        value = event[field]
        expected_type = spec.get("type")

        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"Field '{field}': expected string, got {type(value).__name__}")
        elif expected_type == "object" and not isinstance(value, dict):
            errors.append(f"Field '{field}': expected object, got {type(value).__name__}")

        # Skip further validation if type is wrong
        if errors and errors[-1].startswith(f"Field '{field}': expected"):
            continue

        # Pattern validation
        if expected_type == "string" and "pattern" in spec:
            if not re.match(spec["pattern"], str(value)):
                errors.append(
                    f"Field '{field}': value '{value}' does not match pattern '{spec['pattern']}'"
                )

        # Format validation (JSON Schema formats)
        if expected_type == "string" and "format" in spec:
            fmt = spec["format"]
            if fmt == "uuid" and not _UUID_RE.match(str(value)):
                errors.append(
                    f"Field '{field}': value '{value}' is not a valid UUID"
                )
            elif fmt == "date-time":
                if not _ISO_DATETIME_RE.match(str(value)):
                    errors.append(
                        f"Field '{field}': value '{value}' is not a valid ISO 8601 date-time"
                    )

        # Enum validation
        if "enum" in spec and value not in spec["enum"]:
            errors.append(
                f"Field '{field}': value '{value}' not in allowed values: {spec['enum']}"
            )
    return errors


def _check_major_version(event: dict) -> str | None:
    """Check schema_version is compatible (major version 1).
    Returns error string or None.
    """
    version = event.get("schema_version", "")
    if not version:
        return "Missing schema_version"
    match = re.match(r"^(\d+)\.", version)
    if not match:
        return f"Invalid schema_version format: '{version}'"
    major = int(match.group(1))
    if major != 1:
        return (
            f"Schema major version {major} is incompatible. "
            f"This validator supports major version 1 only."
        )
    return None


def validate_event(event: dict[str, Any]) -> list[str]:
    """Validate an event against the agent-event schema.

    Returns a list of validation errors. Empty list = valid.

    Validation rules:
    - All required fields must be present
    - Field types must match schema
    - Pattern constraints must be satisfied
    - Enum values must be valid
    - Schema major version must be 1 (additive-only compat)
    - Unknown additional properties within major 1 are silently accepted
    """
    schema = load_schema()
    errors: list[str] = []

    # Major version check first
    version_err = _check_major_version(event)
    if version_err:
        errors.append(version_err)
        return errors  # Don't continue if version is incompatible

    errors.extend(_validate_required(schema, event))
    errors.extend(_validate_types(schema, event))
    return errors


def is_valid_event(event: dict[str, Any]) -> bool:
    """Return True if the event passes all validation checks."""
    return len(validate_event(event)) == 0
