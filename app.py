"""Calliope application server (stdlib HTTP). The normal app is live: real providers, the shopper's own photo and top.

  npm start                               build the React frontend, then run this server (one command)
  .venv/bin/python app.py                 http://127.0.0.1:8765/   (live; needs the configuration below and a built frontend)
  CALLIOPE_PROVIDER=stub .venv/bin/python app.py      development stub: every picture is SIMULATED, nothing is paid
  .venv/bin/python app.py --check         self-check (stub, temporary state)
  .venv/bin/python app.py --catalog-refresh   rebuild the catalog index from the Livostyle MCP now (also: npm run catalog)
  .venv/bin/python app.py --catalog-status    what index is active, its age, and the last build

Pages (one React bundle, frontend/ -> frontend/dist):
  /            the app (API: /api/*). Never falls back to recorded or simulated data.
  /s/<id>      a friend's read-only view of a shared look.

Configuration (environment or .env):
  YOUCAM_API_KEY, AWS_PROFILE (default "calliope"). Spending: the standing authorization in state/authorization.json
  (what is left is recomputed from the engine's ledger and Nova log at every start), or CALLIOPE_LIVE_UNITS (YouCam units
  for this process) and CALLIOPE_NOVA_USD (Nova cap) for a single run. If anything is missing the app shows an
  unavailable state naming it, and starts nothing.
  CALLIOPE_STATE_DIR (default state/app), CALLIOPE_ENGINE_DIR (default state/engine), PORT (default 8765),
  CALLIOPE_BASIC_AUTH=user:password  protects the app (not the friend's share links) when it's reachable beyond localhost,
  CALLIOPE_HOST (default 127.0.0.1), CALLIOPE_SCHEDULE=current|early, CALLIOPE_ROUND1_LOOKS=2|3,
  CALLIOPE_PROVIDER_CONCURRENCY (provider tasks in flight across all sessions, default 6).
  Catalog (catalog_sync.py): CALLIOPE_CATALOG_DIR (default state/catalog), CALLIOPE_CATALOG_REFRESH_DAYS (default 7; checked at
  start and when a session starts, never on a timer), CALLIOPE_CATALOG_NOVA_USD (paid classification cap per build, default 0.05).
  Speech (transcribe.py): CALLIOPE_TRANSCRIBE_USD (Amazon Transcribe spending cap; voice input is off without it),
  CALLIOPE_SPEECH_LANGUAGE (default en-US). CALLIOPE_TLS_CERT + CALLIOPE_TLS_KEY serve HTTPS (a phone's microphone needs a
  secure page; e.g. a mkcert certificate for this computer's LAN address).
Interface: the /api/* routes in Handler below.
"""
import json, os, re, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import base64, hmac

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "frontend" / "dist"         # npm run build
_env = ROOT / ".env"
if _env.exists():                     # values stay server-side and are never logged
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

import shopper as A  # noqa: E402
import catalog_sync as CAT  # noqa: E402
import transcribe as T  # noqa: E402

CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json", ".css": "text/css",
          ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".woff2": "font/woff2",
          ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav"}
COOKIE = "calliope_sid"
MAX_UPLOAD = 12 * 1024 * 1024
BASIC = os.environ.get("CALLIOPE_BASIC_AUTH", "")
# Routes that act for the browser's own session (cookie). /api/shares/<id> GET is the public snapshot (handled first).
OWNER = ("/api/session", "/api/brief", "/api/photo", "/api/owned-top", "/api/media/", "/api/jobs/", "/api/journey/", "/api/version", "/api/sizes",
         "/api/revisions", "/api/handoff", "/api/shares", "/api/speech")


def _img_type(data):
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


class Handler(BaseHTTPRequestHandler):
    server_version = "calliope"

    # ---------- plumbing
    def send(self, code, body, ctype="application/json", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        return True

    def redirect(self, to):
        self.send_response(302); self.send_header("Location", to); self.send_header("Content-Length", "0"); self.end_headers()
        return True

    def error(self, code, error, message=None):
        return self.send(code, {"code": error, "message": message or error})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("too_large")
        return self.rfile.read(n)

    def json_body(self):
        raw = self.body()
        self._req = json.loads(raw or b"{}")
        if not isinstance(self._req, dict):
            raise ValueError("expected a JSON object")
        return self._req

    def token(self):
        # A plain split, not SimpleCookie: it drops every cookie after one it can't parse (JSON, spaces), and browsers
        # send 127.0.0.1 cookies from other local apps on other ports.
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and v:
                return v
        return None

    def secure(self):
        return self.headers.get("X-Forwarded-Proto") == "https" or os.environ.get("CALLIOPE_SECURE_COOKIE") == "1"

    def set_cookie(self, token):
        return ("Set-Cookie", f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={A.SESSION_DAYS * 86400}" + ("; Secure" if self.secure() else ""))

    def authorized(self, path):
        """Optional basic auth for the shopper app when it's exposed (a preview tunnel). Friend links stay public."""
        if not BASIC or path.startswith(("/s/", "/api/shares/", "/assets/", "/fonts/")):      # the bundle holds no session data
            return True
        h = self.headers.get("Authorization") or ""
        ok = h.startswith("Basic ") and hmac.compare_digest(base64.b64decode(h[6:] + "==").decode(errors="replace"), BASIC)
        if not ok:
            self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="Calliope preview"'); self.send_header("Content-Length", "0"); self.end_headers()
        return ok

    def static(self, name, extra_head=""):
        """The built frontend (frontend/dist): index.html, hashed /assets/*, /fonts/*."""
        if not (DIST / "index.html").is_file():
            return self.send(503, b"<!doctype html><meta charset=utf-8><title>Calliope</title><p>The frontend isn't built. Run <code>npm run build</code> "
                             b"(or <code>npm start</code>, which builds and starts the server), then reload.", CTYPES[".html"])
        p = (DIST / unquote(name)).resolve()
        if not p.is_relative_to(DIST.resolve()) or not p.is_file() or p.suffix not in CTYPES:
            return self.error(404, "not_found")
        data = p.read_bytes()
        if extra_head:
            data = data.replace(b"<head>", b"<head>" + extra_head.encode(), 1)
        return self.send(200, data, CTYPES[p.suffix])

    # ---------- routes
    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        self._t0 = time.time(); u = urlparse(self.path); q = parse_qs(u.query)
        if not self.authorized(u.path):
            return
        try:
            return self.get(u, q) or self.error(404, "not_found")
        except (ValueError, KeyError) as e:
            return self.error(400, "bad_request", str(e))

    def get(self, u, q):
        p = u.path
        if p in ("/", "/index.html"):                               # the app (query strings change nothing here)
            return self.static("index.html")
        m = re.fullmatch(r"/s/(shr_[a-z2-7]{26})", p)
        if m:                                                      # the friend's read-only page
            code, sh = A.public_share(m.group(1))
            return self.static("index.html", f'<meta name="robots" content="noindex"><meta property="og:title" content="{"A look shared from Calliope" if code == 200 else "Calliope"}">')
        if p.startswith(("/assets/", "/fonts/")):
            return self.static(p[1:])
        if p.startswith("/api/shares/"):
            parts = p.split("/")
            if len(parts) == 4:
                code, body = A.public_share(parts[3]); return self.send(code, body)
            if len(parts) == 5 and parts[4] == "picture":
                f = A.share_picture(parts[3])
                return self.send(200, f.read_bytes(), "image/jpeg") if f else self.error(404, "not_found")
        if p == "/api/status":
            return self.send(200, A.status())
        if p == "/api/catalog/status":
            return self.send(200, CAT.status())
        if p.startswith(OWNER):
            s = self._s = A.owner(self.token())
            if p == "/api/session":
                return self.send(200, A.session_view(s) if s else {"session": None, "status": A.status()})
            if not s:
                return self.error(401, "no_session", "Start a session first.")
            if p in ("/api/photo", "/api/owned-top/image"):
                f = A.media_file(s, "photo" if p.endswith("photo") else "owned")
                return self.send(200, f.read_bytes(), "image/jpeg") if f and f.exists() else self.error(404, "not_found")
            if p.startswith("/api/jobs/"):
                j = A.job_for(s, p.rsplit("/", 1)[1])
                return self.send(200, j) if j else self.error(404, "not_found")
            if p.startswith("/api/media/"):
                f = A.media_path(s, unquote(p.rsplit("/", 1)[1]))
                return self.send(200, f.read_bytes(), _img_type(f.read_bytes())) if f else self.error(404, "not_found")
        return None

    def do_POST(self):
        return self.mutate()

    def do_PUT(self):
        return self.mutate()

    def do_DELETE(self):
        return self.mutate()

    def mutate(self):
        self._t0 = time.time(); u = urlparse(self.path); q = parse_qs(u.query); p = u.path
        if not self.authorized(p):
            return
        if p == "/api/catalog/refresh" and self.command == "POST":   # manual refresh / retry after a failed setup (bounded, one at a time)
            self.body()
            if CAT._RUN.locked():
                return self.send(202, CAT.status())
            cur = CAT.active()
            if CAT.compatible(cur) and CAT.age_days(cur) * 86400 < 600:
                return self.error(429, "too_soon", "The catalog was rebuilt in the last 10 minutes.")
            import threading
            threading.Thread(target=CAT.run, kwargs={"reason": "manual (app)"}, daemon=True).start()
            time.sleep(0.2)
            return self.send(202, CAT.status())
        if not p.startswith(OWNER):
            return self.error(404, "not_found")
        try:
            if p == "/api/sessions" and self.command == "POST":   # start over: a new session owned by this browser
                self.body()
                token, view = A.new_session()
                self._s = A.owner(token)
                return self.send(201, view, headers=[self.set_cookie(token)])
            s = self._s = A.owner(self.token())
            if not s:
                return self.error(401, "no_session", "Start a session first.")
            return self.route(s, p, q) or self.error(404, "not_found")
        except A.Unavailable as e:
            return self.error(503, "provider_unavailable", "Calliope can't make try-on pictures right now. Nothing has been started.")
        except A.Conflict as e:
            return self.error(409, str(e) if str(e) in ("needs_ack", "preview_not_ready") else "conflict", str(e))
        except (ValueError, KeyError, StopIteration) as e:
            msg = str(e).strip("'")
            return self.error(400, msg if re.fullmatch(r"[a-z_]+", msg or "") else "bad_request", msg)

    def route(self, s, p, q):
        c, parts = self.command, p.split("/")
        if p == "/api/photo":
            if c == "DELETE":
                self.body(); return self.send(200, A.remove_photo(s))
            r = A.upload_photo(s, self.body(), consent=(q.get("consent") or [""])[0] == "1")
            return self.send(200 if r["ok"] else 422, r)
        if p == "/api/owned-top":
            if c == "POST":
                r = A.upload_owned_top(s, self.body(), (q.get("hint") or [""])[0][:60]); return self.send(200 if r["ok"] else 422, r)
            if c == "PUT":
                b = self.json_body(); return self.send(200, A.confirm_owned_top(s, b.get("category"), b.get("colorName"), b.get("hex")))
            if c == "DELETE":
                self.body(); return self.send(200, A.remove_owned_top(s))
        if p.startswith("/api/speech"):
            return self.speech(s, parts)
        b = self.json_body() if c != "DELETE" else (self.body(), {})[1]
        if p == "/api/brief/read":
            return self.send(200, A.read_brief(s, b.get("text") if isinstance(b.get("text"), str) else ""))
        if p == "/api/journey/start":
            return self.send(200, A.journey_start(s, b))
        if p == "/api/journey/palette":
            return self.send(200, A.journey_palette(s, b.get("name") or ""))
        if p == "/api/journey/keep":
            if b.get("round") not in (1, 2) or b.get("id") not in ("A", "B", "C", "D"):
                raise ValueError("round must be 1 or 2 and id A-D")
            return self.send(200, A.journey_keep(s, b["round"], b["id"], b.get("pins") or {}))
        if p == "/api/journey/finalize":
            keys = b.get("keys") or []
            if not isinstance(keys, list) or not set(keys) <= {"colors", "shape", "feel"}:
                raise ValueError("keys must be a subset of colors, shape, feel")
            return self.send(200, A.journey_finalize(s, keys, (b.get("text") or "")[:500]))
        if p == "/api/journey/choose":
            return self.send(200, A.journey_choose(s, b.get("id")))
        if p == "/api/version":
            return self.send(200, A.choose_version(s, int(b.get("index", -1))))
        if p == "/api/sizes":
            return self.send(200, A.set_size(s, b.get("slot"), b.get("size")))
        if p == "/api/revisions":
            if not isinstance(b.get("text"), str) or not 0 < len(b["text"]) <= 300:
                raise ValueError("text must be 1-300 characters")
            return self.send(200, A.request_change(s, b["text"], b.get("versionId")))
        if len(parts) == 5 and parts[2] == "revisions":
            fn = {"answer": lambda: A.answer(s, parts[3], b.get("choice")), "dismiss": lambda: A.dismiss(s, parts[3]),
                  "accept": lambda: A.accept(s, parts[3], b.get("candidateId"))}.get(parts[4])
            return fn and self.send(200, fn())
        if len(parts) == 5 and parts[2] == "jobs" and parts[4] == "retry":
            return self.send(200, A.retry(s, parts[3], bool(b.get("acknowledgePossibleDuplicateCharge"))))
        if p == "/api/handoff":
            try:
                q, rb = b.get("acceptQuote"), b.get("raiseBudgetTo")
                if (q is not None and not isinstance(q, str)) or (rb is not None and (isinstance(rb, bool) or not isinstance(rb, (int, float)) or rb <= 0)):
                    raise ValueError("acceptQuote must be a string and raiseBudgetTo a positive number")
                return self.send(200, A.handoff(s, b.get("versionId"), accept_quote=q, raise_budget_to=rb))
            except OSError as e:
                return self.error(502, "merchant_unreachable", "Couldn't reach Livostyle to check stock right now. Nothing was sent.")
        if p == "/api/shares" and c == "POST":
            return self.send(201, A.create_share(s, b.get("versionId")))
        if len(parts) == 4 and parts[2] == "shares" and c == "DELETE":
            return self.send(200, A.revoke_share(s, parts[3]))
        return None

    def speech(self, s, parts):
        """POST /api/speech -> {speechId}; POST …/{id}/audio (16 kHz mono s16le PCM) -> {text};
        POST …/{id}/end -> {text, final}; DELETE …/{id} cancels. Audio never leaves this server except to Transcribe."""
        c, owner = self.command, s["sessionId"]
        try:
            if len(parts) == 3 and c == "POST":
                self.body()
                return self.send(201, {"speechId": T.start(owner).id, **{k: v for k, v in T.status().items() if k in ("sampleRate", "maxSeconds")}})
            if len(parts) == 5 and parts[4] == "audio" and c == "POST":
                return self.send(200, {"text": T.get(parts[3], owner).feed(self.body())})
            if len(parts) == 5 and parts[4] == "end" and c == "POST":
                self.body(); return self.send(200, T.end(parts[3], owner))
            if len(parts) == 4 and c == "DELETE":
                self.body(); return self.send(200, T.end(parts[3], owner, cancel=True))
        except T.SpeechError as e:
            if len(parts) >= 4:
                try: T.end(parts[3], owner, cancel=True)
                except T.SpeechError: pass
            return self.error({"gone": 404, "too_long": 413, "silence": 422, "budget": 503, "not_configured": 503, "auth": 503}.get(e.code, 502), "speech_" + e.code, str(e))
        return None

    def log_message(self, fmt, *args):            # no query strings or cookies in the log
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), (fmt % args).split("?")[0]))


def configure():
    prov = os.environ.get("CALLIOPE_PROVIDER", "youcam")
    A.configure(os.environ.get("CALLIOPE_STATE_DIR") or ROOT / "state" / "app", provider=prov, nlu=os.environ.get("CALLIOPE_NLU"),
                live_units=os.environ.get("CALLIOPE_LIVE_UNITS") or 0, nova_usd=os.environ.get("CALLIOPE_NOVA_USD") or 0,
                stub_delay=float(os.environ.get("CALLIOPE_STUB_DELAY", "1")), engine_dir=os.environ.get("CALLIOPE_ENGINE_DIR"),
                schedule=os.environ.get("CALLIOPE_SCHEDULE", "current"), round1=os.environ.get("CALLIOPE_ROUND1_LOOKS") or 2)
    import engine as R
    T.configure(R.STATE / "transcribe_calls.jsonl")
    left = (A.CFG.get("budget") or {}).get("novaUsdLeft")
    CAT.CFG.update(nova_log=R.STATE / "nova_calls.jsonl", on_activate=A.use_catalog,
                   nova_left=None if prov == "stub" else (left if left is not None else float(os.environ.get("CALLIOPE_NOVA_USD") or 0)))
    if prov == "stub":
        CAT.CFG["nova_left"] = 0.0                       # the development stub never pays for classification
    A.CFG["catalog_auto"] = prov != "stub"
    return prov


if __name__ == "__main__":
    import faulthandler, signal
    faulthandler.register(signal.SIGUSR1, all_threads=True)      # kill -USR1 <pid>: every thread's stack to stderr (a stuck request)
    if "--check" in sys.argv:
        import subprocess
        sys.exit(subprocess.call([sys.executable, str(ROOT / "shopper.py")]))
    prov = configure()
    if "--catalog-status" in sys.argv:
        print(json.dumps(CAT.status(), indent=1)); sys.exit(0)
    if "--catalog-refresh" in sys.argv:
        print(json.dumps(CAT.run("manual (command line)", progress=lambda **k: (CAT._set(**k), print(f"  {k.get('phase')}: {k.get('done')}/{k.get('total')} {k.get('message') or ''}", flush=True))), indent=1, default=str))
        sys.exit(0)
    host, port = os.environ.get("CALLIOPE_HOST", "127.0.0.1"), int(os.environ.get("PORT") or 8765)
    if prov != "stub":
        cs = CAT.startup()                               # active index, else the shipped snapshot, else a first build (in the background)
        a = cs.get("active") or {}
        print(f"Catalog: {cs['state']}" + (f", built {a['builtAt']} ({a['ageDays']} days old" + (", refreshing in the background" if a.get("refreshDue") else "") + ")" if a else "")
              + (f"  ·  {cs.get('message')}" if cs.get("message") else ""), flush=True)
    st = A.status()
    if not (DIST / "index.html").is_file():
        print("Note: the frontend isn't built (frontend/dist). Run `npm run build`, or start with `npm start`.", flush=True)
    b = A.CFG.get("budget")
    if b:
        print(f"Spending: {b['unitsLeft']} YouCam units and ${b['novaUsdLeft']:.4f} Nova left of the {b['authorizedOn']} authorization "
              f"({b['youcamUnits']} units, ${b['novaUsd']}); state/authorization.json", flush=True)
    print(f"Calliope on http://{host}:{port}/  ·  {'DEVELOPMENT STUB (simulated pictures)' if prov == 'stub' else 'live providers'}"
          + ("" if st["available"] else "  ·  UNAVAILABLE: " + "; ".join(A.CFG["problems"])), flush=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    if os.environ.get("CALLIOPE_TLS_CERT"):
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(os.environ["CALLIOPE_TLS_CERT"], os.environ.get("CALLIOPE_TLS_KEY"))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        os.environ.setdefault("CALLIOPE_SECURE_COOKIE", "1")
        print(f"HTTPS on https://{host}:{port}/", flush=True)
    httpd.serve_forever()
