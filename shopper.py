"""Shopper sessions: the approved journey for one shopper per session, on the engine.

  photo + owned top + brief -> palette -> round 1 (2 looks) -> round 2 (kept look vs challenger) -> what to carry (after
  round 2 only) -> finalists -> selected look -> change requests -> sizes -> bag;  share a version as a read-only snapshot.

Each session is owned by one browser (a random token in an HttpOnly cookie; only its hash is stored). Sessions, jobs and
shares are files under the state directory, so they survive a restart. Providers:
  live (default): YouCam pictures + analysis, Nova for face box, eye check and change requests. Missing configuration is
                  reported by provider_problems(); the app then refuses to start work (never a simulated fallback).
  stub:           explicitly enabled development stand-in (provider_stub.py); every picture says SIMULATED.
    python3 shopper.py      # self-check: two stub sessions through the whole journey (no paid calls)
"""
import base64, copy, hashlib, io, json, os, re, secrets, shutil, threading, time, urllib.error, urllib.request
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

import attributes as AT
import catalog_sync as CAT
import change_request as CR
import engine as R
import seasons

ROOT = Path(__file__).resolve().parent
OCC = json.loads((ROOT / "data/occasions.json").read_text())
IDX, BY_ID = R.INDEX, {i["id"]: i for i in R.INDEX}      # the active catalog; use_catalog() replaces both after a refresh


def use_catalog(doc):
    """A refreshed catalog becomes the one new selections use. Sessions keep their own copies of chosen pieces, so a
    product that left the catalog still shows in a saved look (marked no longer sold) and is checked live at the bag."""
    global IDX, BY_ID
    R.use_catalog(doc)
    IDX, BY_ID = R.INDEX, {i["id"]: i for i in R.INDEX}
BASE_BRIEF = copy.deepcopy(R.DATA["brief"])
KEEP = ("id", "slot", "title", "handle", "price", "hex", "cls", "dist", "family", "pictured_color", "tryon_image", "url", "heel_height")
SLOTS = ("top", "dress", "bottom", "shoes", "necklace")       # display order; a composition uses R.STRUCTS[structure]
STRUCTURES = ("either", "separates", "dress")
SESSION_DAYS, SHARE_DAYS = 7, 14             # ESOD §9: session content 7 days; share snapshots 14 days (proposed, R-17)
TOP_CATEGORIES = ["blouse", "shirt", "t-shirt", "knit top", "camisole"]
HEELS = {"label": "No heels", "attribute": "heel_height", "op": "exclude", "values": ["heeled"], "slots": ["shoes"], "sourcePhrase": "no heels"}
# out_name suffix -> stage. The owned top layer is "-top"; catalog garments are "-tf-<slot>".
STAGE_OF = {"-tf-top": "top", "-tf-dress": "dress", "-tf-bottom": "bottom", "-tf-shoes": "shoes", "-tf-necklace": "necklace", "-top": "owned_top"}

LOCK = threading.RLock()     # ponytail: one lock for all sessions (a few shoppers at once); per-session locks if it ever matters
CFG = {"provider": None}
S = {"sessions": {}, "tokens": {}, "jobs": {}}
NOVA = None
_TL = threading.local()


class Unavailable(Exception):
    """Providers aren't configured; nothing was started."""


class Conflict(Exception):
    """The request contradicts what this session already did (repeat with different data)."""


# ---------------------------------------------------------------- configuration
def configure(state_dir, provider="youcam", nlu=None, live_units=0, nova_usd=0.0, stub_delay=1.0, engine_dir=None, schedule="current", round1=2):
    """Set providers and state locations. Live mode never falls back to the stub."""
    global NOVA
    st = Path(state_dir); eng = Path(engine_dir) if engine_dir else st.parent / "engine"
    if schedule not in ("current", "early") or int(round1) not in (2, 3):
        raise SystemExit("CALLIOPE_SCHEDULE must be current or early; CALLIOPE_ROUND1_LOOKS 2 or 3")
    CFG.update(schedule=schedule, round1=int(round1))
    CFG.update(provider=provider, state=st, sessions=st / "sessions", media=st / "media", shares=st / "shares",
               jobs_file=st / "jobs.json", problems=[], budget=None)
    for k in ("sessions", "media", "shares"):
        CFG[k].mkdir(parents=True, exist_ok=True)
    if provider == "stub":
        from provider_stub import StubYouCam
        R.use_dirs(eng / "state-stub", eng / "out-stub")
        refuse = os.environ.get("CALLIOPE_STUB_REFUSE_NECKLACE") == "1"          # development only: imitate a photo the necklace engine refuses
        stub = StubYouCam(eng / "out-stub" / "provider", delay_s=stub_delay, analysis_results=_stub_analysis,
                          fail=["necklace"] if refuse else (), fail_message="4: Neck roll check failed. (stub)")
        R.upload, R.yc, R.balance = stub.upload, stub.yc, stub.balance
        R.CAP_UNITS = 10 ** 9
        CFG["nlu"] = nlu or "stub"
    elif provider == "youcam":
        R.use_dirs(eng / "state", eng / "out")
        CFG["budget"] = None if int(live_units or 0) or float(nova_usd or 0) else authorization(eng)
        if CFG["budget"]:
            live_units, nova_usd = CFG["budget"]["unitsLeft"], CFG["budget"]["novaUsdLeft"]
        CFG["problems"] = provider_problems(live_units, nova_usd, CFG["budget"])
        R.CAP_UNITS = R.units_committed() + int(live_units)       # units this process may commit (ledger-checked before every POST)
        R.NOVA_USD = float(nova_usd)
        CFG["nlu"] = nlu or "nova"
    else:
        raise SystemExit("CALLIOPE_PROVIDER must be youcam (default) or stub")
    NOVA = None
    if CFG["nlu"] == "stub":
        NOVA = CR.StubNova(json.loads((ROOT / "fixtures/nlu/change_requests.json").read_text())["cases"])
    elif CFG["nlu"] == "nova" and not CFG["problems"]:
        from nova_common import Nova
        R.nova = NOVA = Nova(cap_usd=float(nova_usd), log_path=R.STATE / "nova_calls.jsonl")
        NOVA.mode = "nova"
    R.run_task = _tracked(R.run_task.__wrapped__ if hasattr(R.run_task, "__wrapped__") else R.run_task)
    S.update(sessions={}, tokens={}, jobs=json.loads(CFG["jobs_file"].read_text()) if CFG["jobs_file"].exists() else {})
    for f in CFG["sessions"].glob("ses_*.json"):
        s = json.loads(f.read_text()); S["sessions"][s["sessionId"]] = s; S["tokens"][s["tokenHash"]] = s["sessionId"]
    _resume_jobs()


def authorization(eng):
    """The standing spend authorization (state/authorization.json) for this engine, with what is left of it now. Left =
    authorized - spent elsewhere - spent by this engine since the baseline (its ledger and Nova log), so it is recomputed
    at every start and a restart never grants the budget again. None if there is no record for this engine."""
    f = ROOT / "state" / "authorization.json"
    if not f.is_file():
        return None
    a = json.loads(f.read_text())
    if (ROOT / a["engine"]).resolve() != Path(eng).resolve():
        return None
    log = R.STATE / "nova_calls.jsonl"
    nova = sum(json.loads(l).get("cost", 0) for l in log.read_text().splitlines() if l.strip()) if log.exists() else 0.0
    units = a["youcamUnits"] - a["usedElsewhere"]["youcamUnits"] - (R.units_committed() - a["baseline"]["committedUnits"])
    usd = a["novaUsd"] - a["usedElsewhere"]["novaUsd"] - (nova - a["baseline"]["novaUsd"])
    return {"authorizedOn": a["authorizedOn"], "youcamUnits": a["youcamUnits"], "novaUsd": a["novaUsd"],
            "unitsLeft": max(0, int(units)), "novaUsdLeft": max(0.0, round(usd, 6))}


def provider_problems(live_units, nova_usd, budget=None):
    """Local checks only (no network): what's missing for real providers."""
    p = []
    if not os.environ.get("YOUCAM_API_KEY"): p.append("YOUCAM_API_KEY is not set")
    if budget and int(live_units or 0) <= 0: p.append(f"the {budget['authorizedOn']} authorization ({budget['youcamUnits']} YouCam units) is used up; a new authorization is needed")
    elif budget and float(nova_usd or 0) <= 0: p.append(f"the {budget['authorizedOn']} authorization (${budget['novaUsd']} Nova) is used up; a new authorization is needed")
    if not budget and int(live_units or 0) <= 0: p.append("CALLIOPE_LIVE_UNITS (the authorized YouCam unit budget) is not set, and state/authorization.json has no standing authorization for this engine")
    if not budget and float(nova_usd or 0) <= 0: p.append("CALLIOPE_NOVA_USD (the authorized Nova spend cap) is not set")
    try:
        import boto3
        if not boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "calliope")).get_credentials():
            p.append("no AWS credentials for Nova")
    except ImportError:
        p.append("boto3 is not installed (needed for Nova)")
    except Exception as e:
        p.append(f"AWS profile for Nova: {type(e).__name__}")
    return p


def status():
    live = CFG["provider"] == "youcam"
    cat = CAT.status()
    ready = bool(IDX)
    return {"available": not CFG["problems"] and ready, "mode": "live" if live else "stub",
            "message": None if not CFG["problems"] else "Calliope can't make try-on pictures right now.",
            "action": None if not CFG["problems"] else "The server is missing: " + "; ".join(CFG["problems"]) + ". Set these in .env or the environment and restart the server.",
            "simulated": not live, "speech": speech_status(),
            "catalog": {"ready": ready, **{k: cat.get(k) for k in ("state", "phase", "done", "total", "message", "error", "retry")},
                        "builtAt": (cat.get("active") or {}).get("builtAt"), "refreshDue": (cat.get("active") or {}).get("refreshDue")}}


def speech_status():
    import transcribe
    return transcribe.status()


def _require_available():
    if CFG["problems"]:
        raise Unavailable("; ".join(CFG["problems"]))
    if not IDX:
        raise Unavailable("the catalog index isn't ready yet")


def _stub_analysis(path):
    """Development stub only: a synthetic analysis from the face crop's own pixels (no person's real result)."""
    im = Image.open(path).convert("RGB"); W, H = im.size
    c = im.crop((W * 2 // 5, H * 2 // 5, W * 3 // 5, H * 3 // 5)).resize((1, 1), Image.BOX).getpixel((0, 0))
    return {"color": {"skin_color": "#%02x%02x%02x" % c, "eye_color": "#3b2a20", "eyebrow_color": "#4a3426"},
            "face_quality": {"has_face": True, "frontal": "good", "lighting": "good", "faceangle": "good", "area": "good"}}


# ---------------------------------------------------------------- sessions and ownership
def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session():
    """A fresh session for this browser; returns (token, view). The token is only ever sent in the cookie."""
    token = secrets.token_urlsafe(32); sid = "ses_" + secrets.token_hex(8); now = time.time()
    s = {"sessionId": sid, "tokenHash": _hash(token), "createdAt": now, "expiresAt": now + SESSION_DAYS * 86400,
         "providers": {"generation": CFG["provider"], "interpretation": CFG["nlu"]}, "photo": None, "owned": None, "brief": None,
         "notUsed": [], "ctx": None, "journey": None, "palette": None, "versions": [], "cur": 0, "revisions": {}, "activeRevision": None,
         "sizes": {}, "shares": [], "events": []}
    with LOCK:
        S["sessions"][sid] = s; S["tokens"][s["tokenHash"]] = sid
        _event(s, "session"); _save(s)
    if CFG.get("catalog_auto"):
        CAT.maybe_refresh()                       # a long-running server re-checks freshness when someone starts; no scheduler
    return token, session_view(s)


def owner(token):
    """The session this browser owns, or None. Expired sessions are deleted with their pictures."""
    if not token:
        return None
    with LOCK:
        sid = S["tokens"].get(_hash(token)); s = S["sessions"].get(sid) if sid else None
        if s and time.time() > s["expiresAt"]:
            delete_session(s); return None
        return s


def delete_session(s):
    with LOCK:
        S["sessions"].pop(s["sessionId"], None); S["tokens"].pop(s["tokenHash"], None)
        (CFG["sessions"] / f"{s['sessionId']}.json").unlink(missing_ok=True)
        shutil.rmtree(CFG["media"] / s["sessionId"], ignore_errors=True)


def _save(s):
    p = CFG["sessions"] / f"{s['sessionId']}.json"; tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1)); tmp.replace(p)
    tmp = CFG["jobs_file"].with_suffix(".tmp"); tmp.write_text(json.dumps(S["jobs"], indent=1)); tmp.replace(CFG["jobs_file"])


def _event(sess, name, **detail):
    sess["events"].append({"t": round(time.time(), 3), "ms": round((time.time() - sess["createdAt"]) * 1000), "event": name, **detail})


# ---------------------------------------------------------------- photo and owned top
def _prep_image(data, max_side=4096):
    """Re-encode as JPEG: orientation applied, EXIF and other metadata dropped."""
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO(); im.save(out, "JPEG", quality=93)
    return out.getvalue(), im


def _check_upload(data):
    """YouCam file limits (JPEG/PNG, <=10 MB, >=512x384). Returns (ok, code, message)."""
    if len(data) > 10 * 1024 * 1024:
        return False, "too_large", f"This photo is {len(data) / 1e6:.1f} MB. Use one under 10 MB."
    try:
        im = Image.open(io.BytesIO(data)); im.verify(); im = Image.open(io.BytesIO(data))
    except Exception:
        return False, "unsupported_format", "That file isn't a photo I can read. Use a JPEG or PNG."
    if im.format not in ("JPEG", "PNG"):
        return False, "unsupported_format", f"{im.format} photos aren't supported. Use a JPEG or PNG."
    w, h = ImageOps.exif_transpose(im).size
    if max(w, h) < 512 or min(w, h) < 384:
        return False, "too_small", f"This photo is {w}×{h} pixels. Use one at least 512×384."
    return True, "ok", ""


def upload_photo(s, data, consent):
    """The shopper's full-length photo. Returns as soon as the local checks pass (format, size, resolution, brightness,
    portrait framing). The face box and the necklace check then run in the background while the shopper writes the brief;
    the shopper is never asked about them. A photo the necklace engine refuses simply gets no necklace in its pictures."""
    _require_available()
    if not consent:
        raise ValueError("consent_required")
    if s["journey"]:
        raise Conflict("This session has already started styling. Start over to use another photo.")
    ok, code, msg = _check_upload(data)
    if not ok:
        return {"ok": False, "code": code, "message": msg}
    jpg, im = _prep_image(data)
    sha = hashlib.sha256(jpg).hexdigest()
    d = CFG["media"] / s["sessionId"]; d.mkdir(exist_ok=True)
    (d / f"photo-{sha[:16]}.jpg").write_bytes(jpg)
    W, H = im.size
    L = ImageStat.Stat(im.convert("L")).mean[0]
    issue = None
    if L < 45:
        issue = {"code": "too_dark", "title": "! This photo is quite dark",
                 "body": "Previews come out best in even light. You can keep it, or choose a brighter photo."}
    elif W >= H:
        issue = {"code": "not_portrait", "title": "! This looks like a landscape photo",
                 "body": "Previews need you head to shoes, standing. A portrait photo usually works best."}
    sid = "p" + sha[:16]
    with LOCK:
        s["photo"] = {"photoId": "pho_" + sha[:16], "file": f"photo-{sha[:16]}.jpg", "sha256": sha, "width": W, "height": H, "face": None,
                      "checks": {"format": "ok", "resolution": "ok", "brightness": "dark" if L < 45 else "ok",
                                 "portrait": W < H, "face": "checking", "necklace": "checking"}, "issue": issue}
        _event(s, "photo", w=W, h=H, issue=issue and issue["code"]); _save(s)
    _start_photo_checks(s, sid, d / f"photo-{sha[:16]}.jpg")
    return {"ok": True, "photo": photo_view(s)}


_CHECKS, _FACE_LOCKS = {}, {}


def _start_photo_checks(s, sid, photo):
    """Face box + necklace check for one photo, once, on its own thread (independent of the request that uploaded it)."""
    with LOCK:
        if sid in _CHECKS:
            return _CHECKS[sid]
        ev = _CHECKS[sid] = threading.Event()
    def run():
        face, neck, fb = "found", {"status": "unknown"}, None
        try:
            fb = _face({"sid": sid, "photo": str(photo), "face": None})
            if CFG["schedule"] == "early":            # early schedule: colour analysis and the owned-top layer start now, alongside the check
                def early_analysis():
                    _analyze({"sid": sid, "photo": str(photo), "face": fb})
                    with LOCK:
                        _event(s, "analysis_done"); _save(s)
                threading.Thread(target=_quiet, args=(early_analysis,), daemon=True).start()
                _start_owned_layer(s)
            neck = R.necklace_precheck(sid, photo, fb, _check_necklace())
        except Exception:
            face = "not found"
        with LOCK:
            p = s.get("photo")
            if p and p["sha256"][:16] == sid[1:]:            # the shopper may have replaced the photo meanwhile
                p["face"] = fb; p["checks"].update(face=face, necklace=neck["status"])
                if s.get("ctx") and s["ctx"]["sid"] == sid:
                    s["ctx"].update(face=s["ctx"].get("face") or fb, necklace=neck["status"])
                _event(s, "photo_checks", face=face, necklace=neck["status"]); _save(s)
        ev.set()
    threading.Thread(target=run, daemon=True).start()
    return ev


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass                                     # the step that needs this result reports the failure


def _owned_ctx(s):
    p, o = s.get("photo"), s.get("owned")
    if not (p and o and o.get("confirmed")):
        return None
    return {"sid": "p" + p["sha256"][:16], "photo": str(CFG["media"] / s["sessionId"] / p["file"]), "face": p.get("face"),
            "ownedRef": str(CFG["media"] / s["sessionId"] / o["file"]), "ownedId": o["ownedId"]}


def _start_owned_layer(s):
    """Early schedule: a locked owned top is layered as soon as the photo and the top are both confirmed, then the necklace
    is checked against that layer (it is the base every separates look is built on). Shared stages are reused, never repeated."""
    ctx = _owned_ctx(s)
    if not ctx or s.get("journey"):
        return
    def run():
        out = _ensure_top(ctx)
        _necklace_ok(ctx, out, owned=True)
        with LOCK:
            _event(s, "owned_layer_ready"); _save(s)
    threading.Thread(target=_quiet, args=(run,), daemon=True).start()


def _prestart(s):
    """Early schedule: once the suggested palette is known, select the initial looks for it and start their pictures while
    the shopper reviews the palette. Pictures are keyed by content, so continuing with that palette reuses these jobs;
    another palette selects other looks and these are never shown (they're counted as unused)."""
    with LOCK:
        jr = s["journey"]
        if not jr or jr["step"] != "palette" or jr.get("prestart") or not jr.get("palette"):
            return
        _engine_brief(s)
        looks = _sellable(lambda idx: R.outfits(s["ctx"]["sid"], jr["palette"]["id"], idx, 8, excluded=set(), structure=s.get("structure", "either"), owned_top=s.get("useOwned", False)),
                          lambda ls: _look_items(*ls))
        jr["prestart"] = {"palette": jr["palette"]["id"], "jobs": [start_preview(s, full(s, l), label=f"early {chr(65 + i)}") for i, l in enumerate(looks[:CFG["round1"]])]}
        _event(s, "prestart", palette=jr["prestart"]["palette"], jobs=jr["prestart"]["jobs"]); _save(s)


def _necklace_ok(ctx, base=None, owned=False):
    """May the necklace be drawn on this picture? Decided once per base image: the photo for catalog looks; the owned-top
    layer for looks built on it (a collar can cover a tilted neck: the Oct 2 finding behind the filmed photo + its blue blouse)."""
    sid, photo = ctx["sid"], Path(ctx["photo"])
    if owned and base is not None:
        return R.necklace_precheck(f"{sid}-on-{ctx.get('ownedId')}", base, _face(ctx), _check_necklace())["status"] != "refused"
    ev = _CHECKS.get(sid)
    if ev: ev.wait(120)
    rec = R.jl("necklace_checks.json", {}).get(sid) or R.necklace_precheck(sid, photo, _face(ctx), _check_necklace())
    return rec["status"] != "refused"


def _check_necklace():
    """The catalog necklace used for the photo check (any single-item necklace; the result is about the pose)."""
    return next(i for i in sorted(IDX, key=lambda x: x["id"]) if i["slot"] == "necklace" and i["in_stock"] and i.get("single_item") is not False)


def remove_photo(s):
    if s["journey"]:
        raise Conflict("This session has already started styling. Start over to use another photo.")
    with LOCK:
        s["photo"] = None; _save(s)
    return session_view(s)


def photo_view(s):
    p = s.get("photo")
    return p and {"photoId": p["photoId"], "url": "/api/photo?v=" + p["photoId"], "width": p["width"], "height": p["height"],
                  "checks": p["checks"], "issue": p["issue"]}


def _center_hex(im):
    W, H = im.size
    c = im.crop((W * 2 // 5, H * 2 // 5, W * 3 // 5, H * 3 // 5)).resize((1, 1), Image.BOX).getpixel((0, 0))
    return "#%02x%02x%02x" % c


def _owned_read(oid, path, hint):
    """Category and color of the shopper's top, without asking when the answer is clear: the photo read by Nova when it is
    sure, else the shopper's own words when they name both (e.g. "my cobalt silk blouse"). Returns (category, color,
    readBy); readBy None means the shopper is asked, with whatever was found filled in."""
    words = re.findall(r"[a-z-]+", (hint or "").lower())
    cat = next((c for c in TOP_CATEGORIES if all(w in words for w in c.split())), None) or ("t-shirt" if "tee" in words else None)   # "top" alone names no kind
    color = " ".join(w for w in words if w not in {"my", "your", "a", "an", "the", "silk", "cotton", "linen"} and w not in " ".join(TOP_CATEGORIES).split() + ["top", "tee"])
    read = None
    if CFG["provider"] == "youcam":                       # the development stub has no garment reading; it asks
        try:
            read = R.garment(oid, path, TOP_CATEGORIES)
        except Exception:
            read = None                                   # no reading (budget, provider): ask rather than guess
    r_cat = read and read.get("category") in TOP_CATEGORIES and read["category"]
    r_col = read and (read.get("color") or "").strip().lower()[:30]
    if read and read.get("is_top") and r_cat and r_col and read.get("confidence") in ("high", "medium"):
        return r_cat, r_col, "photo"
    if cat and color:
        return cat, color, "words"
    return r_cat or cat, r_col or color, None


def upload_owned_top(s, data, name_hint=""):
    """A top the shopper owns, photographed on its own. Its kind and color are read from the photo (or taken from the
    shopper's words); the shopper is asked only when neither is clear, and can correct it before styling starts."""
    _require_available()
    if s["journey"]:
        raise Conflict("This session has already started styling. Start over to change your top.")
    ok, code, msg = _check_upload(data)
    if not ok:
        return {"ok": False, "code": code, "message": msg}
    jpg, im = _prep_image(data, 2048)
    sha = hashlib.sha256(jpg).hexdigest()
    d = CFG["media"] / s["sessionId"]; d.mkdir(exist_ok=True)
    (d / f"top-{sha[:16]}.jpg").write_bytes(jpg)
    oid = "own_" + sha[:16]
    cat, color, by = _owned_read(oid, d / f"top-{sha[:16]}.jpg", name_hint)
    with LOCK:
        fit = {k: v for k, v in (R.jl("garments.json", {}).get(oid) or {}).items() if k in ("length", "volume", "fabric_weight", "hem") and v and v != "unclear"}
        s["owned"] = {"ownedId": oid, "file": f"top-{sha[:16]}.jpg", "sha256": sha, "hex": _center_hex(im),
                      "colorName": color or "", "category": cat, "confirmed": bool(by), "readBy": by, "fit": fit}
        _event(s, "owned_top", readBy=by, category=cat); _save(s)
    if by and CFG["schedule"] == "early":
        _start_owned_layer(s)
    return {"ok": True, "owned": owned_view(s), "categories": TOP_CATEGORIES}


def confirm_owned_top(s, category, color_name, hex_=None):
    if s["journey"]:
        raise Conflict("This session has already started styling. Start over to change your top.")
    o = s.get("owned")
    if not o:
        raise ValueError("add a photo of your top first")
    if category not in TOP_CATEGORIES:
        raise ValueError("category must be one of " + ", ".join(TOP_CATEGORIES))
    color_name = (color_name or "").strip()
    if not 0 < len(color_name) <= 30:
        raise ValueError("say the color of your top (1-30 characters)")
    if hex_ is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", hex_):
        raise ValueError("hex must look like #1a2b3c")
    with LOCK:
        o.update(category=category, colorName=color_name.lower(), hex=(hex_ or o["hex"]).lower(), confirmed=True, readBy="shopper")
        _event(s, "owned_top_confirmed", category=category); _save(s)
    if CFG["schedule"] == "early":
        _start_owned_layer(s)
    return owned_view(s)


def remove_owned_top(s):
    if s["journey"]:
        raise Conflict("This session has already started styling. Start over to change your top.")
    with LOCK:
        s["owned"] = None; _save(s)
    return session_view(s)


def owned_view(s):
    o = s.get("owned")
    return o and {"ownedId": o["ownedId"], "url": "/api/owned-top/image?v=" + o["ownedId"], "hex": o["hex"], "colorName": o["colorName"],
                  "category": o["category"], "confirmed": o["confirmed"], "readBy": o.get("readBy"), "label": _top_title(o)}


def _top_title(o):
    return ((o.get("colorName") or "").capitalize() + " " + (o.get("category") or "top")).strip()


def _owned_item(s):
    o = s["owned"]
    return {"slot": "top", "kind": "owned", "owned": True, "ownedGarmentId": o["ownedId"], "title": _top_title(o),
            "pictured_color": o["colorName"], "hex": o["hex"], "price": 0, "attributes": {}, "fit": o.get("fit") or {}}


def media_file(s, kind):
    """The owner's own photo or top image (never anyone else's)."""
    rec = s.get("photo") if kind == "photo" else s.get("owned")
    return CFG["media"] / s["sessionId"] / rec["file"] if rec else None


# ---------------------------------------------------------------- shared helpers
def slug(x):
    return re.sub(r"[^a-z0-9]+", "-", (x or "").lower()).strip("-")


def item_ref(it):
    return {"kind": "owned", "ownedGarmentId": it["ownedGarmentId"]} if it.get("owned") else \
        {"kind": "catalog", "productId": it["id"], "colorKey": slug(it.get("pictured_color"))}


def composition_id(comp):
    canon = json.dumps({sl: item_ref(i) for sl, i in comp.items()}, sort_keys=True, separators=(",", ":"))
    return "cmp_" + hashlib.sha256(canon.encode()).hexdigest()[:24]


def subtotal(comp):
    return round(sum(i["price"] for i in comp.values() if not i.get("owned")), 2)


def full(s, look):
    """Engine look -> validated composition. Separates without a catalog top carry the shopper's own top."""
    st = R.structure_of(look)
    comp = {sl: (look[sl] if look[sl].get("owned") else {k: look[sl].get(k) for k in KEEP}) for sl in R.STRUCTS[st] if isinstance(look.get(sl), dict)}
    if st == "separates" and "top" not in comp and s.get("useOwned"):
        comp["top"] = _owned_item(s)
    return validate_comp(comp)


def validate_comp(comp):
    """Exactly one structure's slots: top+bottom+shoes+necklace or dress+shoes+necklace (never both)."""
    st = R.structure_of(comp)
    if set(comp) != set(R.STRUCTS[st]):
        raise ValueError(f"incomplete {st} composition: {sorted(comp)}")
    return comp


def _necklace_out(comp, jid=None, ctx=None):
    """True when the outfit's picture doesn't (or won't) show the necklace: the finished picture came back without it, or,
    before a finished picture, the necklace check on its base image (photo, or owned-top layer) has refused."""
    j = S["jobs"].get(jid) or {}
    if j.get("status") in ("success", "partial"):
        return j["status"] == "partial"
    c = j.get("ctx") or ctx
    if not c:
        return False
    key = f"{c['sid']}-on-{c.get('ownedId')}" if comp.get("top", {}).get("owned") else c["sid"]
    return (R.jl("necklace_checks.json", {}).get(key) or {}).get("status") == "refused"   # ponytail: a file read per look per view; cache if polling gets heavy


def shown(comp, jid=None, ctx=None):
    """The outfit as its picture shows it: a necklace that couldn't be pictured is left out of the pieces, total, share and bag."""
    return {sl: i for sl, i in comp.items() if sl != "necklace"} if _necklace_out(comp, jid, ctx) else comp


def slots_of(comp):
    return [sl for sl in SLOTS if sl in comp]


def buyable(comp):
    return [sl for sl in slots_of(comp) if not comp[sl].get("owned")]


def sizes_of(it):
    """Sizes for the pictured colour, from the pinned dataset (stock re-checked live at handoff)."""
    if it.get("owned"):
        return [], []
    cur = BY_ID.get(it["id"])
    if not cur:                                          # left the catalog since it was chosen: nothing to size or buy
        return [], []
    want = (it.get("pictured_color") or "").lower()
    vs = [v for v in cur["variants"] if (v["options"].get("Color") or "").lower() == want] or cur["variants"]
    sizes = [v["options"].get("Size") for v in vs if v["options"].get("Size")]
    return sizes, [v["options"]["Size"] for v in vs if v["options"].get("Size") and not v["in_stock"]]


def chosen_size(s, it):
    """The size this shopper chose for this slot, if this item has it in stock; never a default."""
    z = s["sizes"].get(it["slot"])
    sizes, un = sizes_of(it)
    return z if z and z in sizes and z not in un else None


def display_name(title, colour):
    cw = set(re.findall(r"[a-z]+", (colour or "").lower())); words = title.split()
    while len(words) > 1 and words[0].lower().strip("-,") in cw:
        words = words[1:]
    name = " ".join(words)
    return re.sub(r"\s+in\s+" + re.escape(colour or "") + r"$", "", name, flags=re.I) if colour else name


def ui_item(s, it):
    if it.get("owned"):
        return {"key": "blouse", "slot": "top", "name": ((s.get("owned") or {}).get("category") or "top").capitalize(), "variant": (it["pictured_color"] or "").capitalize(),
                "color": it["hex"], "owned": True, "price": 0}
    sizes, un = sizes_of(it)
    return {"key": it["id"], "productId": it["id"], "slot": it["slot"], "name": display_name(it["title"], it.get("pictured_color")), "title": it["title"],
            "variant": (it.get("pictured_color") or "").capitalize(), "color": it.get("hex") or "#999999", "price": it["price"],
            "sizes": sizes or None, "unavailable": un, "savedSize": chosen_size(s, it), "url": it["url"], "image": it.get("tryon_image"),
            "noLongerSold": it["id"] not in BY_ID,
            "attributes": {a: AT.value(R.attrs_of(it), a) for a in AT.V["integrated"] + ["pants_type"]}}


def _engine_brief(s):
    """Selection code reads the brief from R.DATA; set it for this session while holding the lock."""
    R.DATA["brief"] = s["brief"]


# ---------------------------------------------------------------- views
def job_view(jid):
    j = S["jobs"].get(jid)
    if not j:
        return None
    err = j.get("error") or ""
    code = None if j.get("status") not in ("failed", "partial") else (
        "budget_cap_reached" if "cap:" in err else "submit_unknown" if "submit_unknown" in err or "interrupted" in err
        else "provider_timeout" if "still running" in err else "provider_error" if err else "necklace_not_shown")
    return {"jobId": jid, "compositionId": j["compositionId"], "provider": j["provider"], "status": j["status"], "errorCode": code,
            "stage": j.get("stage"), "stageIndex": j.get("stageIndex"), "stageCount": len(_stage_plan(j["composition"])), "startedAt": j.get("startedAt"), "finishedAt": j.get("finishedAt"),
            "imageUrl": f"/api/media/{j['output']}" if j.get("status") in ("success", "partial") and j.get("output") else None,
            "note": j.get("note"), "retry": _retry_state(j) if j.get("status") in ("failed", "partial") else None,
            "tuck": (j.get("tuck") or {}).get("tuck"),
            "steps": [{**{k: st.get(k) for k in ("slot", "status", "reason", "parent", "productId", "category", "provider_s", "wall_s")},
                       **{k: st[k] for k in ("tuck",) if k in st}} for st in j.get("steps", [])]}


def version_view(s, v):
    comp = shown(v["composition"], v["previewJobId"])
    return {"versionId": v["versionId"], "label": v["label"], "compositionId": v["compositionId"], "subtotal": subtotal(comp),
            "items": [ui_item(s, comp[sl]) for sl in slots_of(comp)], "structure": R.structure_of(comp),
            "productIds": {sl: (i.get("ownedGarmentId") if i.get("owned") else i["id"]) for sl, i in comp.items()},
            "previewJob": job_view(v["previewJobId"]), "createdFrom": v.get("createdFrom"),
            "shared": any(sh["versionId"] == v["versionId"] and _share_live(sh) for sh in _shares_of(s))}


def session_view(s):
    b = s["brief"] or {}
    act = s["revisions"].get(s.get("activeRevision") or "")
    return {"sessionId": s["sessionId"], "palette": s.get("palette"), "cur": s["cur"], "status": status(),
            "photo": photo_view(s), "owned": owned_view(s), "notUsed": s["notUsed"], "sizes": s["sizes"],
            "activeRevision": revision_view(s, act) if act and act["status"] not in ("accepted", "dismissed") else None,
            "brief": {"occasion": b.get("occasion"), "budgetNew": b.get("budgetNew"), "budgetMode": b.get("budgetMode"),
                      "paletteRequired": bool(b.get("palette_required")),
                      "constraints": [c["label"] for c in R.DATA["constraints"]["always"] + b.get("constraints", [])],
                      "structure": s.get("structure", "either"), "ownedTopUsed": bool(s.get("useOwned"))} if s["brief"] else None,
            "journey": journey_view(s), "versions": [version_view(s, v) for v in s["versions"]], "providers": s["providers"],
            "shares": [share_owner_view(sh) for sh in _shares_of(s)]}


# ---------------------------------------------------------------- journey
def _brief_from(ui):
    """The start step's fields -> the engine brief. Missing budget is an error (0 means No limit), never a default."""
    occ = (ui.get("occasion") or "").strip()
    if not occ:
        raise ValueError("occasion_required")
    if ui.get("budget") is None:
        raise ValueError("budget_required")
    budget = int(ui["budget"])
    if budget < 0:
        raise ValueError("budget must be 0 (no limit) or more")
    b = copy.deepcopy(BASE_BRIEF)
    cons, not_used = [HEELS] if ui.get("heels") else [], []
    dress, level, why = ui.get("dress") or "", None, ""
    if dress in OCC["dress"]:
        level, why = OCC["dress"][dress], dress
    else:
        key = next((k for k in OCC["occasions"] if k in occ.lower()), None)
        if key is None:
            not_used.append(f"I don't know how dressy “{occ}” is, so it doesn't change which pieces I pick. Say how dressy if it matters.")
        elif OCC["occasions"][key] is None:
            not_used.append(f"“{occ}” doesn't tell me how dressy to go, so it doesn't change which pieces I pick.")
        else:
            level, why = OCC["occasions"][key], occ
    if level:
        L = OCC["levels"][level]
        cons.append({"label": dress if dress in OCC["dress"] else f"{'Smart' if level == 'smart' else 'Formal'} for {occ.lower()}",
                     "attribute": "formality", "op": "exclude", "values": L["exclude"], "slots": L["slots"], "sourcePhrase": why})
    for n in ui.get("notes") or []:
        if isinstance(n, str) and n.strip():
            not_used.append(f"Noted, but I can't use it to choose pieces yet: {n.strip()[:80]}.")
    b.update(occasion=occ, budgetNew=None if budget == 0 else budget, budgetMode="approx10" if ui.get("approx") and budget else "firm",
             constraints=cons, palette_required=False)
    return b, not_used


# The brief, read by Nova (forced tool use). Every field is checked here: quoted words must be in her text, the budget a
# plausible number, choices from fixed lists. The page shows the result as chips she checks before anything starts; when
# Nova isn't configured or fails, the page reads the brief with its own rules instead (interpreter "rules").
_W = {"type": "string", "description": "her exact words for this, copied from the text; empty if she didn't say"}
BRIEF_TOOL = {"name": "record_brief", "description": "What a shopper said about the outfit she needs.",
              "schema": {"type": "object", "additionalProperties": False,
                         "required": ["occasion", "occasion_words", "budget_usd", "budget_kind", "budget_words", "avoid_heels", "heels_words",
                                      "dress_level", "dress_words", "outfit", "outfit_words", "comfort", "comfort_words", "owned_item", "owned_words", "other_wishes"],
                         "properties": {
                             "occasion": {"type": "string", "description": "the occasion in 1-4 plain words, spelling corrected (e.g. 'anniversary dinner'); empty if not said"},
                             "occasion_words": _W,
                             "budget_usd": {"type": "integer", "description": "her budget for new pieces in US dollars; 0 if not said or no limit"},
                             "budget_kind": {"enum": ["about", "up_to", "no_limit", "not_said"]}, "budget_words": _W,
                             "avoid_heels": {"type": "boolean"}, "heels_words": _W,
                             "dress_level": {"enum": ["relaxed", "smart_evening", "formal", "not_said"]}, "dress_words": _W,
                             "outfit": {"enum": ["dress", "separates", "either"]}, "outfit_words": _W,
                             "comfort": {"type": "boolean", "description": "she wants comfort (walking a lot, comfortable)"}, "comfort_words": _W,
                             "owned_item": {"type": "string", "description": "a piece she says she already owns and wants to wear, e.g. 'blue blouse'; empty if none"},
                             "owned_words": _W,
                             "other_wishes": {"type": "array", "items": {"type": "string"}, "description": "anything else she asked for, in a few words each"}}}}
BRIEF_SYSTEM = ("You read a shopper's description of the outfit she needs into fields. Be literal: only record what she said; "
                "correct spelling in 'occasion'; copy her exact words into each *_words field. 'about/around $200' is budget_kind about; "
                "'under/up to/max $200' or a plain '$200 budget' is up_to; 'no budget' or 'no limit' is no_limit.")
DRESS_LEVEL = {"relaxed": "Relaxed", "smart_evening": "Smart evening", "formal": "Formal"}


def _clean_brief(out, text):
    low = text.lower()
    words = lambda k: (out.get(k) or "").strip() if (out.get(k) or "").strip().lower() in low else ""
    f, occ = {}, re.sub(r"\s+", " ", str(out.get("occasion") or "")).strip()[:60]
    if occ:
        f["occasion"] = {"value": occ, "phrase": words("occasion_words") or None, "corrected": occ.lower() not in low}
    n, kind = out.get("budget_usd"), out.get("budget_kind")
    if kind == "no_limit":
        f["budget"] = {"none": True, "phrase": words("budget_words") or None}
    elif kind in ("about", "up_to") and isinstance(n, int) and 10 <= n <= 100000:
        f["budget"] = {"n": n, "approx": kind == "about", "phrase": words("budget_words") or None}
    if out.get("avoid_heels") is True:
        f["heels"] = {"phrase": words("heels_words") or None}
    if out.get("dress_level") in DRESS_LEVEL:
        f["dress"] = {"value": DRESS_LEVEL[out["dress_level"]], "phrase": words("dress_words") or None}
    if out.get("outfit") in ("dress", "separates"):
        f["structure"] = {"value": out["outfit"], "phrase": words("outfit_words") or None}
    if out.get("comfort") is True:
        f["comfort"] = {"phrase": words("comfort_words") or None}
    own = str(out.get("owned_item") or "").strip()[:40]
    if own:
        f["owned"] = {"value": own.lower(), "phrase": words("owned_words") or None}
    f["notes"] = [str(x).strip()[:60] for x in (out.get("other_wishes") or []) if str(x).strip()][:3]
    return f


def read_brief(s, text):
    """-> {interpreter, fields?}. No fields: the page reads the brief with its own rules."""
    text = (text or "").strip()[:500]
    if not text:
        raise ValueError("text_required")
    if CFG.get("nlu") != "nova" or NOVA is None:
        return {"interpreter": "rules"}
    try:
        out, secs = NOVA.call(BRIEF_SYSTEM, [{"text": f"Shopper said:\n{text}"}], BRIEF_TOOL, 400)
    except Exception as e:
        return {"interpreter": f"rules (model unavailable: {type(e).__name__})"}
    if not isinstance(out, dict):
        return {"interpreter": "rules (model gave no reading)"}
    f = _clean_brief(out, text)
    with LOCK:
        _event(s, "brief_read", interpreter="nova", s=round(secs, 2)); _save(s)
    return {"interpreter": "nova", "fields": f}


def journey_start(s, ui):
    """Open fitting room: the brief is fixed for this session; colour reading and the top layer start in the background."""
    _require_available()
    with LOCK:
        if s["journey"]:
            return session_view(s)                     # repeated click: nothing new is started
        if not s["photo"]:
            raise ValueError("photo_required")
        if s["owned"] and not s["owned"]["confirmed"]:
            raise ValueError("owned_top_unconfirmed")
        b, not_used = _brief_from(ui)
        structure = ui.get("structure") or "either"
        if structure not in STRUCTURES:
            raise ValueError("structure must be either, separates or dress")
        owned = bool(s["owned"])
        if owned and structure == "dress":          # the shopper chose a dress over their own top at the start
            not_used.append(f"Your {_top_title(s['owned']).lower()} isn't in these looks, because you asked for a dress.")
        use_owned = owned and structure != "dress"
        photo = media_file(s, "photo")
        s.update(brief=b, notUsed=not_used, structure="separates" if use_owned else structure, useOwned=use_owned,
                 ctx={"sid": "p" + s["photo"]["sha256"][:16], "photo": str(photo), "face": s["photo"].get("face"),
                      "necklace": s["photo"]["checks"].get("necklace"),
                      **({"ownedRef": str(media_file(s, "owned")), "ownedId": s["owned"]["ownedId"]} if use_owned else {"ownedId": None})})
        s["journey"] = {"step": "palette", "brief": {k: ui.get(k) for k in ("occasion", "budget", "approx", "heels", "dress", "structure")},
                        "analysis": {"status": "running", "startedAt": time.time()}, "looks": {}, "jobs": {}, "win": {}, "pins": {}}
        _event(s, "start", brief=s["journey"]["brief"], constraints=[c["label"] for c in b["constraints"]], notUsed=not_used)
        _save(s)
    threading.Thread(target=_read_colors, args=(s,), daemon=True).start()
    if s["useOwned"]:
        threading.Thread(target=_safe_top, args=(s["ctx"],), daemon=True).start()     # every separates look chains from this layer
    return session_view(s)


def _face(ctx):
    if ctx.get("face"):
        return ctx["face"]
    with LOCK:
        lock = _FACE_LOCKS.setdefault(ctx["sid"], threading.Lock())
    with lock:                                   # one face box per photo, whoever asks first (upload check, colours, pictures)
        return _face_once(ctx)


def _face_once(ctx):
    if ctx.get("face"):
        return ctx["face"]
    photo = Path(ctx["photo"])
    if CFG["provider"] == "stub":                                  # development stub: a fixed upper-centre box (no model)
        W, H = Image.open(photo).size
        fb = {"box": [int(W * .42), int(H * .06), int(W * .58), int(H * .16)], "image_size": [W, H], "source": "stub"}
    else:
        fb = R.face_box(ctx["sid"], photo)
    ctx["face"] = fb
    return fb


def _analyze(ctx):
    """Face crop -> colour analysis -> eye check, once per photo (engine caches by photo; concurrent callers share the stage)."""
    fb = _face(ctx)
    fp, _ = R.face_crop(ctx["sid"], Path(ctx["photo"]), fb)
    a = R.analyze(ctx["sid"], fp)
    eyes = R.eyes_visible(ctx["sid"], fp) if CFG["provider"] != "stub" else None
    return a, eyes


def _read_colors(s):
    jr, t0 = s["journey"], time.time()
    try:
        a, eyes = _analyze(s["ctx"])
        pf = R.palette_for(s["ctx"]["sid"], a, eyes)
        cands = [c["season"] for c in pf["interpretation"].get("candidates", [])]
        with LOCK:
            if cands:
                jr["palette"] = {**_palette_payload(s, cands[0], cands[1:]), "headline": pf["headline"], "explanation": pf["explanation"]}
                jr["analysis"] = {"status": "ready", "s": round(time.time() - t0, 1)}
            else:
                jr["analysis"] = {"status": "unavailable", "reason": _why_no_palette(a, eyes, pf), "s": round(time.time() - t0, 1)}
            _event(s, "palette_ready", palette=cands[0] if cands else None, analysis_s=round(time.time() - t0, 1)); _save(s)
        if CFG["schedule"] == "early":
            _prestart(s)
    except Exception as e:
        with LOCK:
            msg = str(e)
            jr["analysis"] = {"status": "unavailable", "reason": "no_face" if "head box" in msg or isinstance(e, (TypeError, AssertionError)) else "provider_error",
                              "s": round(time.time() - t0, 1)}
            _event(s, "palette_failed", error=msg[:160]); _save(s)


ANALYSIS_COPY = {"no_face": "I couldn't find your face clearly in this photo, so any palette I gave you would be a guess.",
                 "face_small": "Your face is quite small in this photo, so any palette I gave you would be a guess.",
                 "face_angle": "Your face is turned away in this photo, so any palette I gave you would be a guess.",
                 "lighting": "The light in this photo is too low to read your colors reliably.",
                 "eyes_covered": "Your eyes are covered in this photo, so any palette I gave you would be a guess.",
                 "between_palettes": "Your coloring sits between palettes, so I won't pick one for you.",
                 "unsuitable": "This photo doesn't let me read your colors reliably.",
                 "provider_error": "I couldn't read your colors right now."}


def _why_no_palette(a, eyes, pf):
    """The actual reason, from the provider's error, the eye check, or the interpretation (never a generic guess)."""
    err = a.get("error") or ""
    if a.get("status") != "success":
        return "lighting" if "lighting" in err else "face_angle" if re.search(r"angle|tilt|forward|upward", err) else \
            "face_small" if "too_small" in err else "no_face" if "face" in err else "provider_error"
    if eyes and (not eyes.get("eyes_visible") or eyes.get("sunglasses")):
        return "eyes_covered"
    return "between_palettes" if pf["interpretation"].get("status") == "direction_only" else "unsuitable"


def _palette_payload(s, pid, alts):
    def sw(p):
        sws = seasons.PALETTES[p]["swatches"]
        return [x["hex"] for x in sws if "neutral" not in x.get("tags", [])][:6] + [x["hex"] for x in sws if "neutral" in x.get("tags", [])][:2]
    o = s["owned"] if s.get("useOwned") else None
    name = o and f"{o['colorName']} {o['category']}"
    def top_line(p):
        if not o: return None
        cls = R.color_class(o["hex"], p)[0]
        return {"on_palette": f"Your {name} sits right in this palette.",
                "neutral": f"Your {name} reads as a neutral here, so almost anything can go with it."}.get(
            cls, f"Your {name} isn’t in this palette, and that’s fine. It’ll be the star, and I’ll find pieces that make it pop.")
    return {"id": pid, "name": seasons.PALETTES[pid]["name"], "swatches": sw(pid), "blouseLine": top_line(pid), "note": "This is a starting point, not a verdict.",
            "alternatives": [{"id": a, "name": seasons.PALETTES[a]["name"], "swatches": sw(a), "blouseLine": top_line(a)} for a in alts]}


def _safe_top(ctx):
    try:
        _ensure_top(ctx)
    except Exception:
        pass                                  # the looks' own jobs report the failure (and retry it explicitly)


def _ensure_top(ctx):
    """The owned top layered on the photo once per (photo, top); every look chains from it."""
    photo = Path(ctx["photo"])
    k = R.skey(f"photo:{hashlib.sha256(photo.read_bytes()).hexdigest()[:16]}", "top", "owned:" + ctx["ownedId"], "upper_body")
    out, _ = R.run_task(k, "/s2s/v2.0/task/cloth-v4", lambda s_, r_: {"src_file_id": s_, "ref_file_id": r_, "garment_category": "upper_body", "change_shoes": False},
                        photo, Path(ctx["ownedRef"]), R.UNITS["cloth"], f"{ctx['sid']}-top")
    return out


def _look_tag(look):
    m, sh = look[R.main_slot(look)], look["shoes"]
    noun = "dress" if "dress" in look else "trousers"
    top = look.get("top")
    lead = f"{(top.get('pictured_color') or '').capitalize()} top, " if top and not top.get("owned") else ""
    return lead + f"{(m.get('pictured_color') or '').lower() if lead else (m.get('pictured_color') or '').capitalize()} {R.shape_word(m)} {noun}, {(sh.get('pictured_color') or '').lower()} shoes"


def _set_look(s, lid, look):
    jr = s["journey"]; comp = full(s, look)
    jr["looks"][lid] = {"look": {sl: comp[sl] for sl in comp if not comp[sl].get("owned")}, "tag": _look_tag(comp)}
    jr["jobs"][lid] = start_preview(s, comp, label=lid)


def journey_palette(s, name):
    """Suggested palette (or another candidate, or 'No palette') -> round 1: the engine's two best looks, both rendered."""
    _require_available()
    with LOCK:
        jr = s["journey"]
        if not jr:
            raise ValueError("open the fitting room first")
        if jr["step"] != "palette":
            if jr.get("paletteName") == name:
                return session_view(s)
            raise Conflict("Colors were already chosen for these looks.")
        if jr["analysis"]["status"] == "running":
            raise Conflict("Still reading your colors.")
        pal = jr.get("palette") or {}
        ids = {pal.get("name"): pal.get("id"), **{a["name"]: a["id"] for a in pal.get("alternatives", [])}}
        if name != "No palette" and name not in ids:
            raise ValueError("choose one of the suggested palettes or No palette")
        pid = ids.get(name) if name != "No palette" else None
        s["palette"] = pid; jr["paletteName"] = name
        _engine_brief(s)
        looks = _sellable(lambda idx: R.outfits(s["ctx"]["sid"], pid, idx, 8, excluded=set(), structure=s.get("structure", "either"), owned_top=s.get("useOwned", False)),
                          lambda ls: _look_items(*ls))
        n = CFG["round1"]
        if len(looks) < n:
            raise ValueError(f"I couldn't find {('two', 'three')[n - 2]} looks within your budget and choices.")
        jr["reserve"] = looks; jr["round1"] = [chr(65 + i) for i in range(n)]; jr["challenger"] = chr(65 + n)
        for i in range(n):
            _set_look(s, chr(65 + i), looks[i])
        jr["step"] = "round1"
        _event(s, "palette_chosen", palette=pid, looks={"A": _key(looks[0]), "B": _key(looks[1])}); _save(s)
    return session_view(s)


def _key(look):
    return R.look_key(look)


def _observe(jr, rnd, lid, shown_ids):
    """Record a comparison as observed (the looks shown, what differed, what was kept) and re-infer the tentative signals."""
    L = jr.setdefault("learning", {"observed": [], "inferred": [], "stated": []})
    rejected = [x for x in shown_ids if x != lid]
    for r in rejected:
        L["observed"].append({"round": rnd, "shown": shown_ids, "chosen": lid, "rejected": r, "pairs": len(rejected), "at": time.time(),
                              "differences": R.compare_looks(jr["looks"][lid]["look"], jr["looks"][r]["look"])})
    L["inferred"] = R.infer_signals(L["observed"])


def journey_keep(s, rnd, lid, pins=None):
    """A round choice keeps the whole look. It is also recorded as an observed comparison; the catalog facts that differed
    become weak, tentative signals (journey.learning) that rank the challenger and Proposal 2 below every stated choice.
    Round 1 -> challenger; round 2 -> what to carry."""
    _require_available()
    with LOCK:
        jr = s["journey"]
        want = {1: "round1", 2: "round2"}[rnd]
        done = jr and jr["win"].get(f"r{rnd}")
        if done:
            if done == lid:
                return session_view(s)                 # repeated click: same answer, nothing new started
            raise Conflict(f"Round {rnd} is already decided (Look {done}).")
        allowed = jr.get("round1", ["A", "B"]) if rnd == 1 else [jr["win"].get("r1"), jr.get("challenger", "C")] if jr else []
        if not jr or jr["step"] != want or lid not in allowed or lid not in jr["looks"]:
            raise ValueError(f"not in round {rnd} or no Look {lid}")
        if jr["jobs"].get(lid) and S["jobs"][jr["jobs"][lid]]["status"] == "running":
            raise Conflict("Wait for this look's picture before keeping it.")
        _engine_brief(s)
        jr["pins"] = {sl: pid for sl, pid in (pins or {}).items() if sl in ("top", "dress", "bottom", "shoes", "necklace")}
        if rnd == 1:
            jr["win"]["r1"] = lid
            _observe(jr, 1, lid, jr.get("round1", ["A", "B"]))
            sig = jr["learning"]["inferred"]
            kept, shown = jr["looks"][lid]["look"], {_key(l["look"]) for l in jr["looks"].values()}
            ok = lambda l: _key(l) not in shown and all(isinstance(l.get(sl), dict) and l[sl]["id"] == p for sl, p in jr["pins"].items())
            n = len(jr.get("round1", ["A", "B"]))
            # the kept structure first (the other if none is left), then the look that best agrees with the round-1 signals
            pool = sorted((l for l in jr["reserve"][n:] if ok(l)), key=lambda l: (R.structure_of(l) != R.structure_of(kept), -R.agreement(l, sig)))
            ch = pool[0] if pool else None
            if not ch:
                p2 = _sellable(lambda idx: R.proposal2(kept, f"Look {lid}", [], s["palette"], idx, shown, set(), pins=jr["pins"], signals=sig),
                               lambda r: _look_items((r or {}).get("look")))
                ch = {sl: dict(BY_ID[v["id"]], **v) for sl, v in p2["look"].items()} if p2 and p2.get("status") != "no_match" else None
            if not ch:
                jr["win"].pop("r1")
                raise ValueError("There isn't another look that keeps your pinned piece within your budget.")
            _set_look(s, jr.get("challenger", "C"), ch)
            jr["step"] = "round2"
            _event(s, "keep_round1", look=lid, pins=jr["pins"], challenger=_key(ch))
        else:
            jr["win"]["r2"] = lid; jr["step"] = "why"
            _observe(jr, 2, lid, [jr["win"]["r1"], jr.get("challenger", "C")])
            _event(s, "keep_round2", look=lid, signals=[R.signal_text(x) + f" ({x['confidence']})" for x in jr["learning"]["inferred"][:4]])
        _save(s)
    return session_view(s)


REASON_KEYS = {"colors": "colors", "shape": "trouser_shape", "feel": "overall_feel"}


def _reasons(s, lid):
    """What to carry, grounded in the winner's own pieces; for a dress, its shape (silhouette or length), never trousers."""
    look = s["journey"]["looks"][lid]["look"]
    m, sh = look[R.main_slot(look)], look["shoes"]
    nk = None if _necklace_out(look, s["journey"]["jobs"].get(lid), s.get("ctx")) else look["necklace"]
    noun = "dress" if "dress" in look else "trousers"
    learned = {x["attribute"]: x["value"] for x in R.learned_from(look, f"Look {lid}", list(REASON_KEYS.values()))}
    short = {"colors": f"{(m.get('pictured_color') or '').capitalize()} {noun}, {(sh.get('pictured_color') or '').lower()} shoes",
             "shape": f"{R.shape_word(m).capitalize()} {noun}",
             "feel": f"{(m.get('pictured_color') or '').capitalize()} {R.shape_word(m)}, {(sh.get('pictured_color') or '').lower()} shoes" + (f", {nk['title'].split(' with ')[0].lower()}" if nk else "")}
    shape_label = "The dress shape" if noun == "dress" else "The trouser shape"
    return [{"k": k, "label": lab, "short": short[k], "value": learned[REASON_KEYS[k]]} for k, lab in (("colors", "The colors"), ("shape", shape_label), ("feel", "The overall feel"))]


def journey_finalize(s, keys, text=""):
    """Reasons stated after round 2 only (or Skip) -> Proposal 2 from the engine; Proposal 1 is the actual winner."""
    _require_available()
    with LOCK:
        jr = s["journey"]
        if jr and jr.get("carry"):
            if sorted(jr["carry"]["keys"]) == sorted(keys):
                return session_view(s)
            raise Conflict("Your final looks were already built from what you told me.")
        if not jr or jr["step"] != "why":
            raise ValueError("finish round 2 first")
        _engine_brief(s)
        wid = jr["win"]["r2"]; win = jr["looks"][wid]["look"]
        ek = [REASON_KEYS[k] for k in keys if k in REASON_KEYS]
        shown = {_key(l["look"]) for l in jr["looks"].values()}
        sig = _apply_stated(jr, ek, win, text)
        p2 = _sellable(lambda idx: R.proposal2(win, f"Look {wid}", ek, s["palette"], idx, shown, set(), pins=jr["pins"], signals=sig),
                       lambda r: _look_items((r or {}).get("look")))
        if not p2 or p2.get("status") == "no_match":
            raise ValueError("I couldn't build a second final look within your budget and choices.")
        look = {sl: dict(BY_ID[v["id"]], **v) for sl, v in p2["look"].items()}
        _set_look(s, "P2", look)
        jr["looks"]["P2"]["tag"] = p2["tag"]
        jr["carry"] = {"keys": keys, "text": text, "learned": [{"attr": k, "value": v["value"], "from": wid} for k, v in zip(
            [k for k in ("colors", "shape", "feel") if k in keys], [x for x in _reasons(s, wid) if x["k"] in keys])],
            "acknowledgement": R.acknowledgement(ek, f"Look {wid}", win), "tradeoffs": p2["tradeoffs"], "tag": p2["tag"],
            "signalsFollowed": p2.get("signalsFollowed", [])}
        jr["step"] = "final"
        _event(s, "finalize", winner=wid, keys=keys, proposal2=_key(look)); _save(s)
    return session_view(s)


STATED_COVERS = {"colors": ("lightness", "color"), "trouser_shape": ("shape",)}


def _apply_stated(jr, keys, win, text=""):
    """The post-round answer confirms, refines or overrides the tentative signals it speaks to. What the shopper said is
    kept as stated (separately from what was observed or inferred); signals it doesn't speak to stay tentative."""
    L = jr.setdefault("learning", {"observed": [], "inferred": [], "stated": []})
    main = R.main_slot(win)
    L["stated"] = [{"reason": k, "from": jr["win"]["r2"], "source": "post-round answer"} for k in keys] + ([{"text": text[:200], "source": "own words"}] if text.strip() else [])
    for x in L["inferred"]:
        covered = next((k for k, attrs in STATED_COVERS.items() if k in keys and x["attribute"] in attrs and x["slot"] in (main, "shoes" if k == "colors" else main)), None)
        if not covered:
            continue
        sides = R.agreement(win, [dict(x, status="tentative")])
        x["status"] = "overridden" if sides < 0 else ("confirmed" if x["attribute"] == "shape" else "refined")
        x["by"] = covered
        x["note"] = {"overridden": "you said to carry Look's own " + covered.replace("trouser_", "") + ", which differs",
                     "confirmed": "you said the shape matters", "refined": "you named the colours, so the exact colours replace this direction"}[x["status"]]
    return L["inferred"]


def journey_choose(s, lid):
    with LOCK:
        jr = s["journey"]
        if jr and jr["win"].get("fin"):
            if jr["win"]["fin"] == lid:
                return session_view(s)
            raise Conflict("You already picked your winner.")
        if not jr or jr["step"] != "final" or lid not in (jr["win"].get("r2"), "P2"):
            raise ValueError("choose one of your final outfits")
        comp = full(s, jr["looks"][lid]["look"])
        s["versions"] = [{"versionId": "ver_0", "label": "Original", "composition": comp, "compositionId": composition_id(comp), "previewJobId": jr["jobs"][lid]}]
        s["cur"] = 0; jr["win"]["fin"] = lid; jr["step"] = "look"
        _event(s, "choose_finalist", look=lid); _save(s)
    return session_view(s)


def journey_view(s):
    jr = s["journey"]
    if not jr:
        return None
    looks = {}
    for lid, l in jr["looks"].items():
        comp = shown(full(s, l["look"]), jr["jobs"][lid])
        looks[lid] = {"items": [ui_item(s, comp[sl]) for sl in slots_of(comp)], "tag": l["tag"], "structure": R.structure_of(comp),
                      "compositionId": composition_id(full(s, l["look"])), "job": job_view(jr["jobs"][lid])}
    an = jr["analysis"]
    L = jr.get("learning") or {}
    learning = {"observed": [{k: o[k] for k in ("round", "chosen", "rejected")} | {"differences": [f"{d['slot']} {d['attribute']}" for d in o["differences"]]} for o in L.get("observed", [])],
                "inferred": [{"text": R.signal_text(x), "slot": x["slot"], "attribute": x["attribute"], "confidence": x["confidence"], "weight": x["net"],
                              "status": x["status"], "rounds": sorted({b["round"] for b in x["basis"] if b["weight"] > 0}), "note": x.get("note")} for x in L.get("inferred", [])],
                "stated": L.get("stated", [])}
    return {"step": jr["step"], "brief": jr["brief"], "palette": jr.get("palette"), "paletteName": jr.get("paletteName"), "looks": looks, "learning": learning,
            "analysis": {**an, "message": ANALYSIS_COPY.get(an.get("reason"))}, "win": jr["win"], "pins": jr["pins"], "carry": jr.get("carry"),
            "reasons": _reasons(s, jr["win"]["r2"]) if jr["win"].get("r2") else None,
            "round1": jr.get("round1", ["A", "B"]), "challenger": jr.get("challenger", "C")}


# ---------------------------------------------------------------- change requests on the selected look
SLOT_WORD = {"top": "top", "dress": "dress", "bottom": "trousers", "shoes": "shoes", "necklace": "necklace"}


def scope_sentence(changed, comp=None):
    present = slots_of(comp) if comp else ["top", "bottom", "shoes", "necklace"]
    kept = [("your " if comp and comp[x].get("owned") else "") + SLOT_WORD[x] for x in present if x not in changed]
    ch = [SLOT_WORD[x] for x in changed]
    return f"Keeping {', '.join(kept[:-1]) + ' and ' + kept[-1] if len(kept) > 1 else kept[0]}. Only the {' and '.join(ch)} change{'s' if len(ch) == 1 and ch[0] not in ('trousers', 'shoes') else ''}."


def _cur(s):
    return s["versions"][s["cur"]]


def _ctx_for_plan(s, comp):
    c = {}
    for sl in slots_of(comp):
        it = comp[sl]
        c[sl] = {"owned": True, "label": _top_title(s["owned"]).lower(), "attributes": {}} if it.get("owned") else \
            {"id": it["id"], "attributes": R.attrs_of(it), "hex": it.get("hex"), "label": SLOT_WORD[sl]}
    return c


def _candidate(s, rev, look, kind, label, why):
    base = _cur(s)["composition"]
    comp = {sl: (base[sl] if sl in base and (base[sl].get("owned") or base[sl].get("id") == v.get("id")) else dict(v))      # unchanged slots keep the exact same record
            for sl, v in look.items() if isinstance(v, dict)}
    comp = validate_comp(comp)
    gone = [sl for sl in slots_of(base) if sl not in comp]
    changed = [sl for sl in slots_of(comp) if sl not in base or comp[sl].get("id") != base[sl].get("id")]
    notes = []
    for sl in changed:
        z = s["sizes"].get(sl); sizes, un = sizes_of(comp[sl])
        if z and sizes and (z not in sizes or z in un):
            notes.append(f"Your size {z} isn't in stock in this one (in stock: {', '.join(x for x in sizes if x not in un) or 'none'}).")
    names = {"top": "Top", "dress": "Dress", "bottom": "Trousers", "shoes": "Shoes", "necklace": "Necklace"}
    def desc(it, sl):          # catalog facts only: colour name, cut when known (never a fit claim)
        cut = R.shape_word(it) if sl in ("bottom", "dress") and R.shape_of(it) else ""
        return " ".join(x for x in ((it.get("pictured_color") or "").lower(), cut) if x)
    kept = [("your " if base[sl].get("owned") else "") + SLOT_WORD[sl] for sl in slots_of(comp) if sl not in changed]
    delta = round(subtotal(comp) - subtotal(base), 2)
    swap = (f"{' and '.join(names[x] for x in gone)} replaced: " if gone else "")
    summary = swap + " ".join(f"{names[sl]}: " + (f"{desc(base[sl], sl)} → " if sl in base else "") + f"{desc(comp[sl], sl)} ({comp[sl]['title']}, ${comp[sl]['price']})." for sl in changed) + \
        ((lambda k: f" {k[0].upper() + k[1:]} unchanged.")(', '.join(kept[:-1]) + ' and ' + kept[-1] if len(kept) > 1 else kept[0]) if kept else "") + \
        (" Same price." if not delta else f" ${abs(delta):.2f} {'less' if delta < 0 else 'more'}.")
    cand = {"candidateId": f"cnd_{len(rev['candidates']) + 1}", "kind": kind, "label": label, "why": why, "sizeNote": " ".join(notes), "changeSummary": summary.strip(),
            "composition": comp, "compositionId": composition_id(comp), "changedSlots": changed, "structure": R.structure_of(comp)}
    rev["candidates"].append(cand)
    return cand


def _resolve(s, rev):
    base = _cur(s)["composition"]; pid = s.get("palette")
    _engine_brief(s)
    pl = CR.plan(rev["raw"], _ctx_for_plan(s, base), R.available_values(pid, IDX, set()), rev.get("released", []))
    rev.update(plan_status=pl["status"], notes=pl.get("notes", []), clarification=pl.get("clarification"), message=pl.get("message"),
               suggestion=pl.get("suggestion"), requested=pl.get("requested", []))
    if pl["status"] == "wish":
        return _resolve_wish(s, rev, base, pid, pl["wish"])
    if pl["status"] != "ready":
        rev["status"] = {"needs_clarification": "needs_clarification", "cannot": "cannot", "unsupported": "unsupported"}[pl["status"]]
        if pl["status"] == "unsupported":
            rev["message"] = "I couldn't turn that into a change I can look for. Name the piece and what you'd like, like \"darker shoes\", \"a longer dress\" or \"more fun shoes\"."
        return
    look = dict(base)
    target = pl.get("structure")
    owned_top = _owned_item(s) if s.get("useOwned") and "top" not in rev.get("released", []) else None
    changed_only = lambda r: [it for x in (r, r.get("runnerUp"), r.get("alternative")) if x for sl, it in (x.get("look") or {}).items() if (base.get(sl) or {}).get("id") != it.get("id")]
    r = _sellable(lambda idx: R.revise_structure(look, target, pl["requested"], pid, idx, set(), owned_top=owned_top) if target else
                  R.revise_attrs(look, pl["requested"], pid, idx, set()), changed_only)
    words = " and ".join(AT.describe(p) for p in pl["requested"])
    if r["status"] == "match":
        words = words or ("A dress" if target == "dress" else "Separates")
        # A new comparison: the best match and, when the catalog has one, the next-best different match. Never padded: one
        # eligible replacement is shown alone and said so. Each option gets its own picture; keeping one reuses that job.
        for n, opt in enumerate([r] + ([r["runnerUp"]] if r.get("runnerUp") else []), 1):
            out = (opt.get("palette") or {}).get("outsideSuggested") or []
            c = _candidate(s, rev, opt["look"], "match", words[0].upper() + words[1:], "Matches what you asked." + (" Outside your suggested palette." if out else ""))
            c["option"] = n; c["palette"] = opt.get("palette")
            c["previewJobId"] = start_preview(s, c["composition"], label=f"option {n} " + c["label"])
        changed = [SLOT_WORD[sl] for sl in r["changed"]]
        rev["onlyOption"] = None if r.get("runnerUp") else \
            f"There's one option: no other {' and '.join(changed)} in the catalog match what you asked while keeping your budget and other choices."
        rev["acknowledgement"] = "Got it: " + " and ".join(dict.fromkeys([*(["a dress instead"] if target == "dress" else ["separates instead"] if target else []), *(_echo(p) for p in pl["requested"])])) + "."
        rev["status"] = "open"
    else:
        rev["status"] = "no_match"
        rev["noMatch"] = {"message": r["message"], "unmet": r["unmet"], "alternative": None}
        if r.get("alternative"):
            a = r["alternative"]; c = _candidate(s, rev, a["look"], "alternative", "Alternative", a["label"])
            rev["noMatch"]["alternative"] = {"label": a["label"], "unmet": a["unmet"], "candidateId": c["candidateId"]}
    rev["scopeSentence"] = scope_sentence([sl for sl in SLOTS if sl in {p["slot"] for p in pl["requested"]}], base) if not target else \
        f"Keeping your shoes and necklace. The {'top and trousers become a dress' if target == 'dress' else 'dress becomes a top and trousers'}."


def _echo(p):
    """The shopper's words for one condition, or its validated description when the model returned a whole sentence."""
    w = (p.get("sourcePhrase") or "").strip().rstrip(".!?,;").lower()
    return w if w and len(w) <= 30 and not re.search(r"[.!?]", w) else AT.describe(p)


WISH_PICK = {"name": "choose_products", "description": "Choose the products that best match the shopper's wish.",
             "schema": {"type": "object", "additionalProperties": False, "required": ["choices"], "properties": {
                 "choices": {"type": "array", "maxItems": 2, "items": {"type": "object", "required": ["id", "why"], "properties": {
                     "id": {"type": "string"}, "why": {"type": "string", "description": "a few words from the product facts"}}}},
                 "none_fit": {"type": "boolean", "description": "true when no candidate really fits the wish"}}}}


def _wish_pick(look, wish, pid, idx):
    """An open wish: the engine's shortlist for that piece (every hard rule already applied), then Nova chooses up to two
    that fit her words. -> [(item, why)]; [] when nothing fits or Nova isn't available."""
    pool = R.shortlist(look, wish["slot"], pid, idx)
    if not pool or NOVA is None or CFG.get("nlu") != "nova":
        return []
    cur = look[wish["slot"]]
    lines = "\n".join(f"{x['id']} | {x['title']} | {x.get('pictured_color') or ''} | ${x['price']}" + (f" | heel {x['heel_height']}" if x.get("heel_height") else "")
                      for x in pool)
    try:
        out, _ = NOVA.call("You help a stylist choose catalog products. Choose only from the candidates; judge from the product names and colours. "
                           "If none really fits the wish, say none_fit.",
                           [{"text": f"Her wish for the {CR.SLOT_NAME[wish['slot']]}: \"{wish['words']}\". Current: {cur.get('title')} ({cur.get('pictured_color') or ''}).\n"
                                     f"Candidates (id | name | colour | price), all within her budget and rules:\n{lines}"}], WISH_PICK, 300)
    except Exception:
        return []
    by = {x["id"]: x for x in pool}
    return [] if not isinstance(out, dict) else [(by[c["id"]], str(c.get("why") or "")[:120]) for c in (out.get("choices") or [])[:2]
                                                if isinstance(c, dict) and c.get("id") in by]


def _resolve_wish(s, rev, base, pid, wish):
    picks = _sellable(lambda idx: _wish_pick(base, wish, pid, idx), lambda ps: [it for it, _ in ps])
    piece = CR.SLOT_NAME[wish["slot"]]
    if not picks:
        rev["status"] = "no_match"
        rev["noMatch"] = {"message": f"I couldn't find {piece} that feel {wish['words']} within your budget and other choices.", "unmet": [wish["words"]], "alternative": None}
        return
    label = f"{wish['words'][0].upper() + wish['words'][1:]} {piece}"
    for n, (it, why) in enumerate(picks, 1):
        look = dict(base); look[wish["slot"]] = {k: it.get(k) for k in KEEP}
        c = _candidate(s, rev, look, "match", label, why or "Chosen for your wish.")
        c["option"] = n; c["previewJobId"] = start_preview(s, c["composition"], label=f"option {n} {label}")
    rev["onlyOption"] = None if len(picks) > 1 else f"There's one option that fits \"{wish['words']}\" within your budget and other choices."
    rev["acknowledgement"] = f"Got it: {wish['words']} {piece}."
    rev["scopeSentence"] = scope_sentence([wish["slot"]], base)
    rev["requested"] = []
    rev["status"] = "open"


def revision_view(s, rev):
    return {k: rev.get(k) for k in ("revisionId", "requestText", "status", "interpreter", "problems", "notes", "clarification", "message",
                                     "suggestion", "noMatch", "scopeSentence", "acknowledgement", "onlyOption")} | \
        {"requested": [AT.describe(p) for p in rev.get("requested", [])],
         "candidates": [{"candidateId": c["candidateId"], "kind": c["kind"], "label": c["label"], "option": c.get("option"), "why": c["why"], "sizeNote": c.get("sizeNote", ""), "changeSummary": c.get("changeSummary", ""),
                         "compositionId": c["compositionId"], "changedSlots": c["changedSlots"], "subtotal": subtotal(cc := shown(c["composition"], c.get("previewJobId"), ctx=s.get("ctx"))),
                         "priceDelta": round(subtotal(cc) - subtotal(shown(_cur(s)["composition"], _cur(s)["previewJobId"])), 2), "palette": c.get("palette"),
                         "structure": c.get("structure"), "items": [ui_item(s, cc[sl]) for sl in slots_of(cc)],
                         "previewJob": job_view(c.get("previewJobId")) if c.get("previewJobId") else None} for c in rev["candidates"]]}


def request_change(s, text, version_id=None):
    _require_available()
    if not s["versions"]:
        raise ValueError("choose a look first")
    t0 = time.time()
    raw, meta = CR.interpret(text, NOVA)
    with LOCK:
        if version_id:
            s["cur"] = next(i for i, v in enumerate(s["versions"]) if v["versionId"] == version_id)
        rid = f"rev_{len(s['revisions']) + 1:02d}"
        rev = {"revisionId": rid, "requestText": text, "raw": raw, "candidates": [], "baseVersionId": _cur(s)["versionId"], **meta}
        _resolve(s, rev)
        s["revisions"][rid] = rev; s["activeRevision"] = rid
        _event(s, "request", text=text, interpreter=meta["interpreter"], status=rev["status"], s=round(time.time() - t0, 2)); _save(s)
    return revision_view(s, rev)


def dismiss(s, rid):
    with LOCK:
        r = s["revisions"][rid]
        if r["status"] != "accepted":
            r["status"] = "dismissed"
        if s.get("activeRevision") == rid:
            s["activeRevision"] = None
        _save(s)
    return session_view(s)


def answer(s, rid, choice):
    rev = s["revisions"][rid]; c = rev.get("clarification")
    if not c or choice not in c["options"]:
        raise ValueError("not an offered answer")
    new = CR.answer(c, choice, rev["raw"])
    with LOCK:
        if new.get("status") == "size_change":
            comp = _cur(s)["composition"]; it = comp.get(new.get("slot") if new.get("slot") in comp else R.main_slot(comp)); sizes, un = sizes_of(it); mine = chosen_size(s, it)
            word = "smaller" if new["direction"] == "smaller" else "bigger"
            cand = ([z for z in sizes[:sizes.index(mine)] if z not in un][::-1] if word == "smaller" else [z for z in sizes[sizes.index(mine) + 1:] if z not in un]) if mine in sizes else []
            size_msg = (
                f"Same {SLOT_WORD[it['slot']]}, a {word} size: {cand[0]} is in stock in {it.get('pictured_color', '').lower()}. Pick it in The pieces" + ("." if new.get("requested") else "; nothing else changes.") if cand else
                f"There isn't a {word} size of this {SLOT_WORD[it['slot']]} in stock in {it.get('pictured_color', '').lower()}" + (f" than your {mine}." if mine else ". Choose your size in The pieces first.") +
                (" Ask for a slimmer cut instead?" if mine else ""))
            if new.get("requested"):                    # e.g. "darker and tighter" -> a smaller size: the darker part is still looked for
                rev["raw"] = {"status": "supported", "requested": new["requested"]}
                rev["candidates"], rev["noMatch"], rev["clarification"], rev["onlyOption"] = [], None, None, None
                _resolve(s, rev)
                rev["notes"] = [size_msg] + (rev.get("notes") or [])
            else:
                rev.update(status="size_change", clarification=None, message=size_msg)
        elif "released" in new and not new.get("released"):
            rev.update(status="kept", clarification=None, message="Kept your top. Nothing changed.")
        else:
            rev["released"] = new.get("released", rev.get("released", []))
            rev["raw"] = {k: v for k, v in new.items() if k not in ("released", "kept")} if new.get("requested") is not None else rev["raw"]
            rev["candidates"], rev["noMatch"], rev["onlyOption"] = [], None, None
            _resolve(s, rev)
        _event(s, "answer", revision=rid, choice=choice, status=rev["status"]); _save(s)
    return revision_view(s, rev)


def accept(s, rid, cid):
    """A new version from a candidate and a preview job bound to its composition. Repeats return the same version."""
    _require_available()
    with LOCK:
        rev = s["revisions"][rid]
        if rev["status"] == "accepted":
            if rev.get("acceptedCandidate") == cid:
                return session_view(s)
            raise Conflict("You already kept an option from this request.")
        c = next(x for x in rev["candidates"] if x["candidateId"] == cid)
        if rev["baseVersionId"] != _cur(s)["versionId"]:
            raise Conflict("This option was made for a different version; ask again.")
        label = c["label"] if c["kind"] == "match" else "Alternative"
        v = {"versionId": f"ver_{len(s['versions'])}", "label": label, "composition": c["composition"], "compositionId": c["compositionId"],
             "createdFrom": {"revisionId": rid, "candidateId": cid, "kind": c["kind"], "request": rev["requestText"]}}
        v["previewJobId"] = c.get("previewJobId") or start_preview(s, c["composition"], label=label)     # the picture she compared
        for sl in list(s["sizes"]):                     # sizes carry over by label only when the piece has it in stock; none for slots that left
            if sl not in c["composition"] or (sl in c["changedSlots"] and not chosen_size(s, c["composition"][sl])):
                s["sizes"].pop(sl)
        s["versions"].append(v); s["cur"] = len(s["versions"]) - 1
        rev["status"] = "accepted"; rev["acceptedCandidate"] = cid; s["activeRevision"] = None
        _event(s, "accept", revision=rid, candidate=cid, compositionId=c["compositionId"]); _save(s)
    return session_view(s)


def choose_version(s, index):
    with LOCK:
        if not 0 <= index < len(s["versions"]):
            raise ValueError("no such version")
        s["cur"] = index; _save(s)
    return session_view(s)


def set_size(s, slot, size):
    """The shopper's size for a slot of the current version (must exist and be in stock in this colour)."""
    with LOCK:
        if not s["versions"]:
            raise ValueError("choose a look first")
        it = _cur(s)["composition"].get(slot)
        if not it or it.get("owned"):
            raise ValueError("no size for this piece")
        sizes, un = sizes_of(it)
        if size not in sizes:
            raise ValueError(f"size must be one of {', '.join(sizes)}")
        if size in un:
            raise Conflict(f"Size {size} is sold out in this color.")
        s["sizes"][slot] = size
        _event(s, "size", slot=slot, size=size); _save(s)
    return {"sizes": s["sizes"]}


# ---------------------------------------------------------------- preview jobs
def _stage_plan(comp):
    """The stages a composition's picture goes through, in order (what 'step n of N' counts)."""
    if "dress" in comp: return ["dress", "shoes", "necklace"]
    return ["owned_top" if comp["top"].get("owned") else "top", "bottom", "shoes", "necklace"]


def _tracked(run_task):
    """Per-stage evidence (cache hit, provider seconds, units) and the job's current stage, for the truthful waiting copy."""
    def wrapped(step_key, endpoint, body_fn, src_path, ref_path, units, out_name):
        j = getattr(_TL, "job", None)
        stage = next((v for k, v in STAGE_OF.items() if out_name.endswith(k)), None)
        if j is not None and stage:
            with LOCK:
                plan = _stage_plan(j["composition"]); j["stage"] = stage; j["stageIndex"] = plan.index(stage) + 1 if stage in plan else None
        before = (R.jl("stages.json", {}).get(step_key) or {})
        t0 = time.time(); row = {"stage": step_key, "slot": stage, "endpoint": endpoint.rsplit("/", 1)[-1], "startedAt": round(t0, 3)}
        try:
            out, st = run_task(step_key, endpoint, body_fn, src_path, ref_path, units, out_name)
        except Exception as e:
            row.update(error=str(e)[:200], wall_s=round(time.time() - t0, 2))
            if j is not None: j.setdefault("stageTimings", []).append(row)
            raise
        hit = before.get("status") == "success"
        row.update(cache_hit=hit, resumed=bool(before.get("task_id")) and not hit, units=0 if hit else units, wall_s=round(time.time() - t0, 2),
                   provider_s=None if hit else st.get("elapsed_s"), task_id=None if hit else st.get("task_id"), slot_wait_s=st.get("slot_wait_s", 0))
        if j is not None: j.setdefault("stageTimings", []).append(row)
        return out, st
    wrapped.__wrapped__ = run_task
    return wrapped


def _formality(s):
    """The brief's dress level for the tuck rule: smart, dressy, or None (no level, e.g. relaxed)."""
    c = next((c for c in (s.get("brief") or {}).get("constraints", []) if c.get("attribute") == "formality"), None)
    return None if not c else "dressy" if "smart" in c.get("values", []) else "smart"


def start_preview(s, comp, label=""):
    """One job per (provider, photo, owned top, composition). A repeat attaches to it (whatever its status); a failed job
    is only run again by an explicit retry."""
    cid, ctx = composition_id(comp), s["ctx"]
    owned = comp.get("top", {}).get("owned")
    tk = R.tuck_decision(comp["top"], comp["bottom"], _formality(s)) if "top" in comp and "bottom" in comp else None
    # identity: provider, shopper photo, own top, products, and the product images' last known fingerprints + render version
    jid = "prv_" + hashlib.sha256(f"{CFG['provider']}|{ctx['sid']}|{ctx.get('ownedId') if owned else ''}|{cid}|{R.ref_ids(comp)}|{tk and tk['tuck']}".encode()).hexdigest()[:12]
    j = S["jobs"].get(jid)
    if j:
        if s["sessionId"] not in j["sessions"]:
            j["sessions"].append(s["sessionId"])
        if j["status"] != "running" and time.time() - j.get("finishedAt", 0) > R.REF_TTL:       # re-check its product images in the background;
            threading.Thread(target=_quiet, args=(lambda: [R.product_ref(it) for it in comp.values()    # a changed one gives the next request a new job
                                                           if isinstance(it, dict) and not it.get("owned") and it.get("tryon_image")],), daemon=True).start()
        _event(s, "preview_attached", job=jid, label=label, status=j["status"], units=0)
        return jid
    S["jobs"][jid] = {"jobId": jid, "compositionId": cid, "composition": comp, "provider": "stub (simulated)" if CFG["provider"] == "stub" else "youcam",
                      "status": "running", "stage": "queued", "stageIndex": 0, "startedAt": time.time(), "label": label,
                      "sessions": [s["sessionId"]], "ctx": ctx, "tuck": tk}
    _event(s, "preview_started", job=jid, label=label)
    threading.Thread(target=_render, args=(jid,), daemon=True).start()
    return jid


def _render(jid):
    j = S["jobs"][jid]; ctx, comp = j["ctx"], validate_comp(j["composition"])
    look = {sl: (comp[sl] if comp[sl].get("owned") else {k: comp[sl].get(k) for k in KEEP}) for sl in comp}
    _TL.job = j
    try:
        tuck = (j.get("tuck") or {}).get("tuck", "tucked"); own = comp.get("top", {}).get("owned")
        owned_out = _ensure_top(ctx) if own and tuck == "tucked" else None          # the shared own-top layer is the tucked base
        steps = R.render_look(ctx["sid"], Path(ctx["photo"]), look, _face(ctx), owned_out, necklace_ok=_necklace_ok(ctx, owned_out, owned=bool(owned_out)),
                              tuck=tuck, owned={"ref": Path(ctx["ownedRef"]), "id": ctx["ownedId"]} if own else None)
        neck = steps[-1]
        tuck_note = next((x["tuckNote"] for x in steps if x.get("tuckNote")), None)
        with LOCK:
            j.update(steps=steps, output=neck["output"], finishedAt=time.time(), stage=None, error=None,
                     status="partial" if neck.get("status") == "not_shown" else "success",
                     note=" ".join(x for x in (tuck_note, "The necklace isn't in this picture: " + neck["reason"] if neck.get("status") == "not_shown" else None) if x) or None)
            for sid in j["sessions"]:
                s = S["sessions"].get(sid)
                if s:
                    _event(s, "preview_done", job=jid, label=j.get("label"), s=round(j["finishedAt"] - j["startedAt"], 1), status=j["status"],
                           cache_hits=sum(1 for r in j.get("stageTimings", []) if r.get("cache_hit")),
                           submissions=sum(1 for r in j.get("stageTimings", []) if r.get("cache_hit") is False)); _save(s)
            _save_jobs()
    except Exception as e:
        with LOCK:
            j.update(status="failed", error=str(e)[:300], finishedAt=time.time())
            _save_jobs()
    finally:
        _TL.job = None


def _save_jobs():
    tmp = CFG["jobs_file"].with_suffix(".tmp"); tmp.write_text(json.dumps(S["jobs"], indent=1)); tmp.replace(CFG["jobs_file"])


def _job_stages(j):
    """Stages of this job that did not succeed (latest attempt per stage)."""
    last = {}
    for r in j.get("stageTimings", []):
        last[r["stage"]] = r
    return [k for k, r in last.items() if "error" in r] + [st["stage"] for st in j.get("steps", []) if st.get("status") == "not_shown" and st.get("stage") and st["stage"] not in last]


def _retry_state(j):
    """Offered only when a retry can change the outcome (a necklace the QA gate rejected would come back the same)."""
    plans = [R.retry_plan(k) for k in _job_stages(j)]
    if not plans:
        return {"allowed": j["status"] == "failed", "needsAck": False}    # e.g. stopped by the spending cap before any submission
    return {"allowed": any(p in ("resume", "resubmit", "needs_ack") for p in plans) and "exhausted" not in plans, "needsAck": "needs_ack" in plans}


def retry(s, jid, acknowledge=False):
    """Explicit retry of a failed (or necklace-less) picture: resumes kept task ids; resubmits only stages the provider
    reported as failed; a stage with an unknown outcome needs the shopper's acknowledgement first."""
    _require_available()
    j = S["jobs"].get(jid)
    if not j or s["sessionId"] not in j["sessions"]:
        raise KeyError("no such picture")
    with LOCK:
        if j["status"] == "running":
            return job_view(jid)
        if j["status"] == "success":
            return job_view(jid)
        stages = _job_stages(j); plans = {k: R.retry_plan(k) for k in stages}
        if "exhausted" in plans.values():
            raise Conflict("This picture has been tried as many times as allowed.")
        if "needs_ack" in plans.values() and not acknowledge:
            raise Conflict("needs_ack")
        for k in stages:
            R.reset_stage(k, acknowledge_duplicate=acknowledge)
        j.update(status="running", stage="queued", stageIndex=0, error=None, note=None, startedAt=time.time(), finishedAt=None)
        _event(s, "retry", job=jid, plans=plans); _save(s)
    threading.Thread(target=_render, args=(jid,), daemon=True).start()
    return job_view(jid)


def _resume_jobs():
    """After a restart: running jobs continue. Finished stages are reused, kept task ids are polled again, and a stage that
    was interrupted mid-submit becomes an unknown outcome (engine), never a new submission."""
    for jid, j in S["jobs"].items():
        if j.get("status") == "running":
            threading.Thread(target=_render, args=(jid,), daemon=True).start()
    for s in S["sessions"].values():
        p = s.get("photo")
        if p and "checking" in p["checks"].values():               # a photo check was interrupted: run it again (cached results are reused)
            _start_photo_checks(s, "p" + p["sha256"][:16], CFG["media"] / s["sessionId"] / p["file"])
        jr = s.get("journey")
        if jr and jr["analysis"]["status"] == "running":       # colors were being read: read again (analysis results are cached by photo)
            threading.Thread(target=_read_colors, args=(s,), daemon=True).start()


def job_for(s, jid):
    j = S["jobs"].get(jid)
    return job_view(jid) if j and s["sessionId"] in j["sessions"] else None


def media_path(s, name):
    """Only picture files of jobs this session uses."""
    if not any(name == j.get("output") and s["sessionId"] in j["sessions"] for j in S["jobs"].values()):
        return None
    p = (R.OUT / name).resolve()
    return p if p.parent == R.OUT.resolve() and p.is_file() else None


# ---------------------------------------------------------------- merchant handoff
_MERCHANT = {}


def merchant(handle):
    hit = _MERCHANT.get(handle)
    if not hit or time.time() - hit[1] > 600:            # stock and prices cached 10 minutes (ESOD §8.4)
        req = urllib.request.Request(f"https://livostyle.com/products/{handle}.js", headers={"User-Agent": "Mozilla/5.0 calliope"})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=20))
        except urllib.error.HTTPError:
            raise                                          # the store answered (e.g. 404: no longer sold); retrying won't change it
        except (urllib.error.URLError, OSError):            # one retry: a passing network or store hiccup shouldn't fail the bag
            time.sleep(1); data = json.load(urllib.request.urlopen(req, timeout=20))
        _MERCHANT[handle] = (data, time.time())
    return _MERCHANT[handle]


_UNSELLABLE, SELL_TTL = {}, 600          # product id -> when the live store said there's nothing of it to buy


def _buyable_now(it):
    """False when Livostyle's live store has nothing of this product to sell: the page is gone, it's another product, or no
    variant in the pictured colour is available. Network trouble never excludes a product (the bag checks again)."""
    try:
        p, _ = merchant(it["handle"])
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return True
    if str(p.get("id")) != str(it["id"]):
        return False
    vs = p.get("variants") or []
    want = (it.get("pictured_color") or "").strip()
    in_colour = [v for v in vs if want and variant_for({"options": p.get("options", []), "variants": [v]}, want, None)]
    return any(v.get("available") for v in (in_colour or vs))


def _idx():
    """The active catalog without products the live store couldn't sell in the last SELL_TTL seconds."""
    t = time.time()
    return [i for i in IDX if t - _UNSELLABLE.get(i["id"], 0) >= SELL_TTL]


def _sellable(pick, items_of):
    """Select (pick(index)), check the chosen products against the live store, and select again without any it can't sell
    (up to 5 rounds), so nothing unbuyable is ever offered or pictured."""
    import concurrent.futures as cf
    for _ in range(5):
        res = pick(_idx())
        its = list({it["id"]: it for it in items_of(res) if isinstance(it, dict) and it.get("id") and not it.get("owned")}.values())
        with cf.ThreadPoolExecutor(8) as ex:
            bad = [it["id"] for it, ok in zip(its, ex.map(_buyable_now, its)) if not ok]
        if not bad:
            return res
        for b in bad:
            _UNSELLABLE[b] = time.time()
    return res


def _look_items(*looks):
    return [it for l in looks if isinstance(l, dict) for it in l.values()]


def variant_for(p, colour, size):
    """The merchant variant in exactly the pictured colour and the chosen size. Options are compared whole, so "Blue"
    never picks "Light Blue" and "S" never picks "XS"."""
    names = [n.lower() for n in p.get("options", [])] if isinstance(p.get("options"), list) and p["options"] and isinstance(p["options"][0], str) \
        else [str(o.get("name", "")).lower() for o in p.get("options", [])]
    def opts(v):
        vals = [x for x in (v.get("option1"), v.get("option2"), v.get("option3")) if x is not None] or [x.strip() for x in v["title"].split(" / ")]
        return {(names[i] if i < len(names) else str(i)): str(x).strip().lower() for i, x in enumerate(vals)}
    want_c, want_s = (colour or "").strip().lower(), (size or "").strip().lower()
    for v in p["variants"]:
        o = opts(v); vals = set(o.values())
        c_ok = not want_c or o.get("color", o.get("colour")) == want_c or (not ({"color", "colour"} & set(o)) and want_c in vals)
        s_ok = not want_s or o.get("size") == want_s or ("size" not in o and want_s in vals)
        if c_ok and s_ok:
            return v
    return None


CURRENCY = "USD"            # Livostyle's storefront and the dataset price in US dollars (price_usd); there is no other currency


def _quote(version_id, comp_id, items):
    """Fingerprint of exactly what a confirmation accepts: version, products, variants (colour + size), prices, currency.
    Any change to the selection or to a live price gives a different quote, so an earlier confirmation no longer applies."""
    rows = [(o["slot"], o["productId"], o["variantId"], o["size"], round(o["price"] * 100)) for o in items]
    return "pq_" + hashlib.sha256(json.dumps([version_id, comp_id, rows, CURRENCY]).encode()).hexdigest()[:20]


def handoff(s, version_id, accept_quote=None, raise_budget_to=None):
    """Exact variants for the version's catalog items in the shopper's chosen sizes, checked live; plus the cart link.

    Prices: each variant's live price is compared with the price Calliope showed. Any change (up or down) is returned in
    priceChanges with both subtotals, and no cart link is made until the shopper accepts that exact quote (accept_quote).
    If the new subtotal is over her budget, the cart also waits for a revision or an explicit budget raise
    (raise_budget_to, at least the new subtotal), which becomes her budget. Merchandise only: tax and shipping are set at
    checkout, and Livostyle may still change a price after the cart opens."""
    v = next(x for x in s["versions"] if x["versionId"] == version_id)
    comp = shown(v["composition"], v["previewJobId"])
    out, problems, changes = [], [], []
    for sl in buyable(comp):                            # every purchasable piece shown in this picture; owned pieces are never sent
        it = comp[sl]
        if it["id"] not in BY_ID:
            problems.append(f"{sl}: no longer in the Livostyle catalog"); continue
        sizes, _ = sizes_of(it)
        size = chosen_size(s, it) if sizes else ""
        if sizes and not size:
            problems.append(f"{sl}: choose a size"); continue
        try:
            p, at = merchant(it["handle"])
        except urllib.error.HTTPError as e:
            if e.code != 404: raise
            problems.append(f"{sl}: no longer sold at Livostyle"); continue
        if str(p["id"]) != it["id"]:
            problems.append(f"{sl}: merchant product {p['id']} != {it['id']}"); continue
        pick = variant_for(p, it.get("pictured_color"), size)
        if not pick:
            problems.append(f"{sl}: no {it.get('pictured_color')} variant in size {size}"); continue
        if not isinstance(pick.get("price"), (int, float)) or pick["price"] <= 0:
            problems.append(f"{sl}: Livostyle didn't give a current price for this piece"); continue
        now_ = round(pick["price"] / 100, 2)
        out.append({"slot": sl, "productId": it["id"], "handle": it["handle"], "variantId": pick["id"], "variantTitle": pick["title"],
                    "available": pick["available"], "price": now_, "shownPrice": it["price"], "size": size,
                    "url": f"https://livostyle.com/products/{it['handle']}?variant={pick['id']}", "checkedAt": at})
        if abs(now_ - it["price"]) >= 0.005:
            changes.append({"slot": sl, "productId": it["id"], "title": it["title"], "variantTitle": pick["title"], "was": it["price"], "now": now_,
                            "delta": round(now_ - it["price"], 2)})
    unavailable = [o["slot"] for o in out if not o["available"]]
    job_ = S["jobs"].get(v["previewJobId"], {})
    agree = job_.get("compositionId") == v["compositionId"] and not problems and [o["productId"] for o in out] == [comp[sl]["id"] for sl in buyable(comp)]
    ready = bool(out) and not problems and not unavailable
    quote = _quote(version_id, v["compositionId"], out) if ready else None
    shown_total, now_total = subtotal(comp), round(sum(o["price"] for o in out), 2)
    b = s.get("brief") or {}
    limit = None if b.get("budgetNew") is None else b["budgetNew"] + (10 if b.get("budgetMode") == "approx10" else 0)
    conflict = {"budget": b["budgetNew"], "mode": b.get("budgetMode"), "limit": limit, "subtotal": now_total, "over": round(now_total - limit, 2)} \
        if ready and limit is not None and now_total > limit + 1e-9 else None
    expired = bool(accept_quote) and ready and accept_quote != quote and bool(changes)   # a price or the selection changed since she confirmed
    accepted = ready and accept_quote == quote
    with LOCK:
        if conflict and accepted and raise_budget_to is not None:
            if float(raise_budget_to) + 1e-9 < now_total:
                raise ValueError("raise_budget_below_subtotal")
            old = b["budgetNew"]; b["budgetNew"], b["budgetMode"] = int(-(-float(raise_budget_to) // 1)), "firm"
            _event(s, "budget_raised", version=version_id, quote=quote, was=old, now=b["budgetNew"], subtotal=now_total)
            conflict = None
        needs = bool(changes) and not accepted
        if accepted and changes:
            _event(s, "prices_confirmed", version=version_id, quote=quote, changes=[(c["slot"], c["was"], c["now"]) for c in changes])
        cart = ("https://livostyle.com/cart/" + ",".join(f"{o['variantId']}:1" for o in out) + "?storefront=true") if ready and not needs and not conflict else None
        _event(s, "handoff", version=version_id, items=[(o["productId"], o["variantId"], o["available"], o["price"]) for o in out], problems=problems,
               priceChanges=len(changes), cart=bool(cart)); _save(s)
    return {"versionId": version_id, "compositionId": v["compositionId"], "previewJobId": v["previewJobId"],
            "previewCompositionId": job_.get("compositionId"), "items": out, "problems": problems, "unavailable": unavailable,
            "currency": CURRENCY, "priceChanges": changes, "subtotalShown": shown_total, "subtotalNow": now_total if ready else None,
            "quote": quote, "needsConfirmation": needs, "confirmationExpired": expired, "budgetConflict": conflict,
            "cartUrl": cart, "ownedNotSent": [sl for sl, i in v["composition"].items() if i.get("owned")], "agree": agree}


# ---------------------------------------------------------------- shares (read-only snapshots)
SLOT_LABEL = {"top": "Top", "dress": "Dress", "bottom": "Trousers", "shoes": "Shoes", "necklace": "Necklace"}


def _shares_of(s):
    out = []
    for shr in s["shares"]:
        p = CFG["shares"] / f"{shr}.json"
        if p.exists():
            out.append(json.loads(p.read_text()))
    return out


def _share_live(sh):
    return not sh.get("revokedAt") and time.time() < sh["expiresAt"]


def share_owner_view(sh):
    return {"snapshotId": sh["snapshotId"], "versionId": sh["versionId"], "versionLabel": sh["versionLabel"], "createdAt": sh["createdAt"],
            "expiresAt": sh["expiresAt"], "revokedAt": sh.get("revokedAt"), "publicUrl": f"/s/{sh['snapshotId']}", "active": _share_live(sh)}


def create_share(s, version_id):
    """Freeze one version (its own ready picture, pieces and prices) into a snapshot with an unguessable id.
    Sharing the same version again returns the active snapshot."""
    with LOCK:
        v = next((x for x in s["versions"] if x["versionId"] == version_id), None)
        if not v:
            raise ValueError("no such version")
        j = S["jobs"].get(v["previewJobId"]) or {}
        if j.get("status") not in ("success", "partial") or j.get("compositionId") != v["compositionId"]:
            raise Conflict("preview_not_ready")
        for sh in _shares_of(s):
            if sh["versionId"] == version_id and _share_live(sh):
                return share_owner_view(sh)
        shr = "shr_" + base64.b32encode(secrets.token_bytes(20)).decode().lower()[:26]
        im = Image.open(R.OUT / j["output"]).convert("RGB"); im.thumbnail((1600, 1600), Image.LANCZOS)
        im.save(CFG["shares"] / f"{shr}.jpg", "JPEG", quality=88)
        comp, now = shown(v["composition"], v["previewJobId"]), time.time()
        items = []
        for sl in slots_of(comp):
            it = comp[sl]; ui = ui_item(s, it)
            items.append({"slot": sl, "slotLabel": SLOT_LABEL[sl], "label": (ui["variant"] + " " + ui["name"].lower()).strip(), "color": ui["color"],
                          "priceText": "Owned" if it.get("owned") else f"${it['price']}", "where": "already owned by the sender" if it.get("owned") else "Livostyle"})
        sh = {"snapshotId": shr, "sessionId": s["sessionId"], "versionId": version_id, "versionLabel": v["label"], "occasion": s["brief"]["occasion"],
              "items": items, "subtotal": subtotal(comp), "pricesAsOf": now, "createdAt": now, "expiresAt": now + SHARE_DAYS * 86400, "revokedAt": None,
              "necklaceShown": j["status"] == "success", "simulated": CFG["provider"] == "stub"}
        (CFG["shares"] / f"{shr}.json").write_text(json.dumps(sh, indent=1))
        s["shares"].append(shr)
        _event(s, "share", snapshot=shr, version=version_id); _save(s)
    return share_owner_view(sh)


def revoke_share(s, shr):
    with LOCK:
        if shr not in s["shares"]:
            raise KeyError("no such share")
        p = CFG["shares"] / f"{shr}.json"; sh = json.loads(p.read_text())
        sh["revokedAt"] = sh.get("revokedAt") or time.time()
        p.write_text(json.dumps(sh, indent=1)); (CFG["shares"] / f"{shr}.jpg").unlink(missing_ok=True)
        _event(s, "share_revoked", snapshot=shr); _save(s)
    return share_owner_view(sh)


def public_share(shr):
    """What a friend sees: no session, photo, sizes or variant ids. (status, body)"""
    if not re.fullmatch(r"shr_[a-z2-7]{26}", shr or ""):
        return 404, {"error": "not found"}
    p = CFG["shares"] / f"{shr}.json"
    if not p.exists():
        return 404, {"error": "not found"}
    sh = json.loads(p.read_text())
    if sh.get("revokedAt"):
        return 410, {"code": "share_revoked", "message": "The sender stopped sharing this look."}
    if time.time() >= sh["expiresAt"]:
        (CFG["shares"] / f"{shr}.jpg").unlink(missing_ok=True)
        return 410, {"code": "share_expired", "message": "This shared look has expired."}
    return 200, {"snapshotId": shr, "label": sh["versionLabel"], "occasion": sh["occasion"], "createdAt": sh["createdAt"], "expiresAt": sh["expiresAt"],
                 "items": sh["items"], "subtotal": sh["subtotal"], "imageUrl": f"/api/shares/{shr}/picture",
                 "note": None if sh.get("necklaceShown", True) else "The necklace isn't shown in this picture.", "simulated": bool(sh.get("simulated"))}


def share_picture(shr):
    code, _ = public_share(shr)
    p = CFG["shares"] / f"{shr}.jpg"
    return p if code == 200 and p.exists() else None


# ---------------------------------------------------------------- self-check
if __name__ == "__main__":
    import tempfile
    from PIL import ImageDraw
    tmp = Path(tempfile.mkdtemp())
    configure(tmp / "app", provider="stub", stub_delay=0, engine_dir=tmp / "engine")
    def img(color, size=(700, 1200)):
        im = Image.new("RGB", size, color); ImageDraw.Draw(im).ellipse([300, 80, 400, 200], fill=(200, 160, 130)); b = io.BytesIO(); im.save(b, "JPEG"); return b.getvalue()
    def wait(s, jid):
        for _ in range(600):
            if S["jobs"][jid]["status"] != "running": return job_view(jid)
            time.sleep(0.05)
    def run(color, owned=True, structure="either", keep="A"):
        tok, v = new_session(); s = owner(tok)
        try: upload_photo(s, img(color), consent=False); raise AssertionError("consent")
        except ValueError: pass
        assert upload_photo(s, img(color), consent=True)["ok"]
        if owned:
            assert upload_owned_top(s, img((40, 120, 60), (600, 600)), "my green silk top")["owned"]["colorName"] == "green"
            confirm_owned_top(s, "blouse", "green")
        try: journey_start(s, {"occasion": "Book club", "heels": True}); raise AssertionError("budget")
        except ValueError as e: assert str(e) == "budget_required"
        v = journey_start(s, {"occasion": "Book club", "budget": 0, "heels": True, "structure": structure})
        assert v["notUsed"] and v["brief"]["budgetNew"] is None, v["brief"]
        assert journey_start(s, {"occasion": "x", "budget": 5})["brief"]["occasion"] == "Book club"     # repeat: no-op
        for _ in range(200):
            if s["journey"]["analysis"]["status"] != "running": break
            time.sleep(0.05)
        journey_palette(s, s["journey"]["palette"]["name"])
        for lid in "AB": wait(s, s["journey"]["jobs"][lid])
        journey_keep(s, 1, keep); journey_keep(s, 1, keep)
        try: journey_keep(s, 1, "B" if keep == "A" else "A"); raise AssertionError("conflict")
        except Conflict: pass
        wait(s, s["journey"]["jobs"]["C"]); journey_keep(s, 2, keep)
        journey_finalize(s, ["shape"]); wait(s, s["journey"]["jobs"]["P2"]); journey_choose(s, keep)
        return tok, s
    def ids(comp): return {sl: (it.get("ownedGarmentId") or it["id"]) for sl, it in comp.items()}
    t1, s1 = run((200, 200, 200)); t2, s2 = run((90, 90, 120))
    assert owner(t1) is s1 and owner(t2) is s2 and owner("nope") is None and s1["sessionId"] != s2["sessionId"]
    assert all(S["jobs"][j]["sessions"] == [s2["sessionId"]] for j in s2["journey"]["jobs"].values()), "different photos never share jobs"
    jid = _cur(s1)["previewJobId"]
    assert job_for(s2, jid) is None and media_path(s2, S["jobs"][jid]["output"]) is None, "no cross-session access"
    r = request_change(s1, "darker trousers with a narrower leg")
    assert r["status"] in ("open", "needs_clarification", "no_match") or (r["status"] == "cannot" and r["message"]), r   # cannot: e.g. already the slimmest leg, said so
    sh = create_share(s1, "ver_0"); code, pub = public_share(sh["snapshotId"])
    assert code == 200 and "sessionId" not in json.dumps(pub) and create_share(s1, "ver_0")["snapshotId"] == sh["snapshotId"]
    revoke_share(s1, sh["snapshotId"]); assert public_share(sh["snapshotId"])[0] == 410
    # no owned garment: either -> round 1 shows separates (catalog top) and a dress
    t3, s3 = run((150, 140, 130), owned=False, keep="A")
    jr3 = s3["journey"]; st3 = {lid: R.structure_of(l["look"]) for lid, l in jr3["looks"].items()}
    assert st3["A"] == "separates" and st3["B"] == "dress" and "top" in jr3["looks"]["A"]["look"], st3
    c3 = _cur(s3)["composition"]; assert set(c3) == {"top", "bottom", "shoes", "necklace"} and not c3["top"].get("owned")
    r = request_change(s3, "longer sleeves")
    assert r["status"] in ("open", "cannot", "needs_clarification", "no_match"), r
    if r["status"] == "open":
        before = ids(c3); accept(s3, r["revisionId"], r["candidates"][0]["candidateId"]); after = ids(_cur(s3)["composition"])
        assert {k for k in before if before[k] != after[k]} == {"top"}, (before, after)
    # switch composition: separates -> dress keeps shoes and necklace; handoff lists exactly the dress, shoes and necklace
    r = request_change(s3, "Can I see a dress instead?")
    assert r["status"] == "open", r
    accept(s3, r["revisionId"], r["candidates"][0]["candidateId"]); cd = _cur(s3)["composition"]
    assert set(cd) == {"dress", "shoes", "necklace"} and cd["shoes"]["id"] == c3["shoes"]["id"] and cd["necklace"]["id"] == c3["necklace"]["id"]
    assert set(s3["sizes"]) <= set(cd), "no sizes left over from the separates"
    r = request_change(s3, "a longer dress")
    if r["status"] == "open":
        b4 = ids(cd); accept(s3, r["revisionId"], r["candidates"][0]["candidateId"]); af = ids(_cur(s3)["composition"])
        assert {k for k in b4 if b4[k] != af[k]} == {"dress"}, (b4, af)
    j = wait(s3, _cur(s3)["previewJobId"])
    assert [st["slot"] for st in j["steps"]] == ["dress", "shoes", "necklace"] and j["steps"][0]["parent"].startswith("photo-"), j["steps"]
    # dress only, no owned garment
    t4, s4 = run((120, 130, 150), owned=False, structure="dress", keep="B")
    assert all(R.structure_of(l["look"]) == "dress" for l in s4["journey"]["looks"].values())
    # an owned top + a dress request asks before releasing it
    r = request_change(s1, "show me a dress instead")
    assert r["status"] == "needs_clarification" and r["clarification"]["kind"] == "release_owned", r
    r = answer(s1, r["revisionId"], r["clarification"]["options"][0]); assert r["status"] in ("open", "no_match"), r
    print("shopper self-check ok |", s1["sessionId"], s2["sessionId"], "| request:", r["status"])
