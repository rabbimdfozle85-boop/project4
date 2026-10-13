"""Entry point: `python3 -m minihermes [options]`."""
from __future__ import annotations

import argparse
import sys
import time
import uuid

from . import agent as agent_mod
from . import config, context, llm, store as store_mod, tools as tools_mod, ui as ui_mod


def _new_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def build(cfg: dict, *, yolo: bool = False, quiet: bool = False):
    """Wire the object graph. Shared by the CLI and the tests."""
    cfg = dict(cfg)
    if yolo:
        cfg["_yolo"] = True
    store = store_mod.Store(cfg["db"])
    client = llm.Client(
        cfg["api_base"], cfg["api_key"],
        timeout=cfg["request_timeout"], max_retries=cfg["max_retries"],
    )
    ui = ui_mod.QuietUI() if quiet else ui_mod.UI()
    tools = tools_mod.Tools(cfg, store, ui)
    agent = agent_mod.Agent(cfg, store, client, tools, ui)
    return agent, store, client, tools, ui


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="minihermes",
        description="A tiny stdlib-only personal agent for low-RAM devices.",
    )
    ap.add_argument("-q", "--query", help="one-shot query: print the answer and exit")
    ap.add_argument("--session", help="session id to use or resume")
    ap.add_argument("--resume", metavar="SESSION", help="resume a session by id")
    ap.add_argument("--model", help="override the configured model")
    ap.add_argument("--yolo", action="store_true", help="skip shell approval")
    ap.add_argument("--init", action="store_true", help="write a starter config.json")
    ap.add_argument("--show-config", action="store_true", help="print effective config")
    ap.add_argument("--version", action="store_true", help="print version and exit")
    args = ap.parse_args(argv)

    cfg = config.load()
    if args.model:
        cfg["model"] = args.model

    if args.init:
        path = config.save(cfg)
        print("wrote %s" % path)
        print("edit it, then set the key:  export MINIHERMES_API_KEY=...")
        return 0

    if args.show_config:
        for k in sorted(cfg):
            if k == "api_key":
                print("%-22s %s" % (k, "(set)" if cfg[k] else "(MISSING)"))
            else:
                print("%-22s %s" % (k, cfg[k]))
        return 0

    if args.version:
        print("mini-hermes 0.2")
        return 0

    if not cfg.get("api_key"):
        print("no API key. set MINIHERMES_API_KEY, or put \"api_key\" in "
              + config.config_path().as_posix(), file=sys.stderr)
        return 2
    if not cfg.get("model"):
        print("no model configured. run --init then edit model, or pass --model",
              file=sys.stderr)
        return 2

    agent, store, client, tools, ui = build(cfg, yolo=args.yolo, quiet=bool(args.query))
    sid = args.session or args.resume or _new_session_id()
    if args.resume:
        info = store.session_info(sid)
        if not info:
            print("session not found: %s" % sid, file=sys.stderr)
            return 2
    store.ensure_session(sid, cfg["model"])

    try:
        if args.query:
            answer = agent.turn(sid, args.query)
            if answer:
                print(answer)
            return 0

        state = {
            "session": sid,
            "cfg": cfg,
            "tool_names": tools.names(),
            "new_session_id": _new_session_id,
        }
        ui.banner("0.2", cfg["model"])
        while True:
            line = ui.prompt(state["session"]).strip()
            if not line:
                continue
            if ui_mod.handle_bang(line, state, ui):
                continue
            try:
                if ui_mod.handle_slash(line, state, store, ui, agent):
                    continue
            except SystemExit:
                break
            try:
                agent.turn(state["session"], line)
                ui.status_bar(state["session"], cfg, agent)
            except llm.LLMError as e:
                ui.note("  ! request failed: %s" % e)
            except KeyboardInterrupt:
                ui.note("  (interrupted)")
    finally:
        client.close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
