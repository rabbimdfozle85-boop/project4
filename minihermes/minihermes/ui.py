"""Terminal I/O: streaming output, a readline REPL, and slash commands.

Kept separate from the agent so the agent can be driven headlessly (tests,
one-shot mode) with a no-op UI.

Upgraded to match the Hermes CLI experience: status bar, full session
management, usage tracking, context display, shell passthrough, and a
complete slash command set.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

try:
    import readline  # noqa: F401  (enables history/editing in input())
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False


# -- ANSI helpers ----------------------------------------------------------
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


def _c(text: str, *codes: str) -> str:
    if not codes:
        return text
    return "".join(codes) + text + C.RESET


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM") not in ("dumb", None)


_USE_COLOR = _supports_color()


def colored(text: str, *codes: str) -> str:
    if _USE_COLOR:
        return _c(text, *codes)
    return text


def strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


class UI:
    def __init__(self, stream=None) -> None:
        self.out = stream or sys.stdout
        self._streaming = False
        self._last_status = ""

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
        line = "  -> %s %s" % (colored(name, C.CYAN, C.BOLD), colored(arguments, C.GRAY))
        self.note(line)

    # -- status bar --------------------------------------------------------
    def status_bar(self, sid: str, cfg: dict, agent=None) -> None:
        """Single-line status bar: model · tokens · context · session."""
        parts = []
        model = cfg.get("model", "?")
        parts.append(colored(model, C.GREEN))
        if agent:
            parts.append(colored(agent.usage_display(), C.GRAY))
            fill = agent.context_fill
            fill_color = C.GREEN if fill < 50 else (C.YELLOW if fill < 80 else C.RED)
            parts.append(colored("context: %.0f%%" % fill, fill_color))
        parts.append(colored(sid[:16], C.GRAY))
        bar = "  ".join(parts)
        self.out.write("\r\033[K" + colored("  ", C.DIM) + bar + "\r")
        self.out.flush()
        self._last_status = bar

    def clear_status(self) -> None:
        if self._last_status:
            self.out.write("\r\033[K")
            self.out.flush()
            self._last_status = ""

    # -- banner ------------------------------------------------------------
    def banner(self, version: str, model: str) -> None:
        self.end_stream()
        b = colored("  mini-hermes %s" % version, C.BOLD, C.CYAN)
        self.note(b)
        self.note(colored("  model %s" % model, C.GRAY))
        self.note(colored("  type /help for commands, /exit to quit", C.GRAY))
        self.note("")

    # -- input -------------------------------------------------------------
    def confirm(self, prompt: str) -> bool:
        self.end_stream()
        try:
            answer = input("  %s [y/N] " % prompt)
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")

    def prompt(self, sid: str) -> str:
        try:
            return input("\n%s> " % colored(sid[:12], C.BOLD, C.BLUE))
        except EOFError:
            return "/exit"

    # -- shell passthrough -------------------------------------------------
    def run_shell(self, command: str, cfg: dict) -> str:
        """Run a shell command from ! passthrough. Returns output string."""
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=int(cfg.get("shell_timeout", 30)),
                cwd=str(os.path.expanduser("~")),
            )
        except subprocess.TimeoutExpired:
            return "timeout after %ss" % cfg.get("shell_timeout", 30)
        out = (proc.stdout or "") + (proc.stderr or "")
        cap = int(cfg.get("tool_output_cap", 6000))
        if len(out) > cap:
            out = out[:cap] + "\n...[truncated %d chars]" % (len(out) - cap)
        return ("exit=%d\n%s" % (proc.returncode, out)) if out else "exit=%d" % proc.returncode


class QuietUI(UI):
    """No-op output, for one-shot mode: only the final answer is printed.

    confirm() denies shell commands, because a non-interactive run has nobody
    to approve them. Pass --yolo if you genuinely want shell in a script.
    """

    def delta(self, piece: str) -> None:
        pass

    def end_stream(self) -> None:
        pass

    def note(self, text: str) -> None:
        pass

    def tool(self, name: str, arguments: str) -> None:
        pass

    def status_bar(self, sid: str, cfg: dict, agent=None) -> None:
        pass

    def clear_status(self) -> None:
        pass

    def banner(self, version: str, model: str) -> None:
        pass

    def run_shell(self, command: str, cfg: dict) -> str:
        return "shell disabled in quiet mode"

    def confirm(self, prompt: str) -> bool:
        return False


HELP = """
commands
  Session    /new [name]        start a fresh session
             /resume <id>       resume a session by id
             /sessions          list recent sessions
             /search <query>    search sessions
             /rename <id> <new> rename a session
             /delete <id>       delete a session
             /export <id>       show full session transcript
             /prune [keep=20]   delete old sessions
  Config     /model [name]      show or set the model
             /config            show effective config
             /yolo              toggle shell approval skip
  Tools      /tools             list available tools
  Info       /status            full session status
             /usage             token usage this session
             /context           context window info
             /memory <query>    search saved memories
             /remember <text>   save a memory
             /help              this text
  Control    /stop              stop current turn (not yet)
             /retry             retry last turn
             /exit              quit
  Shell      !<command>         run a shell command directly
anything else is sent to the model.
""".strip()


def handle_slash(line: str, state: dict, store, ui: UI, agent=None) -> bool:
    """Return True if `line` was a slash command (and was handled)."""
    if not line.startswith("/"):
        return False
    parts = line[1:].split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("exit", "quit", "q"):
        raise SystemExit(0)
    if cmd == "help":
        ui.note(HELP)
    elif cmd == "new":
        state["session"] = arg or state["new_session_id"]()
        store.ensure_session(state["session"], state["cfg"]["model"])
        ui.note("new session: %s" % state["session"])
    elif cmd == "resume":
        if not arg:
            ui.note("usage: /resume <session-id>")
        else:
            sid = arg
            info = store.session_info(sid)
            if info:
                state["session"] = sid
                ui.note("resumed: %s" % sid)
            else:
                ui.note("session not found: %s" % sid)
    elif cmd == "sessions":
        rows = store.sessions()
        if not rows:
            ui.note("no sessions yet")
        for r in rows:
            ui.note("  %s  %s" % (r["id"][:30], r["title"] or ""))
    elif cmd == "search":
        if not arg:
            ui.note("usage: /search <query>")
        else:
            rows = store.search_sessions(arg)
            if not rows:
                ui.note("no sessions matched")
            for r in rows:
                ui.note("  %s  %s" % (r["id"][:30], r["title"] or ""))
    elif cmd == "rename":
        tokens = arg.split(None, 1)
        if len(tokens) < 2:
            ui.note("usage: /rename <old-id> <new-id>")
        else:
            old, new = tokens
            if store.rename_session(old, new):
                if state.get("session") == old:
                    state["session"] = new
                ui.note("renamed: %s -> %s" % (old, new))
            else:
                ui.note("session not found: %s" % old)
    elif cmd == "delete":
        if not arg:
            ui.note("usage: /delete <session-id>")
        else:
            if store.delete_session(arg):
                if state.get("session") == arg:
                    state["session"] = state["new_session_id"]()
                    store.ensure_session(state["session"], state["cfg"]["model"])
                ui.note("deleted: %s" % arg)
            else:
                ui.note("session not found: %s" % arg)
    elif cmd == "export":
        if not arg:
            ui.note("usage: /export <session-id>")
        else:
            text = store.export_session(arg)
            ui.note(text)
    elif cmd == "prune":
        keep = int(arg) if arg.isdigit() else 20
        deleted = store.prune_sessions(keep)
        ui.note("pruned %d session(s), kept %d" % (deleted, keep))
    elif cmd == "model":
        if arg:
            state["cfg"]["model"] = arg
            ui.note("model = %s" % arg)
        else:
            ui.note("model = %s" % (state["cfg"]["model"] or "(unset)"))
    elif cmd == "config":
        for k in sorted(state["cfg"]):
            if k == "api_key":
                v = "(set)" if state["cfg"][k] else "(MISSING)"
            elif k == "sandbox_roots":
                v = ", ".join(state["cfg"][k]) if state["cfg"][k] else "(none)"
            else:
                v = state["cfg"][k]
            ui.note("  %-22s %s" % (k, v))
    elif cmd == "yolo":
        state["cfg"]["_yolo"] = not state["cfg"].get("_yolo", False)
        mode = "ON (shell commands auto-approved)" if state["cfg"]["_yolo"] else "OFF (shell commands require approval)"
        ui.note("yolo mode: %s" % mode)
    elif cmd == "tools":
        ui.note("  " + ", ".join(state["tool_names"]))
    elif cmd == "status":
        sid = state["session"]
        info = store.session_info(sid)
        if not info:
            ui.note("session not found: %s" % sid)
        else:
            ui.note("  session:   %s" % info["id"])
            ui.note("  title:     %s" % (info["title"] or "(none)"))
            ui.note("  model:     %s" % (info["model"] or "(none)"))
            ui.note("  messages:  %d" % info["message_count"])
            ui.note("  updated:   %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info["updated_at"])))
            if agent:
                ui.note("  usage:     %s" % agent.usage_display())
                ui.note("  context:   %.0f%% full" % agent.context_fill)
    elif cmd == "usage":
        if agent:
            ui.note("  usage: %s" % agent.usage_display())
            ui.note("  context: %.0f%% of budget" % agent.context_fill)
        else:
            ui.note("  no agent available")
    elif cmd == "context":
        cfg = state["cfg"]
        sid = state["session"]
        from . import context as ctx
        rows = store.window(sid, int(cfg.get("context_window", 12)))
        total_tokens = sum(ctx.row_tokens(r) for r in rows)
        budget = int(cfg.get("context_token_budget", 6000))
        window = int(cfg.get("context_window", 12))
        fill = (total_tokens / budget * 100) if budget else 0
        ui.note("  window size:    %d messages" % window)
        ui.note("  token budget:   %d" % budget)
        ui.note("  current tokens: ~%d" % total_tokens)
        ui.note("  fill:           %.0f%%" % min(100, fill))
        ui.note("  messages:       %d in window" % len(rows))
    elif cmd == "memory":
        rows = store.memory_search(arg) if arg else []
        if not rows:
            ui.note("no memories matched")
        for r in rows:
            ui.note("- %s" % r["content"])
    elif cmd == "remember":
        if arg:
            store.memory_add(arg)
            ui.note("saved")
        else:
            ui.note("usage: /remember <text>")
    elif cmd == "stop":
        ui.note("stop: not yet supported")
    elif cmd == "retry":
        ui.note("retry: send your message again")
    else:
        ui.note("unknown command: /%s  (try /help)" % cmd)
    return True


def handle_bang(line: str, state: dict, ui: UI) -> bool:
    """Handle !<command> shell passthrough. Returns True if handled."""
    if not line.startswith("!"):
        return False
    cmd = line[1:].strip()
    if not cmd:
        return False
    output = ui.run_shell(cmd, state["cfg"])
    ui.note(output)
    return True
