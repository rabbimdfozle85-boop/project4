"""Clean per-child peak RSS. Each child prints its OWN kernel VmHWM.

The parent cannot use RUSAGE_CHILDREN for several children: it returns the max
across every child ever waited for, so the second reading is just the first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TAIL = (
    "\nimport sys as _s\n"
    "_v = open('/proc/self/status').read()\n"
    "print('VmHWM:' + _v.split('VmHWM:')[1].split('kB')[0].strip())\n"
)

CASES = [
    ("bare interpreter", "pass"),
    ("stdlib: sqlite3/ssl/urllib/gzip/hashlib/subprocess/readline",
     "import sqlite3, ssl, urllib.request, json, gzip, hashlib, subprocess, readline, shlex, uuid, datetime"),
    ("+ asyncio imported", "import asyncio, sqlite3, ssl, urllib.request, json, gzip, hashlib, subprocess, readline"),
    ("+ asyncio event loop created",
     "import asyncio\nloop = asyncio.new_event_loop()\nloop.run_until_complete(asyncio.sleep(0))"),
    ("+ ssl default context built", "import ssl\nssl.create_default_context()"),
    ("full agent wired + db opened + turn context built",
     "import sys; sys.path.insert(0, %r)\n"
     "from minihermes import config, store, tools, llm, agent, context, ui\n"
     "cfg = config.load(); cfg['api_key'] = 'x'; cfg['model'] = 'm'\n"
     "s = store.Store(cfg['db'])\n"
     "c = llm.Client(cfg['api_base'], 'x')\n"
     "t = tools.Tools(cfg, s, ui.UI())\n"
     "a = agent.Agent(cfg, s, c, t, ui.UI())\n"
     "s.ensure_session('probe', 'm')\n"
     "s.add('probe', 'user', 'hello')\n"
     "msgs = context.build(s, 'probe', cfg)\n"
     "assert msgs" % str(ROOT)),
    ("full agent + a streamed turn against a local SSE endpoint",
     "import sys; sys.path.insert(0, %r)\n"
     "sys.path.insert(0, %r)\n"
     "import fake_server\n"
     "httpd, base = fake_server.serve(0)\n"
     "from minihermes import config, store, tools, llm, agent, context, ui\n"
     "cfg = config.load(); cfg['api_key'] = 'x'; cfg['model'] = 'm'\n"
     "cfg['approve_shell'] = False; cfg['api_base'] = base\n"
     "s = store.Store(cfg['db'])\n"
     "c = llm.Client(base, 'x')\n"
     "t = tools.Tools(cfg, s, ui.UI())\n"
     "a = agent.Agent(cfg, s, c, t, ui.QuietUI())\n"
     "a.turn('probe', 'echo hi')\n"
     "httpd.shutdown()" % (str(ROOT), str(ROOT / "tests"))),
]


def main() -> int:
    print("\n=== per-child peak RSS (each child self-reports kernel VmHWM) ===\n")
    for label, code in CASES:
        proc = subprocess.run(
            [sys.executable, "-c", code + TAIL],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
            env={**_env(), "MINIHERMES_HOME": _home()},
        )
        peak = None
        for line in proc.stdout.splitlines():
            if line.startswith("VmHWM:"):
                peak = int(line.split(":")[1]) / 1024.0
        if peak is None:
            print("  %-62s FAILED  %s" % (label, (proc.stderr or "").strip()[-160:]))
        else:
            print("  %-62s %6.1f MB" % (label, peak))
    print()
    return 0


def _env() -> dict:
    import os
    return dict(os.environ)


def _home() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="mh-child-")


if __name__ == "__main__":
    raise SystemExit(main())
