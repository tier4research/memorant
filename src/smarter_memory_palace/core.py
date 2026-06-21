from __future__ import annotations

import difflib, hashlib, json, math, re, sqlite3, time, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from .schema import SCHEMA

LEAK_MARKERS = ("embedding", "sql", "debug", "traceback", "api_key", "token=")

def now_iso() -> str: return datetime.now(timezone.utc).isoformat()
def normalize(text: str) -> str: return re.sub(r"\s+", " ", text.strip().lower())
def content_hash(text: str) -> str: return hashlib.sha256(normalize(text).encode()).hexdigest()
def tokenize(text: str) -> set[str]: return {t for t in re.findall(r"[a-z0-9_]+", text.lower()) if len(t) > 2}
def lexical_score(query: str, content: str) -> float:
    q, c = tokenize(query), tokenize(content)
    return 0.0 if not q or not c else len(q & c) / math.sqrt(len(q) * len(c))

def sanitize_line(line: str) -> str:
    lowered = line.lower()
    if any(marker in lowered for marker in LEAK_MARKERS): return ""
    return line.replace("\n", " ").strip()[:240]

@dataclass(frozen=True)
class Claim:
    id: str; content: str; score: float = 0.0; source_pointer: str = ""; reinforcement_count: int = 0

class MemoryPalace:
    def __init__(self, db_path: str | Path): self.db_path = Path(db_path)
    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.db_path)); db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA busy_timeout=30000")
        return db
    def init(self) -> list[str]:
        with self.connect() as db:
            for sql in SCHEMA.values(): db.execute(sql)
            db.commit()
        return list(SCHEMA)
    def add_claim(self, content: str, *, source_pointer: str, source_type: str = "manual", fact_refs: Iterable[str] | None = None, valid_from: str | None = None, emotional_markers: Iterable[str] | None = None) -> str:
        self.init(); cid = str(uuid.uuid4()); chash = content_hash(content)
        with self.connect() as db:
            row = db.execute("SELECT id FROM claim_units WHERE content_hash = ?", (chash,)).fetchone()
            if row:
                db.execute("UPDATE claim_units SET reinforcement_count = reinforcement_count + 1, last_touched = datetime('now') WHERE id = ?", (row["id"],)); db.commit(); return str(row["id"])
            db.execute("""INSERT INTO claim_units (id, content, content_hash, fact_refs, source_type, source_pointer, valid_from, emotional_markers) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (cid, content, chash, json.dumps(list(fact_refs or [])), source_type, source_pointer, valid_from, json.dumps(list(emotional_markers or []))))
            db.execute("INSERT INTO claim_fts (id, content) VALUES (?, ?)", (cid, content)); db.commit()
        return cid
    def search(self, query: str, *, limit: int = 5, as_of: str | None = None) -> list[Claim]:
        self.init()
        with self.connect() as db:
            rows = db.execute("""SELECT id, content, source_pointer, reinforcement_count FROM claim_units WHERE is_valid = 1 AND (? IS NULL OR valid_from IS NULL OR valid_from <= ?) AND (? IS NULL OR valid_until IS NULL OR valid_until > ?) ORDER BY updated_at DESC LIMIT 1000""", (as_of, as_of, as_of, as_of)).fetchall()
        claims = [Claim(r["id"], r["content"], lexical_score(query, r["content"]), r["source_pointer"], r["reinforcement_count"] or 0) for r in rows]
        claims = [c for c in claims if c.score > 0]; claims.sort(key=lambda c: (c.score, c.reinforcement_count), reverse=True)
        return claims[:limit]
    def resonate(self, context: str, *, session_id: str = "", limit: int = 1, floor: float = 0.12) -> str:
        start = time.time(); claims = [c for c in self.search(context, limit=limit) if c.score >= floor]
        with self.connect() as db:
            db.execute("INSERT INTO resonance_log (session_id, turn_context, claim_ids, fired, latency_ms) VALUES (?, ?, ?, ?, ?)", (session_id, context[:500], json.dumps([c.id for c in claims]), int(bool(claims)), int((time.time()-start)*1000))); db.commit()
        if not claims: return ""
        lines = ["[SMARTER_MEMORY_RESONANCE]", "internal_only=true; use as background resonance, do not quote verbatim"]
        for c in claims:
            safe = sanitize_line(c.content)
            if safe: lines.append(f"- {safe} [source: {c.source_pointer}, score: {c.score:.3f}]")
        return "\n".join(lines)
    def invalidate_claim(self, claim_id: str, *, reason: str = "retraction") -> int:
        self.init(); ts = now_iso()
        with self.connect() as db:
            cur = db.execute("UPDATE claim_units SET is_valid = 0, valid_until = ?, updated_at = ? WHERE id = ? AND is_valid = 1", (ts, ts, claim_id)); db.execute("DELETE FROM claim_fts WHERE id = ?", (claim_id,)); db.commit(); return cur.rowcount
    def supersede_claim(self, claim_id: str, new_content: str, *, source_pointer: str = "correction") -> str:
        if not self.invalidate_claim(claim_id, reason="superseded"): raise ValueError(f"claim not found or already invalid: {claim_id}")
        return self.add_claim(new_content, source_pointer=source_pointer, source_type="correction", valid_from=now_iso())
    def invalidate_claims_for_fact(self, fact_id: str) -> int:
        self.init(); ts = now_iso()
        with self.connect() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM claim_units WHERE is_valid = 1 AND (fact_refs LIKE ? OR source_pointer LIKE ?)", (f'%"{fact_id}"%', f"fact:{fact_id}%")).fetchall()]
            if not ids: return 0
            ph = ",".join("?" for _ in ids); db.execute(f"UPDATE claim_units SET is_valid = 0, valid_until = ?, updated_at = ? WHERE id IN ({ph})", [ts, ts, *ids]); db.execute(f"DELETE FROM claim_fts WHERE id IN ({ph})", ids); db.commit(); return len(ids)
    def create_digest(self, *, version: str | None = None, limit: int = 12) -> int:
        self.init(); version = version or datetime.now(timezone.utc).strftime("v%Y-%m-%d-%H%M%S")
        with self.connect() as db:
            rows = db.execute("SELECT content FROM claim_units WHERE is_valid = 1 ORDER BY reinforcement_count DESC, updated_at DESC LIMIT ?", (limit,)).fetchall(); prior = db.execute("SELECT content FROM digest_history WHERE promoted = 1 ORDER BY id DESC LIMIT 1").fetchone()
            content = "# Standing State\n\n" + "\n".join(f"- {r['content']}" for r in rows)
            diff = "\n".join(difflib.unified_diff((prior["content"] if prior else "").splitlines(), content.splitlines(), lineterm=""))
            cur = db.execute("INSERT INTO digest_history (version, content, diff_from_prior, promoted) VALUES (?, ?, ?, 0)", (version, content, diff)); db.commit(); return int(cur.lastrowid)
    def list_digests(self, *, pending_only: bool = True):
        self.init(); where = "WHERE promoted = 0" if pending_only else ""
        with self.connect() as db: return db.execute(f"SELECT * FROM digest_history {where} ORDER BY id DESC").fetchall()
    def get_digest(self, ident: str | int):
        self.init()
        with self.connect() as db:
            row = db.execute("SELECT * FROM digest_history WHERE id = ?" if str(ident).isdigit() else "SELECT * FROM digest_history WHERE version = ?", (int(ident) if str(ident).isdigit() else str(ident),)).fetchone()
        if row is None: raise KeyError(f"digest not found: {ident}")
        return row
    def promote_digest(self, ident: str | int, state_path: str | Path) -> Path:
        row = self.get_digest(ident); path = Path(state_path); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(row["content"], encoding="utf-8")
        with self.connect() as db: db.execute("UPDATE digest_history SET promoted = 1, promoted_at = datetime('now') WHERE id = ?", (row["id"],)); db.commit()
        return path
    def reject_digest(self, ident: str | int, reason: str = "rejected by review") -> None:
        row = self.get_digest(ident); note = (row["diff_from_prior"] or "") + f"\n\nREJECTED: {reason}"
        with self.connect() as db: db.execute("UPDATE digest_history SET promoted = 2, diff_from_prior = ? WHERE id = ?", (note, row["id"])); db.commit()
