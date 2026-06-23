"""Fix test files by removing sys.path.insert and using conftest.py instead."""
import pathlib

for fname in ['test_andre_hermes_integration_contract.py', 'test_andre_persona_capability.py']:
    p = pathlib.Path(f'/opt/data/tests/{fname}')
    c = p.read_text()
    # Remove any sys.path.insert lines we added
    lines = c.split('\n')
    cleaned = [l for l in lines if l.strip() not in [
        "import sys",
        "sys.path.insert(0, '/opt/data')",
    ]]
    p.write_text('\n'.join(cleaned))
    print(f'CLEANED {fname}')

# Create conftest.py in tests/ directory
conftest = pathlib.Path('/opt/data/tests/conftest.py')
conftest.write_text("import sys\nsys.path.insert(0, '/opt/data')\n")
print('CREATED conftest.py')
