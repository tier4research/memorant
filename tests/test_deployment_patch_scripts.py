import os
import subprocess
import sys
from pathlib import Path


def test_fix_resonance_log_key_removes_audit_guidance(tmp_path: Path) -> None:
    target = tmp_path / "memory_palace.py"
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

    env = os.environ.copy()
    env["MEMORY_PALACE_PATH"] = str(target)
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
