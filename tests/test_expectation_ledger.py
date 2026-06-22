"""Comprehensive tests for Expectation Ledger v1 — ExpectationLedger API."""

import json
import sqlite3
from pathlib import Path

import pytest

from expectation_ledger import (
    ExpectationLedger,
    LedgerConfig,
    Expectation,
    Violation,
    AgentRun,
    TrustTier,
    TrustPolicy,
)


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def ledger(tmp_path: Path) -> ExpectationLedger:
    return ExpectationLedger(tmp_path / "test.db")


@pytest.fixture
def populated(ledger: ExpectationLedger) -> ExpectationLedger:
    """Ledger with a few expectations across trust tiers."""
    ledger.add_expectation(
        "Agent must not output personally identifiable information.",
        source_type="manual",
        trust_tier="operator",
    )
    ledger.add_expectation(
        "Agent must cite sources for factual claims.",
        source_type="manual",
        trust_tier="verified",
    )
    ledger.add_expectation(
        "Agent should respond within 5 seconds.",
        source_type="external",
        trust_tier="untrusted",
    )
    ledger.add_expectation(
        "Agent must log all tool invocations.",
        source_type="manual",
        trust_tier="operator",
    )
    return ledger


# ── Initialization ────────────────────────────────────────────

class TestInit:
    def test_init_creates_tables(self, ledger):
        tables = ledger.init()
        assert "expectations" in tables
        assert "violations" in tables
        assert "contracts" in tables
        assert "runs" in tables
        assert "run_expectations" in tables

    def test_init_is_idempotent(self, ledger):
        t1 = ledger.init()
        t2 = ledger.init()
        assert t1 == t2

    def test_new_store_has_version_zero(self, ledger):
        assert ledger._steward.user_version == 0

    def test_steward_available(self, ledger):
        ledger.init()
        assert ledger._steward.user_version >= 0
        assert ledger.integrity_check()

    def test_connect_returns_connection(self, ledger):
        db = ledger.connect()
        assert isinstance(db, sqlite3.Connection)
        db.close()


# ── Expectation CRUD ──────────────────────────────────────────

class TestExpectationCRUD:
    def test_add_and_retrieve(self, ledger):
        eid = ledger.add_expectation(
            "Agent must not share credentials.",
            source_pointer="policy:v1.0",
        )
        exp = ledger.get_expectation(eid)
        assert exp is not None
        assert exp.content == "Agent must not share credentials."
        assert exp.trust_tier == "untrusted"  # Default policy
        assert exp.status == "active"

    def test_add_with_trust_tier(self, ledger):
        eid = ledger.add_expectation(
            "Operator directive.",
            source_type="manual",
            trust_tier="operator",
        )
        exp = ledger.get_expectation(eid)
        assert exp.trust_tier == "operator"

    def test_add_with_contract(self, ledger):
        cid = ledger.add_contract("Safety Policy", source_pointer="policy:v1")
        eid = ledger.add_expectation(
            "Safety rule.",
            source_type="contract",
            parent_contract_id=cid,
        )
        exp = ledger.get_expectation(eid)
        assert exp.parent_contract_id == cid

    def test_add_with_metadata(self, ledger):
        eid = ledger.add_expectation(
            "Tagged expectation.",
            metadata={"priority": "high", "tags": ["security", "privacy"]},
        )
        exp = ledger.get_expectation(eid)
        assert exp.metadata == {"priority": "high", "tags": ["security", "privacy"]}

    def test_atomic_deduplication(self, ledger):
        """Identical content should not create duplicate."""
        a = ledger.add_expectation("Same expectation.")
        b = ledger.add_expectation("  Same   expectation.  ")  # Whitespace normalized
        assert a == b

    def test_deduplication_reactivates_superseded(self, ledger):
        eid = ledger.add_expectation("Reactivate me.")
        ledger.update_expectation(eid, status="superseded")
        # Re-add should reactivate
        eid2 = ledger.add_expectation("Reactivate me.")
        assert eid == eid2
        exp = ledger.get_expectation(eid)
        assert exp.status == "active"

    def test_get_nonexistent(self, ledger):
        assert ledger.get_expectation("nonexistent-id") is None

    def test_update_expectation(self, ledger):
        eid = ledger.add_expectation("Original content.")
        updated = ledger.update_expectation(eid, content="Updated content.", status="waived")
        assert updated is True
        exp = ledger.get_expectation(eid)
        assert exp.content == "Updated content."
        assert exp.status == "waived"

    def test_update_nonexistent(self, ledger):
        updated = ledger.update_expectation("nonexistent", content="hi")
        assert updated is False

    def test_partial_update(self, ledger):
        eid = ledger.add_expectation("Full content.", trust_tier="untrusted")
        ledger.update_expectation(eid, trust_tier="operator")
        exp = ledger.get_expectation(eid)
        assert exp.trust_tier == "operator"
        assert exp.content == "Full content."  # Unchanged

    def test_delete_expectation(self, ledger):
        eid = ledger.add_expectation("To be deleted.")
        assert ledger.delete_expectation(eid) is True
        assert ledger.get_expectation(eid) is None

    def test_delete_nonexistent(self, ledger):
        assert ledger.delete_expectation("nonexistent") is False

    def test_delete_with_violations_fails(self, ledger):
        """Deleting an expectation with recorded violations returns False."""
        eid = ledger.add_expectation("Cannot delete me, I have history.")
        ledger.record_violation(eid, severity="warning", evidence="Historical violation")
        assert ledger.delete_expectation(eid) is False
        # Expectation should still exist
        assert ledger.get_expectation(eid) is not None


# ── Search ────────────────────────────────────────────────────

class TestSearch:
    def test_basic_search(self, populated):
        results = populated.search("personally identifiable")
        assert len(results) > 0
        assert any("personally identifiable" in r.content.lower() for r in results)

    def test_search_returns_scored_results(self, populated):
        results = populated.search("tool")
        assert len(results) > 0

    def test_search_respects_trust_filter(self, populated):
        # The "5 seconds" expectation is untrusted
        results = populated.search("seconds", min_trust="verified")
        assert not any("5 seconds" in r.content for r in results)

    def test_search_respects_limit(self, populated):
        results = populated.search("agent", limit=2)
        assert len(results) <= 2

    def test_search_stable_ordering(self, populated):
        r1 = populated.search("agent", limit=10)
        r2 = populated.search("agent", limit=10)
        assert [x.id for x in r1] == [x.id for x in r2]

    def test_search_empty_query(self, ledger):
        results = ledger.search("")
        assert results == []

    def test_search_by_status(self, ledger):
        eid = ledger.add_expectation("Waived expectation.")
        ledger.update_expectation(eid, status="waived")
        results = ledger.search("expectation", status="waived")
        assert any(r.id == eid for r in results)
        # Should NOT appear with default status='active'
        results_active = ledger.search("expectation")
        assert not any(r.id == eid for r in results_active)


# ── Violations ────────────────────────────────────────────────

class TestViolations:
    def test_record_violation(self, ledger):
        eid = ledger.add_expectation("Agent must not lie.")
        vid = ledger.record_violation(eid, severity="critical", evidence="Agent invented a fact.")
        assert vid is not None

        exp = ledger.get_expectation(eid)
        assert exp.status == "violated"

    def test_record_violation_with_run(self, ledger):
        eid = ledger.add_expectation("Agent must be honest.")
        rid = ledger.start_run(agent_id="test-agent")
        vid = ledger.record_violation(eid, run_id=rid, severity="warning")

        # Check run violation count
        run = ledger.get_run(rid)
        assert run.violations_found == 1

    def test_get_violations(self, ledger):
        eid = ledger.add_expectation("Honesty policy.")
        ledger.record_violation(eid, severity="warning")
        ledger.record_violation(eid, severity="critical", evidence="Repeated offense")

        violations = ledger.get_violations(expectation_id=eid)
        assert len(violations) == 2
        assert violations[0].expectation_id == eid

    def test_get_violations_filtered_by_run(self, ledger):
        eid = ledger.add_expectation("Test violation.")
        r1 = ledger.start_run(agent_id="agent-1")
        r2 = ledger.start_run(agent_id="agent-2")
        ledger.record_violation(eid, run_id=r1, severity="info")
        ledger.record_violation(eid, run_id=r2, severity="warning")

        v1 = ledger.get_violations(run_id=r1)
        assert len(v1) == 1
        assert v1[0].run_id == r1

        v2 = ledger.get_violations(run_id=r2)
        assert len(v2) == 1
        assert v2[0].run_id == r2


# ── Agent Runs ────────────────────────────────────────────────

class TestRuns:
    def test_start_and_end_run(self, ledger):
        rid = ledger.start_run(agent_id="agent-42", session_id="sess-1")
        run = ledger.get_run(rid)
        assert run is not None
        assert run.status == "started"
        assert run.agent_id == "agent-42"
        assert run.session_id == "sess-1"

        ledger.end_run(rid, status="completed")
        run = ledger.get_run(rid)
        assert run.status == "completed"
        assert run.ended_at is not None

    def test_start_run_with_metadata(self, ledger):
        rid = ledger.start_run(metadata={"env": "production", "version": "2.1"})
        run = ledger.get_run(rid)
        assert run.metadata == {"env": "production", "version": "2.1"}

    def test_check_expectation(self, ledger):
        eid = ledger.add_expectation("Check me.")
        rid = ledger.start_run()

        ledger.check_expectation(rid, eid, passed=True)
        run = ledger.get_run(rid)
        assert run.expectations_checked == 1

    def test_check_expectation_failed(self, ledger):
        eid = ledger.add_expectation("Failing check.")
        rid = ledger.start_run()

        ledger.check_expectation(rid, eid, passed=False)
        run = ledger.get_run(rid)
        # Failed checks don't increment expectations_checked
        assert run.expectations_checked == 0

    def test_get_nonexistent_run(self, ledger):
        assert ledger.get_run("nonexistent") is None


# ── Contracts ────────────────────────────────────────────────

class TestContracts:
    def test_add_contract(self, ledger):
        cid = ledger.add_contract("Safety Rules", description="Core safety expectations")
        contract = ledger.get_contract(cid)
        assert contract is not None
        assert contract["name"] == "Safety Rules"
        assert contract["description"] == "Core safety expectations"

    def test_add_contract_with_trust(self, ledger):
        cid = ledger.add_contract("Verified Policy", trust_tier="verified")
        contract = ledger.get_contract(cid)
        assert contract["trust_tier"] == "verified"

    def test_get_nonexistent_contract(self, ledger):
        assert ledger.get_contract("nonexistent") is None


# ── Stats ─────────────────────────────────────────────────────

class TestStats:
    def test_stats_empty(self, ledger):
        s = ledger.stats()
        assert s["total_expectations"] == 0
        assert s["active_expectations"] == 0
        assert s["total_runs"] == 0

    def test_stats_populated(self, populated):
        s = populated.stats()
        assert s["total_expectations"] == 4
        assert s["active_expectations"] == 4
        assert s["by_trust"]["operator"] == 2
        assert s["by_trust"]["verified"] == 1
        assert s["by_trust"]["untrusted"] == 1

    def test_stats_after_violation(self, ledger):
        eid = ledger.add_expectation("Violated expectation.")
        ledger.record_violation(eid)
        s = ledger.stats()
        assert s["violated_expectations"] == 1
        assert s["total_violations"] == 1

    def test_stats_after_run(self, ledger):
        ledger.start_run(agent_id="a1")
        ledger.start_run(agent_id="a2")
        s = ledger.stats()
        assert s["total_runs"] == 2


# ── Integrity & Backups ──────────────────────────────────────

class TestIntegrity:
    def test_integrity_check_new_db(self, ledger):
        assert ledger.integrity_check() is True

    def test_integrity_check_after_operations(self, populated):
        assert populated.integrity_check() is True

    def test_backup_creates_file(self, populated):
        backup_path = populated.backup()
        assert backup_path.exists()
        assert backup_path.suffix == ".db"

    def test_migrate_noop(self, ledger):
        version = ledger.migrate()
        assert version >= 0


# ── Doctor ────────────────────────────────────────────────────

class TestDoctor:
    def test_doctor_returns_exit_code(self, ledger):
        exit_code = ledger.doctor(json_output=False)
        assert exit_code in (0, 1, 2)

    def test_doctor_json_output(self, ledger, capsys):
        exit_code = ledger.doctor(json_output=True)
        captured = capsys.readouterr()
        assert "expectation_ledger" in captured.out
        assert '"schema_version"' in captured.out


# ── Configuration ─────────────────────────────────────────────

class TestConfig:
    def test_custom_db_path(self, tmp_path):
        path = tmp_path / "custom" / "ledger.db"
        ledger = ExpectationLedger(path)
        assert ledger.db_path == path

    def test_config_object(self, tmp_path):
        config = LedgerConfig(db_path=tmp_path / "cfg.db")
        ledger = ExpectationLedger(config=config)
        assert ledger.db_path.name == "cfg.db"

    def test_trust_policy_rules(self, tmp_path):
        policy = TrustPolicy(rules=[
            {"source_type": "manual", "tier": "operator"},
        ])
        config = LedgerConfig(
            db_path=tmp_path / "trusted.db",
            trust_policy=policy,
        )
        ledger = ExpectationLedger(config=config)
        eid = ledger.add_expectation("Manual is trusted.", source_type="manual")
        exp = ledger.get_expectation(eid)
        assert exp.trust_tier == "operator"


# ── Data type tests ──────────────────────────────────────────

class TestDataTypes:
    def test_expectation_is_frozen(self):
        exp = Expectation(id="x", content="test")
        with pytest.raises(Exception):
            exp.content = "modified"  # type: ignore[misc]

    def test_violation_is_frozen(self):
        v = Violation(id="v1", expectation_id="e1")
        with pytest.raises(Exception):
            v.severity = "critical"  # type: ignore[misc]

    def test_agent_run_is_frozen(self):
        run = AgentRun(id="r1")
        with pytest.raises(Exception):
            run.status = "completed"  # type: ignore[misc]
