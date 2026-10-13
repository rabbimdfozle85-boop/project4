"""Terminal I/O: streaming output, a readline REPL, slash commands, status bar.

Kept separate from the agent so the agent can be driven headlessly (one-shot
mode, subagents, tests) with a no-op UI.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time as _t

try:
    import readline  # noqa: F401  enables history/editing in input()
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False

# --- ANSI ------------------------------------------------------------------
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"


def _colour_enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, colour: str) -> str:
    return ("%s%s%s" % (colour, text, RESET)) if _colour_enabled() else text


HELP = """\
FHONE commands
  session
    /new [title]        start a fresh session
    /sessions           list recent sessions
    /resume <id>        switch to a session
    /rename <title>     title the current session
    /history [n]        show last n messages (default 10)
    /search <query>     search all past messages
    /export [path]      dump the session as JSON
    /prune <days>       delete messages older than N days
    /branch [title]     branch current session into a new one
    /undo [n]           undo last n user turns (default 1)
    /retry              retry the last message
    /clear              clear screen and start fresh
  config
    /model [name]       show or set the model
    /provider [list|add|remove]   manage providers
    /keys [provider]    key pool health
    /config             show effective config (secrets masked)
    /yolo               toggle shell approval for this session
    /verbose            toggle verbose tool output
    /focus              toggle focus view (prompt + answer only)
    /timestamps         toggle timestamps on messages
    /approvals          show/set approval mode
    /reasoning          manage reasoning effort
    /fast               toggle fast mode
  tools
    /tools              list available tools
    /skills [query]     list indexed skills
    /skill <name>       load a skill body
    /todo [list|add ...]  task list
    /jobs               scheduled jobs
    /cron               manage scheduled tasks (list/add/remove)
    /memory [query]     search or list memories
    /remember <text>    save a memory
    /forget <id>        delete a memory
    /team               recent notes from all sessions
  info
    /status             full status: providers, keys, usage, context
    /usage              token and request usage
    /context            context window fill
    /battery            battery status (Termux:API)
    /whoami             show user identity
    /profile            show profile info
    /diff               show git diff (if in a git repo)
  control
    /save               export conversation (alias for /export)
    /snapshot           create a state snapshot
    /rollback           restore from a snapshot
    /steer <text>       inject a message after next tool call
    /goal <text>        set a standing goal
    /refine             review conversation and save lessons
    /review             spawn a review subagent
    /plan <text>        write an implementation plan
    /compress           compress conversation context
    /help               this text
    /exit               quit
  !<command>            run a shell command directly
"""


class UI:
    def __init__(self, stream=None) -> None:
        self.out = stream or sys.stdout
        self._streaming = False

    # -- streaming ---------------------------------------------------------
    def delta(self, piece: str) -> None:
        if not self._streaming:
            self.out.write("\n")
            self._streaming = True
        self.out.write(piece)
        self.out.flush()

    def end_stream(self) -> None:
        if self._streaming:
            self.out.write("\n")
            self.out.flush()
            self._streaming = False

    def note(self, text: str) -> None:
        self.end_stream()
        self.out.write(text + "\n")
        self.out.flush()

    def tool(self, name: str, arguments: str) -> None:
        self.note(c("  -> %s %s" % (name, arguments), DIM))

    # -- input -------------------------------------------------------------
    def confirm(self, prompt: str) -> bool:
        self.end_stream()
        try:
            answer = input("  %s [y/N] " % prompt)
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in ("y", "yes")

    def ask(self, question: str, choices=None) -> str:
        self.end_stream()
        lines = [question]
        if choices:
            for i, ch in enumerate(choices, 1):
                lines.append("  %d) %s" % (i, ch))
        try:
            return input("\n".join(lines) + "\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    def prompt(self, sid: str) -> str:
        label = c(sid[:14], CYAN)
        arrow = c("❯", BOLD, CYAN)
        line = c("─" * 50, DIM)
        try:
            return input("\n%s\n%s %s " % (line, label, arrow)).strip()
        except (EOFError, KeyboardInterrupt):
            return "/exit"

    # -- status bar --------------------------------------------------------
    def status_bar(self, state: dict) -> None:
        if not state.get("status_bar", True):
            return
        health = state.get("health") or {}
        parts = [
            state.get("provider") or "-",
            state.get("model") or "-",
            "keys %d/%d" % (health.get("ok", 0), len(health.get("providers") or [])),
        ]
        usage = state.get("usage") or {}
        if usage.get("requests"):
            parts.append("tok %s" % _short_num(
                (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)))
        ctx = state.get("context") or {}
        if ctx.get("messages"):
            parts.append("ctx %d/%d" % (ctx["messages"], ctx.get("window", 0)))
        self.note(c("  " + "  ·  ".join(parts), DIM))


class QuietUI(UI):
    """No-op output for one-shot mode: only the final answer is printed.

    confirm() denies, because a non-interactive run has nobody to approve.
    Pass --yolo if you genuinely want shell in a script.
    """

    def delta(self, piece: str) -> None:
        pass

    def end_stream(self) -> None:
        pass

    def note(self, text: str) -> None:
        pass

    def tool(self, name: str, arguments: str) -> None:
        pass

    def confirm(self, prompt: str) -> bool:
        return False

    def ask(self, question: str, choices=None) -> str:
        return ""


def _short_num(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fk" % (n / 1_000)
    return str(n)


# ---------------------------------------------------------------------------
# slash commands
# ---------------------------------------------------------------------------

def dispatch(line: str, state: dict) -> bool:
    """Handle a slash command. Returns True if the line was consumed."""
    if not line.startswith("/"):
        return False
    parts = line[1:].split(None, 1)
    cmd = (parts[0].lower() if parts else "")
    arg = parts[1].strip() if len(parts) > 1 else ""

    store = state["store"]
    pool = state["pool"]
    ui = state["ui"]
    cfg = state["cfg"]

    if cmd in ("exit", "quit", "q"):
        raise SystemExit(0)

    if cmd in ("help", "h", "?"):
        ui.note(HELP)

    elif cmd == "new":
        state["session"] = state["new_session_id"]()
        store.ensure_session(state["session"], cfg.get("model", ""))
        if arg:
            store.set_title(state["session"], arg)
        ui.note("new session: %s" % state["session"])

    elif cmd == "sessions":
        rows = store.sessions()
        if not rows:
            ui.note("no sessions yet")
        for r in rows:
            ui.note("  %-30s %s" % (r["id"][:30], r["title"] or ""))

    elif cmd == "resume":
        if not arg:
            ui.note("usage: /resume <session-id>")
        else:
            state["session"] = arg
            store.ensure_session(arg, cfg.get("model", ""))
            ui.note("resumed %s" % arg)

    elif cmd == "rename":
        if arg:
            store.set_title(state["session"], arg)
            ui.note("renamed")
        else:
            ui.note("usage: /rename <title>")

    elif cmd == "search":
        rows = store.search_messages(arg) if arg else []
        ui.note("\n".join("  %s | %s | %s" % (r["session_id"][:20], r["role"],
                                              (r["content"] or "")[:80])
                          for r in rows) or "no matches")

    elif cmd == "export":
        import json
        rows = store.window(state["session"], 10000)
        path = arg or ("%s-export.json" % state["session"])
        with open(os.path.expanduser(path), "w", encoding="utf-8") as fh:
            json.dump([dict(r) for r in rows], fh, indent=2)
        ui.note("exported %d messages to %s" % (len(rows), path))

    elif cmd == "prune":
        import time as _time
        days = int(arg) if arg.isdigit() else 90
        cutoff = int(_time.time()) - days * 86400
        n = store.prune_older_than(cutoff)
        ui.note("pruned %d messages older than %d days" % (n, days))

    elif cmd == "model":
        if arg:
            cfg["model"] = arg
            ui.note("model = %s" % arg)
        else:
            ui.note("model = %s" % (cfg.get("model") or "(unset)"))

    elif cmd == "provider":
        _provider_cmd(arg, state)

    elif cmd == "keys":
        _keys_cmd(arg, state)

    elif cmd == "config":
        for k in sorted(cfg):
            if k in ("api_key", "portal_token", "telegram_token", "nous_anon_token"):
                ui.note("  %-22s %s" % (k, "(set)" if cfg.get(k) else "(empty)"))
            elif k not in ("home", "db", "auth", "skills_dir"):
                ui.note("  %-22s %s" % (k, cfg[k]))

    elif cmd == "tools":
        ui.note("  " + ", ".join(state["tools"].names()))

    elif cmd == "skills":
        from . import skills as skills_mod
        ui.note(skills_mod.list_skills(cfg, query=arg))

    elif cmd == "skill":
        from . import skills as skills_mod
        ui.note(skills_mod.view_skill(cfg, arg) if arg else "usage: /skill <name>")

    elif cmd == "todo":
        ui.note(state["tools"].todo_list("add" if arg else "list",
                                         text=arg))

    elif cmd == "jobs":
        from . import scheduler
        ui.note(scheduler.manage(store, "list"))

    elif cmd == "status":
        _status_cmd(state)

    elif cmd == "usage":
        rows = store.usage_today()
        if not rows:
            ui.note("no usage recorded today")
        for r in rows:
            ui.note("  %-14s requests=%s prompt=%s completion=%s" % (
                r["provider_id"], r["r"], r["p"], r["c"]))

    elif cmd == "context":
        n = store.count(state["session"])
        ui.note("  session %s: %d messages stored, window %d, budget %d tokens" % (
            state["session"][:20], n, cfg["context_window"],
            cfg["context_token_budget"]))

    elif cmd == "memory":
        if arg:
            rows = store.memory_search(arg)
            ui.note("\n".join("- %s" % r["content"] for r in rows) or "no memories matched")
        else:
            rows = store.memory_list()
            ui.note("\n".join("#%d [%s] %s" % (r["id"], r["kind"], r["content"])
                              for r in rows) or "no memories yet")

    elif cmd == "remember":
        if arg:
            store.memory_add(arg, "fact")
            ui.note("saved")
        else:
            ui.note("usage: /remember <text>")

    elif cmd == "forget":
        if arg.isdigit() and store.memory_forget(int(arg)):
            ui.note("forgotten #%s" % arg)
        else:
            ui.note("usage: /forget <id>")

    elif cmd == "team":
        rows = store.team_feed()
        ui.note("\n".join("[%s] %s" % (r["agent_id"] or "-", r["content"][:100])
                          for r in rows) or "no team notes yet")

    elif cmd == "yolo":
        cfg["_yolo"] = not cfg.get("_yolo")
        ui.note("shell approval: %s" % ("DISABLED (yolo)" if cfg["_yolo"] else "enabled"))

    elif cmd == "history":
        n = int(arg) if arg.isdigit() else 10
        rows = store.window(state["session"], n)
        if not rows:
            ui.note("no messages yet")
        for r in rows:
            ts = ""
            if cfg.get("timestamps") and r.get("created_at"):
                import time as _t
                ts = " [%s]" % _t.strftime("%H:%M", _t.localtime(r["created_at"]))
            ui.note("  %s%s: %s" % (r["role"], ts, (r["content"] or "")[:120]))

    elif cmd == "clear":
        os.system("clear" if os.name != "nt" else "cls")
        state["session"] = state["new_session_id"]()
        store.ensure_session(state["session"], cfg.get("model", ""))
        ui.note("cleared — new session: %s" % state["session"])

    elif cmd == "branch":
        old = state["session"]
        new = state["new_session_id"]()
        store.ensure_session(new, cfg.get("model", ""))
        rows = store.window(old, 10000)
        for r in rows:
            store.add(new, r["role"], r["content"],
                      tool_calls=r.get("tool_calls"),
                      tool_call_id=r.get("tool_call_id"),
                      name=r.get("name"))
        if arg:
            store.set_title(new, arg)
        state["session"] = new
        ui.note("branched %s -> %s (%d messages)" % (old, new, len(rows)))

    elif cmd == "undo":
        n = int(arg) if arg.isdigit() else 1
        undone = 0
        for _ in range(n):
            last = store.last_user_message(state["session"])
            if not last:
                break
            store.delete_message(last["id"])
            undone += 1
        ui.note("undid %d turn(s)" % undone)

    elif cmd == "retry":
        last = store.last_user_message(state["session"])
        if not last:
            ui.note("nothing to retry")
        else:
            ui.note("retrying: %s" % (last["content"] or "")[:60])
            state["retry"] = last["content"]

    elif cmd == "verbose":
        cfg["verbose"] = not cfg.get("verbose", False)
        ui.note("verbose tool output: %s" % ("on" if cfg["verbose"] else "off"))

    elif cmd == "focus":
        cfg["focus"] = not cfg.get("focus", False)
        ui.note("focus mode: %s" % ("on (prompt + answer only)" if cfg["focus"] else "off"))

    elif cmd == "timestamps":
        cfg["timestamps"] = not cfg.get("timestamps", False)
        ui.note("timestamps: %s" % ("on" if cfg["timestamps"] else "off"))

    elif cmd == "approvals":
        mode = cfg.get("approve_shell", True)
        if arg in ("on", "off", "yolo", "ask"):
            mode = arg != "off"
            cfg["approve_shell"] = mode
            cfg["_yolo"] = (arg == "yolo")
            ui.note("approvals: %s" % arg)
        else:
            ui.note("approvals: %s  (usage: /approvals on|off|yolo|ask)" % (
                "yolo" if cfg.get("_yolo") else ("ask" if mode else "off")))

    elif cmd == "reasoning":
        if arg:
            cfg["reasoning"] = arg
            ui.note("reasoning = %s" % arg)
        else:
            ui.note("reasoning = %s" % (cfg.get("reasoning") or "(default)"))

    elif cmd == "fast":
        cfg["fast"] = not cfg.get("fast", False)
        ui.note("fast mode: %s" % ("on" if cfg["fast"] else "off"))

    elif cmd == "save":
        import json as _json
        rows = store.window(state["session"], 10000)
        path = arg or ("%s-export.json" % state["session"])
        with open(os.path.expanduser(path), "w", encoding="utf-8") as fh:
            _json.dump([dict(r) for r in rows], fh, indent=2)
        ui.note("saved %d messages to %s" % (len(rows), path))

    elif cmd == "snapshot":
        import json as _json, time as _t
        snap_dir = os.path.join(cfg.get("home", "."), "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap = {
            "session": state["session"],
            "model": cfg.get("model"),
            "messages": [dict(r) for r in store.window(state["session"], 10000)],
            "created_at": int(_t.time()),
        }
        path = os.path.join(snap_dir, "snap-%s.json" % int(_t.time()))
        with open(path, "w", encoding="utf-8") as fh:
            _json.dump(snap, fh, indent=2)
        ui.note("snapshot saved: %s" % path)

    elif cmd == "rollback":
        import json as _json
        snap_dir = os.path.join(cfg.get("home", "."), "snapshots")
        snaps = sorted([f for f in os.listdir(snap_dir) if f.endswith(".json")
                        ]) if os.path.isdir(snap_dir) else []
        if not snaps:
            ui.note("no snapshots found")
        elif arg:
            path = os.path.join(snap_dir, arg)
            if not os.path.exists(path):
                ui.note("no such snapshot: %s" % arg)
            else:
                with open(path, encoding="utf-8") as fh:
                    snap = _json.load(fh)
                state["session"] = snap["session"]
                store.ensure_session(snap["session"], snap.get("model") or "")
                for m in snap.get("messages", []):
                    store.add(snap["session"], m["role"], m["content"])
                ui.note("rolled back to %s (%d messages)" % (
                    snap["session"], len(snap.get("messages", []))))
        else:
            ui.note("snapshots:\n" + "\n".join("  %s" % s for s in snaps))

    elif cmd == "steer":
        if arg:
            state["steer"] = arg
            ui.note("steering after next tool call: %s" % arg[:60])
        else:
            ui.note("usage: /steer <text>")

    elif cmd == "goal":
        if arg:
            store.memory_add("Standing goal: %s" % arg, "goal")
            ui.note("goal set: %s" % arg[:60])
        else:
            rows = store.memory_search("goal")
            ui.note("\n".join("- %s" % r["content"] for r in rows) or "no goals set")

    elif cmd == "refine":
        rows = store.memory_search("lesson")
        lessons = [r["content"] for r in rows]
        if lessons:
            ui.note("lessons from memory:\n" + "\n".join("  - %s" % l for l in lessons[:10]))
        else:
            ui.note("no lessons saved yet")

    elif cmd == "review":
        ui.note("spawning review subagent...")
        state["review"] = True

    elif cmd == "plan":
        if arg:
            plans_dir = os.path.join(cfg.get("home", "."), "plans")
            os.makedirs(plans_dir, exist_ok=True)
            path = os.path.join(plans_dir, "plan-%s.md" % int(_t.time()))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# Plan\n\n%s\n" % arg)
            ui.note("plan written to %s" % path)
        else:
            ui.note("usage: /plan <description>")

    elif cmd == "compress":
        n = int(arg) if arg.isdigit() else 5
        rows = store.window(state["session"], 10000)
        if len(rows) <= n * 2:
            ui.note("nothing to compress (%d messages)" % len(rows))
        else:
            to_summarize = rows[:len(rows) - n]
            summary = "Earlier conversation (%d turns): " % len(to_summarize)
            summary += " ".join((r["content"] or "")[:100] for r in to_summarize
                               if r["role"] == "user")
            store.memory_add(summary[:2000], "summary")
            for r in to_summarize:
                store.delete_message(r["id"])
            ui.note("compressed %d messages into a summary" % len(to_summarize))

    elif cmd == "cron":
        from . import scheduler as _sched
        bits = arg.split(None, 2)
        sub = bits[0] if bits else "list"
        if sub == "list":
            ui.note(_sched.manage(store, "list"))
        elif sub == "add" and len(bits) >= 3:
            ui.note(_sched.manage(store, "add", bits[1], bits[2]))
        elif sub == "remove" and len(bits) >= 2:
            ui.note(_sched.manage(store, "remove", bits[1]))
        else:
            ui.note("usage: /cron list | add '<schedule>' '<command>' | remove <id>")

    elif cmd == "battery":
        try:
            probe = subprocess.run(["termux-battery-status"],
                                   capture_output=True, text=True, timeout=5)
            if probe.stdout.strip():
                import json as _j
                data = _j.loads(probe.stdout)
                ui.note("  battery: %d%%  %s  %s" % (
                    data.get("percentage", "?"),
                    "charging" if data.get("status") == "CHARGING" else "discharging",
                    data.get("health", "")))
            else:
                ui.note("  battery: Termux:API app not installed")
        except Exception as e:
            ui.note("  battery: unavailable (%s)" % e)

    elif cmd == "whoami":
        ui.note("  you are: %s" % (os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"))

    elif cmd == "profile":
        ui.note("  profile: default")
        ui.note("  home:    %s" % cfg.get("home", "."))
        ui.note("  db:      %s" % cfg.get("db", ""))
        ui.note("  model:   %s" % (cfg.get("model") or "(unset)"))

    elif cmd == "diff":
        import subprocess as _sp
        try:
            result = _sp.run(["git", "diff", "--stat"], capture_output=True,
                             text=True, timeout=10)
            if result.stdout.strip():
                ui.note(result.stdout)
            else:
                ui.note("no git changes")
        except Exception as e:
            ui.note("diff unavailable: %s" % e)

    else:
        ui.note("unknown command: /%s  (try /help)" % cmd)

    return True


def _provider_cmd(arg: str, state: dict) -> None:
    store, ui, cfg = state["store"], state["ui"], state["cfg"]
    bits = arg.split()
    sub = bits[0] if bits else "list"
    if sub == "list":
        for p in store.providers(enabled_only=False):
            ui.note("  %-14s %-28s %-10s prio=%d %s" % (
                p["id"], p["base_url"][:28], p["api_style"], p["priority"],
                "" if p["enabled"] else "(disabled)"))
    elif sub == "add" and len(bits) >= 3:
        pid, url = bits[1], bits[2]
        style = bits[3] if len(bits) > 3 else "openai"
        state["pool"].add_provider(pid, url, api_style=style)
        ui.note("added provider %s" % pid)
    elif sub == "remove" and len(bits) >= 2:
        store.provider_remove(bits[1])
        ui.note("removed %s" % bits[1])
    else:
        ui.note("usage: /provider list | add <id> <base_url> [style] | remove <id>")


def _keys_cmd(arg: str, state: dict) -> None:
    store, ui = state["store"], state["ui"]
    for p in store.providers(enabled_only=False):
        if arg and p["id"] != arg:
            continue
        keys = store.keys_for(p["id"])
        ui.note("  %s:" % p["id"])
        if not keys:
            ui.note("    (no keys — keyless or unconfigured)")
        for k in keys:
            ui.note("    #%d %-12s %-9s fails=%d" % (
                k["id"], k["label"] or "-", k["status"], k["fail_count"]))


def _status_cmd(state: dict) -> None:
    from . import banner
    store, pool, ui, cfg = state["store"], state["pool"], state["ui"], state["cfg"]
    health = pool.health()
    total = store.usage_total()
    body = "\n".join([
        "  session   %s" % state["session"],
        "  model     %s" % (cfg.get("model") or "(unset)"),
        "  providers:",
    ] + [
        "    %-14s %-24s %s" % (p["id"], p["state"], p["label"])
        for p in health["providers"]
    ] + [
        "  keys      %d total, %d ok, %d cooling, %d dead" % (
            health["total"], health["ok"], health["cooldown"], health["dead"]),
        "  usage     requests=%s prompt=%s completion=%s" % (
            total["r"] or 0, total["p"] or 0, total["c"] or 0),
        "  messages  %d in this session" % store.count(state["session"]),
        "  memories  %d" % store.memory_count(),
        "  home      %s" % cfg["home"],
    ])
    ui.note(banner.panel("FHONE status", body))
