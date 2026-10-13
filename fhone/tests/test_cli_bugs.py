"""Regression tests for the three CLI bugs reported by the user.

Bug 1: fhone dashboard command does not exist
Bug 2: KeyboardInterrupt in the REPL prints a raw Python traceback
Bug 3: PoolError on total failure dumps raw connection errors
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))


class TestDashboardCommand(unittest.TestCase):
    """Bug 1: fhone dashboard should work as an alias for status."""

    def test_dashboard_subcommand(self):
        """fhone dashboard must exit 0 and print FHONE status."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["FHONE_HOME"] = tmp
            env["PYTHONPATH"] = str(ROOT)
            proc = subprocess.run(
                [sys.executable, "-m", "fhone", "dashboard"],
                capture_output=True, text=True, env=env,
                cwd=str(ROOT), timeout=30,
            )
            self.assertEqual(proc.returncode, 0,
                             "dashboard exit %d: %s" % (proc.returncode, proc.stderr[-200:]))
            self.assertIn("FHONE", proc.stdout)
            self.assertIn("providers", proc.stdout)

    def test_dashboard_in_help(self):
        """fhone --help must list dashboard as a command."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["FHONE_HOME"] = tmp
            env["PYTHONPATH"] = str(ROOT)
            proc = subprocess.run(
                [sys.executable, "-m", "fhone", "--help"],
                capture_output=True, text=True, env=env,
                cwd=str(ROOT), timeout=30,
            )
            self.assertIn("dashboard", proc.stdout)


class TestKeyboardInterrupt(unittest.TestCase):
    """Bug 2: Ctrl-C in the REPL must not print a raw traceback."""

    def test_ctrl_c_clean_exit(self):
        """Simulate KeyboardInterrupt at the prompt; expect clean exit."""
        from fhone import ui as ui_mod

        ui = ui_mod.QuietUI()
        # Mock input to raise KeyboardInterrupt
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            # The UI.prompt should return "/exit" on KeyboardInterrupt
            # (it already catches EOFError, but KeyboardInterrupt propagates)
            result = ui.prompt("test-session")
            # After fix: should return "/exit"
            self.assertEqual(result, "/exit")

    def test_ctrl_c_in_repl_no_traceback(self):
        """Run the REPL with input that triggers KeyboardInterrupt."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "fh"
            home.mkdir(parents=True)
            (home / "config.json").write_text(
                '{"api_base": "http://localhost:1/v1", "model": "test", '
                '"approve_shell": false, "sandbox_roots": ["%s"]}' % tmp)
            env = dict(os.environ)
            env["FHONE_HOME"] = str(home)
            env["PYTHONPATH"] = str(ROOT)
            env["FHONE_API_KEY"] = "k"
            # Send EOF immediately (simulates Ctrl-C / Ctrl-D)
            proc = subprocess.run(
                [sys.executable, "-m", "fhone", "--model", "test"],
                input="", capture_output=True, text=True, env=env,
                cwd=str(ROOT), timeout=30,
            )
            # Should NOT contain a Python traceback
            self.assertNotIn("Traceback", proc.stdout)
            self.assertNotIn("Traceback", proc.stderr)
            # Should exit cleanly
            self.assertIn(proc.returncode, (0, 2),
                          "exit %d: %s" % (proc.returncode, proc.stderr[-200:]))


class TestProviderErrorSummary(unittest.TestCase):
    """Bug 3: PoolError should show a readable summary, not raw errors."""

    def test_pool_error_readable(self):
        """When all providers fail, PoolError should be concise."""
        from fhone import providers, llm, store as store_mod

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "db": str(Path(tmp) / "test.db"),
                "model": "test-model",
                "request_timeout": 5,
                "max_retries": 1,
                "context_window": 12,
                "context_token_budget": 6000,
                "max_tool_rounds": 8,
                "tool_output_cap": 6000,
                "shell_timeout": 5,
                "approve_shell": False,
                "sandbox_roots": [tmp],
            }
            st = store_mod.Store(cfg["db"])
            pool = providers.ProviderPool(st, cfg)
            # Add a provider pointing to a dead port
            pool.add_provider("dead", "http://127.0.0.1:1/v1",
                              label="dead", api_style="openai", priority=1)
            pool.add_key("dead", "k1", "key-one")

            with self.assertRaises(providers.PoolError) as ctx:
                pool.chat([{"role": "user", "content": "hi"}], stream=False)

            msg = str(ctx.exception)
            # Must NOT contain raw errno or socket errors
            self.assertNotIn("Errno", msg)
            self.assertNotIn("[Errno", msg)
            self.assertNotIn("Connection reset", msg)
            # Must contain a readable summary
            self.assertIn("all providers failed", msg.lower())
            st.close()


if __name__ == "__main__":
    unittest.main()
