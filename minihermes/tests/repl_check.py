"""Exercise the interactive REPL path: slash commands and a streamed turn."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import fake_server  # noqa: E402


def main() -> int:
    httpd, base = fake_server.serve(0)
    tmp = tempfile.mkdtemp(prefix="minihermes-repl-")
    home = Path(tmp) / ".minihermes"
    home.mkdir(parents=True)
    (home / "config.json").write_text(json.dumps({
        "api_base": base, "model": "test-model",
        "approve_shell": False, "sandbox_roots": [tmp],
    }))
    env = dict(os.environ)
    env["MINIHERMES_HOME"] = str(home)
    env["MINIHERMES_API_KEY"] = "k"
    env["PYTHONPATH"] = str(ROOT)

    script = (
        "/tools\n"
        "please echo hi\n"
        "/remember the user likes terse replies\n"
        "/memory terse\n"
        "/help\n"
        "/exit\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "minihermes"],
        input=script, capture_output=True, text=True, env=env,
        cwd=str(ROOT), timeout=60,
    )
    print(proc.stdout)
    if proc.stderr.strip():
        print("--- stderr ---")
        print(proc.stderr[-600:])

    out = proc.stdout
    ok = (
        "run_shell" in out            # /tools listed the registry
        and "-> run_shell" in out     # tool was invoked
        and "exit=0" in out           # tool output shown
        and "saved" in out            # /remember worked
        and "terse replies" in out    # /memory recalled it
        and "commands" in out         # /help rendered
    )
    httpd.shutdown()
    print("REPL CHECK: %s" % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
