"""Add sys.path.insert(0, '/opt/data') to andre_ship test files."""
import pathlib

for fname in ['test_andre_hermes_integration_contract.py', 'test_andre_persona_capability.py']:
    p = pathlib.Path(f'/opt/data/tests/{fname}')
    c = p.read_text()
    if 'sys.path.insert' not in c:
        c = "import sys\nsys.path.insert(0, '/opt/data')\n" + c
        p.write_text(c)
        print(f'PATCHED {fname}')
    else:
        print(f'ALREADY_HAS sys.path {fname}')
