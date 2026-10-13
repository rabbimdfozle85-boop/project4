"""Web portal — Python's built-in http.server plus a small router.

No framework. Serves a status dashboard and a chat box, backed by the same core
and the same database as the CLI and Telegram.

Binding rules:
  * 127.0.0.1 is free.
  * ANY other bind requires a token. A LAN-reachable panel is reachable by every
    device on the network, so refusing to start without auth is deliberate.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class PortalError(RuntimeError):
    pass


class Context:
    """Everything the handlers need, built once."""

    def __init__(self, cfg: dict, store, pool, tools, agent, log=None) -> None:
        self.cfg = cfg
        self.store = store
        self.pool = pool
        self.tools = tools
        self.agent = agent
        self.log = log or (lambda *_: None)
        self.started = time.time()
        self.token = cfg.get("portal_token") or ""
        self._lock = threading.Lock()
        self._sessions: dict = {}

    def session_for(self, name: str) -> str:
        with self._lock:
            if name not in self._sessions:
                sid = "web-%s" % (name or "default")
                self.store.ensure_session(sid, self.cfg.get("model", ""))
                self._sessions[name] = sid
            return self._sessions[name]


def _status_payload(ctx: Context) -> dict:
    health = ctx.pool.health()
    usage = ctx.store.usage_total()
    return {
        "product": "fhone",
        "version": __version__,
        "uptime_seconds": int(time.time() - ctx.started),
        "model": ctx.cfg.get("model") or "",
        "home": ctx.cfg["home"],
        "providers": health["providers"],
        "keys": {"total": health["total"], "ok": health["ok"],
                 "cooldown": health["cooldown"], "dead": health["dead"]},
        "usage": {"requests": usage["r"] or 0, "prompt_tokens": usage["p"] or 0,
                  "completion_tokens": usage["c"] or 0},
        "sessions": ctx.store.session_count(),
        "memories": ctx.store.memory_count(),
        "tools": ctx.tools.names(),
        "skills": len(_skill_index(ctx.cfg)),
        "jobs": len(ctx.store.jobs()),
    }


def _skill_index(cfg):
    try:
        from .. import skills as skills_mod
        return skills_mod.index(cfg)
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "fhone/" + __version__

    @property
    def ctx(self) -> Context:
        return self.server.ctx                     # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        pass

    # -- auth --------------------------------------------------------------
    def _authorised(self) -> bool:
        if self.server.server_address[0] in LOOPBACK:      # type: ignore[attr-defined]
            return True
        token = self.ctx.token
        if not token:
            return False
        given = ""
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            given = auth[7:].strip()
        if not given:
            query = urllib.parse.urlparse(self.path).query
            given = (urllib.parse.parse_qs(query).get("token") or [""])[0]
        return secrets.compare_digest(given, token)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _html(self, code: int, text: str) -> None:
        self._send(code, text.encode("utf-8"), "text/html; charset=utf-8")

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self._json(200, {"ok": True})
        if not self._authorised():
            return self._json(401, {"error": "unauthorised",
                                    "hint": "pass ?token=... or an Authorization header"})
        if path in ("/", "/index.html"):
            return self._html(200, DASHBOARD)
        if path == "/api/status":
            return self._json(200, _status_payload(self.ctx))
        if path == "/api/sessions":
            return self._json(200, [dict(r) for r in self.ctx.store.sessions()])
        if path == "/api/memory":
            query = urllib.parse.urlparse(self.path).query
            q = (urllib.parse.parse_qs(query).get("q") or [""])[0]
            rows = (self.ctx.store.memory_search(q) if q
                    else self.ctx.store.memory_list())
            return self._json(200, [dict(r) for r in rows])
        if path == "/api/team":
            return self._json(200, [dict(r) for r in self.ctx.store.team_feed()])
        return self._json(404, {"error": "not_found", "path": path})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._authorised():
            return self._json(401, {"error": "unauthorised"})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad_json"})

        if path == "/api/chat":
            message = str(body.get("message") or "").strip()
            if not message:
                return self._json(400, {"error": "message required"})
            session = str(body.get("session") or "default")
            sid = self.ctx.session_for(session)
            try:
                answer = self.ctx.agent.turn(sid, message)
            except Exception as e:
                self.ctx.log("portal: agent error: %s: %s" % (type(e).__name__, e))
                return self._json(500, {"error": "internal error — check logs"})
            return self._json(200, {"session": sid, "answer": answer})

        if path == "/api/memory":
            content = str(body.get("content") or "").strip()
            if not content:
                return self._json(400, {"error": "content required"})
            mid = self.ctx.store.memory_add(content, str(body.get("kind") or "fact"))
            return self._json(200, {"id": mid})

        return self._json(404, {"error": "not_found", "path": path})


class Portal:
    def __init__(self, cfg: dict, ctx: Context) -> None:
        self.cfg = cfg
        self.ctx = ctx
        self.host = cfg.get("portal_host", "127.0.0.1")
        self.port = int(cfg.get("portal_port", 8787))
        self.httpd = None
        self.thread = None

    def url(self) -> str:
        host = self.host if self.host not in ("0.0.0.0", "::") else _lan_ip()
        return "http://%s:%d" % (host, self.port)

    def start(self) -> str:
        if self.host not in LOOPBACK and not self.cfg.get("portal_token"):
            raise PortalError(
                "refusing to bind %s without a token: a LAN-reachable panel is "
                "reachable by every device on the network. Set portal_token in "
                "config.json (or bind 127.0.0.1)." % self.host)
        handler = Handler
        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as e:
            raise PortalError("cannot bind %s:%d (%s)" % (self.host, self.port, e))
        self.httpd.ctx = self.ctx                  # type: ignore[attr-defined]
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.5}, daemon=True)
        self.thread.start()
        return self.url()

    def stop(self) -> None:
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None


def _lan_ip() -> str:
    """Best-effort LAN address, without opening a real connection."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))          # TEST-NET-1: never routed
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


DASHBOARD = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FHONE</title>
<style>
 :root{--bg:#0f1115;--fg:#e6e6e6;--dim:#8b93a1;--acc:#7aa2f7;--ok:#9ece6a;--warn:#e0af68;--bad:#f7768e}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
 header{padding:14px 18px;border-bottom:1px solid #1c2029;display:flex;gap:14px;align-items:baseline}
 header b{color:var(--acc)} header span{color:var(--dim);font-size:12px}
 main{display:grid;grid-template-columns:1fr;gap:16px;padding:16px;max-width:900px;margin:0 auto}
 @media(min-width:760px){main{grid-template-columns:320px 1fr}}
 section{border:1px solid #1c2029;border-radius:8px;padding:14px;background:#12151b}
 h2{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim)}
 .row{display:flex;justify-content:space-between;gap:10px;padding:3px 0}
 .k{color:var(--dim)} .ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)}
 #log{height:340px;overflow:auto;padding:8px;background:#0c0e12;border-radius:6px;white-space:pre-wrap;word-break:break-word}
 .msg{margin:0 0 10px} .me{color:var(--acc)} .bot{color:var(--fg)}
 form{display:flex;gap:8px;margin-top:10px}
 input{flex:1;background:#0c0e12;border:1px solid #232833;color:var(--fg);padding:9px;border-radius:6px;font:inherit}
 button{background:var(--acc);color:#0c0e12;border:0;padding:9px 16px;border-radius:6px;font:inherit;cursor:pointer}
</style></head><body>
<header><b>FHONE</b><span id="ver"></span><span id="up"></span></header>
<main>
 <section><h2>status</h2><div id="status">loading…</div></section>
 <section><h2>chat</h2><div id="log"></div>
  <form id="f"><input id="m" placeholder="ask something…" autocomplete="off"><button>send</button></form>
 </section>
</main>
<script>
const $=s=>document.querySelector(s);
function esc(t){return String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function status(){
 try{
  const s=await (await fetch('/api/status')).json();
  $('#ver').textContent='v'+s.version;
  $('#up').textContent='up '+Math.floor(s.uptime_seconds/60)+'m';
  const k=s.keys;
  let h='';
  h+='<div class="row"><span class="k">model</span><span>'+esc(s.model||'unset')+'</span></div>';
  h+='<div class="row"><span class="k">keys</span><span class="'+(k.ok?'ok':'bad')+'">'+k.ok+' ok · '+k.cooldown+' cooling · '+k.dead+' dead</span></div>';
  h+='<div class="row"><span class="k">usage</span><span>'+s.usage.requests+' req · '+((s.usage.prompt_tokens+s.usage.completion_tokens)||0)+' tok</span></div>';
  h+='<div class="row"><span class="k">sessions</span><span>'+s.sessions+'</span></div>';
  h+='<div class="row"><span class="k">memories</span><span>'+s.memories+'</span></div>';
  h+='<div class="row"><span class="k">skills</span><span>'+s.skills+'</span></div>';
  h+='<div class="row"><span class="k">tools</span><span>'+s.tools.length+'</span></div>';
  s.providers.forEach(p=>{h+='<div class="row"><span class="k">'+esc(p.id)+'</span><span>'+esc(p.state)+'</span></div>'});
  $('#status').innerHTML=h;
 }catch(e){$('#status').textContent='unreachable'}
}
function add(cls,text){const d=document.createElement('div');d.className='msg '+cls;d.textContent=text;$('#log').appendChild(d);$('#log').scrollTop=1e9}
$('#f').addEventListener('submit',async e=>{
 e.preventDefault();const m=$('#m').value.trim();if(!m)return;
 $('#m').value='';add('me','› '+m);
 try{const r=await (await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m})})).json();
  add('bot',r.answer||('error: '+(r.error||'unknown')));}catch(err){add('bot','error: '+err)}
 status();
});
status();setInterval(status,5000);
</script></body></html>
"""
