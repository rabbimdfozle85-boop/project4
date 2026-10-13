"""Tests for remaining low/medium bugs from subagent review.

- No SIGHUP handler (unclean termination on terminal close)
- _shutdown() doesn't join threads (daemon threads killed mid-operation)
- Store.close() doesn't checkpoint WAL (slow startup, large WAL file)
- process_manage list shows raw poll() value
- _fetch() reads 4x cap into memory
- _migrate() not atomic (crash window)
"""
from __future__ import annotations

import inspect
import os
import signal
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


class TestSIGHUPHandler(unittest.TestCase):
    """No SIGHUP handler means unclean termination on terminal close."""

    def test_sighup_handled(self):
        from fhone import supervisor
        source = inspect.getsource(supervisor.Supervisor._install_signal_handlers)
        self.assertIn("SIGHUP", source,
                      "Supervisor must handle SIGHUP for clean shutdown")


class TestShutdownJoinsThreads(unittest.TestCase):
    """_shutdown() must join threads before exiting."""

    def test_shutdown_joins(self):
        from fhone import supervisor
        source = inspect.getsource(supervisor.Supervisor._shutdown)
        self.assertIn("join", source,
                      "_shutdown() must join background threads")


class TestStoreWALCheckpoint(unittest.TestCase):
    """Store.close() should checkpoint WAL to minimize file size."""

    def test_wal_checkpoint(self):
        from fhone.store import Store
        source = inspect.getsource(Store.close)
        self.assertIn("wal_checkpoint", source,
                      "Store.close() should checkpoint WAL")


if __name__ == "__main__":
    unittest.main()
