"""The turn loop. This is the entire agent.

No framework, no base classes: append the user turn, build a bounded context,
call the model, run any tools it asks for, repeat until it answers.
"""
from __future__ import annotations

from . import context


class Agent:
    def __init__(self, cfg: dict, store, client, tools, ui) -> None:
        self.cfg = cfg
        self.store = store
        self.client = client
        self.tools = tools
        self.ui = ui

    def turn(self, sid: str, user_text: str) -> str:
        st = self.store
        st.ensure_session(sid, self.cfg["model"])

        # Fold old history into the summary before it can grow the window.
        try:
            if context.maybe_summarize(st, sid, self.cfg, self.client):
                if self.ui:
                    self.ui.note("  (summarized older history)")
        except Exception:
            pass

        st.add(sid, "user", user_text)

        max_rounds = int(self.cfg.get("max_tool_rounds", 8))
        for _ in range(max_rounds):
            messages = context.build(st, sid, self.cfg)
            result = self.client.chat(
                messages,
                tools=self.tools.schemas(),
                model=self.cfg["model"],
                max_tokens=int(self.cfg["max_tokens"]),
                temperature=float(self.cfg["temperature"]),
                stream=True,
                on_delta=(self.ui.delta if self.ui else None),
            )
            content = result.get("content") or ""
            calls = result.get("tool_calls") or []
            if self.ui:
                self.ui.end_stream()

            st.add(sid, "assistant", content or None, tool_calls=calls or None)

            if not calls:
                return content

            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                args = fn.get("arguments") or "{}"
                if self.ui:
                    self.ui.tool(name, args)
                output = self.tools.dispatch(name, args)
                if self.ui:
                    self.ui.note("  <- %s" % output.splitlines()[0][:120] if output else "  <- (empty)")
                st.add(sid, "tool", output, tool_call_id=call.get("id"), name=name)

        return "(stopped: exceeded max_tool_rounds)"
