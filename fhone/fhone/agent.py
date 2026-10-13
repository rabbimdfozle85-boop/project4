"""The turn loop. This is the entire agent.

No framework: append the user turn, build a bounded context, call the model,
run any tools it asks for, repeat until it answers.
"""
from __future__ import annotations

from . import context


class Agent:
    def __init__(self, cfg: dict, store, pool, tools, ui=None) -> None:
        self.cfg = cfg
        self.store = store
        self.pool = pool
        self.tools = tools
        self.ui = ui
        self.session_id = ""
        self.agent_id = "main"

    def turn(self, sid: str, user_text: str, *, on_delta=None) -> str:
        st = self.store
        self.session_id = sid
        self.tools.session_id = sid
        self.tools.agent_id = self.agent_id
        st.ensure_session(sid, self.cfg.get("model", ""))
        self.tools.ui = self.ui

        # Fold old history into the summary before it can grow the window.
        try:
            if context.maybe_summarize(st, sid, self.cfg, self.pool):
                if self.ui:
                    self.ui.note("  (summarized older history)")
        except Exception as e:
            if self.ui:
                self.ui.note("  ! summarize failed: %s: %s" % (type(e).__name__, e))

        st.add(sid, "user", user_text)

        extra_system = self._memory_block(sid)
        max_rounds = int(self.cfg.get("max_tool_rounds", 8))

        try:
            return self._run_turn(sid, user_text, extra_system, max_rounds, on_delta)
        except KeyboardInterrupt:
            st.mark_interrupted(sid)
            raise
        except Exception as e:
            st.mark_interrupted(sid)
            raise

    def _run_turn(self, sid: str, user_text: str, extra_system: list,
                  max_rounds: int, on_delta) -> str:
        st = self.store
        for _ in range(max_rounds):
            messages = context.build(st, sid, self.cfg, extra_system)
            result = self.pool.chat(
                messages, self.tools.schemas(), model=self.cfg.get("model", ""),
                stream=True, on_delta=on_delta or (self.ui.delta if self.ui else None))
            content = result.get("content") or ""
            calls = result.get("tool_calls") or []
            if self.ui:
                self.ui.end_stream()

            st.add(sid, "assistant", content or None, tool_calls=calls or None)
            if not calls:
                self._publish(sid, user_text, content)
                return content

            # Run tool calls in parallel for lower latency
            from concurrent.futures import ThreadPoolExecutor, as_completed
            outputs = {}
            with ThreadPoolExecutor(max_workers=min(len(calls), 4)) as pool:
                futures = {}
                for call in calls:
                    fn = call.get("function") or {}
                    name = fn.get("name") or ""
                    args = fn.get("arguments") or "{}"
                    if self.ui:
                        self.ui.tool(name, args)
                    futures[pool.submit(self.tools.dispatch, name, args)] = call
                for future in as_completed(futures):
                    call = futures[future]
                    try:
                        output = future.result()
                    except Exception as e:
                        output = "error: %s: %s" % (type(e).__name__, e)
                    outputs[call.get("id")] = output
                    if self.ui:
                        first_line = output.splitlines()[0][:120] if output else "(empty)"
                        self.ui.note("  <- " + first_line)
            for call in calls:
                st.add(sid, "tool", outputs.get(call.get("id"), "error: no output"),
                       tool_call_id=call.get("id"), name=call.get("function", {}).get("name", ""))

        return "(stopped: exceeded max_tool_rounds)"

    # -- helpers -----------------------------------------------------------
    def _memory_block(self, sid: str):
        """Progressive retrieval: surface only memories matching the latest turn.

        Nothing is loaded wholesale — that is the whole point of querying rather
        than carrying memory in context.
        """
        rows = self.store.window(sid, 1)
        if not rows:
            return []
        text = (rows[-1]["content"] or "").strip()
        if len(text) < 8:
            return []
        try:
            hits = self.store.memory_search(text[:200], limit=5)
        except Exception:
            return []
        if not hits:
            return []
        return ["Relevant memories (retrieved, not exhaustive):\n"
                + "\n".join("- " + h["content"] for h in hits)]

    def _publish(self, sid: str, user_text: str, answer: str) -> None:
        """Flush a durable finding to the shared brain after N turns."""
        every = int(self.cfg.get("team_flush_every", 10))
        if every <= 0:
            return
        try:
            if self.store.count(sid) % (every * 2) == 0:
                self.store.note_add(
                    "session %s: %s" % (sid, (answer or "")[:200]),
                    session_id=sid, agent_id=self.agent_id, kind="episode")
        except Exception as e:
            if self.ui:
                self.ui.note("  ! publish failed: %s: %s" % (type(e).__name__, e))
