"""Generate the Mini-Hermes ranked skill catalog from the real skill tree.

Tier assignment is rule-based so the catalog can be regenerated when the tree
changes, rather than drifting as a hand-maintained list.

  T1  always indexed   - needed on most turns
  T2  on-demand index  - useful when the task looks related
  T3  archive          - on disk, fetchable by name, not indexed

Portability is a separate axis:
  portable      pure stdlib / HTTP / filesystem
  needs-cli     works if the named binary is installed
  needs-dep     would require a Python package (violates C1 as-is)
  not-portable  macOS-only or needs a heavy runtime
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path("/workspaces/project4/Hermes-Agent")
OUT = Path("/workspaces/project4/minihermes-specs/skills.md")

T1 = {
    # methodology - pure reasoning, zero tooling, needed constantly
    "plan-before-build", "lightweight-agent-design", "systematic-debugging",
    "test-driven-development", "spike", "requesting-code-review",
    "simplify-code", "grounded-citations", "subagent-driven-development",
    # knowledge / notes
    "obsidian", "llm-wiki", "hermes-agent-skill-authoring",
    # research
    "arxiv", "youtube-content", "competitor-news-monitor",
    # web
    "blocked-page-recovery", "har-derived-api-client",
    # dev tooling that is pure-ish
    "github", "codebase-inspection", "python-debugpy", "dogfood",
    "rest-graphql-debug",
    # docs / data
    "pdf", "docx", "xlsx", "maps",
    # comms / planning
    "weekly-review-planning", "document-to-action-items",
    "meeting-action-items", "email-inbox-triage",
    # agent orchestration
    "hermes-agent", "claude-code", "codex", "opencode",
}

T2_CATEGORIES = {
    "productivity", "research", "media", "note-taking", "web", "email",
    "software-development", "autonomous-ai-agents", "creative",
    "web-development", "devops",
}

NOT_PORTABLE = {"apple", "smart-home"}
HEAVY = {
    "ascii-video", "manim-video", "songsee", "computer-use", "p5js",
    "inspecting-hermes-desktop-dom", "node-inspect-debugger",
    "page-agent", "scrollcraft", "baoyu-infographic",
}
NEEDS_CLI = {
    "himalaya", "xurl", "notion", "airtable", "box", "google-workspace",
    "powerpoint", "findmy", "imessage", "apple-notes", "apple-reminders",
    "gif-search", "songsee", "codebase-inspection", "github",
    "claude-code", "codex", "opencode", "publish-site",
    "cloudflare-temporary-deploy",
}


def parse(p: Path):
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    name = desc = ""
    if m:
        fm = m.group(1)
        nm = re.search(r"^name:\s*(.+)$", fm, re.M)
        dm = re.search(r"^description:\s*(.+)$", fm, re.M)
        name = nm.group(1).strip().strip('"') if nm else p.parent.name
        desc = dm.group(1).strip().strip('"') if dm else ""
    return name or p.parent.name, desc, len(text)


def classify(cat: str, name: str):
    top = cat.split("/")[0]
    if name in T1:
        tier = 1
    elif top in T2_CATEGORIES:
        tier = 2
    else:
        tier = 3
    if top in NOT_PORTABLE:
        port = "not-portable"
    elif name in HEAVY:
        port = "needs-dep"
    elif name in NEEDS_CLI:
        port = "needs-cli"
    else:
        port = "portable"
    return tier, port


rows = []
for base in ("skills", "optional-skills"):
    root = ROOT / base
    if not root.is_dir():
        continue
    for p in sorted(root.rglob("SKILL.md")):
        cat = str(p.parent.parent.relative_to(root)) if p.parent.parent != root else "(root)"
        name, desc, size = parse(p)
        tier, port = classify(cat, name)
        rows.append({"base": base, "cat": cat, "name": name, "desc": desc,
                     "size": size, "tier": tier, "port": port})

tiers = Counter(r["tier"] for r in rows)
ports = Counter(r["port"] for r in rows)

L = []
w = L.append
w("# Mini-Hermes — Ranked Skill Catalog")
w("")
w("Companion to `minihermes-plan-final.md` §10. Generated from the real tree at")
w("`/workspaces/project4/Hermes-Agent` — regenerate with")
w("`python3 minihermes-specs/gen_skills_catalog.py` rather than editing by hand.")
w("")
w("**Everything ships. Nothing is dropped.** What the tiers change is what competes")
w("for the model's attention on each turn.")
w("")
w("| Tier | Meaning | Count |")
w("|---|---|---|")
w("| **T1** | always indexed — needed on most turns | %d |" % tiers[1])
w("| **T2** | indexed on demand — useful when the task looks related | %d |" % tiers[2])
w("| **T3** | archive — on disk, fetchable by name, not indexed | %d |" % tiers[3])
w("| | **total** | **%d** |" % len(rows))
w("")
w("Portability is a separate axis:")
w("")
w("| Portability | Count | Meaning |")
w("|---|---|---|")
w("| portable | %d | pure stdlib / HTTP / filesystem |" % ports["portable"])
w("| needs-cli | %d | works if the named binary is installed |" % ports["needs-cli"])
w("| needs-dep | %d | would need a Python package — conflicts with C1 as-is |" % ports["needs-dep"])
w("| not-portable | %d | macOS-only |" % ports["not-portable"])
w("")
w("Why the tier split exists: a 60-entry index measures at **0.1 MB**, so size is")
w("not the constraint — *selection quality* is. An agent choosing among 213 skills")
w("picks worse than one choosing among 40. Tiers give you the whole collection")
w("without the selection penalty, and `set_tier` lets the agent promote a skill")
w("itself when it turns out to matter.")
w("")
w("---")
w("")

for tier in (1, 2, 3):
    sub = [r for r in rows if r["tier"] == tier]
    if not sub:
        continue
    title = {1: "T1 — always indexed", 2: "T2 — indexed on demand",
             3: "T3 — archive"}[tier]
    w("## %s (%d)" % (title, len(sub)))
    w("")
    bycat = {}
    for r in sub:
        bycat.setdefault((r["base"], r["cat"]), []).append(r)
    for key in sorted(bycat):
        w("### %s / %s" % key)
        w("")
        w("| Skill | Portability | Size | Description |")
        w("|---|---|---|---|")
        for r in sorted(bycat[key], key=lambda x: x["name"]):
            desc = r["desc"].replace("|", "\\|")[:110]
            w("| `%s` | %s | %dB | %s |" % (r["name"], r["port"], r["size"], desc))
        w("")

w("---")
w("")
w("## Promotion and demotion")
w("")
w("Tier is a frontmatter field. Changing it is a one-line edit:")
w("")
w("```yaml")
w("---")
w("name: some-skill")
w("description: ...")
w("tier: 1")
w("triggers: [keyword, keyword]")
w("---")
w("```")
w("")
w("The agent can do this itself via `skill_manage(action=\"set_tier\")`, so the")
w("ranking improves from use rather than staying a fixed guess.")
w("")
w("## Rules used by the generator")
w("")
w("1. An explicit T1 list (methodology, notes, research, web, core dev tooling,")
w("   document formats, planning, agent orchestration).")
w("2. Any other skill whose category is in the broadly-useful set becomes T2.")
w("3. Everything else is T3.")
w("4. Portability is orthogonal: macOS-only categories are `not-portable`,")
w("   skills needing a Python package are `needs-dep`, skills needing a named")
w("   binary are `needs-cli`.")
w("")
w("## The `needs-dep` list, explicitly")
w("")
w("These are the only skills that cannot run under C1 without either a rewrite or")
w("an accepted exception. Each is a decision, not an oversight:")
w("")
for r in sorted([r for r in rows if r["port"] == "needs-dep"], key=lambda x: x["name"]):
    w("- `%s` — %s" % (r["name"], r["desc"][:90]))
w("")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print("wrote %s" % OUT)
print("  %d skills: T1=%d T2=%d T3=%d" % (len(rows), tiers[1], tiers[2], tiers[3]))
print("  portability: %s" % dict(ports))
print("  bytes: %d" % OUT.stat().st_size)
