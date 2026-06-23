import os
import subprocess
import sys
from pathlib import Path


def test_fix_resonance_log_key_removes_audit_guidance(tmp_path: Path) -> None:
    target = tmp_path / "memory_palace.py"
    listener_test = tmp_path / "test_listener_task_mode.py"
    target.write_text(
        "\n".join(
            [
                'out = {"audit_log": None}',
                "status.get('audit_log')",
                "status['audit_log']",
                '"- write_path: import sys; sys.path.insert(0, \'/opt/data/palace_upgrade\'); from palace.ingest import ingest_fact; from palace.audit import log_audit.",',
                '"- audit_call: log_audit(actor=\'elle\', action=\'ingest\', target_type=\'fact\', target_id=fact_id, reason=\'why this belongs in memory\').",',
            ]
        )
    )
    listener_test.write_text('assert "log_audit" in block\n')

    env = os.environ.copy()
    env["MEMORY_PALACE_PATH"] = str(target)
    env["LISTENER_TASK_TEST_PATH"] = str(listener_test)
    subprocess.run(
        [sys.executable, "scripts/fix_resonance_log_key.py"],
        check=True,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
    )

    patched = target.read_text()
    assert "audit_log" not in patched
    assert '"resonance_log": None' in patched
    assert "status.get('resonance_log')" in patched
    assert "status['resonance_log']" in patched
    assert "Memorant claim_units write path" in patched
    assert "resonance_log: record why durable memory" in patched
    assert listener_test.read_text() == 'assert "resonance_log" in block\n'


def test_fix_andre_ship_conftest_preserves_sys_import() -> None:
    from scripts.fix_andre_ship_conftest import clean_test_source

    source = "\n".join(
        [
            "from __future__ import annotations",
            "",
            "import sys",
            "sys.path.insert(0, '/opt/data')",
            "",
            "sys.modules.pop('context_compiler', None)",
        ]
    )

    cleaned = clean_test_source(source)
    assert "sys.path.insert(0, '/opt/data')" not in cleaned
    assert "import sys" in cleaned
    assert "sys.modules.pop" in cleaned
