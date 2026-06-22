"""Doctor contract — standardized health reporting for Tier 4 components.

All Tier 4 packages MUST implement `doctor --json` with this contract:
- schema_version: "1.0.0" (this contract version, not the component version)
- component: component name
- component_version: component semantic version
- status: "healthy" | "degraded" | "unhealthy"
- checks: list of {name, status, message, latency_ms}
- timestamp: ISO 8601 UTC

Exit codes: 0=healthy, 1=degraded, 2=unhealthy
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


DOCTOR_SCHEMA_VERSION = "1.0.0"


@dataclass
class CheckResult:
    """A single health check result."""

    name: str
    status: str  # "healthy" | "degraded" | "unhealthy"
    message: str = ""
    latency_ms: int = 0


@dataclass
class DoctorReport:
    """Full doctor health report."""

    component: str
    component_version: str
    checks: list[CheckResult] = field(default_factory=list)

    schema_version: str = DOCTOR_SCHEMA_VERSION
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def status(self) -> str:
        """Aggregate status: unhealthy > degraded > healthy."""
        if any(c.status == "unhealthy" for c in self.checks):
            return "unhealthy"
        if any(c.status == "degraded" for c in self.checks):
            return "degraded"
        return "healthy"

    @property
    def exit_code(self) -> int:
        """Map status to exit code: 0=healthy, 1=degraded, 2=unhealthy."""
        return {"healthy": 0, "degraded": 1, "unhealthy": 2}[self.status]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "schema_version": self.schema_version,
            "component": self.component,
            "component_version": self.component_version,
            "status": self.status,
            "checks": [asdict(c) for c in self.checks],
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


def run_check(name: str, fn, *args, degraded_on_error: bool = True, **kwargs) -> CheckResult:
    """Run a health check function and return a CheckResult.

    Args:
        name: Human-readable check name
        fn: Callable that returns (True, message) on success, or raises on failure
        degraded_on_error: If True, errors produce 'degraded'; if False, 'unhealthy'
    """
    start = time.time()
    try:
        ok, msg = fn(*args, **kwargs)
        latency = int((time.time() - start) * 1000)
        status = "healthy" if ok else ("degraded" if degraded_on_error else "unhealthy")
        return CheckResult(name=name, status=status, message=msg, latency_ms=latency)
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        status = "degraded" if degraded_on_error else "unhealthy"
        return CheckResult(name=name, status=status, message=str(e), latency_ms=latency)


def doctor_main(
    component: str,
    component_version: str,
    checks: list[CheckResult],
    json_output: bool = False,
) -> int:
    """Standard doctor entry point for CLI tools.

    Usage in a component's doctor command:
        checks = [
            run_check("database", check_db_integrity),
            run_check("disk_space", check_disk, degraded_on_error=True),
        ]
        sys.exit(doctor_main("my-component", "1.0.0", checks, json_output=True))

    Args:
        component: Component name
        component_version: Component version
        checks: List of CheckResult from run_check()
        json_output: If True, print JSON report to stdout

    Returns:
        Exit code (0/1/2)
    """
    report = DoctorReport(
        component=component,
        component_version=component_version,
        checks=checks,
    )

    if json_output:
        print(report.to_json())

    return report.exit_code
