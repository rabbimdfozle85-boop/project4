---
name: lightweight-agent-design
description: "Use when building a stdlib-only agent for low-RAM devices."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [agents, stdlib, low-memory, android, termux, sqlite, fts5, design]
tier: 1
---

# Lightweight agent design (stdlib-only, constrained hardware)

Applies when building a personal agent that must run on low-RAM hardware — a
2-3 GB Android phone, an SBC, an old laptop — with zero or near-zero
dependencies.

## When to Use

- Building an agent, CLI tool, or service that must fit a memory budget on
  constrained hardware (Android/Termux, SBC, old laptop).
- The user asks for "zero dependencies" or "pure stdlib".
- Deciding whether a given feature can be ported to a low-resource target.
- Running anything long-lived on Android — read the Termux reference first.

Not for: ordinary application work on a normal machine, where a dependency is
cheaper than the code you would write to avoid it.

## Measure, never assume

The first step is a real measurement on the target, not an estimate. Asserted
footprints are wrong in both directions, and the user is usually optimizing the
wrong axis.

Reference points from a measured stdlib stack: a full import set (sqlite3, ssl,
urllib, gzip, hashlib, subprocess, readline) is ~23 MB RSS; a complete agent with
a live streamed turn is ~28 MB. A "40 MB budget" is therefore usually **not** the
binding constraint — say so, and redirect attention to what is.

Get the numbers with `references/footprint-measurement.md`.

## Dependency and residency are the real filters

Most features are memory-cheap. Filter any feature request by two questions:

1. **Does it need a dependency?** A browser, Node, numpy, a vendor SDK.
2. **Does it need to be resident?** A listener, a daemon, a poller.

Non-resident, dependency-free, single-threaded features port almost for free.
Resident features fail on phones for lifecycle reasons long before memory does.
Rank the answer by those two axes, not by megabytes.

## Design patterns that keep it small

- **Rows, not blobs.** One row per message in SQLite — never one JSON document
  rewritten per turn. A blob is O(history) write amplification and a RAM spike
  on every save.
- **Caps are memory controls.** A tool-output cap is not just context hygiene: a
  single uncapped 10 MB output is a +10 MB allocation in one go. Bound every
  tool's return size, and bound tool recursion rounds.
- **Window plus summary.** Keep a hard message window and a token budget, and
  fold older history into a stored summary. Without both, RSS climbs with
  conversation length until the OS kills the process.
- **FTS5 before embeddings.** SQLite's FTS5 is built in, costs roughly 1 MB per
  10k rows, needs no network and no vector store. Reach for embeddings only when
  keyword recall measurably fails.
- **Progressive retrieval.** Never load memory into context; query it and load
  only the hits. FTS5 for keyword; optional in-house cosine over SQLite BLOBs
  for semantic (pure Python is fast enough for a few thousand vectors).
- **Reuse one connection.** A fresh TLS handshake per request costs 300-800 ms
  on mobile data. Keep the HTTP connection alive across turns.
- **gzip non-streaming bodies only.** On an SSE stream it usually adds buffering
  latency for no gain.
- **Skip asyncio for a single-user CLI.** It costs a few MB and buys nothing when
  one blocking user is the only concurrency. Add it only for a real daemon.
- **Low-RAM SQLite pragmas:** `cache_size=-1000` (1 MB page cache),
  `temp_store=MEMORY`, `journal_mode=WAL`, `synchronous=NORMAL`.
- **Approximate tokens as `len(text)/4`.** A real tokenizer is megabytes of
  tables for a number used only to decide when to trim.
- **Provider pool as data, not code.** Providers, keys, cooldowns and usage
  counters belong in SQLite rows, so a new provider is added at runtime by URL +
  key and nothing is hardcoded. Rotate keys on 429/5xx, fall back down a chain on
  hard failure, and surface pool health in the status bar.

## Tool surface

Every tool schema is sent on every request. Keep the list short and treat each
addition as a permanent per-request tax. A personal agent needs roughly 8-20.

## Verifying without burning credits

Test the agent loop against a canned SSE endpoint, not a real provider. See
`references/offline-llm-testing.md` — it covers the harness and the
process-hygiene pitfalls that come with it.

When the user supplies a batch of provider keys, characterize them rather than
assuming the batch is uniform — see `references/provider-key-probing.md`. The
headline pitfall: a Python client's default User-Agent draws a Cloudflare
403/1010, which looks like every key being dead and is not.

## If the target is a phone

Read `references/termux-android-runtime.md` **before** promising a 24/7 daemon.
The short version: the OS will kill it, wake-lock does not prevent that, and the
design must be restart-first rather than immortal.
