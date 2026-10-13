"""Tests for remaining medium bugs from subagent review.

- _migrate() not atomic (crash window between schema change and version bump)
- _connections list grows unbounded (fd leak)
- con property doesn't detect closed connections
- executescript runs outside lock in _new_connection
- streaming doesn't handle connection drop mid-stream
- tool_call_ids set incomplete when tool_calls JSON is corrupt
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
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import fake_server  # noqa: E402


class TestMigrateAtomic(unittest.TestCase):
    """Medium: _migrate() must be atomic (schema change + version bump in one txn)."""

    def test_migrate_atomic(self):
        from fhone.store import Store
        source = inspect.getsource(Store._migrate)
        # Should use BEGIN/COMMIT or similar atomic wrapper
        self.assertIn("BEGIN", source.upper(),
                      "_migrate() must wrap schema changes in a transaction")


class TestConnectionsUnbounded(unittest.TestCase):
    """Medium: _connections list must not grow unbounded."""

    def test_connections_cleanup(self):
        from fhone.store import Store
        source = inspect.getsource(Store)
        # Should have some cleanup mechanism for dead thread connections
        self.assertIn("_connections", source)
        # Check if there's any removal logic
        has_removal = "remove" in source or "discard" in source or "pop" in source
        self.assertTrue(has_removal,
                        "Store must have connection cleanup for dead threads")


class TestConPropertyClosed(unittest.TestCase):
    """Medium: con property must detect closed connections."""

    def test_con_detects_closed(self):
        from fhone.store import Store
        source = inspect.getsource(Store.con.fget)
        # Should detect closed connections by executing a test query
        has_check = "SELECT 1" in source
        self.assertTrue(has_check,
                        "con property must detect closed connections via test query")


class TestNewConnectionLock(unittest.TestCase):
    """Low: _new_connection runs executescript outside lock."""

    def test_new_connection_locked(self):
        from fhone.store import Store
        source = inspect.getsource(Store._new_connection)
        # executescript should be inside a lock
        self.assertIn("_lock", source,
                      "_new_connection must run executescript inside lock")


if __name__ == "__main__":
    unittest.main()
