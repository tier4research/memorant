# Elle Palace → Memorant Upgrade Plan

**Date:** June 22, 2026
**Target:** Elle's memory palace on Hostinger VPS → Memorant v1.0.0-rc.1
**Protocol:** MiMo v2.5 Pro deploys → GPT 5.5 audits/wraps → Andre reviews
**Memorant repo:** `C:/Users/Admin/Documents/tier4-infra/memorant`
**Memorant wheel:** `dist/memorant-1.0.0rc1-py3-none-any.whl`
**Memorant commit:** `d49be3c` (278 tests pass, 4 SQLCipher skip)

---

## 0. Pre-Flight — Credentials & Access

### VPS Access
- **IP:** 93.188.161.20
- **User:** root
- **Password:** Retrieve from Memory Palace → `andre/credentials` room → drawer `VPS ACCESS CREDENTIAL — 93.188.161.20` → `Password:` field
- **Fallback:** `C:/Users/Admin/Documents/andre_credentials_palace/` or `F:/Dropbox/API keys/hostinger/`
- **Tool:** `plink.exe` (PuTTY CLI), NOT Windows OpenSSH `ssh.exe`
- **Container:** `hermes-agent-l07q-hermes-agent-1`

### Password shell pattern
```bash
# Write password to temp file (avoids & special char issues)
echo 'THE_PASSWORD' > C:/Users/Admin/.vps_pw.tmp
PW=$(cat C:/Users/Admin/.vps_pw.tmp)

# Every command follows this shape:
plink -batch -pw "$PW" root@93.188.161.20 "docker exec hermes-agent-l07q-hermes-agent-1 sh -c '...'"
```

### Host bind-mount shortcut
Container path `/opt/data/` = host path `/docker/hermes-agent-l07q/data/`. Use host paths for file transfers to avoid `docker exec` layer.

---

## 1. Snapshot & Backup (BEFORE ANY CHANGE)

### 1.1 Backup the current palace database
```bash
PW=$(cat C:/Users/Admin/.vps_pw.tmp)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/docker/hermes-agent-l07q/data/archive/palace-v2-backup-$TIMESTAMP

plink -batch -pw "$PW" root@93.188.161.20 "
  mkdir -p $BACKUP_DIR
  cp /docker/hermes-agent-l07q/data/memory_palace_v2/facts.db $BACKUP_DIR/
  cp /docker/hermes-agent-l07q/data/memory_palace_v2/facts.db-wal $BACKUP_DIR/ 2>/dev/null || true
  cp /docker/hermes-agent-l07q/data/memory_palace_v2/facts.db-shm $BACKUP_DIR/ 2>/dev/null || true
  cp -r /docker/hermes-agent-l07q/data/palace_upgrade $BACKUP_DIR/
  md5sum $BACKUP_DIR/facts.db > $BACKUP_DIR/checksums.txt
  echo BACKUP_OK
"
```

### 1.2 Run health check on current palace
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 /opt/data/palace_upgrade/migrate.py --healthcheck 2>&1
  '
"
```
Expected: `{"status": "healthy", "integrity": "ok", "fts": "ok", "row_count": ...}`

### 1.3 Record current fact count
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 -c \"
import sqlite3
db = sqlite3.connect('/opt/data/memory_palace_v2/facts.db')
print('facts:', db.execute('SELECT COUNT(*) FROM facts').fetchone()[0])
print('rooms:', db.execute('SELECT COUNT(*) FROM rooms').fetchone()[0])
print('entities:', db.execute('SELECT COUNT(*) FROM entities').fetchone()[0])
\"
  '
"
```

---

## 2. Build Memorant Wheel (if not already built)

```bash
cd "C:/Users/Admin/Documents/tier4-infra/memorant"
python -m build --wheel
ls -la dist/memorant-1.0.0rc1-py3-none-any.whl
```

---

## 3. Deploy Memorant to VPS Container

### 3.1 Upload wheel to container
```bash
PW=$(cat C:/Users/Admin/.vps_pw.tmp)
WHEEL="C:/Users/Admin/Documents/tier4-infra/memorant/dist/memorant-1.0.0rc1-py3-none-any.whl"

# Upload to VPS host first
"C:/Program Files/PuTTY/pscp.exe" -batch -pw "$PW" "$WHEEL" root@93.188.161.20:/tmp/memorant.whl

# Then docker cp into container
plink -batch -pw "$PW" root@93.188.161.20 "
  docker cp /tmp/memorant.whl hermes-agent-l07q-hermes-agent-1:/tmp/memorant.whl
  echo UPLOAD_OK
"
```

### 3.2 Install wheel in container
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/pip install --force-reinstall /tmp/memorant.whl 2>&1 | tail -5
    rm /tmp/memorant.whl
  '
"
```
**Verify:** `Successfully installed memorant-1.0.0rc1`

### 3.3 Verify installation
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 -c \"
from memorant import MemorantStore, __version__
print('Memorant version:', __version__)
print('Import OK')
\"
  '
"
```

---

## 4. Schema Migration — Palace V2 → Memorant

Memorant schema is DIFFERENT from Palace V2. Must migrate data, not just swap code.

### Palace V2 tables → Memorant tables mapping
| Palace V2 | Memorant | Migration |
|-----------|----------|-----------|
| `facts` (219 rows) | `claim_units` | Map: content→content, source_doc_id→source_pointer, confidence→trust_tier, category→fact_refs |
| `facts_fts` | `claim_fts` | Rebuild from migrated claim_units |
| `rooms` | _(none)_ | Store as JSON in `fact_refs` on each claim |
| `entities` | _(none)_ | Store as JSON in `fact_refs` on each claim |
| `events` | _(none)_ | Optional: skip or map to `standing_facts` |
| `quarantine` | _(trust_tier='untrusted')_ | Map to claim_units with trust_tier='untrusted' |
| `invalidations` | `corrects` | Map to corrects table |
| `retrieval_log` | `resonance_log` | Skip (different purpose) |
| `audit_log` | _(none)_ | Skip (not in Memorant v1) |
| `schema_meta` | `_steward_canary` | Handled by steward |

### Trust tier mapping
| Palace V2 confidence | Memorant trust_tier |
|---------------------|---------------------|
| >= 0.85 | `verified` |
| >= 0.6 | `derived` |
| >= 0.3 | `derived` |
| < 0.3 | `untrusted` |
| Quarantine | `untrusted` |

### 4.1 Create migration script

Write to `C:/Users/Admin/Documents/tier4-infra/memorant/scripts/migrate_palace_v2.py`:

```python
#!/usr/bin/env python3
"""Migrate Palace V2 facts.db → Memorant memorant.db"""
import json, sqlite3, hashlib, uuid, sys, os
from datetime import datetime, timezone

SOURCE = "/docker/hermes-agent-l07q/data/memory_palace_v2/facts.db"
TARGET = "/docker/hermes-agent-l07q/data/memory_palace_v2/memorant.db"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def content_hash(text):
    import re
    return hashlib.sha256(re.sub(r"\s+", " ", text.strip().lower()).encode()).hexdigest()

def trust_tier(confidence):
    if confidence >= 0.85: return "verified"
    if confidence >= 0.3: return "derived"
    return "untrusted"

def main():
    src = sqlite3.connect(SOURCE)
    src.row_factory = sqlite3.Row
    
    dst = sqlite3.connect(TARGET)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA foreign_keys=ON")
    
    # Create Memorant schema
    from memorant.schema import SCHEMA_V1
    for sql in SCHEMA_V1.values():
        dst.execute(sql)
    
    # Migrate facts → claim_units
    facts = src.execute("SELECT * FROM facts WHERE is_active = 1").fetchall()
    migrated = 0
    for f in facts:
        cid = str(uuid.uuid4())
        chash = content_hash(f["content"])
        tier = trust_tier(f.get("confidence", 0.5))
        fact_refs = json.dumps({
            "room": f.get("room_id", ""),
            "entity": f.get("entity_id", ""),
            "category": f.get("category", ""),
            "source": f.get("source_doc_id", ""),
            "provenance": f.get("provenance_group", ""),
        })
        
        try:
            dst.execute("""
                INSERT INTO claim_units (id, content, content_hash, fact_refs,
                    source_type, source_pointer, trust_tier, valid_from, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cid, f["content"], chash, fact_refs,
                "migration", f"palace-v2:{f.get('source_doc_id', '')}", tier,
                f.get("valid_from") or now_iso(), now_iso(), now_iso()
            ))
            dst.execute("INSERT INTO claim_fts (id, content) VALUES (?, ?)", (cid, f["content"]))
            migrated += 1
        except sqlite3.IntegrityError:
            # Duplicate content_hash — skip, already exists
            pass
    
    # Migrate invalidations → corrects
    invals = src.execute("SELECT * FROM invalidations").fetchall()
    for inv in invals:
        # Find the Memorant claim that matches the invalidated content
        # (best-effort; invalidations table structure may vary)
        pass
    
    # Migrate quarantine → claim_units with untrusted tier
    quars = src.execute("SELECT * FROM quarantine").fetchall()
    for q in quars:
        cid = str(uuid.uuid4())
        chash = content_hash(q.get("content", ""))
        if not chash:
            continue
        try:
            dst.execute("""
                INSERT INTO claim_units (id, content, content_hash, fact_refs,
                    source_type, source_pointer, trust_tier, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cid, q.get("content", ""), chash, "[]",
                "migration", f"palace-v2:quarantine", "untrusted", now_iso(), now_iso()
            ))
            dst.execute("INSERT INTO claim_fts (id, content) VALUES (?, ?)", (cid, q.get("content", "")))
        except sqlite3.IntegrityError:
            pass
    
    dst.commit()
    dst.execute("PRAGMA user_version = 7")
    
    count = dst.execute("SELECT COUNT(*) FROM claim_units").fetchone()[0]
    print(f"Migrated {migrated} facts, {count} total claim_units")
    print(f"Target: {TARGET}")

if __name__ == "__main__":
    main()
```

### 4.2 Upload and run migration
```bash
# Push script
PW=$(cat C:/Users/Admin/.vps_pw.tmp)
B64=$(base64 -w0 "C:/Users/Admin/Documents/tier4-infra/memorant/scripts/migrate_palace_v2.py")
plink -batch -pw "$PW" root@93.188.161.20 "
  echo $B64 | base64 -d > /tmp/migrate_palace_v2.py
  docker cp /tmp/migrate_palace_v2.py hermes-agent-l07q-hermes-agent-1:/tmp/migrate_palace_v2.py
"

# Run migration (inside container, uses hermes venv with memorant installed)
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 /tmp/migrate_palace_v2.py
  '
"
```

### 4.3 Verify migration
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 -c \"
from memorant import MemorantStore
store = MemorantStore('/opt/data/memory_palace_v2/memorant.db')
store.init()
claims = store.search('elle identity', limit=3)
for c in claims:
    print(f'[{c.trust_tier}] {c.content[:80]}...')
print(f'Total claims: {len(store.search(\"\", limit=1000))}')
\"
  '
"
```
**Expected:** 219+ claims returned, search works.

---

## 5. MCP Tool Rewrite

### Current MCP tool
File: `/opt/data/hermes_mcp_server.py`
Function: `search_palace(query, limit)` — queries Palace V2 FTS5 index

### New MCP tool
Replace with Memorant-backed search:

```python
@mcp.tool()
async def search_palace(query: str, limit: int = 6) -> str:
    """Search Elle's memory palace. Use this for identity, relationships,
    boundaries, experiences, growth, and working memory facts."""
    from memorant import MemorantStore
    store = MemorantStore("/opt/data/memory_palace_v2/memorant.db")
    store.init()
    claims = store.search(query, limit=limit, min_trust="derived")
    if not claims:
        return json.dumps({"results": [], "query": query})
    results = []
    for c in claims:
        results.append({
            "id": c.id,
            "content": c.content,
            "score": round(c.score, 3),
            "trust": c.trust_tier,
        })
    return json.dumps({"results": results, "query": query})
```

### 5.1 Backup MCP server
```bash
PW=$(cat C:/Users/Admin/.vps_pw.tmp)
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    cp /opt/data/hermes_mcp_server.py /opt/data/hermes_mcp_server.py.bak-$(date +%Y%m%d)
  '
"
```

### 5.2 Patch MCP server
Use the patch-script pattern (upload a Python script that patches the file):

Script: `C:/Users/Admin/Documents/tier4-infra/memorant/scripts/patch_mcp_for_memorant.py`

```python
#!/usr/bin/env python3
"""Replace search_palace MCP tool with Memorant-backed version."""
import re

TARGET = "/opt/data/hermes_mcp_server.py"

with open(TARGET) as f:
    content = f.read()

# Find the old search_palace function and replace it
old_pattern = r'async def search_palace\(query: str, limit: int = 6\) -> str:.*?(?=\n@mcp\.tool|$)'
new_code = '''async def search_palace(query: str, limit: int = 6) -> str:
    """Search Elle's memory palace using Memorant FTS5 + trust filtering."""
    import json as _json
    try:
        from memorant import MemorantStore
        store = MemorantStore("/opt/data/memory_palace_v2/memorant.db")
        store.init()
        claims = store.search(query, limit=limit, min_trust="derived")
        results = []
        for c in claims:
            results.append({
                "id": c.id,
                "content": c.content,
                "score": round(c.score, 3),
                "trust": c.trust_tier,
            })
        return _json.dumps({"results": results, "query": query, "backend": "memorant"})
    except Exception as e:
        return _json.dumps({"error": str(e), "query": query})
'''

content = re.sub(old_pattern, new_code, content, flags=re.DOTALL)

with open(TARGET, 'w') as f:
    f.write(content)

print("MCP server patched for Memorant")
```

**Push and run:**
```bash
PW=$(cat C:/Users/Admin/.vps_pw.tmp)
B64=$(base64 -w0 "C:/Users/Admin/Documents/tier4-infra/memorant/scripts/patch_mcp_for_memorant.py")
plink -batch -pw "$PW" root@93.188.161.20 "
  echo $B64 | base64 -d > /tmp/patch_mcp.py
  docker cp /tmp/patch_mcp.py hermes-agent-l07q-hermes-agent-1:/tmp/patch_mcp.py
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 /tmp/patch_mcp.py
  '
"
```

### 5.3 Verify MCP server syntax
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 -m py_compile /opt/data/hermes_mcp_server.py && echo SYNTAX_OK
  '
"
```

---

## 6. Service Restart

### 6.1 Restart gateway and MCP server
```bash
PW=$(cat C:/Users/Admin/.vps_pw.tmp)

# Kill gateway (MCP server is a child process)
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    pkill -f \"hermes gateway run\"
  '
"

sleep 5

# Restart gateway (will spawn new MCP server)
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec -d hermes-agent-l07q-hermes-agent-1 bash -c '
    cd /opt/hermes
    set -a; [ -f /opt/data/.env ] && . /opt/data/.env; set +a
    export HERMES_HOME=/opt/data
    export PYTHONPATH=/opt/data:/opt/data/.pydeps:/opt/hermes
    export HERMES_ALLOW_ROOT_GATEWAY=1
    nohup .venv/bin/hermes gateway run > /opt/data/logs/hermes-stdout.log 2>&1 &
  '
"

sleep 10
```

### 6.2 Verify all services healthy
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    for p in 8642 8643 8644 8645; do
      echo -n \"port \$p: \"
      curl -sf --connect-timeout 3 http://localhost:\$p/health 2>/dev/null || echo UNREACHABLE
    done
    echo ---
    ps aux | grep python | grep -v grep | wc -l
  '
"
```
**Expected:** all 4 ports healthy, 8+ Python processes.

---

## 7. Verification

### 7.1 Test Memorant search works
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 -c \"
from memorant import MemorantStore
store = MemorantStore('/opt/data/memory_palace_v2/memorant.db')
store.init()
# Search for key topics
for q in ['elle identity', 'Miguel', 'boundary', 'DreamSim']:
    claims = store.search(q, limit=3)
    print(f'{q}: {len(claims)} results')
    for c in claims:
        print(f'  [{c.trust_tier}] {c.content[:60]}...')
\"
  '
"
```

### 7.2 Test MCP tool works through gateway
```bash
# Send a message to Elle's Discord via the gateway API
# This verifies the MCP tool is registered and searchable
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    curl -s http://localhost:8642/v1/models | head -20
  '
"
```

### 7.3 Verify palace DB file integrity
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    /opt/hermes/.venv/bin/python3 -c \"
import sqlite3
db = sqlite3.connect('/opt/data/memory_palace_v2/memorant.db')
for row in db.execute('PRAGMA integrity_check'):
    print(row[0])
print('claims:', db.execute('SELECT COUNT(*) FROM claim_units').fetchone()[0])
\"
  '
"
```
**Expected:** `ok`, claim count >= 219.

---

## 8. Cleanup (Optional — Deployer's Judgment)

### 8.1 Remove old palace files
```bash
# KEEP the old facts.db as a backup — archive it, don't delete
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    mkdir -p /opt/data/archive/deprecated-palace-v2
    mv /opt/data/memory_palace_v2/facts.db /opt/data/archive/deprecated-palace-v2/ 2>/dev/null
    mv /opt/data/memory_palace_v2/facts.db-wal /opt/data/archive/deprecated-palace-v2/ 2>/dev/null
    mv /opt/data/memory_palace_v2/facts.db-shm /opt/data/archive/deprecated-palace-v2/ 2>/dev/null
    mv /opt/data/palace_upgrade /opt/data/archive/deprecated-palace-v2/ 2>/dev/null
    echo ARCHIVED
  '
"
```

### 8.2 Remove migration scripts from container
```bash
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    rm -f /tmp/migrate_palace_v2.py /tmp/patch_mcp.py
  '
"
```

---

## 9. Rollback (If Needed)

```bash
PW=$(cat C:/Users/Admin/.vps_pw.tmp)
TIMESTAMP=<from step 1.1>

# Restore old palace DB
plink -batch -pw "$PW" root@93.188.161.20 "
  cp /docker/hermes-agent-l07q/data/archive/palace-v2-backup-$TIMESTAMP/facts.db \\
     /docker/hermes-agent-l07q/data/memory_palace_v2/facts.db
  cp /docker/hermes-agent-l07q/data/archive/palace-v2-backup-$TIMESTAMP/facts.db-wal \\
     /docker/hermes-agent-l07q/data/memory_palace_v2/facts.db-wal 2>/dev/null || true
  cp /docker/hermes-agent-l07q/data/archive/palace-v2-backup-$TIMESTAMP/facts.db-shm \\
     /docker/hermes-agent-l07q/data/memory_palace_v2/facts.db-shm 2>/dev/null || true
"

# Restore MCP server
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c '
    cp /opt/data/hermes_mcp_server.py.bak-$(ls -t /opt/data/hermes_mcp_server.py.bak-* | head -1 | rev | cut -d- -f1 | rev) /opt/data/hermes_mcp_server.py 2>/dev/null || echo \"Find backup manually\"
  '
"

# Restart gateway
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec hermes-agent-l07q-hermes-agent-1 sh -c 'pkill -f \"hermes gateway run\"'
"
sleep 3
plink -batch -pw "$PW" root@93.188.161.20 "
  docker exec -d hermes-agent-l07q-hermes-agent-1 bash -c '
    cd /opt/hermes
    set -a; [ -f /opt/data/.env ] && . /opt/data/.env; set +a
    export HERMES_HOME=/opt/data
    export HERMES_ALLOW_ROOT_GATEWAY=1
    nohup .venv/bin/hermes gateway run > /opt/data/logs/hermes-stdout.log 2>&1 &
  '
"
```

---

## Key Pitfalls for Deployers

1. **Python interpreter:** Always `/opt/hermes/.venv/bin/python3`, never bare `python3`
2. **WAL checkpointing:** The old Palace V2 DB may have un-checkpointed WAL. Run `PRAGMA wal_checkpoint(TRUNCATE)` before copying.
3. **pkill signal propagation:** `pkill` inside `docker exec bash -c` can kill the parent shell (exit 143). Split kill and restart into TWO separate commands.
4. **Container paths vs host paths:** `/opt/data/memory_palace_v2/` inside container = `/docker/hermes-agent-l07q/data/memory_palace_v2/` on host.
5. **Gateway must restart:** MCP server changes only take effect after gateway restart.
6. **env vars at restart:** Gateway restart MUST include `HERMES_ALLOW_ROOT_GATEWAY=1` and sourcing `.env`.
7. **plink -T for binary:** If pulling/pushing DB files, use `plink -T` to avoid PTY corruption.
8. **Stale .pyc:** After patching MCP server, ensure `__pycache__` is cleared or the old bytecode may persist.
9. **The password contains `&`:** Always wrap in double quotes or use temp-file pattern.
10. **Database is locked:** If migration fails with "database is locked", the gateway may have an open connection. Kill gateway first, run migration, then restart.

---

## Files to Deliver to Deployer

All at `C:/Users/Admin/Documents/tier4-infra/memorant/`:

| File | Purpose |
|------|---------|
| `dist/memorant-1.0.0rc1-py3-none-any.whl` | The Memorant wheel |
| `scripts/migrate_palace_v2.py` | Schema + data migration script |
| `scripts/patch_mcp_for_memorant.py` | MCP server patcher |

---

*Plan reviewed by Andre, June 22 2026. Deployer: MiMo v2.5 Pro. Auditor: GPT 5.5. Reviewer: Andre.*
