"""Build the message list for one API call, and keep it small.

Two knobs keep resident memory flat however long a session runs: a hard window
(the newest N messages) and a token budget. When the stored history grows well
past the window, the older slice is summarised once and folded into the
session's summary row.
"""
from __future__ import annotations

import json


def approx_tokens(text) -> int:
    """len/4 is within ~10% of a real tokenizer for English and costs nothing.

    Shipping a BPE tokenizer would add megabytes of tables for a number we only
    use to decide when to trim.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def row_tokens(row) -> int:
    n = approx_tokens(row["content"])
    if row["tool_calls"]:
        n += approx_tokens(row["tool_calls"])
    return n + 4


def to_api(row) -> dict:
    msg = {"role": row["role"]}
    if row["content"] is not None:
        msg["content"] = row["content"]
    if row["tool_calls"]:
        try:
            msg["tool_calls"] = json.loads(row["tool_calls"])
        except ValueError:
            pass
    if row["tool_call_id"]:
        msg["tool_call_id"] = row["tool_call_id"]
    if row["name"]:
        msg["name"] = row["name"]
    return msg


def build(store, sid: str, cfg: dict, extra_system: list | None = None) -> list:
    out = [{"role": "system", "content": cfg["system_prompt"]}]
    for block in (extra_system or []):
        if block:
            out.append({"role": "system", "content": block})
    summary = store.get_summary(sid)
    if summary:
        out.append({"role": "system",
                    "content": "Summary of earlier conversation:\n" + summary})

    rows = store.window(sid, int(cfg["context_window"]))
    budget = int(cfg["context_token_budget"])

    total = sum(row_tokens(r) for r in rows)
    start = 0
    while total > budget and start < len(rows) - 1:
        total -= row_tokens(rows[start])
        start += 1
    # Never begin on a tool result whose assistant call was dropped: the API
    # rejects an orphaned tool message.
    while start < len(rows) - 1 and rows[start]["role"] == "tool":
        start += 1

    out.extend(to_api(r) for r in rows[start:])
    return out


def needs_summary(store, sid: str, cfg: dict) -> bool:
    """True when there is unsummarised history older than the window.

    Asking "is this session long?" was the bug: the stored count never drops, so
    the trigger stayed true and the same old messages were re-summarised on every
    single turn. Asking "is there anything new to fold in?" is self-limiting.
    """
    return bool(store.unsummarized_older(sid, int(cfg["context_window"])))


def render_for_summary(rows) -> str:
    lines = []
    for r in rows:
        role = r["role"]
        text = (r["content"] or "").strip()
        if r["tool_calls"]:
            try:
                names = [c.get("function", {}).get("name", "?")
                         for c in json.loads(r["tool_calls"])]
                text = (text + " [called: " + ", ".join(names) + "]").strip()
            except ValueError:
                pass
        if r["role"] == "tool":
            role = "tool:" + (r["name"] or "?")
            text = text[:400]
        if text:
            lines.append("%s: %s" % (role, text))
    return "\n".join(lines)


def maybe_summarize(store, sid: str, cfg: dict, pool) -> bool:
    """Fold history older than the window into the session summary.

    Non-fatal on failure: a session that cannot be summarised still works, it
    just carries a longer window.
    """
    if not needs_summary(store, sid, cfg):
        return False
    keep = int(cfg["context_window"])
    old = store.unsummarized_older(sid, keep)
    if not old:
        return False
    transcript = render_for_summary(old)
    if not transcript.strip():
        return False
    prior = store.get_summary(sid) or ""
    prompt = (
        "Summarise the conversation below into at most 12 short bullet points. "
        "Keep concrete facts, decisions, file paths and open questions. Drop "
        "pleasantries.\n\n"
        + (("Existing summary:\n" + prior + "\n\n") if prior else "")
        + "Conversation:\n" + transcript[:12000]
    )
    try:
        result = pool.chat([{"role": "user", "content": prompt}], stream=False)
    except Exception:
        return False
    text = (result.get("content") or "").strip()
    if not text:
        return False
    store.set_summary(sid, text)
    store.set_summarized_upto(sid, old[-1]["id"])
    return True
