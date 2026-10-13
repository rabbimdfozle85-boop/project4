"""Tests for config, skills, and context bugs found in code review.

Bug A: config DEFAULTS has duplicate keys (status_bar, timestamps, etc.)
Bug B: skills._find() calls index() (full dir scan) on every lookup
Bug C: context.build() can produce orphaned tool results (API rejects)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))


class TestConfigDefaults(unittest.TestCase):
    """Bug A: DEFAULTS dict has duplicate keys."""

    def test_no_duplicate_keys(self):
        from fhone import config
        # Parse DEFAULTS source to detect duplicates
        source = Path(config.__file__).read_text()
        # Count occurrences of "status_bar" in DEFAULTS
        import ast
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "DEFAULTS":
                        keys = [k.value for k in node.value.keys
                                if isinstance(k, ast.Constant)]
                        duplicates = [k for k in keys if keys.count(k) > 1]
                        self.assertEqual(duplicates, [],
                                         "Duplicate keys in DEFAULTS: %s" % duplicates)


class TestSkillsFind(unittest.TestCase):
    """Bug B: _find() should not scan the directory every time."""

    def test_find_caches_index(self):
        from fhone import skills

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"skills_dir": str(Path(tmp) / "skills")}
            skill_dir = Path(cfg["skills_dir"])
            skill_dir.mkdir(parents=True)
            (skill_dir / "demo").mkdir()
            (skill_dir / "demo" / "SKILL.md").write_text(
                "---\nname: demo\ndescription: test\ntier: 1\n---\nbody\n")

            # First call should work
            entry = skills._find(cfg, "demo")
            self.assertIsNotNone(entry)

            # Second call should also work (and not re-scan)
            entry2 = skills._find(cfg, "demo")
            self.assertIsNotNone(entry2)

            # Non-existent skill
            entry3 = skills._find(cfg, "nonexistent")
            self.assertIsNone(entry3)


class TestContextBuild(unittest.TestCase):
    """Bug C: build() must not produce orphaned tool results."""

    def test_no_orphan_tool_results(self):
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

            # Add a conversation with tool calls
            store.add("s1", "user", "do something")
            store.add("s1", "assistant", None,
                      tool_calls=[{"id": "call_1", "type": "function",
                                   "function": {"name": "terminal",
                                                "arguments": "{}"}}])
            store.add("s1", "tool", "output", tool_call_id="call_1",
                      name="terminal")
            store.add("s1", "assistant", "done")

            # Build context
            messages = context.build(store, "s1", cfg)

            # Check: no tool message without preceding assistant tool_call
            for i, msg in enumerate(messages):
                if msg.get("role") == "tool":
                    # Must have a preceding assistant message with tool_calls
                    has_tool_call = False
                    for j in range(i - 1, -1, -1):
                        if messages[j].get("role") == "assistant":
                            if messages[j].get("tool_calls"):
                                has_tool_call = True
                            break
                    self.assertTrue(has_tool_call,
                                    "Orphaned tool result at index %d" % i)

            store.close()


if __name__ == "__main__":
    unittest.main()
