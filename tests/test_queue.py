"""Tests for queue — enqueue idempotency, lease recovery, retry, race conditions."""

import json
import threading
from pathlib import Path

import pytest

from memorant import MemorantStore
from memorant_ontology.config import OntologyConfig
from memorant_ontology.store import OntologyStore
from memorant_ontology.queue import enqueue, dequeue, lease_recover, complete, fail, sweep_dead_letter


@pytest.fixture
def stores(tmp_path: Path):
    """Return (core_store, ontology_store) pair."""
    db_path = tmp_path / "test.db"
    core = MemorantStore(db_path)
    core.init()
    store = OntologyStore(db_path, OntologyConfig(enabled=True))
    store.init()
    return core, store


class TestEnqueueIdempotency:
    def test_enqueue_new_claim(self, stores):
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        assert enqueue(store, cid) is True

    def test_enqueue_already_processed(self, stores):
        """F4: enqueue(claim) when ontology_processed_at IS NOT NULL → returns False."""
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        with store.connect() as db:
            db.execute(
                "UPDATE claim_units SET ontology_processed_at = datetime('now') WHERE id = ?",
                (cid,),
            )
            db.commit()
        assert enqueue(store, cid) is False

    def test_enqueue_duplicate_pending(self, stores):
        """F4: enqueue when queue row already exists in pending → returns False."""
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        assert enqueue(store, cid) is True
        # Second enqueue should be caught by ux_ontology_queue_active
        assert enqueue(store, cid) is False

    def test_enqueue_duplicate_in_progress(self, stores):
        """F4: enqueue when queue row in in_progress → returns False."""
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        enqueue(store, cid)
        # Move to in_progress
        dequeue(store, "worker-1")
        assert enqueue(store, cid) is False


class TestDequeue:
    def test_dequeue_pending(self, stores):
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        enqueue(store, cid)
        row = dequeue(store, "worker-1")
        assert row is not None
        assert row["claim_id"] == cid

    def test_dequeue_empty_queue(self, stores):
        _, store = stores
        row = dequeue(store, "worker-1")
        assert row is None

    def test_dequeue_no_double_claim(self, stores):
        """Two dequeue calls should not return the same row."""
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        enqueue(store, cid)
        row1 = dequeue(store, "w1")
        row2 = dequeue(store, "w2")
        assert row1 is not None
        assert row2 is None  # queue is empty after first dequeue


class TestLeaseRecovery:
    def test_lease_recover_expired(self, stores):
        """F3: expired in_progress rows are recovered."""
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        enqueue(store, cid)
        dequeue(store, "worker-dead")

        # Manually set started_at to far past
        with store.connect() as db:
            db.execute(
                "UPDATE memorant_ontology_queue SET started_at = datetime('now', '-700 seconds') "
                "WHERE claim_id = ?",
                (cid,),
            )
            db.commit()

        recovered = lease_recover(store, lease_seconds=600)
        assert recovered == 1

        # Should be dequeueable again
        row = dequeue(store, "worker-alive")
        assert row is not None

    def test_lease_recover_no_expired(self, stores):
        """No recovery when leases are fresh."""
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        enqueue(store, cid)
        dequeue(store, "worker-1")
        recovered = lease_recover(store, lease_seconds=600)
        assert recovered == 0


class TestRetry:
    def test_fail_increments_attempts(self, stores):
        """F15: on failure, state='pending', attempts += 1."""
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        enqueue(store, cid)
        row = dequeue(store, "worker-1")
        fail(store, row["id"], "test error", max_attempts=3)

        with store.connect() as db:
            q = db.execute(
                "SELECT state, attempts FROM memorant_ontology_queue WHERE id = ?",
                (row["id"],),
            ).fetchone()
        assert q["state"] == "pending"
        assert q["attempts"] == 1

    def test_fail_max_attempts_marks_failed(self, stores):
        """F15: attempts >= max_attempts → state='failed'."""
        core, store = stores
        cid = core.add_claim("Test claim", source_pointer="test")
        enqueue(store, cid)
        row = dequeue(store, "worker-1")
        fail(store, row["id"], "error 1", max_attempts=1)

        with store.connect() as db:
            q = db.execute(
                "SELECT state FROM memorant_ontology_queue WHERE id = ?",
                (row["id"],),
            ).fetchone()
        assert q["state"] == "failed"


class TestRaceCondition:
    def test_concurrent_dequeue(self, stores):
        """B3 race: two threads, same pending row → exactly one succeeds."""
        core, store = stores
        cid = core.add_claim("Race claim", source_pointer="test")
        enqueue(store, cid)

        results = []
        barrier = threading.Barrier(2)

        def worker(wid):
            barrier.wait()
            row = dequeue(store, wid)
            results.append(row is not None)

        t1 = threading.Thread(target=worker, args=("w1",))
        t2 = threading.Thread(target=worker, args=("w2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sum(results) == 1, f"Expected exactly one dequeue success, got {sum(results)}"


class TestSweepDeadLetter:
    def test_sweep_reenqueues_and_deletes_file(self, stores):
        """Dead-letter entries are re-enqueued and the rotated file is removed."""
        core, store = stores
        cid = core.add_claim("Dead letter claim", source_pointer="test")
        # Ensure the claim is in the queue, then move it to a failed state
        enqueue(store, cid)
        row = dequeue(store, "w1")
        assert row is not None, "dequeue should return the newly enqueued row"
        fail(store, row["id"], "llm error", max_attempts=1)

        dl = store.db_path.with_suffix(".dead-letter.jsonl")
        dl.write_text(json.dumps({"claim_id": cid}) + "\n", encoding="utf-8")

        count = sweep_dead_letter(store)
        assert count == 1
        assert not dl.exists()

        with store.connect() as db:
            pending = db.execute(
                "SELECT * FROM memorant_ontology_queue WHERE claim_id = ? AND state = 'pending'",
                (cid,),
            ).fetchall()
        assert len(pending) == 1

    def test_sweep_skips_bad_json(self, stores):
        """Malformed lines are ignored without crashing."""
        _, store = stores
        dl = store.db_path.with_suffix(".dead-letter.jsonl")
        dl.write_text("not json\n", encoding="utf-8")
        count = sweep_dead_letter(store)
        assert count == 0
        # Rotated file should still be cleaned up
        assert not store.db_path.with_suffix(".dead-letter.processing.jsonl").exists()

    def test_sweep_returns_partial_count_on_failure(self, stores, monkeypatch):
        """If processing fails mid-file, return the already re-enqueued count."""
        core, store = stores
        cid = core.add_claim("Dead letter claim", source_pointer="test")
        dl = store.db_path.with_suffix(".dead-letter.jsonl")
        dl.write_text(
            json.dumps({"claim_id": cid}) + "\n" + json.dumps({"claim_id": cid}) + "\n",
            encoding="utf-8",
        )

        # Force enqueue to fail on the second call so we can test partial count
        original_enqueue_called = [0]
        from memorant_ontology import queue as queue_module

        def fake_enqueue(store, claim_id):
            original_enqueue_called[0] += 1
            if original_enqueue_called[0] == 1:
                return True
            raise RuntimeError("boom")

        monkeypatch.setattr(queue_module, "enqueue", fake_enqueue)
        count = sweep_dead_letter(store)
        assert count == 1
        # Original dead-letter file should be restored
        assert dl.exists()

    def test_dequeue_max_retries_returns_none(self, stores, monkeypatch):
        """If pending rows are repeatedly stolen, dequeue exhausts retries and returns None."""
        from contextlib import contextmanager

        core, store = stores
        cid = core.add_claim("Race claim", source_pointer="test")
        enqueue(store, cid)

        # Speed up the test by removing the sleep between retries.
        monkeypatch.setattr("memorant_ontology.queue.time.sleep", lambda x: None)
        monkeypatch.setattr("memorant_ontology.queue._MAX_DEQUEUE_RETRIES", 3)

        real_connect = store.connect
        update_count = [0]

        class PatchedConnection:
            def __init__(self, real_conn):
                self._real_conn = real_conn

            def __getattr__(self, name):
                return getattr(self._real_conn, name)

            def execute(self, sql, parameters=()):
                sql_upper = sql.upper()
                if "UPDATE MEMORANT_ONTOLOGY_QUEUE" in sql_upper and "RETURNING" in sql_upper:
                    update_count[0] += 1
                    class FakeCursor:
                        def fetchone(self): return None
                        @property
                        def rowcount(self): return 0
                    return FakeCursor()
                if "SELECT 1 FROM MEMORANT_ONTOLOGY_QUEUE" in sql_upper:
                    class FakeCursor:
                        def fetchone(self): return (1,)
                    return FakeCursor()
                return self._real_conn.execute(sql, parameters)

        @contextmanager
        def fake_connect():
            conn = real_connect()
            try:
                yield PatchedConnection(conn)
            finally:
                conn.close()

        monkeypatch.setattr(store, "connect", fake_connect)

        row = dequeue(store, "worker-1")
        assert row is None
        assert update_count[0] == 3
