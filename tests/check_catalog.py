"""Catalog setup and refresh, against a scripted MCP (same search/get semantics as livostyle-catalog-mcp@0.1.0: price window,
category substring, exact product type, 50 results, total_matches). Classification is replaced by counting stand-ins, so
"paid" calls are counted, never made. No network, no spending.

    .venv/bin/python tests/check_catalog.py

Covers: first setup (no index, no snapshot), enumeration past the 50-result cap, an interrupted setup resumed without
repeating finished classifications, an unchanged refresh (nothing reclassified), a changed product (only it), a price or
stock change (re-read, not reclassified), a removed product, a method-version bump (only that slot), a failed refresh
(previous index kept), the drop guard, the per-run spending cap, startup (snapshot install, overdue refresh), and a new leaf
category at a single crowded price.
"""
import copy, json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import catalog_sync as C  # noqa: E402

CATS = {"top": "Apparel & Accessories > Clothing > Clothing Tops > Blouses", "dress": "Apparel & Accessories > Clothing > Dresses",
        "bottom": "Apparel & Accessories > Clothing > Pants > Trousers", "shoes": "Apparel & Accessories > Shoes",
        "necklace": "Apparel & Accessories > Jewelry > Necklaces", "other": "Apparel & Accessories > Jewelry > Rings"}


def catalog():
    """~150 products; 60 rings share one price, so enumeration must split a single-price bucket."""
    out, n = {}, 0
    for slot, k in (("top", 20), ("dress", 15), ("bottom", 12), ("shoes", 10), ("necklace", 8), ("other", 60), ("other", 25)):
        for i in range(k):
            n += 1; hd = f"{slot}-{n}"
            price = 29.99 if slot == "other" and n % 2 else round(18 + (n * 7.31) % 90, 2)
            out[hd] = {"handle": hd, "id": str(7_800_000_000_000 + n), "title": f"Solid {slot.title()} {n}", "url": f"https://livostyle.com/products/{hd}",
                       "product_type": {"top": "Blouse", "dress": "Dress", "bottom": "Pants", "shoes": "Shoes", "necklace": "Necklace"}.get(slot, "Ring" if n % 3 else "Bracelet"),
                       "category": CATS[slot], "tags": ["solid"], "description": "A plain piece.", "images": [f"https://cdn.shopify.com/{hd}-{j}.webp" for j in range(3)],
                       "variants": [{"sku": f"{n}{j}", "title": f"Black / {z}", "price_usd": price, "in_stock": True, "Color": "Black", "Size": z} for j, z in enumerate("SML")],
                       "metafields": {"gender": "Female", "style": "Classic"}}
    return out


class FakeMcp:
    def __init__(self, data, broken=False):
        self.data, self.broken, self.calls = data, broken, {}
        self.info = {"serverInfo": {"name": "fake", "version": "0"}}

    def call(self, name, a):
        self.calls[name] = self.calls.get(name, 0) + 1
        ps = list(self.data.values())
        if name == "catalog_stats":
            return {"total_products": len(ps), "last_synced": "2026-10-01T00:00:00Z"}
        if name == "get_product":
            p = self.data.get(a["handle"])
            return {"error": "no product"} if not p else {k: p[k] for k in ("handle", "title", "url", "product_type", "category", "tags", "description", "images", "variants")}
        hits = [p for p in ps if (not a.get("min_price_usd") or p["variants"][0]["price_usd"] >= a["min_price_usd"])
                and (not a.get("max_price_usd") or p["variants"][0]["price_usd"] <= a["max_price_usd"])
                and (not a.get("category") or a["category"].lower() in p["category"].lower())
                and (not a.get("product_type") or a["product_type"].lower() == p["product_type"].lower())
                and (not a.get("min_rating") or p.get("rating", 0) >= a["min_rating"])]
        if self.broken and a.get("min_price_usd"):
            hits = hits[1:]                                  # the upstream drops a product mid-enumeration
        return {"total_matches": len(hits), "results": [{"handle": p["handle"], "category": p["category"], "product_type": p["product_type"]} for p in hits[:min(a.get("limit", 12), 50)]]}

    def close(self):
        pass


class Crash(BaseException):
    """Stands for the process being killed mid-build."""


COUNT = {"paid": 0, "free": 0, "crash_after": None}


def fake_paid(p, slot, nova):
    if COUNT["crash_after"] is not None and COUNT["paid"] >= COUNT["crash_after"]:
        raise Crash()
    COUNT["paid"] += 1; nova.spent += 0.001
    return {"hex": "#202020", "pictured_color": "Black", "pattern": "solid", "gender": "womens", "formality": "smart", "formality_details": [],
            "heel_height": "flat" if slot == "shoes" else None, "color_source": "nova", "image": 0, **({"single_item": True} if slot == "necklace" else {})}


def fake_free(p, slot, rec):
    COUNT["free"] += 1
    return {"hex": "#202020", "pictured_color": "Black", "color_source": "pixels", "pattern": "solid", "gender": "womens", "heel_height": None,
            "formality": "smart", "formality_details": None, "image": 0}


class FakeNova:
    spent, calls = 0.0, 0


def setup(tmp):
    C.CFG.update(dir=Path(tmp), nova_left=None, on_activate=None, days=7)
    C.STATUS.clear(); C.STATUS["state"] = "unknown"
    C.classify_paid, C.classify_free = fake_paid, fake_free
    C.SEED_CLASSES = Path(tmp) / "no-seed.json"; C.SNAPSHOT = Path(tmp) / "no-snapshot.json"


def go(data, broken=False, **kw):
    sup = ({hd: {"id": p["id"], "title": p["title"], "metafields": p["metafields"], "description": p["description"]} for hd, p in data.items()}, "sha-test")
    COUNT.update(paid=0, free=0)
    return C.run("test", mcp_factory=lambda: FakeMcp(data, broken), supplement=sup, nova=FakeNova(), budget_usd=kw.pop("budget_usd", 1.0), progress=lambda **k: None, **kw)


def main():
    tmp = tempfile.mkdtemp(prefix="calliope-catalog-check-")
    setup(tmp); data = catalog(); paid_slots = sum(1 for p in data.values() if p["category"] in (CATS["bottom"], CATS["shoes"], CATS["necklace"]))

    # interrupted first setup: the process dies after 7 paid classifications; nothing becomes active
    COUNT["crash_after"] = 7
    try:
        go(data); raise AssertionError("expected the simulated crash")
    except Crash:
        pass
    C._RUN.release() if C._RUN.locked() else None
    assert C.active() is None, "an interrupted build never becomes active"
    # resumed: the 7 finished paid classifications are not repeated
    COUNT["crash_after"] = None
    r = go(data)
    assert r["ok"], r
    assert COUNT["paid"] == paid_slots - 7, (COUNT, paid_slots)
    assert r["mcpCalls"]["get_product"] == len(data), "every product read through the MCP"
    doc = C.active()
    assert C.compatible(doc) and doc["source"]["totalProducts"] == len(data) and len(doc["products"]) == len(data), "enumeration got past the 50-result cap"
    assert {i["slot"] for i in doc["items"]} == {"top", "dress", "bottom", "shoes", "necklace"} and not any(i["handle"].startswith("other") for i in doc["items"])
    print(f"first setup resumed after a crash: {paid_slots - 7} paid classifications on resume (7 kept), {len(doc['items'])} indexed")

    # unchanged refresh: nothing reclassified, nothing changed
    r = go(data)
    assert r["ok"] and COUNT == {"paid": 0, "free": 0, "crash_after": None} and r["changes"] == {"new": [], "removed": [], "changed": []}, (COUNT, r["changes"])
    assert (Path(tmp) / "catalog.prev.json").exists(), "previous index kept"
    print("unchanged refresh: 0 classifications")

    # changes: a shoe's title (reclassify it), a necklace's stock and price (re-read only), a new dress, a removed top
    d2 = copy.deepcopy(data)
    d2["shoes-50"]["title"] = "Solid Shoes 50 Loafer"
    for v in d2["necklace-60"]["variants"]: v["in_stock"] = False
    for v in d2["necklace-61"]["variants"]: v["price_usd"] = 12.5
    d2["dress-new"] = dict(copy.deepcopy(d2["dress-21"]), handle="dress-new", id="7899999999999", title="Solid Dress New")
    del d2["top-3"]
    r = go(d2)
    assert r["ok"] and COUNT["paid"] == 1 and COUNT["free"] == 1, COUNT
    ch = r["changes"]; assert ch["new"] == ["dress-new"] and ch["removed"] == ["top-3"] and set(ch["changed"]) == {"shoes-50", "necklace-60", "necklace-61"}, ch
    by = {i["handle"]: i for i in C.active()["items"]}
    assert "top-3" not in by and not by["necklace-60"]["in_stock"] and by["necklace-61"]["price"] == 12.5 and by["shoes-50"]["title"].endswith("Loafer")
    print("changed refresh: 1 paid (changed title) + 1 free (new dress); stock/price re-read without reclassifying; removed product dropped")

    # a slot method version change reclassifies that slot only
    old = dict(C.METHODS); C.METHODS["necklace"] = "nova-describe@2"
    r = go(d2)
    assert r["ok"] and COUNT["paid"] == 8 and COUNT["free"] == 0, COUNT
    C.METHODS.clear(); C.METHODS.update(old)
    print("method change: only the 8 necklaces reclassified")

    # a failed refresh keeps the previous index (incomplete enumeration)
    before = C.active()["builtAt"]; time.sleep(1.1)
    r = go(d2, broken=True)
    assert not r["ok"] and "incomplete enumeration" in r["error"] and C.active()["builtAt"] == before and C.STATUS["state"] == "ready" and C.STATUS["retry"], (r, C.STATUS)
    # the drop guard: an upstream that lost most products doesn't replace the index
    small = {k: v for i, (k, v) in enumerate(d2.items()) if not k.startswith(("top", "dress")) or i % 4 == 0}
    r = go(small)
    assert not r["ok"] and ("fell from" in r["error"] or "selectable" in r["error"]) and C.active()["builtAt"] == before, r
    print("failed refreshes keep the previous index:", r["error"][:90])

    # spending cap: products that would exceed it are left out (and named), the rest of the build goes on
    setup(tempfile.mkdtemp(prefix="calliope-catalog-cap-"))
    r = go(data, budget_usd=C.EST_USD_PER_CALL * 20)
    lo = C.active()["leftOut"]
    assert r["ok"] and COUNT["paid"] == 20 and len(lo) == paid_slots - 20 and all("cap" in v for v in lo.values()), (COUNT, len(lo))
    r = go(data, budget_usd=1.0)
    assert r["ok"] and COUNT["paid"] == paid_slots - 20 and not C.active()["leftOut"], COUNT
    print(f"spending cap: 20 classified, {paid_slots - 20} left out and named, added by the next refresh")

    # startup: a compatible shipped snapshot is installed without a build; an overdue index refreshes (index stays usable)
    t3 = tempfile.mkdtemp(prefix="calliope-catalog-start-"); snap = Path(t3) / "snap.json"
    snap.write_text(json.dumps(C.active())); setup(t3); C.SNAPSHOT = snap
    calls = []
    orig = C.run
    C.run = lambda *a, **k: calls.append(k) or {"ok": True}
    st = C.startup(background=False)
    assert st["state"] == "ready" and C.active()["source"].get("installedFrom") and not calls, (st, calls)
    doc = C.active(); doc["builtAt"] = "2020-01-01T00:00:00+00:00"; C._write(C._paths()["active"], doc)
    C.startup(background=False)
    assert calls and calls[0]["reason"] == "overdue", calls             # (a missing index retries as "no usable index")
    C.run = orig
    print("startup: shipped snapshot installed; an overdue index triggers one background refresh")

    # a crowded price holding a leaf category no other product has (a category added upstream) is reached through its parent
    crowd = catalog(); at = lambda p, **k: dict(copy.deepcopy(p), variants=[dict(v, price_usd=24.99) for v in p["variants"]], **k)
    for i in range(55):
        crowd[f"ring-x{i}"] = at(crowd["other-67" if i < 30 else "other-69"], handle=f"ring-x{i}")          # 30 rings, 25 bracelets
    crowd["skirt-new"] = at(crowd["bottom-36"], handle="skirt-new", category="Apparel & Accessories > Clothing > Skirts")
    crowd["anklet-new"] = at(crowd["other-67"], handle="anklet-new", category="Apparel & Accessories > Jewelry > Anklets")   # its parent has >50 here
    crowd["on-path"] = at(crowd["other-67"], handle="on-path", category="Apparel & Accessories > Jewelry", product_type="", rating=4.7)  # no leaf, no type
    got, total = C.enumerate_catalog(FakeMcp(crowd))
    assert {"skirt-new", "anklet-new", "on-path"} <= set(got) and len(got) == total == len(crowd), (len(got), total)
    print("new leaf categories at a crowded price: reached through the parent path, by product type, or by rating")
    print("catalog checks ok")


if __name__ == "__main__":
    main()
