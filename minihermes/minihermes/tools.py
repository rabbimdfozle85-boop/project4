"""The tool registry.

Every schema in here is sent on every API call, so the list is deliberately
short: each extra tool is a permanent tax on every request. Eight tools is
enough for a personal agent and cheap enough to keep.
"""
from __future__ import annotations

import gzip
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

_SCHEMA_SHELL = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": "Run a shell command and return its combined output.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}
_SCHEMA_READ = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a text file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
}
_SCHEMA_WRITE = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write text to a file, replacing it.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
}
_SCHEMA_EDIT = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": "Replace the first exact occurrence of old with new in a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
    },
}
_SCHEMA_LS = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "List directory entries.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}
_SCHEMA_HTTP = {
    "type": "function",
    "function": {
        "name": "http_get",
        "description": "Fetch a URL and return the body as text.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
}
_SCHEMA_MEM_SAVE = {
    "type": "function",
    "function": {
        "name": "memory_save",
        "description": "Save a durable fact about the user or environment.",
        "parameters": {
            "type": "object",
            "properties": {"content": {"type": "string"}, "kind": {"type": "string"}},
            "required": ["content"],
        },
    },
}
_SCHEMA_MEM_SEARCH = {
    "type": "function",
    "function": {
        "name": "memory_search",
        "description": "Search saved memories by keyword.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


class Tools:
    def __init__(self, cfg: dict, store, ui=None) -> None:
        self.cfg = cfg
        self.store = store
        self.ui = ui
        roots = cfg.get("sandbox_roots") or [str(Path.home())]
        self.roots = [Path(os.path.expanduser(r)).resolve() for r in roots]
        self._table = {
            "run_shell": (_SCHEMA_SHELL, self.run_shell),
            "read_file": (_SCHEMA_READ, self.read_file),
            "write_file": (_SCHEMA_WRITE, self.write_file),
            "edit_file": (_SCHEMA_EDIT, self.edit_file),
            "list_dir": (_SCHEMA_LS, self.list_dir),
            "http_get": (_SCHEMA_HTTP, self.http_get),
            "memory_save": (_SCHEMA_MEM_SAVE, self.memory_save),
            "memory_search": (_SCHEMA_MEM_SEARCH, self.memory_search),
        }

    # -- registry ----------------------------------------------------------
    def schemas(self) -> list:
        return [schema for schema, _ in self._table.values()]

    def names(self) -> list:
        return list(self._table)

    def dispatch(self, name: str, arguments) -> str:
        entry = self._table.get(name)
        if entry is None:
            return "error: unknown tool %r" % name
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError as e:
                return "error: bad JSON arguments: %s" % e
        if not isinstance(arguments, dict):
            return "error: arguments must be an object"
        try:
            return str(entry[1](**arguments))
        except TypeError as e:
            return "error: bad arguments for %s: %s" % (name, e)
        except Exception as e:  # a tool must never kill the turn
            return "error: %s: %s" % (type(e).__name__, e)

    # -- helpers -----------------------------------------------------------
    def _cap(self, text: str) -> str:
        cap = int(self.cfg["tool_output_cap"])
        if len(text) > cap:
            return text[:cap] + "\n...[truncated %d chars]" % (len(text) - cap)
        return text

    def _resolve(self, raw: str) -> Path:
        path = Path(os.path.expanduser(raw)).resolve()
        for root in self.roots:
            try:
                path.relative_to(root)
                return path
            except ValueError:
                continue
        raise PermissionError("path outside sandbox: %s" % path)

    # -- tools -------------------------------------------------------------
    def run_shell(self, command: str) -> str:
        if self.cfg.get("approve_shell") and not self.cfg.get("_yolo"):
            if self.ui is not None and not self.ui.confirm("run: " + command):
                return "denied by user"
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=int(self.cfg["shell_timeout"]), cwd=str(Path.home()),
            )
        except subprocess.TimeoutExpired:
            return "timeout after %ss" % self.cfg["shell_timeout"]
        out = (proc.stdout or "") + (proc.stderr or "")
        out = self._cap(out)
        return ("exit=%d\n%s" % (proc.returncode, out)) if out else "exit=%d" % proc.returncode

    def read_file(self, path: str, max_bytes: int = 0) -> str:
        p = self._resolve(path)
        limit = int(max_bytes) or int(self.cfg["tool_output_cap"])
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return self._cap(fh.read(limit))

    def write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return "wrote %d bytes to %s" % (len(content), p)

    def edit_file(self, path: str, old: str, new: str) -> str:
        p = self._resolve(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        if old not in text:
            return "error: old text not found in %s" % p
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return "edited %s" % p

    def list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        entries = []
        for child in sorted(p.iterdir()):
            entries.append(("d " if child.is_dir() else "f ") + child.name)
        return self._cap("\n".join(entries) or "(empty)")

    def http_get(self, url: str) -> str:
        req = urllib.request.Request(
            url, headers={"User-Agent": "mini-hermes/0.1", "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(int(self.cfg["tool_output_cap"]) * 2)
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
        except urllib.error.HTTPError as e:
            return "HTTP %s: %s" % (e.code, e.reason)
        except urllib.error.URLError as e:
            return "error: %s" % e.reason
        return self._cap(raw.decode("utf-8", "replace"))

    def memory_save(self, content: str, kind: str = "fact") -> str:
        rid = self.store.memory_add(content, kind)
        return "saved memory #%d" % rid

    def memory_search(self, query: str) -> str:
        rows = self.store.memory_search(query)
        if not rows:
            return "no memories matched"
        return "\n".join("- %s" % r["content"] for r in rows)
