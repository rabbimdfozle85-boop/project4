"""Structured menu system for FHONE.

Text-based menus with readline navigation. No curses — keeps the footprint
small. Used by /help, /provider, /config, and the setup wizard.
"""
from __future__ import annotations

import os
import sys

try:
    import readline  # noqa: F401
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
BG_BLUE = "\033[44m"


def _colour_enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, colour: str) -> str:
    return ("%s%s%s" % (colour, text, RESET)) if _colour_enabled() else text


def h1(text: str) -> str:
    """Level 1 header."""
    line = "=" * 50
    return "\n%s\n  %s\n%s" % (c(line, DIM), c(text, BOLD), c(line, DIM))


def h2(text: str) -> str:
    """Level 2 header."""
    return "\n  %s" % c(text, BOLD)


def sep() -> str:
    return "  " + c("-" * 46, DIM)


def item(num: int | str, label: str, desc: str = "") -> str:
    """A numbered menu item."""
    d = c(" — %s" % desc, DIM) if desc else ""
    return "    %s) %s%s" % (c(str(num), CYAN), label, d)


def back(label: str = "Back") -> str:
    return "    0) %s" % c(label, YELLOW)


def info(text: str) -> str:
    return "  %s %s" % (c("i", CYAN), text)


def success(text: str) -> str:
    return "  %s %s" % (c("✓", GREEN), text)


def warning(text: str) -> str:
    return "  %s %s" % (c("!", YELLOW), text)


def error(text: str) -> str:
    return "  %s %s" % (c("✗", RED), text)


def render(title: str, body: list[str]) -> str:
    """Render a complete menu screen."""
    from . import banner
    return banner.panel(title, "\n".join(body))


def prompt(label: str, default: str = "") -> str:
    """Prompt for input with a default."""
    suffix = c(" [%s]" % default, DIM) if default else ""
    try:
        val = input("  %s%s: " % (label, suffix)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return val or default


def confirm(label: str, default: bool = True) -> bool:
    suffix = c(" [Y/n]" if default else " [y/N]", DIM)
    try:
        val = input("  %s%s: " % (label, suffix)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    if not val:
        return default
    return val in ("y", "yes")


def show_menu(title: str, options: list[tuple], cancel: bool = True) -> int:
    """
    Display a menu and get the user's choice.
    
    Options: list of (key, label, description_or_None)
    Returns: the index of the selected option, or -1 for cancel.
    """
    while True:
        print()
        print(h1(title))
        print(sep())
        for i, (key, label, desc) in enumerate(options, 1):
            d = c(" — %s" % desc, DIM) if desc else ""
            print("    %s) %s%s" % (c(str(i), CYAN), label, d))
        if cancel:
            print("    0) %s" % c("Cancel", YELLOW))
        print(sep())
        
        try:
            val = input("  %s: " % c("Select", BOLD)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)
        
        if val == "0" and cancel:
            return -1
        try:
            idx = int(val) - 1
            if 0 <= idx < len(options):
                return idx
        except (ValueError, TypeError):
            pass
        
        print(warning("Invalid choice. Try again."))


def search_filter(items: list[str], query: str) -> list[str]:
    """Filter a list of strings by a query."""
    if not query:
        return items
    q = query.lower()
    return [i for i in items if q in i.lower()]


class MenuBar:
    """A horizontal menu bar (text-based)."""
    
    def __init__(self, items: list[str], active: int = 0):
        self.items = items
        self.active = active
    
    def render(self) -> str:
        parts = []
        for i, label in enumerate(self.items):
            if i == self.active:
                parts.append(c(" [%s] " % label, BG_BLUE))
            else:
                parts.append(c(" %s " % label, DIM))
        return "\n" + "".join(parts) + "\n"
    
    def next(self):
        self.active = (self.active + 1) % len(self.items)
    
    def prev(self):
        self.active = (self.active - 1) % len(self.items)
