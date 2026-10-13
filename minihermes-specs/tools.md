# Mini-Hermes — Tool Contracts

Companion to `minihermes-plan-final.md` §9. Every tool's exact schema, error
surface, and acceptance test. A tool without a passing acceptance test is not
shipped.

---

## 0. Universal rules

These bind every tool. Violating one is a bug, not a style choice.

| # | Rule |
|---|---|
| U1 | A tool **returns a string**. It never raises into the turn loop. Any internal exception becomes an `error: <Type>: <message>` string. |
| U2 | Every return is truncated to `tool_output_cap` (default 6000 chars) with a visible `...[truncated N chars]` marker. |
| U3 | Every argument is validated before use. A bad argument returns a specific, actionable message — never a traceback, never a silent default. |
| U4 | Every path argument is resolved with `Path(...).resolve()` and must be inside one of `sandbox_roots`. Symlink escapes are rejected. |
| U5 | Every tool has a timeout. Nothing blocks forever. |
| U6 | Returns are text. Structured data is JSON-encoded into that text. |
| U7 | No tool writes to stdout/stderr directly — all output goes through the return value. |
| U8 | Secrets are never included in a return value, a log line, or an error string. |
| U9 | Every tool is registered with a schema, and that schema is sent on every API request. Additions must justify their permanent per-request cost. |
| U10 | Side-effecting tools (`terminal`, `write_file`, `patch`, `execute_code`, `skill_manage`) are gated by the approval policy where one applies. |

Error string format, used consistently:

```
error: <kind>: <detail>
```

Examples: `error: not_found: /path/to/x`, `error: outside_sandbox: /etc/passwd`,
`error: bad_argument: timeout must be an integer`, `error: timeout: 30s exceeded`.

---

## 1. `read_file`

```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "Read a UTF-8 text file. Returns its contents, capped.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {"type": "string", "description": "File path."},
        "max_bytes": {"type": "integer", "description": "Read cap. 0 = tool_output_cap."},
        "offset": {"type": "integer", "description": "1-based first line. Default 1."},
        "limit": {"type": "integer", "description": "Max lines. Default all."}
      },
      "required": ["path"]
    }
  }
}
```

**Returns:** file text. When `offset`/`limit` are used, prefixed with
`[lines A-B of N]`.

**Errors:** `not_found`, `is_a_directory`, `outside_sandbox`, `permission_denied`,
`not_utf8` (falls back to `errors="replace"`, so this is only for binary content).

**Acceptance:** reads a known file exactly; a missing file returns
`error: not_found`; a path above the sandbox root returns `error: outside_sandbox`;
a 1 MB file returns exactly `tool_output_cap` characters plus the marker.

---

## 2. `write_file`

```json
{
  "type": "function",
  "function": {
    "name": "write_file",
    "description": "Write text to a file, replacing it. Creates parent directories.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "append": {"type": "boolean", "description": "Append instead of replace."}
      },
      "required": ["path", "content"]
    }
  }
}
```

**Returns:** `wrote N bytes to <path>` or `appended N bytes to <path>`.

**Errors:** `outside_sandbox`, `permission_denied`, `disk_full`, `is_a_directory`.

**Acceptance:** writes and reads back byte-identical; `append` adds without
truncating; a parent directory is created; an out-of-sandbox path is refused and
**no file is created**.

---

## 3. `patch`

```json
{
  "type": "function",
  "function": {
    "name": "patch",
    "description": "Replace the first exact occurrence of old with new. Fails if old is absent.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {"type": "string"},
        "old": {"type": "string"},
        "new": {"type": "string"},
        "replace_all": {"type": "boolean"}
      },
      "required": ["path", "old", "new"]
    }
  }
}
```

**Returns:** a unified diff, or `error: not_found: old text not present in <path>`.

**Rule:** this tool **fails loudly**. A silent no-op is the single most damaging
tool behaviour in an agent — the model believes the edit landed and proceeds.

**Errors:** `not_found`, `ambiguous` (when `old` matches >1 and `replace_all` is
false — return the count and refuse), `outside_sandbox`, `permission_denied`.

**Acceptance:** applies and produces a correct diff; a missing `old` returns
`not_found` **and leaves the file unchanged**; a duplicated `old` without
`replace_all` refuses; with `replace_all` it replaces every occurrence.

---

## 4. `search_files`

```json
{
  "type": "function",
  "function": {
    "name": "search_files",
    "description": "Search file contents by regex, or find files by glob.",
    "parameters": {
      "type": "object",
      "properties": {
        "pattern": {"type": "string"},
        "target": {"type": "string", "enum": ["content", "files"]},
        "path": {"type": "string"},
        "file_glob": {"type": "string"},
        "limit": {"type": "integer"}
      },
      "required": ["pattern"]
    }
  }
}
```

**Returns:** `path:line: content` per match (content mode), or paths (files mode).

**Errors:** `bad_regex`, `not_found`, `outside_sandbox`.

**Acceptance:** finds a known string with correct line numbers; a bad regex
returns `bad_regex` rather than crashing; results are capped at `limit`.

---

## 5. `list_dir`

```json
{
  "type": "function",
  "function": {
    "name": "list_dir",
    "description": "List directory entries, sorted.",
    "parameters": {
      "type": "object",
      "properties": {"path": {"type": "string"}},
      "required": ["path"]
    }
  }
}
```

**Returns:** one entry per line, `d ` or `f ` prefixed. Empty directory returns
`(empty)`.

**Errors:** `not_found`, `not_a_directory`, `outside_sandbox`.

---

## 6. `terminal`

```json
{
  "type": "function",
  "function": {
    "name": "terminal",
    "description": "Run a shell command. Requires approval unless yolo.",
    "parameters": {
      "type": "object",
      "properties": {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "description": "Seconds. Default 30, max 600."},
        "workdir": {"type": "string"}
      },
      "required": ["command"]
    }
  }
}
```

**Returns:** `exit=N\n<stdout+stderr, capped>`.

**Errors:** `denied_by_user`, `timeout: Ns exceeded`, `bad_workdir`,
`outside_sandbox` (for `workdir`).

**Acceptance:** `echo hi` returns `exit=0\nhi`; a failing command returns its
non-zero exit code; a `sleep 60` with `timeout=1` returns `timeout: 1s exceeded`
**and the child is killed**; approval denial returns `denied_by_user` and does not
execute.

---

## 7. `process_manage`

```json
{
  "type": "function",
  "function": {
    "name": "process_manage",
    "description": "Manage background processes started with terminal(background=true).",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {"type": "string", "enum": ["list", "poll", "log", "kill"]},
        "session_id": {"type": "string"},
        "offset": {"type": "integer"},
        "limit": {"type": "integer"}
      },
      "required": ["action"]
    }
  }
}
```

**Returns:** `list` → one line per job with pid and status. `poll` → status.
`log` → the tail. `kill` → confirmation.

**Backed by:** the `jobs` table, so jobs survive a supervisor restart.

**Errors:** `not_found: <session_id>`, `bad_action`.

---

## 8. `execute_code`

```json
{
  "type": "function",
  "function": {
    "name": "execute_code",
    "description": "Run Python in an isolated subprocess and return stdout.",
    "parameters": {
      "type": "object",
      "properties": {
        "code": {"type": "string"},
        "timeout": {"type": "integer"}
      },
      "required": ["code"]
    }
  }
}
```

**Returns:** stdout, then stderr if non-empty, capped.

**Isolation:** a separate process, so a crash or a memory blow-up cannot take the
supervisor with it. Runs under the same sandbox root and the same timeout policy.

**Errors:** `timeout`, `syntax_error` (from the child's traceback).

---

## 9. `memory`

```json
{
  "type": "function",
  "function": {
    "name": "memory",
    "description": "Save or search durable facts about the user or environment.",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {"type": "string", "enum": ["add", "search", "list", "forget"]},
        "content": {"type": "string"},
        "query": {"type": "string"},
        "kind": {"type": "string"},
        "id": {"type": "integer"}
      },
      "required": ["action"]
    }
  }
}
```

**Returns:** `add` → `saved memory #N`. `search` → bulleted matches. `list` →
recent entries. `forget` → confirmation.

**Errors:** `bad_argument: content required for add`, `not_found: #N`.

**Note:** FTS5 `MATCH` rejects raw punctuation; the implementation falls back to a
`LIKE` scan when the query raises, so a query like `what's the "deal"?` still
returns results instead of an error.

---

## 10. `session_search`

```json
{
  "type": "function",
  "function": {
    "name": "session_search",
    "description": "Search past conversations by keyword.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer"}
      },
      "required": ["query"]
    }
  }
}
```

**Returns:** matches as `session_id | role | snippet`.

**Errors:** none on a bad query — it degrades to `LIKE`.

---

## 11. `todo_list`

```json
{
  "type": "function",
  "function": {
    "name": "todo_list",
    "description": "Track a task list across turns.",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {"type": "string", "enum": ["add", "list", "complete", "clear"]},
        "text": {"type": "string"},
        "id": {"type": "integer"}
      },
      "required": ["action"]
    }
  }
}
```

**Returns:** the current list after the operation, `[ ]` / `[x]` prefixed.

**Errors:** `bad_argument: text required for add`, `not_found: #N`.

---

## 12. `skills_list`

```json
{
  "type": "function",
  "function": {
    "name": "skills_list",
    "description": "List indexed skills with descriptions.",
    "parameters": {
      "type": "object",
      "properties": {
        "tier": {"type": "integer", "description": "1, 2 or 3. Omit for all indexed."},
        "query": {"type": "string"}
      }
    }
  }
}
```

**Returns:** `name — description` per line, grouped by tier.

**Note:** returns the **index only**. Bodies are never returned here; that is
`skill_view`'s job, and it is what keeps the index cheap.

---

## 13. `skill_view`

```json
{
  "type": "function",
  "function": {
    "name": "skill_view",
    "description": "Load a skill's full body into context.",
    "parameters": {
      "type": "object",
      "properties": {"name": {"type": "string"}},
      "required": ["name"]
    }
  }
}
```

**Returns:** the skill body, capped.

**Critical invariant:** the body is injected as a **user message**, never into the
system prompt. Injecting it into the system prompt would invalidate the provider's
cached prefix on every skill load, multiplying cost and latency.

**Errors:** `not_found: <name>` (with a near-miss suggestion when the name is
close to an existing one).

---

## 14. `skill_manage`

```json
{
  "type": "function",
  "function": {
    "name": "skill_manage",
    "description": "Create, edit or delete a skill. This is the self-improvement loop.",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {"type": "string", "enum": ["create", "edit", "delete", "set_tier"]},
        "name": {"type": "string"},
        "content": {"type": "string"},
        "tier": {"type": "integer"}
      },
      "required": ["action", "name"]
    }
  }
}
```

**Returns:** confirmation with the resulting path.

**Validation:** `create` requires valid frontmatter (`name`, `description`). An
invalid frontmatter is refused with the specific parse error, and **no file is
written** — a half-written skill would be loaded on the next turn.

**Errors:** `bad_frontmatter: <detail>`, `already_exists`, `not_found`,
`outside_sandbox`.

---

## 15. `delegate_task`

```json
{
  "type": "function",
  "function": {
    "name": "delegate_task",
    "description": "Spawn a subagent for an independent subtask. Returns its final summary.",
    "parameters": {
      "type": "object",
      "properties": {
        "goal": {"type": "string"},
        "context": {"type": "string"},
        "timeout": {"type": "integer"}
      },
      "required": ["goal"]
    }
  }
}
```

**Returns:** the subagent's final summary text.

**Cap:** 3 concurrent, enforced by the supervisor. A fourth call returns
`error: subagent_limit: 3 concurrent subagents already running` — **not** a
queued request. A model asked politely to limit itself will not; the supervisor
refuses.

**Errors:** `subagent_limit`, `timeout`, `spawn_failed`.

---

## 16. `cronjob_manage`

```json
{
  "type": "function",
  "function": {
    "name": "cronjob_manage",
    "description": "Schedule a recurring job.",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {"type": "string", "enum": ["create", "list", "pause", "resume", "remove", "run_now"]},
        "schedule": {"type": "string", "description": "'30m', 'every 2h', or a 5-field cron expression."},
        "command": {"type": "string"},
        "id": {"type": "integer"}
      },
      "required": ["action"]
    }
  }
}
```

**Returns:** the job list after the operation, with last-run and next-run.

**Backed by:** the `jobs` table plus the supervisor's tick. Because the supervisor
is resident, this is a real scheduler rather than a shell cron — but it is still
subject to the process being killed (§3.1 of the plan). Missed runs are recorded,
not silently skipped.

**Errors:** `bad_schedule`, `not_found: #N`.

---

## 17. `web_search`

```json
{
  "type": "function",
  "function": {
    "name": "web_search",
    "description": "Search the web.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer"}
      },
      "required": ["query"]
    }
  }
}
```

**Returns:** `title — url — snippet` per result.

**Implementation:** keyless DuckDuckGo HTML endpoint parsed with `html.parser`. If
a search API is configured in the provider registry, that is used instead.

**Requires:** a browser-like `User-Agent`. The default `Python-urllib/3.x` UA is
blocked by Cloudflare on several endpoints (measured — error 1010 on Groq and
ApiFreeLLM until the UA was changed). This is a hard implementation requirement.

**Errors:** `blocked: 403` (with the UA note), `timeout`, `no_results`.

---

## 18. `web_extract`

```json
{
  "type": "function",
  "function": {
    "name": "web_extract",
    "description": "Fetch a URL and return readable text.",
    "parameters": {
      "type": "object",
      "properties": {
        "url": {"type": "string"},
        "max_chars": {"type": "integer"}
      },
      "required": ["url"]
    }
  }
}
```

**Returns:** page text with tags stripped, capped.

**Errors:** `blocked`, `timeout`, `not_html`, `too_large`.

**Security:** fetched content is **untrusted input**. It is delimited clearly in
the message and never treated as instructions. This is the primary prompt-
injection surface in the whole system.

---

## 19. `http_get`

```json
{
  "type": "function",
  "function": {
    "name": "http_get",
    "description": "Fetch a URL and return the raw body.",
    "parameters": {
      "type": "object",
      "properties": {
        "url": {"type": "string"},
        "headers": {"type": "object"}
      },
      "required": ["url"]
    }
  }
}
```

**Returns:** raw body, capped. gzip handled transparently.

**Errors:** `http_error: <code>`, `timeout`, `bad_url`.

**Note:** this is the substrate the HTTP-based browser tools build on (cookie jar,
redirect following, form POST).

---

## 20. `vision_analyze`

```json
{
  "type": "function",
  "function": {
    "name": "vision_analyze",
    "description": "Describe or answer questions about an image.",
    "parameters": {
      "type": "object",
      "properties": {
        "image": {"type": "string", "description": "Path or URL."},
        "question": {"type": "string"}
      },
      "required": ["image", "question"]
    }
  }
}
```

**Returns:** the model's text answer.

**Requires:** a vision-capable provider. If none is configured, returns
`error: no_vision_provider` rather than silently failing.

---

## 21. `image_generate`

```json
{
  "type": "function",
  "function": {
    "name": "image_generate",
    "description": "Generate an image from a prompt.",
    "parameters": {
      "type": "object",
      "properties": {
        "prompt": {"type": "string"},
        "size": {"type": "string"}
      },
      "required": ["prompt"]
    }
  }
}
```

**Returns:** the saved file path.

**Requires:** an image provider. Returns `error: no_image_provider` otherwise.

---

## 22. `text_to_speech`

```json
{
  "type": "function",
  "function": {
    "name": "text_to_speech",
    "description": "Speak text aloud through the phone's TTS engine.",
    "parameters": {
      "type": "object",
      "properties": {
        "text": {"type": "string"},
        "rate": {"type": "number"}
      },
      "required": ["text"]
    }
  }
}
```

**Returns:** confirmation, or the audio file path when saving instead of speaking.

**Implementation:** `termux-tts-speak`, which calls Android's `TextToSpeech` API —
i.e. the phone's own engine, whatever it is.

**Errors:** `termux_api_missing` — with the exact instruction to install the
Termux:API app. This is the degraded-mode path, and it must be explicit rather
than silent.

---

## 23. `clarify`

```json
{
  "type": "function",
  "function": {
    "name": "clarify",
    "description": "Ask the user a question when a decision is genuinely theirs.",
    "parameters": {
      "type": "object",
      "properties": {
        "question": {"type": "string"},
        "choices": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["question"]
    }
  }
}
```

**Returns:** the user's answer.

**Per surface:** terminal prompt in the CLI; an inline keyboard on Telegram; a
form in the web portal. On a non-interactive run, returns
`error: no_interactive_channel` rather than hanging.

---

## 24. Registry summary

| Group | Tools | Count |
|---|---|---|
| Filesystem | read_file, write_file, patch, search_files, list_dir | 5 |
| Execution | terminal, process_manage, execute_code | 3 |
| Memory | memory, session_search, todo_list | 3 |
| Skills | skills_list, skill_view, skill_manage | 3 |
| Delegation | delegate_task | 1 |
| Scheduling | cronjob_manage | 1 |
| Network | web_search, web_extract, http_get | 3 |
| Multimodal | vision_analyze, image_generate, text_to_speech | 3 |
| Control | clarify | 1 |
| **Total** | | **23** |

23 schemas are sent on every request. That is the permanent per-request cost of
the tool surface, and it is why the list is audited rather than grown.

**Excluded:** `computer_use` (needs an accessibility driver), `kanban_*` (14,
deferred), `manage_connections` (Hermes-hosted, not portable), and the four
browser tools that require a real engine (`browser_vision`, `browser_cdp`,
`browser_vault`, `browser_exec`).

---

## 25. Test matrix

Each row is one test file. All run offline; none require an API key.

| Test | Asserts |
|---|---|
| `test_tools_fs.py` | read/write/patch/search/list happy paths + every documented error |
| `test_tools_patch_safety.py` | patch refuses a missing `old` and leaves the file byte-identical |
| `test_tools_exec.py` | exit codes, timeouts kill the child, approval denial does not execute |
| `test_tools_sandbox.py` | `../`, absolute escapes and symlink escapes are all refused |
| `test_tools_cap.py` | a 10 MB return is truncated and RSS does not spike |
| `test_tools_memory.py` | FTS5 add/search/forget; punctuation queries degrade to LIKE |
| `test_tools_skills.py` | index is body-free; `skill_view` injects as a user message |
| `test_tools_delegate.py` | a fourth concurrent delegate is refused by the supervisor |
| `test_tools_registry.py` | every registered tool has a schema and a callable; no orphans |
| `test_provider_rotation.py` | 429 → next key; 401 → key quarantined; exhaustion → fallback |
| `test_provider_usage.py` | exactly one usage row per request |
| `test_stream_contract.py` | tool-call arguments split across deltas reassemble exactly |
| `test_restart_recovery.py` | kill mid-turn; on restart the turn replays with no loss |
| `test_auth_gate.py` | a non-loopback bind without a token refuses to start |
| `test_redaction.py` | a key never appears in a log line or an error string |

---

*End of tool contracts.*
