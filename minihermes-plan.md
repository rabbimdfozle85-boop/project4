# Mini-Hermes — Plan v2

**Status: plan for review. No code is approved or written.**
Supersedes v1. v1's measurements remain valid; its 40 MB budget does not.

Target: realme RMX3261 (C25s), Android 11 / SDK 30, aarch64 64-bit, 8 cores,
2.78 GB RAM, ~896 MB available, 1.68 GB swap of which ~1.15 GB is consumed,
3.9 GB free storage. Python 3.14.6, SQLite 3.54.0, FTS5 present, Termux:API CLI
package installed.

---

## 0. What changed from v1

| | v1 | v2 |
|---|---|---|
| Memory budget | 40 MB | **200 MB** (user-raised) |
| Process model | on-demand REPL, no residency | **resident supervisor**, restart-first |
| Surfaces | CLI only | **CLI + web portal + Telegram** |
| Concurrency | none | **up to 3 subagents, 2 concurrent sessions** |
| Providers | one endpoint | **provider registry, key rotation, fallback chains** |
| Identity | none | **Nous sign-in on first run** |
| Retrieval | FTS5 memory | **progressive retrieval + shared team brain** |

The 40 MB ceiling is gone, so residency and concurrency are now allowed — both
were things v1 had to rule out. One v1 premise was also wrong, and §1 corrects it.

**Hard constraint, confirmed: no dependencies.** Pure Python standard library,
end to end. That rules out Chromium, every pip package, and any vector store —
see §13 for what it costs and §16 for the decisions it settles.

---

## 1. Corrected premise: restart-first, not immortal

The spec said Termux "doesn't shut down or stop its runtime until I uninstall it."
That is not how Android works, and the whole design depends on getting it right.

Android terminates a Termux process via: the low-memory killer under pressure; the
OEM background-app killer (realme UI / ColorOS are among the most aggressive);
swiping the app from Recents; Doze / App Standby, which suspends CPU and network
with the screen off; and reboot.

**`termux-wake-lock` prevents none of these.** It stops the CPU sleeping — it
makes the process *active*, not *immortal*. Conflating the two is the single most
common error in this space, and a design built on it does not survive a day.

What actually helps, all of them together:

- `termux-wake-lock` while running, `termux-wake-unlock` on exit
- battery-optimization exemption for Termux (Unrestricted / Don't optimize)
- "Allow background activity" in realme UI's app settings
- never swipe Termux from Recents
- Termux:Boot, to restart after a reboot
- a supervisor wrapper that relaunches the process when it dies

**Design consequence:** the agent must be able to die and come back losslessly.
All durable state lives in SQLite; a restart is cheap. "One command to start it
and resume where it left off" is the **primary** design, not the fallback.
Charging 24/7 removes the battery concern and none of the above.

Android 11 has no phantom-process killer (that arrived in Android 12), so child
processes are not at risk from that specific mechanism.

One clarification on `termux-api`: `dpkg -l` confirms the **CLI package**
(0.60.0). The Termux:API **app** is a separate install, and `termux-battery-status`
returning empty is the tell that it is not present. Wake-lock, TTS and
job-scheduler all need the app, not just the CLI package. Worth confirming before
Phase 2.

---

## 2. Measured baseline

Per-child peak RSS, each child self-reporting kernel `VmHWM`
(`tests/measure_children.py`). `RUSAGE_CHILDREN` was rejected: it returns the max
across every child ever waited for, so readings after the first are copies.

| Configuration | Peak RSS |
|---|---|
| Bare interpreter | 9.9 MB |
| + sqlite3, ssl, urllib, gzip, hashlib, subprocess, readline | 22.9 MB |
| + asyncio imported | 26.5 MB |
| Full agent wired, DB open, context built | 25.9 MB |
| Full agent + a real streamed turn | **27.6 MB** |

Marginal feature costs (`tests/measure_features.py`):

| Feature | Marginal RSS |
|---|---|
| Skills index, 60 files | +0.1 MB |
| Skills, all 60 bodies resident | +0.2 MB |
| Session DB empty, 1 MB cache | +0.6 MB |
| 10,000 messages + FTS5 index | +1.0 MB (5.8 MB on disk) |
| Uncapped 10 MB tool output | **+10 MB spike** |

Under a 200 MB ceiling these are comfortable. They still matter: the device has
~896 MB available and is already swapping 1.15 GB, so a resident stack of
supervisor + web server + Telegram poller + 3 workers is roughly 150–200 MB —
which the phone will feel.

---

## 3. Architecture: three surfaces, one core

```
   CLI (REPL)   ──┐
   Web portal   ──┤──▶  CORE (turn loop, tools, providers, memory)  ──▶ SQLite
   Telegram     ──┘                 │                                    (one writer)
                    ┌───────────────┴────────────────┐
                    │ supervisor (restart-first)     │
                    │ workers (sessions, subagents)  │
                    └────────────────────────────────┘
```

All three surfaces are thin renderers over one core and one database. A message
from Telegram, the portal, or the CLI enters the same turn loop and lands in the
same `messages` table.

**Process model:** one supervisor parent, N workers.

- **Supervisor** owns SQLite (single writer — this is what prevents corruption and
  keeps the "team" coherent), the web server, and the Telegram poller. It
  relaunches workers that die, and restarts itself if killed.
- **Workers** run sessions and subagents. ~28 MB each.

Two distinct failure layers, both needed: the Telegram poller reconnects with
exponential backoff on a dropped socket; the supervisor restarts on process death.
A dropped socket and a killed process are different problems and need different
handling.

**Lifecycle:** `mh start` (acquires wake-lock, launches the supervisor detached,
returns), `mh stop`, `mh status`, `mh restart`. Startup resumes sessions from
SQLite — nothing is lost.

---

## 4. Identity: Nous sign-in on first run

This is the "identity" requirement, and it is implementable in pure `urllib`. The
flow is the one Hermes itself uses (`hermes_cli/anon_auth.py`) — the same one you
walked through when a URL and a code appeared.

Portal `https://portal.nousresearch.com`, client id `nas-anonymous`.

```
1. POST /api/anonymous/create
   -> {user_id, org_id, token, idle_ttl_days}
   Mints a guest identity. Token shown once.

2. POST /api/anonymous/token {"token": ...}
   -> {access_token, expires_in, inference_base_url, ...}
   Short-lived JWTs plus the inference base URL. Free-tier inference, no key.

3. Promotion into your real account — the "official notification":
   POST /api/anonymous/promotion-intent {"token","user_code","device_code"}
   -> {claim_code, claim_url, expires_in, interval}
   Open claim_url, approve on the Nous site; the device upgrades into your
   account and connectors transfer.
   Poll POST /api/anonymous/promotion-status {"claim_code"} until not "pending".
```

Headers: `content-type: application/json`, a user-agent, optionally
`x-anonymous-api-secret`.

**First-run UX:** steps 1–2 run silently, the `claim_url` is printed, and the
agent waits. Approve in a browser and you are signed in on your real account;
ignore it and you keep working on the guest tier. Nous is the primary provider;
everything else is a configurable fallback.

**Two caveats, stated plainly.** These are Hermes' internal endpoints, not a
published public API — as stable as `hermes login`, but not a third-party
contract, so the code needs a clean failure path and a fallback to a plain key.
And I have not executed the flow, because doing so would mint a real account on
your behalf. It is designed from source, not verified. Running it once is Phase
1's first task.

---

## 5. Provider layer — tested

A registry in SQLite, not code. Nothing hardcoded.

```
providers(id, label, base_url, api_style, default_model, headers_json, enabled)
provider_keys(provider_id, key, label, status, fail_count, cooldown_until, last_used)
usage(provider_id, key_id, day, requests, prompt_tokens, completion_tokens)
```

`api_style` covers the wire shapes actually found (`openai`, `gemini`,
`apifreellm`). A provider is added at runtime with a name, URL, key and style — no
code change. Rotation on 429/5xx, fallback down a chain on hard failure, per-key
usage counters surfaced in the status bar.

### Key test results (executed, 2025-10-13)

| Provider | Keys | Working | Detail |
|---|---|---|---|
| **OpenRouter** | 6 | **6** | all 200; per-key usage visible |
| **Gemini** | 5 | **2 solid, 1 partial, 2 dead** | below |
| **ApiFreeLLM** | 6 | **5** | 1 transient 503 |
| **Groq** | 6 | **0** | account-level restriction, not the keys |
| **Pollinations** | keyless | **1** | free, no key, OpenAI-compatible |

**Gemini detail:**
- `asad` — works (`gemini-2.5-flash`)
- `M6789` — works (`gemini-2.5-flash-lite`; the 2.5-flash / 2.0-flash variants are
  gated for this project)
- `M56789` — partial: most models return 404 "no longer available to new users";
  `gemini-flash-latest` reached the model but 503'd on capacity. Access exists,
  reliability does not.
- `POPKIDON`, `POPKIDON1` — 403 "Your project has been denied access." Dead at the
  project level; nothing to fix client-side.

**Groq:** every key returns `{"code":"organization_restricted"}` — the
*organization* is restricted, so no key will work. That needs a Groq support
ticket, not a code change.

**ApiFreeLLM — two corrections to the brief.** The request shape is
`{"message": "..."}`, not OpenAI's `messages` array. And it is **not unlimited**:
responses report `tier=community`, `dailyRequestLimit=50`, `bestEffort=true`.
50 requests/day, best-effort. Useful as a last-resort fallback, not a workhorse.

**Also confirmed free and keyless:** `https://text.pollinations.ai/openai`
(OpenAI-compatible; `GET /models` lists tiers). A good zero-config default when
every key is exhausted.

---

## 6. Tools — full mapping

You asked that no Hermes tool be dropped except by explicit decision. Every core
tool, with its disposition.

**Port directly (pure stdlib, no provider needed):**

| Hermes tool | Mini-Hermes form |
|---|---|
| `terminal`, `process_manage` | `subprocess` + a job table in SQLite |
| `read_file`, `write_file`, `patch`, `search_files` | direct; search via `re`/`fnmatch` walk |
| `todo_list` | SQLite table + `/todo` |
| `memory` | SQLite + FTS5 |
| `session_search` | FTS5 over `messages` |
| `clarify` | terminal prompt, or a Telegram inline keyboard |
| `execute_code` | `exec` in a subprocess with a timeout |
| `delegate_task` | spawn a worker; capped at 3 in the supervisor |
| `cronjob_manage` | job table + the supervisor's tick |
| `skills_list`, `skill_view`, `skill_manage` | filesystem + frontmatter parse |
| `text_to_speech` | `termux-tts-speak` |

**Port behind a provider endpoint:** `web_search`, `web_extract`,
`vision_analyze`, `image_generate`. `web_search` can also run keyless against
DuckDuckGo's HTML endpoint with `urllib` + `html.parser`.

**Collapse to an HTTP equivalent — the browser family.** Eleven browser tools
(`navigate`, `snapshot`, `click`, `type`, `scroll`, `back`, `press`,
`get_images`, `console`, `dialog`, plus the `exec` / `vision` / `cdp` / `vault`
variants) become a smaller HTTP-based set: fetch with a cookie jar, parse with
`html.parser`, POST forms. That covers scraping, APIs, sessions and form
submission — most of what people actually use a browser for — with zero
dependencies. It does **not** cover JavaScript-rendered pages.

**Excluded, with reasons:**

| Tool | Why |
|---|---|
| `browser_vision`, `browser_cdp`, `browser_vault`, `browser_exec` | need a real browser engine or a debugger socket |
| `computer_use` | needs an Android accessibility driver |
| `kanban_*` (14 tools) | fleet coordination; you deferred it |
| `manage_connections` | Hermes' hosted connector gateway, not portable |

Net: ~20 tools port directly, 4 more behind a provider, and the browser family
becomes ~6 HTTP tools. §13 covers the JavaScript and touch-automation gap.

---

## 7. Skills

Port the *format* and a curated set, not the whole library. Hermes' collection
includes many skills about Hermes itself, which earn nothing on a phone. The
measured cost of a 60-entry index is 0.1 MB, so size is not the constraint —
*relevance* is.

Design: `skills/*.md` with frontmatter; an in-RAM index of name + description;
bodies loaded on demand and injected as a **user message** (never the system
prompt — that would break prompt caching). `skills_list`, `skill_view` and
`skill_manage` port directly, so **the agent can read, write and edit its own
skills** — the self-improvement loop you asked for.

Proposed curated set (~15): terminal/shell usage, file editing, HTTP/API patterns,
SQLite, Python, git, Telegram, markdown authoring, task planning, plus the two
design skills already written for this project (`lightweight-agent-design`,
`plan-before-build`).

---

## 8. Second brain and the team

Two separable things you described.

**Progressive retrieval** (your "sequential second brain"): never load memory into
context; query it and load only the hits.

- Tier 1 — FTS5 over `memories` and `messages`. Zero network, +1 MB per 10k rows.
- Tier 2 — in-house embeddings: computed via a provider's embedding endpoint,
  stored as a SQLite BLOB, ranked by cosine similarity in pure Python. No numpy,
  no vector DB, no plugin. A few thousand vectors is milliseconds.

**Shared team brain.** Every session writes to the same `memories` table plus a
`notes` table with provenance (`session_id`, `agent_id`, `kind`, `confidence`).
Agents publish findings; a `team_feed` view is what makes "work together as a
team" real rather than decorative. An `agent_messages` table lets workers address
each other directly. After N messages a session flushes its durable findings —
your "must update everything into the main brain" requirement, made concrete.

One scope note: "like a group bot" implies addressing and turn-taking rules, which
is a real protocol. Buildable, but it is its own phase, and done badly it produces
agents that talk past each other.

---

## 9. Concurrency

- Supervisor + workers. Supervisor owns the single SQLite writer; workers use WAL
  with a short busy timeout for reads.
- Sessions: 2 concurrent, as asked.
- Subagents: **cap 3, prefer 1.** Each is a fresh ~28 MB process. Three, plus the
  supervisor and the web server, is ~150 MB.
- The cap is enforced in the supervisor, not the prompt — a model asked politely
  to limit itself will not.

---

## 10. Data model

```sql
sessions(id, title, model, provider, summary, created_at, updated_at)
messages(id, session_id, role, content, tool_calls, tool_call_id, name, created_at)
  INDEX (session_id, id)
messages_fts USING fts5(content)
memories(id, content, kind, created_at, used_at)
memories_fts USING fts5(content)
notes(id, session_id, agent_id, kind, content, confidence, created_at)
agent_messages(id, from_agent, to_agent, body, created_at, read_at)
providers(id, label, base_url, api_style, default_model, headers_json, enabled)
provider_keys(provider_id, key, label, status, fail_count, cooldown_until, last_used)
usage(provider_id, key_id, day, requests, prompt_tokens, completion_tokens)
jobs(id, kind, schedule, command, next_run, last_run, enabled)
```

Pragmas: `journal_mode=WAL`, `synchronous=NORMAL`, `temp_store=MEMORY`,
`cache_size=-1000`, `busy_timeout=5000`. Under a 200 MB ceiling `cache_size` could
rise to 2 MB; 1 MB is kept because the device is swap-pressured.

Retention: default keep-everything, `/prune <days>` available. At 100 messages/day,
10,000 messages is ~100 days and 5.8 MB — a privacy question, not a capacity one.

---

## 11. Web portal and remote access

Python's built-in `http.server` with a small router — no framework. Pages: status
(providers, key health, usage, context size), chat, sessions, memory, jobs,
settings.

Access: `127.0.0.1` for the phone's own browser; bind `0.0.0.0` for LAN access
from your laptop at `http://<phone-ip>:<port>`. **A LAN bind requires auth** — a
token or password — because it is reachable by anything on the network. That is
not optional and I will not ship a LAN-reachable panel without it.

---

## 12. CLI

Full slash-command surface, mirroring Hermes' registry so nothing is missing:
session (`/new /resume /sessions /search`), config (`/model /provider /keys`),
tools (`/tools /skills /todo`), info (`/status /usage /context /help`), exit.
Plus an ANSI status bar: provider, model, key-pool health, token spend, context
fill, session id. All stdlib — `readline` for history and editing.

---

## 13. Browser and screen automation — the honest position

Your spec: pure requests for background work, and "Python's built-in browser" for
frontend actions, with clicking and swiping.

There is no built-in browser engine in Python. `webbrowser` opens the system
browser; it does not automate it. The capability splits into three tiers with very
different costs:

| Tier | What it does | Cost | Verdict |
|---|---|---|---|
| **A. HTTP automation** | fetch, cookies, sessions, forms, scraping | zero deps | **Do it.** Covers most real needs. |
| **B. JavaScript rendering** | pages that need JS to produce content | Chromium (~200 MB+) or a remote rendering API | **Out** — no dependencies |
| **C. Touch/UI automation** | `tap`, `swipe`, lock screen, position handling | ADB-over-WiFi (`android-tools` + wireless-debugging pairing) or root | **Deferred** — needs a system binary, not a Python package |

Tier C is the one to flag clearly: **there is no Termux command that taps the
screen.** `termux-api` does not expose touch injection. The only non-root path is
`adb connect localhost:5555` after enabling wireless debugging, then
`adb shell input tap x y`. That works, but it is a real setup step, it can drop,
and it is inherently fragile.

### Settled by the no-dependency constraint

- **Tier B is out.** No Chromium, no remote rendering service. Pages that need
  JavaScript to produce their content are simply out of scope. Everything else in
  Tier A still works, and Tier A is where most real browser work lives.
- **Tier C is deferred to Phase 8.** It needs no *Python* package, but it does
  need the `android-tools` binary plus a wireless-debugging pairing — a system
  install with a fragile lifecycle. Opt-in if ever built; not on the critical path.
- **Nothing else is affected.** Web search runs keyless against DuckDuckGo's HTML
  endpoint with `urllib` + `html.parser`. Semantic recall computes embeddings via
  a provider API and does cosine similarity in pure Python. Both stay inside the
  constraint.

---

## 14. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **Android kills the process.** Independent of memory. | High | Restart-first: supervisor, wake-lock, battery exemption, Termux:Boot. Durable state in SQLite. |
| 2 | **Swap pressure.** 1.15 GB already swapped before we start. | High | Keep the resident stack lean; cap subagents at 3 in the supervisor. |
| 3 | **Single-writer contention** across workers. | High | Supervisor owns writes; WAL + `busy_timeout`. |
| 4 | **Nous internal endpoints change.** Undocumented contract. | Medium | Clean failure path; fall back to a plain key; surface the error, never hang. |
| 5 | **Key-pool rot.** 9 of 23 supplied keys are dead on arrival. | Medium | Health-check on load; quarantine with cooldown; pool health in the status bar. |
| 6 | **LAN-exposed web panel.** | High if enabled | Auth required on any non-loopback bind. Non-negotiable. |
| 7 | **Uncapped tool output** spikes RSS by the output's size. | Medium | Hard cap + bounded tool rounds. |
| 8 | **Team protocol degrades into noise.** | Medium | Phase it separately; define addressing and turn-taking first. |
| 9 | **Prompt-cache breakage** from injecting skills into the system prompt. | Medium | Skills go in as a user message, never the system prompt. |
| 10 | **ADB dependency for touch automation** is fragile. | Medium | Tier C is opt-in and fails loudly. |
| 11 | **Secrets on disk.** 23 keys in a SQLite table. | Medium | Store in a 0600 file or the DB with restrictive perms; env override supported. |
| 12 | **Mid-stream network drop.** Mobile data is unstable. | Medium | Pre-response retry works; mid-stream resume does not yet. |

---

## 15. Phases

**Phase 1 — identity and providers.** Nous sign-in (execute it for real, once).
Provider registry with rotation, fallback and usage counters. Load and health-check
the tested keys. Everything else sits on this.

**Phase 2 — the resident core.** Supervisor, wake-lock, restart-first,
`mh start/stop/status/restart`, SQLite writer ownership.

**Phase 3 — surfaces.** CLI with the full slash set and status bar; web portal
(loopback first, auth before LAN); Telegram long-poll with reconnect.

**Phase 4 — tools.** Direct ports, then provider-backed, then the HTTP browser set.

**Phase 5 — skills.** Format, index, on-demand loading, self-editing.

**Phase 6 — second brain.** FTS5 retrieval, then optional embeddings.

**Phase 7 — team.** `notes`, `agent_messages`, the flush protocol.

**Phase 8 — optional.** Touch automation (Tier C), JS rendering (Tier B), MCP seam.

---

## 16. Decisions

**Locked** — change any of these and I will revise the plan:

| # | Decision | Rationale |
|---|---|---|
| 1 | **No dependencies. Hard constraint.** Pure Python stdlib end to end. | Your directive. Governs everything below. |
| 2 | **JavaScript rendering: out.** No Chromium, no remote rendering API. | Follows from 1. Pages needing JS are out of scope; Tier A HTTP covers the rest. |
| 3 | **Touch automation: deferred** to Phase 8, opt-in. | Needs the `android-tools` binary and a wireless-debugging pairing — a system install, not a Python package. |
| 4 | **Skills: curated ~15**, agent-editable. | A 60-entry index measures at 0.1 MB, so size is not the limit; relevance is. |
| 5 | **Team protocol: Phase 7**, after the rest works. | It is a real addressing/turn-taking protocol; done badly it produces noise. |
| 6 | **LAN access: yes**, bind `0.0.0.0` behind token auth. | You asked to drive it from your laptop by phone IP. Auth is non-negotiable on a non-loopback bind. |
| 7 | **Nous first-run: always attempt promotion**, guest tier as fallback. | You called it the identity of the project. |
| 8 | **Retention: keep everything**, `/prune <days>` available. | Capacity is a non-issue at 5.8 MB per 10k messages. |
| 9 | **MCP: seam only**, no server shipped. | Your instruction. |
| 10 | **Telegram only** for the gateway. No other platforms, no discovery. | Your instruction. |

**Yours to action, not mine:**

- **Groq** — the organization is restricted; no key works until a support ticket clears it.
- **Termux:API app** — confirm it is installed. The CLI package alone provides neither wake-lock nor TTS.

**Still open:**

- **Skills list** — approve the curated ~15, or name specific ones to include?

---

## Appendix — reproducing the evidence

```bash
cd /workspaces/project4/minihermes
python3 tests/measure_children.py    # per-child peak RSS
python3 tests/measure_features.py    # marginal cost per feature
python3 tests/smoke.py               # end-to-end + RSS assertion
python3 tests/repl_check.py          # interactive path
```

Key probes (results in §5) live in the scratch dir; they print status only and
redact all key material.
