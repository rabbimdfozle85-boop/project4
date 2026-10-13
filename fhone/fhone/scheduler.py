"""Cron-style scheduling backed by the jobs table.

Because the supervisor is resident this is a real scheduler rather than a shell
cron — but it is still subject to the process being killed, so a job that must
run should also be reachable by hand. Missed runs are recorded, never silently
skipped.
"""
from __future__ import annotations

import re
import subprocess
import time

INTERVAL = re.compile(r"^\s*(?:every\s+)?(\d+)\s*([smhd])\s*$", re.I)
MULT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_schedule(spec: str) -> dict:
    """Returns {"kind": "interval"|"cron"|None, "value": ..., "error": str}.

    A dict rather than a tuple so the two branches stay untyped-safe and the
    failure case carries its own message.
    """
    if not spec:
        return {"kind": None, "error": "schedule required"}
    m = INTERVAL.match(spec)
    if m:
        return {"kind": "interval",
                "value": int(m.group(1)) * MULT[m.group(2).lower()]}
    fields = spec.split()
    if len(fields) == 5:
        try:
            parsed = [_cron_field(f, lo, hi) for f, lo, hi in zip(
                fields, (0, 0, 1, 1, 0), (59, 23, 31, 12, 6))]
        except ValueError as e:
            return {"kind": None, "error": "bad_schedule: %s" % e}
        return {"kind": "cron", "value": parsed}
    return {"kind": None,
            "error": "bad_schedule: use '30m', 'every 2h', or a 5-field cron expression"}


def _cron_field(field: str, lo: int, hi: int):
    if field == "*":
        return set(range(lo, hi + 1))
    out = set()
    for part in field.split(","):
        if part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                raise ValueError("step must be positive")
            out |= set(range(lo, hi + 1, step))
        elif "-" in part:
            a, _, b = part.partition("-")
            out |= set(range(int(a), int(b) + 1))
        else:
            val = int(part)
            if not (lo <= val <= hi):
                raise ValueError("%d out of range %d-%d" % (val, lo, hi))
            out.add(val)
    return out


def next_run(spec: str, now: int | None = None) -> int:
    """Unix timestamp of the next fire time, or 0 if the spec is unusable."""
    now = int(now if now is not None else time.time())
    parsed = parse_schedule(spec)
    if parsed["kind"] == "interval":
        return now + int(parsed["value"])
    if parsed["kind"] != "cron":
        return 0
    minutes, hours, dom, month, dow = parsed["value"]
    # Pure epoch arithmetic in UTC. Mixing mktime (local) with gmtime (UTC)
    # computed wrong times outside UTC and broke across DST.
    probe = now + 60 - ((now + 60) % 60)
    for _ in range(60 * 24 * 366):
        tm = time.gmtime(probe)
        if (tm.tm_min in minutes and tm.tm_hour in hours
                and tm.tm_mday in dom and tm.tm_mon in month
                and ((tm.tm_wday + 1) % 7) in dow):
            return int(probe)
        probe += 60
    return 0


def due(store, now: int | None = None):
    return store.job_due(int(now if now is not None else time.time()))


def tick(store, log=None) -> int:
    """Run every due job once. Returns the number executed."""
    ran = 0
    for job in due(store):
        command = job["command"] or ""
        if log:
            log("job #%d: %s" % (job["id"], command[:80]))
        try:
            proc = subprocess.run(command, shell=True, capture_output=True,
                                  text=True, timeout=300)
            ok = proc.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        except Exception:
            ok = False
        nxt = next_run(job["schedule"] or "")
        store.job_set(job["id"], last_run=int(time.time()),
                      next_run=nxt, enabled=1 if nxt else 0)
        ran += 1
        if log:
            log("job #%d: %s" % (job["id"], "ok" if ok else "failed"))
    return ran


def manage(store, action: str, schedule: str = "", command: str = "",
           job_id: int = 0) -> str:
    if action == "create":
        if not schedule or not command:
            return "error: bad_argument: schedule and command are required"
        parsed = parse_schedule(schedule)
        if parsed["kind"] is None:
            return "error: %s" % parsed.get("error", "bad_schedule")
        nxt = next_run(schedule)
        if not nxt:
            return "error: bad_schedule: could not compute a next run"
        jid = store.job_add(schedule, command, next_run=nxt)
        return "created job #%d (next run %s)" % (
            jid, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(nxt)))

    if action == "list":
        rows = store.jobs()
        if not rows:
            return "(no jobs)"
        lines = []
        for r in rows:
            nxt = time.strftime("%H:%M", time.gmtime(r["next_run"])) if r["next_run"] else "-"
            last = time.strftime("%H:%M", time.gmtime(r["last_run"])) if r["last_run"] else "-"
            lines.append("#%d [%s] %-10s next=%s last=%s  %s" % (
                r["id"], "on" if r["enabled"] else "off", r["schedule"] or "",
                nxt, last, (r["command"] or "")[:50]))
        return "\n".join(lines)

    if action == "pause":
        store.job_set(job_id, enabled=0)
        return "paused #%d" % job_id
    if action == "resume":
        store.job_set(job_id, enabled=1, next_run=next_run(_job_schedule(store, job_id)))
        return "resumed #%d" % job_id
    if action == "remove":
        store.job_remove(job_id)
        return "removed #%d" % job_id
    if action == "run_now":
        for r in store.jobs():
            if r["id"] == job_id:
                try:
                    subprocess.run(r["command"], shell=True, capture_output=True,
                                   timeout=300)
                except Exception as e:
                    return "error: run_failed: %s" % e
                store.job_set(job_id, last_run=int(time.time()))
                return "ran #%d" % job_id
        return "error: not_found: #%d" % job_id
    return "error: bad_argument: unknown action %s" % action


def _job_schedule(store, job_id: int) -> str:
    for r in store.jobs():
        if r["id"] == job_id:
            return r["schedule"] or ""
    return ""
