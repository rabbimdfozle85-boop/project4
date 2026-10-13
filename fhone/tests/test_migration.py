"""Migration test: a real v1 database must upgrade in place without data loss.

Builds a genuine v1-shaped database (no `summarized_upto` column), opens it with
the current code, and asserts the schema advanced and every row survived.

Run: python3 tests/test_migration.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
RESULTS: list = []


def check(label, ok, detail=None):
    RESULTS.append((label, bool(ok)))
    print("  %s  %-54s %s" % (PASS if ok else FAIL, label,
                              ("-- " + str(detail)[:60]) if detail else ""))


V1_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE sessions(
    id TEXT PRIMARY KEY, title TEXT, model TEXT, provider TEXT, summary TEXT,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,
    tool_calls TEXT, tool_call_id TEXT, name TEXT, created_at INTEGER,
    interrupted INTEGER NOT NULL DEFAULT 0);
CREATE VIRTUAL TABLE messages_fts USING fts5(content);
CREATE TABLE memories(
    id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, kind TEXT, session_id TEXT,
    agent_id TEXT, confidence REAL DEFAULT 1.0, created_at INTEGER, used_at INTEGER);
CREATE VIRTUAL TABLE memories_fts USING fts5(content);
CREATE TABLE providers(
    id TEXT PRIMARY KEY, label TEXT, base_url TEXT NOT NULL,
    api_style TEXT NOT NULL DEFAULT 'openai', default_model TEXT,
    headers_json TEXT, enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100);
CREATE TABLE provider_keys(
    id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id TEXT NOT NULL, key TEXT NOT NULL,
    label TEXT, status TEXT NOT NULL DEFAULT 'unknown', fail_count INTEGER DEFAULT 0,
    cooldown_until INTEGER DEFAULT 0, last_used INTEGER);
"""


V2_SCHEMA = V1_SCHEMA.replace(
    "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);",
    "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
    "summarized_upto INTEGER NOT NULL DEFAULT 0);")


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="fhone-migrate-")) / "fh"
    home.mkdir(parents=True)
    os.environ["FHONE_HOME"] = str(home)
    db = home / "fhone.db"

    print("\nFHONE migration test (v1 -> v3)\n")

    con = sqlite3.connect(str(db))
    con.executescript(V1_SCHEMA)
    con.execute("INSERT INTO meta VALUES('schema_version','1')")
    con.execute("INSERT INTO sessions(id,title,model,created_at,updated_at) "
                "VALUES('old','legacy session','m',1,1)")
    for i in range(3):
        cur = con.execute("INSERT INTO messages(session_id,role,content,created_at) "
                          "VALUES('old','user',?,1)", ("legacy message %d" % i,))
        con.execute("INSERT INTO messages_fts(rowid,content) VALUES(?,?)",
                    (cur.lastrowid, "legacy message %d" % i))
    con.execute("INSERT INTO memories(content,kind,created_at) VALUES('old fact','f',1)")
    con.execute("INSERT INTO providers(id,label,base_url) VALUES('legacy','Legacy','http://x/v1')")
    con.execute("INSERT INTO provider_keys(provider_id,key,label) VALUES('legacy','sk-old','k1')")
    con.commit()
    con.close()

    check("v1 database built", db.exists())
    before_size = db.stat().st_size

    from fhone.store import Store, SCHEMA_VERSION

    store = Store(str(db))

    cols = [r[1] for r in store.con.execute("PRAGMA table_info(sessions)")]
    check("schema_version advanced to current",
          store.get_meta("schema_version") == str(SCHEMA_VERSION),
          store.get_meta("schema_version"))
    check("summarized_upto column added", "summarized_upto" in cols)
    check("legacy session survived",
          store.con.execute("SELECT title FROM sessions WHERE id='old'"
                            ).fetchone()["title"] == "legacy session")
    check("legacy messages survived", store.count("old") == 3, store.count("old"))
    check("legacy memory survived", store.memory_count() == 1)
    check("legacy provider survived", store.provider_get("legacy") is not None)
    check("legacy key survived", len(store.keys_for("legacy")) == 1)
    check("FTS still searchable", len(store.search_messages("legacy", 10)) >= 1)
    check("new column defaults to 0 (nothing falsely marked summarised)",
          store.con.execute("SELECT summarized_upto FROM sessions WHERE id='old'"
                            ).fetchone()["summarized_upto"] == 0)
    check("unsummarized_older sees the legacy history",
          len(store.unsummarized_older("old", 0)) == 3)
    check("integrity check passes after migration", store.integrity_check())

    # Re-opening must be idempotent, not re-apply the ALTER.
    store.close()
    store2 = Store(str(db))
    check("re-opening is idempotent",
          store2.get_meta("schema_version") == str(SCHEMA_VERSION))
    check("no data lost on reopen", store2.count("old") == 3)
    check("file grew only marginally",
          db.stat().st_size < before_size * 3,
          "%d -> %d bytes" % (before_size, db.stat().st_size))
    store2.close()

    # --- a genuine v2 database ----------------------------------------------
    print("\nv2 -> v3")
    db2 = home / "v2.db"
    con = sqlite3.connect(str(db2))
    con.executescript(V2_SCHEMA)
    con.execute("INSERT INTO meta VALUES('schema_version','2')")
    con.execute("INSERT INTO providers(id,label,base_url) "
                "VALUES('legacy2','Legacy','http://x/v1')")
    con.commit()
    con.close()

    st2 = Store(str(db2))
    check("v3: schema_version advanced",
          st2.get_meta("schema_version") == str(SCHEMA_VERSION),
          st2.get_meta("schema_version"))
    p = st2.provider_get("legacy2")
    check("v3: providers.models column exists and parses as a list",
          bool(p) and isinstance(p.get("models"), list),
          p and p.get("models"))
    check("v3: models defaults to [] (= any model)", bool(p) and p["models"] == [])
    check("v3: legacy provider survived", bool(p) and p["id"] == "legacy2")
    st2.close()

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n%s" % ("=" * 66))
    print("%d/%d passed" % (passed, len(RESULTS)))
    print("=" * 66)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
