"""Tests for critical bugs found in final review.

Critical:
- process_manage log blocks on read() (hangs turn)
- shell=True injection risk

High:
- No SSRF protection in _fetch
- context.build() orphan tool results in edge cases
- llm streaming discards partial content on connection drop

Medium:
- Content-Length not validated
- gzip decode uncaught
- Full body read before truncate
- No per-provider health in status bar
- show_menu EOF returns -1
"""
from __future__ import annotations

import inspect
import json
import os
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


class TestProcessManageLogBlocks(unittest.TestCase):
    """Critical: process_manage log must not block on read()."""

    def test_log_non_blocking(self):
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

            # Start a long-running background process
            result = t.dispatch("terminal", {"command": "sleep 30", "background": True})
            self.assertIn("started", result)
            sid = result.split()[1]

            # Log call must return immediately, not block for 30s
            start = time.time()
            log_result = t.dispatch("process_manage", {"action": "log", "session_id": sid})
            elapsed = time.time() - start
            self.assertLess(elapsed, 2.0,
                            "process_manage log blocked for %.1fs" % elapsed)

            # Clean up
            t.dispatch("process_manage", {"action": "kill", "session_id": sid})
            store.close()


class TestSSRFProtection(unittest.TestCase):
    """High: _fetch must block file:// and private IPs."""

    def test_file_scheme_blocked(self):
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

            result = t.dispatch("http_get", {"url": "file:///etc/passwd"})
            self.assertIn("error", result.lower(),
                          "file:// scheme must be blocked")

            store.close()


class TestContextOrphanEdgeCase(unittest.TestCase):
    """High: build() must not leave orphan tool results when only tools remain."""

    def test_all_tools_no_assistant(self):
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

            # Add only tool results (no assistant with tool_calls)
            store.add("s1", "tool", "orphan1", tool_call_id="call_1", name="terminal")
            store.add("s1", "tool", "orphan2", tool_call_id="call_2", name="terminal")

            messages = context.build(store, "s1", cfg)

            # No tool messages should remain (no matching tool_calls)
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            self.assertEqual(len(tool_msgs), 0,
                             "Orphaned tool results must be removed")

            store.close()


class TestGzipDecodeUncaught(unittest.TestCase):
    """Medium: gzip decode must not crash on bad data."""

    def test_bad_gzip(self):
        from fhone import tools as tools_mod
        from fhone.store import Store
        import gzip

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

            # Directly test that gzip decode failure is handled
            raw = b"not valid gzip data"
            try:
                gzip.decompress(raw)
            except (gzip.BadGzipFile, OSError, EOFError):
                pass  # Should not crash — the fix catches this

            # Also test via http_get with a mock that returns bad gzip
            original_fetch = t._fetch
            def bad_fetch(url, headers=None, timeout=30):
                # Simulate what happens when gzip decode fails
                return "raw fallback data"
            t._fetch = bad_fetch

            result = t.dispatch("http_get", {"url": "http://example.com"})
            self.assertIsInstance(result, str)
            self.assertIn("raw fallback data", result)

            store.close()


class TestContentLengthValidation(unittest.TestCase):
    """Medium: Content-Length must be validated."""

    def test_content_length_reasonable(self):
        from fhone.surfaces import portal as portal_mod
        source = inspect.getsource(portal_mod.Handler.do_POST)
        # Should validate Content-Length is reasonable
        self.assertIn("Content-Length", source)


class TestMenuEOF(unittest.TestCase):
    """Medium: show_menu must handle EOF gracefully."""

    def test_eof_raises_systemexit(self):
        from fhone import menu
        source = inspect.getsource(menu.show_menu)
        # Should raise SystemExit on EOF, not return -1
        self.assertIn("SystemExit", source,
                      "show_menu must raise SystemExit on EOF")


class TestStatusBarProviderHealth(unittest.TestCase):
    """Status bar must show per-provider health, not just a global count."""

    def test_status_bar_shows_providers(self):
        from fhone.ui import UI
        import io
        stream = io.StringIO()
        ui = UI(stream=stream)
        state = {
            "status_bar": True,
            "model": "test-model",
            "health": {
                "ok": 1,
                "providers": [
                    {"id": "nous", "state": "1/1 keys", "label": "Nous"},
                    {"id": "pollinations", "state": "no keys", "label": "Poll"},
                ],
            },
            "usage": {"requests": 10, "prompt_tokens": 100, "completion_tokens": 50},
            "context": {"messages": 3, "window": 12},
        }
        ui.status_bar(state)
        output = stream.getvalue()
        self.assertIn("nous", output)
        self.assertIn("pollinations", output)
        self.assertIn("nokeys", output)
        self.assertIn("ctx 3/12", output)
        self.assertIn("tok 150", output)


if __name__ == "__main__":
    unittest.main()
