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
        self._usage: dict = {}
        self._context_fill: float = 0.0

    @property
    def usage(self) -> dict:
        return dict(self._usage)

    @property
    def context_fill(self) -> float:
        return self._context_fill

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
            self._context_fill = self._calc_fill(st, sid)
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
            usage = result.get("usage") or {}
            if usage:
                self._merge_usage(usage)
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

    def _merge_usage(self, usage: dict) -> None:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = usage.get(k)
            if isinstance(v, (int, float)):
                self._usage[k] = self._usage.get(k, 0) + int(v)

    def _calc_fill(self, st, sid: str) -> float:
        """Percentage of the context token budget currently used."""
        budget = int(self.cfg.get("context_token_budget", 6000))
        if budget <= 0:
            return 0.0
        rows = st.window(sid, int(self.cfg.get("context_window", 12)))
        total = sum(context.row_tokens(r) for r in rows)
        return min(100.0, (total / budget) * 100)

    def usage_display(self) -> str:
        parts = []
        if "prompt_tokens" in self._usage:
            parts.append("prompt: %dk" % (self._usage["prompt_tokens"] // 1024))
        if "completion_tokens" in self._usage:
            parts.append("completion: %dk" % (self._usage["completion_tokens"] // 1024))
        if "total_tokens" in self._usage:
            parts.append("total: %dk" % (self._usage["total_tokens"] // 1024))
        if not parts:
            parts.append("tokens: 0")
        return " · ".join(parts)
