"""The FHONE tool registry.

Every tool returns a string and never raises into the turn loop. Every return is
capped. Every path is sandboxed. Every tool has a timeout.

The schemas below are sent on every API request, so the list is audited rather
than grown: 23 tools is the permanent per-request cost of the surface.
"""
from __future__ import annotations

import fnmatch
import html
import html.parser
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .llm import USER_AGENT

# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------

def _fn(name, desc, props, required):
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}}}


S_READ = _fn("read_file", "Read a UTF-8 text file.",
             {"path": {"type": "string"},
              "offset": {"type": "integer", "description": "1-based first line."},
              "limit": {"type": "integer", "description": "Max lines."}},
             ["path"])
S_WRITE = _fn("write_file", "Write text to a file, replacing it. Creates parents.",
              {"path": {"type": "string"}, "content": {"type": "string"},
               "append": {"type": "boolean"}}, ["path", "content"])
S_PATCH = _fn("patch", "Replace the first exact occurrence of old with new. "
                       "Fails if old is absent.",
              {"path": {"type": "string"}, "old": {"type": "string"},
               "new": {"type": "string"}, "replace_all": {"type": "boolean"}},
              ["path", "old", "new"])
S_SEARCH = _fn("search_files", "Search file contents by regex, or find files by glob.",
               {"pattern": {"type": "string"},
                "target": {"type": "string", "enum": ["content", "files"]},
                "path": {"type": "string"}, "file_glob": {"type": "string"},
                "limit": {"type": "integer"}}, ["pattern"])
S_LS = _fn("list_dir", "List directory entries, sorted.",
           {"path": {"type": "string"}}, ["path"])
S_TERM = _fn("terminal", "Run a shell command and return its combined output.",
             {"command": {"type": "string"},
              "timeout": {"type": "integer", "description": "Seconds, default 30."},
              "background": {"type": "boolean"},
              "workdir": {"type": "string"}}, ["command"])
S_PROC = _fn("process_manage", "Manage background processes started by terminal.",
             {"action": {"type": "string", "enum": ["list", "poll", "log", "kill"]},
              "session_id": {"type": "string"},
              "limit": {"type": "integer"}}, ["action"])
S_EXEC = _fn("execute_code", "Run Python in an isolated subprocess, return stdout.",
             {"code": {"type": "string"}, "timeout": {"type": "integer"}}, ["code"])
S_MEM = _fn("memory", "Save or search durable facts about the user or environment.",
            {"action": {"type": "string", "enum": ["add", "search", "list", "forget"]},
             "content": {"type": "string"}, "query": {"type": "string"},
             "kind": {"type": "string"}, "id": {"type": "integer"}}, ["action"])
S_SSEARCH = _fn("session_search", "Search past conversations by keyword.",
                {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"])
S_TODO = _fn("todo_list", "Track a task list across turns.",
             {"action": {"type": "string", "enum": ["add", "list", "complete", "clear"]},
              "text": {"type": "string"}, "id": {"type": "integer"}}, ["action"])
S_SKLIST = _fn("skills_list", "List indexed skills with descriptions.",
               {"tier": {"type": "integer"}, "query": {"type": "string"}}, [])
S_SKVIEW = _fn("skill_view", "Load a skill's full body into context.",
               {"name": {"type": "string"}}, ["name"])
S_SKMNG = _fn("skill_manage", "Create, edit or delete a skill (self-improvement).",
              {"action": {"type": "string",
                          "enum": ["create", "edit", "delete", "set_tier"]},
               "name": {"type": "string"}, "content": {"type": "string"},
               "description": {"type": "string"}, "tier": {"type": "integer"}},
              ["action", "name"])
S_DELEG = _fn("delegate_task", "Spawn a subagent for an independent subtask.",
              {"goal": {"type": "string"}, "context": {"type": "string"},
               "timeout": {"type": "integer"}}, ["goal"])
S_CRON = _fn("cronjob_manage", "Schedule a recurring job.",
             {"action": {"type": "string",
                         "enum": ["create", "list", "pause", "resume", "remove", "run_now"]},
              "schedule": {"type": "string"}, "command": {"type": "string"},
              "id": {"type": "integer"}}, ["action"])
S_WSEARCH = _fn("web_search", "Search the web.",
                {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"])
S_WEXTRACT = _fn("web_extract", "Fetch a URL and return readable text.",
                 {"url": {"type": "string"}, "max_chars": {"type": "integer"}}, ["url"])
S_HTTP = _fn("http_get", "Fetch a URL and return the raw body.",
             {"url": {"type": "string"}, "headers": {"type": "object"}}, ["url"])
S_VISION = _fn("vision_analyze", "Describe or answer questions about an image.",
               {"image": {"type": "string"}, "question": {"type": "string"}},
               ["image", "question"])
S_IMG = _fn("image_generate", "Generate an image from a prompt.",
            {"prompt": {"type": "string"}, "size": {"type": "string"}}, ["prompt"])
S_TTS = _fn("text_to_speech", "Speak text aloud through the phone's TTS engine.",
            {"text": {"type": "string"}, "rate": {"type": "number"}}, ["text"])
S_CLARIFY = _fn("clarify", "Ask the user a question when the decision is theirs.",
                {"question": {"type": "string"},
                 "choices": {"type": "array", "items": {"type": "string"}}},
                ["question"])
S_HTTP_POST = _fn("http_post", "POST data to a URL. Body is JSON if dict, else raw text.",
                  {"url": {"type": "string"}, "data": {},
                   "headers": {"type": "object"}}, ["url"])
S_SYSINFO = _fn("sysinfo", "Disk, memory, CPU, and uptime info.",
                 {"section": {"type": "string"}}, [])
S_FMOVE = _fn("file_move", "Move a file or directory.",
               {"src": {"type": "string"}, "dst": {"type": "string"}},
               ["src", "dst"])
S_FCOPY = _fn("file_copy", "Copy a file or directory.",
               {"src": {"type": "string"}, "dst": {"type": "string"}},
               ["src", "dst"])
S_FDELETE = _fn("file_delete", "Delete a file or directory.",
                 {"path": {"type": "string"}}, ["path"])
S_ENVGET = _fn("env_get", "Get an environment variable.",
                {"name": {"type": "string"}}, ["name"])
S_ENVSET = _fn("env_set", "Set an environment variable for this session.",
                {"name": {"type": "string"}, "value": {"type": "string"}},
                ["name", "value"])
S_GIT = _fn("git", "Git operations: status, diff, log, commit.",
             {"action": {"type": "string"}, "args": {"type": "string"}}, ["action"])
S_CURL = _fn("curl", "Make an HTTP request with full control.",
              {"url": {"type": "string"}, "method": {"type": "string"},
               "headers": {"type": "object"}, "data": {"type": "string"}}, ["url"])
S_JSON = _fn("json", "Parse and extract data from JSON.",
             {"data": {"type": "string"}, "path": {"type": "string"}}, ["data"])
S_CSV = _fn("csv", "Parse CSV data.",
             {"data": {"type": "string"}, "delimiter": {"type": "string"}}, ["data"])
S_BASE64 = _fn("base64", "Encode or decode base64.",
               {"data": {"type": "string"}, "decode": {"type": "boolean"}}, ["data"])
S_HASH = _fn("hash", "Compute hash (md5, sha1, sha256).",
             {"data": {"type": "string"}, "algorithm": {"type": "string"}}, ["data"])
S_RANDOM = _fn("random", "Generate random values.",
               {"type": {"type": "string"}, "length": {"type": "integer"}}, [])
S_DATETIME = _fn("datetime", "Current date/time operations.",
                 {"action": {"type": "string"}, "timezone": {"type": "string"}}, [])
S_REGEX = _fn("regex", "Apply regex to text.",
              {"pattern": {"type": "string"}, "text": {"type": "string"},
               "replace": {"type": "string"}}, ["pattern", "text"])


# ---------------------------------------------------------------------------
# background process registry (in-process; the supervisor owns the real one)
# ---------------------------------------------------------------------------

_BACKGROUND: dict = {}


def _human_duration(seconds: float) -> str:
    if seconds < 60:
        return "%ds" % int(seconds)
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        return "%dh%dm" % (seconds // 3600, (seconds % 3600) // 60)
    return "%dd%dh" % (seconds // 86400, (seconds % 86400) // 3600)


def _bg_id() -> str:
    return "bg%d" % (len(_BACKGROUND) + 1 + int(time.time()) % 1000)


class _HTMLText(html.parser.HTMLParser):
    """Strip tags; collect text and links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple] = []
        self._skip = 0
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "a":
            self._href = dict(attrs).get("href")
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if text:
            self.parts.append(text + " ")

    def text(self) -> str:
        joined = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", joined)).strip()


class _DDG(html.parser.HTMLParser):
    """Extract result links and snippets from DuckDuckGo's HTML endpoint."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "a" and "result__a" in cls:
            self.results.append({"title": "", "url": a.get("href", ""), "snippet": ""})
            self._in_title = True
        elif tag == "a" and "result__snippet" in cls:
            self._in_snippet = True

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_title = False
            self._in_snippet = False

    def handle_data(self, data):
        if not self.results:
            return
        if self._in_title:
            self.results[-1]["title"] += data.strip()
        elif self._in_snippet:
            self.results[-1]["snippet"] += data.strip()


class Tools:
    def __init__(self, cfg: dict, store, ui=None, pool=None) -> None:
        self.cfg = cfg
        self.store = store
        self.ui = ui
        self.pool = pool
        self.session_id = ""
        self.agent_id = "main"
        roots = cfg.get("sandbox_roots") or [str(Path.home())]
        self.roots = [Path(os.path.expanduser(r)).resolve() for r in roots]
        self._table = {
            "read_file": (S_READ, self.read_file),
            "write_file": (S_WRITE, self.write_file),
            "patch": (S_PATCH, self.patch),
            "search_files": (S_SEARCH, self.search_files),
            "list_dir": (S_LS, self.list_dir),
            "terminal": (S_TERM, self.terminal),
            "process_manage": (S_PROC, self.process_manage),
            "execute_code": (S_EXEC, self.execute_code),
            "memory": (S_MEM, self.memory),
            "session_search": (S_SSEARCH, self.session_search),
            "todo_list": (S_TODO, self.todo_list),
            "skills_list": (S_SKLIST, self.skills_list),
            "skill_view": (S_SKVIEW, self.skill_view),
            "skill_manage": (S_SKMNG, self.skill_manage),
            "delegate_task": (S_DELEG, self.delegate_task),
            "cronjob_manage": (S_CRON, self.cronjob_manage),
            "web_search": (S_WSEARCH, self.web_search),
            "web_extract": (S_WEXTRACT, self.web_extract),
            "http_get": (S_HTTP, self.http_get),
            "vision_analyze": (S_VISION, self.vision_analyze),
            "image_generate": (S_IMG, self.image_generate),
            "text_to_speech": (S_TTS, self.text_to_speech),
            "clarify": (S_CLARIFY, self.clarify),
            "http_post": (S_HTTP_POST, self.http_post),
            "sysinfo": (S_SYSINFO, self.sysinfo),
            "file_move": (S_FMOVE, self.file_move),
            "file_copy": (S_FCOPY, self.file_copy),
            "file_delete": (S_FDELETE, self.file_delete),
            "env_get": (S_ENVGET, self.env_get),
            "env_set": (S_ENVSET, self.env_set),
            "git": (S_GIT, self.git),
            "curl": (S_CURL, self.curl),
            "json": (S_JSON, self.json),
            "csv": (S_CSV, self.csv),
            "base64": (S_BASE64, self.base64),
            "hash": (S_HASH, self.hash),
            "random": (S_RANDOM, self.random),
            "datetime": (S_DATETIME, self.datetime),
            "regex": (S_REGEX, self.regex),
        }

    # -- registry ----------------------------------------------------------
    def schemas(self) -> list:
        return [schema for schema, _ in self._table.values()]

    def names(self) -> list:
        return list(self._table)

    def dispatch(self, name: str, arguments) -> str:
        entry = self._table.get(name)
        if entry is None:
            return "error: unknown_tool: %s" % name
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError as e:
                return "error: bad_arguments: invalid JSON: %s" % e
        if not isinstance(arguments, dict):
            return "error: bad_arguments: expected an object"
        try:
            result = entry[1](**arguments)
        except TypeError as e:
            return "error: bad_arguments: %s: %s" % (name, e)
        except PermissionError as e:
            return "error: outside_sandbox: %s" % e
        except FileNotFoundError as e:
            return "error: not_found: %s" % e
        except IsADirectoryError:
            return "error: is_a_directory: %s" % arguments.get("path", "")
        except Exception as e:                      # a tool must never kill a turn
            return "error: %s: %s" % (type(e).__name__, e)
        return self._cap(str(result))

    # -- helpers -----------------------------------------------------------
    def _cap(self, text: str) -> str:
        cap = int(self.cfg["tool_output_cap"])
        if len(text) > cap:
            return text[:cap] + "\n...[truncated %d chars]" % (len(text) - cap)
        return text

    def _resolve(self, raw: str) -> Path:
        path = Path(os.path.expanduser(raw))
        if not path.is_absolute():
            path = Path.home() / path
        path = path.resolve()
        for root in self.roots:
            try:
                path.relative_to(root)
                return path
            except ValueError:
                continue
        raise PermissionError(str(path))

    # -- filesystem --------------------------------------------------------
    def read_file(self, path: str, offset: int = 0, limit: int = 0) -> str:
        p = self._resolve(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        if offset or limit:
            lines = text.splitlines()
            start = max(0, int(offset) - 1) if offset else 0
            end = start + int(limit) if limit else len(lines)
            body = "\n".join(lines[start:end])
            return "[lines %d-%d of %d]\n%s" % (start + 1, min(end, len(lines)),
                                                len(lines), body)
        return text

    def write_file(self, path: str, content: str, append: bool = False) -> str:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with p.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        return "%s %d bytes to %s" % ("appended" if append else "wrote",
                                      len(content), p)

    def patch(self, path: str, old: str, new: str, replace_all: bool = False) -> str:
        if not old:
            return "error: bad_argument: old must not be empty"
        p = self._resolve(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            return "error: not_found: old text not present in %s" % p
        if count > 1 and not replace_all:
            return ("error: ambiguous: old text matches %d times in %s; "
                    "pass replace_all=true or add context" % (count, p))
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        p.write_text(updated, encoding="utf-8")
        return "patched %s (%d replacement%s)" % (
            p, count if replace_all else 1, "s" if replace_all and count > 1 else "")

    def search_files(self, pattern: str, target: str = "content", path: str = ".",
                     file_glob: str = "", limit: int = 50) -> str:
        root = self._resolve(path)
        cap = int(limit)
        if target == "files":
            hits = []
            for p in sorted(root.rglob("*")):
                if p.is_file() and (not file_glob or fnmatch.fnmatch(p.name, file_glob)):
                    hits.append(str(p))
                    if len(hits) >= cap:
                        break
            return "\n".join(hits) or "no matches"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return "error: bad_regex: %s" % e
        hits = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if file_glob and not fnmatch.fnmatch(p.name, file_glob):
                continue
            try:
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    for n, line in enumerate(fh, 1):
                        if rx.search(line):
                            hits.append("%s:%d: %s" % (p, n, line.rstrip()[:200]))
                            if len(hits) >= cap:
                                return "\n".join(hits)
            except OSError:
                continue
        return "\n".join(hits) or "no matches"

    def list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.is_dir():
            return "error: not_a_directory: %s" % p
        entries = [("d " if c.is_dir() else "f ") + c.name for c in sorted(p.iterdir())]
        return "\n".join(entries) or "(empty)"

    # -- execution ---------------------------------------------------------
    def terminal(self, command: str, timeout: int = 0, background: bool = False,
                 workdir: str = "") -> str:
        if self.cfg.get("approve_shell") and not self.cfg.get("_yolo"):
            if self.ui is not None and not self.ui.confirm("run: " + command):
                return "denied_by_user"
        secs = int(timeout) or int(self.cfg["shell_timeout"])
        secs = min(secs, 600)
        cwd = str(self._resolve(workdir)) if workdir else str(Path.home())
        if background:
            proc = subprocess.Popen(command, shell=True, cwd=cwd,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True)
            sid = _bg_id()
            _BACKGROUND[sid] = {"proc": proc, "cmd": command, "out": "",
                                "started": int(time.time())}
            return "started %s (pid %d)" % (sid, proc.pid)
        try:
            proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                                  timeout=secs, cwd=cwd)
        except subprocess.TimeoutExpired:
            return "error: timeout: %ss exceeded" % secs
        out = (proc.stdout or "") + (proc.stderr or "")
        return ("exit=%d\n%s" % (proc.returncode, out)) if out else "exit=%d" % proc.returncode

    def process_manage(self, action: str, session_id: str = "", limit: int = 200) -> str:
        if action == "list":
            if not _BACKGROUND:
                return "no background processes"
            return "\n".join(
                "%s  pid=%d  %s  %s" % (sid, j["proc"].pid, j["proc"].poll(),
                                        j["cmd"][:60])
                for sid, j in _BACKGROUND.items())
        job = _BACKGROUND.get(session_id)
        if job is None:
            return "error: not_found: %s" % session_id
        if action == "poll":
            return "running" if job["proc"].poll() is None else "exit=%s" % job["proc"].poll()
        if action == "log":
            out = job["proc"].stdout
            if out is not None:
                # Non-blocking: read() on a live pipe blocks until the child
                # exits, which hangs the whole turn.
                try:
                    os.set_blocking(out.fileno(), False)
                except (OSError, ValueError, AttributeError):
                    pass
                try:
                    chunk = out.read()
                except (BlockingIOError, TypeError, ValueError):
                    chunk = None
                if chunk:
                    job["out"] += chunk
            return self._cap(job["out"][-int(limit):] or "(no output yet)")
        if action == "kill":
            job["proc"].terminate()
            return "killed %s" % session_id
        return "error: bad_action: %s" % action

    def execute_code(self, code: str, timeout: int = 0) -> str:
        secs = min(int(timeout) or 30, 300)
        if self.cfg.get("approve_shell") and not self.cfg.get("_yolo"):
            if self.ui is not None and not self.ui.confirm("execute %d chars of Python" % len(code)):
                return "denied_by_user"
        try:
            proc = subprocess.run([sys.executable, "-c", code],
                                  capture_output=True, text=True, timeout=secs,
                                  cwd=str(Path.home()))
        except subprocess.TimeoutExpired:
            return "error: timeout: %ss exceeded" % secs
        out = proc.stdout or ""
        if proc.returncode:
            out += ("\n" if out else "") + (proc.stderr or "")[-2000:]
        return out or "(no output)"

    # -- memory ------------------------------------------------------------
    def memory(self, action: str, content: str = "", query: str = "",
               kind: str = "fact", id: int = 0) -> str:
        if action == "add":
            if not content:
                return "error: bad_argument: content required for add"
            mid = self.store.memory_add(content, kind, self.session_id, self.agent_id)
            return "saved memory #%d" % mid
        if action == "search":
            if not query:
                return "error: bad_argument: query required for search"
            rows = self.store.memory_search(query)
            return "\n".join("- %s" % r["content"] for r in rows) or "no memories matched"
        if action == "list":
            rows = self.store.memory_list()
            return "\n".join("#%d [%s] %s" % (r["id"], r["kind"], r["content"])
                             for r in rows) or "no memories yet"
        if action == "forget":
            return "forgotten" if self.store.memory_forget(id) else "error: not_found: #%s" % id
        return "error: bad_argument: unknown action %s" % action

    def session_search(self, query: str, limit: int = 10) -> str:
        rows = self.store.search_messages(query, limit)
        if not rows:
            return "no matches"
        return "\n".join("%s | %s | %s" % (r["session_id"], r["role"],
                                           (r["content"] or "")[:120]) for r in rows)

    def todo_list(self, action: str, text: str = "", id: int = 0) -> str:
        if action == "add":
            if not text:
                return "error: bad_argument: text required for add"
            self.store.todo_add(text)
        elif action == "complete":
            if not self.store.todo_complete(id):
                return "error: not_found: #%s" % id
        elif action == "clear":
            self.store.todo_clear()
        elif action != "list":
            return "error: bad_argument: unknown action %s" % action
        rows = self.store.todo_list()
        if not rows:
            return "(no todos)"
        return "\n".join("[%s] #%d %s" % ("x" if r["done"] else " ", r["id"], r["text"])
                         for r in rows)

    # -- skills ------------------------------------------------------------
    def skills_list(self, tier: int = 0, query: str = "") -> str:
        from . import skills as skills_mod
        return skills_mod.list_skills(self.cfg, tier=tier, query=query)

    def skill_view(self, name: str) -> str:
        from . import skills as skills_mod
        return skills_mod.view_skill(self.cfg, name)

    def skill_manage(self, action: str, name: str, content: str = "",
                     description: str = "", tier: int = 0) -> str:
        from . import skills as skills_mod
        return skills_mod.manage_skill(self.cfg, action, name, content, description, tier)

    # -- delegation --------------------------------------------------------
    def delegate_task(self, goal: str, context: str = "", timeout: int = 0) -> str:
        from . import delegate
        return delegate.run_subagent(self.cfg, goal, context, int(timeout) or 300)

    # -- scheduling --------------------------------------------------------
    def cronjob_manage(self, action: str, schedule: str = "", command: str = "",
                       id: int = 0) -> str:
        from . import scheduler
        return scheduler.manage(self.store, action, schedule, command, id)

    # -- network -----------------------------------------------------------
    def _fetch(self, url: str, headers: dict | None = None, timeout: int = 30):
        h = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        h.update(headers or {})
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(int(self.cfg["tool_output_cap"]) * 4)
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                import gzip
                raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")

    def web_search(self, query: str, limit: int = 5) -> str:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        try:
            body = self._fetch(url)
        except urllib.error.HTTPError as e:
            return "error: blocked: HTTP %s (the client sends a browser User-Agent; "\
                   "if this persists the endpoint is rate-limiting)" % e.code
        except Exception as e:
            return "error: fetch_failed: %s" % e
        parser = _DDG()
        try:
            parser.feed(body)
        except Exception:
            pass
        rows = parser.results[:int(limit)]
        if not rows:
            return "no_results"
        return "\n".join("%s\n  %s\n  %s" % (r["title"], r["url"], r["snippet"])
                         for r in rows)

    def web_extract(self, url: str, max_chars: int = 0) -> str:
        try:
            body = self._fetch(url)
        except urllib.error.HTTPError as e:
            return "error: http_error: %s" % e.code
        except Exception as e:
            return "error: fetch_failed: %s" % e
        parser = _HTMLText()
        try:
            parser.feed(body)
        except Exception:
            pass
        text = html.unescape(parser.text())
        limit = int(max_chars) or int(self.cfg["tool_output_cap"])
        return text[:limit]

    def http_get(self, url: str, headers: dict | None = None) -> str:
        try:
            return self._fetch(url, headers)
        except urllib.error.HTTPError as e:
            return "error: http_error: %s %s" % (e.code, e.reason)
        except Exception as e:
            return "error: fetch_failed: %s" % e

    # -- multimodal --------------------------------------------------------
    def http_post(self, url: str, data: str | None = None,
                 headers: dict | None = None) -> str:
        """POST data to a URL. Body is JSON if dict, else raw text."""
        import json as _json
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        body = None
        if data is not None:
            if isinstance(data, (dict, list)):
                body = _json.dumps(data).encode()
            else:
                body = str(data).encode()
                hdrs["Content-Type"] = "text/plain"
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")[:10000]
        except urllib.error.HTTPError as e:
            return "HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:2000])
        except Exception as e:
            return "error: %s: %s" % (type(e).__name__, e)

    def sysinfo(self, section: str = "all") -> str:
        """Disk, memory, CPU, and uptime info. Pure stdlib."""
        import platform
        lines = []
        if section in ("all", "disk"):
            try:
                st = os.statvfs(".")
                free = st.f_bavail * st.f_frsize
                total = st.f_blocks * st.f_frsize
                lines.append("  disk: %.1f GB free / %.1f GB total" % (
                    free / 1e9, total / 1e9))
            except Exception:
                lines.append("  disk: unavailable")
        if section in ("all", "mem"):
            try:
                with open("/proc/meminfo") as fh:
                    mem = {}
                    for line in fh:
                        if ":" in line:
                            k, v = line.split(":", 1)
                            mem[k.strip()] = v.strip()
                total = int(mem.get("MemTotal", "0 kB").split()[0]) * 1024
                avail = int(mem.get("MemAvailable", "0 kB").split()[0]) * 1024
                lines.append("  memory: %.1f GB free / %.1f GB total" % (
                    avail / 1e9, total / 1e9))
            except Exception:
                lines.append("  memory: unavailable")
        if section in ("all", "cpu"):
            try:
                with open("/proc/cpuinfo") as fh:
                    model = ""
                    cores = 0
                    for line in fh:
                        if "model name" in line and not model:
                            model = line.split(":", 1)[1].strip()
                        if "processor" in line:
                            cores += 1
                lines.append("  cpu: %s (%d cores)" % (model or "unknown", cores))
            except Exception:
                lines.append("  cpu: unavailable")
        if section in ("all", "uptime"):
            try:
                with open("/proc/uptime") as fh:
                    secs = float(fh.read().split()[0])
                lines.append("  uptime: %s" % _human_duration(secs))
            except Exception:
                lines.append("  uptime: unavailable")
        return "\n".join(lines) or "sysinfo: no info"

    def file_move(self, src: str, dst: str) -> str:
        """Move a file or directory."""
        import shutil
        try:
            shutil.move(src, dst)
            return "moved %s -> %s" % (src, dst)
        except Exception as e:
            return "error: %s" % e

    def file_copy(self, src: str, dst: str) -> str:
        """Copy a file or directory."""
        import shutil
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            return "copied %s -> %s" % (src, dst)
        except Exception as e:
            return "error: %s" % e

    def file_delete(self, path: str) -> str:
        """Delete a file or directory."""
        import shutil
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
            return "deleted %s" % path
        except Exception as e:
            return "error: %s" % e

    def env_get(self, name: str) -> str:
        """Get an environment variable."""
        val = os.environ.get(name)
        return val if val is not None else "(not set)"

    def env_set(self, name: str, value: str) -> str:
        """Set an environment variable for this session."""
        os.environ[name] = value
        return "set %s = %s" % (name, value)

    def git(self, action: str = "status", args: str = "") -> str:
        """Git operations: status, diff, log, commit."""
        cmd = ["git", action] + ([args] if args else [])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.stdout.strip():
                return result.stdout[:5000]
            if result.stderr.strip():
                return "error: %s" % result.stderr[:500]
            return "ok"
        except FileNotFoundError:
            return "error: git not found"
        except subprocess.TimeoutExpired:
            return "error: timeout"
        except Exception as e:
            return "error: %s" % e

    def curl(self, url: str, method: str = "GET", headers: dict | None = None,
             data: str | None = None) -> str:
        """Make an HTTP request with full control."""
        hdrs = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        if headers:
            hdrs.update(headers)
        body = None
        if data:
            body = data.encode() if isinstance(data, str) else data
        req = urllib.request.Request(url, data=body, headers=hdrs,
                                     method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")[:10000]
        except urllib.error.HTTPError as e:
            return "HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:2000])
        except Exception as e:
            return "error: %s: %s" % (type(e).__name__, e)

    def json(self, data: str, path: str = "") -> str:
        """Parse and extract data from JSON."""
        try:
            obj = json.loads(data)
        except ValueError as e:
            return "error: invalid JSON: %s" % e
        if not path:
            return json.dumps(obj, indent=2)[:5000]
        try:
            for key in path.split("."):
                if isinstance(obj, list):
                    obj = obj[int(key)]
                else:
                    obj = obj[key]
            return json.dumps(obj, indent=2)[:5000]
        except (KeyError, IndexError, ValueError) as e:
            return "error: path '%s' not found: %s" % (path, e)

    def csv(self, data: str, delimiter: str = ",") -> str:
        """Parse CSV data."""
        import io
        import csv as _csv
        try:
            reader = _csv.reader(io.StringIO(data), delimiter=delimiter)
            rows = list(reader)
            if not rows:
                return "empty"
            return "\n".join(", ".join(str(c) for c in row) for row in rows)
        except Exception as e:
            return "error: %s" % e

    def base64(self, data: str, decode: bool = False) -> str:
        """Encode or decode base64."""
        import base64 as _b64
        try:
            if decode:
                return _b64.b64decode(data).decode("utf-8", "replace")
            return _b64.b64encode(data.encode()).decode()
        except Exception as e:
            return "error: %s" % e

    def hash(self, data: str, algorithm: str = "sha256") -> str:
        """Compute hash (md5, sha1, sha256)."""
        import hashlib
        try:
            h = hashlib.new(algorithm)
            h.update(data.encode())
            return h.hexdigest()
        except ValueError:
            return "error: unknown algorithm: %s" % algorithm
        except Exception as e:
            return "error: %s" % e

    def random(self, type: str = "string", length: int = 16) -> str:
        """Generate random values."""
        import secrets
        import string
        if type == "hex":
            return secrets.token_hex(length)
        if type == "url":
            return secrets.token_urlsafe(length)
        if type == "int":
            return str(secrets.randbelow(10 ** length))
        chars = string.ascii_letters + string.digits
        return "".join(secrets.choice(chars) for _ in range(length))

    def datetime(self, action: str = "now", timezone: str = "") -> str:
        """Current date/time operations."""
        import time
        if action == "now":
            if timezone:
                os.environ["TZ"] = timezone
                time.tzset()
            return time.strftime("%Y-%m-%d %H:%M:%S %Z")
        if action == "epoch":
            return str(int(time.time()))
        if action == "format":
            return time.strftime("%Y-%m-%d %H:%M:%S")
        return "error: unknown action: %s" % action

    def regex(self, pattern: str, text: str, replace: str = "") -> str:
        """Apply regex to text."""
        import re
        try:
            if replace:
                return re.sub(pattern, replace, text)
            matches = re.findall(pattern, text)
            return "\n".join(matches) if matches else "no matches"
        except re.error as e:
            return "error: %s" % e
        except Exception as e:
            return "error: %s" % e

    def vision_analyze(self, image: str, question: str) -> str:
        return ("error: no_vision_provider: configure a vision-capable provider "
                "and set vision.model")

    def image_generate(self, prompt: str, size: str = "") -> str:
        return ("error: no_image_provider: configure an image endpoint and set "
                "image.model")

    def text_to_speech(self, text: str, rate: float = 0.0) -> str:
        if not _which("termux-tts-speak"):
            return ("error: termux_api_missing: install the Termux:API app from "
                    "F-Droid (the CLI package alone is not enough)")
        cmd = ["termux-tts-speak", text]
        if rate:
            cmd += ["-r", str(rate)]
        try:
            subprocess.run(cmd, timeout=30, capture_output=True)
        except Exception as e:
            return "error: tts_failed: %s" % e
        return "spoken"

    # -- control -----------------------------------------------------------
    def clarify(self, question: str, choices=None) -> str:
        if self.ui is None:
            return "error: no_interactive_channel"
        return self.ui.ask(question, choices)


def _which(binary: str) -> bool:
    from shutil import which
    return which(binary) is not None


def shell_quote(value: str) -> str:
    return shlex.quote(value)
