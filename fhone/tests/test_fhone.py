"""FHONE test suite. Offline, no API key, no credits burned.

Run:  python3 tests/test_fhone.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import fake_server  # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
RESULTS: list = []


def check(label: str, ok: bool, detail=None) -> None:
    RESULTS.append((label, bool(ok)))
    print("  %s  %-52s %s" % (PASS if ok else FAIL, label,
                              ("-- " + str(detail)[:70]) if detail else ""))


def section(name: str) -> None:
    print("\n%s" % name)


def fresh_home() -> Path:
    d = Path(tempfile.mkdtemp(prefix="fhone-test-")) / "fh"
    d.mkdir(parents=True)
    os.environ["FHONE_HOME"] = str(d)
    return d


def main() -> int:
    from fhone import config, context, llm, providers, scheduler, skills, tools
    from fhone import backup as backup_mod
    from fhone.store import Store

    httpd, base = fake_server.serve(0)
    home = fresh_home()
    cfg = config.load()
    cfg["approve_shell"] = False
    cfg["sandbox_roots"] = [str(home.parent)]
    print("FHONE test suite")
    print("  home: %s" % home)
    print("  fake llm: %s" % base)

    # ---------------------------------------------------------------- store
    section("store")
    store = Store(cfg["db"])
    check("database created", Path(cfg["db"]).exists())
    check("integrity check passes", store.integrity_check())
    check("schema_version recorded", store.get_meta("schema_version") == "3")

    store.ensure_session("s1", "test-model")
    store.add("s1", "user", "hello world")
    store.add("s1", "assistant", "hi there")
    check("session row written", store.session_count() == 1)
    check("messages counted", store.count("s1") == 2)
    check("window returns newest last",
          [r["role"] for r in store.window("s1", 10)] == ["user", "assistant"])
    check("FTS5 message search", len(store.search_messages("hello")) == 1)
    check("search degrades on punctuation",
          len(store.search_messages('what"s the "deal"?')) >= 0)

    mid = store.memory_add("the user prefers terse replies", "pref")
    store.memory_add("device is a realme C25s", "env")
    check("memory FTS5 recall", len(store.memory_search("terse")) == 1)
    check("memory count", store.memory_count() == 2)
    check("memory forget", store.memory_forget(mid) and store.memory_count() == 1)

    store.note_add("a team finding", session_id="s1", agent_id="main")
    check("team feed", len(store.team_feed()) == 1)
    store.agent_send("worker1", "please check the log")
    check("agent inbox", len(store.agent_inbox("worker1")) == 1)
    check("inbox marks read", len(store.agent_inbox("worker1")) == 0)

    tid = store.todo_add("buy milk")
    check("todo add/list", len(store.todo_list()) == 1)
    check("todo complete", store.todo_complete(tid))

    # ------------------------------------------------------------- providers
    section("providers")
    pool = providers.ProviderPool(store, cfg)
    pool.seed_defaults()
    check("no default providers pre-loaded (clean start)", len(store.providers()) == 0)
    pool.add_provider("fake", base, label="fake", api_style="openai", priority=1)
    k1 = pool.add_key("fake", "key-one", "one")
    k2 = pool.add_key("fake", "key-two", "two")
    check("keys added", len(store.keys_for("fake")) == 2)
    check("health reports providers", pool.health()["total"] >= 2)

    cfg["model"] = "test-model"
    result = pool.chat([{"role": "user", "content": "hi"}], stream=False)
    check("pool.chat returns a result", bool(result.get("content")))
    check("pool tags the provider", result.get("_provider") == "fake")
    usage = store.usage_total()
    check("usage recorded", (usage["r"] or 0) >= 1)

    # rotation: 429 must cool the key and move on
    before = dict(fake_server.HITS)
    cfg["model"] = "test-429"
    try:
        pool.chat([{"role": "user", "content": "hi"}], stream=False)
        rotated = True
    except providers.PoolError:
        rotated = True          # exhausted is also acceptable here
    cooled = [k for k in store.keys_for("fake") if k["status"] == "cooldown"]
    check("429 cools keys instead of crashing", len(cooled) >= 1,
          "%d cooling" % len(cooled))

    # auth failure kills the key (reset first: the 429 test cooled both)
    store.key_set_status(k1, "ok")
    store.key_set_status(k2, "ok")
    cfg["model"] = "test-401"
    try:
        pool.chat([{"role": "user", "content": "hi"}], stream=False)
    except providers.PoolError:
        pass
    dead = [k for k in store.keys_for("fake") if k["status"] == "dead"]
    check("401 marks the key dead", len(dead) >= 1, "%d dead" % len(dead))

    # reset keys and prove the client retries a 500 internally
    store.key_set_status(k1, "ok")
    store.key_set_status(k2, "ok")
    cfg["model"] = "test-500"
    cfg["max_retries"] = 2
    start = fake_server.HITS.get("test-500", 0)
    try:
        pool.chat([{"role": "user", "content": "hi"}], stream=False)
    except providers.PoolError:
        pass
    check("500 is retried before giving up",
          fake_server.HITS.get("test-500", 0) - start >= 2,
          "%d attempts" % (fake_server.HITS.get("test-500", 0) - start))
    cfg["max_retries"] = 4

    # ------------------------------------------------------------------ llm
    section("llm")
    client = llm.Client(base, "k", timeout=20, max_retries=2)
    res = client.chat([{"role": "user", "content": "hi"}], model="test-plain",
                      stream=False)
    check("non-streaming parse", res["content"] == "plain answer")
    check("usage surfaced", (res.get("usage") or {}).get("prompt_tokens") == 11)

    parts = []
    res = client.chat([{"role": "user", "content": "hi"}], model="test-plain",
                      stream=True, on_delta=parts.append)
    check("streaming deltas delivered", "".join(parts) == "plain answer")

    res = client.chat([{"role": "user", "content": "hi"}], model="test-split",
                      stream=True)
    args = res["tool_calls"][0]["function"]["arguments"]
    check("split tool arguments reassembled", args == '{"command": "echo split-ok"}',
          args[:50])
    check("tool call name parsed", res["tool_calls"][0]["function"]["name"] == "terminal")
    check("browser-like User-Agent sent", "Mozilla" in fake_server.HITS.get("_last_ua", ""))
    client.close()

    # -------------------------------------------------------------- context
    section("context")
    store.ensure_session("s2", "test-model")
    for i in range(30):
        store.add("s2", "user", "message number %d" % i)
        store.add("s2", "assistant", "reply number %d" % i)
    built = context.build(store, "s2", cfg)
    window = int(cfg["context_window"])
    check("window bounds the message list", len(built) <= window + 3,
          "%d built" % len(built))
    check("system prompt first", built[0]["role"] == "system")
    check("never starts on an orphan tool result",
          all(m["role"] != "tool" for m in built[1:2]))
    check("summarize triggers past the threshold", context.needs_summary(store, "s2", cfg))
    check("approx token estimate sane", context.approx_tokens("abcd") == 1)

    # ---------------------------------------------------------------- tools
    section("tools")
    t = tools.Tools(cfg, store, None, pool)
    check("45 tools registered", len(t.names()) == 45, "%d" % len(t.names()))
    check("every tool has a schema",
          all(s.get("function", {}).get("name") for s in t.schemas()))

    target = home.parent / "sample.txt"
    check("write_file", "wrote" in t.dispatch("write_file",
                                              {"path": str(target), "content": "alpha\nbeta\n"}))
    check("read_file", "alpha" in t.dispatch("read_file", {"path": str(target)}))
    check("read_file offset/limit", "[lines 2-2" in t.dispatch(
        "read_file", {"path": str(target), "offset": 2, "limit": 1}))
    check("patch applies", "patched" in t.dispatch(
        "patch", {"path": str(target), "old": "beta", "new": "gamma"}))
    check("patch refuses a missing old and does not corrupt",
          "not_found" in t.dispatch("patch", {"path": str(target),
                                              "old": "NOPE", "new": "x"})
          and "gamma" in target.read_text())
    check("list_dir", "sample.txt" in t.dispatch("list_dir",
                                                 {"path": str(home.parent)}))
    check("search_files", "gamma" in t.dispatch(
        "search_files", {"pattern": "gamma", "path": str(home.parent)}))

    check("sandbox refuses an escape",
          "outside_sandbox" in t.dispatch("read_file", {"path": "/etc/passwd"}))
    check("sandbox refuses traversal",
          "outside_sandbox" in t.dispatch("read_file",
                                          {"path": str(home.parent / ".." / ".." / "etc" / "passwd")}))

    out = t.dispatch("terminal", {"command": "echo hi"})
    check("terminal runs", "exit=0" in out and "hi" in out)
    out = t.dispatch("terminal", {"command": "exit 3"})
    check("terminal reports exit codes", "exit=3" in out)
    out = t.dispatch("terminal", {"command": "sleep 5", "timeout": 1})
    check("terminal times out", "timeout" in out)
    out = t.dispatch("terminal", {"command": "python3 -c 'print(\"z\"*20000)'"})
    check("tool output is capped", "truncated" in out, "%d chars" % len(out))
    check("execute_code", "42" in t.dispatch("execute_code", {"code": "print(42)"}))
    check("execute_code timeout", "timeout" in t.dispatch(
        "execute_code", {"code": "import time; time.sleep(5)", "timeout": 1}))

    check("unknown tool is an error not a crash",
          "unknown_tool" in t.dispatch("nope", {}))
    check("bad JSON arguments are an error",
          "bad_arguments" in t.dispatch("read_file", "{not json"))
    check("bad argument names are an error",
          "bad_arguments" in t.dispatch("read_file", {"wrong": "x"}))

    check("memory tool add", "saved" in t.dispatch("memory", {"action": "add",
                                                              "content": "likes dark mode"}))
    check("memory tool search", "dark" in t.dispatch("memory", {"action": "search",
                                                                "query": "dark"}))
    t.dispatch("todo_list", {"action": "add", "text": "call bob"})
    check("todo tool", "call bob" in t.dispatch("todo_list", {"action": "list"}))
    check("tts degrades cleanly without termux-api",
          "termux_api_missing" in t.dispatch("text_to_speech", {"text": "hi"})
          or "spoken" in t.dispatch("text_to_speech", {"text": "hi"}))
    check("vision reports a missing provider",
          "no_vision_provider" in t.dispatch("vision_analyze",
                                             {"image": "x", "question": "y"}))
    check("clarify without a channel is an error",
          "no_interactive_channel" in t.dispatch("clarify", {"question": "?"}))

    # --------------------------------------------------------------- skills
    section("skills")
    sdir = Path(cfg["skills_dir"])
    (sdir / "demo").mkdir(parents=True, exist_ok=True)
    (sdir / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: a demo skill\ntier: 1\n---\nbody here\n",
        encoding="utf-8")
    idx = skills.index(cfg)
    check("skill indexed", len(idx) == 1 and idx[0]["name"] == "demo")
    check("index holds no body", idx[0]["size"] > 0 and "body" not in idx[0])
    check("list_skills", "demo" in skills.list_skills(cfg))
    check("view_skill returns the body", "body here" in skills.view_skill(cfg, "demo"))
    check("view_skill near-miss hint", "not_found" in skills.view_skill(cfg, "dema"))
    check("create requires frontmatter",
          "bad_frontmatter" in skills.manage_skill(cfg, "create", "broken", "no fm"))
    created = skills.manage_skill(
        cfg, "create", "second",
        "---\nname: second\ndescription: second skill\ntier: 3\n---\ncontent\n")
    check("create writes a skill", "created" in created)
    check("set_tier promotes", "tier" in skills.manage_skill(
        cfg, "set_tier", "second", tier=1))
    check("tier took effect",
          any(e["name"] == "second" and e["tier"] == 1 for e in skills.index(cfg)))
    check("delete removes it", "deleted" in skills.manage_skill(cfg, "delete", "second"))

    # ------------------------------------------------------------ scheduler
    section("scheduler")
    check("interval parsed", scheduler.parse_schedule("30m")["kind"] == "interval")
    check("'every 2h' parsed", scheduler.parse_schedule("every 2h")["value"] == 7200)
    check("cron parsed", scheduler.parse_schedule("0 9 * * *")["kind"] == "cron")
    check("bad schedule rejected", scheduler.parse_schedule("nonsense")["kind"] is None)
    nxt = scheduler.next_run("10m")
    check("next_run in the future", nxt > time.time() and nxt < time.time() + 700)
    out = scheduler.manage(store, "create", "5m", "echo tick")
    check("job created", "created" in out)
    check("job listed", "echo tick" in scheduler.manage(store, "list"))
    check("job paused", "paused" in scheduler.manage(store, "pause", job_id=1))
    check("job removed", "removed" in scheduler.manage(store, "remove", job_id=1))

    # --------------------------------------------------------------- backup
    section("backup")
    config.save(cfg)          # a backup only contains files that exist
    bpath = backup_mod.create(cfg, store, quick=False, label="test")
    check("backup created", Path(bpath).exists())
    man = backup_mod.inspect(bpath)
    check("manifest present", man.get("product") == "fhone")
    check("manifest records schema", man.get("schema_version") == "3")
    check("backup contains the database", "fhone.db" in man["files"])
    check("backup contains config", "config.json" in man["files"])
    quick_man = backup_mod.inspect(backup_mod.create(cfg, store, quick=True, label="q"))
    check("quick backup omits skills",
          not any(k.startswith("skills/") for k in quick_man["files"]))
    listed = backup_mod.list_backups(cfg)
    check("backups listed", len(listed) >= 2)

    # VACUUM INTO must produce a readable database
    import sqlite3
    snap = Path(tempfile.mkdtemp()) / "snap.db"
    store.vacuum_into(str(snap))
    con = sqlite3.connect(str(snap))
    n = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    con.close()
    check("VACUUM INTO snapshot is consistent and readable", n == store.count("s1") + store.count("s2"))

    # ---------------------------------------------------------------- agent
    section("agent (end to end)")
    cfg["model"] = "test-model"
    from fhone import agent as agent_mod
    from fhone.ui import QuietUI
    qui = QuietUI()
    t2 = tools.Tools(cfg, store, qui, pool)
    ag = agent_mod.Agent(cfg, store, pool, t2, qui)
    answer = ag.turn("e2e", "please echo hi")
    check("turn returns an answer", bool(answer) and "Done." in answer, answer[:50])
    rows = store.window("e2e", 50)
    check("turn persisted as user/assistant/tool/assistant",
          [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant"],
          str([r["role"] for r in rows]))
    calls = [r for r in rows if r["role"] == "assistant" and r["tool_calls"]]
    args = json.loads(calls[0]["tool_calls"])[0]["function"]["arguments"]
    check("tool arguments reassembled end to end", args == '{"command": "echo hi"}', args)

    # ---------------------------------------------------------------- backup restore
    section("restore")
    store.set_meta("restore_probe", "before")
    b2 = backup_mod.create(cfg, store, quick=True, label="restore-src")
    store.set_meta("restore_probe", "after")
    store.close()
    store2 = Store(cfg["db"])
    result = backup_mod.restore(cfg, store2, b2)
    check("restore reports success", not result.startswith("error:"), result[:60])
    store3 = Store(cfg["db"])
    check("restored state matches the snapshot",
          store3.get_meta("restore_probe") == "before",
          store3.get_meta("restore_probe"))
    check("restore made a safety copy",
          any("pre-restore" in b["path"] for b in backup_mod.list_backups(cfg)))
    store3.close()

    # ------------------------------------------------------------ cli one-shot
    section("cli")
    import subprocess
    env = dict(os.environ)
    env["FHONE_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "fhone", "-q", "please echo hi", "--yolo",
         "--model", "test-model"],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=90)
    check("one-shot exits 0", proc.returncode == 0,
          (proc.stderr or "")[-120:] if proc.returncode else "")
    check("one-shot prints only the answer",
          "Done." in proc.stdout and "->" not in proc.stdout, proc.stdout.strip()[:60])

    proc = subprocess.run([sys.executable, "-m", "fhone", "version"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))
    check("version subcommand", "FHONE" in proc.stdout)

    proc = subprocess.run([sys.executable, "-m", "fhone", "status"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))
    check("status subcommand", "FHONE" in proc.stdout and "providers" in proc.stdout)

    proc = subprocess.run([sys.executable, "-m", "fhone", "doctor"],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))
    check("doctor runs", "FHONE doctor" in proc.stdout)

    # ------------------------------------------------------------------ done
    httpd.shutdown()
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\n%s" % ("=" * 64))
    print("%d/%d passed" % (passed, total))
    if passed != total:
        print("\nfailures:")
        for label, ok in RESULTS:
            if not ok:
                print("  - %s" % label)
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
