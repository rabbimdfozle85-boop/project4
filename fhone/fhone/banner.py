"""Hermes-style banner, panels, and visual UI components."""
from __future__ import annotations

import os
import sys

# --- ANSI ------------------------------------------------------------------
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
BG_BLUE = "\033[44m"
BG_CYAN = "\033[46m"


def _colour_enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, colour: str) -> str:
    return ("%s%s%s" % (colour, text, RESET)) if _colour_enabled() else text


# --- Panels ----------------------------------------------------------------

def panel(title: str, body: str, icon: str = "☤") -> str:
    """Hermes-style panel with rounded borders."""
    lines = body.split("\n")
    width = max(len(l) for l in lines + [title]) + 4
    top = "╭─ %s %s%s" % (icon, title, "─" * max(0, width - len(title) - 5))
    bot = "╰%s" % ("─" * (width - 1))
    parts = [c(top, CYAN)]
    for line in lines:
        parts.append("│ %s%s" % (line, " " * max(0, width - len(line) - 3)))
    parts.append(c(bot, CYAN))
    return "\n".join(parts)


def header(text: str, icon: str = "☤") -> str:
    """Hermes-style header bar."""
    return c("╭─ %s %s%s" % (icon, text, "─" * max(0, 46 - len(text))), CYAN)


def footer() -> str:
    return c("╰%s" % ("─" * 47), CYAN)


def separator() -> str:
    return c("├%s" % ("─" * 47), CYAN)


def prompt_symbol() -> str:
    return c("❯", BOLD)


# --- Progress ---------------------------------------------------------------

def bar(percent: int, width: int = 20, fill: str = "█", empty: str = "░") -> str:
    """Text progress bar."""
    filled = int(width * percent / 100)
    return c(fill * filled, GREEN) + c(empty * (width - filled), DIM)


def context_bar(used: int, total: int, width: int = 30) -> str:
    """Context usage bar like Hermes."""
    pct = int(used * 100 / total) if total else 0
    color = GREEN if pct < 50 else (YELLOW if pct < 80 else RED)
    filled = int(width * pct / 100)
    return "%s%s%s %d%%" % (
        c("█" * filled, color),
        c("░" * (width - filled), DIM),
        c(" %d%%" % pct, color))


# --- Status -----------------------------------------------------------------

def status_line(model: str, tokens: int, total: int, keys_ok: int, keys_total: int,
                session: str) -> str:
    """Hermes-style status line."""
    ctx = context_bar(tokens, total) if total else "-"
    parts = [
        c("☤", CYAN),
        c(model, BOLD),
        "│",
        ctx,
        "│",
        "keys %d/%d" % (keys_ok, keys_total),
    ]
    return " ".join(parts)


def turn_header(session: str, model: str) -> str:
    """Turn header like Hermes."""
    return "\n%s  %s  %s" % (
        c("╭─", CYAN),
        c(session[:14], DIM),
        c(model, BOLD))


def response_panel(text: str) -> str:
    """Response panel like Hermes."""
    return panel("", text.strip())


# --- Tables -----------------------------------------------------------------

def table(headers: list[str], rows: list[list[str]], align: str = "left") -> str:
    """Simple text table."""
    if not rows:
        return "  (empty)"
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    sep = "  ".join("-" * w for w in widths)
    parts = ["  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    parts.append("  " + sep)
    for row in rows:
        parts.append("  " + "  ".join(
            str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(parts)


# --- Colors -----------------------------------------------------------------

class Colors:
    DIM = DIM
    BOLD = BOLD
    GREEN = GREEN
    YELLOW = YELLOW
    RED = RED
    CYAN = CYAN
    MAGENTA = MAGENTA


def color(text: str, colour: str) -> str:
    return c(text, colour)


# --- Spinner ----------------------------------------------------------------

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class Spinner:
    """Text spinner for long operations."""

    def __init__(self, label: str = "working"):
        self.label = label
        self._running = False
        self._frame = 0

    def start(self) -> None:
        self._running = True
        self._frame = 0

    def tick(self) -> str:
        frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        self._frame += 1
        return "\r  %s %s%s " % (c(frame, CYAN), self.label, c("...", DIM))

    def stop(self) -> str:
        self._running = False
        return "\r  %s %s  " % (c("✓", GREEN), self.label)
