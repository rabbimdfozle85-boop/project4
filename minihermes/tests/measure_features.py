"""Measure the marginal RSS of each candidate Mini-Hermes feature.

Run:  python3 tests/measure_features.py

Everything is measured from /proc/self/status (VmRSS = current, VmHWM = peak),
never self-reported by the code under test. Each feature is added cumulatively
so the delta attributable to it is visible.
"""
from __future__ import annotations

import gc
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def vm() -> dict:
    out = {}
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:") or line.startswith("VmHWM:"):
                key = line.split(":")[0]
                out[key] = int(line.split()[1]) / 1024.0
    return out


ROWS = []


def mark(label: str, note: str = "") -> None:
    gc.collect()
    v = vm()
    ROWS.append((label, v["VmRSS"], v["VmHWM"], note))
    print("%-44s rss=%6.1f  peak=%6.1f  %s" % (label, v["VmRSS"], v["VmHWM"], note))


def child_peak_mb(code: str) -> float:
    """Peak RSS of a fresh `python3 -c <code>` child, in MB."""
    before = _children_peak()
    subprocess.run([sys.executable, "-c", code], check=True,
                   capture_output=True, timeout=60)
    after = _children_peak()
    return max(after, before) / 1024.0


def _children_peak() -> int:
    import resource
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mh-measure-"))
    print("\n=== mini-hermes feature cost measurements ===\n")

    mark("baseline: interpreter")

    import sqlite3 as _s  # noqa: F401
    mark("+ sqlite3")

    import ssl  # noqa: F401
    import urllib.request  # noqa: F401
    mark("+ ssl + urllib.request")

    import asyncio  # noqa: F401
    mark("+ asyncio")

    # --- skills: index-only vs loading every body ---------------------------
    skills_dir = tmp / "skills"
    skills_dir.mkdir()
    body = "x" * 4000
    for i in range(60):
        (skills_dir / ("skill%02d.md" % i)).write_text(
            "---\nname: skill%02d\ndescription: a skill about topic %d\n---\n%s\n"
            % (i, i, body))
    mark("on disk: 60 skill files", "~240 KB of markdown")

    def index_skills(d):
        idx = []
        for p in sorted(d.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            m = re.match(r"---\n(.*?)\n---\n", text, re.S)
            if m:
                meta = dict(l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)
                idx.append((meta.get("name", p.stem),
                            meta.get("description", "").strip(), str(p)))
        return idx

    idx = index_skills(skills_dir)
    mark("+ skills INDEX only (60 entries)", "%d entries held" % len(idx))

    def load_all_bodies(d):
        return {p.stem: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.md"))}

    bodies = load_all_bodies(skills_dir)
    mark("+ skills: ALL bodies resident", "%d bodies held" % len(bodies))
    del bodies
    mark("  (bodies released)")

    # --- session history + FTS ---------------------------------------------
    db = tmp / "sessions.db"
    con = sqlite3.connect(str(db), isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA cache_size=-1000")
    con.execute("PRAGMA temp_store=MEMORY")
    con.executescript(
        "CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT);"
        "CREATE VIRTUAL TABLE messages_fts USING fts5(content);")
    mark("+ sqlite session db (empty, 1 MB cache)")

    filler = ("the quick brown fox jumps over the lazy dog " * 6)[:240]
    for i in range(10000):
        con.execute("INSERT INTO messages(session_id, role, content) VALUES('s1','user',?)", (filler,))
        con.execute("INSERT INTO messages_fts(rowid, content) VALUES(?,?)", (i + 1, filler))
    mark("+ 10,000 messages + FTS index", "on disk, cache capped at 1 MB")

    hit = con.execute(
        "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'brown' LIMIT 5").fetchall()
    mark("  after an FTS search", "%d hits" % len(hit))

    db_size = db.stat().st_size / 1024 / 1024
    print("  (session db on disk: %.1f MB)" % db_size)

    # --- a full context window ---------------------------------------------
    window = [{"role": "user", "content": filler} for _ in range(40)]
    body_json = json.dumps({"model": "m", "messages": window})
    mark("+ 6,000-token request body serialized", "%.0f KB json" % (len(body_json) / 1024))
    del body_json
    mark("  (request body released)")

    # --- an UNCAPPED tool output (the failure mode) -------------------------
    huge = "A" * (10 * 1024 * 1024)
    mark("+ UNCAPPED 10 MB tool output", "this is what the cap prevents")
    del huge
    mark("  (uncapped output released)")

    capped = ("A" * 10 * 1024 * 1024)[:6000]
    mark("+ CAPPED 6 KB tool output", "the configured behaviour")

    con.close()

    # --- separate processes -------------------------------------------------
    print()
    bare = child_peak_mb("import sqlite3, ssl, urllib.request, json, subprocess; pass")
    print("fresh child, stdlib imports only            peak=%6.1f" % bare)

    agent_child = child_peak_mb(
        "import sys; sys.path.insert(0, %r); "
        "from minihermes import config, store, tools, llm, agent, context, ui; "
        "cfg = config.load(); cfg['api_key']='x'; cfg['model']='m'; "
        "s = store.Store(cfg['db']); c = llm.Client(cfg['api_base'], 'x'); "
        "t = tools.Tools(cfg, s, ui.UI()); a = agent.Agent(cfg, s, c, t, ui.UI()); "
        "print('wired')" % str(ROOT))
    print("fresh child, FULL agent wired + db open    peak=%6.1f" % agent_child)

    # --- summary ------------------------------------------------------------
    print("\n=== deltas from baseline ===")
    base = ROWS[0][1]
    for label, rss, peak, note in ROWS:
        print("  %+7.1f MB  %s" % (rss - base, label))
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
