"""Fix inconsistent key name in palace_status() initial dict."""
import pathlib

p = pathlib.Path('/opt/data/listener_task/memory_palace.py')
c = p.read_text()
# Fix the initial dict key from audit_log to resonance_log
c = c.replace('"audit_log": None', '"resonance_log": None')
# Also fix build_memory_palace_awareness_block if it references audit_log
c = c.replace("status.get('audit_log')", "status.get('resonance_log')")
c = c.replace("status['audit_log']", "status['resonance_log']")
p.write_text(c)
print('FIXED: audit_log -> resonance_log in initial dict and awareness block')
