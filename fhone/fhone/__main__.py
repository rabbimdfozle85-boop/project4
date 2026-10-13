"""FHONE entry point: ``python3 -m fhone [command]``.

Commands: chat (default), install, start, stop, status, backup, restore,
provider, keys, skills, doctor, version.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import agent as agent_mod
from . import backup as backup_mod
from . import config as config_mod
from . import providers as providers_mod
from . import skills as skills_mod
from . import tools as tools_mod
from . import ui as ui_mod
from .store import Store


def new_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def _pool_for(cfg: dict, store):
    """Build the provider pool and seed the defaults. Shared by every surface."""
    pool = providers_mod.ProviderPool(store, cfg)
    pool.seed_defaults()
    return pool


def build(cfg: dict, *, yolo: bool = False, quiet: bool = False, ui=None):
    """Wire the object graph. Shared by the CLI, subagents and tests."""
    cfg = dict(cfg)
    if yolo:
        cfg["_yolo"] = True
    store = Store(cfg["db"])
    pool = _pool_for(cfg, store)
    if ui is None:
        ui = ui_mod.QuietUI() if quiet else ui_mod.UI()
    tools = tools_mod.Tools(cfg, store, ui, pool)
    agent = agent_mod.Agent(cfg, store, pool, tools, ui)
    return agent, store, pool, tools, ui


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_chat(cfg: dict, args) -> int:
    if not cfg.get("model"):
        print("no model configured. Run:  fhone provider add <id> <url> <model>",
              file=sys.stderr)
        print("or set FHONE_MODEL, or run: fhone install", file=sys.stderr)
        return 2

    agent, store, pool, tools, ui = build(cfg, yolo=args.yolo,
                                          quiet=bool(args.query))
    sid = args.session or new_session_id()
    store.ensure_session(sid, cfg.get("model", ""))

    state = {
        "cfg": cfg, "store": store, "pool": pool, "tools": tools, "ui": ui,
        "session": sid, "new_session_id": new_session_id,
        "model": cfg.get("model", ""), "provider": "",
        "status_bar": cfg.get("status_bar", True),
        "window": int(cfg.get("context_window", 12)),
    }

    try:
        if args.query:
            answer = agent.turn(sid, args.query)
            if answer:
                print(answer)
            return 0

        ui.note("FHONE %s · model %s · session %s" % (
            _version(), cfg.get("model"), sid))
        ui.note("type /help for commands, /exit to quit")
        while True:
            line = ui.prompt(state["session"]).strip()
            if not line:
                continue
            if line.startswith("!"):
                out = tools.terminal(line[1:])
                ui.note(out)
                continue
            try:
                if ui_mod.dispatch(line, state):
                    continue
            except SystemExit:
                break
            try:
                agent.turn(state["session"], line)
            except providers_mod.PoolError as e:
                ui.note("  ! %s" % e)
            except KeyboardInterrupt:
                ui.note("  (interrupted)")
            except Exception as e:
                ui.note("  ! %s: %s" % (type(e).__name__, e))
            _refresh_status(state, store, pool)
    finally:
        pool.close()
        store.close()
    return 0


def _refresh_status(state: dict, store, pool) -> None:
    try:
        state["health"] = pool.health()
        state["usage"] = dict(store.usage_total() or {})
        state["context"] = {"messages": store.count(state["session"]),
                            "window": state["window"]}
        state["ui"].status_bar(state)
    except Exception:
        pass


def cmd_install(cfg: dict, args) -> int:
    print("FHONE install")
    ok = True

    # 1. python + fts5
    import sqlite3
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        print("  ok   python %s, sqlite %s, FTS5 present"
              % (sys.version.split()[0], sqlite3.sqlite_version))
    except sqlite3.Error as e:
        print("  FAIL FTS5 unavailable: %s" % e)
        ok = False

    # 2. termux:api app
    from shutil import which
    if which("termux-wake-lock"):
        probe = subprocess.run(["termux-battery-status"], capture_output=True,
                               text=True, timeout=10)
        if probe.stdout.strip():
            print("  ok   Termux:API app reachable (wake-lock, TTS available)")
        else:
            print("  warn Termux:API CLI present but the APP is not installed.")
            print("       Install 'Termux:API' from F-Droid for wake-lock and TTS.")
    else:
        print("  warn termux-api not found (not running under Termux?)")

    # 3. config + db
    cfg["_version"] = _version()
    config_mod.save(cfg)
    store = Store(cfg["db"])
    store.set_meta("schema_version", store.get_meta("schema_version") or "1")
    print("  ok   config %s" % config_mod.config_path())
    print("  ok   database %s" % config_mod.db_path())

    # 4. providers
    pool = providers_mod.ProviderPool(store, cfg)
    pool.seed_defaults()
    print("  ok   providers seeded (%s)" % ", ".join(
        p["id"] for p in store.providers()))

    # 5. skills
    bundled = Path(__file__).resolve().parent.parent / "skills"
    count = skills_mod.install_tree(cfg, bundled)
    print("  ok   skills: %d installed" % count)

    # 6. mh-style command binding
    if args.no_path is False:
        _install_command()
    store.close()

    # 7. nous sign-in
    if not args.skip_nous:
        from . import auth_nous
        print("\n  Nous sign-in")
        try:
            auth_nous.sign_in(cfg, ui_mod.UI(),
                              poll_seconds=10 if args.quick_nous else 300)
        except Exception as e:
            print("  warn Nous sign-in failed: %s" % e)
            print("       Add a provider by hand instead: fhone provider add ...")

    print("\n%s" % ("install complete." if ok else "install finished WITH ERRORS."))
    print("run:  fhone")
    return 0 if ok else 1


def _launcher_shebang() -> str:
    """Interpreter line for the `fhone` launcher.

    Termux keeps sh under $PREFIX/bin; anywhere else /bin/sh is correct.
    Hardcoding the Termux path broke every other host.
    """
    prefix = os.environ.get("PREFIX")
    if prefix:
        candidate = Path(prefix) / "bin" / "sh"
        if candidate.exists():
            return "#!" + str(candidate)
    return "#!/bin/sh"


def _install_command() -> None:
    """Bind a `fhone` launcher into the Termux bin dir (or ~/.local/bin)."""
    target_dir = None
    prefix = os.environ.get("PREFIX")
    if prefix and Path(prefix).is_dir():
        target_dir = Path(prefix) / "bin"
    else:
        target_dir = Path.home() / ".local" / "bin"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        launcher = target_dir / "fhone"
        launcher.write_text(
            _launcher_shebang() + "\n"
            "exec python3 -m fhone \"$@\"\n", encoding="utf-8")
        launcher.chmod(0o755)
        print("  ok   command bound: %s" % launcher)
    except OSError as e:
        print("  warn could not bind the fhone command: %s" % e)


def cmd_backup(cfg: dict, args) -> int:
    store = Store(cfg["db"])
    try:
        path = backup_mod.create(cfg, store, quick=args.quick, label=args.label or "",
                                 out=args.output or "")
    finally:
        store.close()
    print("backup written: %s (%.1f KB)" % (path, Path(path).stat().st_size / 1024))
    if args.send:
        _send(path, cfg, args.send)
    return 0


def cmd_restore(cfg: dict, args) -> int:
    store = Store(cfg["db"])
    try:
        result = backup_mod.restore(cfg, store, args.path)
    finally:
        store.close()
    print(result)
    return 0 if not result.startswith("error:") else 1


def cmd_provider(cfg: dict, args) -> int:
    store = Store(cfg["db"])
    pool = providers_mod.ProviderPool(store, cfg)
    pool.seed_defaults()
    try:
        if args.action == "list":
            for p in store.providers(enabled_only=False):
                keys = store.keys_for(p["id"])
                print("  %-14s %-40s %-10s keys=%d %s" % (
                    p["id"], p["base_url"][:40], p["api_style"], len(keys),
                    "" if p["enabled"] else "(disabled)"))
        elif args.action == "add":
            if not args.name or not args.url:
                print("usage: fhone provider add <id> <base_url> [--style S] "
                      "[--model M] [--models a,b,c]")
                return 2
            models = [m.strip() for m in (args.models or "").split(",") if m.strip()]
            pool.add_provider(args.name, args.url, api_style=args.style or "openai",
                              model=args.model or "", priority=args.priority or 100,
                              models=models or None)
            print("added provider %s" % args.name)
        elif args.action == "models":
            if not args.name or args.models is None:
                print("usage: fhone provider models <id> <m1,m2,...|'*'   "
                      "(* = any model)")
                return 2
            models = [] if args.models == "*" else [
                m.strip() for m in args.models.split(",") if m.strip()]
            pool.set_provider_models(args.name, models)
            if models:
                print("%s serves %s" % (args.name, ", ".join(models)))
            else:
                print("%s serves any model" % args.name)
        elif args.action == "remove":
            store.provider_remove(args.name)
            print("removed %s" % args.name)
        elif args.action == "key":
            if not args.name or not args.key:
                print("usage: fhone provider key <id> <api-key> [--label L]")
                return 2
            kid = pool.add_key(args.name, args.key, args.label or "")
            print("added key #%d to %s" % (kid, args.name))
        elif args.action == "probe":
            for provider in ([args.name] if args.name else
                             [p["id"] for p in store.providers()]):
                for kid, label, ok, detail in pool.probe(provider):
                    print("  %-14s #%-4d %-10s %s  %s" % (
                        provider, kid, label or "-", "OK  " if ok else "FAIL", detail))
        else:
            print("usage: fhone provider {list|add|remove|key|probe}")
    finally:
        pool.close()
        store.close()
    return 0


def cmd_status(cfg: dict, args) -> int:
    store = Store(cfg["db"])
    pool = providers_mod.ProviderPool(store, cfg)
    try:
        health = pool.health()
        print("FHONE %s" % _version())
        print("  home      %s" % cfg["home"])
        print("  database  %s (%.1f KB)" % (
            cfg["db"], Path(cfg["db"]).stat().st_size / 1024
            if Path(cfg["db"]).exists() else 0))
        print("  model     %s" % (cfg.get("model") or "(unset)"))
        print("  providers %d" % len(health["providers"]))
        for p in health["providers"]:
            print("    %-14s %-24s %s" % (p["id"], p["state"], p["label"]))
        print("  keys      %d total, %d ok, %d cooling, %d dead" % (
            health["total"], health["ok"], health["cooldown"], health["dead"]))
        total = store.usage_total()
        print("  usage     requests=%s tokens=%s" % (
            total["r"] or 0, (total["p"] or 0) + (total["c"] or 0)))
        print("  sessions  %d" % store.session_count())
        print("  memories  %d" % store.memory_count())
        print("  skills    %d" % len(skills_mod.index(cfg)))
        print("  jobs      %d" % len(store.jobs()))
        print("  backups   %d" % len(backup_mod.list_backups(cfg)))
        from . import auth_nous
        print("  nous      %s" % ("guest" if auth_nous.is_guest(cfg) else
                                  ("signed in" if config_mod.load_auth() else "not signed in")))
    finally:
        pool.close()
        store.close()
    return 0


def cmd_skills(cfg: dict, args) -> int:
    if args.action == "list":
        print(skills_mod.list_skills(cfg, tier=args.tier or 0, query=args.query or ""))
    elif args.action == "show":
        print(skills_mod.view_skill(cfg, args.name))
    elif args.action == "install":
        src = Path(args.name)
        n = skills_mod.install_tree(cfg, src)
        print("installed %d skills from %s" % (n, src))
    else:
        print("usage: fhone skills {list|show|install}")
    return 0


def cmd_doctor(cfg: dict, args) -> int:
    print("FHONE doctor")
    problems = 0

    import sqlite3
    store = Store(cfg["db"])
    if store.integrity_check():
        print("  ok   database integrity")
    else:
        print("  FAIL database integrity — restore from a backup")
        problems += 1

    pool = providers_mod.ProviderPool(store, cfg)
    health = pool.health()
    if health["ok"]:
        print("  ok   %d provider(s) usable" % health["ok"])
    else:
        print("  FAIL no usable provider — add one with `fhone provider add`")
        problems += 1
    if not cfg.get("model"):
        print("  FAIL no model configured")
        problems += 1

    from shutil import which
    print("  %s termux-api (%s)" % ("ok  " if which("termux-wake-lock") else "warn",
                                    "present" if which("termux-wake-lock") else "absent"))

    try:
        import ssl
        ctx = ssl.create_default_context()
        print("  ok   TLS: %s" % (ctx.cert_store_stats() and "context built"))
    except Exception as e:
        print("  FAIL TLS: %s" % e)
        problems += 1

    print("\n%s" % ("all checks passed" if not problems else
                    "%d problem(s) found" % problems))
    pool.close()
    store.close()
    return 1 if problems else 0


def _send(path: str, cfg: dict, target: str) -> None:
    """Deliver a file through Hermes' gateway tooling, if present."""
    from shutil import which
    if not which("hermes"):
        print("  (hermes not on PATH — cannot send %s)" % path)
        return
    try:
        subprocess.run(["hermes", "send", "--to", target,
                        "FHONE backup. MEDIA:%s" % path], timeout=120)
    except Exception as e:
        print("  (send failed: %s)" % e)


def cmd_telegram(cfg: dict, args) -> int:
    """Configure the Telegram surface."""
    from . import config as config_mod
    action = args.action or "status"
    if action == "set":
        if not args.key:
            print("usage: fhone telegram set <bot-token>", file=sys.stderr)
            return 2
        cfg["telegram_token"] = args.key
        cfg["telegram_enabled"] = True
        config_mod.save(cfg)
        print("telegram token saved; surface enabled")
        print("restart the supervisor to pick it up:  fhone restart")
        return 0
    if action == "allow":
        if not args.name:
            print("usage: fhone telegram allow <user-id> [more ids...]",
                  file=sys.stderr)
            return 2
        ids = [x for x in (args.name.split(",") if args.name else []) if x.strip()]
        cfg["telegram_allowlist"] = ids
        config_mod.save(cfg)
        print("allowlist set to: %s" % (", ".join(ids) or "(empty — bot is open)"))
        return 0
    if action == "off":
        cfg["telegram_enabled"] = False
        config_mod.save(cfg)
        print("telegram surface disabled")
        return 0

    print("telegram")
    print("  enabled   %s" % ("yes" if cfg.get("telegram_enabled") else "no"))
    print("  token     %s" % ("(set)" if cfg.get("telegram_token") else "(missing)"))
    allow = cfg.get("telegram_allowlist") or []
    print("  allowlist %s" % (", ".join(allow) if allow else "(empty — open to anyone)"))
    if not cfg.get("telegram_token"):
        print("\nset one with:  fhone telegram set <bot-token>")
    return 0


def cmd_portal(cfg: dict, args) -> int:
    """Portal configuration and token management."""
    from . import config as config_mod
    action = args.action or "status"
    if action == "token":
        import secrets
        token = secrets.token_urlsafe(24)
        cfg["portal_token"] = token
        config_mod.save(cfg)
        print("portal token generated and saved (0600 config).")
        print("  %s" % token)
        print("\nOpen the portal with:  %s/?token=%s" % (_portal_url(cfg), token))
        return 0
    if action == "open":
        print(_portal_url(cfg))
        return 0

    print("portal")
    print("  enabled  %s" % ("yes" if cfg.get("portal_enabled", True) else "no"))
    print("  bind     %s:%s" % (cfg.get("portal_host"), cfg.get("portal_port")))
    print("  token    %s" % ("(set)" if cfg.get("portal_token") else "(none)"))
    host = cfg.get("portal_host", "127.0.0.1")
    if host not in ("127.0.0.1", "::1", "localhost") and not cfg.get("portal_token"):
        print("\n  WARNING: a non-loopback bind requires a token; the supervisor")
        print("           will refuse to start the portal until one is set.")
        print("           Run:  fhone portal token")
    print("  url      %s" % _portal_url(cfg))
    return 0


def _portal_url(cfg: dict) -> str:
    from .surfaces.portal import _lan_ip
    host = cfg.get("portal_host", "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = _lan_ip()
    return "http://%s:%s" % (host, cfg.get("portal_port", 8787))


def cmd_service(cfg: dict, args) -> int:
    from . import supervisor
    action = args.action or "status"
    if action == "start":
        return supervisor.start(cfg, foreground=bool(args.foreground))
    if action == "stop":
        return supervisor.stop(cfg)
    if action == "restart":
        return supervisor.restart(cfg)
    if action == "boot":
        return supervisor.boot_hook(cfg)
    return supervisor.status(cfg)


def cmd_supervise(cfg: dict, args) -> int:
    """Internal: run the supervisor in the foreground. Used by `fhone start`."""
    from . import supervisor
    return supervisor.Supervisor(cfg).run()


def _version() -> str:
    from . import __version__
    return __version__


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="fhone", description="FHONE — a personal agent for a phone.")
    ap.add_argument("command", nargs="?", default="chat",
                    choices=["chat", "install", "backup", "restore", "provider",
                             "status", "skills", "doctor", "version",
                             "start", "stop", "restart", "service", "boot",
                             "portal", "telegram", "_supervise"])
    ap.add_argument("-q", "--query", help="one-shot query, print the answer and exit")
    ap.add_argument("--session", help="session id to use or resume")
    ap.add_argument("--model", help="override the configured model")
    ap.add_argument("--yolo", action="store_true", help="skip shell approval")
    ap.add_argument("--path", help="backup file (restore)")
    ap.add_argument("--quick", action="store_true", help="quick backup")
    ap.add_argument("--label", help="backup label")
    ap.add_argument("-o", "--output", help="backup output path")
    ap.add_argument("--send", help="send the backup to a gateway target (e.g. telegram)")
    ap.add_argument("--name", help="provider/skill name")
    ap.add_argument("--url", help="provider base url")
    ap.add_argument("--key", help="api key")
    ap.add_argument("--style", help="provider wire style")
    ap.add_argument("--models",
                    help="comma-separated model allow-list (* or empty = any)")
    ap.add_argument("--priority", type=int, help="provider priority")
    ap.add_argument("--tier", type=int, help="skill tier filter")
    ap.add_argument("--skip-nous", action="store_true", help="install: skip Nous sign-in")
    ap.add_argument("--quick-nous", action="store_true",
                    help="install: short promotion poll")
    ap.add_argument("--no-path", action="store_true", help="install: skip the command binding")
    ap.add_argument("--foreground", action="store_true",
                    help="start: run in the foreground instead of detaching")
    ap.add_argument("action", nargs="?", help="sub-action (provider/skills/portal/telegram/service)")

    args = ap.parse_args(argv)
    cfg = config_mod.load()
    if args.model:
        cfg["model"] = args.model

    if args.command == "version":
        print("FHONE %s" % _version())
        return 0
    if args.command == "install":
        return cmd_install(cfg, args)
    if args.command == "backup":
        return cmd_backup(cfg, args)
    if args.command == "restore":
        if not args.path:
            print("usage: fhone restore --path <backup.zip>", file=sys.stderr)
            return 2
        return cmd_restore(cfg, args)
    if args.command == "provider":
        return cmd_provider(cfg, args)
    if args.command == "status":
        return cmd_status(cfg, args)
    if args.command == "skills":
        return cmd_skills(cfg, args)
    if args.command == "doctor":
        return cmd_doctor(cfg, args)
    if args.command == "_supervise":
        return cmd_supervise(cfg, args)
    if args.command in ("start", "stop", "restart", "service", "boot"):
        if args.command == "boot":
            args.action = "boot"
        elif args.command in ("start", "stop", "restart"):
            args.action = args.command
        return cmd_service(cfg, args)
    if args.command == "portal":
        return cmd_portal(cfg, args)
    if args.command == "telegram":
        return cmd_telegram(cfg, args)
    if args.command == "chat":
        if args.model:
            cfg["model"] = args.model
        cfg["_yolo"] = bool(getattr(args, "yolo", False))
        args.session = getattr(args, "session", "cli-default")
        return cmd_chat(cfg, args)
    return cmd_chat(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
