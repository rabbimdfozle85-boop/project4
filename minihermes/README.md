# Mini-Hermes

A tiny personal AI agent that runs on a 2-3 GB Android phone under Termux.

Pure Python standard library. No pip installs, no frameworks, no build step.
Roughly 1,400 lines across nine small modules.

Measured peak RSS on a full turn: **~28 MB**.

## Why it is small

The agent does not run a model locally. It is a client: your phone holds the
loop, the tools and the memory; a remote OpenAI-compatible endpoint holds the
weights. That single decision is what makes a 28 MB footprint possible.

## Install (Termux)

    pkg install python
    # unpack minihermes somewhere, then:
    cd minihermes
    python3 -m minihermes --init
    export MINIHERMES_API_KEY=sk-...
    # edit ~/.minihermes/config.json and set "model"
    python3 -m minihermes

`--init` writes `~/.minihermes/config.json` (mode 0600, because it may hold the
key). The key is read from `MINIHERMES_API_KEY` first, so the file does not have
to contain it at all.

## Use

    python3 -m minihermes                    # interactive REPL
    python3 -m minihermes -q "what's my disk usage?"   # one-shot
    python3 -m minihermes -q "..." --yolo    # allow shell in one-shot mode
    python3 -m minihermes --show-config
    python3 -m minihermes --model gpt-4o-mini

Slash commands: `/help /new /sessions /memory /remember /tools /model /exit`

## Config knobs that matter

    api_base              OpenAI-compatible base URL
    model                 model id
    context_window        messages kept verbatim (default 12)
    context_token_budget  hard token ceiling for the window (default 6000)
    tool_output_cap       max chars any tool may return (default 6000)
    shell_timeout         seconds (default 30)
    approve_shell         ask before running shell (default true)
    sandbox_roots         filesystem roots tools may touch

`context_window` and `context_token_budget` are what keep memory flat. Without
them RSS climbs with conversation length until Android kills the process.

## Tools

`run_shell`, `read_file`, `write_file`, `edit_file`, `list_dir`, `http_get`,
`memory_save`, `memory_search`.

Every tool schema is sent on every request, so this list is deliberately short.

## Design notes

* **SQLite, one row per message.** Not one JSON blob per conversation — that
  would rewrite the whole history on every turn.
* **Pragmas tuned for a phone:** `cache_size=-1000` (1 MB page cache),
  `temp_store=MEMORY`, WAL, `synchronous=NORMAL`.
* **FTS5 for memory, not embeddings.** Keyword recall over your own notes costs
  zero dependencies and zero network calls. Verified working on Termux.
* **Split tool-call arguments.** Providers stream `tool_calls[].function
  .arguments` in fragments; they are concatenated by index, not replaced.
* **One reused HTTPS connection.** On mobile data a fresh TLS handshake is
  300-800 ms; reuse removes it from every turn.
* **gzip on non-streaming bodies only.** On SSE it usually just adds latency.
* **No asyncio.** A single-user blocking REPL gains nothing from it.

## What it deliberately does not do

Multi-platform gateway, subagents, plugins, MCP, vector search, TUI, profiles.
Each would cost RAM, dependencies, or both.

## Tests

    python3 tests/smoke.py        # end-to-end against a canned SSE endpoint
    python3 tests/repl_check.py   # interactive path and slash commands

Neither test touches the network or needs an API key.

## Known limits

* Interactive use only. It is not designed to survive as a background daemon;
  Android will reap it and Doze will cut its network.
* No streaming retry. A drop mid-stream surfaces as an error, not a resume.
* The token estimate is `len(text)/4`, not a real tokenizer.
