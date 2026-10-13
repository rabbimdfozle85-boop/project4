"""The only network code in Mini-Hermes.

Standard library only (http.client + ssl + gzip + json). Talks to any
OpenAI-compatible /chat/completions endpoint over ONE reused connection,
streams SSE, and retries transient failures with backoff.

Two deliberate choices worth knowing:
  * gzip is applied to non-streaming bodies only. On an SSE stream gzip
    usually adds buffering latency for no real gain, so it is off there.
  * the connection is kept alive between calls. On mobile data a fresh
    TCP+TLS handshake costs 300-800 ms per turn; reuse removes it.
"""
from __future__ import annotations

import gzip
import http.client
import json
import random
import ssl
import time
from urllib.parse import urlparse

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}


class LLMError(RuntimeError):
    """A request failed in a way the caller should not retry through."""


class _Transient(LLMError):
    """A failure worth retrying (connection drop, 429, 5xx)."""


class Client:
    def __init__(self, base: str, api_key: str, timeout: int = 120,
                 max_retries: int = 4) -> None:
        u = urlparse(base if "://" in base else "https://" + base)
        self.scheme = u.scheme or "https"
        self.host = u.hostname
        self.port = u.port or (443 if self.scheme == "https" else 80)
        self.prefix = u.path.rstrip("/")
        self.api_key = api_key or ""
        self.timeout = int(timeout)
        self.max_retries = max(1, int(max_retries))
        self._ssl_ctx = ssl.create_default_context()
        self._conn = None

    # -- connection reuse --------------------------------------------------
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

    def _headers(self, accept_gzip: bool) -> dict:
        h = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "User-Agent": "mini-hermes/0.1",
        }
        if accept_gzip:
            h["Accept-Encoding"] = "gzip"
        return h

    def _post(self, path: str, payload: dict, *, accept_gzip: bool):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        conn = self._connection()
        try:
            conn.request("POST", self.prefix + path, body=body,
                         headers=self._headers(accept_gzip))
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException):
            self.close()
            raise
        if resp.status in RETRY_STATUS:
            detail = resp.read(2000).decode("utf-8", "replace")
            self.close()
            raise _Transient("HTTP %s: %s" % (resp.status, detail))
        if resp.status >= 400:
            detail = resp.read(4000).decode("utf-8", "replace")
            raise LLMError("HTTP %s: %s" % (resp.status, detail))
        return resp

    def _with_retry(self, fn):
        delay = 0.5
        last = None
        for attempt in range(self.max_retries):
            try:
                return fn()
            except _Transient as e:
                last = e
            except (OSError, http.client.HTTPException) as e:
                last = e
                self.close()
            if attempt < self.max_retries - 1:
                time.sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 8.0)
        raise LLMError("request failed after %d attempts: %s" % (self.max_retries, last))

    # -- public API --------------------------------------------------------
    def chat(self, messages, tools=None, *, model, max_tokens=1024,
             temperature=0.7, stream=True, on_delta=None) -> dict:
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream"] = True
            return self._with_retry(lambda: self._stream(payload, on_delta))
        return self._with_retry(lambda: self._once(payload))

    # -- streaming ---------------------------------------------------------
    def _stream(self, payload: dict, on_delta) -> dict:
        resp = self._post("/chat/completions", payload, accept_gzip=False)
        content_parts: list[str] = []
        calls: dict = {}
        finish = None
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
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    content_parts.append(piece)
                    if on_delta:
                        on_delta(piece)
                # Tool-call arguments arrive split across many deltas; they must
                # be concatenated by index, not replaced.
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
                resp.read()  # drain so the keep-alive connection stays reusable
            except Exception:
                self.close()
        return self._normalize("".join(content_parts), calls, finish)

    @staticmethod
    def _normalize(content: str, calls: dict, finish) -> dict:
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
        }

    # -- non-streaming (gzip on) ------------------------------------------
    def _once(self, payload: dict) -> dict:
        resp = self._post("/chat/completions", payload, accept_gzip=True)
        raw = resp.read()
        if (resp.getheader("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        obj = json.loads(raw.decode("utf-8", "replace"))
        choices = obj.get("choices") or [{}]
        msg = choices[0].get("message") or {}
        return {
            "role": "assistant",
            "content": msg.get("content"),
            "tool_calls": msg.get("tool_calls"),
            "finish_reason": choices[0].get("finish_reason"),
        }
