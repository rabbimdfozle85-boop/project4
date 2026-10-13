"""Measure FHONE's real footprint. Each child reports its own kernel VmHWM.

Run: python3 tests/measure.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import fake_server  # noqa: E402

TAIL = ("\nimport sys as _s\n"
        "_v = open('/proc/self/status').read()\n"
        "print('VmHWM:' + _v.split('VmHWM:')[1].split('kB')[0].strip())\n")


def run(label: str, code: str, env: dict) -> float:
    proc = subprocess.run([sys.executable, "-c", code + TAIL],
                          capture_output=True, text=True, timeout=120,
                          cwd=str(ROOT), env=env)
    peak = None
    for line in proc.stdout.splitlines():
        if line.startswith("VmHWM:"):
            peak = int(line.split(":")[1]) / 1024.0
    if peak is None:
        print("  %-52s FAILED  %s" % (label, (proc.stderr or "").strip()[-140:]))
        return -1.0
    print("  %-52s %6.1f MB" % (label, peak))
    return peak


def main() -> int:
    httpd, base = fake_server.serve(0)
    home = Path(tempfile.mkdtemp(prefix="fhone-measure-")) / "fh"
    home.mkdir(parents=True)
    import json
    (home / "config.json").write_text(json.dumps({
        "model": "test-model", "approve_shell": False,
        "sandbox_roots": [str(home.parent)]}))

    env = dict(os.environ)
    env["FHONE_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)

    print("\n=== FHONE footprint (per-child peak RSS, kernel VmHWM) ===\n")
    run("bare interpreter", "pass", env)
    run("stdlib imports only",
        "import sqlite3, ssl, urllib.request, json, gzip, hashlib, subprocess, readline, re, html.parser, fnmatch, shlex, zipfile, uuid", env)
    run("FHONE fully wired + db + providers",
        "import sys; sys.path.insert(0, %r)\n"
        "from fhone import config\n"
        "from fhone.store import Store\n"
        "from fhone import providers, tools, agent, ui\n"
        "cfg = config.load(); cfg['model']='test-model'\n"
        "s = Store(cfg['db']); p = providers.ProviderPool(s, cfg); p.seed_defaults()\n"
        "t = tools.Tools(cfg, s, ui.QuietUI(), p); a = agent.Agent(cfg, s, p, t, ui.QuietUI())\n"
        "print('wired', len(t.names()), 'tools')" % str(ROOT), env)
    run("FHONE + a full streamed turn with a tool call",
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "import fake_server\n"
        "httpd, base = fake_server.serve(0)\n"
        "from fhone import config\n"
        "from fhone.store import Store\n"
        "from fhone import providers, tools, agent, ui\n"
        "cfg = config.load(); cfg['model']='test-model'\n"
        "p = providers.ProviderPool(Store(cfg['db']), cfg); p.seed_defaults()\n"
        "p.add_provider('fake', base, api_style='openai', priority=1)\n"
        "p.add_key('fake', 'k')\n"
        "s = Store(cfg['db'])\n"
        "t = tools.Tools(cfg, s, ui.QuietUI(), p); a = agent.Agent(cfg, s, p, t, ui.QuietUI())\n"
        "print(a.turn('m', 'please echo hi')[:40])\n"
        "httpd.shutdown()" % (str(ROOT), str(HERE)), env)
    run("FHONE + 10k messages + FTS5",
        "import sys; sys.path.insert(0, %r)\n"
        "from fhone import config\n"
        "from fhone.store import Store\n"
        "cfg = config.load()\n"
        "s = Store(cfg['db'])\n"
        "s.ensure_session('big', 'm')\n"
        "for i in range(10000):\n"
        "    s.add('big', 'user', 'the quick brown fox jumps over the lazy dog ' + str(i))\n"
        "print(len(s.search_messages('brown', 5)), 'hits')" % str(ROOT), env)

    print()
    httpd.shutdown()

    # --- the resident supervisor: the real production footprint -------------
    print("=== resident supervisor (portal + scheduler threads) ===\n")
    sup_home = Path(tempfile.mkdtemp(prefix="fhone-sup-")) / "fh"
    sup_home.mkdir(parents=True)
    (sup_home / "config.json").write_text(json.dumps({
        "model": "test-model", "portal_port": 0, "portal_host": "127.0.0.1"}))
    senv = dict(os.environ)
    senv["FHONE_HOME"] = str(sup_home)
    senv["PYTHONPATH"] = str(ROOT)
    proc = subprocess.Popen([sys.executable, "-m", "fhone", "_supervise"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=senv, cwd=str(ROOT), start_new_session=True)
    peak = None
    try:
        for _ in range(40):
            time.sleep(0.5)
            try:
                status = Path("/proc/%d/status" % proc.pid).read_text()
            except OSError:
                break
            for line in status.splitlines():
                if line.startswith("VmHWM:"):
                    peak = int(line.split()[1]) / 1024.0
            if "supervisor ready" in (sup_home / "fhone.log").read_text(
                    encoding="utf-8", errors="replace"):
                break
        if peak:
            print("  %-52s %6.1f MB" % ("supervisor: portal + scheduler resident",
                                        peak))
        else:
            print("  supervisor measurement FAILED")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
