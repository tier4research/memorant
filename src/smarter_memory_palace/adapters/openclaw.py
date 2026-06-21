from __future__ import annotations
from pathlib import Path
from smarter_memory_palace import MemoryPalace

def build_context_packet(current_input: str, *, db_path: str | Path, session_id: str = "") -> str:
    return MemoryPalace(db_path).resonate(current_input, session_id=session_id)
