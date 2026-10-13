# FHONE

A personal AI agent that runs entirely on a phone. Pure Python standard library —
no pip packages, no frameworks, no build step.

Three ways in — a terminal CLI, a local web portal, and Telegram — over one
brain and one database. Signs in to Nous on first run, rotates across whatever
other providers you configure, remembers across sessions, and can edit its own
skills.

**Measured: 27.9 MB peak on a full streamed turn; 29.0 MB resident as a
supervisor with the web portal and scheduler running.**

---

## Install

Two routes. **Option B is more reliable** — it skips `/sdcard` entirely, which is
where installs usually go wrong (`termux-setup-storage` not run, or Telegram
saving to `Download/Telegram/` rather than `Download/`).

### Option A — from a zip already on the phone

```sh
termux-setup-storage          # required: without it Termux cannot see /sdcard
cd ~ && mkdir -p fhone && cd fhone

# find it wherever it landed — Telegram's default path is
# /storage/emulated/0/Download/Telegram/, not /sdcard/Download/
Z=$(find /sdcard /storage/emulated/0 -iname "fhone*.zip" 2>/dev/null | head -1)
echo "found: $Z"

python3 -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall('.')" "$Z"
python3 -m fhone install
```

If `found:` is empty, the file never downloaded. Long-press it in Telegram and
choose "Save to Downloads", then re-run.

### Option B — download directly into Termux

```sh
cd ~ && mkdir -p fhone && cd fhone
python3 -c "import urllib.request; urllib.request.urlretrieve('URL_TO_ZIP','fhone.zip')"
python3 -c "import zipfile; zipfile.ZipFile('fhone.zip').extractall('.')"
python3 -m fhone install
```

No storage permission and no path guessing. `zipfile` and `urllib` are stdlib, so
nothing needs installing first.

`install` checks Python and FTS5, checks for the Termux:API **app**, writes
`~/.fhone/config.json` (0600), creates and migrates the database, binds the
`fhone` command, installs the bundled skills, and runs the Nous sign-in.

If no model is configured, install automatically starts the **interactive setup
wizard** — pick a provider, enter an API key, choose a model, toggle surfaces.
Cancel anytime and return to the terminal.

Then:

```sh
fhone                      # interactive
fhone setup                # re-run the setup wizard
fhone -q "what's using my disk?"     # one-shot
fhone doctor               # health check
```

## Commands

```
fhone                      chat (default)
fhone -q "..."             one-shot, prints only the answer
fhone install              bootstrap
fhone start                start the resident supervisor (detached)
fhone stop                 stop it
fhone restart              stop, then start
fhone service status       pid, uptime, surfaces, wake-lock
fhone boot                 write the Termux:Boot hook (survives a reboot)
fhone status               providers, keys, usage, sessions
fhone doctor               health check
fhone backup               full backup zip
fhone backup --quick       critical state only
fhone backup --send telegram
fhone restore --path FILE
fhone provider list
fhone provider add <id> <base_url> [--style openai|gemini|apifreellm] [--model M]
fhone provider key <id> <api-key> [--label L]
fhone provider probe [id]  live-check every key
fhone portal token         generate a token (required for a LAN bind)
fhone portal status
fhone telegram set <token>
fhone telegram allow <user-id>
fhone skills list
fhone version
```

## Surfaces

Three ways in, one core, one database.

| Surface | How | Notes |
|---|---|---|
| **CLI** | `fhone` | readline REPL, 30+ slash commands, live status bar |
| **Web portal** | `fhone start`, then open the URL | stdlib `http.server`, no framework. Status dashboard + chat |
| **Telegram** | `fhone telegram set <token>` then `fhone start` | hand-rolled long-poll over `urllib`, zero dependencies |

All three share the same agent, tools, providers and memory. A message from any
of them lands in the same `messages` table.

**Portal binding:** `127.0.0.1` is free. Any other bind requires a token —
`fhone portal token` generates one. The portal *refuses to start* on a
non-loopback bind without it, because such a panel is reachable by every device
on the network.

**Telegram:** set an allowlist with `fhone telegram allow <your-user-id>` or the
bot answers anyone who finds it.

**Restart-first.** Android will terminate the supervisor — the low-memory killer,
the OEM background-app killer, swiping from Recents, Doze, or a reboot.
`termux-wake-lock` prevents none of those; it stops the CPU sleeping, it does not
make the process immortal. So the design assumes death: all durable state is in
SQLite, the pidfile records `(pid, process start time)` so a recycled PID is
never mistaken for a live supervisor, and `fhone start` is idempotent — one
command brings it back, losslessly.

### Slash commands (in the REPL)

```
session
  /new [title]        start a fresh session
  /sessions           list recent sessions
  /resume <id>        switch to a session
  /rename <title>     title the current session
  /history [n]        show last n messages (default 10)
  /search <query>     search all past messages
  /export [path]      dump the session as JSON
  /prune <days>       delete messages older than N days
  /branch [title]     branch current session into a new one
  /undo [n]           undo last n user turns (default 1)
  /retry              retry the last message
  /clear              clear screen and start fresh

config
  /model [name]       show or set the model
  /provider [list|add|remove]   manage providers
  /keys [provider]    key pool health
  /config             show effective config (secrets masked)
  /yolo               toggle shell approval for this session
  /verbose            toggle verbose tool output
  /focus              toggle focus view (prompt + answer only)
  /timestamps         toggle timestamps on messages
  /approvals          show/set approval mode
  /reasoning          manage reasoning effort
  /fast               toggle fast mode

tools
  /tools              list available tools
  /skills [query]     list indexed skills
  /skill <name>       load a skill body
  /todo [list|add ...]  task list
  /jobs               scheduled jobs
  /cron               manage scheduled tasks (list/add/remove)
  /memory [query]     search or list memories
  /remember <text>    save a memory
  /forget <id>        delete a memory
  /team               recent notes from all sessions

info
  /status             full status: providers, keys, usage, context
  /usage              token and request usage
  /context            context window fill
  /battery            battery status (Termux:API)
  /whoami             show user identity
  /profile            show profile info
  /diff               show git diff (if in a git repo)

control
  /save               export conversation (alias for /export)
  /snapshot           create a state snapshot
  /rollback           restore from a snapshot
  /steer <text>       inject a message after next tool call
  /goal <text>        set a standing goal
  /refine             review conversation and save lessons
  /review             spawn a review subagent
  /plan <text>        write an implementation plan
  /compress           compress conversation context
  /help               this text
  /exit               quit

!<command>          run a shell command directly
```

---

## Providers

Nothing is hardcoded. Providers, keys, wire style and priority all live in
SQLite and are editable at runtime.

Three wire styles are supported, each verified against a live endpoint:

| style | shape |
|---|---|
| `openai` | `{model, messages, tools, stream}` — OpenRouter, Groq, Pollinations, most others |
| `gemini` | `{contents:[{parts:[{text}]}]}`, key in the query string |
| `apifreellm` | `{message}` → `{response}` |

Selection runs providers by priority, keys by health, and handles failures
precisely:

- **429** → cool the key down (exponential, capped), try the next
- **401/403** → mark the key dead, never retry it
- **5xx / socket** → retry twice inside the client, then move on
- **provider exhausted** → next provider in the chain
- **all exhausted** → a clear error naming what failed, never a silent hang

Usage is recorded per key per day and shown in the status bar.

---

## Tools (40)

`read_file` `write_file` `patch` `search_files` `list_dir` `terminal`
`process_manage` `execute_code` `memory` `session_search` `todo_list`
`skills_list` `skill_view` `skill_manage` `delegate_task` `cronjob_manage`
`web_search` `web_extract` `http_get` `http_post` `vision_analyze` `image_generate`
`text_to_speech` `clarify` `sysinfo` `file_move` `file_copy` `file_delete`
`env_get` `env_set` `git` `curl` `json` `csv` `base64` `hash` `random`
`datetime` `regex`

Every tool: returns a string, never raises into the turn loop, caps its output,
validates its arguments, sandboxes its paths, and has a timeout.

`patch` **fails loudly** if the old text is absent — a silent no-op makes the
model believe an edit landed. `skill_view` injects a body as a **user message**,
never the system prompt, because that would break provider-side prompt caching on
every skill load.

---

## Skills

38 bundled, in three tiers. Everything ships; the tier decides what competes for
the model's attention.

| Tier | Count | Behaviour |
|---|---|---|
| T1 | 19 | always indexed |
| T2 | 10 | indexed when the task looks related |
| T3 | 8 | on disk, fetchable by name |

The agent can create, edit and re-tier its own skills via `skill_manage` — the
self-improvement loop.

---

## Backup

```sh
fhone backup                    # full
fhone backup --quick            # config + auth + database
fhone backup --keep 3           # rotation
fhone backup --send telegram    # deliver the zip
fhone restore --path FILE
```

**The database is snapshotted with `VACUUM INTO`, never a file copy.** Copying a
live SQLite file can capture a torn WAL and produce a backup that looks fine and
restores corrupt.

Restore refuses a newer schema version, takes a safety copy first, and runs
`PRAGMA integrity_check` before replacing anything. If a step fails, nothing is
replaced.

---

## Architecture

```
   CLI      ──┐
   Portal   ──┤──▶  core (turn loop · tools · providers · memory)  ──▶ SQLite
   Telegram ──┘
                    ┌──────────────────────────────────┐
                    │ supervisor (restart-first)       │
                    │  portal · poller · scheduler     │
                    └──────────────────────────────────┘
```

| Module | Job |
|---|---|
| `config.py` | config + secrets (env first, 0600 file second) |
| `store.py` | SQLite: schema, migrations, pragmas, every query. One connection per thread |
| `llm.py` | the only inference network code: connection reuse, SSE, retry, gzip |
| `providers.py` | registry, key pools, rotation, fallback, usage |
| `context.py` | window + token budget + rolling summary with a watermark |
| `tools.py` | the 30-tool registry |
| `agent.py` | the turn loop |
| `skills.py` | index, on-demand loading, self-editing |
| `delegate.py` | subagents, capped in the parent |
| `scheduler.py` | cron-style jobs |
| `backup.py` | VACUUM INTO snapshots, verified restore |
| `auth_nous.py` | the Nous sign-in flow |
| `supervisor.py` | resident process, wake-lock, pidfile identity, boot hook |
| `surfaces/portal.py` | stdlib HTTP server + router, token auth |
| `surfaces/telegram.py` | long-poll client with reconnect and backoff |
| `ui.py` | streaming, REPL, slash commands, status bar |

### Design rules that matter

- **Rows, not blobs.** One row per message. A JSON blob per conversation rewrites
  the whole history every turn — O(history) write amplification and a RAM spike.
- **Caps are memory controls.** An uncapped 10 MB tool output is a +10 MB
  allocation in one go. Every tool return is bounded.
- **Window plus summary.** Without both, RSS climbs with conversation length
  until the OS kills the process.
- **FTS5 before embeddings.** Built in, ~1 MB per 10k rows, no network.
- **Progressive retrieval.** Memory is queried, never loaded wholesale.
- **A browser-like User-Agent is required.** The default `Python-urllib/3.x` UA is
  blocked by Cloudflare on several endpoints (verified: error 1010 on Groq and
  ApiFreeLLM until the UA changed).
- **One reused connection.** A fresh TLS handshake is 300–800 ms on mobile data.
- **SQLite pragmas:** `cache_size=-1000` (1 MB), `temp_store=MEMORY`, WAL,
  `synchronous=NORMAL`, `busy_timeout=5000`.

---

## Running on Android

Android **will** terminate the process — the low-memory killer, the OEM
background-app killer, swiping the app from Recents, Doze, or a reboot.
`termux-wake-lock` prevents **none** of these: it stops the CPU sleeping, it does
not make the process immortal.

What actually helps, all of them:

- `termux-wake-lock` while running
- battery-optimization exemption for Termux (Unrestricted / Don't optimize)
- "Allow background activity" in the OEM's app settings
- never swipe Termux from Recents
- Termux:Boot, to restart after a reboot

All durable state is in SQLite, so a restart is cheap and lossless.

---

## Tests

```sh
python3 tests/test_fhone.py        # 100 assertions — the whole core surface
python3 tests/test_regressions.py  #  26 assertions — one per fixed bug
python3 tests/test_migration.py    #  19 assertions — v1→v3 database migrations
python3 tests/test_surfaces.py     #  46 assertions — portal, Telegram, supervisor
python3 tests/measure.py           # footprint, measured from the kernel
```

191 assertions, all offline, no API key.

The suites cover the store, FTS5, provider rotation and quarantine, SSE parsing
with split tool arguments, every tool's happy path and error surface, sandbox
escapes, output caps, skills, scheduling, backup/restore, an end-to-end turn,
database migrations (v1→v3), the portal's routes and its mandatory auth off
loopback, Telegram message splitting and command handling, and a real
supervisor start/stop/restart cycle against a live pidfile.

## Changelog

### 1.001

- 25 new slash commands: /history /clear /branch /undo /retry /verbose /focus
  /timestamps /approvals /reasoning /fast /save /snapshot /rollback /steer
  /goal /refine /review /plan /compress /cron /battery /whoami /profile /diff
- 7 new tools: http_post sysinfo file_move file_copy file_delete env_get env_set
- Provider model routing: providers declare which models they serve; the pool
  never asks a provider for a model it does not have
- `fhone config set/get/list` command
- CLI: `provider add` and `provider models` take positional args
- Schema v3 (`providers.models` column); v1→v3 migration tested
- Install no longer crashes when the Termux:API app is absent
- 191 test assertions (was 138)

### 0.0099

Eight bugs found by a red-first bug hunt and fixed, each with a regression test
that failed before the fix:

| # | Bug | Impact |
|---|---|---|
| 1 | `config.save()` dropped secret keys | `fhone install` silently erased a stored API key |
| 2 | Summarisation re-fired every turn | The same old messages were re-summarised on every turn — a wasted API call each time, forever |
| 3 | `process_manage(log)` blocked on a live pipe | Reading a running job's output hung the whole turn |
| 4 | Connection not drained after a large 4xx | The next request on that client read leftover bytes and corrupted |
| 5 | `patch` accepted an empty `old` | Could insert text throughout a file instead of failing |
| 6 | Pruning orphaned FTS rows | The search index grew without bound |
| 7 | Cron used local time for a UTC schedule | Wrong fire times outside UTC and across DST |
| 8 | Provider client cache keyed only on `(id, key)` | Editing a provider's URL kept using the old connection |

Also: the `fhone` launcher hardcoded a Termux shebang (broke every other host),
streaming never requested a usage chunk (so streamed turns recorded 0 tokens),
and the Nous promotion step posted empty device codes instead of saying it could
not proceed.

Schema is now v2 (`sessions.summarized_upto`); v1 databases migrate in place with
no data loss.

---

## Licence

MIT.
