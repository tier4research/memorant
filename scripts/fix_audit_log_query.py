"""Update palace_status() to query resonance_log instead of audit_log."""
import pathlib

p = pathlib.Path('/opt/data/listener_task/memory_palace.py')
c = p.read_text()
c = c.replace('SELECT COUNT(*) FROM audit_log', 'SELECT COUNT(*) FROM resonance_log')
c = c.replace('out["audit_log"]', 'out["resonance_log"]')
p.write_text(c)
print('PATCHED audit_log -> resonance_log')
