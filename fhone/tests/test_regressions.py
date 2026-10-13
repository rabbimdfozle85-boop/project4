"""Regression tests for bugs found in the v1 bug hunt.

Each test reproduces a specific defect. They are written to FAIL against v1 and
pass once the fix lands, so the fix is proven rather than asserted.

Run:  python3 tests/test_regressions.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import fake_server  # noqa: E402

from fhone import config as config_mod  # noqa: E402
from fhone import providers as providers_mod  # noqa: E402
from fhone import store as store_mod  # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
RESULTS: list = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok)))
    print("  %s  %-54s %s" % (PASS if ok else FAIL, label,
                              ("-- " + str(detail)[:60]) if detail else ""))


def fresh_home() -> Path:
    d = Path(tempfile.mkdtemp(prefix="fhone-reg-")) / "fh"
    d.mkdir(parents=True)
    os.environ["FHONE_HOME"] = str(d)
    return d


def main() -> int:
    from fhone import config, context, llm, scheduler
    from fhone.store import Store

    httpd, base = fake_server.serve(0)
    home = fresh_home()
    cfg = config.load()
    cfg["approve_shell"] = False
    cfg["sandbox_roots"] = [str(home.parent)]
    print("\nFHONE regression suite (bugs found in v1)\n")

    # ---------------------------------------------------------------- BUG 1
    # config.save() dropped secrets that lived in config.json, so `fhone
    # install` silently erased a stored API key.
    print("BUG 1  config.save() erases secrets from config.json")
    (home / "config.json").write_text(json.dumps({
        "model": "test-model", "api_key": "SECRET-KEEP-ME",
        "telegram_token": "TG-KEEP-ME"}))
    cfg1 = config.load()
    config.save(cfg1)
    after = json.loads((home / "config.json").read_text())
    check("api_key survives config.save()", after.get("api_key") == "SECRET-KEEP-ME",
          repr(after.get("api_key"))[:30])
    check("telegram_token survives config.save()",
          after.get("telegram_token") == "TG-KEEP-ME")
    check("non-secret settings still written", after.get("model") == "test-model")

    # ---------------------------------------------------------------- BUG 2
    # maybe_summarize() re-summarised the same old messages on every turn,
    # because nothing recorded what had already been folded in.
    print("\nBUG 2  summarisation re-fires every turn")
    store = Store(cfg["db"])
    store.ensure_session("sum", "test-model")
    for i in range(40):
        store.add("sum", "user", "message %d" % i)
        store.add("sum", "assistant", "reply %d" % i)

    class CountingPool:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None, **kw):
            self.calls += 1
            return {"content": "- a summary", "tool_calls": None}

    cp = CountingPool()
    first = context.maybe_summarize(store, "sum", cfg, cp)
    second = context.maybe_summarize(store, "sum", cfg, cp)
    third = context.maybe_summarize(store, "sum", cfg, cp)
    check("first summarise runs", first)
    check("second does NOT re-summarise", not second, "calls=%d" % cp.calls)
    check("third does NOT re-summarise", not third, "calls=%d" % cp.calls)
    check("only one summariser call total", cp.calls == 1, "calls=%d" % cp.calls)

    # ---------------------------------------------------------------- BUG 3
    # process_manage(action="log") called .read() on a live pipe and blocked
    # forever instead of returning what was available.
    print("\nBUG 3  process_manage log hangs on a running process")
    from fhone import providers, tools, ui
    pool = providers.ProviderPool(store, cfg)
    t = tools.Tools(cfg, store, ui.QuietUI(), pool)
    started = t.dispatch("terminal", {"command": "echo first; sleep 8; echo last",
                                      "background": True})
    bg_id = started.split()[1] if started.startswith("started") else ""
    check("background job started", bool(bg_id), started[:40])

    # The assertion is that the READ does not block on a live pipe. The child
    # needs a moment to emit, so retry briefly rather than racing it.
    result = {}
    deadline = time.time() + 8
    while time.time() < deadline:
        box = {}

        def read_log(box=box):
            box["out"] = t.dispatch("process_manage", {"action": "log",
                                                       "session_id": bg_id})

        th = threading.Thread(target=read_log, daemon=True)
        th.start()
        th.join(timeout=4)
        if th.is_alive():
            result["hung"] = True
            break
        result["out"] = box.get("out", "")
        if "first" in result["out"]:
            break
        time.sleep(0.2)

    check("log returns promptly instead of hanging", not result.get("hung"),
          "thread still blocked")
    check("log shows what the job has produced",
          "first" in result.get("out", ""), repr(result.get("out", ""))[:40])
    t.dispatch("process_manage", {"action": "kill", "session_id": bg_id})

    # ---------------------------------------------------------------- BUG 4
    # A 4xx with a large body left unread bytes on the keep-alive connection,
    # corrupting the NEXT request made with the same client.
    print("\nBUG 4  connection poisoned after a large 4xx body")
    client = llm.Client(base, "k", timeout=20, max_retries=1)
    try:
        client.chat([{"role": "user", "content": "hi"}], model="test-400-long",
                    stream=False)
        check("400 raised as an error", False, "no error raised")
    except llm.LLMError:
        check("400 raised as an error", True)
    try:
        ok = client.chat([{"role": "user", "content": "hi"}], model="test-plain",
                         stream=False)
        check("next request on the same client succeeds",
              ok.get("content") == "plain answer", repr(ok.get("content"))[:40])
    except Exception as e:
        check("next request on the same client succeeds", False,
              "%s: %s" % (type(e).__name__, str(e)[:50]))
    client.close()

    # ---------------------------------------------------------------- BUG 5
    # patch(old="") matched everywhere and inserted text throughout the file.
    print("\nBUG 5  patch with an empty old string corrupts the file")
    target = home.parent / "patchme.txt"
    target.write_text("abc\n")
    out = t.dispatch("patch", {"path": str(target), "old": "", "new": "XX"})
    check("empty old is rejected", out.startswith("error:"), out[:50])
    check("file is unchanged", target.read_text() == "abc\n",
          repr(target.read_text()))

    # ---------------------------------------------------------------- BUG 6
    # /prune deleted messages but left their FTS rows behind.
    print("\nBUG 6  pruning orphans FTS rows")
    store.ensure_session("prune", "m")
    for i in range(5):
        store.add("prune", "user", "prunable content %d" % i)
    before = len(store.search_messages("prunable", 20))
    store.con.execute("DELETE FROM messages WHERE session_id='prune'")
    after = len(store.search_messages("prunable", 20))
    check("FTS returns nothing after the rows are deleted", after == 0,
          "%d before, %d after" % (before, after))

    # ---------------------------------------------------------------- BUG 7
    # cron next_run mixed mktime (local) with gmtime (UTC).
    print("\nBUG 7  cron next_run timezone arithmetic")
    nxt = scheduler.next_run("0 9 * * *", now=1700000000)
    tm = time.gmtime(nxt)
    check("cron '0 9 * * *' lands on 09:00 UTC", tm.tm_hour == 9 and tm.tm_min == 0,
          time.strftime("%Y-%m-%d %H:%M UTC", tm))
    check("cron result is in the future", nxt > 1700000000)
    check("cron result is within 24h", nxt - 1700000000 <= 86400)

    # ---------------------------------------------------------------- BUG 8
    # the `fhone` launcher hardcoded a Termux shebang, breaking on any other
    # host (and in the codespace).
    print("\nBUG 8  launcher shebang hardcoded to Termux")
    env = dict(os.environ)
    env["FHONE_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("PREFIX", None)
    proc = subprocess.run([sys.executable, "-m", "fhone", "install",
                           "--skip-nous", "--no-path"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT),
                          timeout=90)
    try:
        from fhone.__main__ import _launcher_shebang
    except ImportError:
        _launcher_shebang = lambda: "#!/data/data/com.termux/files/usr/bin/sh"
    shebang = _launcher_shebang()
    check("shebang is not a hardcoded Termux path",
          "com.termux" not in shebang or Path(shebang[2:]).exists(),
          shebang)
    check("shebang interpreter exists", Path(shebang[2:].strip()).exists(), shebang)

    # ---------------------------------------------------------------- BUG 9
    # the provider client cache keyed only on (id, key), so editing a
    # provider's base_url kept using the old connection.
    print("\nBUG 9  stale client after a provider URL change")
    pool.add_provider("swap", "http://127.0.0.1:1/v1", api_style="openai", priority=1)
    pool.add_key("swap", "k")
    c1 = pool._client_for(store.provider_get("swap"), "k")
    port1 = c1.port
    pool.add_provider("swap", base, api_style="openai", priority=1)
    c2 = pool._client_for(store.provider_get("swap"), "k")
    check("client rebuilt after the URL changes", c2.port != port1,
          "old port %s, new port %s" % (port1, c2.port))

    # --------------------------------------------------------------- BUG 10
    # streaming never captured usage, so every streamed turn recorded 0 tokens.
    print("\nBUG 10 streaming usage is never recorded")
    payload = {"model": "test-plain", "messages": [{"role": "user", "content": "hi"}],
               "stream": True}
    sent = {}

    class Capture(llm.Client):
        def _post(self, path, payload, *, accept_gzip):
            sent.update(payload)
            return super()._post(path, payload, accept_gzip=accept_gzip)

    c3 = Capture(base, "k", timeout=20)
    c3.chat([{"role": "user", "content": "hi"}], model="test-plain", stream=True)
    check("streaming request asks for usage",
          bool((sent.get("stream_options") or {}).get("include_usage")),
          json.dumps(sent.get("stream_options")))
    c3.close()

    # --------------------------------------------------------------- BUG 11
    # the Nous promotion step sent empty user_code/device_code, so the claim
    # could never work — it failed silently instead of saying so.
    print("\nBUG 11 Nous promotion sent empty device codes")
    from fhone import auth_nous
    fake_auth = {"anon_token": "anon_x", "auth_method": "anonymous"}
    captured = {}

    def fake_intent(cfg_, token, user_code, device_code):
        captured["user_code"] = user_code
        captured["device_code"] = device_code
        raise auth_nous.NousError("stop here for the test")

    orig = auth_nous.promotion_intent
    auth_nous.promotion_intent = fake_intent
    try:
        auth_nous._try_promotion(cfg, dict(fake_auth), ui.QuietUI(), 1)
    finally:
        auth_nous.promotion_intent = orig
    check("promotion is skipped when device codes are missing",
          "user_code" not in captured,
          "called with user_code=%r" % captured.get("user_code"))

    # ------------------------------------------------------------------ done
    httpd.shutdown()
    store.close()
    # --- BUG 12 a global model override must not reach a provider that does not
    #     serve that model. The override made the keyless fallback 404 on every
    #     call because it was handed an OpenRouter model id.
    print("\nBUG 12 a provider that does not serve the model is never asked")
    home2 = Path(tempfile.mkdtemp(prefix="fhone-route-")) / "fh"
    home2.mkdir(parents=True)
    os.environ["FHONE_HOME"] = str(home2)
    from fhone import config, providers as providers_mod
    cfg2 = config_mod.load()
    cfg2["model"] = "meta/llama"
    config_mod.save(cfg2)
    st = store_mod.Store(cfg2["db"])
    pool = providers_mod.ProviderPool(st, cfg2)
    pool.add_provider("or", base, api_style="openai", priority=1)
    pool.add_key("or", "k")
    pool.add_provider("pollinations", "https://text.pollinations.ai/openai",
                      api_style="openai", priority=2, models=["meta/llama"])
    check("an unrestricted provider serves any model",
          pool._supports(st.provider_get("or"), "anything") is True)
    check("a restricted provider serves its own model",
          pool._supports(st.provider_get("pollinations"), "meta/llama") is True)
    check("a restricted provider refuses a model it does not serve",
          pool._supports(st.provider_get("pollinations"), "some-openrouter-model") is False)
    st.close()
    os.environ["FHONE_HOME"] = str(home)

    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\n%s" % ("=" * 66))
    print("%d/%d passed" % (passed, total))
    if passed != total:
        print("\nstill failing (these are the bugs to fix):")
        for label, ok in RESULTS:
            if not ok:
                print("  - %s" % label)
    print("=" * 66)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
