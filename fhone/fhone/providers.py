"""Provider registry, key pools, rotation, fallback and usage accounting.

Nothing is hardcoded: providers, keys, wire style and priority all live in
SQLite and are editable at runtime. The only built-in is the Nous endpoint,
seeded on first run because FHONE signs in there before anything else.

Selection order: providers by priority, keys by (status, fail_count, last_used).
A 429 cools a key down; a 401/403 kills it; a 5xx retries inside the client and
then moves on. Exhausting everything returns a clear error, never a silent hang.
"""
from __future__ import annotations

import json
import time

from . import llm

# A provider with no keys is usable (keyless), e.g. Pollinations.
KEYLESS_OK = {"pollinations"}

DEFAULT_PROVIDERS = [
    # (id, label, base_url, style, model, priority)
    ("nous", "Nous Portal", "https://inference-api.nousresearch.com/v1",
     "openai", "", 10),
    ("pollinations", "Pollinations (keyless)", "https://text.pollinations.ai/openai",
     "openai", "openai", 900),
]

COOLDOWN_BASE = 30        # seconds; doubles per consecutive failure, capped
COOLDOWN_MAX = 900


class PoolError(RuntimeError):
    """No provider could serve the request."""


class ProviderPool:
    def __init__(self, store, cfg: dict) -> None:
        self.store = store
        self.cfg = cfg
        self._clients: dict = {}
        self._no_model: set = set()   # (provider_id, model) pairs that 404'd

    # -- setup -------------------------------------------------------------
    def seed_defaults(self) -> None:
        """Intentionally does nothing. No providers are pre-loaded.

        The user must configure their own providers during setup.
        This is a deliberate design choice: the platform starts clean.
        """
        return None

    def add_provider(self, pid: str, base_url: str, *, label: str = "",
                     api_style: str = "openai", model: str = "",
                     headers: dict | None = None, priority: int = 100,
                     models: list | None = None) -> None:
        self.store.provider_add(pid, base_url, label=label, api_style=api_style,
                                default_model=model, headers=headers,
                                priority=priority, models=models)

    def set_provider_models(self, pid: str, models: list) -> None:
        """Declare which models a provider serves.

        Empty means 'any model'. A provider whose list is non-empty and does not
        contain the requested model is never asked for it.
        """
        row = self.store.provider_get(pid)
        if row is None:
            raise PoolError("no such provider: %s" % pid)
        self.store.provider_add(pid, row["base_url"], label=row.get("label") or "",
                                api_style=row["api_style"],
                                default_model=row.get("default_model") or "",
                                priority=row["priority"], models=models)

    def add_key(self, provider_id: str, key: str, label: str = "") -> int:
        return self.store.key_add(provider_id, key, label)

    def remove_key(self, key_id: int) -> None:
        self.store.key_remove(key_id)

    # -- selection ---------------------------------------------------------
    def _client_for(self, prow, key: str) -> llm.Client:
        # Key on the full connection identity: editing a provider's URL or wire
        # style must not keep serving the old, cached connection.
        cache_key = (prow["id"], prow["base_url"], prow["api_style"],
                     prow["headers_json"], key)
        client = self._clients.get(cache_key)
        if client is None:
            headers = {}
            if prow["headers_json"]:
                try:
                    headers = json.loads(prow["headers_json"])
                except ValueError:
                    headers = {}
            client = llm.Client(
                prow["base_url"], key, api_style=prow["api_style"], headers=headers,
                timeout=self.cfg.get("request_timeout", 120),
                max_retries=self.cfg.get("max_retries", 4))
            self._clients[cache_key] = client
        return client

    def candidates(self):
        """Yield (provider_row, key_row_or_None) in preference order."""
        for prow in self.store.providers(enabled_only=True):
            keys = self.store.keys_for(prow["id"])
            if not keys:
                if prow["id"] in KEYLESS_OK:
                    yield prow, None
                continue
            for krow in keys:
                if self.store.key_healthy(krow):
                    yield prow, krow

    def close(self) -> None:
        for c in self._clients.values():
            c.close()
        self._clients.clear()

    # -- the call ----------------------------------------------------------
    def _supports(self, prow, model: str) -> bool:
        """Does this provider serve the requested model?

        An empty `models` list means 'any model'. A non-empty list is an explicit
        allow-list: Pollutions, for example, cannot serve an OpenRouter model id.
        """
        allowed = prow.get("models") or []
        return not allowed or model in allowed

    def chat(self, messages, tools=None, *, model: str = "", on_delta=None,
             stream: bool = True) -> dict:
        """Try providers in order until one answers. Raises PoolError if none can.

        Model-aware: a provider that does not serve the requested model is never
        asked for it. A global model override would otherwise hand a keyless
        fallback an OpenRouter model id and get a 404 on every call.
        """
        attempts = []
        tried = 0
        candidates = list(self.candidates())
        serving = [c for c in candidates if self._supports(c[0], model)] or candidates
        for prow, krow in serving:
            tried += 1
            key = krow["key"] if krow else ""
            use_model = model or prow["default_model"] or self.cfg.get("model", "")
            if not use_model:
                attempts.append("%s: no model configured" % prow["id"])
                continue
            if (prow["id"], use_model) in self._no_model:
                continue
            client = self._client_for(prow, key)
            try:
                result = client.chat(
                    messages, tools, model=use_model,
                    max_tokens=self.cfg.get("max_tokens", 1024),
                    temperature=self.cfg.get("temperature", 0.7),
                    stream=stream, on_delta=on_delta)
            except llm.AuthError as e:
                attempts.append("%s/%s: auth (%s)" % (prow["id"], _label(krow), e))
                if krow:
                    self.store.key_set_status(krow["id"], "dead")
                continue
            except llm.RateLimitError as e:
                attempts.append("%s/%s: rate limited" % (prow["id"], _label(krow)))
                if krow:
                    self._cooldown(krow)
                continue
            except llm.LLMError as e:
                if "model not found" in str(e).lower():
                    self._no_model.add((prow["id"], use_model))
                    attempts.append("%s/%s: does not serve %s"
                                    % (prow["id"], _label(krow), use_model))
                else:
                    attempts.append("%s/%s: %s" % (prow["id"], _label(krow), e))
                    if krow:
                        self.store.key_bump_fail(krow["id"])
                continue

            # success
            if krow:
                self.store.key_touch(krow["id"])
                self.store.key_set_status(krow["id"], "ok")
            usage = result.get("usage") or {}
            self.store.usage_add(
                prow["id"], krow["id"] if krow else 0,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0))
            result["_provider"] = prow["id"]
            result["_key_id"] = krow["id"] if krow else 0
            result["_model"] = use_model
            return result

        if tried == 0:
            raise PoolError("no usable provider or key configured")
        # Strip raw socket/errno noise — show a readable summary instead.
        clean = []
        for a in attempts:
            # Keep provider/key label, drop the raw error detail after ": "
            if ": " in a:
                label, _, detail = a.partition(": ")
                # Filter out raw connection errors
                if any(x in detail for x in ("Errno", "Connection reset", "Connection refused",
                                              "Name or service known", "Temporary failure",
                                              "Network is unreachable", "timed out")):
                    clean.append(label + ": connection failed")
                else:
                    clean.append(a)
            else:
                clean.append(a)
        raise PoolError("all providers failed:\n  " + "\n  ".join(clean))

    def _cooldown(self, krow) -> None:
        fails = int(krow["fail_count"] or 0) + 1
        delay = min(COOLDOWN_BASE * (2 ** (fails - 1)), COOLDOWN_MAX)
        self.store.key_set_status(krow["id"], "cooldown", cooldown_until=int(time.time()) + delay)
        self.store.key_bump_fail(krow["id"])

    # -- health ------------------------------------------------------------
    def health(self) -> dict:
        """Summary for the status bar and /status."""
        out = {"providers": [], "ok": 0, "cooldown": 0, "dead": 0, "total": 0}
        for prow in self.store.providers(enabled_only=True):
            keys = self.store.keys_for(prow["id"])
            if not keys and prow["id"] not in KEYLESS_OK:
                state = "no keys"
            elif not keys:
                state = "keyless"
                out["ok"] += 1
            else:
                healthy = sum(1 for k in keys if self.store.key_healthy(k))
                state = "%d/%d keys" % (healthy, len(keys))
                if healthy:
                    out["ok"] += 1
            for k in keys:
                out["total"] += 1
                if k["status"] == "dead":
                    out["dead"] += 1
                elif k["status"] == "cooldown":
                    out["cooldown"] += 1
            out["providers"].append({
                "id": prow["id"], "label": prow["label"], "state": state,
                "priority": prow["priority"], "style": prow["api_style"],
            })
        return out

    def probe(self, provider_id: str) -> list:
        """Live-check every key of one provider. Returns [(key_id, label, ok, detail)]."""
        prow = self.store.provider_get(provider_id)
        if prow is None:
            return []
        out = []
        for krow in self.store.keys_for(provider_id):
            client = self._client_for(prow, krow["key"])
            model = prow["default_model"] or self.cfg.get("model", "")
            try:
                if not model:
                    raise llm.LLMError("no model configured to probe with")
                client.chat([{"role": "user", "content": "reply with: ok"}],
                            model=model, max_tokens=8, stream=False)
                self.store.key_set_status(krow["id"], "ok")
                out.append((krow["id"], krow["label"], True, "ok"))
            except llm.AuthError as e:
                self.store.key_set_status(krow["id"], "dead")
                out.append((krow["id"], krow["label"], False, "auth: %s" % e))
            except llm.LLMError as e:
                self.store.key_bump_fail(krow["id"])
                out.append((krow["id"], krow["label"], False, str(e)[:120]))
        return out


def _label(krow) -> str:
    if krow is None:
        return "(keyless)"
    return krow["label"] or ("key#%d" % krow["id"])
