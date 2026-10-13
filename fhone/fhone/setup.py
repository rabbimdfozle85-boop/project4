"""Interactive setup wizard.

Runs after `install`. Guides the user through provider setup, model selection,
surface toggles, and authentication. Cancel at any step returns to the terminal
prompt. Re-running setup updates existing config.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import config as config_mod
from .store import Store


PROVIDERS = [
    ("pollinations", "Pollinations", "https://text.pollinations.ai/openai",
     "openai", "Free, no key. Model: openai-fast"),
    ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
     "openai", "Free tier available. Many models."),
    ("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta",
     "gemini", "Free tier available."),
    ("nous", "Nous Research", "https://inference-api.nousresearch.com/v1",
     "openai", "Free guest tier."),
    ("custom", "Custom", "", "openai", "Enter your own URL"),
]


def _input(prompt: str, default: str = "") -> str:
    """Read a line with a default value."""
    suffix = " [%s]" % default if default else ""
    try:
        val = input("  %s%s: " % (prompt, suffix)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return val or default


def _confirm(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        val = input("  %s%s: " % (prompt, suffix)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    if not val:
        return default
    return val in ("y", "yes")


def _menu(title: str, options: list, back: bool = True) -> int:
    """Show a numbered menu. Returns the selected index, or -1 for back/cancel."""
    print()
    print("  %s" % title)
    print("  " + "-" * 40)
    for i, (key, label, *rest) in enumerate(options, 1):
        desc = rest[0] if rest else ""
        print("    %d) %s%s" % (i, label, (" — %s" % desc) if desc else ""))
    if back:
        print("    0) Back / Cancel")
    print()
    try:
        val = input("  Select: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return -1
    if not val and back:
        return -1
    if val == "0" and back:
        return -1
    try:
        idx = int(val) - 1
        if 0 <= idx < len(options):
            return idx
    except ValueError:
        pass
    return -2  # invalid, re-show


def run(cfg: dict) -> int:
    """Run the interactive setup wizard. Returns 0 on success, 1 on cancel."""
    print()
    print("=" * 50)
    print("  FHONE Setup Wizard")
    print("=" * 50)
    print()
    print("  You can cancel at any time. Your progress is saved.")
    print()

    while True:
        print()
        print("  Setup:")
        print("    1) Provider and API key")
        print("    2) Model selection")
        print("    3) Surfaces (CLI, web portal, Telegram)")
        print("    4) Authentication (Nous)")
        print("    5) Review and save")
        print("    6) Finish")
        print("    0) Cancel and exit")
        print()

        try:
            val = input("  Select: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1

        if val == "0":
            print()
            print("  Setup cancelled. Your config is saved.")
            print("  Run 'fhone setup' anytime to redo this.")
            return 1

        elif val == "1":
            _setup_provider(cfg)

        elif val == "2":
            _setup_model(cfg)

        elif val == "3":
            _setup_surfaces(cfg)

        elif val == "4":
            _setup_auth(cfg)

        elif val == "5":
            _review(cfg)

        elif val == "6":
            config_mod.save(cfg)
            print()
            print("  Setup complete. Run 'fhone' to start.")
            return 0

        else:
            print("  Invalid choice. Try again.")


def _setup_provider(cfg: dict) -> None:
    """Provider and API key setup."""
    from . import __main__ as main_mod
    from .store import Store
    from . import providers as providers_mod

    while True:
        idx = _menu("Select provider", PROVIDERS)
        if idx < 0:
            break
        pid, label, url, style, desc = PROVIDERS[idx]

        if pid == "custom":
            url = _input("  Base URL")
            style = _input("  Wire style (openai/gemini/apifreellm)", "openai")
            label = _input("  Label", "custom")
            if not url:
                print("  No URL entered. Cancelled.")
                break

        print()
        print("  Provider: %s (%s)" % (label, url))
        print("  Style: %s" % style)
        print()

        if pid == "pollinations":
            # Keyless
            print("  Pollunations is keyless — no API key needed.")
            cfg["portal_enabled"] = cfg.get("portal_enabled", True)
            store = Store(cfg["db"])
            pool = providers_mod.ProviderPool(store, cfg)
            pool.add_provider(pid, url, api_style=style, priority=10)
            pool.close()
            store.close()
            print("  Provider added: %s" % pid)
            break
        else:
            key = _input("  API key")
            if not key:
                if not _confirm("  Save without key?"):
                    continue
            store = Store(cfg["db"])
            pool = providers_mod.ProviderPool(store, cfg)
            pool.add_provider(pid, url, api_style=style, priority=10)
            if key:
                pool.add_key(pid, key, "primary")
            pool.close()
            store.close()
            print("  Provider added: %s" % pid)
            break


def _setup_model(cfg: dict) -> None:
    """Model selection."""
    print()
    current = cfg.get("model") or "(unset)"
    print("  Current model: %s" % current)
    print()
    print("  Common models:")
    models = [
        ("openai-fast", "Pollinations — free, no key"),
        ("google/gemma-4-31b-it:free", "OpenRouter — free"),
        ("meta-llama/llama-3.3-70b-instruct:free", "OpenRouter — free"),
        ("nousresearch/hermes-4-405b", "Nous — free guest"),
    ]
    for i, (mid, desc) in enumerate(models, 1):
        print("    %d) %s — %s" % (i, mid, desc))
    print("    0) Enter custom model")
    print("    00) Back")
    print()

    try:
        val = input("  Select: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if val == "1":
        cfg["model"] = "openai-fast"
    elif val == "2":
        cfg["model"] = "google/gemma-4-31b-it:free"
    elif val == "3":
        cfg["model"] = "meta-llama/llama-3.3-70b-instruct:free"
    elif val == "4":
        cfg["model"] = "nousresearch/hermes-4-405b"
    elif val == "0":
        cfg["model"] = _input("  Model name", cfg.get("model", ""))
    elif val == "00":
        return

    config_mod.save(cfg)
    print("  Model set: %s" % cfg.get("model"))


def _setup_surfaces(cfg: dict) -> None:
    """Surface toggles."""
    print()
    print("  Surfaces:")
    print("    1) CLI (terminal) — always on")
    print("    2) Web portal: %s" % ("on" if cfg.get("portal_enabled", True) else "off"))
    print("    3) Telegram: %s" % ("on" if cfg.get("telegram_enabled") else "off"))
    print("    0) Back")
    print()

    try:
        val = input("  Select: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if val == "2":
        cfg["portal_enabled"] = not cfg.get("portal_enabled", True)
        config_mod.save(cfg)
        print("  Web portal: %s" % ("on" if cfg["portal_enabled"] else "off"))
    elif val == "3":
        if cfg.get("telegram_enabled"):
            cfg["telegram_enabled"] = False
        else:
            token = _input("  Telegram bot token")
            if token:
                cfg["telegram_token"] = token
                cfg["telegram_enabled"] = True
            else:
                print("  No token. Telegram stays off.")
        config_mod.save(cfg)
        print("  Telegram: %s" % ("on" if cfg.get("telegram_enabled") else "off"))


def _setup_auth(cfg: dict) -> None:
    """Authentication setup."""
    print()
    print("  Authentication:")
    print("    Nous sign-in is optional. You can use other providers without it.")
    print()
    print("    1) Try Nous sign-in now")
    print("    2) Skip (use other providers)")
    print("    0) Back")
    print()

    try:
        val = input("  Select: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if val == "1":
        from . import auth_nous
        from .ui import QuietUI
        try:
            result = auth_nous.mint_guest(cfg)
            print("  Nous sign-in: success")
        except Exception as e:
            print("  Nous sign-in failed: %s" % e)
            print("  You can use other providers without Nous.")
    elif val == "2":
        print("  Skipped. You can run 'fhone setup' anytime.")


def _review(cfg: dict) -> None:
    """Review current configuration."""
    print()
    print("  Current configuration:")
    print("    Model:      %s" % (cfg.get("model") or "(unset)"))
    print("    Portal:     %s" % ("on" if cfg.get("portal_enabled", True) else "off"))
    print("    Telegram:   %s" % ("on" if cfg.get("telegram_enabled") else "off"))
    print("    Approve:    %s" % ("yolo" if cfg.get("_yolo") else "ask"))
    print()
    print("  Secrets are stored in config.json (0600).")
    print("  Database: %s" % cfg.get("db", ""))
