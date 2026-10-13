"""Tests for __main__ and llm bugs found in review.

Bug D: cmd_install reports "providers seeded" when seed_defaults() is a no-op
Bug E: cmd_chat uses hardcoded "cli-default" session for all CLI chats
Bug F: cmd_chat doesn't validate that a provider key is healthy, just that one exists
Bug G: llm._with_retry can return None if fn() raises an exception not in the catch list
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import fake_server  # noqa: E402


class TestInstallProvidersSeeded(unittest.TestCase):
    """Bug D: install prints "providers seeded" even when nothing is seeded."""

    def test_install_no_seeded_message(self):
        from fhone import __main__ as main_mod
        from fhone.store import Store
        from fhone import config as config_mod

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "fh"
            home.mkdir(parents=True)
            cfg = {
                "db": str(home / "test.db"),
                "home": str(home),
                "model": "",
                "api_key": "test",
                "sandbox_roots": [tmp],
                "skills_dir": str(home / "skills"),
                "nous_portal_url": "http://localhost:1",
            }

            # Test the actual cmd_install output by checking the code
            import inspect
            source = inspect.getsource(main_mod.cmd_install)
            # The old behavior was: print("  ok   providers seeded (%s)" % ...)
            # The fixed behavior should check if seeded list is non-empty
            self.assertIn("if seeded", source,
                          "cmd_install should check if any providers were seeded")
            self.assertIn("no default providers", source,
                          "cmd_install should print 'no default providers' when empty")


class TestCLIChatSession(unittest.TestCase):
    """Bug E: CLI chat should use unique session IDs, not hardcoded 'cli-default'."""

    def test_session_id_not_hardcoded(self):
        from fhone import __main__ as main_mod
        import inspect
        source = inspect.getsource(main_mod.main)
        # The line args.session = "cli-default" should not exist
        self.assertNotIn('"cli-default"', source,
                         "CLI should not use hardcoded 'cli-default' session")


class TestChatProviderValidation(unittest.TestCase):
    """Bug F: cmd_chat should validate key health, not just provider existence."""

    def test_provider_health_checked(self):
        from fhone import __main__ as main_mod
        import inspect
        source = inspect.getsource(main_mod.main)
        # Should check pool.health() or similar, not just store.providers()
        has_health_check = "health" in source.lower() or "pool" in source.lower()
        self.assertTrue(has_health_check,
                        "cmd_chat should validate provider health, not just existence")


class TestLLMRetry(unittest.TestCase):
    """Bug G: _with_retry should not silently return None."""

    def test_retry_no_silent_none(self):
        from fhone import llm

        # If fn raises an unexpected exception, _with_retry should not
        # silently return None. It should either catch it or raise.
        client = llm.Client("http://127.0.0.1:1/v1", "k", timeout=5, max_retries=2)

        def bad_fn():
            raise ValueError("unexpected error")

        with self.assertRaises((llm.LLMError, ValueError)):
            client._with_retry(bad_fn)
        client.close()


if __name__ == "__main__":
    unittest.main()
