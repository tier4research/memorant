"""Fix sys.path placement - must come AFTER __future__ imports."""
import pathlib

for fname in ['test_andre_hermes_integration_contract.py', 'test_andre_persona_capability.py']:
    p = pathlib.Path(f'/opt/data/tests/{fname}')
    c = p.read_text()
    # Remove the badly-placed sys.path lines we added at the top
    if c.startswith("import sys\nsys.path.insert"):
        c = c[c.index('\n', c.index('\n') + 1) + 1:]  # skip first 2 lines
    # Find the right place: after __future__ import line
    if 'from __future__' in c and 'sys.path.insert' not in c:
        idx = c.index('\n', c.index('from __future__'))
        c = c[:idx+1] + "import sys\nsys.path.insert(0, '/opt/data')\n" + c[idx+1:]
        p.write_text(c)
        print(f'FIXED {fname}')
    elif 'sys.path.insert' in c and 'from __future__' in c:
        print(f'ALREADY_OK {fname}')
    else:
        print(f'NO_FUTURE {fname}')
