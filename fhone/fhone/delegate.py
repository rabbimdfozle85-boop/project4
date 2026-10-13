"""Subagent delegation.

Each subagent is a separate process (~28 MB), so the cap is enforced here in the
parent rather than asked for in the prompt — a model told politely to limit
itself will not.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _lockdir(cfg: dict) -> Path:
    d = Path(cfg["home"]) / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def running(cfg: dict) -> int:
    """Count live subagents, reaping stale lock files."""
    d = _lockdir(cfg)
    live = 0
    for f in list(d.iterdir()):
        try:
            pid = int(f.read_text().strip())
        except (OSError, ValueError):
            pid = 0
        if pid and _alive(pid):
            live += 1
            continue
        try:
            f.unlink()
        except OSError:
            pass
    return live


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_subagent(cfg: dict, goal: str, context: str = "", timeout: int = 300) -> str:
    cap = int(cfg.get("max_subagents", 3))
    if running(cfg) >= cap:
        return ("error: subagent_limit: %d concurrent subagents already running"
                % cap)

    prompt = goal if not context else "%s\n\nContext:\n%s" % (goal, context)
    env = dict(os.environ)
    env["FHONE_HOME"] = cfg["home"]
    env["FHONE_SUBAGENT"] = "1"
    # Subagents don't need the parent's API key in env — they use config
    env.pop("FHONE_API_KEY", None)

    lock = _lockdir(cfg) / ("%d-%d.lock" % (os.getpid(), int(time.time() * 1000)))
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "fhone", "-q", prompt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            cwd=str(Path.home()))
        lock.write_text(str(proc.pid), encoding="utf-8")
        try:
            out, err = proc.communicate(timeout=max(30, int(timeout)))
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return "error: timeout: subagent exceeded %ss\n%s" % (timeout, (out or "")[:2000])
    except Exception as e:
        return "error: spawn_failed: %s" % e
    finally:
        try:
            lock.unlink()
        except OSError:
            pass

    text = (out or "").strip()
    if proc.returncode and not text:
        text = "error: subagent exited %d\n%s" % (proc.returncode, (err or "")[-1500:])
    return text or "(subagent produced no output)"
