from __future__ import annotations
from pathlib import Path
from memorant import MemoryPalace

def pre_llm_call_context(user_message: str, *, db_path: str | Path, session_id: str = "") -> dict[str, str] | None:
    if not user_message.strip(): return None
    text = MemoryPalace(db_path).resonate(user_message, session_id=session_id)
    return {"context": text} if text else None
