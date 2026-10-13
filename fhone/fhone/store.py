"""SQLite persistence for FHONE.

One row per message, never one JSON blob per conversation: a blob means
rewriting the whole history on every turn (O(history) write amplification and a
RAM spike on every save). Rows make each append O(1).

Pragmas are tuned for a swap-pressured phone: a 1 MB page cache and temp
b-trees held in memory rather than spilled to flash.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    title           TEXT,
    model           TEXT,
    provider        TEXT,
    summary         TEXT,
    summarized_upto INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT,
    tool_calls   TEXT,
    tool_call_id TEXT,
    name         TEXT,
    created_at   INTEGER NOT NULL,
    interrupted  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content);

CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    kind       TEXT,
    session_id TEXT,
    agent_id   TEXT,
    confidence REAL DEFAULT 1.0,
    created_at INTEGER NOT NULL,
    used_at    INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    agent_id   TEXT,
    kind       TEXT,
    content    TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT,
    to_agent   TEXT,
    body       TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    read_at    INTEGER
);

CREATE TABLE IF NOT EXISTS providers (
    id            TEXT PRIMARY KEY,
    label         TEXT,
    base_url      TEXT NOT NULL,
    api_style     TEXT NOT NULL DEFAULT 'openai',
    default_model TEXT,
    headers_json  TEXT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    priority      INTEGER NOT NULL DEFAULT 100,
    models        TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS provider_keys (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id    TEXT NOT NULL,
    key            TEXT NOT NULL,
    label          TEXT,
    status         TEXT NOT NULL DEFAULT 'unknown',
    fail_count     INTEGER NOT NULL DEFAULT 0,
    cooldown_until INTEGER NOT NULL DEFAULT 0,
    last_used      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_keys_provider ON provider_keys(provider_id, status);

CREATE TABLE IF NOT EXISTS usage (
    provider_id       TEXT NOT NULL,
    key_id            INTEGER NOT NULL,
    day               TEXT NOT NULL,
    requests          INTEGER NOT NULL DEFAULT 0,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider_id, key_id, day)
);

CREATE TABLE IF NOT EXISTS jobs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT,
    schedule TEXT,
    command  TEXT,
    next_run INTEGER,
    last_run INTEGER,
    enabled  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS todos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""

PRAGMAS = (
    "journal_mode=WAL",
    "synchronous=NORMAL",
    "temp_store=MEMORY",
    "cache_size=-1000",
    "busy_timeout=5000",
    "foreign_keys=ON",
)

# Forward-only migrations. Each is a list of statements; a statement that fails
# because the column already exists is tolerated, so a re-run is idempotent.
MIGRATIONS = [
    (2, [
        "ALTER TABLE sessions ADD COLUMN summarized_upto INTEGER NOT NULL DEFAULT 0",
    ]),
    (3, [
        "ALTER TABLE providers ADD COLUMN models TEXT NOT NULL DEFAULT '[]'",
    ]),
]


def now() -> int:
    return int(time.time())


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _last_id(cur) -> int:
    """lastrowid is typed Optional; after an INSERT it is always set."""
    return int(cur.lastrowid or 0)


def models_of(row) -> list:
    """Parse a provider row's `models` JSON. An empty list means 'any model'."""
    if row is None:
        return []
    try:
        return [m for m in (json.loads(row["models"] or "[]") or []) if m]
    except (ValueError, TypeError):
        return []


class Store:
    """Thread-aware SQLite access.

    One connection PER THREAD. sqlite3 connections are thread-bound by default,
    and one FHONE process serves several threads at once — portal request
    handlers, the Telegram poller, the scheduler tick. WAL supports many
    connections inside one process and `busy_timeout` serialises writers, so this
    is both correct and cheap. (Sharing a single connection across threads raises
    `ProgrammingError: SQLite objects created in a thread can only be used in
    that same thread`.)
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._local = threading.local()
        self._connections: list = []
        self._lock = threading.Lock()
        self._setup()

    # -- connection management --------------------------------------------
    def _new_connection(self):
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        for pragma in PRAGMAS:
            try:
                con.execute("PRAGMA " + pragma)
            except sqlite3.Error:
                pass
        con.executescript(SCHEMA)
        with self._lock:
            self._connections.append(con)
        return con

    def _setup(self) -> None:
        self._local.con = self._new_connection()
        self._migrate()

    @property
    def con(self):
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._new_connection()
            self._local.con = con
        return con

    def close(self) -> None:
        with self._lock:
            connections, self._connections = self._connections, []
        for con in connections:
            try:
                con.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    def _migrate(self) -> None:
        current = self.get_meta("schema_version")
        if current is None:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            return
        try:
            version = int(current)
        except ValueError:
            version = SCHEMA_VERSION
        for target, statements in MIGRATIONS:
            if target <= version:
                continue
            for sql in statements:
                try:
                    self.con.execute(sql)
                except sqlite3.Error:
                    pass          # column already present: idempotent
            self.set_meta("schema_version", str(target))
            version = target

    def integrity_check(self) -> bool:
        try:
            row = self.con.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        except sqlite3.Error:
            return False

    def vacuum_into(self, dest: str) -> None:
        """Consistent snapshot. NEVER copy a live SQLite file by hand."""
        self.con.execute("VACUUM INTO ?", (dest,))

    # -- meta --------------------------------------------------------------
    def get_meta(self, key: str):
        row = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.con.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # -- sessions ----------------------------------------------------------
    def ensure_session(self, sid: str, model: str = "", provider: str = "") -> None:
        ts = now()
        self.con.execute(
            "INSERT INTO sessions(id, model, provider, created_at, updated_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
            (sid, model, provider, ts, ts))

    def set_title(self, sid: str, title: str) -> None:
        self.con.execute("UPDATE sessions SET title=? WHERE id=?", (title, sid))

    def set_summary(self, sid: str, text: str) -> None:
        self.con.execute("UPDATE sessions SET summary=? WHERE id=?", (text, sid))

    def get_summary(self, sid: str):
        row = self.con.execute("SELECT summary FROM sessions WHERE id=?", (sid,)).fetchone()
        return row["summary"] if row else None

    def sessions(self, limit: int = 20):
        return self.con.execute(
            "SELECT id, title, model, updated_at FROM sessions "
            "ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()

    def session_count(self) -> int:
        return self.con.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]

    # -- messages ----------------------------------------------------------
    def add(self, sid: str, role: str, content=None, tool_calls=None,
            tool_call_id=None, name=None, interrupted: int = 0) -> int:
        cur = self.con.execute(
            "INSERT INTO messages(session_id, role, content, tool_calls, "
            "tool_call_id, name, created_at, interrupted) VALUES(?,?,?,?,?,?,?,?)",
            (sid, role, content,
             json.dumps(tool_calls) if tool_calls else None,
             tool_call_id, name, now(), interrupted))
        rid = _last_id(cur)
        if role in ("user", "assistant") and content:
            self.con.execute("INSERT INTO messages_fts(rowid, content) VALUES(?,?)",
                             (rid, content))
        self.con.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), sid))
        return rid

    def mark_interrupted(self, sid: str) -> None:
        self.con.execute(
            "UPDATE messages SET interrupted=1 WHERE session_id=? AND role='assistant'",
            (sid,))

    def window(self, sid: str, limit: int = 12):
        rows = self.con.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (sid, int(limit))).fetchall()
        return list(reversed(rows))

    def older_than(self, sid: str, keep: int):
        rows = self.con.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC", (sid,)).fetchall()
        return rows[:-int(keep)] if keep else list(rows)

    def unsummarized_older(self, sid: str, keep: int):
        """Rows older than the newest `keep` that have not been summarised yet.

        Without the watermark, every turn re-summarised the same old messages:
        the count never dropped, so the trigger stayed true forever.
        """
        row = self.con.execute(
            "SELECT summarized_upto FROM sessions WHERE id=?", (sid,)).fetchone()
        upto = int(row["summarized_upto"] or 0) if row else 0
        rows = self.con.execute(
            "SELECT * FROM messages WHERE session_id=? AND id>? ORDER BY id ASC",
            (sid, upto)).fetchall()
        return rows[:-int(keep)] if keep else list(rows)

    def set_summarized_upto(self, sid: str, mid: int) -> None:
        self.con.execute("UPDATE sessions SET summarized_upto=? WHERE id=?",
                         (int(mid), sid))

    def prune_older_than(self, ts: int) -> int:
        """Delete old messages AND their FTS rows (an FTS delete keeps the index
        from growing without bound)."""
        rows = self.con.execute(
            "SELECT id FROM messages WHERE created_at < ?", (int(ts),)).fetchall()
        ids = [int(r["id"]) for r in rows]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        self.con.execute("DELETE FROM messages_fts WHERE rowid IN (%s)" % marks, ids)
        self.con.execute("DELETE FROM messages WHERE id IN (%s)" % marks, ids)
        return len(ids)

    def count(self, sid: str) -> int:
        return self.con.execute(
            "SELECT COUNT(*) c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"]

    def search_messages(self, query: str, limit: int = 10):
        try:
            return self.con.execute(
                "SELECT m.session_id, m.role, m.content FROM messages_fts f "
                "JOIN messages m ON m.id = f.rowid "
                "WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, int(limit))).fetchall()
        except sqlite3.Error:
            return self.con.execute(
                "SELECT session_id, role, content FROM messages "
                "WHERE content LIKE ? LIMIT ?", ("%" + query + "%", int(limit))).fetchall()

    # -- memories ----------------------------------------------------------
    def memory_add(self, content: str, kind: str = "fact", session_id: str = "",
                   agent_id: str = "", confidence: float = 1.0) -> int:
        cur = self.con.execute(
            "INSERT INTO memories(content, kind, session_id, agent_id, confidence, "
            "created_at) VALUES(?,?,?,?,?,?)",
            (content, kind, session_id, agent_id, float(confidence), now()))
        rid = _last_id(cur)
        self.con.execute("INSERT INTO memories_fts(rowid, content) VALUES(?,?)",
                         (rid, content))
        return rid

    def memory_search(self, query: str, limit: int = 5):
        try:
            rows = self.con.execute(
                "SELECT m.id, m.content, m.kind FROM memories_fts f "
                "JOIN memories m ON m.id = f.rowid "
                "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, int(limit))).fetchall()
        except sqlite3.Error:
            rows = self.con.execute(
                "SELECT id, content, kind FROM memories WHERE content LIKE ? LIMIT ?",
                ("%" + query + "%", int(limit))).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            self.con.execute(
                "UPDATE memories SET used_at=? WHERE id IN (%s)"
                % ",".join("?" * len(ids)), (now(), *ids))
        return rows

    def memory_list(self, limit: int = 20):
        return self.con.execute(
            "SELECT id, content, kind, created_at FROM memories "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()

    def memory_forget(self, mid: int) -> bool:
        cur = self.con.execute("DELETE FROM memories WHERE id=?", (int(mid),))
        if cur.rowcount:
            self.con.execute("DELETE FROM memories_fts WHERE rowid=?", (int(mid),))
            return True
        return False

    def memory_count(self) -> int:
        return self.con.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]

    # -- notes / team ------------------------------------------------------
    def note_add(self, content: str, session_id: str = "", agent_id: str = "",
                 kind: str = "note", confidence: float = 1.0) -> int:
        cur = self.con.execute(
            "INSERT INTO notes(session_id, agent_id, kind, content, confidence, "
            "created_at) VALUES(?,?,?,?,?,?)",
            (session_id, agent_id, kind, content, float(confidence), now()))
        return _last_id(cur)

    def team_feed(self, limit: int = 20):
        return self.con.execute(
            "SELECT session_id, agent_id, kind, content, created_at FROM notes "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()

    def agent_send(self, to_agent: str, body: str, from_agent: str = "") -> int:
        cur = self.con.execute(
            "INSERT INTO agent_messages(from_agent, to_agent, body, created_at) "
            "VALUES(?,?,?,?)", (from_agent, to_agent, body, now()))
        return _last_id(cur)

    def agent_inbox(self, agent_id: str, mark_read: bool = True):
        rows = self.con.execute(
            "SELECT * FROM agent_messages WHERE to_agent=? AND read_at IS NULL "
            "ORDER BY id ASC", (agent_id,)).fetchall()
        if rows and mark_read:
            self.con.execute(
                "UPDATE agent_messages SET read_at=? WHERE to_agent=? AND read_at IS NULL",
                (now(), agent_id))
        return rows

    # -- providers ---------------------------------------------------------
    def provider_add(self, pid: str, base_url: str, label: str = "",
                     api_style: str = "openai", default_model: str = "",
                     headers: dict | None = None, priority: int = 100,
                     models: list | None = None) -> None:
        models_json = json.dumps(models) if models else "[]"
        self.con.execute(
            "INSERT INTO providers(id,label,base_url,api_style,default_model,"
            "headers_json,enabled,priority,models) VALUES(?,?,?,?,?,?,1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET label=excluded.label, "
            "base_url=excluded.base_url, api_style=excluded.api_style, "
            "default_model=excluded.default_model, headers_json=excluded.headers_json, "
            "priority=excluded.priority, models=excluded.models",
            (pid, label or pid, base_url, api_style, default_model,
             json.dumps(headers) if headers else None, int(priority), models_json))

    def provider_remove(self, pid: str) -> None:
        self.con.execute("DELETE FROM provider_keys WHERE provider_id=?", (pid,))
        self.con.execute("DELETE FROM providers WHERE id=?", (pid,))

    def providers(self, enabled_only: bool = True):
        sql = "SELECT * FROM providers"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY priority ASC, id ASC"
        return [self._provider_dict(r) for r in self.con.execute(sql).fetchall()]

    def _provider_dict(self, row) -> dict:
        d = dict(row)
        d["models"] = models_of(row)
        return d

    def provider_get(self, pid: str):
        row = self.con.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
        return self._provider_dict(row) if row else None

    def key_add(self, provider_id: str, key: str, label: str = "") -> int:
        cur = self.con.execute(
            "INSERT INTO provider_keys(provider_id, key, label) VALUES(?,?,?)",
            (provider_id, key, label))
        return _last_id(cur)

    def key_remove(self, key_id: int) -> None:
        self.con.execute("DELETE FROM provider_keys WHERE id=?", (int(key_id),))

    def keys_for(self, provider_id: str):
        return self.con.execute(
            "SELECT * FROM provider_keys WHERE provider_id=? "
            "ORDER BY (status='dead') ASC, fail_count ASC, COALESCE(last_used,0) ASC",
            (provider_id,)).fetchall()

    def key_set_status(self, key_id: int, status: str, cooldown_until: int = 0) -> None:
        self.con.execute(
            "UPDATE provider_keys SET status=?, cooldown_until=?, last_used=? WHERE id=?",
            (status, int(cooldown_until), now(), int(key_id)))

    def key_bump_fail(self, key_id: int) -> None:
        self.con.execute(
            "UPDATE provider_keys SET fail_count=fail_count+1, last_used=? WHERE id=?",
            (now(), int(key_id)))

    def key_touch(self, key_id: int) -> None:
        self.con.execute("UPDATE provider_keys SET last_used=? WHERE id=?",
                         (now(), int(key_id)))

    def key_healthy(self, row) -> bool:
        if row["status"] == "dead":
            return False
        if row["cooldown_until"] and row["cooldown_until"] > now():
            return False
        return True

    def key_counts(self):
        return self.con.execute(
            "SELECT status, COUNT(*) c FROM provider_keys GROUP BY status").fetchall()

    # -- usage -------------------------------------------------------------
    def usage_add(self, provider_id: str, key_id: int, requests: int = 1,
                  prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.con.execute(
            "INSERT INTO usage(provider_id,key_id,day,requests,prompt_tokens,"
            "completion_tokens) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(provider_id,key_id,day) DO UPDATE SET "
            "requests=requests+excluded.requests, "
            "prompt_tokens=prompt_tokens+excluded.prompt_tokens, "
            "completion_tokens=completion_tokens+excluded.completion_tokens",
            (provider_id, int(key_id), today(), int(requests),
             int(prompt_tokens), int(completion_tokens)))

    def usage_today(self):
        return self.con.execute(
            "SELECT provider_id, SUM(requests) r, SUM(prompt_tokens) p, "
            "SUM(completion_tokens) c FROM usage WHERE day=? GROUP BY provider_id",
            (today(),)).fetchall()

    def usage_total(self):
        row = self.con.execute(
            "SELECT SUM(requests) r, SUM(prompt_tokens) p, SUM(completion_tokens) c "
            "FROM usage").fetchone()
        return row

    # -- todos -------------------------------------------------------------
    def todo_add(self, text: str) -> int:
        cur = self.con.execute(
            "INSERT INTO todos(text, created_at) VALUES(?,?)", (text, now()))
        return _last_id(cur)

    def todo_list(self, include_done: bool = True):
        sql = "SELECT * FROM todos"
        if not include_done:
            sql += " WHERE done=0"
        sql += " ORDER BY done ASC, id ASC"
        return self.con.execute(sql).fetchall()

    def todo_complete(self, tid: int) -> bool:
        cur = self.con.execute("UPDATE todos SET done=1 WHERE id=?", (int(tid),))
        return bool(cur.rowcount)

    def todo_clear(self) -> int:
        cur = self.con.execute("DELETE FROM todos WHERE done=1")
        return cur.rowcount

    # -- jobs --------------------------------------------------------------
    def job_add(self, schedule: str, command: str, kind: str = "shell",
                next_run: int = 0) -> int:
        cur = self.con.execute(
            "INSERT INTO jobs(kind, schedule, command, next_run) VALUES(?,?,?,?)",
            (kind, schedule, command, int(next_run)))
        return _last_id(cur)

    def jobs(self, enabled_only: bool = False):
        sql = "SELECT * FROM jobs"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id ASC"
        return self.con.execute(sql).fetchall()

    def job_set(self, jid: int, **fields) -> None:
        allowed = {"enabled", "next_run", "last_run", "schedule", "command"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        clause = ", ".join("%s=?" % k for k in sets)
        self.con.execute("UPDATE jobs SET %s WHERE id=?" % clause,
                         (*sets.values(), int(jid)))

    def job_remove(self, jid: int) -> None:
        self.con.execute("DELETE FROM jobs WHERE id=?", (int(jid),))

    def job_due(self, ts: int):
        return self.con.execute(
            "SELECT * FROM jobs WHERE enabled=1 AND next_run>0 AND next_run<=? "
            "ORDER BY next_run ASC", (int(ts),)).fetchall()
