"""Catalog index: built and refreshed from the Livostyle catalog MCP (livostyle-catalog-mcp@0.1.0, stdio JSON-RPC).

What the MCP provides (verified 2026-10-13 against the pinned package):
  enumeration   search_products has no offset or cursor and returns at most 50 results, but it reports total_matches and
                an empty query matches every product. The catalog is enumerated by splitting on price (cents, inclusive),
                then category and product type inside a price that alone exceeds 50; the union must equal total_matches.
  details       get_product(handle): title, product type, category path, tags, description (first 1500 characters),
                up to 6 image URLs, variants (sku, title, price, stock, options).
  identifiers   handle and variant SKU. No product or variant ID.
  freshness     catalog_stats.last_synced (whole dataset). No per-product timestamps.
What it does not provide, and where that comes from instead (an explicitly identified supplement, joined by handle):
  the Shopify product ID, the dataset's metafields (supplier title and description, fit, length, style, pattern...),
  the untruncated description and per-product created/updated times. They are read from the MCP's own declared upstream
  file (its CATALOG_URL, read from the package). Membership, titles, variants, prices, stock and images always come from
  the MCP; a product whose supplement record disagrees with the MCP's title is left out of that build, never mixed.

Per build: enumerate -> details -> supplement -> per-product hashes -> attributes (rules, free) -> classification
(cached by input hash; paid Nova calls only for products whose inputs are new or changed, or whose slot method version
changed) -> validation -> atomic replace of the active index (the previous one is kept as catalog.prev.json).
Interrupted builds resume: every classification is written to the cache as soon as it exists, so a retry never pays twice.

    .venv/bin/python catalog_sync.py status       # what is active, how old, when the next refresh is due
    .venv/bin/python catalog_sync.py plan         # dry run: what a refresh would fetch and classify, and the estimated cost
    .venv/bin/python catalog_sync.py refresh      # manual refresh (also: app.py --catalog-refresh, npm run catalog)
    .venv/bin/python catalog_sync.py export       # maintainer: ship the active index (data/catalog.json) and classifications
    .venv/bin/python tests/check_catalog.py       # behavioural checks with a scripted MCP (no network, no paid calls)
Configuration: CALLIOPE_CATALOG_DIR (default state/catalog), CALLIOPE_CATALOG_REFRESH_DAYS (default 7),
CALLIOPE_CATALOG_NOVA_USD (cap for paid classification per run, default 0.05; never above what the spend authorization has left).
"""
import concurrent.futures as cf, fcntl, hashlib, io, json, os, re, select, shutil, subprocess, sys, threading, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import attributes as AT  # noqa: E402
import palette as P      # noqa: E402

SCHEMA = "calliope-catalog@2"
MCP_JS = ROOT / "node_modules" / "livostyle-catalog-mcp" / "dist" / "index.js"
SNAPSHOT = ROOT / "data" / "catalog.json"                 # shipped, built by this module; used when no active index exists
SEED_CLASSES = ROOT / "data" / "classifications.json"     # shipped classification cache (seed + paid results), so a fresh build pays nothing for them
# Slot methods. Bump a version and every record made by it is reclassified on the next build.
METHODS = {"top": "pixels+rules@1", "dress": "pixels+rules@1", "bottom": "pixels+nova-describe@1", "shoes": "nova-describe@1", "necklace": "nova-describe@1"}
PAID = {"bottom", "shoes", "necklace"}
EST_USD_PER_CALL = 0.0012                                  # measured mean $0.00087 (190 Oct 2 calls, 4 photos each), rounded up
MIN_SELECTABLE = {"top": 10, "dress": 10, "bottom": 5, "shoes": 5, "necklace": 3}
UA = {"User-Agent": "Mozilla/5.0 calliope-catalog"}
CFG = {"dir": Path(os.environ.get("CALLIOPE_CATALOG_DIR") or ROOT / "state" / "catalog"),
       "days": float(os.environ.get("CALLIOPE_CATALOG_REFRESH_DAYS") or 7), "nova_usd": float(os.environ.get("CALLIOPE_CATALOG_NOVA_USD") or 0.05),
       "nova_log": None, "nova_left": None, "on_activate": None}
_RUN = threading.Lock()
STATUS = {"state": "unknown"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":")); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- MCP client
class Mcp:
    """Newline-delimited JSON-RPC over stdio to the pinned MCP package. One request in flight at a time."""

    def __init__(self, timeout=90):
        if not shutil.which("node"):
            raise RuntimeError("Node.js is not on PATH (needed to run the Livostyle catalog MCP)")
        if not MCP_JS.is_file():
            raise RuntimeError("livostyle-catalog-mcp is not installed; run npm ci")
        self.p = subprocess.Popen(["node", str(MCP_JS)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.n, self.timeout, self.calls = 0, timeout, {}
        self.info = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "calliope", "version": "2"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()

    def rpc(self, method, params):
        self.n += 1; self._send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        end = time.time() + self.timeout
        while True:
            if not select.select([self.p.stdout], [], [], max(0.0, end - time.time()))[0]:
                raise RuntimeError(f"MCP {method} timed out after {self.timeout}s")
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("MCP process exited")
            msg = json.loads(line)
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"MCP {method}: {msg['error']}")
                return msg["result"]

    def call(self, name, args):
        self.calls[name] = self.calls.get(name, 0) + 1
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        if r.get("isError"):
            raise RuntimeError(r["content"][0]["text"])
        return json.loads(r["content"][0]["text"])

    def close(self):
        self.p.kill()


def enumerate_catalog(mcp, progress=lambda **k: None):
    """{handle: compact} for the whole catalog, or raises if the union isn't exactly total_matches."""
    total = mcp.call("search_products", {"limit": 50})["total_matches"]
    seen = {}

    def bucket(a, b):                               # integer cents, inclusive
        r = mcp.call("search_products", {"limit": 50, "min_price_usd": a / 100, "max_price_usd": b / 100})
        if r["total_matches"] <= 50:
            seen.update({x["handle"]: x for x in r["results"]}); progress(done=len(seen), total=total); return
        if a < b:
            m = (a + b) // 2; bucket(a, m); bucket(m + 1, b); return
        pool, got = list(seen.values()) + r["results"], {}      # one price with more than 50 products
        win = {"limit": 50, "min_price_usd": a / 100, "max_price_usd": b / 100}
        cats = {x["category"] for x in pool if x.get("category")}
        types = {x["product_type"] for x in pool if x.get("product_type")}

        def by_category(path):
            # The MCP matches a category substring, so a parent path also reaches leaf categories not seen yet; go one level
            # deeper only while a path has more than 50 products at this price.
            q = mcp.call("search_products", dict(win, category=path))
            cats.update(x["category"] for x in q["results"] if x.get("category"))
            types.update(x["product_type"] for x in q["results"] if x.get("product_type"))
            if q["total_matches"] <= 50:
                got.update({x["handle"]: x for x in q["results"]}); return
            for k in sorted({path + " > " + c[len(path) + 3:].split(" > ")[0] for c in cats if c.startswith(path + " > ")}):
                by_category(k)
            # What the known children leave out (a product filed on the path itself, or in an unseen child): split by product
            # type, then by rating, the last resort for a product with neither a leaf category nor a type. Anything still
            # missing makes the total check below refuse the build.
            for f in [{"product_type": t} for t in sorted(types)] + [{"min_rating": r} for r in (4.9, 4.5, 4.0, 3.0, 0.1)]:
                if sum(path.lower() in (x.get("category") or "").lower() for x in got.values()) >= q["total_matches"]:
                    return
                qq = mcp.call("search_products", dict(win, category=path, **f))
                if qq["total_matches"] <= 50:
                    got.update({x["handle"]: x for x in qq["results"]})

        for root in sorted({c.split(" > ")[0] for c in cats}):
            by_category(root)
        seen.update(got); progress(done=len(seen), total=total)

    bucket(1, 1_000_000)
    if len(seen) != total:
        raise RuntimeError(f"incomplete enumeration: {len(seen)} of {total} products reached through the MCP")
    return seen, total


def supplement_url():
    m = re.search(r"const CATALOG_URL = '([^']+)'", MCP_JS.read_text())
    if not m:
        raise RuntimeError("the MCP package no longer declares CATALOG_URL; the supplement source must be reviewed")
    return m.group(1)


def fetch_supplement(url):
    data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
    return {p["handle"]: p for p in json.loads(data)}, hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- products
VOLATILE = ("price_usd", "in_stock")


def merge(full, sup):
    """MCP record + supplement -> dataset-shaped product (what attributes.normalize reads), or (None, reason)."""
    if not sup:
        return None, "not in the supplement (the MCP and its upstream are out of sync); retried next build"
    if sup["title"] != full["title"] or not str(sup.get("id") or "").isdigit():
        return None, "supplement disagrees with the MCP (title or id); left out of this build"
    base = {"sku", "title", "price_usd", "in_stock"}
    variants = [{"sku": v["sku"], "title": v["title"], "price_usd": v["price_usd"], "in_stock": v["in_stock"],
                 "options": {k: x for k, x in v.items() if k not in base}} for v in full["variants"]]
    prices = [v["price_usd"] for v in variants if v["price_usd"] is not None]
    desc = sup.get("description") or ""
    return {"id": str(sup["id"]), "handle": full["handle"], "title": full["title"], "url": full["url"], "product_type": full.get("product_type"),
            "category": {"full_path": full.get("category") or ""}, "tags": full.get("tags") or [],
            "description": desc if desc[:1500] == full.get("description") else full.get("description") or "",
            "images": [{"url": u} for u in full.get("images") or []], "variants": variants,
            "price": {"min_usd": min(prices) if prices else None, "max_usd": max(prices) if prices else None},
            "metafields": sup.get("metafields") or {}, "vendor": sup.get("vendor"), "updated_at": sup.get("updated_at")}, None


def class_inputs(p):
    """Everything a classification may read, without price and stock (they change weekly and are re-read every build)."""
    return {"title": p["title"], "type": p["product_type"], "category": p["category"]["full_path"], "tags": p["tags"],
            "description": p["description"], "images": [i["url"] for i in p["images"]],
            "options": sorted({json.dumps(v["options"], sort_keys=True) for v in p["variants"]}),
            "metafields": {k: p["metafields"].get(k) for k in ("supplier_title", "supplier_description", "fit", "silhouette", "length",
                                                               "style", "closure", "material", "fabric", "pattern", "best_for", "gender")}}


RULES = json.loads((ROOT / "data" / "index_rules.json").read_text())


def slot_of(p):
    """Calliope slot for a product, or None. Clothing uses the attribute vocabulary's categories and permitted();
    shoes and necklaces use the merchant category."""
    path = p["category"]["full_path"]
    if (p["metafields"].get("gender") or "") == "Male" or re.search(r"\bmen'?s\b", p["title"], re.I):
        return None                                  # stock never decides membership: a sold-out piece stays indexed (unselectable)
    if re.search(r"> Shoes( >|$)", path):
        return "shoes"
    if re.search(r"> Jewelry > Necklaces$", path):
        return "necklace"
    cat = AT.category_of(p)
    if not cat or not AT.permitted(dict(p, variants=[dict(v, in_stock=True) for v in p["variants"]])):
        return None
    slot = {"pants": "bottom", "tops": "top", "dresses": "dress"}[cat]
    ex = RULES.get("slot_exclude", {}).get(slot)
    return None if ex and re.search(ex, p["title"], re.I) else slot


# ---------------------------------------------------------------- classification
ROI = {"top": (0.28, 0.22, 0.72, 0.48), "dress": (0.30, 0.28, 0.70, 0.62), "bottom": (0.25, 0.50, 0.75, 0.88)}


def fetch_image(url, width=480):
    u = url + ("&" if "?" in url else "?") + f"width={width}"
    from PIL import Image
    return Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read())).convert("RGB")


def image_hex(im, slot):
    """Spike method (2026-10-02): the most common non-background colour in the garment region."""
    from PIL import Image
    w, hh = im.size
    bg = [P.hex_to_oklab("#%02x%02x%02x" % im.getpixel((x, y))) for x in (2, w - 3) for y in (2, hh - 3)]
    x0, y0, x1, y1 = ROI[slot]
    q = im.crop((int(x0 * w), int(y0 * hh), int(x1 * w), int(y1 * hh))).resize((60, 60)).quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:18]
    for n, i in sorted(q.getcolors(), reverse=True):
        hx = "#%02x%02x%02x" % tuple(pal[i * 3: i * 3 + 3]); L, a, b = P.hex_to_oklab(hx)
        if min(sum((u - v) ** 2 for u, v in zip((L, a, b), g)) ** .5 for g in bg) < 0.05 or (L > 0.93 and (a * a + b * b) ** .5 < 0.02):
            continue
        return hx
    return None


def _rules_tools():
    sys.path.insert(0, str(ROOT / "tools"))
    import build_catalog as B          # the colour-name table, pictured() and pattern() used for the pinned tops and dresses
    return B


def classify_free(p, slot, rec):
    """Tops and dresses (no paid calls): colour measured from the first photo, the pictured colour by name, pattern and
    formality from the attribute rules."""
    B = _rules_tools()
    colors = list(dict.fromkeys(v["options"].get("Color") for v in p["variants"] if v["options"].get("Color")))
    hx = image_hex(fetch_image(p["images"][0]["url"]), slot) if p["images"] else None
    pc = B.pictured(colors, hx) if colors else None
    return {"hex": hx, "pictured_color": pc, "color_source": "pixels", "pattern": B.pattern(p, rec, pc), "gender": "womens",
            "heel_height": None, "formality": B.selectable(rec, "formality"), "formality_details": None, "image": 0}


DESCRIBE = {"name": "describe_product", "description": "Describe the product being sold in these photos.",
            "schema": {"type": "object", "additionalProperties": False,
                       "required": ["pictured_color", "garment_hex", "is_pattern", "best_photo", "tryon_ok", "gender", "formality"],
                       "properties": {
                           "pictured_color": {"type": "string", "description": "which of the listed colour names is shown, or 'other'"},
                           "garment_hex": {"type": "string", "description": "#rrggbb of the product's main colour (not skin, background or other clothes)"},
                           "is_pattern": {"type": "boolean"},
                           "best_photo": {"type": "integer", "description": "1-based index of the photo that shows the product most completely with the fewest other items"},
                           "tryon_ok": {"type": "boolean", "description": "true if that photo shows the whole product clearly, unobstructed, without other clothing or accessories overlapping it"},
                           "item_count": {"type": "integer", "description": "how many separate items of this product are visible in the best photo"},
                           "heel": {"enum": ["flat", "low_platform", "heeled", "not_shoes"], "description": "flat: under 1 inch; low_platform: chunky or platform sole under 2 inches; heeled: heel, wedge or platform 2 inches or more"},
                           "gender": {"enum": ["womens", "mens", "unisex"]},
                           "formality": {"enum": ["casual", "smart", "dressy"], "description": "casual: everyday, lounge or athletic (drawstring or elastic lounge waists, sneakers, distressed denim); smart: right for a nice dinner (tailored trousers, clean flats and sandals); dressy: evening or formal"},
                           "formality_details": {"type": "array", "items": {"enum": ["athletic", "embellished", "drawstring_waist", "elastic_waist", "tailored", "denim", "distressed", "linen", "leather_or_faux", "none"]}}}}}
DESCRIBE_SYS = ("You catalogue fashion products for a styling app. Look only at the product named in the title. "
                "Report its main colour as seen in the photos, ignoring skin, hair, background and other garments. Be literal.")


def classify_paid(p, slot, nova):
    """Trousers, shoes and necklaces: one Nova 2 Lite call over up to 4 photos (the Oct 2 colour-spike call, plus formality,
    which Oct 2 classified in a separate call whose prompt wasn't kept, and the item count for necklaces)."""
    colors = list(dict.fromkeys(v["options"].get("Color") for v in p["variants"] if v["options"].get("Color")))
    content = [{"text": f"Slot: {slot}. Title: {p['title']}. Listed colours: {', '.join(colors)}. {min(4, len(p['images']))} photos follow."}]
    for k, im in enumerate(p["images"][:4]):
        b = io.BytesIO(); fetch_image(im["url"]).save(b, "JPEG", quality=85)
        content += [{"text": f"Photo {k + 1}:"}, {"image": {"format": "jpeg", "source": {"bytes": b.getvalue()}}}]
    out, _ = nova.call(DESCRIBE_SYS, content, DESCRIBE, 300)
    if not out:
        raise RuntimeError("Nova returned no classification")
    hx = str(out.get("garment_hex", "")).lower()
    listed = (out.get("pictured_color") or "").lower() in [c.lower() for c in colors]
    heel = out.get("heel") if slot == "shoes" and out.get("heel") in ("flat", "low_platform", "heeled") else None
    if heel in ("flat", "low_platform") and re.search(RULES["heel_keywords_heeled"], p["title"], re.I):
        heel = None                                          # disputed -> unknown (data/index_rules.json)
    rec = {"pictured_color": next((c for c in colors if c.lower() == out["pictured_color"].lower()), None) if listed else None,
           "pattern": "patterned" if out.get("is_pattern") else "solid", "gender": out.get("gender"), "heel_height": heel,
           "formality": out.get("formality"), "formality_details": out.get("formality_details"),
           "image": max(1, min(int(out.get("best_photo") or 1), len(p["images"]))) - 1, "nova": out}
    if slot == "bottom":
        rec.update(hex=image_hex(fetch_image(p["images"][0]["url"]), "bottom"), color_source="pixels")
    else:
        rec.update(hex=hx if re.fullmatch(r"#[0-9a-f]{6}", hx) else None, color_source="nova")
    if slot == "necklace":
        rec["single_item"] = out.get("item_count") == 1
    return rec


# ---------------------------------------------------------------- cache, status, active index
def _paths():
    d = CFG["dir"]
    return {"active": d / "catalog.json", "prev": d / "catalog.prev.json", "cache": d / "classifications.json",
            "status": d / "status.json", "lock": d / ".lock"}


class Cache:
    """Classification records by key = (slot, method version, input hash). Shipped seed + this installation's results."""

    def __init__(self):
        self.p = _paths()["cache"]; self.mine = _read(self.p, {}) or {}
        self.seed = (_read(SEED_CLASSES, {}) or {}).get("records", {})
        self.lock = threading.Lock()

    def get(self, k):
        return self.mine.get(k) or self.seed.get(k)

    def put(self, k, rec):
        with self.lock:
            self.mine[k] = rec; _write(self.p, self.mine)        # immediately: an interrupted build never pays for this again


def key(slot, ihash):
    return f"{slot}|{METHODS[slot]}|{ihash}"


def _set(**kw):
    STATUS.update(kw, updatedAt=now())
    try:
        _write(_paths()["status"], STATUS)
    except OSError:
        pass


def active():
    return _read(_paths()["active"])


def usable(doc):
    """A complete index in this schema: safe to serve, even if it was classified by older slot methods."""
    return bool(doc and doc.get("schema") == SCHEMA and doc.get("items") and doc.get("complete"))


def compatible(doc):
    """Usable, and made by the current slot methods and attribute vocabulary (nothing to rebuild)."""
    return usable(doc) and doc.get("methods") == METHODS and doc.get("vocabulary") == AT.V["version"]


def age_days(doc):
    return (time.time() - datetime.fromisoformat(doc["builtAt"]).timestamp()) / 86400


def summary(doc):
    if not doc:
        return None
    return {"builtAt": doc["builtAt"], "source": doc["source"]["kind"], "upstreamSynced": doc["source"].get("lastSynced"),
            "products": doc["source"].get("totalProducts"), "counts": doc["counts"], "ageDays": round(age_days(doc), 2),
            "refreshDue": age_days(doc) >= CFG["days"], "refreshDays": CFG["days"], "changes": doc.get("changes")}


def status():
    return dict(STATUS, active=summary(active()))


# ---------------------------------------------------------------- build
def fetch(mcp_factory=Mcp, supplement=None, progress=lambda **k: None):
    """The whole catalog through the MCP, plus the supplement. -> dict"""
    progress(phase="enumerate", done=0, total=0, message="Listing the Livostyle catalog through its MCP")
    mcp = mcp_factory()
    try:
        stats = mcp.call("catalog_stats", {})
        compact, total = enumerate_catalog(mcp, lambda done, total: progress(phase="enumerate", done=done, total=total, message="Listing the Livostyle catalog through its MCP"))
        full = {}
        for i, hd in enumerate(sorted(compact)):
            d = mcp.call("get_product", {"handle": hd})
            if "error" in d:
                raise RuntimeError(f"get_product {hd}: {d['error']}")
            full[hd] = d
            if i % 100 == 0: progress(phase="details", done=i, total=total, message="Reading product details through the MCP")
        calls, server = dict(mcp.calls), mcp.info.get("serverInfo")
    finally:
        mcp.close()
    progress(phase="supplement", done=0, total=1, message="Reading product IDs and supplier details from the MCP's upstream file")
    url = supplement_url() if supplement is None else "test"
    sup, sup_sha = supplement if supplement is not None else fetch_supplement(url)
    prods, products, left = {}, {}, {}
    for hd, f in full.items():
        p, why = merge(f, sup.get(hd))
        if not p:
            left[hd] = why; continue
        prods[hd] = p
        products[hd] = {"id": p["id"], "contentHash": h(f), "inputHash": h(class_inputs(p)), "upstreamUpdatedAt": p.get("updated_at")}
    return {"stats": stats, "total": total, "calls": calls, "server": server, "url": url, "supSha": sup_sha, "prods": prods, "products": products, "left": left}


def build(mcp_factory=Mcp, supplement=None, nova=None, budget_usd=None, dry=False, progress=None):
    """One complete build. Returns (doc, report). Raises on anything that makes the result incomplete or invalid."""
    progress = progress or (lambda **k: _set(**k))
    t0 = time.time(); report = {"startedAt": now(), "classified": {"cached": 0, "free": 0, "paid": 0}, "paidUsd": 0.0}
    F = fetch(mcp_factory, supplement, progress)
    stats, total, server, url, sup_sha, prods, products, left = (F[k] for k in ("stats", "total", "server", "url", "supSha", "prods", "products", "left"))
    report["mcpCalls"] = F["calls"]
    prev = active() or {}
    prev_products = prev.get("products", {})
    changes = {"new": sorted(set(products) - set(prev_products)), "removed": sorted(set(prev_products) - set(products)),
               "changed": sorted(hd for hd in products if hd in prev_products and prev_products[hd]["contentHash"] != products[hd]["contentHash"])} if prev_products else None

    cache, todo, recs, items = Cache(), [], {}, {}
    for hd, p in prods.items():
        sl = slot_of(p)
        if not sl:
            continue
        rec = AT.normalize(p) if AT.category_of(p) else None
        recs[hd] = (sl, rec)
        k = key(sl, products[hd]["inputHash"])
        if cache.get(k):
            report["classified"]["cached"] += 1
        else:
            todo.append((hd, sl, rec, k))
    by_slot = {sl: [t for t in todo if t[1] == sl] for sl in sorted(PAID)}
    paid = [t for grp in __import__("itertools").zip_longest(*by_slot.values()) for t in grp if t]   # round-robin: a capped build still covers every slot
    report["plan"] = {"toClassify": len(todo), "paid": len(paid), "free": len(todo) - len(paid), "estimatedUsd": round(len(paid) * EST_USD_PER_CALL, 4)}
    if dry:
        return None, report

    cap = budget_usd if budget_usd is not None else CFG["nova_usd"]
    if CFG["nova_left"] is not None:
        cap = min(cap, CFG["nova_left"])
    if paid and nova is None and cap > 0:
        from nova_common import Nova
        nova = Nova(cap_usd=cap, log_path=CFG["nova_log"] or (ROOT / "state" / "engine" / "state" / "nova_calls.jsonl"))
    affordable = int(cap / EST_USD_PER_CALL + 1e-9) if nova is not None else 0
    skipped = paid[affordable:]
    for hd, sl, _, _ in skipped:
        left[hd] = f"needs a paid classification ({sl}); the per-run cap (${cap:.4f}) was reached. It is added on a later refresh"
    run = [t for t in todo if t[1] not in PAID] + paid[:affordable]
    done = [0]

    def one(t):
        hd, sl, rec, k = t
        try:
            out = classify_paid(prods[hd], sl, nova) if sl in PAID else classify_free(prods[hd], sl, rec)
            out.update(method=METHODS[sl], at=now(), source="run")
            cache.put(k, out)
            report["classified"]["paid" if sl in PAID else "free"] += 1
        except Exception as e:                       # one product's failure leaves it out; the build goes on
            left[hd] = f"classification failed: {str(e)[:160]}"
        done[0] += 1
        progress(phase="classify", done=done[0], total=len(run), message=f"Classifying new or changed products ({report['plan']['paid']} paid, {report['plan']['free']} free)")

    progress(phase="classify", done=0, total=len(run), message="Classifying new or changed products")
    with cf.ThreadPoolExecutor(4) as ex:              # free work (photo downloads) in parallel
        list(ex.map(one, [t for t in run if t[1] not in PAID]))
    for t in run:                                     # paid calls one at a time: Nova paces itself (~1 call/s) and Bedrock throttles bursts
        if t[1] in PAID:
            one(t)
    if nova is not None:
        report["paidUsd"] = round(getattr(nova, "spent", 0.0), 6); report["novaCalls"] = getattr(nova, "calls", 0)

    attrs = {}
    for hd, (sl, rec) in recs.items():
        c = cache.get(key(sl, products[hd]["inputHash"]))
        if not c:
            continue
        p = prods[hd]
        colors = list(dict.fromkeys(v["options"].get("Color") for v in p["variants"] if v["options"].get("Color")))
        pc = c.get("pictured_color")
        vs = [v for v in p["variants"] if sl in ("top", "dress") and pc and (v["options"].get("Color") or "") == pc] or p["variants"]
        stock = [v for v in vs if v["in_stock"]]
        img = p["images"][min(c.get("image") or 0, len(p["images"]) - 1)]["url"] if p["images"] else None
        it = {"id": p["id"], "slot": sl, "handle": hd, "title": p["title"], "url": p["url"],
              "price": min(v["price_usd"] for v in stock) if stock else None, "in_stock": bool(stock), "colors": colors,
              "pictured_color": pc, "hex": c.get("hex"), "color_source": c.get("color_source"), "pattern": c.get("pattern"),
              "gender": c.get("gender"), "heel_height": c.get("heel_height"), "formality": c.get("formality"),
              "formality_details": c.get("formality_details"), "tryon_image": img, "variants": p["variants"],
              "classified": {"method": c.get("method"), "source": c.get("source"), "at": c.get("at")}}
        if sl == "necklace":
            it["single_item"] = c.get("single_item")
        items[hd] = it
        if rec:
            attrs[p["id"]] = {"attributes": rec["attributes"]}
    doc = {"schema": SCHEMA, "builtAt": now(), "complete": True, "methods": METHODS, "vocabulary": AT.V["version"],
           "source": {"kind": "mcp", "mcp": "livostyle-catalog-mcp@0.1.0", "server": server, "lastSynced": stats.get("last_synced"),
                      "totalProducts": total, "mcpCalls": report["mcpCalls"],
                      "supplement": {"url": url, "sha256": sup_sha, "joinedBy": "handle",
                                     "fields": ["id", "metafields", "description (beyond 1500 characters)", "updated_at"]}},
           "counts": counts(list(items.values())), "changes": changes and {k: len(v) for k, v in changes.items()},
           "leftOut": left, "products": products, "items": sorted(items.values(), key=lambda x: x["id"]), "attributes": attrs}
    report.update(seconds=round(time.time() - t0, 1), leftOut=len(left), changes=changes)
    return doc, report


def selectable(it):
    return bool(it["in_stock"] and it.get("price") is not None and it.get("hex") and it.get("pattern") == "solid" and it.get("gender") in ("womens", "unisex")
                and it.get("single_item") is not False and (it["slot"] not in ("top", "dress") or it.get("pictured_color")))


def counts(items):
    out = {}
    for it in items:
        c = out.setdefault(it["slot"], {"indexed": 0, "selectable": 0}); c["indexed"] += 1; c["selectable"] += selectable(it)
    return out


def validate(doc, prev=None):
    """Problems that keep a build from becoming active (the previous index stays)."""
    probs = []
    ids, handles = [i["id"] for i in doc["items"]], [i["handle"] for i in doc["items"]]
    if len(set(ids)) != len(ids) or len(set(handles)) != len(handles):
        probs.append("duplicate product ids or handles")
    for it in doc["items"]:
        if not (it["id"].isdigit() and it["variants"] and (it["tryon_image"] or "").startswith("https://")):
            probs.append(f"incomplete record {it['handle']}"); break
    for sl, n in MIN_SELECTABLE.items():
        if doc["counts"].get(sl, {}).get("selectable", 0) < n:
            probs.append(f"only {doc['counts'].get(sl, {}).get('selectable', 0)} selectable {sl} items (needs {n})")
    if prev and usable(prev):
        a = sum(c["selectable"] for c in prev["counts"].values()); b = sum(c["selectable"] for c in doc["counts"].values())
        if b < a * 0.5:
            probs.append(f"selectable products fell from {a} to {b}; the upstream catalog looks incomplete")
    return probs


def activate(doc):
    """Atomic: the new index replaces the active one in a single rename; the previous one is kept."""
    p = _paths()
    if p["active"].exists():
        shutil.copy2(p["active"], p["prev"])
    _write(p["active"], doc)
    if CFG["on_activate"]:
        CFG["on_activate"](doc)


def run(reason="manual", **kw):
    """Build, validate, activate. One at a time per installation (thread lock + file lock). Returns the report."""
    if not _RUN.acquire(blocking=False):
        return {"ok": False, "error": "a catalog build is already running"}
    p = _paths(); p["lock"].parent.mkdir(parents=True, exist_ok=True)
    lk = open(p["lock"], "a")
    try:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"ok": False, "error": "another process is building the catalog"}
        prev = active()
        _set(state="refreshing" if usable(prev) else "setup", reason=reason, error=None, retry=False, phase="start", done=0, total=0)
        try:
            doc, rep = build(**kw)
            probs = validate(doc, prev)
            if probs:
                raise RuntimeError("the new index failed validation: " + "; ".join(probs))
            activate(doc)
            _set(state="ready", phase="done", message=f"Catalog ready: {sum(c['selectable'] for c in doc['counts'].values())} selectable products",
                 lastRun={**{k: rep[k] for k in ("startedAt", "seconds", "classified", "paidUsd", "leftOut", "plan")}, "ok": True})
            _log({"t": now(), "ok": True, "reason": reason, **{k: rep[k] for k in ("seconds", "classified", "paidUsd", "plan", "leftOut")}, "changes": doc.get("changes")})
            return {"ok": True, **rep}
        except Exception as e:
            kept = usable(prev)
            _set(state="ready" if kept else "failed", phase="failed", error=str(e)[:300], retry=True,
                 message=("The catalog refresh failed; the previous index is still in use." if kept else "The catalog couldn't be set up.") + " " + str(e)[:200],
                 lastRun={"ok": False, "error": str(e)[:300], "at": now()})
            _log({"t": now(), "ok": False, "reason": reason, "error": str(e)[:300]})
            return {"ok": False, "error": str(e)}
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN); lk.close(); _RUN.release()


def _log(row):
    with open(CFG["dir"] / "runs.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")


def startup(background=True, **kw):
    """At application start: use the active index; else install the shipped snapshot if it is compatible; else build one.
    An overdue index stays in use while a refresh runs in the background. Never a recurring scheduler."""
    p = _paths(); CFG["dir"].mkdir(parents=True, exist_ok=True)
    doc = active()
    if not usable(doc):
        snap = _read(SNAPSHOT)
        if compatible(snap) and os.environ.get("CALLIOPE_CATALOG_SNAPSHOT", "1") != "0":
            snap["source"] = dict(snap["source"], installedFrom="data/catalog.json (shipped snapshot)")
            activate(snap); doc = snap
            _set(state="ready", phase="done", message="Installed the shipped catalog snapshot", error=None, retry=False)
        else:
            _set(state="setup", phase="start", message="No catalog index yet; building one from the Livostyle MCP", error=None, retry=False)
            t = threading.Thread(target=run, kwargs={"reason": "first setup", **kw}, daemon=True)
            t.start()
            if not background:
                t.join()
            return status()
    else:
        if CFG["on_activate"]:
            CFG["on_activate"](doc)
        _set(state="ready", phase="done", error=None, retry=False, message=None)
    maybe_refresh(background=background, **kw)
    return status()


def maybe_refresh(background=True, **kw):
    """Start a refresh when the active index is older than the configured interval (checked at start and on new sessions)."""
    doc = active()
    if (compatible(doc) and age_days(doc) < CFG["days"]) or _RUN.locked():          # older methods or overdue: rebuild, old index serves meanwhile
        return False
    reason = "overdue" if compatible(doc) else "older classification methods" if usable(doc) else "no usable index"
    t = threading.Thread(target=run, kwargs={"reason": reason, **kw}, daemon=True); t.start()
    if not background:
        t.join()
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "plan":
        _, rep = build(dry=True, progress=lambda **k: None)
        print(json.dumps(rep, indent=1))
    elif cmd == "refresh":
        print(json.dumps(run("manual (command line)"), indent=1, default=str))
    elif cmd == "export":                            # maintainer: ship the active index and every classification made here
        doc = active(); assert compatible(doc), "no current active index to export"
        seed = _read(SEED_CLASSES, {"records": {}}); mine = _read(_paths()["cache"], {}) or {}
        seed["records"].update({k: v for k, v in mine.items() if v.get("source") == "run"})
        SEED_CLASSES.write_text(json.dumps(seed, indent=0))
        SNAPSHOT.write_text(json.dumps(dict(doc, source=dict(doc["source"], shipped="data/catalog.json, exported " + now())), separators=(",", ":")))
        print(f"exported {len(doc['items'])} products to {SNAPSHOT.relative_to(ROOT)}; {len(seed['records'])} classifications in {SEED_CLASSES.relative_to(ROOT)}")
    else:
        raise SystemExit(__doc__)
