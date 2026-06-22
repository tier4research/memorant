from pathlib import Path
from memorant.adapters.hermes import pre_llm_call_context
DB_PATH = Path.home() / ".memorant" / "palace.db"
def register(ctx): ctx.register_hook("pre_llm_call", on_pre_llm_call)
def on_pre_llm_call(**kwargs): return pre_llm_call_context(str(kwargs.get("user_message") or ""), db_path=DB_PATH, session_id=str(kwargs.get("session_id") or ""))
