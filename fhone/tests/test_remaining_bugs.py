"""Tests for remaining medium/low bugs from subagent review.

Medium:
- execute_code() discards stderr on success
- supervisor has no health check for background threads
- _connections list grows unbounded (fd leak)
- Store.close() not thread-safe
- context.build() mid-window orphan tool results
- streaming doesn't handle connection drop mid-stream

Low:
- process_manage list shows raw poll() value
- _fetch() reads 4x cap into memory
- No SIGHUP handler
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import fake_server  # noqa: E402


class TestExecuteCodeStderr(unittest.TestCase):
    """Medium: execute_code() must include stderr even on success."""

    def test_stderr_on_success(self):
        from fhone import tools as tools_mod
        from fhone.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "fh"
            home.mkdir(parents=True)
            cfg = {
                "db": str(home / "test.db"),
                "model": "test",
                "approve_shell": False,
                "sandbox_roots": [str(home)],
                "shell_timeout": 5,
                "tool_output_cap": 6000,
            }
            store = Store(cfg["db"])
            t = tools_mod.Tools(cfg, store)

            # Code that writes to stderr but exits 0
            result = t.dispatch("execute_code", {
                "code": "import sys; sys.stderr.write('warning'); print('ok')"
            })
            self.assertIn("warning", result,
                          "execute_code must include stderr on success")
            self.assertIn("ok", result)

            store.close()


class TestProcessManageList(unittest.TestCase):
    """Low: process_manage list must show human-readable status."""

    def test_list_human_readable(self):
        from fhone import tools as tools_mod
        from fhone.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "fh"
            home.mkdir(parents=True)
            cfg = {
                "db": str(home / "test.db"),
                "model": "test",
                "approve_shell": False,
                "sandbox_roots": [str(home)],
                "shell_timeout": 5,
                "tool_output_cap": 6000,
            }
            store = Store(cfg["db"])
            t = tools_mod.Tools(cfg, store)

            # Start a background process
            t.dispatch("terminal", {"command": "sleep 0.1", "background": True})
            time.sleep(0.2)  # Let it finish

            result = t.dispatch("process_manage", {"action": "list"})
            # Should show "exit=" or "running", not "None" or "0"
            self.assertNotIn("None", result,
                             "process_manage list should not show raw None")

            store.close()


class TestStoreCloseThreadSafety(unittest.TestCase):
    """Medium: Store.close() must not kill other threads' connections."""

    def test_close_other_thread_safe(self):
        from fhone.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(str(Path(tmp) / "test.db"))

            errors = []

            def worker():
                try:
                    for _ in range(50):
                        store.con.execute("SELECT 1").fetchone()
                except Exception as e:
                    errors.append(e)

            th = threading.Thread(target=worker)
            th.start()
            time.sleep(0.01)
            store.close()
            th.join()

            # Worker should not have gotten "closed database" errors
            self.assertEqual(errors, [],
                             "Store.close() killed another thread's connection")


class TestContextMidWindowOrphan(unittest.TestCase):
    """High: build() must not create mid-window orphan tool results."""

    def test_mid_window_orphan(self):
        from fhone import context
        from fhone.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "db": str(Path(tmp) / "test.db"),
                "model": "test",
                "context_window": 4,
                "context_token_budget": 6000,
                "system_prompt": "test",
            }
            store = Store(cfg["db"])
            store.ensure_session("s1", "test")

            # Build a conversation where budget trim can split tool pairs
            store.add("s1", "user", "do something")
            store.add("s1", "assistant", None,
                      tool_calls=[{"id": "call_1", "type": "function",
                                   "function": {"name": "terminal",
                                                "arguments": "{}"}}])
            store.add("s1", "tool", "output1", tool_call_id="call_1",
                      name="terminal")
            store.add("s1", "assistant", None,
                      tool_calls=[{"id": "call_2", "type": "function",
                                   "function": {"name": "terminal",
                                                "arguments": "{}"}}])
            store.add("s1", "tool", "output2", tool_call_id="call_2",
                      name="terminal")
            store.add("s1", "assistant", "done")

            messages = context.build(store, "s1", cfg)

            # Check: every tool message has a matching assistant tool_call
            tool_call_ids = set()
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        if tc.get("id"):
                            tool_call_ids.add(tc["id"])

            for msg in messages:
                if msg.get("role") == "tool":
                    self.assertIn(msg.get("tool_call_id"), tool_call_ids,
                                  "Orphaned tool result with no matching tool_calls")

            store.close()


class TestSupervisorHealthCheck(unittest.TestCase):
    """Medium: supervisor must check background thread health."""

    def test_health_check_exists(self):
        from fhone import supervisor
        import inspect
        source = inspect.getsource(supervisor.Supervisor.run)
        # Should check thread health
        self.assertIn("is_alive", source,
                      "Supervisor must check if background threads are alive")


if __name__ == "__main__":
    unittest.main()
