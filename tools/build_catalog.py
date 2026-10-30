"""Superseded 2026-10-13 by catalog_sync.py, which imports this file's colour-name table, pictured(), selectable() and pattern()
for tops and dresses. Its __main__ is the record of how the v1 index added tops and dresses (inputs under artifacts/, not shipped).

Add catalog tops and dresses to the pinned selection index (data/catalog-index.json). Free: no model calls.

Inputs (development only, not needed at runtime): artifacts/catalog/livostyle-products.json (open women's catalog dataset)
and artifacts/catalog/catalog-attributes.json (normalized attributes with status). Product photos are read from the CDN.
  .venv/bin/python tools/build_catalog.py        # rewrites data/catalog-index.json and data/catalog-attributes.json

Per product, from data only (unknown stays unknown and is never selected):
  hex            measured from the product photo (spike method: quantize a body region, drop background and studio white)
  pictured_color the listed colour whose name is nearest the measured colour, only if unambiguous (else unknown)
  pattern        catalog attribute 'pattern' (solid / patterned) when its status is selectable
  formality      catalog attribute 'formality' (a range, e.g. casual+smart): its highest level, as the index's single value
  gender         'womens' (the dataset is a women's catalog)
  price/in_stock from the pictured colour's variants
Existing index items (bottoms, shoes, necklaces, the measured tops) are kept unchanged.
"""
import io, json, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import palette as P  # noqa: E402

PRODUCTS, ATTRS = ROOT / "artifacts/catalog/livostyle-products.json", ROOT / "artifacts/catalog/catalog-attributes.json"
OUT_INDEX, OUT_ATTRS = ROOT / "data/catalog-index.json", ROOT / "data/catalog-attributes.json"
SELECTABLE = {"explicit", "inferred"}
SLOT = {"tops": "top", "dresses": "dress"}
ROI = {"top": (0.28, 0.22, 0.72, 0.48), "dress": (0.30, 0.28, 0.70, 0.62)}
# Colour-name table from the 2026-10-02 colour spike (spike_free.py), unchanged.
NAMES = {
 "black": "#1b1b1b", "white": "#f4f2ee", "off white": "#ece6d8", "ivory": "#ece3cf", "cream": "#e9dfc8", "oatmeal": "#d8ccb4",
 "beige": "#d6c3a3", "khaki": "#b9a77c", "tan": "#c2a07a", "camel": "#b8865a", "taupe": "#a39383", "mocha": "#7b5b4a",
 "ash mocha": "#8a7466", "cappuccino": "#9b7b62", "caramel": "#b27a43", "cognac": "#8a4a24", "cinnamon": "#a5592f",
 "rust": "#9b4426", "brown": "#6b4a33", "dark brown": "#4a3022", "rum brown": "#5e3b28", "olive brown": "#6a5a35",
 "gray": "#8d8d8d", "grey": "#8d8d8d", "light gray": "#c4c4c4", "dark gray": "#4f4f4f", "charcoal": "#3c3c3e",
 "blue grey": "#6f7f8f", "navy": "#1f2a44", "dark navy": "#161d33", "blue": "#2f5fa8", "light blue": "#a8c4e0",
 "sky blue": "#87bde6", "dusty blue": "#7f98b0", "cloudy blue": "#9fb3c8", "misty blue": "#a9bccd", "pastel blue": "#b7cfe8",
 "dark blue": "#1f3a6b", "royal blue": "#2b4fbf", "cobalt blue": "#1f4fb4", "classic blue": "#2a4a85", "cerulean": "#2a7ab8",
 "denim": "#4a6a8f", "peacock blue": "#1f6f86", "teal": "#1f7a7a", "deep teal": "#155a5a", "green": "#3f7f4f",
 "dark green": "#24442f", "army green": "#4b5a32", "moss": "#5f6b3a", "olive": "#5d5b35", "sage": "#9aa98a",
 "dusty sage": "#a3ad94", "gray green": "#7d8a78", "matcha green": "#93a865", "light green": "#a9d3a0", "agate green": "#4f8f7f",
 "gum leaf": "#9cb4a0", "red": "#b3262e", "deep red": "#7f1d22", "brick red": "#8f3a2c", "scarlet": "#c22f2a",
 "burgundy": "#5e1b2a", "wine": "#5a1f2e", "cabernet": "#5b1f2f", "red coral": "#e0644f", "coral pink": "#ef8a7a",
 "pink": "#e9a6b8", "blush pink": "#efc3c6", "dusty pink": "#d4a0a5", "lt rose": "#e8b9c0", "rose": "#c9737f",
 "hot pink": "#e0407a", "fuchsia": "#c42a83", "fuchsia pink": "#d0378f", "strawberry": "#d9475a", "mauve": "#a3778a",
 "purple": "#6a3d8f", "violet": "#7f4fb0", "lavender": "#b8a6d6", "lilac": "#c3a7d1", "yellow": "#e6c83a",
 "banana yellow": "#ecd96b", "pastel yellow": "#f1e3a0", "amber": "#d08f2a", "orange": "#e0782a", "tangerine": "#ef8a2f",
 "sherbet": "#f2a98a", "gold": "#c9a44c", "rose gold": "#c99a86", "silver": "#b8b8bc", "dust storm": "#cbbcb0",
}
PATTERN = re.compile(r"leopard|plaid|floral|multi|tie ?dye|rainbow|combo|/|style |print|flower|heart|star|eye|leaf|teardrop|triangle|turtle|sun\b|^color$|^medium$|rhinestone", re.I)


def name_hex(c):
    k = re.sub(r"\s+", " ", c.strip().lower())
    if PATTERN.search(k):
        return None
    if k in NAMES:
        return NAMES[k]
    for w in sorted(NAMES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(w)}\b", k):
            return NAMES[w]
    return None


def image_hex(im, slot):
    """Spike method: the most common non-background colour in the body region."""
    w, h = im.size
    bg = [P.hex_to_oklab("#%02x%02x%02x" % im.getpixel((x, y))) for x in (2, w - 3) for y in (2, h - 3)]
    x0, y0, x1, y1 = ROI[slot]
    q = im.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))).resize((60, 60)).quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:18]
    for n, idx in sorted(q.getcolors(), reverse=True):
        hx = "#%02x%02x%02x" % tuple(pal[idx * 3: idx * 3 + 3]); L, a, b = P.hex_to_oklab(hx)
        if min(sum((u - v) ** 2 for u, v in zip((L, a, b), g)) ** .5 for g in bg) < 0.05 or (L > 0.93 and (a * a + b * b) ** .5 < 0.02):
            continue
        return hx
    return None


def pictured(colors, hx):
    """The listed colour shown in the photo: the only one, or the nearest by name when clearly nearer than the next."""
    if len(colors) == 1:
        return colors[0]
    if not hx:
        return None
    d = sorted((P.oklab_distance(hx, nh), c) for c in colors if (nh := name_hex(c)))
    if d and d[0][0] <= 0.12 and (len(d) == 1 or d[1][0] - d[0][0] >= 0.05):
        return d[0][1]
    return None


LEVELS = ["casual", "smart", "dressy"]


def selectable(rec, key):
    sh = rec.get("shared") or {}
    v, st = sh.get(key), sh.get(key + "_status", "explicit")
    if st not in SELECTABLE or not v:
        return None
    return max(v, key=LEVELS.index) if isinstance(v, list) else v


MULTI = re.compile(r"graphic|letter|slogan|colou?r ?block|contrast|two[- ]tone|stripe|print", re.I)


def pattern(p, rec, pc):
    """The catalog's pattern attribute, but a multi-colour listed name or graphic/contrast wording is never 'solid'."""
    v = selectable(rec, "pattern")
    if v == "solid" and ((pc and PATTERN.search(pc)) or MULTI.search(p["title"] + " " + " ".join(p.get("tags") or []))):
        return "patterned"
    return v


def fetch(url):
    u = url + ("&" if "?" in url else "?") + "width=480"
    return Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 calliope-catalog"}), timeout=30).read())).convert("RGB")


def entry(p, rec):
    slot = SLOT[rec["category"]]
    colors = list(dict.fromkeys(v["options"].get("Color") for v in p["variants"] if v["options"].get("Color")))
    try:
        hx = image_hex(fetch(p["images"][0]["url"]), slot)
    except Exception:
        hx = None
    pc = pictured(colors, hx) if colors else None
    vs = [v for v in p["variants"] if not pc or (v["options"].get("Color") or "") == pc]
    stock = [v for v in vs if v["in_stock"]]
    return {"id": p["id"], "slot": slot, "handle": p["handle"], "title": p["title"], "url": p["url"],
            "price": min(v["price_usd"] for v in stock) if stock else None, "in_stock": bool(stock), "colors": colors,
            "pictured_color": pc, "hex": hx, "color_source": "pixels", "pattern": pattern(p, rec, pc), "gender": "womens",
            "heel_height": None, "formality": selectable(rec, "formality"), "formality_details": None,
            "tryon_image": p["images"][0]["url"], "variants": p["variants"], "catalog_source": "build_catalog.py"}


if __name__ == "__main__":
    idx = json.loads(OUT_INDEX.read_text())
    keep = [i for i in idx if i.get("catalog_source") != "build_catalog.py"]
    have = {i["id"] for i in keep}
    recs = json.loads(ATTRS.read_text())
    prods = {p["id"]: p for p in json.loads(PRODUCTS.read_text())}
    todo = [(prods[k], r) for k, r in recs["records"].items() if r["category"] in SLOT and k in prods and k not in have and prods[k]["images"]]
    with ThreadPoolExecutor(12) as ex:
        new = list(ex.map(lambda a: entry(*a), todo))
    out = keep + new
    OUT_INDEX.write_text(json.dumps(out, indent=1))
    ids = {i["id"] for i in out}
    OUT_ATTRS.write_text(json.dumps({"provenance": recs["provenance"], "records": {k: {"attributes": v["attributes"]} for k, v in recs["records"].items() if k in ids}},
                                    separators=(",", ":")))
    usable = lambda i: bool(i["in_stock"] and i["hex"] and i["pictured_color"] and i["pattern"] == "solid")
    for sl in ("top", "dress"):
        xs = [i for i in new if i["slot"] == sl]
        print(sl, len(xs), "added;", sum(usable(i) for i in xs), "selectable (in stock, colour measured and named, solid);",
              {f: sum(i["formality"] == f for i in xs if usable(i)) for f in ("casual", "smart", "dressy", None)})
    print("index:", len(out), "items")
