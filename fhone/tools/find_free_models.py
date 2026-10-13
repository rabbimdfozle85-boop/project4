"""Find a real, free, tool-capable model on OpenRouter.

Run: python3 tools/find_free_models.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

KEYS = Path("/home/codespace/.hermes/cache/scratch/mh_keys.json")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def main() -> int:
    keys = json.loads(KEYS.read_text())
    key = keys["openrouter"]["asad"]

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": "Bearer " + key, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read().decode())

    rows = []
    for model in data.get("data", []):
        mid = model.get("id", "")
        params = model.get("supported_parameters") or []
        if not mid.endswith(":free"):
            continue
        if "tools" not in params:
            continue
        ctx = model.get("context_length") or 0
        rows.append((ctx, mid, (model.get("name") or "")[:44]))

    rows.sort(reverse=True)
    print("free models on OpenRouter that support tool calling: %d\n" % len(rows))
    for ctx, mid, name in rows[:25]:
        print("  %8d  %-52s %s" % (ctx, mid, name))
    if not rows:
        print("  (none — falling back to any free model)")
        for model in data.get("data", []):
            mid = model.get("id", "")
            if mid.endswith(":free"):
                print("    %-52s params=%s" % (mid, model.get("supported_parameters")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
