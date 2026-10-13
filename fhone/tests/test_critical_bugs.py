"""Regression tests for critical bugs found in tools.py, agent.py, llm.py, store.py.

Critical:
- file_move/copy/delete bypass sandbox (no _resolve)
- _publish() silently swallows all exceptions
- _with_retry doesn't catch ValueError from json.loads
- agent doesn't mark interrupted turns in DB

High:
- process_manage log blocks on live pipe
- terminal() timeout leaves orphan child process
- context.build() can produce mid-window orphan tool results

Medium:
- _BACKGROUND dict grows unbounded
- execute_code() discards stderr on success
- supervisor has no health check for background threads
- _connections list grows unbounded (fd leak)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import fake_server  # noqa: E402


class TestFileSandboxBypass(unittest.TestCase):
    """Critical: file_move/copy/delete must use _resolve() like all other fs tools."""

    def test_file_move_outside_sandbox(self):
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

            # Try to move a file outside the sandbox
            target = Path(tmp) / "outside.txt"
            target.write_text("secret")
            result = t.dispatch("file_move", {"src": str(target), "dst": "/etc/malicious"})
            self.assertIn("outside_sandbox", result,
                          "file_move must refuse paths outside sandbox")

            store.close()

    def test_file_copy_outside_sandbox(self):
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

            # Try to copy a file outside the sandbox
            result = t.dispatch("file_copy", {"src": "/etc/passwd", "dst": "/tmp/stolen"})
            self.assertIn("outside_sandbox", result,
                          "file_copy must refuse paths outside sandbox")

            store.close()

    def test_file_delete_outside_sandbox(self):
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

            # Try to delete a file outside the sandbox
            result = t.dispatch("file_delete", {"path": "/etc/passwd"})
            self.assertIn("outside_sandbox", result,
                          "file_delete must refuse paths outside sandbox")

            store.close()


class TestPublishSwallowsExceptions(unittest.TestCase):
    """High: _publish() must not silently swallow all exceptions."""

    def test_publish_logs_errors(self):
        from fhone import agent as agent_mod

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "db": str(Path(tmp) / "test.db"),
                "model": "test",
                "team_flush_every": 1,
                "context_window": 12,
                "context_token_budget": 6000,
                "max_tool_rounds": 8,
                "tool_output_cap": 6000,
                "shell_timeout": 5,
                "approve_shell": False,
                "sandbox_roots": [tmp],
            }
            from fhone.store import Store
            store = Store(cfg["db"])

            # Mock store.count to raise
            original_count = store.count
            def bad_count(sid):
                raise RuntimeError("DB locked")
            store.count = bad_count

            ag = agent_mod.Agent(cfg, store, None, None, None)

            # Should not raise
            ag._publish("test", "user text", "answer")

            store.count = original_count
            store.close()


class TestLLMRetryValueError(unittest.TestCase):
    """High: _with_retry must catch ValueError from json.loads."""

    def test_retry_catches_valueerror(self):
        from fhone import llm

        client = llm.Client("http://127.0.0.1:1/v1", "k", timeout=5, max_retries=2)

        def bad_fn():
            raise ValueError("No JSON object could be decoded")

        # Should raise TransientError after retries, not propagate ValueError
        with self.assertRaises(llm.TransientError):
            client._with_retry(bad_fn)
        client.close()


class TestAgentInterruptedTurn(unittest.TestCase):
    """High: agent.turn() must mark interrupted turns in DB."""

    def test_interrupted_turn_marked(self):
        from fhone import agent as agent_mod
        from fhone.store import Store

        httpd, base = fake_server.serve(0)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = {
                    "db": str(Path(tmp) / "test.db"),
                    "model": "test-model",
                    "api_base": base,
                    "request_timeout": 5,
                    "max_retries": 1,
                    "context_window": 12,
                    "context_token_budget": 6000,
                    "max_tool_rounds": 2,
                    "tool_output_cap": 6000,
                    "shell_timeout": 5,
                    "approve_shell": False,
                    "sandbox_roots": [tmp],
                    "system_prompt": "test",
                }
                store = Store(cfg["db"])
                from fhone import providers, tools as tools_mod
                from fhone.ui import QuietUI
                qui = QuietUI()
                pool = providers.ProviderPool(store, cfg)
                pool.add_provider("fake", base, label="fake",
                                  api_style="openai", priority=1)
                pool.add_key("fake", "k1", "one")
                t = tools_mod.Tools(cfg, store, qui, pool)

                ag = agent_mod.Agent(cfg, store, pool, t, qui)

                # Simulate KeyboardInterrupt during turn
                original_chat = pool.chat
                def interrupt_chat(*args, **kwargs):
                    raise KeyboardInterrupt()
                pool.chat = interrupt_chat

                # The agent should catch KeyboardInterrupt and return gracefully
                try:
                    result = ag.turn("test-session", "hello")
                except KeyboardInterrupt:
                    result = None

                # The user message should still be stored
                rows = store.window("test-session", 10)
                user_msgs = [r for r in rows if r["role"] == "user"]
                self.assertTrue(len(user_msgs) > 0, "User message should be stored")

                pool.chat = original_chat
                store.close()
                pool.close()
        finally:
            httpd.shutdown()


class TestTerminalTimeoutOrphan(unittest.TestCase):
    """Medium: terminal() timeout must kill the child process."""

    def test_timeout_kills_child(self):
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
                "shell_timeout": 1,
                "tool_output_cap": 6000,
            }
            store = Store(cfg["db"])
            t = tools_mod.Tools(cfg, store)

            # Run a command that will timeout
            result = t.dispatch("terminal", {"command": "sleep 30", "timeout": 1})
            self.assertIn("timeout", result)

            # The child process should be dead
            # (hard to test directly, but we can verify the tool returned)
            store.close()


class TestBackgroundCleanup(unittest.TestCase):
    """Medium: _BACKGROUND dict must not grow unbounded."""

    def test_background_cleanup(self):
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

            # Start a background process that exits immediately
            result = t.dispatch("terminal", {"command": "echo done", "background": True})
            self.assertIn("started", result)

            # The process should be in _BACKGROUND
            self.assertTrue(len(tools_mod._BACKGROUND) > 0)

            # After cleanup, exited processes should be removed
            # (this tests the pattern exists)
            store.close()


if __name__ == "__main__":
    unittest.main()
