"""End-to-end smoke test for Mini-Hermes. No network, no API key required.

Runs the real CLI as a subprocess against a canned SSE endpoint, then checks:
  1. the streamed tool call was parsed and its split arguments reassembled
  2. the tool actually executed
  3. the second round produced a final answer
  4. every turn landed in SQLite
  5. FTS5 memory recall works
  6. peak child RSS stayed in budget
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import fake_server  # noqa: E402

PASS, FAIL = "\033[32mok\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print("  %s  %s%s" % (PASS if ok else FAIL, label,
                          ("  -- " + detail) if detail else ""))


def main() -> int:
    httpd, base = fake_server.serve(0)
    tmp = tempfile.mkdtemp(prefix="minihermes-")
    home = Path(tmp) / ".minihermes"
    home.mkdir(parents=True)
    (home / "config.json").write_text(json.dumps({
        "api_base": base,
        "model": "test-model",
        "approve_shell": False,
        "context_window": 8,
        "sandbox_roots": [tmp],
    }))

    env = dict(os.environ)
    env["MINIHERMES_HOME"] = str(home)
    env["MINIHERMES_API_KEY"] = "test-key"
    env["PYTHONPATH"] = str(ROOT)

    print("\nmini-hermes smoke test")
    print("  home: %s" % home)

    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    proc = subprocess.run(
        [sys.executable, "-m", "minihermes", "-q", "please echo hi", "--yolo"],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60,
    )
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak_mb = max(after, before) / 1024.0
    out = proc.stdout + proc.stderr

    check("process exited 0", proc.returncode == 0,
          "rc=%d %s" % (proc.returncode, out.strip()[-120:] if proc.returncode else ""))
    check("one-shot printed the final answer",
          "Done." in proc.stdout, repr(proc.stdout.strip()[:90]))
    check("tool really executed (echo output present)",
          "exit=0" in proc.stdout and "hi" in proc.stdout)

    # -- database ------------------------------------------------------------
    from minihermes.store import Store

    db = home / "minihermes.db"
    check("sqlite file created", db.exists())
    store = Store(str(db))
    sessions = store.sessions()
    check("session row written", len(sessions) == 1, "%d session(s)" % len(sessions))
    sid = sessions[0]["id"]
    msgs = store.window(sid, 50)
    roles = [r["role"] for r in msgs]
    check("turn persisted as user/assistant/tool/assistant",
          roles == ["user", "assistant", "tool", "assistant"], str(roles))

    assistant_calls = None
    for r in msgs:
        if r["role"] == "assistant" and r["tool_calls"]:
            assistant_calls = json.loads(r["tool_calls"])
    args = (assistant_calls or [{}])[0].get("function", {}).get("arguments")
    check("split SSE arguments reassembled correctly",
          args == '{"command": "echo hi"}', repr(args))
    check("tool result stored with its call id",
          any(r["role"] == "tool" and r["tool_call_id"] == "call_1" for r in msgs))

    # -- FTS5 memory ---------------------------------------------------------
    store.memory_add("the user prefers terse replies", "pref")
    store.memory_add("device is a realme C25s running Android 11", "env")
    hits = store.memory_search("terse")
    check("FTS5 memory recall", len(hits) == 1 and "terse" in hits[0]["content"],
          "%d hit(s)" % len(hits))
    check("FTS5 survives punctuation in the query",
          len(store.memory_search('what"s the "deal"?')) >= 0)
    check("memories persisted to disk", store.memory_count() == 2)

    # -- footprint -----------------------------------------------------------
    check("peak child RSS under 60 MB", peak_mb < 60, "%.1f MB" % peak_mb)
    print("  peak child RSS: %.1f MB" % peak_mb)

    store.close()
    httpd.shutdown()
    print("\n%d/%d passed\n" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
