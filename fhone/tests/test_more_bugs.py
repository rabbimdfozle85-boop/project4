"""Additional regression tests for FHONE bugs found in code review.

Bug 4: PoolError with dead keys still shows raw error
Bug 5: All keys dead → PoolError says "no usable provider" not "all failed"
Bug 6: Non-streaming chat doesn't record usage
Bug 7: Telegram handler sends raw exception to user
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import fake_server  # noqa: E402


class TestPoolErrorDeadKeys(unittest.TestCase):
    """Bug 4: When all keys are dead, PoolError should be clear."""

    def test_all_dead_keys_readable(self):
        from fhone import providers, store as store_mod

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
            pool.add_provider("fake", "http://127.0.0.1:1/v1",
                              label="fake", api_style="openai", priority=1)
            k1 = pool.add_key("fake", "k1", "one")
            # Mark key as dead
            st.key_set_status(k1, "dead")

            with self.assertRaises(providers.PoolError) as ctx:
                pool.chat([{"role": "user", "content": "hi"}], stream=False)

            msg = str(ctx.exception)
            # Should say something about no usable provider
            self.assertIn("no usable provider", msg.lower())
            st.close()


class TestNonStreamingUsage(unittest.TestCase):
    """Bug 6: Non-streaming chat must record usage."""

    def test_non_streaming_usage_recorded(self):
        from fhone import providers, store as store_mod

        httpd, base = fake_server.serve(0)
        try:
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
                pool.add_provider("fake", base, label="fake",
                                  api_style="openai", priority=1)
                pool.add_key("fake", "k1", "one")

                result = pool.chat([{"role": "user", "content": "hi"}],
                                   stream=False)
                self.assertTrue(result.get("content"))

                usage = st.usage_total()
                self.assertGreater(usage["r"], 0,
                                   "non-streaming usage not recorded")
                st.close()
        finally:
            httpd.shutdown()


class TestTelegramErrorHandling(unittest.TestCase):
    """Bug 7: Telegram handler must not send raw exceptions to users."""

    def test_telegram_no_raw_exception(self):
        from fhone.surfaces import telegram as tg_mod

        # Build a mock agent that raises
        class FailingAgent:
            def turn(self, sid, text):
                raise RuntimeError("connection reset by peer [Errno 104]")

        class MockStore:
            def ensure_session(self, sid, model):
                pass

            def sessions(self):
                return []

            def usage_today(self):
                return []

            def memory_search(self, q):
                return []

            def memory_list(self):
                return []

            def memory_add(self, content, kind):
                pass

            def team_feed(self):
                return []

        sent_messages = []

        class MockAPI:
            def send_message(self, chat_id, text):
                sent_messages.append(text)

            def send_action(self, chat_id, action="typing"):
                pass

            def set_commands(self, commands):
                pass

        cfg = {
            "model": "test",
            "telegram_token": "fake",
            "telegram_allowlist": [],
        }

        surface = tg_mod.TelegramSurface(cfg, lambda: None)
        surface.api = MockAPI()
        surface._cache["123"] = (FailingAgent(), MockStore(), None, None, None)
        surface.sessions["123"] = "tg-123"

        update = {
            "update_id": 1,
            "message": {
                "text": "hello",
                "chat": {"id": 123},
                "from": {"id": 999},
            }
        }

        # This should not raise
        surface._handle(update)

        # User should get a friendly message, not a raw exception
        self.assertTrue(len(sent_messages) > 0)
        user_msg = sent_messages[-1]
        self.assertNotIn("Errno", user_msg)
        self.assertNotIn("Traceback", user_msg)
        # Should be a friendly message, not a raw exception
        self.assertTrue(len(user_msg) > 0)


if __name__ == "__main__":
    unittest.main()
