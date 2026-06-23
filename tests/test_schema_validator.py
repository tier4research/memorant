from memorant._vendor.schema_validator import is_valid_event, validate_event


def _valid_event():
    return {
        "schema_version": "1.0.0",
        "event_id": "123e4567-e89b-12d3-a456-426614174000",
        "timestamp": "2026-06-23T10:00:00Z",
        "session_id": "session-1",
        "trace_id": "trace-1",
        "component": "memorant",
        "component_version": "1.0.0-rc.1",
        "event_type": "claim.recalled",
        "severity": "info",
        "payload": {"claim_id": "claim-1"},
    }


def test_bundled_schema_validates_valid_event():
    assert is_valid_event(_valid_event()) is True


def test_bundled_schema_rejects_invalid_event():
    event = _valid_event()
    event["severity"] = "debug"

    errors = validate_event(event)

    assert any("severity" in error for error in errors)
