"""Telegram surface — a long-poll client built on urllib.

No dependency on python-telegram-bot: the Bot API is plain JSON over HTTPS.

Two failure layers, deliberately separate:
  * the socket drops            -> this module reconnects with exponential backoff
  * the whole process is killed -> the supervisor restarts it (see supervisor.py)

Turns are handled sequentially. A long turn does not lose messages: Telegram
holds updates until they are fetched with a later offset.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from ..llm import USER_AGENT

API = "https://api.telegram.org/bot%s/%s"
MAX_MESSAGE = 4096
TYPING_REFRESH = 4.0


class TelegramError(RuntimeError):
    pass


class TelegramAPI:
    """Thin, stateless wrapper over the Bot API."""

    def __init__(self, token: str, timeout: int = 60) -> None:
        self.token = token
        self.timeout = int(timeout)

    def _call(self, method: str, params: dict | None = None, timeout: int = 0):
        url = API % (self.token, method)
        data = None
        if params:
            data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = e.read(600).decode("utf-8", "replace")
            raise TelegramError("HTTP %s: %s" % (e.code, " ".join(body.split())[:200]))
        except urllib.error.URLError as e:
            raise TelegramError("network: %s" % e.reason)
        except ValueError as e:
            raise TelegramError("bad response: %s" % e)
        if not payload.get("ok"):
            raise TelegramError("api: %s" % payload.get("description", payload))
        return payload.get("result")

    def get_me(self):
        return self._call("getMe", timeout=20)

    def get_updates(self, offset: int = 0, timeout: int = 50):
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset
        return self._call("getUpdates", params, timeout=timeout + 15) or []

    def send_message(self, chat_id, text: str, reply_to=None):
        if not text:
            return None
        for chunk in split_message(text):
            params = {"chat_id": chat_id, "text": chunk,
                      "disable_web_page_preview": True}
            if reply_to:
                params["reply_to_message_id"] = reply_to
                reply_to = None
            self._call("sendMessage", params, timeout=30)
        return True

    def send_action(self, chat_id, action: str = "typing"):
        try:
            self._call("sendChatAction", {"chat_id": chat_id, "action": action},
                       timeout=15)
        except TelegramError:
            pass

    def set_commands(self, commands: list) -> bool:
        try:
            self._call("setMyCommands", {"commands": commands}, timeout=20)
            return True
        except TelegramError:
            return False


def split_message(text: str, limit: int = MAX_MESSAGE) -> list:
    """Split on paragraph, then line, then hard — Telegram's 4096-char cap."""
    if len(text) <= limit:
        return [text]
    out = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        out.append(remaining)
    return out


COMMANDS = [
    {"command": "help", "description": "list commands"},
    {"command": "new", "description": "start a fresh session"},
    {"command": "sessions", "description": "list recent sessions"},
    {"command": "status", "description": "providers, keys and usage"},
    {"command": "usage", "description": "token and request usage"},
    {"command": "memory", "description": "search memories"},
    {"command": "remember", "description": "save a memory"},
    {"command": "skills", "description": "list skills"},
    {"command": "tools", "description": "list tools"},
    {"command": "todo", "description": "task list"},
    {"command": "team", "description": "recent notes from all sessions"},
]


class TelegramSurface:
    """Polls Telegram and drives one agent session per chat."""

    def __init__(self, cfg: dict, build, log=None) -> None:
        self.cfg = cfg
        self.build = build              # callable() -> (agent, store, pool, tools, ui)
        self.log = log or (lambda *_: None)
        self.api = TelegramAPI(cfg.get("telegram_token", ""))
        self.offset = 0
        self.sessions: dict = {}
        self._cache: dict = {}
        self._stop = False
        self._allow = {str(x).strip() for x in (cfg.get("telegram_allowlist") or [])
                       if str(x).strip()}

    # -- lifecycle ---------------------------------------------------------
    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        """Poll forever, reconnecting with backoff. Returns only on stop()."""
        me = None
        try:
            me = self.api.get_me()
            self.log("telegram: connected as @%s" % me.get("username"))
        except TelegramError as e:
            self.log("telegram: cannot reach the API (%s)" % e)
            return
        self.api.set_commands(COMMANDS)

        delay = 2.0
        while not self._stop:
            try:
                updates = self.api.get_updates(self.offset, timeout=50)
                delay = 2.0                       # a good poll resets the backoff
            except TelegramError as e:
                self.log("telegram: poll failed (%s); retrying in %.0fs" % (e, delay))
                self._sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            for update in updates:
                self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
                try:
                    self._handle(update)
                except Exception as e:
                    self.log("telegram: handler error: %s: %s" % (type(e).__name__, e))

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop and time.time() < end:
            time.sleep(0.25)

    # -- message handling --------------------------------------------------
    def _handle(self, update: dict) -> None:
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        user = message.get("from") or {}
        user_id = str(user.get("id") or "")
        if not text or chat_id is None:
            return

        if self._allow and user_id not in self._allow:
            self.api.send_message(chat_id, "This bot is private.")
            self.log("telegram: refused user %s" % user_id)
            return

        if text.startswith("/"):
            if self._command(chat_id, text):
                return

        agent, store, pool, tools, ui = self._session(chat_id)
        sid = self.sessions[str(chat_id)]
        self.api.send_action(chat_id)
        try:
            answer = agent.turn(sid, text)
        except Exception as e:
            # Never send raw exceptions (with errno, tracebacks, etc.) to users
            error_name = type(e).__name__
            self.log("telegram: agent error: %s: %s" % (error_name, e))
            answer = "Sorry, something went wrong. Please try again."
        self.api.send_message(chat_id, answer or "(no answer)")

    def _session(self, chat_id):
        """Cache one agent per chat, keyed by STRING id throughout.

        Keying `sessions` by str but reading it with the int chat id raised
        KeyError on every message.
        """
        key = str(chat_id)
        cached = self._cache.get(key)
        if cached is None:
            agent, store, pool, tools, ui = self.build()
            sid = "tg-%s" % key
            store.ensure_session(sid, self.cfg.get("model", ""))
            self.sessions[key] = sid
            cached = (agent, store, pool, tools, ui)
            self._cache[key] = cached
        return cached

    def _command(self, chat_id, text: str) -> bool:
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        if cmd in ("start", "help"):
            self.api.send_message(chat_id, "\n".join(
                "/%s — %s" % (c["command"], c["description"]) for c in COMMANDS))
            return True
        if cmd == "new":
            key = str(chat_id)
            from .. import __main__ as main_mod
            sid = main_mod.new_session_id()
            agent, store, pool, tools, ui = self._session(chat_id)
            self.sessions[key] = sid
            store.ensure_session(sid, self.cfg.get("model", ""))
            self.api.send_message(chat_id, "new session: %s" % sid)
            return True

        agent, store, pool, tools, ui = self._session(chat_id)
        sid = self.sessions[str(chat_id)]

        if cmd == "sessions":
            rows = store.sessions()
            self.api.send_message(chat_id, "\n".join(
                "%s  %s" % (r["id"][:28], r["title"] or "") for r in rows) or "none")
        elif cmd == "status":
            health = pool.health()
            lines = ["FHONE status", "  session  %s" % sid,
                     "  model    %s" % (self.cfg.get("model") or "(unset)")]
            for p in health["providers"]:
                lines.append("  %-14s %s" % (p["id"], p["state"]))
            lines.append("  keys     %d ok, %d cooling, %d dead"
                         % (health["ok"], health["cooldown"], health["dead"]))
            self.api.send_message(chat_id, "\n".join(lines))
        elif cmd == "usage":
            rows = store.usage_today()
            self.api.send_message(chat_id, "\n".join(
                "%s requests=%s tokens=%s" % (r["provider_id"], r["r"],
                                              (r["p"] or 0) + (r["c"] or 0))
                for r in rows) or "no usage today")
        elif cmd == "memory":
            rows = store.memory_search(arg) if arg else store.memory_list()
            self.api.send_message(chat_id, "\n".join(
                "- %s" % (r["content"] if "content" in r.keys() else "") for r in rows)
                or "no memories")
        elif cmd == "remember":
            if arg:
                store.memory_add(arg, "fact")
                self.api.send_message(chat_id, "saved")
            else:
                self.api.send_message(chat_id, "usage: /remember <text>")
        elif cmd == "skills":
            from .. import skills as skills_mod
            self.api.send_message(chat_id, skills_mod.list_skills(self.cfg, query=arg))
        elif cmd == "tools":
            self.api.send_message(chat_id, ", ".join(tools.names()))
        elif cmd == "todo":
            self.api.send_message(chat_id, tools.todo_list("add" if arg else "list",
                                                           text=arg))
        elif cmd == "team":
            rows = store.team_feed()
            self.api.send_message(chat_id, "\n".join(
                "[%s] %s" % (r["agent_id"] or "-", r["content"][:90]) for r in rows)
                or "no team notes yet")
        else:
            return False
        return True
