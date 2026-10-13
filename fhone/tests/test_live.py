"""LIVE end-to-end test: a real provider, a real model, a real tool call.

This is NOT a mocked test. It talks to OpenRouter over the network and asserts
that FHONE completes a genuine turn, calls a tool, and persists the result.

Requires network access and a key. Run:
    python3 tests/test_live.py
    python3 tests/test_live.py --model <id>     # try a different model
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

KEYS_FILE = Path("/home/codespace/.hermes/cache/scratch/mh_keys.json")
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
RESULTS: list = []

CANDIDATE_MODELS = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "thinkingmachines/inkling:free",
    "cohere/north-mini-code:free",
    "poolside/laguna-s-2.1:free",
]


def check(label, ok, detail=None):
    RESULTS.append((label, bool(ok)))
    print("  %s  %-50s %s" % (PASS if ok else FAIL, label,
                              ("-- " + str(detail)[:70]) if detail else ""))


def main() -> int:
    model = None
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]

    if not KEYS_FILE.exists():
        print("no key file at %s" % KEYS_FILE)
        return 2
    keys = json.loads(KEYS_FILE.read_text())
    or_key = keys["openrouter"]["asad"]

    home = Path(tempfile.mkdtemp(prefix="fhone-live-")) / "fh"
    home.mkdir(parents=True)
    os.environ["FHONE_HOME"] = str(home)

    from fhone import config
    from fhone import __main__ as main_mod

    cfg = config.load()
    cfg["approve_shell"] = False
    cfg["sandbox_roots"] = [str(home.parent)]
    cfg["request_timeout"] = 90
    cfg["max_retries"] = 2
    config.save(cfg)

    store = main_mod.Store(cfg["db"])
    pool = main_mod._pool_for(cfg, store)
    pool.add_provider("openrouter", "https://openrouter.ai/api/v1",
                      label="OpenRouter", api_style="openai", priority=1)
    pool.add_key("openrouter", or_key, "asad")
    store.close()

    print("\nFHONE LIVE test (real provider, real model)")
    print("  home: %s" % home)

    env = dict(os.environ)
    env["FHONE_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)

    def run(args, timeout=180):
        return subprocess.run([sys.executable, "-m", "fhone"] + args,
                              capture_output=True, text=True, env=env,
                              cwd=str(ROOT), timeout=timeout)

    # ---------------------------------------------------------- pick a model
    print("\nmodel selection")
    if model:
        chosen = model
        print("  using --model %s" % chosen)
    else:
        chosen = None
        for candidate in CANDIDATE_MODELS:
            probe = run(["-q", "Reply with exactly: READY", "--model", candidate,
                         "--yolo"], timeout=120)
            text = (probe.stdout or "").strip()
            if probe.returncode == 0 and "READY" in text.upper():
                chosen = candidate
                print("  %-46s OK" % candidate)
                break
            print("  %-46s failed (%s)" % (candidate,
                                           (text or probe.stderr or "")[:60].replace("\n", " ")))
        check("found a working free model", chosen is not None, chosen)

    if not chosen:
        print("\ncould not reach any free model — aborting")
        return 1

    # ---------------------------------------------------- a REAL plain turn
    print("\nreal turn (no tools)")
    proc = run(["-q", "What is 2+2? Answer with just the number.", "--model", chosen,
                "--yolo"], timeout=180)
    out = (proc.stdout or "").strip()
    check("plain turn returns text", proc.returncode == 0 and bool(out),
          (out or proc.stderr)[:70])
    check("plain turn answered correctly", "4" in out, out[:50])

    # ---------------------------------------------- a REAL tool-calling turn
    print("\nreal turn (tool call)")
    marker = "fhone-live-%d" % int(time.time())
    proc = run(["-q",
                "Run this exact shell command using your terminal tool, then tell "
                "me its output: echo %s" % marker,
                "--model", chosen, "--yolo"], timeout=240)
    out = (proc.stdout or "").strip()
    check("tool turn exited 0", proc.returncode == 0, (proc.stderr or "")[-90:])
    check("model actually called a tool and got the real output",
          marker in out, out[:100])
    print("      answer: %s" % out[:120].replace("\n", " "))

    # -------------------------------------------------- persistence check
    print("\npersistence")
    store = main_mod.Store(cfg["db"])
    sessions = store.sessions()
    check("session recorded in SQLite", len(sessions) >= 1, len(sessions))
    total_msgs = 0
    tool_msgs = 0
    for row in sessions:
        rows = store.window(row["id"], 100)
        total_msgs += len(rows)
        tool_msgs += sum(1 for r in rows if r["role"] == "tool")
    check("messages persisted", total_msgs >= 4, total_msgs)
    check("a real tool result was stored", tool_msgs >= 1, tool_msgs)

    usage = store.usage_total()
    check("usage recorded against the real provider", (usage["r"] or 0) >= 1,
          "requests=%s" % usage["r"])
    store.close()

    # ------------------------------------------- a REAL multi-turn session
    print("\nmulti-turn memory in one session")
    sid = "live-multi"
    proc = run(["-q", "My favourite colour is chartreuse. Just acknowledge.",
                "--session", sid, "--model", chosen, "--yolo"], timeout=180)
    check("turn 1 ok", proc.returncode == 0, (proc.stderr or "")[-60:])
    proc = run(["-q", "What did I say my favourite colour was? One word.",
                "--session", sid, "--model", chosen, "--yolo"], timeout=180)
    out = (proc.stdout or "").strip().lower()
    check("turn 2 recalled turn 1 from the database",
          "chartreuse" in out, out[:60])

    # ------------------------------------------------------- the log check
    log = home / "fhone.log"
    check("no key material in any log",
          not log.exists() or or_key not in log.read_text(encoding="utf-8",
                                                          errors="replace"))

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n%s" % ("=" * 64))
    print("LIVE: %d/%d passed  (model: %s)" % (passed, len(RESULTS), chosen))
    if passed != len(RESULTS):
        print("\nfailures:")
        for label, ok in RESULTS:
            if not ok:
                print("  - %s" % label)
    print("=" * 64)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
