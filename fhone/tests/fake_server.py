"""A canned OpenAI-compatible endpoint for offline testing.

Behaviours are selected by the requested model name, so one server can drive
rotation, fallback and retry tests:

  test-model     normal: tool call first, then a final answer
  test-429       always 429 (rate limit)
  test-401       always 401 (auth failure)
  test-500       always 500 (server error)
  test-plain     straight text, no tool call
  test-split     tool-call arguments split across MANY deltas

The split case is the one naive SSE parsers get wrong: arguments must be
concatenated by index, never replaced.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HITS: dict = {}


def _frame(delta: dict, finish=None) -> str:
    return "data: " + json.dumps(
        {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


def _sse(frames) -> bytes:
    return ("".join(frames) + "data: [DONE]\n\n").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}

        model = body.get("model") or "test-model"
        HITS[model] = HITS.get(model, 0) + 1
        HITS["_last_path"] = self.path
        HITS["_last_ua"] = self.headers.get("User-Agent", "")

        if model == "test-429":
            return self._err(429, {"error": {"message": "rate limited"}})
        if model == "test-401":
            return self._err(401, {"error": {"message": "invalid api key"}})
        if model in ("test-500", "test-boom"):
            return self._err(500, {"error": {"message": "server exploded"}})
        if model == "test-400-long":
            # A 4xx with a body far larger than a single read() window. If the
            # client does not drain it, the next request on the same keep-alive
            # connection reads leftover bytes and corrupts.
            return self._err(400, {"error": {"message": "x" * 40000}})
        if model == "test-plain-long":
            messages = body.get("messages") or []
            streaming = bool(body.get("stream"))
            frames = [_frame({"role": "assistant", "content": "y" * 40000}),
                      _frame({}, finish="stop")]
            payload = _sse(frames) if streaming else json.dumps(
                {"choices": [{"index": 0, "finish_reason": "stop",
                              "message": {"role": "assistant", "content": "y" * 40000}}]}
            ).encode()
            ctype = "text/event-stream" if streaming else "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        messages = body.get("messages") or []
        streaming = bool(body.get("stream"))
        saw_tool = any(m.get("role") == "tool" for m in messages)
        saw_call = any(m.get("role") == "assistant" and m.get("tool_calls")
                       for m in messages)

        if model == "test-plain":
            frames = [_frame({"role": "assistant", "content": "plain answer"}),
                      _frame({}, finish="stop")]
        elif saw_tool:
            frames = [_frame({"role": "assistant", "content": "Done. "}),
                      _frame({"content": "tool said: "}),
                      _frame({"content": _last_tool(messages)}),
                      _frame({}, finish="stop")]
        elif saw_call:
            frames = [_frame({"content": "(no more tools)"}, finish="stop")]
        elif model == "test-split":
            args = '{"command": "echo split-ok"}'
            frames = [_frame({"role": "assistant", "tool_calls": [
                {"index": 0, "id": "call_s", "type": "function",
                 "function": {"name": "terminal", "arguments": args[:5]}}]})]
            for i in range(5, len(args), 4):
                frames.append(_frame({"tool_calls": [
                    {"index": 0, "function": {"arguments": args[i:i + 4]}}]}))
            frames.append(_frame({}, finish="tool_calls"))
        else:
            frames = [
                _frame({"role": "assistant", "tool_calls": [
                    {"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "terminal", "arguments": '{"comm'}}]}),
                _frame({"tool_calls": [
                    {"index": 0, "function": {"arguments": 'and": "echo hi"}'}}]}),
                _frame({}, finish="tool_calls"),
            ]

        if streaming:
            # Add a usage frame before [DONE] so streaming usage is testable
            usage_frame = "data: " + json.dumps(
                {"usage": {"prompt_tokens": 11, "completion_tokens": 7}}) + "\n\n"
            payload = ("".join(frames) + usage_frame + "data: [DONE]\n\n").encode("utf-8")
            ctype = "text/event-stream"
        else:
            text = {"test-plain": "plain answer"}.get(
                model, "SUM: prior turns summarised.")
            obj = {"choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": text}}],
                   "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
            payload = json.dumps(obj).encode("utf-8")
            ctype = "application/json"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _err(self, code, obj):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _last_tool(messages) -> str:
    for m in reversed(messages):
        if m.get("role") == "tool":
            return (m.get("content") or "").strip().replace("\n", " ")[:80]
    return "(none)"


def serve(port: int = 0):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d/v1" % httpd.server_address[1]
