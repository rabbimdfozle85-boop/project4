"""Bundle the curated skill set into fhone/skills/.

Picks skills that are genuinely portable to a phone (pure stdlib / HTTP /
filesystem / methodology) and injects a `tier` field so the index ranks them.

Run: python3 tools/bundle_skills.py
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

HERMES = Path("/workspaces/project4/Hermes-Agent")
PROFILE_SKILLS = Path("/home/codespace/.hermes/skills")
DEST = Path(__file__).resolve().parent.parent / "skills"

# (source_root, category, name, tier)
PICKS = [
    # ---- T1: methodology and core tooling --------------------------------
    ("profile", "software-development", "plan-before-build", 1),
    ("profile", "software-development", "lightweight-agent-design", 1),
    ("skills", "software-development", "systematic-debugging", 1),
    ("skills", "software-development", "test-driven-development", 1),
    ("skills", "software-development", "spike", 1),
    ("skills", "software-development", "requesting-code-review", 1),
    ("skills", "software-development", "simplify-code", 1),
    ("skills", "software-development", "hermes-agent-skill-authoring", 1),
    ("skills", "software-development", "dogfood", 1),
    ("skills", "software-development", "github", 1),
    ("skills", "web", "blocked-page-recovery", 1),
    ("skills", "note-taking", "obsidian", 1),
    ("skills", "research", "grounded-citations", 1),
    ("skills", "research", "arxiv", 1),
    ("skills", "research", "llm-wiki", 1),
    ("skills", "media", "youtube-content", 1),
    ("skills", "productivity", "weekly-review-planning", 1),
    ("skills", "productivity", "document-to-action-items", 1),
    ("skills", "productivity", "meeting-action-items", 1),
    ("skills", "productivity", "maps", 1),
    # ---- T2: useful when the task fits -----------------------------------
    ("skills", "research", "competitor-news-monitor", 2),
    ("skills", "email", "email-inbox-triage", 2),
    ("skills", "software-development", "codebase-inspection", 2),
    ("skills", "software-development", "python-debugpy", 2),
    ("optional-skills", "software-development", "rest-graphql-debug", 2),
    ("optional-skills", "web-development", "har-derived-api-client", 2),
    ("skills", "productivity", "product-price-monitor", 2),
    ("skills", "productivity", "pdf", 2),
    ("skills", "productivity", "xlsx", 2),
    ("skills", "productivity", "docx", 2),
    # ---- T3: archive ------------------------------------------------------
    ("skills", "social-media", "xurl", 3),
    ("skills", "email", "himalaya", 3),
    ("skills", "productivity", "notion", 3),
    ("skills", "productivity", "airtable", 3),
    ("skills", "productivity", "google-workspace", 3),
    ("skills", "creative", "humanizer", 3),
    ("skills", "creative", "architecture-diagram", 3),
]


def source_path(kind: str, category: str, name: str):
    if kind == "profile":
        return PROFILE_SKILLS / category / name / "SKILL.md"
    root = HERMES / ("skills" if kind == "skills" else "optional-skills")
    return root / category / name / "SKILL.md"


def add_tier(text: str, tier: int) -> str:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return "---\nname: unknown\ndescription: imported skill\ntier: %d\n---\n\n%s" % (tier, text)
    fm = m.group(1)
    body = text[m.end():]
    if re.search(r"^tier\s*:", fm, re.M):
        fm = re.sub(r"^tier\s*:.*$", "tier: %d" % tier, fm, count=1, flags=re.M)
    else:
        fm = fm.rstrip() + "\ntier: %d" % tier
    return "---\n%s\n---\n\n%s" % (fm, body)


def main() -> int:
    if DEST.exists():
        shutil.rmtree(DEST)
    copied = 0
    missing = []
    for kind, category, name, tier in PICKS:
        src = source_path(kind, category, name)
        if not src.exists():
            missing.append("%s/%s/%s" % (kind, category, name))
            continue
        out = DEST / category / name / "SKILL.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(add_tier(src.read_text(encoding="utf-8", errors="replace"), tier),
                       encoding="utf-8")
        copied += 1
    print("bundled %d skills into %s" % (copied, DEST))
    if missing:
        print("missing (%d):" % len(missing))
        for m in missing:
            print("  ", m)
    tiers = {}
    for p in DEST.rglob("SKILL.md"):
        m = re.search(r"^tier:\s*(\d)", p.read_text(encoding="utf-8"), re.M)
        t = m.group(1) if m else "?"
        tiers[t] = tiers.get(t, 0) + 1
    print("by tier:", dict(sorted(tiers.items())))
    total = sum(p.stat().st_size for p in DEST.rglob("SKILL.md"))
    print("total size: %.0f KB" % (total / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
