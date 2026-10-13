"""Backup and restore.

The database is snapshotted with ``VACUUM INTO``, never a file copy: copying a
live SQLite file can capture a torn WAL and produce a backup that looks fine and
restores corrupt. ``VACUUM INTO`` writes a consistent, compacted copy while the
agent keeps running.
"""
from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path

from . import config as config_mod

MANIFEST = "MANIFEST.json"


def backup_dir(cfg: dict) -> Path:
    d = Path(cfg.get("backup_dir") or (Path(cfg["home"]) / "backups"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def create(cfg: dict, store, *, quick: bool = False, label: str = "",
           out: str = "") -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = "fhone-backup-%s%s.zip" % (stamp, ("-" + label) if label else "")
    target = Path(out) if out else (backup_dir(cfg) / name)
    target.parent.mkdir(parents=True, exist_ok=True)

    staging = Path(cfg["home"]) / (".backup-staging-%d" % time.time())
    staging.mkdir(parents=True, exist_ok=True)
    files: dict = {}

    try:
        # Consistent DB snapshot — NOT a file copy.
        db_snapshot = staging / "fhone.db"
        store.vacuum_into(str(db_snapshot))
        files["fhone.db"] = db_snapshot.stat().st_size

        for src, rel in ((config_mod.config_path(), "config.json"),
                         (config_mod.auth_path(), "auth.json")):
            if src.exists():
                shutil.copy2(src, staging / rel)
                files[rel] = (staging / rel).stat().st_size

        if not quick:
            skills_src = Path(cfg["skills_dir"])
            if skills_src.is_dir():
                count = 0
                for skill in skills_src.rglob("SKILL.md"):
                    rel = Path("skills") / skill.parent.relative_to(skills_src) / "SKILL.md"
                    dest = staging / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(skill, dest)
                    files[rel.as_posix()] = dest.stat().st_size
                    count += 1
                files["_skills_count"] = count
            log = Path(cfg["home"]) / "fhone.log"
            if log.exists():
                shutil.copy2(log, staging / "fhone.log")
                files["fhone.log"] = (staging / "fhone.log").stat().st_size

        manifest = {
            "product": "fhone",
            "version": cfg.get("_version", "0.0099"),
            "schema_version": store.get_meta("schema_version"),
            "created_at": int(time.time()),
            "created_at_human": time.strftime("%Y-%m-%d %H:%M:%S UTC",
                                              time.gmtime()),
            "quick": bool(quick),
            "label": label,
            "files": {k: v for k, v in files.items() if not k.startswith("_")},
            "skills_count": files.get("_skills_count", 0),
        }
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2),
                                        encoding="utf-8")

        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    z.write(path, path.relative_to(staging).as_posix())
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    prune(cfg, int(cfg.get("backup_keep", 3)))
    return str(target)


def prune(cfg: dict, keep: int) -> int:
    """Keep the newest `keep` backups. keep <= 0 keeps everything."""
    if keep <= 0:
        return 0
    d = backup_dir(cfg)
    files = sorted(d.glob("fhone-backup-*.zip"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def list_backups(cfg: dict) -> list:
    d = backup_dir(cfg)
    out = []
    for p in sorted(d.glob("fhone-backup-*.zip"),
                    key=lambda q: q.stat().st_mtime, reverse=True):
        out.append({"path": str(p), "size": p.stat().st_size,
                    "mtime": int(p.stat().st_mtime)})
    return out


def inspect(path: str) -> dict:
    with zipfile.ZipFile(path) as z:
        if MANIFEST not in z.namelist():
            raise ValueError("not a fhone backup (no MANIFEST.json)")
        return json.loads(z.read(MANIFEST).decode("utf-8"))


def restore(cfg: dict, store, path: str) -> str:
    """Restore a backup. Nothing is replaced unless every check passes."""
    manifest = inspect(path)
    current = store.get_meta("schema_version") or "0"
    incoming = str(manifest.get("schema_version") or "0")
    if incoming > current:
        return ("error: schema_too_new: backup is schema %s, this build is %s — "
                "upgrade FHONE first" % (incoming, current))

    # Safety copy of the current state before touching anything.
    safety = create(cfg, store, quick=True, label="pre-restore")

    staging = Path(cfg["home"]) / (".restore-staging-%d" % time.time())
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as z:
            z.extractall(staging)

        db_src = staging / "fhone.db"
        if not db_src.exists():
            return "error: bad_backup: no fhone.db inside"
        check = _integrity(db_src)
        if not check:
            return "error: corrupt_backup: PRAGMA integrity_check failed; nothing replaced"

        store.close()
        shutil.copy2(db_src, config_mod.db_path())
        for rel, dest in (("config.json", config_mod.config_path()),
                          ("auth.json", config_mod.auth_path())):
            src = staging / rel
            if src.exists():
                shutil.copy2(src, dest)
                config_mod._chmod_600(dest)
        skills_src = staging / "skills"
        if skills_src.is_dir():
            dest = Path(cfg["skills_dir"])
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(skills_src, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return ("restored from %s\n  safety copy: %s\n  restart FHONE to pick it up"
            % (Path(path).name, Path(safety).name))


def _integrity(db_path: Path) -> bool:
    import sqlite3
    try:
        con = sqlite3.connect(str(db_path))
        row = con.execute("PRAGMA integrity_check").fetchone()
        con.close()
        return bool(row) and row[0] == "ok"
    except sqlite3.Error:
        return False
