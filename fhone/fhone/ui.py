"""Terminal I/O: streaming output, a readline REPL, slash commands, status bar.

Kept separate from the agent so the agent can be driven headlessly (one-shot
mode, subagents, tests) with a no-op UI.
"""
from __future__ import annotations

import os
import sys

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
    /search <query>     search all past messages
    /export [path]      dump the session as JSON
    /prune <days>       delete messages older than N days
  config
    /model [name]       show or set the model
    /provider [list|add|remove]   manage providers
    /keys [provider]    key pool health
    /config             show effective config (secrets masked)
  tools
    /tools              list available tools
    /skills [query]     list indexed skills
    /skill <name>       load a skill body
    /todo [list|add ...]  task list
    /jobs               scheduled jobs
  info
    /status             full status: providers, keys, usage, context
    /usage              token and request usage
    /context            context window fill
    /memory [query]     search or list memories
    /remember <text>    save a memory
    /forget <id>        delete a memory
    /team               recent notes from all sessions
  control
    /yolo               toggle shell approval for this session
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
        try:
            return input("\n%s %s " % (label, c(">", BOLD)))
        except EOFError:
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
    store, pool, ui, cfg = state["store"], state["pool"], state["ui"], state["cfg"]
    health = pool.health()
    ui.note(c("FHONE status", BOLD))
    ui.note("  session   %s" % state["session"])
    ui.note("  model     %s" % (cfg.get("model") or "(unset)"))
    ui.note("  providers:")
    for p in health["providers"]:
        ui.note("    %-14s %-24s %s" % (p["id"], p["state"], p["label"]))
    ui.note("  keys      %d total, %d ok, %d cooling, %d dead" % (
        health["total"], health["ok"], health["cooldown"], health["dead"]))
    total = store.usage_total()
    ui.note("  usage     requests=%s prompt=%s completion=%s" % (
        total["r"] or 0, total["p"] or 0, total["c"] or 0))
    ui.note("  messages  %d in this session" % store.count(state["session"]))
    ui.note("  memories  %d" % store.memory_count())
    ui.note("  home      %s" % cfg["home"])
