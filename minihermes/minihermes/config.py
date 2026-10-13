"""Configuration for Mini-Hermes.

One JSON file under MINIHERMES_HOME (default ~/.minihermes). The API key is the
only secret: it is read from the environment first, and only falls back to the
config file (written 0600) so a phone user does not have to export a variable
in every new shell.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("MINIHERMES_HOME") or (Path.home() / ".minihermes"))


def config_path() -> Path:
    return home() / "config.json"


def db_path() -> Path:
    return home() / "minihermes.db"


DEFAULTS: dict = {
    "api_base": "https://inference-api.nousresearch.com/v1",
    "model": "",
    "max_tokens": 1024,
    "temperature": 0.7,
    "request_timeout": 120,
    "max_retries": 4,
    # Context discipline. These two knobs are what keep RSS flat over a long
    # session on a device with 2.8 GB of RAM and an already-busy swapfile.
    "context_window": 12,
    "context_token_budget": 6000,
    "max_tool_rounds": 8,
    # Caps: an unbounded `cat bigfile` would blow both the context and RAM.
    "tool_output_cap": 6000,
    "shell_timeout": 30,
    "approve_shell": True,
    "sandbox_roots": [],
    "system_prompt": (
        "You are Mini-Hermes, a terse personal agent running on a phone. "
        "Use tools when they genuinely help. Keep answers short. "
        "Never invent file contents or command output."
    ),
}


def load() -> dict:
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    if not cfg.get("sandbox_roots"):
        cfg["sandbox_roots"] = [str(Path.home())]
    cfg["api_key"] = (
        os.environ.get("MINIHERMES_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or cfg.get("api_key")
        or ""
    )
    cfg["model"] = cfg.get("model") or os.environ.get("MINIHERMES_MODEL", "")
    cfg["home"] = str(h)
    cfg["db"] = str(db_path())
    return cfg


def save(cfg: dict) -> Path:
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in cfg.items() if k not in ("home", "db", "_yolo")}
    p = config_path()
    p.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    try:
        p.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600: it may hold the API key
    except OSError:
        pass
    return p
