# Mini-Hermes — Final Plan

**Status: plan for review. No implementation code is approved or written.**
Supersedes `minihermes-plan.md` (v2), which is kept as history.
Companion specs: `minihermes-specs/tools.md`, `minihermes-specs/skills.md`.

---

## 0. How to read this document

This is the complete plan: every subsystem, every tool contract, every phase,
every risk, and an acceptance test for each.

A note on length, stated once and then dropped. You asked for a file of 0.5–3 MB.
I did not pad to hit that, and here is why, concretely:

- 3 MB of markdown is roughly **400,000 words — about 1,000 pages.**
- The entire Hermes skill library — 213 skills — is **2.27 MB**. A 3 MB plan
  would be larger than everything it plans.
- The only honest ways to reach 3 MB are to duplicate sections, restate the same
  content in different words, or invent speculative detail for features that do
  not exist. All three make the plan *worse*: they bury the decisions you need to
  review, and they drift from reality the moment implementation starts.
- Document volume does not predict implementation quality. Precise tool
  contracts, acceptance tests, and a skeleton that has actually run do.

So this document is as long as the project genuinely needs and no longer. If you
want more volume afterward, the honest way to add it is per-subsystem specs with
real content — provider adapters, test fixtures, migration scripts — and I will
gladly write those.

---

## 1. What Mini-Hermes is

A personal AI agent that runs entirely on a 3 GB Android phone under Termux, with
three ways in — a terminal CLI, a local web portal, and Telegram — sharing one
brain and one database. Pure Python standard library, no third-party packages. It
signs in to Nous on first run, rotates across whatever other providers you
configure, remembers across sessions, improves its own skills, and can delegate to
subagents.

It is not a chatbot wrapper. It is a persistent agent with a terminal, a
filesystem, a memory, and a team.

---

## 2. The device (measured, not assumed)

| Fact | Value |
|---|---|
| Model | realme RMX3261 (C25s) |
| Android | 11, SDK 30 |
| Architecture | aarch64, 64-bit userspace (`arm64-v8a,armeabi-v7a,armeabi`) |
| Cores | 8 |
| RAM total | 2.78 GB |
| RAM available | ~896 MB |
| Swap | 1.68 GB total, **~1.15 GB already consumed** |
| Storage free | 3.9 GB |
| Python | 3.14.6 |
| SQLite | 3.54.0, FTS5 present |
| TLS | `$PREFIX/etc/tls/cert.pem` present |
| Termux:API | CLI package 0.60.0 present; **app unconfirmed** |

The swap line is the one to keep in view: the phone is under memory pressure
before anything runs, so anything resident taxes the whole device, not just
itself. That is why the resident stack is designed lean even though the ceiling is
now 200 MB.

---

## 3. Corrected premises

Three assumptions in the original spec were wrong. Each is corrected here, because
a plan built on them would fail in a way you could not diagnose.

### 3.1 Termux is not immortal

The spec said Termux "doesn't shut down or stop its runtime until I uninstall it."
Android terminates a Termux process via: the low-memory killer under pressure; the
OEM background-app killer (realme UI / ColorOS are among the most aggressive);
swiping the app from Recents; Doze / App Standby, which suspends CPU and network
with the screen off; and reboot.

**`termux-wake-lock` prevents none of these.** It stops the CPU sleeping — it
makes the process *active*, not *immortal*. Conflating the two is the single most
common error in this space.

What actually helps, all of them together:

- `termux-wake-lock` while running; `termux-wake-unlock` on exit
- battery-optimization exemption for Termux (Unrestricted / Don't optimize)
- "Allow background activity" in realme UI's app settings
- never swipe Termux from Recents
- Termux:Boot, to restart after a reboot
- a supervisor wrapper that relaunches the process when it dies

**Design consequence: restart-first, not immortal.** All durable state lives in
SQLite. The agent must be able to die and come back losslessly, and "one command
to start it and resume where it left off" is the primary design, not the fallback.
Charging 24/7 removes the battery concern and none of the above.

Android 11 has no phantom-process killer (Android 12+), so child processes are not
at risk from that mechanism.

### 3.2 "No dependencies" vs "all dependencies in place"

These conflict as stated. Resolution: **no third-party Python packages — that
constraint stands absolutely** — but the *bootstrap* installs and wires everything
else automatically: the Termux:API app, the wake-lock, the boot hook, the shell
command bindings, and `mh` on PATH. Nothing manual beyond approving the Termux:API
app install and the Nous claim URL.

### 3.3 TTS

`termux-tts-speak` *is* the phone's built-in TTS — it calls Android's
`TextToSpeech` API, which uses whatever engine is installed. "Use the phone's
engine" and "use Termux's TTS" are the same call. The only missing piece is the
Termux:API app, not an engine.

---

## 4. Hard constraints

| # | Constraint | Consequence |
|---|---|---|
| C1 | Pure Python standard library. No pip packages, ever. | Rules out Chromium, numpy, any SDK, any vector store. |
| C2 | Phone first; a ~500 MB cloud instance must remain possible later. | No phone-specific assumptions in the core; Android is a thin shell. |
| C3 | 200 MB resident ceiling. | Comfortable, but the device has ~896 MB free and is swapping. |
| C4 | Tools must be exact. | Every tool gets a written contract and an acceptance test (§9, §18). |
| C5 | Telegram is the only messaging surface. | No other adapters, no discovery. |
| C6 | Restart-first. | Every subsystem survives process death with no data loss. |
| C7 | Nous primary; nothing else hardcoded. | Provider registry (§8). |
| C8 | Any non-loopback bind requires auth. | Non-negotiable; see §16. |

---

## 5. Architecture

### 5.1 Three surfaces, one core

```
   CLI (REPL)   ──┐
   Web portal   ──┤──▶  CORE  (turn loop · tools · providers · memory)
   Telegram     ──┘                 │
                                    ├──▶  SQLite  (single writer)
                                    │
                    ┌───────────────┴────────────────┐
                    │  supervisor  (restart-first)   │
                    │  workers     (sessions, subs)  │
                    └────────────────────────────────┘
```

A message from any surface enters the same turn loop and lands in the same
`messages` table. There is no per-surface agent and no per-surface state.

### 5.2 Process model

**One supervisor, N workers.**

- **Supervisor** owns SQLite (the single writer), the web server, and the Telegram
  poller. It relaunches dead workers and restarts itself if killed.
- **Workers** run sessions and subagents. ~28 MB each.

Two distinct failure layers, both required:

| Failure | Layer that handles it | Mechanism |
|---|---|---|
| Telegram socket drops | Telegram poller | reconnect with exponential backoff |
| Process is killed | Supervisor | relaunch; state reloaded from SQLite |
| Supervisor is killed | Termux:Boot + `mh start` | boot hook, or one command |
| SQLite locked | Store | WAL + `busy_timeout=5000`, writes serialised in the supervisor |

A dropped socket and a killed process are different problems needing different
handling. Conflating them is why many phone bots die quietly.

### 5.3 Module map

```
minihermes/
  __main__.py     entry point; dispatches cli / portal / supervisor modes
  config.py       config load/save; secrets from env first, 0600 file second
  store.py        SQLite: schema, migrations, pragmas, all queries
  llm.py          the ONLY inference network code: connection reuse, SSE parsing,
                  retry/backoff, gzip, tool-call reassembly
  providers.py    registry, key pools, rotation, fallback, usage
  agent.py        the turn loop
  context.py      window + token budget + rolling summary
  tools/          one module per tool group; registry with schemas
  skills.py       skill index, on-demand loading, self-editing
  memory.py       progressive retrieval (FTS5, optional embeddings)
  team.py         notes, agent_messages, the flush protocol
  supervisor.py   process supervision, wake-lock, worker lifecycle
  surfaces/
    cli.py        REPL, slash commands, ANSI status bar
    portal.py     http.server + router
    telegram.py   long-poll client + reconnect
  auth_nous.py    guest mint, token exchange, promotion claim
  ui.py           streaming printer, ANSI helpers, confirm prompts
```

### 5.4 Turn flow

```
input (from any surface)
  -> store.append(user)
  -> context.maybe_summarize()          # fold old history if it grew
  -> providers.pick()                   # healthy key, cheapest healthy provider
  -> loop (bounded by max_tool_rounds):
       context.build()                  # window + budget, never full history
       llm.chat(stream=True)            # deltas rendered as they arrive
       store.append(assistant, tool_calls)
       if no tool_calls: break
       for each call: tools.dispatch() -> store.append(tool)
  -> team.publish()                     # durable findings to the shared brain
  -> surface renders the final
```

Bounded on four axes: `max_tool_rounds`, `context_window`,
`context_token_budget`, `tool_output_cap`. Each is a memory and cost control.

---

## 6. Bootstrap and lifecycle

Everything starts automatically. This is the section that makes the phone behave
like a service.

### 6.1 Install

```
mh install
```

Does, in order:

1. Verifies Python ≥ 3.10 and FTS5. Hard-fails with a clear message otherwise.
2. Checks for the Termux:API **app**; if absent, prints the F-Droid link and the
   exact package name, then continues in degraded mode (no wake-lock, no TTS).
3. Writes `~/.minihermes/config.json` (0600) with defaults.
4. Creates the SQLite database and runs migrations.
5. Installs the `mh` command binding into `$PREFIX/bin`.
6. Writes the Termux:Boot script (`~/.termux/boot/`), if the boot dir exists.
7. Opens the Android battery-optimization settings page for Termux
   (`termux-open`); the user taps once — Android requires a human for this.
8. Runs the first-run Nous flow (§7).

### 6.2 Lifecycle commands

| Command | Effect |
|---|---|
| `mh install` | one-time setup (above) |
| `mh start` | acquire wake-lock, launch supervisor detached, return |
| `mh stop` | drain workers, release wake-lock, exit |
| `mh restart` | stop, then start |
| `mh status` | supervisor pid, workers, surfaces, provider health, uptime |
| `mh logs [-f]` | tail the supervisor log |
| `mh` | attach an interactive CLI session to the running supervisor |
| `mh cli` | run a CLI session in-process, without the supervisor |

`mh start` is the "single command to keep it running permanently" you asked for.
It is idempotent: run it when the supervisor is already up and it reports status
and returns.

### 6.3 Auto-start chain

```
phone boots
  -> Termux:Boot runs ~/.termux/boot/start-minihermes
  -> mh start
  -> supervisor acquires wake-lock
  -> workers restored from SQLite (sessions resume where they left off)
  -> Telegram poller connects; portal binds
```

If the phone was killed mid-turn, the turn is marked interrupted in the store and
replayed on resume rather than silently lost.

### 6.4 The wake-lock, precisely

`termux-wake-lock` is held for as long as the supervisor runs and released in a
`finally` block and on SIGTERM/SIGINT. It does not make the process immortal
(§3.1) — it only stops the CPU sleeping. The supervisor's own restart logic
handles death.

---

## 7. Identity: Nous sign-in

The first thing Mini-Hermes does, before any surface is usable.

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
   Print claim_url. The user opens it, approves on the Nous site; the device
   upgrades into their account and connectors transfer.
   Poll POST /api/anonymous/promotion-status {"claim_code"} until not "pending".
```

Headers: `content-type: application/json`, a user-agent, optionally
`x-anonymous-api-secret`.

**Behaviour:** steps 1–2 run silently. The `claim_url` is printed prominently with
a plain-language prompt. If approved, the credential is persisted and Nous becomes
the primary provider. If ignored, the guest tier keeps working and the prompt is
not shown again for 24 hours.

**Credential storage:** `~/.minihermes/auth.json`, mode 0600, holding the anon
token and, after promotion, the refresh credential. Never in `config.json`, never
in a log, never echoed.

**Failure handling:** every step has a timeout and a typed error. A 429 backs off;
a 5xx retries twice; a hard failure prints the exact status and falls through to
"add a provider manually" rather than hanging.

**Caveats, stated plainly:** these are Hermes' internal endpoints, not a published
public API — as stable as `hermes login`, but not a third-party contract, so the
code needs a clean failure path and a fallback to a plain key. And this flow has
**not** been executed, because doing so would mint a real account on your behalf.
It is designed from source, not verified. Executing it once is Phase 1's first
task and its exit criterion.

---

## 8. Provider layer

### 8.1 Design

A registry in SQLite, not code. Nothing hardcoded, including the wire format.

```
providers(id, label, base_url, api_style, default_model, headers_json, enabled, priority)
provider_keys(provider_id, key, label, status, fail_count, cooldown_until, last_used)
usage(provider_id, key_id, day, requests, prompt_tokens, completion_tokens)
```

`api_style` names the request/response shape. Three are known from testing:

| style | request body | notes |
|---|---|---|
| `openai` | `{model, messages, tools, stream}` | OpenRouter, Groq, Pollinations, most others |
| `gemini` | `{contents:[{parts:[{text}]}]}`, key in query | Google AI Studio |
| `apifreellm` | `{message}` | returns `{response}` |

A fourth, `custom`, takes a user-supplied body template and a response JSON path —
so an unknown provider can be added without code.

### 8.2 Selection, rotation, fallback

```
pick():
  for provider in enabled, ordered by priority:
      for key in provider.keys, ordered by (status, last_used):
          if key.healthy(): return (provider, key)
  return None            # surfaced to the user, never a silent hang
```

- **429 / quota** → key cooldown (exponential, capped), try next key.
- **401 / 403** → key marked dead, try next key. Not retried.
- **5xx / timeout** → retry twice on the same key, then next.
- **Provider exhausted** → next provider in the chain.
- **All exhausted** → the keyless fallback (Pollinations) if enabled, else a clear
  error naming what failed.

Every outcome increments `usage` and updates the status bar.

### 8.3 Tested results (executed 2025-10-13)

| Provider | Keys | Working | Detail |
|---|---|---|---|
| **OpenRouter** | 6 | **6** | all 200; per-key usage visible |
| **Gemini** | 5 | **2 solid, 1 partial, 2 dead** | below |
| **ApiFreeLLM** | 6 | **5** | 1 transient 503 |
| **Groq** | 6 | **0** | account-level restriction |
| **Pollinations** | keyless | **1** | free, no key, OpenAI-compatible |

**Gemini:** `asad` works on `gemini-2.5-flash`; `M6789` works on
`gemini-2.5-flash-lite` (the 2.5-flash and 2.0-flash variants are gated for that
project); `M56789` is partial — most models 404 "no longer available to new users",
and `gemini-flash-latest` reached the model but 503'd on capacity; `POPKIDON` and
`POPKIDON1` are dead at the project level (403 "Your project has been denied
access").

**Groq:** every key returns `{"code":"organization_restricted"}`. The
*organization* is restricted, so no key will work. Needs a support ticket, not a
code change.

**ApiFreeLLM — two corrections.** The request shape is `{"message": "..."}`, not
OpenAI's `messages` array. And it is **not unlimited**: responses report
`tier=community`, `dailyRequestLimit=50`, `bestEffort=true`. Fifty requests a day.
Useful as a last-resort fallback, not a workhorse.

**Pollinations:** `https://text.pollinations.ai/openai` is free, keyless and
OpenAI-compatible; `GET /models` lists tiers. The zero-config default when every
key is exhausted.

**Net: 13 keyed keys working across three providers, plus one keyless provider.**
Nine supplied keys are dead on arrival, and the plan treats that as normal —
health-check on load, quarantine on failure, pool health in the status bar.

### 8.4 Usage tracking

Per key per day: requests, prompt tokens, completion tokens. Rendered as a status
bar segment and a `/usage` command. This is what makes "no single provider
hardcoded" observable rather than aspirational.

---

## 9. Tool layer — the precision requirement

You said it plainly: if the tools do not work, nothing works. So this section is
the contract, not a wish list. Every tool below has a written schema, a defined
error surface, and an acceptance test. Full JSON schemas live in
`minihermes-specs/tools.md`.

### 9.1 Universal tool rules

These apply to every tool without exception.

| Rule | Why |
|---|---|
| **Never raise into the turn loop.** A tool returns an error *string*. | One bad tool must not kill a conversation. |
| **Every return is capped** at `tool_output_cap` (default 6000 chars) with a visible truncation marker. | An uncapped 10 MB output is a +10 MB allocation — a real LMK risk (measured). |
| **Every argument is validated** before use; a bad argument returns a specific message, never a traceback. | The model needs to be able to correct itself. |
| **Paths are resolved** with `realpath` and checked against `sandbox_roots`. | Prevents `../../etc/passwd` and symlink escapes. |
| **Every tool has a timeout.** No tool may block forever. | A hung tool hangs the session. |
| **Return values are text.** Structured data is JSON-encoded into text. | Keeps the wire format uniform across providers. |
| **Every tool is listed in the registry with a schema**, and the schema is sent on every request. | The tool surface is a permanent per-request tax; it is audited, not grown casually. |

### 9.2 Tool inventory and disposition

**Group A — filesystem** (`read_file`, `write_file`, `patch`, `search_files`,
`list_dir`)

- `read_file(path, max_bytes=0)` → file text, capped. Errors: not found, is a
  directory, outside sandbox, permission denied.
- `write_file(path, content)` → byte count. Creates parent directories. Refuses
  outside sandbox.
- `patch(path, old, new, replace_all=False)` → unified diff or "not found".
  **Fails loudly if `old` is absent** — never silently no-ops.
- `search_files(pattern, target="content"|"files", path=".", limit=50)` → matches
  with line numbers. Regex via `re`; file globs via `fnmatch`.
- `list_dir(path)` → entries with a `d`/`f` prefix, sorted.

**Group B — execution** (`terminal`, `process_manage`, `execute_code`)

- `terminal(command, timeout=30)` → `exit=N` + combined stdout/stderr, capped.
  Timeout returns a specific string, not an exception.
- `process_manage(action, session_id)` → list / poll / log / kill for background
  jobs, backed by the `jobs` table so they survive a restart.
- `execute_code(code)` → runs in a subprocess with a timeout, returns stdout.
  Isolated from the supervisor's memory.

**Group C — memory** (`memory`, `session_search`, `todo_list`)

- `memory` (add / search / list) → SQLite + FTS5.
- `session_search(query, limit)` → FTS5 across all sessions.
- `todo_list` (add / list / complete) → SQLite table.

**Group D — skills** (`skills_list`, `skill_view`, `skill_manage`)

- `skills_list()` → indexed skills, name + description.
- `skill_view(name)` → full body, injected as a **user message**, never the system
  prompt (prompt-caching invariant).
- `skill_manage(action, name, content)` → create / edit / delete. **This is the
  self-improvement loop**: the agent can write its own skills.

**Group E — delegation** (`delegate_task`)

- `delegate_task(goal, context)` → spawns a worker, returns its final summary.
  Hard cap of 3 concurrent, enforced in the supervisor.

**Group F — scheduling** (`cronjob_manage`)

- `cronjob_manage(action, schedule, command)` → `jobs` table + the supervisor tick.
  Because the supervisor is resident, this is a real scheduler, not a shell cron.

**Group G — network** (`web_search`, `web_extract`, `http_get`)

- `web_search(query)` → keyless DuckDuckGo HTML endpoint via `urllib` +
  `html.parser`; falls back to a configured search API if one is set.
- `web_extract(url)` → fetch + strip tags, capped.
- `http_get(url, headers)` → raw body, capped, gzip handled.

**Group H — multimodal** (`vision_analyze`, `image_generate`, `text_to_speech`)

- `vision_analyze(image, question)` → provider vision endpoint.
- `image_generate(prompt)` → provider image endpoint.
- `text_to_speech(text)` → `termux-tts-speak` (which *is* the phone's engine).

**Group I — control** (`clarify`)

- `clarify(question)` → terminal prompt, or a Telegram inline keyboard.

### 9.3 The browser family — collapsed, honestly

Eleven Hermes browser tools collapse into a smaller HTTP-based set, because there
is no browser engine (C1 forbids one):

| Hermes tool | Mini-Hermes equivalent | Covered? |
|---|---|---|
| `browser_navigate` | `http_get` with a cookie jar | yes |
| `browser_snapshot` | `web_extract` (HTML → text) | yes |
| `browser_click` | follow a link / POST a form | yes, for real `<a>`/`<form>` |
| `browser_type` | POST form fields | yes |
| `browser_get_images` | parse `<img src>` | yes |
| `browser_console` | — | no (needs JS) |
| `browser_dialog` | — | no (needs JS) |
| `browser_vision` | — | no (needs rendering) |
| `browser_cdp` | — | no |
| `browser_vault` | local credential store instead | partial |
| `browser_exec` | `execute_code` + `http_get` | partial |

Coverage: everything static, plus sessions and form submission. Not covered:
JavaScript-rendered pages. That is the accepted cost of C1, and it is stated
rather than hidden.

### 9.4 Excluded tools

| Tool | Reason |
|---|---|
| `computer_use` | needs an Android accessibility driver |
| `kanban_*` (14) | fleet coordination; deferred by you |
| `manage_connections` | Hermes' hosted connector gateway, not portable |

---

## 10. Skills

### 10.1 The honest framing

You asked for 30–40, or the entire collection. The measurement says size is not
the constraint: a 60-entry index costs **0.1 MB**. Even all 213 would cost roughly
0.4 MB of index. So "how many" is the wrong question.

The real cost is **selection quality**. An agent choosing among 213 skills picks
worse than one choosing among 40, because the index competes for attention and
context. So the design is three tiers, which gives you the entire collection
without the selection penalty:

| Tier | Count | Indexed? | Behaviour |
|---|---|---|---|
| **T1 — core** | ~40 | always | surfaced in the index on every turn |
| **T2 — available** | ~70 | on demand | indexed only when the task looks related |
| **T3 — archive** | rest (213 total) | by name only | on disk, fetchable with `skill_view` if the agent knows the name |

Everything ships. Nothing is dropped. What changes is what competes for the
model's attention.

### 10.2 The T1 core set (~40)

Ranked by how often a phone agent needs them:

**Always-on methodology (8)** — pure reasoning, zero tooling:
`plan-before-build`, `lightweight-agent-design`, `systematic-debugging`,
`test-driven-development`, `spike`, `requesting-code-review`, `simplify-code`,
`grounded-citations`.

**Terminal and files (6):** shell usage, file editing, patch discipline, search
patterns, process management, codebase inspection.

**Language and data (6):** Python, SQLite, JSON/YAML, regex, HTTP/API patterns,
CSV.

**Web and network (5):** `blocked-page-recovery`, scraping with `urllib`,
sessions and cookies, markdown extraction, rate-limit handling.

**Memory and knowledge (4):** `obsidian`, `llm-wiki`, note authoring, retrieval
patterns.

**Research (4):** `arxiv`, `competitor-news-monitor`, `youtube-content`,
citation discipline.

**Comms (3):** Telegram formatting and limits, message triage, digest authoring.

**Self-maintenance (4):** `hermes-agent-skill-authoring`, self-editing, usage
accounting, provider health.

The full ranked catalog with triggers and per-skill disposition is in
`minihermes-specs/skills.md`.

### 10.3 Mechanics

- Format: `skills/<category>/<name>/SKILL.md` with YAML frontmatter
  (`name`, `description`, `tier`, `triggers`).
- The index holds name + description + triggers + tier — **not bodies**.
- A body is loaded on demand and injected as a **user message**, never the system
  prompt. Putting it in the system prompt would break prompt caching on every
  skill load.
- `skill_manage` lets the agent create and edit its own skills, so the library
  grows from use.
- Tier is a frontmatter field, so promoting a skill from T3 to T1 is a one-line
  edit — the agent can do it itself.

---

## 11. Memory and retrieval

### 11.1 Progressive retrieval

Never load memory into context. Query it; load only the hits.

**Tier 1 — FTS5 (always on).** `memories` and `messages` both have FTS5 indexes.
Measured at **+1 MB for 10,000 rows**. Zero network, zero dependencies. This is
most of what "second brain" means in practice.

**Tier 2 — embeddings (optional).** Computed via a provider's embedding endpoint,
stored as a SQLite BLOB, ranked by cosine similarity in pure Python. No numpy, no
vector DB, no plugin. A few thousand vectors ranks in milliseconds. Enabled by
config; off by default.

**Routing:** keyword-shaped queries go to FTS5; conceptual queries go to
embeddings when enabled; results merge and dedupe by rowid.

### 11.2 What gets remembered

| Kind | Trigger | Lifetime |
|---|---|---|
| `fact` | model calls `memory(add)` | permanent |
| `note` | end-of-turn durable-finding extraction | permanent |
| `episode` | summarised conversation window | until pruned |
| `todo` | explicit | until completed |

Every write records provenance (`session_id`, `agent_id`, `kind`, `confidence`) —
that is what makes §12 possible.

---

## 12. Team protocol

You asked that sessions sync into a shared brain and that agents work together
"like a group bot". Two mechanisms:

**Shared brain (passive).** Every session writes to the same `memories` and
`notes` tables. A `team_feed` view surfaces recent findings across all sessions.
Nothing coordinates; the shared store *is* the coordination.

**Direct messaging (active).** An `agent_messages` table lets workers address each
other by id. A worker can leave a message for a sibling and continue; the sibling
reads it on its next turn. The supervisor routes.

**The flush rule.** After N messages (default 10) or at turn end, a session
extracts durable findings and publishes them. This is your "must update everything
into the main brain" requirement, made concrete and bounded.

**Honest scope note.** "Like a group bot" implies turn-taking and addressing
rules, which is a real protocol. It is Phase 7, deliberately after everything else
works, because a team protocol bolted onto an unstable core produces agents that
talk past each other. It is buildable; it is not free.

---

## 13. Concurrency and subagents

| Setting | Value | Enforcement |
|---|---|---|
| Concurrent sessions | 2 | supervisor |
| Subagents | cap 3, prefer 1 | supervisor |
| Memory per worker | ~28 MB | measured |
| Typical resident total | 150–200 MB | supervisor + portal + poller + workers |

The cap is enforced in the supervisor, **not in the prompt**. A model asked
politely to limit itself will not; a supervisor that refuses to spawn a fourth
worker will.

Delegation is sequential by default. Parallel delegation is allowed up to 3 only
when explicitly requested, because each worker is a full process and the device is
already swapping.

---

## 14. Data model

### 14.1 Schema

```sql
-- conversation
sessions(id TEXT PK, title TEXT, model TEXT, provider TEXT, summary TEXT,
         created_at INT, updated_at INT)
messages(id INTEGER PK AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,
         tool_calls TEXT, tool_call_id TEXT, name TEXT, created_at INT,
         interrupted INT DEFAULT 0)
  INDEX (session_id, id)
messages_fts USING fts5(content)

-- memory
memories(id INTEGER PK AUTOINCREMENT, content TEXT, kind TEXT, session_id TEXT,
         agent_id TEXT, confidence REAL, created_at INT, used_at INT)
memories_fts USING fts5(content)
notes(id INTEGER PK AUTOINCREMENT, session_id TEXT, agent_id TEXT, kind TEXT,
      content TEXT, confidence REAL, created_at INT)

-- team
agent_messages(id INTEGER PK AUTOINCREMENT, from_agent TEXT, to_agent TEXT,
               body TEXT, created_at INT, read_at INT)

-- providers
providers(id TEXT PK, label TEXT, base_url TEXT, api_style TEXT,
          default_model TEXT, headers_json TEXT, enabled INT, priority INT)
provider_keys(id INTEGER PK AUTOINCREMENT, provider_id TEXT, key TEXT, label TEXT,
              status TEXT, fail_count INT, cooldown_until INT, last_used INT)
usage(provider_id TEXT, key_id INT, day TEXT, requests INT,
      prompt_tokens INT, completion_tokens INT,
      PRIMARY KEY (provider_id, key_id, day))

-- work
jobs(id INTEGER PK AUTOINCREMENT, kind TEXT, schedule TEXT, command TEXT,
     next_run INT, last_run INT, enabled INT)
todos(id INTEGER PK AUTOINCREMENT, text TEXT, done INT, created_at INT)
```

### 14.2 Pragmas

```sql
PRAGMA journal_mode = WAL;        -- readers don't block writers; crash-safe
PRAGMA synchronous  = NORMAL;     -- safe under WAL, far cheaper on flash
PRAGMA temp_store   = MEMORY;     -- temp b-trees stay out of flash
PRAGMA cache_size   = -1000;      -- 1 MB page cache (SQLite default is 2 MB)
PRAGMA busy_timeout = 5000;       -- workers wait rather than fail
PRAGMA foreign_keys = ON;
```

`cache_size=-1000` is the deliberate departure from SQLite's defaults, sized for
this device. A 10,000-row table plus its FTS index costs ~1 MB of RAM at this
setting, measured.

### 14.3 Migrations

A `schema_version` row in a `meta` table. Migrations are a list of
`(version, sql)` applied in order at startup, each inside a transaction. Forward
only; no down-migrations. A migration that fails rolls back and refuses to start
rather than running on a half-migrated database.

### 14.4 Why rows, not a blob

Storing a conversation as one JSON document and rewriting it each turn is
O(history) write amplification — every save rewrites everything, wearing the flash
and spiking RAM to the size of the whole conversation. Rows make each append O(1)
and keep the working set bounded by the window, not the history.

### 14.5 Growth

At 100 messages/day: 10,000 messages is ~100 days and 5.8 MB on disk, 1 MB in RAM.
The device has 3.9 GB free. Retention is a privacy question, not a capacity one.
Default: keep everything; `/prune <days>` available.

---

## 15. Surfaces

### 15.1 CLI

A REPL over the same core, plus an ANSI status bar.

**Status bar** (single line, refreshed in place):
`provider · model · key-pool health · tokens this session · context fill · session id`

**Slash commands** — the full surface, mirroring Hermes' registry so nothing is
missing:

| Group | Commands |
|---|---|
| Session | `/new` `/resume` `/sessions` `/search` `/rename` `/prune` `/export` |
| Config | `/model` `/provider` `/keys` `/config` `/skin` |
| Tools | `/tools` `/skills` `/skill` `/todo` `/jobs` |
| Info | `/status` `/usage` `/context` `/memory` `/team` `/help` |
| Control | `/stop` `/retry` `/yolo` `/exit` |

Plus `!command` to run a shell command directly, and `/` with no argument to list
everything.

**Input:** `readline` gives history, reverse search and editing. Multiline paste is
detected and held until a blank line.

### 15.2 Web portal

Python's built-in `http.server` with a small router. No framework.

| Route | Purpose |
|---|---|
| `/` | status dashboard: providers, key health, usage, context, uptime |
| `/chat` | chat UI over the same core (plain HTML + fetch, no framework) |
| `/sessions` | browse and resume sessions |
| `/memory` | search and edit memories |
| `/jobs` | cron jobs, last run, next run |
| `/settings` | providers, keys, config |
| `/api/*` | JSON endpoints backing the above |

**Bind:** `127.0.0.1` by default. `0.0.0.0` for LAN access from your laptop at
`http://<phone-ip>:<port>` — **requires token auth** (C8). The token is generated
at install and printed once.

### 15.3 Telegram

Hand-rolled long-poll over `urllib`. Zero dependencies.

- `getUpdates` with a long timeout (server-side blocking), so there is no busy
  loop and the CPU sleeps.
- Reconnect with exponential backoff on socket errors; a health check confirms
  progress after reconnect.
- Commands mirror the CLI's slash set, registered with `setMyCommands` so they
  appear in the Telegram menu.
- Long replies are split at Telegram's 4096-character limit on paragraph
  boundaries.
- Inline keyboards for `clarify` and for provider/key confirmation prompts.
- Allowlist by user id; anything else is refused with a single short message.

---

## 16. Security model

| Concern | Control |
|---|---|
| **Command execution** | Shell commands require approval by default. `--yolo` / `/yolo` disables it per session and says so loudly. Non-interactive runs deny by default. |
| **Filesystem** | Every path resolved with `realpath` and checked against `sandbox_roots`. Symlink escapes rejected. |
| **Network binds** | Loopback free; any other bind requires a token. Refuses to start otherwise. |
| **Secrets at rest** | `auth.json` and `config.json` mode 0600. Keys never logged, never echoed, redacted from error strings. |
| **Secrets in transit** | TLS verified against the system CA bundle. No `verify=False` anywhere. |
| **Prompt injection** | Fetched web content and third-party skill bodies are untrusted. Skill bodies load as user messages, never system. Fetched content is clearly delimited and never treated as instructions. |
| **Tool output as data** | Tool results are data, never commands. The agent is told this in the system prompt, and the shell approval gate is the enforcement. |
| **Key exposure** | Keys are stored per provider; a dead key is quarantined, not deleted. `/keys` shows status only, never the value. |

The one thing I will not do is ship a LAN-reachable panel without auth. It is
reachable by every device on the network, including untrusted ones.

---

## 17. Failure modes and recovery

| Failure | Detection | Recovery | Data loss |
|---|---|---|---|
| Telegram socket drops | socket error | reconnect + backoff | none |
| Telegram rate-limits | 429 | backoff, respect `retry_after` | none |
| Worker crashes | supervisor waitpid | respawn, resume from SQLite | none |
| Supervisor killed | boot hook / `mh start` | relaunch | none |
| Phone reboots | Termux:Boot | `mh start` | none |
| Killed mid-turn | `interrupted` flag | turn replayed on resume | none |
| Provider 429 | response code | key cooldown, next key | none |
| Provider 5xx | response code | retry ×2, then next | none |
| All providers down | selection empty | keyless fallback, else clear error | none |
| SQLite locked | busy timeout | wait, then fail loudly | none |
| Mid-stream drop | read error | error surfaced (resume not yet implemented) | partial reply |
| Disk full | write error | refuse the write, surface it | the write |

The design rule: **every failure is either retried or reported, never silently
swallowed.** A phone agent that dies quietly is worse than one that says why.

---

## 18. Test plan and acceptance criteria

Tests run offline against a canned SSE endpoint — no API key, no credits burned.

### 18.1 Per-tool acceptance

Every tool in §9 gets a test asserting: correct output on the happy path, a
specific error string on each documented failure, and that the output cap is
enforced. A tool without a passing test is not shipped.

### 18.2 Contract tests

| Area | Assertion |
|---|---|
| Tool-call reassembly | arguments split across SSE deltas reassemble exactly (already proven) |
| Stream prefix stability | frame N is a prefix of frame N+1 |
| Provider rotation | a 429 on key 1 moves to key 2 without a user-visible failure |
| Fallback chain | provider A fully down → provider B answers |
| Key quarantine | a 401 marks the key dead and it is not retried |
| Usage accounting | every request increments exactly one row |
| Context window | a 100-message session never sends more than the window |
| Summarisation | history past the window is folded and the window stays bounded |
| Output cap | a 10 MB tool return is truncated and RSS does not spike |
| Path sandbox | `../` and symlink escapes are refused |
| Restart recovery | kill the supervisor mid-turn; on restart the turn replays and nothing is lost |
| Single writer | two workers writing concurrently never corrupt the DB |
| Subagent cap | a fourth concurrent delegate is refused by the supervisor |
| Auth gate | a non-loopback bind without a token refuses to start |
| Secret redaction | a key never appears in logs or error strings |

### 18.3 Measurement tests

The existing harness (`tests/measure_children.py`, `tests/measure_features.py`)
becomes a regression gate: resident RSS and per-feature marginal cost are asserted
against ceilings, so a future change that doubles memory fails CI rather than the
phone.

---

## 19. Phases and exit criteria

Each phase ends with a demonstrable result, not a percentage.

**Phase 1 — identity and providers.**
Exit: a real Nous sign-in completes on the phone (guest mint, claim URL printed,
promotion observed); the tested keys load, health-check, rotate and fall back; the
status bar shows live pool health.

**Phase 2 — resident core.**
Exit: `mh install && mh start` leaves a supervisor running with a wake-lock; kill
it and it returns; reboot the phone and it returns; `mh status` is accurate.

**Phase 3 — surfaces.**
Exit: a message sent from Telegram, the portal and the CLI all land in the same
session list and the same database; a Telegram drop reconnects without a restart;
the portal refuses a LAN bind without a token.

**Phase 4 — tools.**
Exit: every tool in §9 passes its acceptance test; a real task (fetch a page, save
it, summarise it) runs end to end on the phone.

**Phase 5 — skills.**
Exit: the T1 index loads; `skill_view` injects a body as a user message; the agent
creates a new skill with `skill_manage` and uses it in the next session.

**Phase 6 — second brain.**
Exit: FTS5 recall works across 10,000 messages; optional embeddings rank a
conceptual query; both stay inside the memory ceiling.

**Phase 7 — team.**
Exit: two concurrent sessions exchange a message and both findings appear in the
team feed; the flush rule fires at the configured threshold.

**Phase 8 — optional.**
Touch automation (Tier C), MCP seam. Neither blocks anything else.

---

## 20. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Android kills the process. Independent of memory. | High | Restart-first: supervisor, wake-lock, battery exemption, Termux:Boot, SQLite state. |
| 2 | Swap pressure — 1.15 GB already consumed. | High | Lean resident stack; subagent cap enforced in the supervisor. |
| 3 | Single-writer contention across workers. | High | Supervisor owns writes; WAL + `busy_timeout`. |
| 4 | Nous internal endpoints change. | Medium | Typed errors, clean fallback to a plain key, never a hang. |
| 5 | Key-pool rot — 9 of 23 supplied keys are dead on arrival. | Medium | Health-check on load; quarantine with cooldown; pool health visible. |
| 6 | LAN-exposed portal. | High if enabled | Token required on any non-loopback bind. Non-negotiable. |
| 7 | Uncapped tool output spikes RSS. | Medium | Hard cap + bounded tool rounds; regression-tested. |
| 8 | Team protocol degrades into noise. | Medium | Phase 7, after the core is stable; addressing rules defined first. |
| 9 | Prompt-cache breakage from skill injection. | Medium | Skill bodies load as user messages, never the system prompt. |
| 10 | ADB dependency for touch automation is fragile. | Medium | Tier C is opt-in and fails loudly. |
| 11 | Secrets at rest — 23 keys on the device. | Medium | 0600 files, redaction, `/keys` shows status only. |
| 12 | Mid-stream network drop. | Medium | Pre-response retry works; mid-stream resume is a known gap, Phase 1 backlog. |
| 13 | Tool schemas bloat every request. | Low | Tool surface audited; each addition must justify its per-request cost. |
| 14 | Termux:API app not installed. | Low | Degraded mode with a clear message; wake-lock and TTS unavailable until installed. |

---

## 21. Decisions and open items

**Locked** (change any and I revise the plan):

| # | Decision |
|---|---|
| 1 | No dependencies. Pure Python stdlib, end to end. |
| 2 | JavaScript rendering: out. No Chromium, no remote rendering API. |
| 3 | Touch automation: deferred to Phase 8, opt-in. |
| 4 | Skills: ship the entire collection in three tiers; ~40 always indexed. |
| 5 | Team protocol: Phase 7, after the rest works. |
| 6 | LAN access: yes, behind token auth. |
| 7 | Nous first-run: always attempt promotion; guest tier as fallback. |
| 8 | Retention: keep everything; `/prune <days>` available. |
| 9 | MCP: seam only, no server shipped. |
| 10 | Telegram only for messaging. |
| 11 | Concurrency: 2 sessions, 3 subagents max, prefer 1. |
| 12 | Restart-first lifecycle with `mh start` as the single command. |

**Yours to action:**

- **Groq** — the organization is restricted; needs a support ticket.
- **Termux:API app** — confirm it is installed; wake-lock and TTS depend on it.

**Open:**

- Which tier each of the 213 skills lands in — I have proposed T1 in §10.2; the
  full ranking is in `minihermes-specs/skills.md` for your review.

---

## 22. Install, distribution and backup

### 22.1 Distribution decision: Telegram zip — proven

**Chosen: a zip delivered over Telegram.** Reasons, in order:

1. No git — your stated preference, and git would need the binary plus a remote.
2. No hosting to maintain.
3. It works from the phone itself, with no second device.
4. **It has been exercised.** `hermes send --to telegram:<chat_id> "MEDIA:<path>"`
   delivered a 93 KB zip today, confirmed by `message_id: 546`. This is not a
   hypothetical path.

**Rejected: git.** Needs `git` installed, a remote to host, and a checkout to
maintain on the phone. More moving parts for no benefit at this size.

**Fallback: HTTP from a host.** Needed if a payload exceeds Telegram's 50 MB
per-file bot upload limit. The core is small (the plan + harness is 93 KB; the
program will be a few hundred KB), so this is a contingency, not the primary path.

### 22.2 Installing from the zip

On the phone, no packages required — `zipfile` is stdlib:

```sh
termux-setup-storage                 # one-time: grants access to /sdcard
cd ~ && mkdir -p minihermes && cd minihermes
python3 - <<'PY'
import zipfile, glob
z = sorted(glob.glob('/sdcard/Download/minihermes*.zip'))[-1]
zipfile.ZipFile(z).extractall('.')
print('extracted', z)
PY
python3 -m minihermes install
```

`install` is the bootstrap from §6.1: verify Python and FTS5, check the Termux:API
app, write `config.json` (0600), create and migrate the database, bind `mh` into
`$PREFIX/bin`, write the Termux:Boot hook, open the battery-exemption page, then
run the Nous flow.

**Known friction, stated honestly:** the file must be tapped in Telegram and saved
to Downloads first — there is no way to fetch it straight from the chat into
Termux without a bot token on the device. If that matters, `mh update` can pull
from a configured URL instead (§22.3).

### 22.3 Updates

The zip is the distribution unit.

```sh
python3 -m minihermes install --upgrade
```

Replaces code, **preserves `~/.minihermes`** (config, database, auth, skills). A
`VERSION` file inside the zip lets it refuse a downgrade and report a
schema-version mismatch rather than corrupting the database.

`mh update` can alternatively pull from a URL you set in config, for the case
where re-sending a file over Telegram each time becomes tedious.

### 22.4 Backup — `mh backup`

Modeled on `hermes backup`, which does the same job for Hermes itself.

```
mh backup                    # full: config, auth, db, skills, logs
mh backup --quick            # critical state only: config, auth, db
mh backup --label before-x
mh backup --keep 3           # rotate, keep the newest 3 (default 3, 0 = keep all)
mh backup --send telegram    # deliver the zip to your Telegram
mh backup --to /sdcard/Download
```

Archive layout:

```
minihermes-backup-<timestamp>.zip
  MANIFEST.json      version, timestamp, schema_version, file list with sizes
  config.json
  auth.json
  minihermes.db
  skills/
  logs/              (full backup only)
```

**The one technical detail that matters:** the database is snapshotted with
`VACUUM INTO`, **not** a file copy. Copying a live SQLite file can capture a torn
WAL and produce a backup that looks fine and restores corrupt. `VACUUM INTO`
writes a consistent, compacted copy while the agent keeps running. SQLite 3.54 on
the device supports it. A naive `shutil.copy` here is the classic way to lose a
database.

**Restore:**

```
mh restore minihermes-backup-<timestamp>.zip
```

- refuses a manifest whose `schema_version` is newer than the running code
- takes a `--quick` safety backup of the current state first, suffixed `.pre-restore`
- runs `PRAGMA integrity_check` on the extracted database **before** swapping it in
- if any step fails, nothing is replaced

### 22.5 The two zips are different things

| Zip | Contains | Purpose | Replaceable? |
|---|---|---|---|
| **program zip** | code + skills + specs | install and update | yes — versioned, disposable |
| **backup zip** | config + auth + database + skills | your data | no — must survive any program change |

They are kept separate deliberately. Mixing them is how an upgrade silently
becomes a data-loss event.

### 22.6 Scheduled backup

`mh backup --quick` registers cleanly as a `cronjob_manage` job — daily, writing
to `/sdcard/Download`. Because the supervisor is resident, this is a real
scheduler rather than a shell cron, and missed runs are recorded rather than
silently skipped. It is still subject to the process being killed (§3.1), so a
backup that must happen should also be reachable by hand.

---

## Appendix A — provider test results (executed 2025-10-13)

| Provider | Key label | Result |
|---|---|---|
| OpenRouter | asad, M789, M56789, M6789, POPKIDON, POPKIDON1 | **all 200** |
| Gemini | asad | works — `gemini-2.5-flash` |
| Gemini | M6789 | works — `gemini-2.5-flash-lite` |
| Gemini | M56789 | partial — model gating + 503 on capacity |
| Gemini | POPKIDON, POPKIDON1 | 403 project denied |
| ApiFreeLLM | Asad, M789, M567, POPKIDON, POPKIDON1 | works — community tier, 50/day |
| ApiFreeLLM | M6789 | transient 503 |
| Groq | all six | 403 `organization_restricted` |
| Pollinations | (keyless) | works, OpenAI-compatible |

Totals: 13 keyed keys working, 9 dead, 1 keyless provider.

Method note: the first pass used the default `Python-urllib` User-Agent and got
Cloudflare 1010 on Groq and ApiFreeLLM. A browser-like UA resolved it. That is a
real implementation requirement, not a test artifact — the client must send a
plausible User-Agent or those providers block it.

---

## Appendix B — measurements

Per-child peak RSS (`tests/measure_children.py`):

| Configuration | Peak RSS |
|---|---|
| Bare interpreter | 9.9 MB |
| + stdlib (sqlite3, ssl, urllib, gzip, hashlib, subprocess, readline) | 22.9 MB |
| + asyncio | 26.5 MB |
| Full agent wired, DB open, context built | 25.9 MB |
| Full agent + a real streamed turn | **27.6 MB** |

Marginal feature cost (`tests/measure_features.py`):

| Feature | Marginal RSS |
|---|---|
| Skills index, 60 files | +0.1 MB |
| Session DB empty, 1 MB cache | +0.6 MB |
| 10,000 messages + FTS5 index | +1.0 MB (5.8 MB on disk) |
| Uncapped 10 MB tool output | **+10 MB spike** |

Method note: `RUSAGE_CHILDREN` was rejected for multi-child measurement — it
returns the max across every child ever waited for, so every reading after the
first is a copy. Each child self-reports its own kernel `VmHWM` instead.

---

## Appendix C — skill inventory

213 skills across `skills/` (58) and `optional-skills/` (155), totalling 2.27 MB.
Full per-category listing:

| Category | Count |
|---|---|
| optional-skills/creative | 31 |
| optional-skills/mlops | 25 (+ 11 sub-areas) |
| optional-skills/research | 15 |
| skills/productivity | 14 |
| skills/software-development | 12 |
| optional-skills/productivity | 12 |
| skills/creative | 10 |
| optional-skills/finance | 9 |
| optional-skills/autonomous-ai-agents | 7 |
| optional-skills/devops | 7 |
| optional-skills/security | 6 |
| optional-skills/software-development | 6 |
| skills/autonomous-ai-agents | 5 |
| optional-skills/web-development | 5 |
| skills/apple | 4 |
| skills/research | 4 |
| optional-skills/blockchain | 3 |
| optional-skills/mcp | 3 |
| optional-skills/payments | 3 |
| skills/media | 3 |
| skills/email | 2 |
| optional-skills/gaming | 2 |
| optional-skills/health | 2 |
| skills/devops, skills/note-taking, skills/social-media, skills/web | 1 each |
| optional-skills/(root, communication, data-science, dogfood, email, migration, smart-home, social-media) | 1 each |

The full ranked catalog with triggers is in `minihermes-specs/skills.md`.

---

*End of plan. No code written. Awaiting your go-ahead.*
