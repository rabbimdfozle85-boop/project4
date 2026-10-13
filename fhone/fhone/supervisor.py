"""The resident supervisor.

Restart-first, not immortal. Android WILL terminate this process — the low-memory
killer, the OEM background-app killer, swiping the app from Recents, Doze, or a
reboot. `termux-wake-lock` prevents none of those: it stops the CPU sleeping, it
does not make the process immortal.

So the design assumes death:
  * all durable state lives in SQLite, so a restart is lossless
  * the pidfile records (pid, process start time) so a recycled PID is never
    mistaken for a live supervisor
  * `fhone start` is idempotent and is the single command that brings it back
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config as config_mod
from . import scheduler
from .store import Store

WAKE_LOCK = "termux-wake-lock"
WAKE_UNLOCK = "termux-wake-unlock"


# ---------------------------------------------------------------------------
# pidfile: (pid, start_time) — never bare PID existence
# ---------------------------------------------------------------------------

def _proc_start_time(pid: int):
    """Field 22 of /proc/<pid>/stat, the process start time in clock ticks."""
    try:
        with open("/proc/%d/stat" % pid, "r") as fh:
            data = fh.read()
    except OSError:
        return None
    try:
        rest = data.rsplit(")", 1)[1].split()
        return int(rest[19])
    except (IndexError, ValueError):
        return None


def pidfile(cfg: dict) -> Path:
    return Path(cfg["home"]) / "supervisor.pid"


def read_pidfile(cfg: dict):
    p = pidfile(cfg)
    if not p.exists():
        return None
    try:
        pid_s, start_s = p.read_text(encoding="utf-8").strip().split(":", 1)
        return int(pid_s), int(start_s)
    except (OSError, ValueError):
        return None


def write_pidfile(cfg: dict) -> None:
    pid = os.getpid()
    start = _proc_start_time(pid) or 0
    pidfile(cfg).write_text("%d:%d" % (pid, start), encoding="utf-8")


def clear_pidfile(cfg: dict) -> None:
    try:
        pidfile(cfg).unlink()
    except OSError:
        pass


def live_pid(cfg: dict):
    """The supervisor's PID if it is genuinely still the same process, else None.

    The start time is checked even for our own PID: a stale pidfile naming a live
    process with a different start time is a recycled PID, not our supervisor.
    """
    entry = read_pidfile(cfg)
    if not entry:
        return None
    pid, recorded_start = entry
    actual = _proc_start_time(pid)
    if actual is None:
        return None                       # gone
    if recorded_start and actual != recorded_start:
        return None                       # PID recycled: not our process
    return pid


def is_running(cfg: dict) -> bool:
    return live_pid(cfg) is not None


# ---------------------------------------------------------------------------
# wake lock
# ---------------------------------------------------------------------------

def _have(binary: str) -> bool:
    from shutil import which
    return which(binary) is not None


def acquire_wake_lock(log=None) -> bool:
    if not _have(WAKE_LOCK):
        if log:
            log("wake-lock: termux-api not available (install the Termux:API app)")
        return False
    try:
        subprocess.run([WAKE_LOCK], timeout=10, capture_output=True)
        if log:
            log("wake-lock: acquired")
        return True
    except Exception as e:
        if log:
            log("wake-lock: failed (%s)" % e)
        return False


def release_wake_lock(log=None) -> None:
    if not _have(WAKE_UNLOCK):
        return
    try:
        subprocess.run([WAKE_UNLOCK], timeout=10, capture_output=True)
        if log:
            log("wake-lock: released")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# the resident process
# ---------------------------------------------------------------------------

class Supervisor:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.store = None
        self.pool = None
        self.portal = None
        self.telegram = None
        self.threads: list = []
        self.log_path = Path(cfg["home"]) / "fhone.log"

    # -- logging -----------------------------------------------------------
    def log(self, message: str) -> None:
        line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), message)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        if sys.stdout.isatty():
            print(line, flush=True)

    # -- lifecycle ---------------------------------------------------------
    def run(self) -> int:
        from . import __main__ as main_mod
        from . import tools as tools_mod
        from . import ui as ui_mod
        from .surfaces import portal as portal_mod
        from .surfaces import telegram as tg_mod

        Path(self.cfg["home"]).mkdir(parents=True, exist_ok=True)
        write_pidfile(self.cfg)
        self._install_signal_handlers()
        self.log("supervisor starting (pid %d, version %s)"
                 % (os.getpid(), main_mod._version()))

        acquire_wake_lock(self.log)

        self.store = Store(self.cfg["db"])
        self.pool = main_mod._pool_for(self.cfg, self.store)
        self.pool.seed_defaults()

        # --- portal -------------------------------------------------------
        if self.cfg.get("portal_enabled", True):
            try:
                ui = ui_mod.QuietUI()
                tools = tools_mod.Tools(self.cfg, self.store, ui, self.pool)
                from . import agent as agent_mod
                agent = agent_mod.Agent(self.cfg, self.store, self.pool, tools, ui)
                ctx = portal_mod.Context(self.cfg, self.store, self.pool, tools,
                                         agent, self.log)
                self.portal = portal_mod.Portal(self.cfg, ctx)
                url = self.portal.start()
                self.log("portal listening on %s" % url)
            except portal_mod.PortalError as e:
                self.log("portal: %s" % e)

        # --- telegram -----------------------------------------------------
        if self.cfg.get("telegram_enabled") and self.cfg.get("telegram_token"):
            self.telegram = tg_mod.TelegramSurface(
                self.cfg, lambda: main_mod.build(self.cfg, quiet=True), self.log)
            th = threading.Thread(target=self.telegram.run, name="telegram",
                                  daemon=True)
            th.start()
            self.threads.append(th)
            self.log("telegram: poller started")
        elif self.cfg.get("telegram_enabled"):
            self.log("telegram: enabled but no token configured")

        # --- scheduler ----------------------------------------------------
        th = threading.Thread(target=self._scheduler_loop, name="scheduler",
                              daemon=True)
        th.start()
        self.threads.append(th)

        self.log("supervisor ready: %d background thread(s)" % len(self.threads))

        # --- main loop ----------------------------------------------------
        while not self.stop_event.is_set():
            self.stop_event.wait(5.0)

        self.log("supervisor shutting down")
        self._shutdown()
        return 0

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            self.log("signal %d received" % signum)
            self.stop_event.set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    def _scheduler_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                ran = scheduler.tick(self.store, self.log)
                if ran:
                    self.log("scheduler: ran %d job(s)" % ran)
            except Exception as e:
                self.log("scheduler error: %s: %s" % (type(e).__name__, e))
            self.stop_event.wait(30.0)

    def _shutdown(self) -> None:
        if self.telegram is not None:
            self.telegram.stop()
        if self.portal is not None:
            self.portal.stop()
        if self.pool is not None:
            self.pool.close()
        if self.store is not None:
            self.store.close()
        release_wake_lock(self.log)
        clear_pidfile(self.cfg)


# ---------------------------------------------------------------------------
# control commands
# ---------------------------------------------------------------------------

def start(cfg: dict, *, foreground: bool = False) -> int:
    if is_running(cfg):
        pid = live_pid(cfg)
        print("already running (pid %s)" % pid)
        return 0
    if foreground:
        return Supervisor(cfg).run()

    env = dict(os.environ)
    env["FHONE_HOME"] = cfg["home"]
    log = Path(cfg["home"]) / "supervisor.out"
    Path(cfg["home"]).mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "fhone", "_supervise"],
            stdout=out, stderr=out, stdin=subprocess.DEVNULL,
            env=env, start_new_session=True, cwd=str(Path(cfg["home"])))
    # Wait briefly for the child to write its pidfile.
    for _ in range(40):
        time.sleep(0.1)
        if is_running(cfg):
            break
    if is_running(cfg):
        print("started (pid %s)" % live_pid(cfg))
        return 0
    print("failed to start — see %s" % log, file=sys.stderr)
    return 1


def stop(cfg: dict, timeout: float = 15.0) -> int:
    pid = live_pid(cfg)
    if pid is None:
        clear_pidfile(cfg)
        print("not running")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print("could not signal pid %s (%s)" % (pid, e), file=sys.stderr)
        clear_pidfile(cfg)
        return 1
    deadline = time.time() + timeout
    while time.time() < deadline:
        if live_pid(cfg) is None:
            print("stopped")
            return 0
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    clear_pidfile(cfg)
    print("stopped (forced)")
    return 0


def restart(cfg: dict) -> int:
    stop(cfg)
    return start(cfg)


def status(cfg: dict) -> int:
    pid = live_pid(cfg)
    print("FHONE supervisor")
    if pid is None:
        print("  state     stopped")
        entry = read_pidfile(cfg)
        if entry:
            print("  note      a stale pidfile was present (pid %d)" % entry[0])
    else:
        start_time = _proc_start_time(pid)
        uptime = ""
        if start_time:
            try:
                ticks = os.sysconf("SC_CLK_TCK")
                boot = float(Path("/proc/uptime").read_text().split()[0])
                started = boot - (start_time / ticks)
                uptime = " (up %s)" % _human(int(started))
            except Exception:
                uptime = ""
        print("  state     running (pid %d)%s" % (pid, uptime))
    print("  home      %s" % cfg["home"])
    print("  portal    %s:%s%s" % (cfg.get("portal_host"), cfg.get("portal_port"),
                                   "" if cfg.get("portal_enabled", True) else " (disabled)"))
    print("  telegram  %s" % ("enabled" if cfg.get("telegram_enabled") else "disabled"))
    print("  wake-lock %s" % ("available" if _have(WAKE_LOCK) else "unavailable"))
    log = Path(cfg["home"]) / "fhone.log"
    if log.exists():
        print("  log       %s (%.1f KB)" % (log, log.stat().st_size / 1024))
    return 0 if pid else 1


def _human(seconds: int) -> str:
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    return "%dh%dm" % (seconds // 3600, (seconds % 3600) // 60)


def boot_hook(cfg: dict) -> int:
    """Write a Termux:Boot script so the supervisor returns after a reboot."""
    boot_dir = Path.home() / ".termux" / "boot"
    if not boot_dir.parent.exists():
        print("no ~/.termux directory — install the Termux:Boot app first")
        return 1
    boot_dir.mkdir(parents=True, exist_ok=True)
    script = boot_dir / "start-fhone"
    script.write_text(
        "#!%s\n"
        "termux-wake-lock\n"
        "sleep 20\n"
        "cd %s && exec python3 -m fhone start --foreground\n"
        % (_shell(), cfg["home"]), encoding="utf-8")
    script.chmod(0o755)
    print("boot hook written: %s" % script)
    return 0


def _shell() -> str:
    prefix = os.environ.get("PREFIX")
    if prefix and (Path(prefix) / "bin" / "sh").exists():
        return str(Path(prefix) / "bin" / "sh")
    return "/bin/sh"


def free_port(host: str = "127.0.0.1") -> int:
    s = socket.socket()
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port
