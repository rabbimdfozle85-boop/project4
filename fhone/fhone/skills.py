"""Skill index, on-demand body loading, and self-editing.

The index holds name + description + tier + triggers, never bodies. A body is
loaded only when the agent asks for it, and it is injected as a USER message —
never into the system prompt, which would invalidate the provider's cached
prefix on every skill load.

Tier controls what competes for attention, not what exists:
  1  always indexed
  2  indexed when the task looks related
  3  archive — on disk, fetchable by name, not indexed
"""
from __future__ import annotations

import re
from pathlib import Path

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_frontmatter(text: str) -> tuple:
    """Returns (meta_dict, body). Tolerant: malformed frontmatter is skipped."""
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, text[m.end():]


def index(cfg: dict) -> list:
    """Scan the skills directory. Cheap: reads only the frontmatter block."""
    root = Path(cfg["skills_dir"])
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob("SKILL.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, _ = parse_frontmatter(text)
        name = meta.get("name") or path.parent.name
        out.append({
            "name": name,
            "description": meta.get("description", ""),
            "tier": int(meta.get("tier", 3) or 3),
            "triggers": [t.strip() for t in (meta.get("triggers") or "").split(",") if t.strip()],
            "path": path,
            "size": len(text),
        })
    return out


_index_cache: dict = {}
_index_cache_key: str = ""


def _find(cfg: dict, name: str):
    """Find a skill by name. Caches the index to avoid repeated dir scans."""
    global _index_cache, _index_cache_key
    key = cfg.get("skills_dir", "")
    if key != _index_cache_key:
        _index_cache = {e["name"]: e for e in index(cfg)}
        _index_cache_key = key
    return _index_cache.get(name)


def list_skills(cfg: dict, tier: int = 0, query: str = "") -> str:
    entries = index(cfg)
    if not entries:
        return "no skills installed"
    if tier:
        entries = [e for e in entries if e["tier"] == int(tier)]
    if query:
        q = query.lower()
        entries = [e for e in entries
                   if q in e["name"].lower() or q in e["description"].lower()]
    if not entries:
        return "no skills matched"
    lines = []
    for want in (1, 2, 3):
        group = [e for e in entries if e["tier"] == want]
        if not group:
            continue
        lines.append("T%d:" % want)
        for e in group:
            lines.append("  %-34s %s" % (e["name"], e["description"][:90]))
    return "\n".join(lines)


def view_skill(cfg: dict, name: str) -> str:
    entry = _find(cfg, name)
    if entry is None:
        near = [e["name"] for e in index(cfg)
                if name.lower()[:4] in e["name"].lower()][:3]
        hint = ("  did you mean: " + ", ".join(near)) if near else ""
        return "error: not_found: %s%s" % (name, hint)
    try:
        text = entry["path"].read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return "error: read_failed: %s" % e
    meta, body = parse_frontmatter(text)
    return "# %s\n%s\n\n%s" % (entry["name"], entry["description"], body.strip())


def manage_skill(cfg: dict, action: str, name: str, content: str = "",
                 description: str = "", tier: int = 0) -> str:
    global _index_cache, _index_cache_key
    root = Path(cfg["skills_dir"])
    root.mkdir(parents=True, exist_ok=True)
    target_dir = root / _slug(name)
    target = target_dir / "SKILL.md"

    if action == "create":
        if target.exists():
            return "error: already_exists: %s" % name
        if not content:
            return "error: bad_frontmatter: content required (include frontmatter)"
        meta, body = parse_frontmatter(content)
        if not meta.get("name"):
            return ("error: bad_frontmatter: missing 'name' in the YAML "
                    "frontmatter block")
        if not meta.get("description"):
            return "error: bad_frontmatter: missing 'description'"
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _index_cache.clear()
        _index_cache_key = ""
        return "created %s" % target

    entry = _find(cfg, name)
    if entry is None:
        return "error: not_found: %s" % name

    if action == "edit":
        if not content:
            return "error: bad_frontmatter: content required"
        meta, _ = parse_frontmatter(content)
        if not meta.get("name"):
            return "error: bad_frontmatter: missing 'name'"
        entry["path"].write_text(content, encoding="utf-8")
        _index_cache.clear()
        _index_cache_key = ""
        return "edited %s" % entry["path"]

    if action == "delete":
        try:
            entry["path"].unlink()
            parent = entry["path"].parent
            if parent != root and not any(parent.iterdir()):
                parent.rmdir()
        except OSError as e:
            return "error: delete_failed: %s" % e
        _index_cache.clear()
        _index_cache_key = ""
        return "deleted %s" % name

    if action == "set_tier":
        if int(tier) not in (1, 2, 3):
            return "error: bad_argument: tier must be 1, 2 or 3"
        text = entry["path"].read_text(encoding="utf-8", errors="replace")
        if re.search(r"^tier\s*:", text, re.M):
            text = re.sub(r"^tier\s*:.*$", "tier: %d" % int(tier), text,
                          count=1, flags=re.M)
        else:
            text = FRONTMATTER.sub(
                lambda m: "---\n%s\ntier: %d\n---\n" % (m.group(1).rstrip(), int(tier)),
                text, count=1)
        entry["path"].write_text(text, encoding="utf-8")
        _index_cache.clear()
        _index_cache_key = ""
        return "tier of %s set to %d" % (name, int(tier))

    return "error: bad_argument: unknown action %s" % action


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "skill"


def install_tree(cfg: dict, source: Path) -> int:
    """Copy a skill tree into the skills dir. Used by `fhone install`."""
    dest = Path(cfg["skills_dir"])
    count = 0
    if not source.is_dir():
        return 0
    for src in source.rglob("SKILL.md"):
        rel = src.parent.relative_to(source)
        out = dest / rel / "SKILL.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        count += 1
    return count
