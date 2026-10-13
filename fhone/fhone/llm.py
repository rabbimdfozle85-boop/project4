"""The only inference network code in FHONE.

Standard library only. One reused connection per host, SSE streaming with
tool-call reassembly, retry with backoff, gzip on non-streaming bodies only.

Three wire styles are supported, all discovered by testing real endpoints:

  openai      POST {base}/chat/completions   {model, messages, tools, stream}
              OpenRouter, Groq, Pollinations, most others.
  gemini      POST {base}/models/{model}:generateContent?key=KEY
              {contents:[{parts:[{text}]}]}
  apifreellm  POST {base}/chat               {message} -> {response}

Two hard-won implementation details, both from live probing:

  * A browser-like User-Agent is REQUIRED. The default ``Python-urllib/3.x`` UA
    is blocked by Cloudflare on several endpoints (error 1010 on Groq and
    ApiFreeLLM until the UA was changed).
  * Tool-call arguments arrive split across many SSE deltas and must be
    concatenated by index, never replaced. A naive parser silently truncates
    every tool call to its first fragment.
"""
from __future__ import annotations

import gzip
import http.client
import json
import random
import ssl
import time
from urllib.parse import urlparse

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}
AUTH_STATUS = {401, 403}


class LLMError(RuntimeError):
    """A request failed and retrying will not help."""


class AuthError(LLMError):
    """401/403 — the key is bad. Quarantine it."""


class RateLimitError(LLMError):
    """429 — back off and try another key."""


class TransientError(LLMError):
    """5xx / socket failure — worth retrying."""


class Client:
    """One client per provider. Reuses its connection across calls."""

    def __init__(self, base_url: str, api_key: str = "", api_style: str = "openai",
                 headers: dict | None = None, timeout: int = 120,
                 max_retries: int = 4) -> None:
        url = urlparse(base_url if "://" in base_url else "https://" + base_url)
        self.scheme = url.scheme or "https"
        self.host = url.hostname or ""
        self.port = url.port or (443 if self.scheme == "https" else 80)
        self.prefix = url.path.rstrip("/")
        self.api_key = api_key or ""
        self.api_style = api_style or "openai"
        self.extra_headers = dict(headers or {})
        self.timeout = int(timeout)
        self.max_retries = max(1, int(max_retries))
        self._ssl_ctx = ssl.create_default_context()
        self._conn = None

    # -- connection --------------------------------------------------------
    def _connection(self):
        if self._conn is None:
            if self.scheme == "https":
                self._conn = http.client.HTTPSConnection(
                    self.host, self.port, timeout=self.timeout, context=self._ssl_ctx)
            else:
                self._conn = http.client.HTTPConnection(
                    self.host, self.port, timeout=self.timeout)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # -- request -----------------------------------------------------------
    def _headers(self, accept_gzip: bool) -> dict:
        h = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        if self.api_key and self.api_style != "gemini":
            h["Authorization"] = "Bearer " + self.api_key
        if accept_gzip:
            h["Accept-Encoding"] = "gzip"
        h.update(self.extra_headers)
        return h

    def _post(self, path: str, payload: dict, *, accept_gzip: bool):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        conn = self._connection()
        try:
            conn.request("POST", self.prefix + path, body=body,
                         headers=self._headers(accept_gzip))
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            self.close()
            raise TransientError("connection failed: %s" % e)
        if resp.status >= 400:
            detail = resp.read(4000).decode("utf-8", "replace")
            # Drain whatever is left and drop the connection. An unread body on
            # a keep-alive socket corrupts the NEXT request made with this
            # client — the leftover bytes are read as the next response.
            try:
                while resp.read(65536):
                    pass
            except Exception:
                pass
            self.close()
            if resp.status in AUTH_STATUS:
                raise AuthError("HTTP %s: %s" % (resp.status, _short(detail)))
            if resp.status == 429:
                raise RateLimitError("HTTP 429: %s" % _short(detail))
            if resp.status in RETRY_STATUS:
                raise TransientError("HTTP %s: %s" % (resp.status, _short(detail)))
            raise LLMError("HTTP %s: %s" % (resp.status, _short(detail, 300)))
        return resp

    def _with_retry(self, fn):
        delay = 0.5
        last = None
        for attempt in range(self.max_retries):
            try:
                return fn()
            except AuthError:
                raise
            except (TransientError, OSError, http.client.HTTPException, ValueError) as e:
                last = e
                self.close()
            if attempt < self.max_retries - 1:
                time.sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 8.0)
        raise TransientError("failed after %d attempts: %s" % (self.max_retries, last))

    # -- public API --------------------------------------------------------
    def chat(self, messages, tools=None, *, model, max_tokens=1024,
             temperature=0.7, stream=True, on_delta=None) -> dict:
        """Returns {role, content, tool_calls, finish_reason, usage}."""
        if self.api_style == "gemini":
            return self._with_retry(
                lambda: self._gemini(messages, model, max_tokens, temperature))
        if self.api_style == "apifreellm":
            return self._with_retry(lambda: self._apifreellm(messages))
        if self.api_style == "custom":
            return self._with_retry(
                lambda: self._openai(messages, tools, model, max_tokens,
                                     temperature, stream, on_delta))
        return self._with_retry(
            lambda: self._openai(messages, tools, model, max_tokens,
                                 temperature, stream, on_delta))

    # -- openai style ------------------------------------------------------
    def _openai(self, messages, tools, model, max_tokens, temperature,
                stream, on_delta) -> dict:
        payload = {
            "model": model,
            "messages": _sanitize_messages(messages),
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream"] = True
            # Ask for a usage chunk so streamed turns are accounted for. Not
            # every provider accepts the field, so a rejection retries without
            # it rather than failing the turn.
            payload["stream_options"] = {"include_usage": True}
            try:
                return self._stream_openai(payload, on_delta)
            except LLMError as e:
                if "stream_options" in str(e) or "unknown" in str(e).lower():
                    payload.pop("stream_options", None)
                    return self._stream_openai(payload, on_delta)
                raise
        return self._once_openai(payload)

    def _stream_openai(self, payload: dict, on_delta) -> dict:
        resp = self._post("/chat/completions", payload, accept_gzip=False)
        parts: list[str] = []
        calls: dict = {}
        finish = None
        usage = None
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    parts.append(piece)
                    if on_delta:
                        on_delta(piece)
                for tc in (delta.get("tool_calls") or []):
                    idx = tc.get("index", 0)
                    slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
        finally:
            try:
                resp.read()
            except Exception:
                self.close()
        return _normalize("".join(parts), calls, finish, usage)

    def _once_openai(self, payload: dict) -> dict:
        resp = self._post("/chat/completions", payload, accept_gzip=True)
        raw = resp.read()
        if (resp.getheader("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        obj = json.loads(raw.decode("utf-8", "replace"))
        choices = obj.get("choices") or [{}]
        msg = choices[0].get("message") or {}
        calls = {}
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            calls[i] = {"id": tc.get("id", ""), "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", "")}
        return _normalize(msg.get("content") or "", calls,
                          choices[0].get("finish_reason"), obj.get("usage"))

    # -- gemini style ------------------------------------------------------
    def _gemini(self, messages, model, max_tokens, temperature) -> dict:
        contents, system = _to_gemini(messages)
        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": int(max_tokens),
                "temperature": float(temperature),
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        path = "/models/%s:generateContent?key=%s" % (model, self.api_key)
        resp = self._post(path, payload, accept_gzip=True)
        raw = resp.read()
        if (resp.getheader("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        obj = json.loads(raw.decode("utf-8", "replace"))
        try:
            text = obj["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            text = ""
        usage = obj.get("usageMetadata")
        return {"role": "assistant", "content": text or None, "tool_calls": None,
                "finish_reason": None, "usage": usage}

    # -- apifreellm style --------------------------------------------------
    def _apifreellm(self, messages) -> dict:
        prompt = "\n".join(
            "%s: %s" % (m.get("role", "user"), m.get("content") or "")
            for m in messages if m.get("content"))
        resp = self._post("/chat", {"message": prompt}, accept_gzip=True)
        raw = resp.read()
        if (resp.getheader("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        obj = json.loads(raw.decode("utf-8", "replace"))
        if not obj.get("success", True):
            raise LLMError("provider error: %s" % _short(obj.get("error", obj), 200))
        return {"role": "assistant", "content": obj.get("response"),
                "tool_calls": None, "finish_reason": "stop", "usage": None}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _short(text, n: int = 200) -> str:
    return " ".join(str(text).split())[:n]


def _normalize(content: str, calls: dict, finish, usage) -> dict:
    tool_calls = []
    for idx in sorted(calls):
        slot = calls[idx]
        if not slot["name"]:
            continue
        tool_calls.append({
            "id": slot["id"] or "call_%d" % idx,
            "type": "function",
            "function": {
                "name": slot["name"],
                "arguments": slot["arguments"] or "{}",
            },
        })
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": tool_calls or None,
        "finish_reason": finish,
        "usage": usage,
    }


def _sanitize_messages(messages) -> list:
    """Strip FHONE-internal keys; providers reject unknown message fields."""
    out = []
    for m in messages:
        clean = {"role": m.get("role", "user")}
        for k in ("content", "tool_calls", "tool_call_id", "name"):
            if m.get(k) is not None:
                clean[k] = m[k]
        if clean["role"] == "assistant" and not clean.get("content") \
                and not clean.get("tool_calls"):
            clean["content"] = ""
        out.append(clean)
    return out


def _to_gemini(messages) -> tuple:
    """(contents, system_text) — Gemini has no system role."""
    contents = []
    system_parts = []
    for m in messages:
        role = m.get("role")
        text = m.get("content") or ""
        if role == "system":
            system_parts.append(text)
            continue
        if role == "tool":
            text = "tool result (%s): %s" % (m.get("name") or "?", text)
            role = "user"
        elif role == "assistant":
            role = "model"
        elif role != "user":
            role = "user"
        if not text:
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    if not contents:
        contents = [{"role": "user", "parts": [{"text": "hello"}]}]
    return contents, "\n".join(system_parts)
