"""Patch listener_task/memory_palace.py to use Memorant claim_units table."""
import pathlib

p = pathlib.Path('/opt/data/listener_task/memory_palace.py')
c = p.read_text()

# Update SQL queries from old palace facts table to Memorant claim_units
c = c.replace(
    "SELECT COUNT(*) FROM facts WHERE is_active=1 AND is_valid=1",
    "SELECT COUNT(*) FROM claim_units WHERE invalidated_at IS NULL",
)
c = c.replace(
    "SELECT COUNT(*) FROM facts WHERE room_id=? AND is_active=1 AND is_valid=1",
    "SELECT COUNT(*) FROM claim_units WHERE json_extract(fact_refs, '$.room')=? AND invalidated_at IS NULL",
)

p.write_text(c)
print('PATCHED memory_palace.py SQL queries for Memorant')
