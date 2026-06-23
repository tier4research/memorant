"""Fix stale audit_log references in listener_task/memory_palace.py."""
import os
import pathlib


DEFAULT_TARGET = "/opt/data/listener_task/memory_palace.py"
DEFAULT_LISTENER_TEST = "/opt/data/tests/test_listener_task_mode.py"


def patch_text(content: str) -> str:
    content = content.replace('"audit_log": None', '"resonance_log": None')
    content = content.replace("status.get('audit_log')", "status.get('resonance_log')")
    content = content.replace("status['audit_log']", "status['resonance_log']")
    content = content.replace(
        "- write_path: import sys; sys.path.insert(0, '/opt/data/palace_upgrade'); from palace.ingest import ingest_fact; from palace.audit import log_audit.",
        "- write_path: use the Memorant claim_units write path; log durable retrieval/write effects through resonance_log.",
    )
    content = content.replace(
        "- audit_call: log_audit(actor='elle', action='ingest', target_type='fact', target_id=fact_id, reason='why this belongs in memory').",
        "- resonance_log: record why durable memory was retrieved or written when the calling workflow supports it.",
    )
    return content


def main() -> None:
    target = pathlib.Path(os.environ.get("MEMORY_PALACE_PATH", DEFAULT_TARGET))
    target.write_text(patch_text(target.read_text()))
    listener_test = pathlib.Path(os.environ.get("LISTENER_TASK_TEST_PATH", DEFAULT_LISTENER_TEST))
    if listener_test.exists():
        listener_test.write_text(
            listener_test.read_text().replace(
                'assert "log_audit" in block',
                'assert "resonance_log" in block',
            )
        )
    print("FIXED: stale audit_log references in memory_palace.py")


if __name__ == "__main__":
    main()
