"""Root conftest.py — ensures src/ is on sys.path for test imports."""

import sys
from pathlib import Path

# Add src/ to sys.path if not already present
_src = str(Path(__file__).parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
