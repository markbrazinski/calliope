"""Calliope engine: colour analysis -> palette -> look selection -> try-on chain, over the pinned catalog in data/.

Moved from the 2026-10-02 spike runner (artifacts/spikes/2026-10-02-cc/runner/calliope_run.py, kept there as the research
record). Only what the application uses is here. Nothing is specific to a person: every per-shopper value comes from the
shopper's photo or from the brief the application sets in DATA["brief"] (under BRIEF_LOCK) before calling selection code.
Provider rules: ledger row before every POST, submit at most once per stage, never resubmit without an explicit retry.
"""
import hashlib, heapq, io, json, os, re, threading, time, urllib.request, urllib.error
from pathlib import Path
from PIL import Image

import palette as P                                  # OKLab maths
import seasons                                       # 12-season interpretation
import attributes as AT                              # catalog attributes + request matching
from nova_common import Nova

ROOT = Path(__file__).resolve().parent
DATA = {k: json.loads((ROOT / "data" / f"{k}.json").read_text()) for k in ("brief", "constraints", "neutrals", "index_rules")}
INDEX, _ATTRS = [], {}                               # the active catalog (catalog_sync); use_catalog() swaps both at once


def use_catalog(doc):
    """Make a validated catalog document the one selection reads. Compositions already made keep their own copies."""
    global INDEX, _ATTRS
    INDEX, _ATTRS = doc["items"], doc["attributes"]


import catalog_sync as _C                            # noqa: E402  (local files only at import: no network)
_doc = _C.active() if _C.usable(_C.active()) else _C._read(_C.SNAPSHOT) if os.environ.get("CALLIOPE_CATALOG_SNAPSHOT", "1") != "0" else None
if _C.usable(_doc):
    use_catalog(_doc)
BRIEF_LOCK = threading.RLock()                       # DATA["brief"] is set per call by the application while it holds this lock
STATE = OUT = None                                   # set by use_dirs()
CAP_UNITS = 0                                        # set by the application from its authorized unit budget
NOVA_USD = 0.0


def use_dirs(state, out):
    global STATE, OUT
    STATE, OUT = Path(state), Path(out); STATE.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)


class _LazyNova:
    """Created on first use, so importing needs no AWS SDK or credentials."""
    def __getattr__(self, k):
        global nova
        nova = Nova(cap_usd=NOVA_USD, log_path=STATE / "nova_calls.jsonl")
        return getattr(nova, k)


nova = _LazyNova()
LOCK = threading.RLock()
_KEY_LOCKS, _KEY_LOCKS_LOCK = {}, threading.Lock()
BUDGET_FILE = os.environ.get("CALLIOPE_BUDGET_FILE")          # one budget shared by every process and namespace (paid experiments)
BUDGET_UNITS = int(os.environ.get("CALLIOPE_BUDGET_UNITS") or 0)
HOLDS = ("reserved", "submitted", "consumed", "uncertain")     # uncertain outcomes count; only a provider-reported error releases


def _budget(op, key, units=0, status=None):
    """Reserve or update units in the shared budget, atomically across processes (exclusive file lock)."""
    if not BUDGET_FILE:
        return
    import fcntl
    with open(BUDGET_FILE + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            p = Path(BUDGET_FILE); d = json.loads(p.read_text()) if p.exists() else {"cap": BUDGET_UNITS, "rows": []}
            if op == "reserve":
                used = sum(r["units"] for r in d["rows"] if r["status"] in HOLDS)
                if used + units > d["cap"]:
                    raise ProviderError(f"cap: experiment budget {used}+{units} > {d['cap']} units")
                d["rows"].append({"key": key, "units": units, "status": "reserved", "t": time.time()})
            else:
                row = next((r for r in reversed(d["rows"]) if r["key"] == key and r["status"] in ("reserved", "submitted")), None)
                if row: row["status"] = status
            tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(d, indent=1)); tmp.replace(p)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _key_lock(step_key):
    """One submission per stage across threads: a second caller waits, then reuses or resumes the first one's task."""
    with _KEY_LOCKS_LOCK:
        return _KEY_LOCKS.setdefault(step_key, threading.Lock())


BASE = "https://yce-api-01.makeupar.com"
UNITS = {"cloth": 2, "necklace": 1, "analysis": 20, "tuck": 1}
USD_PER_UNIT = 0.055
PAL_W = {"on_palette": 0, "free": 0, "neutral": 0.6, "off": 2.0}      # soft palette: off-palette stays eligible, ranked last
STORY_PENALTY = 1.5                                                 # soft outfit rule: a look without an on-palette story piece ranks lower
SIZE_PENALTY = 1.0                                                  # brief.rank_saved_sizes: a piece without a saved size in stock ranks lower (never a filter)
REASON_WORD = {"colors": "the colors", "trouser_shape": "the trouser shape", "overall_feel": "the overall feel"}


def jl(name, default):
    with LOCK:
        p = STATE / name
        return json.loads(p.read_text()) if p.exists() else default


def js(name, obj):
    with LOCK:
        tmp = STATE / (name + f".{threading.get_ident()}.tmp"); tmp.write_text(json.dumps(obj, indent=1)); tmp.replace(STATE / name)


class ProviderError(Exception):
    pass


def yc(method, path, body=None, timeout=30):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ.get("YOUCAM_API_KEY", ""), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        with open(STATE / "http_errors.jsonl", "a") as f:     # evidence for throttling (429) and provider errors
            f.write(json.dumps({"t": time.time(), "code": e.code, "method": method, "path": path.split("?")[0]}) + "\n")
        raise ProviderError(f"HTTP {e.code} on {path}: {e.read()[:300].decode(errors='replace')}")


def balance():
    """Bookkeeping only: a failed read must never fail a task."""
    try:
        return sum(float(r["amount_dec"]) for r in yc("GET", "/s2s/v1.0/client/credit")["results"])
    except Exception:
        return None


def check_image(data):                               # same limits as server.check_image
    im = Image.open(io.BytesIO(data)); w, h = im.size
    assert im.format in ("JPEG", "PNG") and len(data) <= 10 * 1024 * 1024 and max(w, h) <= 4096 and max(w, h) >= 512 and min(w, h) >= 384, (im.format, w, h)


def upload(path):
    data = path.read_bytes(); h = hashlib.sha256(data).hexdigest(); files = jl("files.json", {})
    if h in files and time.time() - files[h]["t"] < 20 * 3600:
        return files[h]["file_id"]
    check_image(data)
    ct = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpg"
    fi = yc("POST", "/s2s/v2.0/file", {"files": [{"content_type": ct, "file_name": "input." + ct[6:], "file_size": len(data)}]})["data"]["files"][0]
    for q in fi["requests"]:
        urllib.request.urlopen(urllib.request.Request(q["url"], data=data, method=q.get("method", "PUT"), headers=q.get("headers", {})), timeout=60).read()
    _upd("files.json", h, lambda d, k: d.__setitem__(k, {"file_id": fi["file_id"], "t": time.time()}), {})
    return fi["file_id"]


def units_committed():
    return sum(r["units_expected"] for r in jl("ledger.json", []) if r["consumption"] in ("reserved", "submitted", "consumed", "uncertain"))


def _upd(name, key, fn, default):
    """Atomic read-modify-write of one entry in a state file."""
    with LOCK:
        d = jl(name, default); v = fn(d, key); js(name, d); return v


PROVIDER_SLOTS = threading.BoundedSemaphore(int(os.environ.get("CALLIOPE_PROVIDER_CONCURRENCY") or 6))   # across all sessions


def run_task(step_key, endpoint, body_fn, src_path, ref_path, units, out_name):
    """Submit once (ledger before POST), poll, download. Re-running reuses a finished or submitted stage; never resubmits.
    Holding the stage's key lock for the whole call means two looks that share a stage make one submission. At most
    PROVIDER_SLOTS provider tasks are in flight at once; the time spent waiting for a slot is reported as slot_wait_s."""
    with _key_lock(step_key):
        s = jl("stages.json", {}).get(step_key) or {}
        if s.get("status") == "success" and (OUT / s["output"]).exists():
            return OUT / s["output"], s
        t0 = time.time()
        with PROVIDER_SLOTS:
            waited = round(time.time() - t0, 2)
            out, st = _run_task(step_key, endpoint, body_fn, src_path, ref_path, units, out_name)
            return out, dict(st, slot_wait_s=waited)


def _run_task(step_key, endpoint, body_fn, src_path, ref_path, units, out_name):
    with LOCK:
        s = dict(jl("stages.json", {}).get(step_key) or {})
        if s and s.get("status") == "success" and (OUT / s["output"]).exists():
            return OUT / s["output"], s
        if s.get("status") == "submitting" and not s.get("task_id"):
            # We hold this stage's lock, so no live thread is submitting it: an earlier process stopped mid-submit.
            # The provider may or may not have the task. Unknown outcome, never permission to submit again.
            s.update(status="submit_unknown", error="interrupted during submission; the provider may have the task")
            _upd("stages.json", step_key, lambda d, k: d.__setitem__(k, s), {})
            def unk(L, k):
                for r in L:
                    if r["step"] == step_key and r["consumption"] == "reserved": r.update(consumption="uncertain", error=s["error"])
            _upd("ledger.json", None, unk, [])
            _budget("set", f"{STATE}|{step_key}", status="uncertain")
        if s and s.get("status") in ("submit_unknown", "error"):
            raise ProviderError(f"stage {step_key} is {s['status']} ({s.get('error')}); explicit retry required")
        fresh = not s.get("task_id")
        if fresh:
            if units_committed() + units > CAP_UNITS:
                raise ProviderError(f"cap: {units_committed()}+{units} > {CAP_UNITS} units")
            _budget("reserve", f"{STATE}|{step_key}", units)
            ledger = jl("ledger.json", [])
            row_n = len(ledger) + 1
            ledger.append({"n": row_n, "step": step_key, "endpoint": endpoint, "units_expected": units, "consumption": "reserved",
                           "parent": Path(src_path).name, "ref": Path(ref_path).name if ref_path else None, "t": time.time(),
                           "retry": s.get("retries", 0)})
            js("ledger.json", ledger)
            s = {"status": "submitting", "endpoint": endpoint, "retries": s.get("retries", 0)}
            _upd("stages.json", step_key, lambda d, k: d.__setitem__(k, s), {})
    def set_row(**kw):
        def f(d, k):
            row = next(r for r in d if r["step"] == step_key and r["consumption"] in ("reserved", "submitted"))
            row.update(kw)
        _upd("ledger.json", None, f, [])
    if fresh:
        set_row(balance_before=balance())
        try:
            src_id = upload(src_path); ref_id = upload(ref_path) if ref_path else None
            s["task_id"] = yc("POST", endpoint, body_fn(src_id, ref_id))["data"]["task_id"]
            s.update(status="submitted", submitted_at=time.time())
            set_row(consumption="submitted", task_id=s["task_id"], submitted_at=s["submitted_at"]); _budget("set", f"{STATE}|{step_key}", status="submitted")
        except Exception as e:
            s.update(status="submit_unknown", error=str(e)[:300]); set_row(consumption="uncertain", error=str(e)[:200]); _budget("set", f"{STATE}|{step_key}", status="uncertain")
            raise
        finally:
            _upd("stages.json", step_key, lambda d, k: d.__setitem__(k, s), {})
    t0, fails = time.time(), 0
    while time.time() - t0 < 900:
        try:
            d = yc("GET", f"{endpoint}/{s['task_id']}")["data"]; fails = 0
        except Exception:
            fails += 1
            if fails >= 4: raise
            time.sleep(5); continue
        st = d.get("task_status")
        if st in ("success", "error"):
            def fin(L, k, kw):
                row = next(r for r in L if r.get("task_id") == s["task_id"]); row.update(kw)
            if st == "success":
                url = _result_url(d.get("results"))
                if url:
                    data = urllib.request.urlopen(url, timeout=60).read()
                    ext = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
                else:
                    data, ext = json.dumps(d.get("results"), indent=1).encode(), "json"
                name = f"{out_name}-{step_key}.{ext}"; (OUT / name).write_bytes(data)
                s.update(status="success", output=name, sha256=hashlib.sha256(data).hexdigest(), elapsed_s=round(time.time() - s["submitted_at"], 1),
                         finished_at=time.time(), results={k: v for k, v in (d.get("results") or {}).items() if k != "url"} if isinstance(d.get("results"), dict) else None)
                kw = {"consumption": "consumed", "elapsed_s": s["elapsed_s"], "finished_at": s["finished_at"]}; _budget("set", f"{STATE}|{step_key}", status="consumed")
            else:
                s.update(status="error", error=f"{d.get('error')}: {d.get('error_message') or ''}")
                kw = {"consumption": "released", "error": s["error"]}; _budget("set", f"{STATE}|{step_key}", status="released")
            kw["balance_after"] = balance()
            _upd("ledger.json", None, lambda L, k: fin(L, k, kw), [])
            _upd("stages.json", step_key, lambda dd, k: dd.__setitem__(k, s), {})
            if st == "error":
                raise ProviderError(s["error"])
            return OUT / s["output"], s
        time.sleep(5)
    raise ProviderError(f"stage {step_key} still running after 900 s; task kept, re-run resumes polling")


MAX_RETRIES = 2


def retry_plan(step_key):
    """What an explicit retry would do for one stage: 'none' (finished), 'resume' (task id kept: poll again, no new
    submission), 'resubmit' (provider said error: 0 units were consumed), 'needs_ack' (unknown outcome: a new submission
    may duplicate a charge), 'exhausted'."""
    s = jl("stages.json", {}).get(step_key) or {}
    st = s.get("status")
    if st == "success" or not s: return "none"
    if s.get("task_id") and st == "submitted": return "resume"
    if s.get("retries", 0) >= MAX_RETRIES: return "exhausted"
    if st == "error": return "resubmit"
    if st in ("submit_unknown", "submitting"): return "needs_ack"
    return "none"


def reset_stage(step_key, acknowledge_duplicate=False):
    """Explicit retry only. Clears a failed stage so the next run_task submits it again (counted against the cap)."""
    with _key_lock(step_key), LOCK:
        plan = retry_plan(step_key)
        if plan == "needs_ack" and not acknowledge_duplicate: return plan
        if plan in ("resubmit", "needs_ack"):
            s = jl("stages.json", {})[step_key]
            _upd("stages.json", step_key, lambda d, k: d.__setitem__(k, {"status": "retry", "retries": s.get("retries", 0) + 1,
                                                                        "previous": {x: s.get(x) for x in ("status", "error", "task_id")}}), {})
            return "reset"
        return plan


def _result_url(r):
    if isinstance(r, dict): return r.get("url")
    if isinstance(r, list) and r: return _result_url(r[0])


def skey(*parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def face_box(sid, photo):
    """Nova finds the head (0-1000 box convention); OpenCV Haar finds the exact face inside that region.
    If Haar finds nothing (e.g. sunglasses), use Nova's box shrunk to its centre and mark it 'coarse'. Cached."""
    try:
        import cv2                                   # optional refinement; without it the Nova box is used ("nova_coarse")
    except ImportError:
        cv2 = None
    faces = jl("faces.json", {})
    cached = faces.get(sid)
    if cached and (cached["source"] != "nova_coarse" or not cv2): return cached
    im = Image.open(photo).convert("RGB"); W, H = im.size
    if cached:
        nb = cached["nova_head_box"]                 # refine a coarse box once OpenCV is available, without asking Nova again
    else:
        small = im.copy(); small.thumbnail((900, 900)); buf = io.BytesIO(); small.save(buf, "JPEG", quality=88)
        tool = {"name": "detect", "description": "Detected objects", "schema": {"type": "object", "required": ["face"], "properties": {
            "face": {"type": "array", "items": {"type": "integer"}, "description": "[x1, y1, x2, y2] scaled 0-1000"}}}}
        out, _ = nova.call("Detect objects and return bounding boxes as [x1, y1, x2, y2] with coordinates scaled from 0 to 1000 relative to the image width and height.",
                           [{"image": {"format": "jpeg", "source": {"bytes": buf.getvalue()}}}, {"text": "Detect the face of the person (forehead to chin)."}], tool, 200)
        x1, y1, x2, y2 = out["face"]; nb = [x1 * W // 1000, y1 * H // 1000, x2 * W // 1000, y2 * H // 1000]
    assert 0 < nb[2] - nb[0] < W * 0.6 and 0 < nb[3] - nb[1] < H * 0.35, f"implausible head box {nb}"
    cx, cy, bw, bh = (nb[0] + nb[2]) / 2, (nb[1] + nb[3]) / 2, (nb[2] - nb[0]) * 1.6, (nb[3] - nb[1]) * 1.6
    roi = [int(max(0, cx - bw / 2)), int(max(0, cy - bh / 2)), int(min(W, cx + bw / 2)), int(min(H, cy + bh / 2))]
    fs = []
    if cv2:
      casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
      g = cv2.cvtColor(cv2.imread(str(photo)), cv2.COLOR_BGR2GRAY)[roi[1]:roi[3], roi[0]:roi[2]]
      fs = sorted([[int(a) for a in f] for f in casc.detectMultiScale(g, scaleFactor=1.05, minNeighbors=5, minSize=(int((roi[2] - roi[0]) * 0.2),) * 2)], key=lambda f: -f[2] * f[3])
    if fs:
        f = fs[0]; box, src = [roi[0] + f[0], roi[1] + f[1], roi[0] + f[0] + f[2], roi[1] + f[1] + f[3]], "haar_in_nova_roi"
    else:
        sw, sh = (nb[2] - nb[0]) * 0.3, (nb[3] - nb[1]) * 0.3
        box, src = [int(nb[0] + sw), int(nb[1] + sh * 0.6), int(nb[2] - sw), int(nb[3] - sh * 1.4)], "nova_coarse"
    rec = {"box": box, "image_size": [W, H], "source": src, "nova_head_box": nb}
    _upd("faces.json", sid, lambda d, k: d.__setitem__(k, rec), {})   # locked per-key update (parallel-safe)
    return rec


def face_crop(sid, photo, fb):
    """Square crop around the face (margin 2x face size). Upscaled with Lanczos (non-generative) when under 640 px."""
    x0, y0, x1, y1 = fb["box"]; cx, cy, s = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) * 2.0
    im = Image.open(photo).convert("RGB"); W, H = im.size
    box = [int(max(0, cx - s / 2)), int(max(0, cy - s / 2)), int(min(W, cx + s / 2)), int(min(H, cy + s / 2))]
    c = im.crop(box); note = "lossless crop"
    if min(c.size) < 640:
        f = 640 / min(c.size); c = c.resize((round(c.width * f), round(c.height * f)), Image.LANCZOS); note = f"crop upscaled x{f:.2f} (Lanczos, non-generative)"
    p = OUT / f"{sid}-face.jpg"; c.save(p, "JPEG", quality=95)
    return p, {"box": box, "transform": note}


def neck_box(fb, size):
    """Necklace detail crop from the face box: neck must be >= 15% of crop width (provider rule)."""
    x0, y0, x1, y1 = fb["box"]; fw = x1 - x0; W, H = size; cx = (x0 + x1) / 2
    cw = 3.4 * fw; ch = cw * 1.25; top = y0 + 0.25 * (y1 - y0)
    return [int(max(0, cx - cw / 2)), int(max(0, top)), int(min(W, cx + cw / 2)), int(min(H, top + ch))]


def analyze(sid, face_path):
    """YouCam colour analysis: strictness 'high', one retry at 'low' only for face-angle errors (existing rule)."""
    res = jl("analyses.json", {}); fsha = hashlib.sha256(face_path.read_bytes()).hexdigest()
    if sid in res and res[sid].get("face_sha256") != fsha: res.pop(sid)      # attempts on a different face crop don't count for this one
    if sid in res and res[sid]["status"] == "success": return res[sid]
    attempts = res.get(sid, {}).get("attempts", [])
    for level in ("high", "low"):
        if any(a["level"] == level for a in attempts): continue
        if attempts and not re.search(r"angle|forward", attempts[-1].get("error") or ""): break
        k = skey("analysis", sid, hashlib.sha256(face_path.read_bytes()).hexdigest(), level)
        try:
            _, st = run_task(k, "/s2s/v2.0/task/skin-tone-analysis",
                             lambda s_, r_: {"src_file_id": s_, "face_angle_strictness_level": level}, face_path, None, UNITS["analysis"], f"{sid}-analysis")
            attempts.append({"level": level, "status": "success"})
            res[sid] = {"status": "success", "results": st["results"], "settings": {"face_angle_strictness_level": level},
                        "input": face_path.name, "face_sha256": fsha, "attempts": attempts}
            js("analyses.json", res); return res[sid]
        except ProviderError as e:
            attempts.append({"level": level, "status": "error", "error": str(e)[:200]})
            res[sid] = {"status": "error", "error": str(e)[:200], "face_sha256": fsha, "attempts": attempts}; js("analyses.json", res)
    return res[sid]


def eyes_visible(sid, face_path):
    cache = jl("eyes.json", {})
    if sid not in cache:
        tool = {"name": "face_check", "description": "Visibility of facial features", "schema": {"type": "object", "required": ["eyes_visible", "sunglasses"],
                "properties": {"eyes_visible": {"type": "boolean"}, "sunglasses": {"type": "boolean"}, "hair_over_eyes": {"type": "boolean"}}}}
        b = io.BytesIO(); im = Image.open(face_path).convert("RGB"); im.thumbnail((640, 640)); im.save(b, "JPEG", quality=88)
        out, _ = nova.call("You check whether facial features are visible. Be literal.", [{"image": {"format": "jpeg", "source": {"bytes": b.getvalue()}}},
                           {"text": "Are both eyes clearly visible (not covered by sunglasses or hair)?"}], tool, 80)
        cache[sid] = out; js("eyes.json", cache)
    return cache[sid]


def garment(oid, path, categories):
    """What a top the shopper owns is, read from its photo (Nova): its kind, its main color in plain words, and how sure
    the reading is. Once per image (garments.json)."""
    cache = jl("garments.json", {})
    if oid not in cache or "length" not in cache[oid]:          # readings from before the fit facts are read again once
        tool = {"name": "garment", "description": "The garment in the photo", "schema": {"type": "object", "required": ["is_top", "category", "color", "confidence"],
                "properties": {"is_top": {"type": "boolean", "description": "the photo shows one garment worn on the upper body"},
                               "category": {"type": "string", "enum": list(categories) + ["other"]},
                               "color": {"type": "string", "description": "its main color in 1-3 plain words a shopper would use, e.g. royal blue, ivory, olive green"},
                               "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                               # how it would sit with trousers (data/tuck_rule.json facts)
                               "length": {"type": "string", "enum": ["cropped", "waist", "hip", "tunic", "unclear"]},
                               "volume": {"type": "string", "enum": ["fitted", "regular", "high", "unclear"]},
                               "fabric_weight": {"type": "string", "enum": ["light", "fluid", "medium", "heavy", "stiff", "unclear"]},
                               "hem": {"type": "string", "enum": ["straight", "curved", "shirttail", "unclear"]}}}}
        b = io.BytesIO(); im = Image.open(path).convert("RGB"); im.thumbnail((768, 768)); im.save(b, "JPEG", quality=88)
        out, _ = nova.call("You describe clothing photos for a styling app. Be literal. Use low confidence when the kind or color is unclear; "
                           "use 'unclear' for a property you can't see.",
                           [{"image": {"format": "jpeg", "source": {"bytes": b.getvalue()}}}, {"text": "What kind of top is this, its main color, its length, how full it is, its fabric weight and its hem?"}], tool, 160)
        cache[oid] = out; js("garments.json", cache)
    return cache[oid]


def palette_for(sid, a, eyes=None):
    if eyes and (not eyes.get("eyes_visible") or eyes.get("sunglasses")):
        a = dict(a, status="error", error="eyes not visible (sunglasses or covered); eye/brow contrast would be read from the occlusion")
    obs = seasons.observation_from_analysis(sid, a); it = seasons.interpret(obs)
    pid = it["candidates"][0]["season"] if it["candidates"] else None
    return {"interpretation": it, "palette_id": pid, "headline": seasons.headline(it), "explanation": seasons.explanation(it),
            "observed": (a.get("results") or {}).get("color")}


def color_class(hx, palette_id):
    if not hx: return "unknown", None, None
    L, C, h = P.hex_to_oklch(hx)
    for f in DATA["neutrals"]["families"]:
        if C <= f.get("C_max", 9) and f.get("L_min", 0) <= L <= f.get("L_max", 9) and \
                ("h_min" not in f or f["h_min"] <= h <= f["h_max"]):
            return "neutral", None, f["name"]
    if not palette_id: return "free", None, None
    pal = seasons.PALETTES[palette_id]["swatches"]
    d = min(P.oklab_distance(hx, s["hex"]) for s in pal if not {"explore", "neutral"} & set(s.get("tags", [])))
    return ("on_palette" if d <= DATA["neutrals"]["palette_fit_max"] else "off"), round(d, 3), None


def passes(item, constraints):
    """Tight, literal enforcement: the attribute must be known and satisfy every predicate."""
    for c in constraints:
        if item["slot"] not in c.get("slots", [item["slot"]]): continue
        v = item.get(DATA["constraints"]["attributes"][c["attribute"]]["index_field"])
        if v is None: return False, f"{c['label']}: {c['attribute']} unknown"
        if (c["op"] == "exclude" and v in c["values"]) or (c["op"] == "require" and v not in c["values"]):
            return False, f"{c['label']}: {c['attribute']}={v}"
    return True, None


# Colour intent (documented policy, deterministic; OKLCH from the measured catalog hex).
#   A colour is NEUTRAL when its chroma is under NEUTRAL_C (black, greys, white, oatmeal, stone, very dark brown).
#   "Darker" alone keeps the colour family:
#     neutral current piece  -> a darker true neutral (chroma under GREY_C: grey, charcoal, black), or a tinted neutral
#                               (chroma under NEUTRAL_C: taupe, dark brown) only when the current piece has a usable tint itself
#                               (chroma at least TINT_C) and the hue stays within NEUTRAL_HUE_TOLERANCE (warm stays warm).
#                               Below TINT_C a hue is unstable and is never compared. More colourful candidates are a change,
#                               and so is a piece the merchant names with a colour word ("Army Green", "Rose") however muted.
#     coloured current piece -> a darker coloured piece whose hue is within HUE_TOLERANCE degrees (light blue -> navy),
#                               never a neutral (black) or another hue (pink); those are colour changes
#   A named colour ("black trousers", "darker trousers in navy") replaces the family rule: the item must match that name,
#   by its merchant colour name or by the measured colour (COLOR_NAMES). If nothing in the family fits, revise_attrs offers
#   the colour change as a labelled alternative that the shopper has to accept; it is never shown as a match.
NEUTRAL_C, GREY_C, TINT_C, HUE_TOLERANCE, NEUTRAL_HUE_TOLERANCE = 0.05, 0.025, 0.015, 30.0, 30.0
COLOR_ALIASES = AT.V["computed_request_attributes"]["color"]["aliases"]
_in = lambda h, lo, hi: lo <= h <= hi if lo <= hi else (h >= lo or h <= hi)       # hue ranges may wrap through 0
COLOR_NAMES = {   # name -> test on (L, C, h); thresholds chosen from the catalog's measured colours
    "black": lambda L, C, h: C < 0.05 and L < 0.33, "white": lambda L, C, h: C < 0.04 and L > 0.9,
    "grey": lambda L, C, h: C < 0.025 and 0.33 <= L <= 0.9, "cream": lambda L, C, h: L > 0.85 and 0.015 <= C < 0.08 and _in(h, 60, 110),
    "beige": lambda L, C, h: 0.62 <= L <= 0.9 and 0.015 <= C < 0.09 and _in(h, 45, 105), "brown": lambda L, C, h: L < 0.62 and 0.025 <= C < 0.15 and _in(h, 25, 85),
    "navy": lambda L, C, h: L < 0.45 and C >= 0.04 and _in(h, 225, 290), "blue": lambda L, C, h: C >= 0.05 and _in(h, 215, 290),
    "purple": lambda L, C, h: C >= 0.05 and _in(h, 290, 335), "pink": lambda L, C, h: C >= 0.06 and L > 0.55 and _in(h, 335, 15),
    "red": lambda L, C, h: C >= 0.1 and _in(h, 10, 40), "burgundy": lambda L, C, h: L < 0.45 and C >= 0.06 and _in(h, 345, 40),
    "orange": lambda L, C, h: C >= 0.1 and _in(h, 40, 70), "yellow": lambda L, C, h: C >= 0.08 and _in(h, 70, 110),
    "olive": lambda L, C, h: 0.3 <= L <= 0.62 and 0.04 <= C < 0.13 and _in(h, 95, 135), "green": lambda L, C, h: C >= 0.04 and _in(h, 110, 200)}


CHROMATIC = set(COLOR_NAMES) - {"black", "white", "grey", "beige", "cream", "brown"}


def same_family(hx, base):
    """True when hx is in the colour family of base (the current piece), per the policy above."""
    L1, C1, h1 = P.hex_to_oklch(hx); L0, C0, h0 = P.hex_to_oklch(base)
    dh = abs((h1 - h0 + 180) % 360 - 180)
    if C0 < NEUTRAL_C:
        return C1 < GREY_C or (C1 < NEUTRAL_C and C0 >= TINT_C and dh <= NEUTRAL_HUE_TOLERANCE)
    if C1 < NEUTRAL_C:
        return False
    return dh <= HUE_TOLERANCE


def color_named(it, name):
    """The item is the named colour: its merchant colour name says so, or its measured colour fits the name. None if unknown."""
    words = {COLOR_ALIASES.get(w, w) for w in re.findall(r"[a-z]+", (it.get("pictured_color") or "").lower())}
    if name in words: return True
    if not it.get("hex"): return None
    return bool(COLOR_NAMES[name](*P.hex_to_oklch(it["hex"])))


def request_ok(it, p):
    """One requested condition on one item: True / False / None (unknown never counts as a match).
    lightness compares the measured catalog colours (OKLab L) of the item and the current piece and, unless the shopper
    named a colour or accepted another one (p["anyHue"]), keeps the colour family (same_family). color: color_named()."""
    if p["attribute"] == "lightness":
        base = (p.get("anchor") or {}).get("values") or [None]
        if not it.get("hex") or not base[0]: return None
        step = AT.V["computed_request_attributes"]["lightness"]["step"]
        d = P.hex_to_oklch(it["hex"])[0] - P.hex_to_oklch(base[0])[0]
        if not (d <= -step if p["op"] == "lt" else d >= step): return False
        if p.get("anyHue"): return True
        named = {COLOR_ALIASES.get(w, w) for w in re.findall(r"[a-z]+", (it.get("pictured_color") or "").lower())} & CHROMATIC
        return same_family(it["hex"], base[0]) and not (named and P.hex_to_oklch(base[0])[1] < NEUTRAL_C)
    if p["attribute"] == "color":
        return color_named(it, p["value"])
    return AT.satisfies(attrs_of(it), p)


def budget_limit(b):
    """Budget for new pieces: None means the shopper chose No limit (never a default)."""
    return float("inf") if b["budgetNew"] is None else b["budgetNew"] + (10 if b["budgetMode"] == "approx10" else 0)


def within(limit):
    return "" if limit == float("inf") else f" within ${limit:g}"


def palette_required():
    """Owner decision 2026-10-05: a suggested palette is a soft ranking preference everywhere, unless the shopper made it mandatory."""
    return bool(DATA["brief"].get("palette_required"))


def size_pen(it):
    return SIZE_PENALTY if DATA["brief"].get("rank_saved_sizes") and not has_saved_size(it) else 0


# Product images sent to YouCam. A stage key holds the fingerprint of the exact reference bytes (and RENDER_V), so a new
# image at the same URL, or a new URL with different bytes, makes new stages from that point down the chain; identical
# images keep their stages. Bump RENDER_CONFIG when the request or the reference preparation changes.
RENDER_CONFIG = "cloth-v4 change_shoes=false; ref jpeg q93 width=1080; necklace: neck crop, remove background, QA gate"
RENDER_V = hashlib.sha256(RENDER_CONFIG.encode()).hexdigest()[:8]
REF_TTL = 600                                       # seconds between re-validations of one image with the CDN (ETag / Last-Modified)
_REFS = {}


def _fetch_ref(url, meta):
    """Conditional GET -> (bytes, or None when the CDN says unchanged; etag; last-modified)."""
    h = {"User-Agent": "Mozilla/5.0 calliope-spike"}
    if meta.get("etag"): h["If-None-Match"] = meta["etag"]
    if meta.get("lastModified"): h["If-Modified-Since"] = meta["lastModified"]
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60)
        return r.read(), r.headers.get("ETag"), r.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304: return None, meta.get("etag"), meta.get("lastModified")
        raise


def ref_url(item):
    return item["tryon_image"] + ("&" if "?" in item["tryon_image"] else "?") + "width=1080"


def product_ref(item):
    """(path, fingerprint) of the product image as sent to YouCam: sha-256 of those exact JPEG bytes. Re-validated with the
    CDN at most every REF_TTL seconds. Files are named by fingerprint and never overwritten, so pictures already made keep
    the image they were made from."""
    url = ref_url(item); hit = _REFS.get(url)
    if hit and time.time() - hit[2] < REF_TTL and hit[0].exists(): return hit[0], hit[1]
    meta = jl("refs.json", {}).get(url) or {}
    if meta and not (OUT / meta.get("file", "-")).exists(): meta = {}
    data, etag, lm = _fetch_ref(url, meta)
    if data is None:
        p, fp = OUT / meta["file"], meta["fp"]
    else:
        b = io.BytesIO(); Image.open(io.BytesIO(data)).convert("RGB").save(b, "JPEG", quality=93); jpg = b.getvalue()
        fp = hashlib.sha256(jpg).hexdigest()[:16]; p = OUT / f"ref-{item['id']}-{fp}.jpg"
        if not p.exists(): p.write_bytes(jpg)
    _upd("refs.json", url, lambda d, k: d.__setitem__(k, {"file": p.name, "fp": fp, "etag": etag, "lastModified": lm, "productId": item["id"], "checkedAt": time.time()}), {})
    _REFS[url] = (p, fp, time.time())
    return p, fp


def ref_ids(look):
    """What a finished picture of this look depends on, without the network: each item's last known image fingerprint (else
    its URL) and RENDER_V. Part of the picture-job identity."""
    known = jl("refs.json", {})
    return RENDER_V + "|" + ",".join(f"{sl}:{(known.get(ref_url(it)) or {}).get('fp') or ref_url(it)}" for sl, it in sorted(look.items())
                                     if isinstance(it, dict) and not it.get("owned") and it.get("tryon_image"))


def _legacy_ref_ok(item, fp):
    """Stages made before references were fingerprinted were keyed by product ID only. One may be reused only when its saved
    reference file (ref-<id>.jpg, written by the same preparation) is byte-identical to today's reference."""
    old = OUT / f"ref-{item['id']}.jpg"
    return old.exists() and hashlib.sha256(old.read_bytes()).hexdigest()[:16] == fp


def _adopt(k, legacy_k):
    """Carry a successful legacy stage over to its fingerprinted key (same output file, marked adoptedFrom). -> reused?"""
    st = jl("stages.json", {})
    if (st.get(k) or {}).get("status") == "success": return True
    old = st.get(legacy_k) or {}
    if old.get("status") == "success" and old.get("output") and (OUT / old["output"]).exists():
        _upd("stages.json", k, lambda d, kk: d.__setitem__(kk, dict(old, adoptedFrom=legacy_k, adoptedAt=time.time())), {})
        return True
    return False


def _change_metrics(before, after, W=200, thr=60):
    """(share of pixels changed, share of changed pixels inside a solid 5x5 changed block). Necklaces are thin."""
    H = int(W * before.height / before.width); a, b = before.resize((W, H)), after.resize((W, H)); pa, pb = a.load(), b.load()
    m = [[sum(abs(u - v) for u, v in zip(pa[x, y], pb[x, y])) > thr for y in range(H)] for x in range(W)]
    ch = sum(map(sum, m)); inner = sum(1 for x in range(2, W - 2) for y in range(2, H - 2) if all(m[x + i][y + j] for i in range(-2, 3) for j in range(-2, 3)))
    return round(ch / (W * H), 3), round(inner / max(ch, 1), 3)


def attrs_of(item):
    """Normalized attributes for a catalog item (the active catalog), or an owned item's own attributes. A product that has
    left the catalog has none (unknown never matches)."""
    if item.get("owned"): return item.get("attributes", {})
    return (_ATTRS.get(item["id"]) or {}).get("attributes", {})


def shape_of(item):
    """Shape of the main garment: trousers = leg_cut; dress = silhouette, else length. None when unknown or unresolved."""
    for a in (("silhouette", "dress_length") if item.get("slot") == "dress" else ("leg_cut",)):
        v = AT.value(attrs_of(item), a)
        if v and len(v) == 1: return v[0]
    return None


def shape_word(item):
    return (shape_of(item) or "unlisted-shape").replace("_", "-").replace("a-line", "A-line")








def eligible(it, slot, palette_id, cons, excluded=(), palette_ok=True):
    """Selection eligibility shared by revise_attrs() and available_values(): stock, single item, constraints, solid, colour rules."""
    rule = DATA["brief"]["outfit_rule"]
    if it["slot"] != slot or not it["in_stock"] or it.get("price") is None or it.get("single_item") is False or it["id"] in excluded \
            or not passes(it, cons)[0] or it["pattern"] != "solid" or (slot in ("top", "dress") and not it.get("pictured_color")): return None
    cls, d, fam = color_class(it["hex"], palette_id)
    if cls == "unknown": return None
    if palette_ok and (cls == "off" or (cls == "neutral" and slot not in rule["neutral_ok_slots"])): return None
    return dict(it, cls=cls, dist=d, family=fam)


def available_values(palette_id, idx, excluded=()):
    """For clarifications: which attribute values actually exist among items this shopper could be shown."""
    cons = DATA["constraints"]["always"] + DATA["brief"]["constraints"]
    def f(slot, attr):
        return {v for it in idx if eligible(it, slot, palette_id, cons, excluded, palette_ok=bool(DATA["brief"].get("palette_required")))
                for v in (AT.value(attrs_of(it), attr) or [])}
    return f


def has_saved_size(it):
    """One of the shopper's saved sizes is in stock in the pictured colour (dataset stock; re-checked live at handoff)."""
    saved = [x.lower() for x in DATA["brief"].get("saved_sizes", {}).get(it["slot"], [])]
    if not saved: return True
    toks = lambda t: {x.strip().lower() for x in re.split(r"[/()\s]+", t or "") if x.strip()}
    want = (it.get("pictured_color") or "").lower()
    vs = [v for v in it.get("variants", []) if (v["options"].get("Color") or "").lower() == want] or it.get("variants", [])
    return any(v["in_stock"] and (set(saved) & toks(v["options"].get("Size")) or (v["options"].get("Size") or "").lower() in saved) for v in vs)   # "One Size" is one size


def revise_attrs(look, requested, palette_id, idx, excluded=()):
    """Attribute-level change request (change_request.plan 'ready' output). Only requested slots change; the rest stay
    byte-equal. Hard: constraints, budget (owned items never priced), outfit rule, colour eligibility as in outfits().
    Every preference must be met by a known value (unknown never counts). No match -> a clearly labelled alternative,
    returned separately and never as the result."""
    b = DATA["brief"]; rule = b["outfit_rule"]; cons = DATA["constraints"]["always"] + b["constraints"]
    limit = budget_limit(b)
    named = {p["slot"] for p in requested if p["attribute"] == "color"}           # a named colour replaces the family rule
    requested = [dict(p, anyHue=True) if p["attribute"] == "lightness" and p["slot"] in named else p for p in requested]
    required = bool(b.get("palette_required"))   # owner correction 2026-10-05: a suggested palette is soft unless the shopper made it mandatory
    slots = sorted({p["slot"] for p in requested})
    fixed = {k: v for k, v in look.items() if isinstance(v, dict) and k not in slots}
    others = sum(v["price"] for v in fixed.values() if not v.get("owned"))
    keep = ("id", "slot", "title", "handle", "price", "hex", "cls", "dist", "family", "pictured_color", "tryon_image", "url", "heel_height")

    def pool(slot, palette_ok):
        return [e for it in idx if it["id"] != (look.get(slot) or {}).get("id") and (e := eligible(it, slot, palette_id, cons, excluded, palette_ok))]

    def search(prefs, palette_ok=required, accept=lambda r: all(x is True for x in r)):
        per = {sl: [it for it in pool(sl, palette_ok) if accept([request_ok(it, p) for p in prefs if p["slot"] == sl])] for sl in slots}
        ranked = []
        for combo in __import__("itertools").product(*(per[sl] for sl in slots)):
            c = dict(zip(slots, combo)); tot = others + sum(x["price"] for x in combo)
            if tot > limit: continue
            full = {**fixed, **c}
            story = story_slots(structure_of(full), catalog_top=not (full.get("top") or {}).get("owned"))
            short = max(0, rule["min_on_palette"] - sum(1 for sl in story if full.get(sl, {}).get("cls") == "on_palette")) if palette_id else 0
            if palette_ok and short: continue                      # mandatory palette: the outfit rule is hard
            base = lambda sl: (look.get(sl) or {}).get("hex")
            key = (sum(not has_saved_size(x) for x in combo), short,     # her size in stock outranks the soft colour story; never a filter
                   sum({"on_palette": 0, "free": 0, "neutral": 1 if sl in rule["neutral_ok_slots"] else 1.5}.get(x["cls"], 2) for sl, x in c.items()),
                   round(sum(P.oklab_distance(x["hex"], base(sl)) if base(sl) else 0 for sl, x in c.items()), 3), tuple(x["id"] for x in combo))
            ranked.append((key, c, tot))
        if not ranked: return None
        def pack(r):
            new = {**{k: v for k, v in look.items() if isinstance(v, dict)}, **{sl: {f: x[f] for f in keep} for sl, x in r[1].items()}}
            outside = [sl for sl, x in r[1].items() if x["cls"] == "off" or (x["cls"] == "neutral" and sl not in rule["neutral_ok_slots"])]
            return {"look": new, "subtotal": round(r[2], 2), "changed": slots, "sizeMissing": [sl for sl, x in r[1].items() if not has_saved_size(x)],
                    "palette": {"mode": "required" if required else "soft", "outsideSuggested": outside}}
        top = heapq.nsmallest(2, ranked, key=lambda r: r[0])          # the best and, when one exists, the next eligible combination
        return {**pack(top[0]), "runnerUp": pack(top[1]) if len(top) > 1 else None}

    met = search(requested)
    words = " and ".join(AT.describe(p) for p in requested)
    if met: return {"status": "match", **met, "meets": [AT.describe(p) for p in requested], "requested": requested}
    res = {"status": "no_match", "unmet": [AT.describe(p) for p in requested],
           "message": f"I couldn't find {words} while keeping your budget and other choices." if not required else f"I couldn't find {words} while keeping your colors, budget and other choices.", "alternative": None}
    family = [p for p in requested if p["attribute"] == "lightness" and not p.get("anyHue")]
    if family:                                            # nothing darker in the same colour family: offer the colour change, labelled
        relaxed = [dict(p, anyHue=True) if p in family else p for p in requested]
        alt = search(relaxed, palette_ok=required)
        if alt:
            sl = family[0]["slot"]; was, now = (look.get(sl) or {}).get("pictured_color") or "the current colour", alt["look"][sl].get("pictured_color") or "another colour"
            res["message"] = f"I couldn't find {words} in the same colour family while keeping your budget and other choices."
            res["alternative"] = {**alt, "label": f"Alternative: {words}, but in {now.lower()} instead of {was.lower()} (a different colour)", "unmet": ["same colour family"]}
            return res
    if required:
        alt = search(requested, palette_ok=False)
        if alt:
            res["alternative"] = {**alt, "label": f"Alternative: {words}, but the color is outside the palette you asked for", "unmet": ["palette"]}; return res
    for p in requested if len(requested) > 1 else []:
        rest = [q for q in requested if q is not p]; alt = search(rest)
        if alt:
            res["alternative"] = {**alt, "label": f"Alternative: {' and '.join(AT.describe(q) for q in rest)}, but not {AT.describe(p)}", "unmet": [AT.describe(p)]}; return res
    alt = search(requested, accept=lambda r: all(x is not False for x in r) and any(x is None for x in r))
    if alt:
        miss = [p["attribute"].replace("_", " ") for p in requested]
        res["alternative"] = {**alt, "label": f"Alternative: the {' and '.join(miss)} isn't listed for this one, so I can't confirm {words}", "unmet": ["not listed"]}
    return res


# ---------------- outfit structures (owner decision 2026-10-07) ----------------
def shortlist(look, slot, palette_id, idx, excluded=(), k=30):
    """Products that could replace look[slot] under every hard rule: eligibility, the other pieces' prices within the
    budget, the shopper's constraints. Ranked by palette fit, then price. For open wishes ("more fun shoes"): Nova chooses
    from this list only, so whatever it picks is already allowed."""
    b = DATA["brief"]; cons = DATA["constraints"]["always"] + b["constraints"]; limit = budget_limit(b)
    others = sum(v["price"] for sl, v in look.items() if isinstance(v, dict) and sl != slot and not v.get("owned"))
    cur = (look.get(slot) or {}).get("id")
    pool = [e for it in idx if it["id"] != cur and (e := eligible(it, slot, palette_id, cons, excluded, bool(b.get("palette_required")))) and others + e["price"] <= limit]
    return sorted(pool, key=lambda e: (PAL_W.get(e["cls"], 0) + (e["dist"] or 0), e["price"], e["id"]))[:k]


STRUCTS = {"separates": ("top", "bottom", "shoes", "necklace"), "dress": ("dress", "shoes", "necklace")}
SLOT_WORD = {"top": "top", "bottom": "trousers", "dress": "dress", "shoes": "shoes", "necklace": "necklace"}


def structure_of(look):
    """'dress' or 'separates'. A dress never coexists with a top or bottom."""
    has_dress = isinstance(look.get("dress"), dict)
    assert not (has_dress and (isinstance(look.get("top"), dict) or isinstance(look.get("bottom"), dict))), "a dress can't be combined with separates"
    return "dress" if has_dress else "separates"


def main_slot(look):
    return "dress" if structure_of(look) == "dress" else "bottom"


def story_slots(st, catalog_top=False):
    """Soft colour story: at least one of these on-palette (owner direction 2026-10-02, extended to dresses and catalog tops)."""
    return ["dress", "shoes"] if st == "dress" else (["top", "bottom", "shoes"] if catalog_top else ["bottom", "shoes"])


def look_key(look):
    return tuple(look[s]["id"] for s in STRUCTS[structure_of(look)] if isinstance(look.get(s), dict) and not look[s].get("owned"))


def learned_from(look, look_id, keys):
    """Stated preferences only, grounded in the winner's actual catalog pieces. Round choices never reach here."""
    m, sh = look[main_slot(look)], look["shoes"]
    noun = SLOT_WORD[main_slot(look)]
    V = {"colors": (f"{m.get('pictured_color') or 'its'} {noun} and {sh.get('pictured_color') or 'its'} shoes", [m, sh]),
         "trouser_shape": (f"{shape_word(m)} {noun}", [m]),
         "overall_feel": (f"the overall feel of {look_id}", [x for x in (look.get(s) for s in STRUCTS[structure_of(look)]) if isinstance(x, dict) and not x.get("owned")])}
    return [{"attribute": k, "value": V[k][0], "sourceLookId": look_id, "origin": "stated",
             "grounding": [{"slot": i["slot"], "productId": i["id"], "colorName": i.get("pictured_color") or "", "shape": shape_of(i) or "unknown"} for i in V[k][1]]}
            for k in ("colors", "trouser_shape", "overall_feel") if k in keys]


# ---------------- learning from comparison choices (2026-10-13) ----------------
# Observed: which looks were shown and which was kept. Inferred: tentative signals from the catalog facts that differed.
# A whole-look choice is spread over everything that differed (weight 1/sqrt(differences)), so one pick never claims
# every attribute; only repeated or near-isolated evidence reaches "moderate", nothing is ever stronger. Signals rank
# below hard constraints, the budget, stated reasons and the palette, and the shopper's stated reasons confirm or override them.
LEARN_ATTRS = (("lightness", "the {piece} lightness"), ("color", "the {piece} colour"), ("shape", "the {piece} shape"),
               ("heel_height", "the heel"), ("neckline", "the neckline"), ("sleeve_length", "the sleeves"))


def look_facts(look):
    """Comparable catalog facts of a look (never the shopper's own pieces): {(slot, attribute): value}."""
    out = {("look", "structure"): structure_of(look)}
    m = look.get(main_slot(look))
    if isinstance(m, dict) and m.get("hex"):                       # the main garment, so a dress and trousers can be compared by colour
        out[("main", "lightness")] = round(P.hex_to_oklch(m["hex"])[0], 3); out[("main", "color")] = m["hex"]
    for sl in ("top", "bottom", "dress", "shoes"):
        it = look.get(sl)
        if not isinstance(it, dict) or it.get("owned"):
            continue
        if it.get("hex"):
            out[(sl, "lightness")] = round(P.hex_to_oklch(it["hex"])[0], 3); out[(sl, "color")] = it["hex"]
        if sl in ("bottom", "dress") and shape_of(it):
            out[(sl, "shape")] = shape_of(it)
        if sl == "shoes" and it.get("heel_height"):
            out[(sl, "heel_height")] = it["heel_height"]
        if sl == "top":
            for a in ("neckline", "sleeve_length"):
                v = AT.value(attrs_of(it), a)
                if v and len(v) == 1: out[(sl, a)] = v[0]
    return out


def compare_looks(chosen, rejected):
    """The catalog facts that differ between a kept and a passed-over look (both known; colours only when clearly different)."""
    a, b, step = look_facts(chosen), look_facts(rejected), AT.V["computed_request_attributes"]["lightness"]["step"]
    same_structure = a[("look", "structure")] == b[("look", "structure")]
    diffs = []
    for k in sorted(set(a) & set(b)):
        if k[0] == "main" and same_structure: continue              # same structure: compared per slot; a dress vs trousers: as the main garment
        x, y = a[k], b[k]
        if k[1] == "lightness" and abs(x - y) < step: continue
        if k[1] == "color" and P.oklab_distance(x, y) <= DATA["neutrals"]["palette_fit_max"] * 1.5: continue
        if k[1] not in ("lightness", "color") and x == y: continue
        diffs.append({"slot": k[0], "attribute": k[1], "chosen": x, "rejected": y})
    return diffs


def infer_signals(comparisons):
    """Tentative preferences from observed comparisons. Each: slot, attribute, prefers (a value, 'darker'/'lighter', or a
    colour), net weight (evidence for minus evidence against), confidence (weak|moderate), basis, status 'tentative'."""
    sig = {}
    def add(k, w, i, c, isolated, chosen, rejected, over):
        s = sig.setdefault(k, {"slot": k[0], "attribute": k[1], "prefers": k[2], "net": 0.0, "basis": [], "over": [], "ref": {"chosen": [], "rejected": []}})
        s["net"] = round(s["net"] + w, 3)
        s["basis"].append({"comparison": i, "round": c["round"], "weight": w, "isolated": isolated})
        if w > 0:
            s["over"].append(over); s["ref"]["chosen"].append(chosen); s["ref"]["rejected"].append(rejected)
    for i, c in enumerate(comparisons):
        d = c["differences"]
        w = round(1 / max(1, len(d)) ** 0.5 / max(1, c.get("pairs", 1)), 3)
        whole = any(x["attribute"] == "structure" for x in d)    # a dress vs separates differs in everything; never isolated evidence
        for x in d:
            iso = len(d) <= 2 and not whole
            if x["attribute"] == "lightness":
                darker = x["chosen"] < x["rejected"]
                add((x["slot"], "lightness", "darker"), w if darker else -w, i, c, iso, x["chosen"], x["rejected"], "lighter")
                add((x["slot"], "lightness", "lighter"), -w if darker else w, i, c, iso, x["chosen"], x["rejected"], "darker")
            elif x["attribute"] == "color":
                add((x["slot"], "color", x["chosen"]), w, i, c, iso, x["chosen"], x["rejected"], x["rejected"])
            else:
                add((x["slot"], x["attribute"], x["chosen"]), w, i, c, iso, x["chosen"], x["rejected"], x["rejected"])
                add((x["slot"], x["attribute"], x["rejected"]), -w, i, c, iso, x["chosen"], x["rejected"], x["chosen"])
    out = []
    for s in sig.values():
        if s["net"] <= 0 or not s["ref"]["chosen"]: continue
        rounds = {b["round"] for b in s["basis"] if b["weight"] > 0}
        s["confidence"] = "moderate" if (len(rounds) >= 2 and s["net"] >= 0.75) or (any(b["isolated"] and b["weight"] > 0 for b in s["basis"]) and s["net"] >= 0.7) else "weak"
        s["status"] = "tentative"
        out.append(s)
    return sorted(out, key=lambda s: -s["net"])


def agreement(look, signals):
    """How well a candidate look agrees with the signals still in force (tentative, or confirmed or refined by what the
    shopper said; never overridden): + net weight when it sides with what was kept, - when it sides with what was passed
    over, 0 when it can't be told."""
    f, total = look_facts(look), 0.0
    for s in signals:
        if s.get("status") == "overridden": continue
        v = f.get((s["slot"], "lightness" if s["attribute"] == "lightness" else s["attribute"]))
        if v is None: continue
        if s["attribute"] == "lightness":
            ch, rj = sum(s["ref"]["chosen"]) / len(s["ref"]["chosen"]), sum(s["ref"]["rejected"]) / len(s["ref"]["rejected"])
            sgn = 1 if abs(v - ch) < abs(v - rj) else -1
        elif s["attribute"] == "color":
            sgn = 1 if P.oklab_distance(v, s["prefers"]) < min(P.oklab_distance(v, r) for r in s["ref"]["rejected"]) else -1
        else:
            sgn = 1 if v == s["prefers"] else -1 if v in s["over"] else 0
        total += sgn * s["net"]
    return round(total, 3)


def signal_text(s):
    """Factual wording of a signal (what was picked, never why)."""
    piece = {"bottom": "trousers", "dress": "dress", "top": "top", "shoes": "shoes", "look": "look", "main": "dress or trousers"}[s["slot"]]
    if s["attribute"] == "lightness": return f"the {s['prefers']} {piece}"
    if s["attribute"] == "structure": return "a dress" if s["prefers"] == "dress" else "separates"
    if s["attribute"] == "color": return f"that {piece} colour"
    if s["attribute"] == "shape": return f"{str(s['prefers']).replace('_', '-')} {piece}"
    if s["attribute"] == "heel_height": return {"flat": "flat shoes", "low_platform": "low platform shoes", "heeled": "heeled shoes"}.get(s["prefers"], "those shoes")
    if s["attribute"] == "sleeve_length": return "a sleeveless top" if s["prefers"] == "sleeveless" else f"{str(s['prefers']).replace('_', '-')} sleeves"
    return f"a {AT.V['requests']['labels'].get(s['prefers'], str(s['prefers']).replace('_', ' ')).lower()} top"


def acknowledgement(keys, look_id, look=None):
    if not keys: return f"No problem. I'll build your final looks from {look_id} and your brief."
    words = dict(REASON_WORD, trouser_shape=f"the {SLOT_WORD[main_slot(look)] if look else 'trouser'} shape".replace("trousers shape", "trouser shape"))
    w = [words[k] for k in ("colors", "trouser_shape", "overall_feel") if k in keys]
    return f"Got it. I'll carry {w[0] if len(w) == 1 else ', '.join(w[:-1]) + ' and ' + w[-1]} from {look_id} into your final looks."


def _pool(slot, palette_id, idx, cons, excluded, k=None, near=None, pin=None):
    """Selectable items for one slot: in stock, single item, solid, constraints met, colour known; soft palette ranking."""
    rule = DATA["brief"]["outfit_rule"]; out = []
    for it in idx:
        if it["slot"] != slot or not it["in_stock"] or it.get("price") is None or it.get("single_item") is False or it["pattern"] != "solid" \
                or it["id"] in excluded or not passes(it, cons)[0] or (slot in ("top", "dress") and not it.get("pictured_color")):
            continue
        if pin and it["id"] != pin: continue
        cls, d, fam = color_class(it["hex"], palette_id)
        if cls == "unknown" or (palette_required() and (cls == "off" or (cls == "neutral" and slot not in rule["neutral_ok_slots"]))): continue
        out.append(dict(it, cls=cls, dist=d, family=fam))
    prank = lambda x: {"on_palette": 0, "free": 0}.get(x["cls"], 1 if x["cls"] == "neutral" else 2)
    pref_ids = {x["product_id"] for x in DATA["brief"].get("preferred_items", []) if x["slot"] == slot}
    key = (lambda x: (P.oklab_distance(x["hex"], near["hex"]) if near and near.get("hex") else 0, prank(x), x["dist"] or 1, x["id"])) if near else \
          (lambda x: (x["id"] not in pref_ids, size_pen(x), prank(x), x["dist"] or 1, x["id"]))
    out.sort(key=key)
    return out[:k] if k else out


KEEP_FIELDS = ("id", "slot", "title", "handle", "price", "hex", "cls", "dist", "family", "pictured_color", "tryon_image", "url", "heel_height")


def outfits(sid, palette_id, idx, n=4, excluded=None, structure="either", owned_top=True):
    """n distinct looks from the same rules. structure: 'separates', 'dress' or 'either' (then the best of each lead).
    owned_top: separates keep the shopper's own top (not priced); otherwise a catalog top is chosen."""
    excluded = set(excluded or ())
    b = DATA["brief"]; rule = b["outfit_rule"]; cons = DATA["constraints"]["always"] + b["constraints"]
    limit = budget_limit(b)
    structs = [s for s in (["separates", "dress"] if structure == "either" else [structure]) if not (owned_top and s == "dress")]
    per = {}
    for st in structs:
        slots = [s for s in STRUCTS[st] if not (st == "separates" and s == "top" and owned_top)]
        pools = {s: _pool(s, palette_id, idx, cons, excluded, k=(6 if s in ("top", "dress") else 8)) for s in slots}
        story = story_slots(st, catalog_top="top" in slots)
        combos = []
        for combo in __import__("itertools").product(*(pools[s] for s in slots)):
            c = dict(zip(slots, combo)); tot = sum(x["price"] for x in combo)
            if tot > limit: continue
            story_ok = not palette_id or sum(1 for s in story if s in c and c[s]["cls"] == "on_palette") >= rule["min_on_palette"]
            if not story_ok and palette_required(): continue
            pref = {x["product_id"] for x in b.get("preferred_items", [])}
            score = sum(PAL_W[x["cls"]] + (x["dist"] or 0) + size_pen(x) - (0.7 if x["id"] in pref else 0) for x in combo) + (0 if story_ok else STORY_PENALTY)
            combos.append((score, tuple(x["id"] for x in combo), c, tot, story_ok and not any(x["cls"] == "off" for x in combo)))
        combos.sort(key=lambda x: (x[0], x[1]))
        chosen, used = [], {s: set() for s in slots}
        main = "dress" if st == "dress" else "bottom"
        for allow_off in (False, True):    # soft palette: off-palette combos only when the palette-fitting ones can't fill n looks
            for strict in (2, 1, 0):       # 2: every piece distinct; 1: distinct necklaces; 0: only the main garment distinct
                for c in combos:
                    if len(chosen) == n: break
                    if (not allow_off and not c[4]) or c[2][main]["id"] in used[main]: continue
                    if strict >= 2 and any(c[2][s]["id"] in used[s] for s in slots if s not in (main, "necklace")): continue
                    if strict >= 1 and c[2]["necklace"]["id"] in used["necklace"]: continue
                    chosen.append(c); [used[s].add(c[2][s]["id"]) for s in slots]
        per[st] = [{**{s: {k: c[2][s][k] for k in KEEP_FIELDS} for s in slots}, "subtotal": round(c[3], 2)} for c in chosen]
    if len(structs) == 1:
        return per[structs[0]]
    out, a, d = [], per["separates"], per["dress"]
    for i in range(max(len(a), len(d))):                 # either: alternate, so round 1 can show one of each
        out += ([a[i]] if i < len(a) else []) + ([d[i]] if i < len(d) else [])
    return out[:n]


def proposal2(look, look_id, keys, palette_id, idx, shown, excluded=(), requested=(), pins=None, signals=()):
    """Proposal 2 from stated reasons over the real catalog, in the winner's structure. Hard: constraints, budget, owned
    pieces, outfit rule. Unnamed attributes may vary; never made dressier by default; unmet reasons become explained tradeoffs.
    signals: tentative preferences inferred from the round choices. They rank only after the stated reasons and the palette:
    a moderate one keeps its attribute out of the exploration penalty, and every one nudges the fit score by its weight."""
    st = structure_of(look); main = main_slot(look); noun = SLOT_WORD[main]
    slots = [s for s in STRUCTS[st] if isinstance(look.get(s), dict) and not look[s].get("owned")]
    off = [p for p in requested if p["slot"] not in slots]
    if off:
        return {"status": "no_match", "unmet": [AT.describe(p) for p in off], "superseded": [],
                "message": "Your top stays in Proposal 2. Ask to swap it and I'll look for " + " and ".join(AT.describe(p) for p in off) + "."}
    superseded = [k for k in keys if k == "trouser_shape" and any(p["attribute"] in ("leg_cut", "silhouette") for p in requested)]
    keys = [k for k in keys if k not in superseded]
    req_ok = lambda c: all(request_ok(c[p["slot"]], p) is True for p in requested if p["slot"] in c)
    b = DATA["brief"]; rule = b["outfit_rule"]; cons = DATA["constraints"]["always"] + b["constraints"]
    limit = budget_limit(b); maxd = DATA["neutrals"]["palette_fit_max"]
    story = story_slots(st, catalog_top="top" in slots)
    pools = {s: _pool(s, palette_id, idx, cons, set(excluded), k=10, near=look[s], pin=(pins or {}).get(s)) for s in slots}
    near = lambda a, c: bool(a.get("hex") and c.get("hex")) and P.oklab_distance(a["hex"], c["hex"]) <= maxd
    checks = {"colors": lambda c: near(c[main], look[main]) and near(c["shoes"], look["shoes"]),
              "trouser_shape": lambda c: shape_of(c[main]) is not None and shape_of(c[main]) == shape_of(look[main]),
              "overall_feel": lambda c: c["shoes"]["id"] == look["shoes"]["id"] and c["necklace"]["id"] == look["necklace"]["id"]}
    explore = bool(keys) and "overall_feel" not in keys
    live = [x for x in signals if x.get("status") != "overridden"]       # refined/confirmed ones agree with what was said: still a guide when it can't be met exactly
    firm = {(x["slot"], x["attribute"]) for x in live if x["confidence"] == "moderate" and x.get("status") == "tentative"}
    owned_cost = 0
    cands = []
    for combo in __import__("itertools").product(*(pools[s] for s in slots)):
        c = dict(zip(slots, combo)); tot = owned_cost + sum(x["price"] for x in combo)
        if c[main]["id"] == look[main]["id"] and not (pins and main in pins): continue
        if tot > limit or tuple(x["id"] for x in combo) in shown or not req_ok(c): continue
        short = max(0, rule["min_on_palette"] - sum(1 for s in story if s in c and c[s]["cls"] == "on_palette")) if palette_id else 0
        if short and palette_required(): continue
        off_pen = short + sum(1 for x in combo if x["cls"] == "off")
        unmet = [k for k in keys if not checks[k](c)]
        changed = [s for s in slots if c[s]["id"] != look[s]["id"]]
        variety = len(changed) if ("overall_feel" in keys or not keys) else -min(len(changed), 2)
        same = (("trouser_shape" not in keys and (main, "shape") not in firm and shape_of(c[main]) is not None and shape_of(c[main]) == shape_of(look[main]))
                + ("colors" not in keys and not {(main, "lightness"), (main, "color")} & firm and near(c[main], look[main]))) if explore else 0
        agree = agreement(c, live) if live else 0.0
        fit = sum(PAL_W.get(x["cls"], 0) + (x["dist"] or 0) + size_pen(x) for x in combo) - 0.5 * agree
        cands.append(((len(unmet), off_pen, same, variety, round(fit, 3), "".join(x["id"] for x in combo)), c, unmet, changed, tot))
    if not cands:
        if requested:
            return {"status": "no_match", "unmet": [AT.describe(p) for p in requested], "superseded": superseded,
                    "message": f"I couldn't find {' and '.join(AT.describe(p) for p in requested)} while keeping your other choices{within(limit)}."}
        return None
    rank, c, unmet, changed, tot = min(cands, key=lambda x: x[0])
    met = [k for k in keys if k not in unmet]; ch = [SLOT_WORD[x] for x in changed]
    chs = ch[0] if len(ch) == 1 else ", ".join(ch[:-1]) + " and " + ch[-1]
    words = dict(REASON_WORD, trouser_shape=f"the {'dress' if main == 'dress' else 'trouser'} shape")
    mw = [words[k] for k in met]
    tag = ((f"Keeps {mw[0] if len(mw) == 1 else ', '.join(mw[:-1]) + ' and ' + mw[-1]} from {look_id}. " if mw else "") if keys else f"Built from {look_id} and your brief. ") + f"Changes the {chs}."
    if requested: tag += " " + "Has " + " and ".join(AT.describe(p) for p in requested) + ", as you asked."
    followed = [x for x in live if x.get("status") == "tentative" and agreement(c, [x]) > 0 and x["slot"] in c]
    if followed: tag += " Also goes with what you picked in the rounds: " + ", ".join(signal_text(x) for x in followed[:2]) + "."
    T = {"trouser_shape": f"There isn't another {shape_word(look[main])} {noun} that also fits your other choices{within(limit)}, so this one tries a {shape_word(c[main])} {noun}.",
         "colors": f"I couldn't keep colors this close with your other choices{within(limit)}, so this one tries {c[main].get('pictured_color') or 'a different'} {noun}.",
         "overall_feel": f"To keep the other things you asked for, this one also changes the {chs}."}
    return {"look": {k: {f: v[f] for f in ("id", "slot", "title", "price", "hex", "cls", "dist", "pictured_color", "heel_height")} for k, v in c.items()},
            "subtotal": round(tot, 2), "tag": tag, "applied": learned_from(look, look_id, met),
            "tradeoffs": [{"attribute": k, "message": T[k]} for k in unmet], "changed": changed,
            "status": "match", "requested": [AT.describe(p) for p in requested], "superseded": superseded,
            "signalsFollowed": [signal_text(x) for x in followed]}


def revise_structure(look, target, requested, palette_id, idx, excluded=(), owned_top=None):
    """Switch a look between separates and a dress, keeping the shoes and necklace. The new garment(s) must meet every
    requested condition (unknown never counts). No complete match -> no_match; never a forced or partial composition."""
    b = DATA["brief"]; cons = DATA["constraints"]["always"] + b["constraints"]; limit = budget_limit(b)
    keep = {s: look[s] for s in ("shoes", "necklace")}
    fill = ["dress"] if target == "dress" else (["bottom"] if owned_top else ["top", "bottom"])
    others = sum(v["price"] for v in keep.values())
    pools = {s: [it for it in _pool(s, palette_id, idx, cons, set(excluded), k=40, near=look.get("dress") if s != "dress" else look.get("bottom"))
                 if all(request_ok(it, p) is True for p in requested if p["slot"] == s)] for s in fill}
    ranked = []
    for combo in __import__("itertools").product(*(pools[s] for s in fill)):
        tot = others + sum(x["price"] for x in combo)
        if tot > limit: continue
        key = (sum(not has_saved_size(x) for x in combo), sum(PAL_W.get(x["cls"], 0) + (x["dist"] or 0) for x in combo), tuple(x["id"] for x in combo))
        ranked.append((key, dict(zip(fill, combo)), tot))
    words = " and ".join(AT.describe(p) for p in requested) or ("a dress" if target == "dress" else "separates")
    if not ranked:
        return {"status": "no_match", "unmet": [AT.describe(p) for p in requested] or [target], "alternative": None,
                "message": f"I couldn't find {words} that works with your shoes and necklace{within(limit)}."}
    def pack(r):
        new = {**({"top": owned_top} if owned_top and target == "separates" else {}), **{s: {f: x[f] for f in KEEP_FIELDS} for s, x in r[1].items()}, **keep}
        return {"look": new, "subtotal": round(r[2], 2), "palette": {"mode": "soft", "outsideSuggested": [s for s, x in r[1].items() if x["cls"] == "off"]}}
    top = heapq.nsmallest(2, ranked, key=lambda r: r[0])
    return {"status": "match", **pack(top[0]), "runnerUp": pack(top[1]) if len(top) > 1 else None, "changed": fill, "requested": requested,
            "meets": [AT.describe(p) for p in requested]}


POSE_REFUSALS = re.compile(r"neck roll|face alignment|neck size|position should be", re.I)


def necklace_precheck(sid, photo, fb, necklace):
    """Can the provider place a necklace on this photo at all? (Oct 2 finding: refusals come from the pose, e.g. a tilted head,
    whatever is worn.) One necklace call on the photo itself: a refusal costs 0; an accepted check costs 1 unit. Cached per photo.
    -> {"status": "ok" | "refused" | "unknown", "reason"}"""
    cache = jl("necklace_checks.json", {})
    if sid in cache: return cache[sid]
    ref, fp = product_ref(necklace)
    box = neck_box(fb, Image.open(photo).size); k = skey("necklace-precheck", f"photo:{hashlib.sha256(photo.read_bytes()).hexdigest()[:16]}", box, necklace["id"], fp, RENDER_V)
    crop_p = OUT / f"{sid}-neckcheck-{k}.png"; Image.open(photo).convert("RGB").crop(box).save(crop_p)
    try:
        run_task(k, "/s2s/v2.0/task/2d-vto/necklace",
                 lambda s_, r_: {"src_file_id": s_, "ref_file_ids": [r_], "source_info": {"name": s_},
                                 "object_infos": [{"name": r_, "parameter": {"necklace_need_remove_background": True}}]},
                 crop_p, ref, UNITS["necklace"], f"{sid}-neckcheck")
        rec = {"status": "ok", "reason": None}
    except Exception as e:
        rec = {"status": "refused" if POSE_REFUSALS.search(str(e)) else "unknown", "reason": str(e)[:200]}
    if rec["status"] != "unknown":                    # a transient failure is not remembered
        _upd("necklace_checks.json", sid, lambda d, kk: d.__setitem__(kk, rec), {})
    return rec


TUCK = json.loads((ROOT / "data" / "tuck_rule.json").read_text())


def tuck_decision(top, bottom, formality=None):
    """Tucked or untucked for a top + trousers (data/tuck_rule.json, the 2026-10-02 styling rule). Facts the top doesn't
    have (catalog tops, or an owned top read before these were recorded) never match a rule, so the default applies.
    -> {"tuck": "tucked"|"untucked", "rule": index or None, "facts": {...}}"""
    facts = dict((top or {}).get("fit") or {})
    rise = AT.value(attrs_of(bottom), "rise") if bottom else None
    facts["trouser_rise"] = rise[0] if rise and len(rise) == 1 else "unknown"
    facts["occasion_formality"] = formality
    def match(cond):
        for k, vals in cond.items():
            if k == "any":
                if not any(facts.get(kk) in vv for kk, vv in vals.items()): return False
            elif facts.get(k) not in vals: return False
        return True
    for i, rule in enumerate(TUCK["rules"]):
        if match(rule["if"]):
            return {"tuck": rule["then"], "rule": i, "facts": facts}
    return {"tuck": TUCK["default"], "rule": None, "facts": facts}


def _edit_size(w, h, cap):
    """The image-to-image output size: the picture's aspect, longest side cap, both multiples of 16 (provider rule)."""
    k = cap / max(w, h)
    return f"{int(w * k) // 16 * 16}*{int(h * k) // 16 * 16}"


def _face_on(path):
    """The face box on an edited picture (the edit reframes slightly): the largest OpenCV Haar face. None if not found."""
    try:
        import cv2, numpy as np
    except ImportError:
        return None
    im = Image.open(path).convert("L"); g = np.asarray(im)
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    fs = sorted(casc.detectMultiScale(g, scaleFactor=1.05, minNeighbors=6, minSize=(im.width // 14,) * 2), key=lambda f: -f[2] * f[3])
    if not len(fs): return None
    x, y, w, h = (int(v) for v in fs[0])
    return {"box": [x, y, x + w, y + h], "image_size": list(im.size), "source": "haar_on_tuck_edit"}


def tuck_edit(sid, cur, lineage, look):
    """YouCam has no tuck control, so a tucked look that comes back untucked is tucked by YouCam's own image editor
    (AI Image Generator V2, image-to-image) from an instruction: no mask, no waist detection, any photo. A top that is
    already tucked or short comes back essentially unchanged. One task per picture (UNITS["tuck"]), cached like any stage.
    -> (output, lineage, step)"""
    E = TUCK["edit"]; t0 = time.time()
    noun = "skirt" if "skirt" in (look["bottom"].get("title") or "").lower() else "trousers"
    prompt = E["prompt"].format(bottom=noun)
    size = _edit_size(*Image.open(cur).size, E["max_side"])
    k = skey(lineage, "tuck", E["model"], prompt, E["negative_prompt"], size)
    out_p, st_ = run_task(k, "/s2s/v2.0/task/image-to-image/youcam",
                          lambda s_, r_: {"src_file_ids": [s_], "model": E["model"], "prompt": prompt, "negative_prompt": E["negative_prompt"],
                                          "prompt_extend": False, "size": size}, cur, None, UNITS["tuck"], f"{sid}-tf-tuck")
    return out_p, f"step:{k}", {"slot": "tuck", "stage": k, "parent": cur.name, "output": out_p.name, "provider_s": st_.get("elapsed_s"),
                                "wall_s": round(time.time() - t0, 1), "tuck": {"wanted": "tucked", "method": "youcam image edit"}}


def render_look(sid, photo, look, fb, owned_out=None, necklace_ok=True, tuck="tucked", owned=None):
    """The whole outfit, layered from the shopper's photo. The last layer sits on top, so the order is the tuck:
      dress:              photo -> dress (full_body) -> shoes -> necklace
      separates, tucked:  photo -> top (upper_body) -> bottom -> tuck edit -> shoes -> necklace  (own top: its shared layer -> bottom)
      separates, untucked: photo -> bottom -> top -> shoes -> necklace
    A tucked look then goes through tuck_edit (YouCam's image editor tucks the top in); if that task fails or the cap is
    reached, the untucked picture is kept and says so. The edit reframes slightly, so the necklace uses the face found on
    the edited picture (the original face box scaled to it if none is found). owned = {"ref": path, "id": ownedId} for the shopper's own top.
    Each stage key chains from its parent, so nothing from another composition can carry over."""
    st = structure_of(look); photo_lin = f"photo:{hashlib.sha256(photo.read_bytes()).hexdigest()[:16]}"
    steps, legacy, old_lineage = [], True, photo_lin     # legacy: every stage so far was carried over from a pre-fingerprint stage

    def cloth(cur, lineage, old, slot, cat):
        """One cloth-v4 stage. -> (output, lineage, old lineage, step)"""
        nonlocal legacy
        item, t0 = look[slot], time.time()
        if slot == "top" and item.get("owned"):
            ref = owned["ref"]; fp = hashlib.sha256(ref.read_bytes()).hexdigest()[:16]
            k = skey(photo_lin, "top", f"owned:{owned['id']}", "upper_body") if cur == photo else skey(lineage, "top", f"owned:{owned['id']}", fp, cat, RENDER_V)
            lk, out_name, pid = k, f"{sid}-top", item.get("ownedGarmentId")
        else:
            ref, fp = product_ref(item)
            k, lk = skey(lineage, slot, item["id"], fp, cat, RENDER_V), skey(old, slot, item["id"], cat)
            legacy = legacy and _legacy_ref_ok(item, fp) and _adopt(k, lk)
            out_name, pid = f"{sid}-tf-{slot}", item["id"]
        out_p, st_ = run_task(k, "/s2s/v2.0/task/cloth-v4", lambda s_, r_, cat=cat: {"src_file_id": s_, "ref_file_id": r_, "garment_category": cat, "change_shoes": False},
                              cur, ref, UNITS["cloth"], out_name)
        step = {"slot": slot, "stage": k, "parent": cur.name, "productId": pid, "ref": fp, "category": cat, "output": out_p.name,
                "provider_s": st_.get("elapsed_s"), "wall_s": round(time.time() - t0, 1), **({"adoptedFrom": lk} if legacy and lk != k else {})}
        return out_p, f"step:{k}", f"step:{lk}", step

    cur, lineage, old = photo, photo_lin, photo_lin
    if st == "dress":
        cur, lineage, old, step = cloth(cur, lineage, old, "dress", "full_body"); steps.append(step)
    elif tuck == "untucked":
        for slot, cat in (("bottom", "lower_body"), ("top", "upper_body")):
            cur, lineage, old, step = cloth(cur, lineage, old, slot, cat); steps.append(step)
    else:
        if look["top"].get("owned") and owned_out is not None:     # the shared own-top layer (made once per photo and top)
            cur = owned_out; lineage = old = "step:" + owned_out.stem.split("-")[-1]
        else:
            cur, lineage, old, step = cloth(cur, lineage, old, "top", "upper_body"); steps.append(step)
        cur, lineage, old, step = cloth(cur, lineage, old, "bottom", "lower_body"); steps.append(step)
        try:
            cur, lineage, step = tuck_edit(sid, cur, lineage, look); steps.append(step)
            W2, H2 = Image.open(cur).size; kx, ky = W2 / fb["image_size"][0], H2 / fb["image_size"][1]
            fb = _face_on(cur) or {**fb, "box": [int(v * (kx, ky)[i % 2]) for i, v in enumerate(fb["box"])], "image_size": [W2, H2], "source": "scaled_after_tuck_edit"}
            legacy = False                                 # nothing after the edit has a pre-fingerprint stage to adopt
        except ProviderError as e:                         # cap or provider: the untucked picture, said plainly
            steps[-1]["tuck"] = {"wanted": "tucked", "tucked": False, "error": str(e)[:160]}
            steps[-1]["tuckNote"] = "This top is meant to be worn tucked in, but the try-on left it out of the trousers, so it's shown untucked."
    old_lineage = old
    for slot, cat in (("shoes", "shoes"),):
        cur, lineage, old_lineage, step = cloth(cur, lineage, old_lineage, slot, cat); steps.append(step)
    if not necklace_ok:                           # the photo failed the necklace check: no submission, said plainly
        steps.append({"slot": "necklace", "stage": None, "status": "not_shown", "productId": look["necklace"]["id"], "output": cur.name, "wall_s": 0,
                      "reason": "necklaces can't be placed on this photo (the head is tilted), so it isn't drawn; it's still part of the outfit"})
        return steps
    box = neck_box(fb, Image.open(cur).size); k = skey(lineage, "neck-crop", box)
    crop_p = OUT / f"{sid}-neckcrop-{k}.png"; Image.open(cur).convert("RGB").crop(box).save(crop_p)
    item = look["necklace"]; t0 = time.time()
    try:
        ref, fp = product_ref(item)
        k2, lk2 = skey(k, "necklace", item["id"], fp, RENDER_V), skey(skey(old_lineage, "neck-crop", box), "necklace", item["id"])
        legacy = legacy and _legacy_ref_ok(item, fp) and _adopt(k2, lk2)
        out_p, st_ = run_task(k2, "/s2s/v2.0/task/2d-vto/necklace",
                              lambda s_, r_: {"src_file_id": s_, "ref_file_ids": [r_], "source_info": {"name": s_},
                                              "object_infos": [{"name": r_, "parameter": {"necklace_need_remove_background": True}}]},
                              crop_p, ref, UNITS["necklace"], f"{sid}-tf-necklace")
        changed, solid = _change_metrics(Image.open(crop_p).convert("RGB"), Image.open(out_p).convert("RGB"))
        if changed > 0.30 or solid > 0.25:
            raise ProviderError(f"QA gate: changed={changed:.3f}, solid={solid:.3f}")
        base = Image.open(cur).convert("RGB"); base.paste(Image.open(out_p).convert("RGB").resize((box[2] - box[0], box[3] - box[1])), (box[0], box[1]))
        comp = OUT / f"{sid}-tf-composite-{k2}.png"; base.save(comp)
        steps.append({"slot": "necklace", "stage": k2, "parent": cur.name, "productId": item["id"], "ref": fp, "output": comp.name, "provider_s": st_.get("elapsed_s"),
                      "wall_s": round(time.time() - t0, 1), "qa": {"changed": changed, "solid": solid}, **({"adoptedFrom": lk2} if legacy else {})})
    except Exception as e:                     # provider error, QA gate, or network: the picture is shown without the necklace
        steps.append({"slot": "necklace", "stage": locals().get("k2"), "status": "not_shown", "reason": str(e)[:160], "output": cur.name, "wall_s": round(time.time() - t0, 1)})
    return steps
