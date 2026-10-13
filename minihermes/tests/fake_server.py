"""A canned OpenAI-compatible endpoint, for offline testing.

Behaviour:
  * if the incoming conversation already contains a tool result -> stream a
    final text answer
  * otherwise -> stream a tool call whose ARGUMENTS ARE SPLIT ACROSS TWO
    DELTAS, which is exactly how real providers send them and the case most
    naive SSE parsers get wrong.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _frame(delta: dict, finish=None) -> str:
    payload = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return "data: " + json.dumps(payload) + "\n\n"


def _sse(frames: list) -> bytes:
    return ("".join(frames) + "data: [DONE]\n\n").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence test noise
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        messages = body.get("messages") or []
        streaming = bool(body.get("stream"))

        saw_tool_result = any(m.get("role") == "tool" for m in messages)
        saw_assistant_tool_call = any(
            m.get("role") == "assistant" and m.get("tool_calls") for m in messages)

        if saw_tool_result:
            text = "Done. The shell tool reported: %s" % _last_tool(messages)
            frames = [
                _frame({"role": "assistant", "content": "Done. "}),
                _frame({"content": "The shell tool reported: "}),
                _frame({"content": _last_tool(messages)}),
                _frame({}, finish="stop"),
            ]
        elif saw_assistant_tool_call:
            frames = [_frame({"content": "(no more tools)"}, finish="stop")]
        else:
            frames = [
                _frame({"role": "assistant", "tool_calls": [
                    {"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "run_shell", "arguments": '{"comm'}}]}),
                _frame({"tool_calls": [
                    {"index": 0, "function": {"arguments": 'and": "echo hi"}'}}]}),
                _frame({}, finish="tool_calls"),
            ]

        if streaming:
            payload = _sse(frames)
            ctype = "text/event-stream"
        else:
            obj = {
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant",
                                "content": "SUM: prior turns were about echo."},
                    "finish_reason": "stop",
                }]
            }
            payload = json.dumps(obj).encode("utf-8")
            ctype = "application/json"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _last_tool(messages) -> str:
    for m in reversed(messages):
        if m.get("role") == "tool":
            return (m.get("content") or "").strip().replace("\n", " ")[:80]
    return "(none)"


def serve(port: int = 0):
    """Start the server on a background thread; return (httpd, base_url)."""
    import threading

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, "http://127.0.0.1:%d/v1" % httpd.server_address[1]


if __name__ == "__main__":
    httpd, url = serve(8099)
    print("fake llm at", url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
