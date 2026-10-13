"""Terminal I/O: streaming output, a readline REPL, and slash commands.

Kept separate from the agent so the agent can be driven headlessly (tests,
one-shot mode) with a no-op UI.
"""
from __future__ import annotations

import sys

try:
    import readline  # noqa: F401  (enables history/editing in input())
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False


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
        self.note("  -> %s %s" % (name, arguments))

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
            return input("\n%s> " % sid[:12])
        except EOFError:
            return "/exit"


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

    def confirm(self, prompt: str) -> bool:
        return False


HELP = """
commands
  /help              this text
  /new [name]        start a fresh session
  /sessions          list recent sessions
  /memory <query>    search saved memories
  /remember <text>   save a memory
  /tools             list available tools
  /model [name]      show or set the model
  /exit              quit
anything else is sent to the model.
""".strip()


def handle_slash(line: str, state: dict, store, ui: UI) -> bool:
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
    elif cmd == "sessions":
        rows = store.sessions()
        if not rows:
            ui.note("no sessions yet")
        for r in rows:
            ui.note("  %s  %s" % (r["id"][:24], r["title"] or ""))
    elif cmd == "memory":
        rows = store.memory_search(arg) if arg else []
        ui.note("\n".join("- " + r["content"] for r in rows) or "no memories matched")
    elif cmd == "remember":
        if arg:
            store.memory_add(arg)
            ui.note("saved")
        else:
            ui.note("usage: /remember <text>")
    elif cmd == "tools":
        ui.note("  " + ", ".join(state["tool_names"]))
    elif cmd == "model":
        if arg:
            state["cfg"]["model"] = arg
            ui.note("model = %s" % arg)
        else:
            ui.note("model = %s" % (state["cfg"]["model"] or "(unset)"))
    else:
        ui.note("unknown command: /%s  (try /help)" % cmd)
    return True
