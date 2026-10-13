"""SQLite persistence: sessions, messages, memories.

One row per message, never one JSON blob for the whole conversation. A blob
would mean rewriting the entire history on every turn -- O(n) write
amplification, flash wear, and a RAM spike each save. Rows make it O(1).

The pragmas are tuned for a low-RAM phone: a 1 MB page cache (the SQLite
default is 2 MB) and temp b-trees held in memory rather than spilled to flash.
"""
from __future__ import annotations

import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    model       TEXT,
    summary     TEXT,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT,
    tool_calls   TEXT,
    tool_call_id TEXT,
    name         TEXT,
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    kind       TEXT,
    created_at INTEGER NOT NULL,
    used_at    INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content);
"""

PRAGMAS = (
    "journal_mode=WAL",
    "synchronous=NORMAL",
    "temp_store=MEMORY",
    "cache_size=-1000",
    "busy_timeout=5000",
)


def _now() -> int:
    return int(time.time())


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        # isolation_level=None -> autocommit. With WAL + synchronous=NORMAL a
        # small write costs almost nothing, and durability is immediate.
        self.con = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        for p in PRAGMAS:
            try:
                self.con.execute("PRAGMA " + p)
            except sqlite3.Error:
                pass
        self.con.executescript(SCHEMA)

    def close(self) -> None:
        try:
            self.con.close()
        except sqlite3.Error:
            pass

    # -- sessions ----------------------------------------------------------
    def ensure_session(self, sid: str, model: str = "") -> None:
        now = _now()
        self.con.execute(
            "INSERT INTO sessions(id, model, created_at, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
            (sid, model, now, now),
        )

    def set_title(self, sid: str, title: str) -> None:
        self.con.execute("UPDATE sessions SET title=? WHERE id=?", (title, sid))

    def set_summary(self, sid: str, text: str) -> None:
        self.con.execute("UPDATE sessions SET summary=? WHERE id=?", (text, sid))

    def get_summary(self, sid: str):
        row = self.con.execute(
            "SELECT summary FROM sessions WHERE id=?", (sid,)).fetchone()
        return row["summary"] if row else None

    def sessions(self, limit: int = 20):
        return self.con.execute(
            "SELECT id, title, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    # -- messages ----------------------------------------------------------
    def add(self, sid: str, role: str, content=None, tool_calls=None,
            tool_call_id=None, name=None) -> int:
        cur = self.con.execute(
            "INSERT INTO messages(session_id, role, content, tool_calls, "
            "tool_call_id, name, created_at) VALUES(?,?,?,?,?,?,?)",
            (sid, role, content,
             json.dumps(tool_calls) if tool_calls else None,
             tool_call_id, name, _now()),
        )
        self.con.execute("UPDATE sessions SET updated_at=? WHERE id=?", (_now(), sid))
        return cur.lastrowid

    def window(self, sid: str, limit: int = 12):
        rows = self.con.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (sid, int(limit)),
        ).fetchall()
        return list(reversed(rows))

    def older_than(self, sid: str, keep: int):
        """Every message except the newest `keep`, oldest first."""
        rows = self.con.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC", (sid,),
        ).fetchall()
        return rows[:-int(keep)] if keep else list(rows)

    def count(self, sid: str) -> int:
        return self.con.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,),
        ).fetchone()["c"]

    # -- memories ----------------------------------------------------------
    def memory_add(self, content: str, kind: str = "fact") -> int:
        cur = self.con.execute(
            "INSERT INTO memories(content, kind, created_at) VALUES(?,?,?)",
            (content, kind, _now()),
        )
        rid = cur.lastrowid
        self.con.execute(
            "INSERT INTO memories_fts(rowid, content) VALUES(?,?)", (rid, content))
        return rid

    def memory_search(self, query: str, limit: int = 5):
        try:
            return self.con.execute(
                "SELECT m.id, m.content, m.kind FROM memories_fts f "
                "JOIN memories m ON m.id = f.rowid "
                "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, int(limit)),
            ).fetchall()
        except sqlite3.Error:
            # FTS5 MATCH rejects raw punctuation; fall back to a substring scan.
            return self.con.execute(
                "SELECT id, content, kind FROM memories WHERE content LIKE ? LIMIT ?",
                ("%" + query + "%", int(limit)),
            ).fetchall()

    def memory_count(self) -> int:
        return self.con.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]

    # -- session management ------------------------------------------------
    def rename_session(self, sid: str, new_id: str) -> bool:
        now = _now()
        cur = self.con.execute(
            "UPDATE sessions SET id=?, updated_at=? WHERE id=?", (new_id, now, sid))
        if cur.rowcount == 0:
            return False
        self.con.execute("UPDATE messages SET session_id=? WHERE session_id=?",
                         (new_id, sid))
        return True

    def delete_session(self, sid: str) -> bool:
        cur = self.con.execute("DELETE FROM sessions WHERE id=?", (sid,))
        if cur.rowcount == 0:
            return False
        self.con.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        return True

    def search_sessions(self, query: str, limit: int = 10):
        try:
            return self.con.execute(
                "SELECT id, title, updated_at FROM sessions "
                "WHERE id LIKE ? OR title LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                ("%" + query + "%", "%" + query + "%", int(limit)),
            ).fetchall()
        except sqlite3.Error:
            return []

    def session_info(self, sid: str) -> dict:
        row = self.con.execute(
            "SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return {}
        msgs = self.con.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id=?",
            (sid,)).fetchone()["c"]
        return {
            "id": row["id"],
            "title": row["title"] or "",
            "model": row["model"] or "",
            "summary": row["summary"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": msgs,
        }

    def export_session(self, sid: str) -> str:
        info = self.session_info(sid)
        if not info:
            return "session not found: %s" % sid
        lines = [
            "Session: %s" % sid,
            "Title: %s" % (info["title"] or "(none)"),
            "Model: %s" % (info["model"] or "(none)"),
            "Messages: %d" % info["message_count"],
            "=" * 60,
            "",
        ]
        rows = self.con.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id",
            (sid,)).fetchall()
        for r in rows:
            role = r["role"]
            content = r["content"] or ""
            if r["tool_calls"]:
                try:
                    calls = json.loads(r["tool_calls"])
                    names = [c.get("function", {}).get("name", "?")
                             for c in calls]
                    content = (content + " [tools: " + ", ".join(names) + "]").strip()
                except ValueError:
                    pass
            if role == "tool":
                role = "tool:" + (r["name"] or "?")
            lines.append("[%s] %s" % (role, content))
            lines.append("")
        return "\n".join(lines)

    def prune_sessions(self, keep: int = 20) -> int:
        """Delete all but the newest `keep` sessions. Returns count deleted."""
        rows = self.con.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (keep, keep)).fetchall()
        deleted = 0
        for r in rows:
            if self.delete_session(r["id"]):
                deleted += 1
        return deleted
