"""Tests for the surfaces (portal, Telegram) and the resident supervisor.

Run: python3 tests/test_surfaces.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import fake_server  # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
RESULTS: list = []


def check(label, ok, detail=None):
    RESULTS.append((label, bool(ok)))
    print("  %s  %-54s %s" % (PASS if ok else FAIL, label,
                              ("-- " + str(detail)[:60]) if detail else ""))


def section(name):
    print("\n%s" % name)


def main() -> int:
    httpd, base = fake_server.serve(0)
    home = Path(tempfile.mkdtemp(prefix="fhone-surf-")) / "fh"
    home.mkdir(parents=True)
    os.environ["FHONE_HOME"] = str(home)

    from fhone import config, supervisor
    from fhone import __main__ as main_mod

    cfg = config.load()
    cfg["model"] = "test-model"
    cfg["approve_shell"] = False
    cfg["sandbox_roots"] = [str(home.parent)]
    cfg["portal_port"] = 0                    # let the OS choose
    config.save(cfg)
    cfg = config.load()

    print("FHONE surfaces + supervisor test")
    print("  home: %s" % home)

    # ------------------------------------------------------------- pidfile
    section("supervisor: pidfile identity")
    check("no pidfile initially", not supervisor.is_running(cfg))
    supervisor.write_pidfile(cfg)
    check("pidfile written", supervisor.pidfile(cfg).exists())
    check("live_pid sees our own process", supervisor.live_pid(cfg) == os.getpid())
    check("is_running true", supervisor.is_running(cfg))

    entry = supervisor.read_pidfile(cfg)
    check("pidfile records (pid, start_time)", entry is not None and len(entry) == 2,
          entry)
    check("recorded start time is non-zero", entry and entry[1] != 0, entry)

    # a recycled PID must not be mistaken for the supervisor
    supervisor.pidfile(cfg).write_text("%d:1" % os.getpid(), encoding="utf-8")
    check("a mismatched start time means 'not running'",
          supervisor.live_pid(cfg) is None)
    supervisor.clear_pidfile(cfg)
    check("pidfile cleared", not supervisor.pidfile(cfg).exists())

    # -------------------------------------------------------------- portal
    section("portal")
    from fhone.surfaces import portal as portal_mod
    agent, store, pool, tools, ui = main_mod.build(cfg, quiet=True)
    pool.add_provider("fake", base, api_style="openai", priority=1)
    pool.add_key("fake", "k")
    ctx = portal_mod.Context(cfg, store, pool, tools, agent)
    portal = portal_mod.Portal(cfg, ctx)
    url = portal.start()
    check("portal started on an ephemeral port", bool(url) and ":" in url, url)

    def get(path, headers=None):
        req = urllib.request.Request(url + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def post(path, body):
        req = urllib.request.Request(
            url + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    code, body = get("/health")
    check("/health needs no auth", code == 200, code)

    code, body = get("/api/status")
    check("/api/status on loopback needs no token", code == 200, code)
    payload = json.loads(body)
    check("status reports the product", payload.get("product") == "fhone")
    check("status reports a version", bool(payload.get("version")))
    check("status lists 30 tools", len(payload.get("tools") or []) == 30,
          len(payload.get("tools") or []))
    check("status reports the fake provider",
          any(p["id"] == "fake" for p in payload.get("providers") or []))

    code, body = get("/")
    check("dashboard serves HTML", code == 200 and "<!doctype html>" in body.lower())

    code, body = post("/api/chat", {"message": "please echo hi"})
    check("portal chat runs a turn", code == 200, code)
    answer = json.loads(body).get("answer", "")
    check("portal chat returns the answer", "Done." in answer, answer[:50])

    code, body = post("/api/chat", {})
    check("portal chat rejects an empty message", code == 400, code)

    code, body = post("/api/memory", {"content": "portal saved this"})
    check("portal can save a memory", code == 200 and json.loads(body).get("id"))

    code, body = get("/api/sessions")
    check("portal lists sessions", code == 200 and "web-default" in body)

    code, body = get("/nope")
    check("unknown route is a 404", code == 404, code)

    # auth on a non-loopback bind
    section("portal: auth is mandatory off loopback")
    cfg2 = dict(cfg)
    cfg2["portal_host"] = "0.0.0.0"
    cfg2["portal_port"] = 0
    cfg2["portal_token"] = ""
    ctx2 = portal_mod.Context(cfg2, store, pool, tools, agent)
    p2 = portal_mod.Portal(cfg2, ctx2)
    try:
        p2.start()
        check("refuses a 0.0.0.0 bind with no token", False, "it started!")
    except portal_mod.PortalError as e:
        check("refuses a 0.0.0.0 bind with no token", "token" in str(e), str(e)[:50])

    cfg2["portal_token"] = "s3cret-token"
    p3 = portal_mod.Portal(cfg2, ctx2)
    url3 = p3.start()
    check("starts with a token set", bool(url3))
    code, _ = get("/api/status")
    # loopback bypasses the token, so this should still work
    check("loopback still works with a token configured", code == 200, code)
    p3.stop()
    portal.stop()

    # ------------------------------------------------------------ telegram
    section("telegram")
    from fhone.surfaces import telegram as tg_mod

    check("message splitting: short text untouched",
          tg_mod.split_message("hi") == ["hi"])
    long_text = ("para one " * 500) + "\n\n" + ("para two " * 500)
    parts = tg_mod.split_message(long_text)
    check("message splitting: every part under the cap",
          all(len(p) <= tg_mod.MAX_MESSAGE for p in parts), len(parts))
    check("message splitting: content preserved",
          "para one" in parts[0] and "para two" in parts[-1])

    sent = []
    surface = tg_mod.TelegramSurface(cfg, lambda: main_mod.build(cfg, quiet=True))
    surface.api.send_message = lambda chat_id, text, reply_to=None: sent.append(
        (chat_id, text)) or True
    surface.api.send_action = lambda *a, **k: None
    surface.api.set_commands = lambda *a, **k: True

    cfg["telegram_allowlist"] = ["111"]
    surface._allow = {"111"}
    surface._handle({"message": {"text": "hi", "chat": {"id": 999},
                                 "from": {"id": "222"}}})
    check("telegram refuses a non-allowlisted user",
          sent and "private" in sent[-1][1].lower(), sent[-1][1][:40] if sent else "")

    sent.clear()
    surface._handle({"message": {"text": "/help", "chat": {"id": 999},
                                 "from": {"id": "111"}}})
    check("telegram answers /help", sent and "/status" in sent[-1][1],
          sent[-1][1][:40] if sent else "")

    sent.clear()
    surface._handle({"message": {"text": "please echo hi", "chat": {"id": 999},
                                 "from": {"id": "111"}}})
    check("telegram runs a turn", sent and "Done." in sent[-1][1],
          sent[-1][1][:50] if sent else "")

    sent.clear()
    surface._handle({"message": {"text": "/remember telegram fact",
                                 "chat": {"id": 999}, "from": {"id": "111"}}})
    check("telegram /remember works", sent and "saved" in sent[-1][1],
          sent[-1][1][:40] if sent else "")

    sent.clear()
    surface._handle({"message": {"text": "/status", "chat": {"id": 999},
                                 "from": {"id": "111"}}})
    check("telegram /status works", sent and "FHONE status" in sent[-1][1],
          sent[-1][1][:40] if sent else "")

    sent.clear()
    surface._handle({"message": {"text": "", "chat": {"id": 999},
                                 "from": {"id": "111"}}})
    check("telegram ignores an empty message", not sent)

    # ---------------------------------------------------------- supervisor
    section("supervisor: real start/stop lifecycle")
    env = dict(os.environ)
    env["FHONE_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)

    proc = subprocess.run([sys.executable, "-m", "fhone", "service", "status"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT),
                          timeout=60)
    check("service status reports stopped", "stopped" in proc.stdout,
          proc.stdout.strip()[:50])

    proc = subprocess.run([sys.executable, "-m", "fhone", "start"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT),
                          timeout=120)
    check("fhone start reports a pid", "started" in proc.stdout, proc.stdout.strip()[:60])

    started = False
    for _ in range(60):
        time.sleep(0.5)
        if supervisor.is_running(cfg):
            started = True
            break
    check("supervisor is genuinely running", started)

    proc = subprocess.run([sys.executable, "-m", "fhone", "start"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT),
                          timeout=60)
    check("start is idempotent", "already running" in proc.stdout,
          proc.stdout.strip()[:50])

    proc = subprocess.run([sys.executable, "-m", "fhone", "service", "status"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT),
                          timeout=60)
    check("service status reports running", "running (pid" in proc.stdout,
          proc.stdout.strip().splitlines()[1][:50] if proc.stdout else "")

    log = home / "fhone.log"
    check("supervisor wrote a log", log.exists() and log.stat().st_size > 0)
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="replace")
        check("log records the portal", "portal" in text, text.strip().splitlines()[-1][:60] if text.strip() else "")
        check("log does not contain a token", "s3cret-token" not in text)

    proc = subprocess.run([sys.executable, "-m", "fhone", "stop"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT),
                          timeout=120)
    check("fhone stop reports stopped", "stopped" in proc.stdout,
          proc.stdout.strip()[:50])
    stopped = False
    for _ in range(40):
        time.sleep(0.5)
        if not supervisor.is_running(cfg):
            stopped = True
            break
    check("supervisor is genuinely gone", stopped)
    check("pidfile removed on shutdown", not supervisor.pidfile(cfg).exists())

    # boot hook
    proc = subprocess.run([sys.executable, "-m", "fhone", "boot"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT),
                          timeout=60)
    check("boot hook reports a missing Termux:Boot dir",
          "Termux:Boot" in proc.stdout or "boot hook written" in proc.stdout,
          proc.stdout.strip()[:50])

    # ------------------------------------------------------------------ done
    pool.close()
    store.close()
    httpd.shutdown()
    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n%s" % ("=" * 66))
    print("%d/%d passed" % (passed, len(RESULTS)))
    if passed != len(RESULTS):
        print("\nfailures:")
        for label, ok in RESULTS:
            if not ok:
                print("  - %s" % label)
    print("=" * 66)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
