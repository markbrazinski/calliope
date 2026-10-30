"""Catalog attributes: normalize product text into typed attribute records, and match shopper requests against them.

Vocabulary, precedence and detection rules are data (attributes.json). Stdlib only; no network, no model calls.
    python3 attributes.py build [products.json] [out.json]   # normalize the permitted pants/tops/dresses
    python3 attributes.py check                              # self-check

Record per attribute: {values, status, basis, method, evidence[], overridden[]}
  status: explicit | reviewed | inferred | unresolved | unknown | not_stated (multi-value fields)
  Only explicit / reviewed / inferred values are selectable; unresolved and unknown never satisfy anything.
"""
import hashlib, html, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
V = json.loads((ROOT / "attributes.json").read_text())
A = V["attributes"]
RX = {a: {v: re.compile(r, re.I) for v, r in d["values"].items()} for a, d in A.items()}
STYLING = re.compile(V["styling_sentence_rx"], re.I)
WEAK = set(V["weak_metafield_values"])
TIER = {s: (i, t) for i, t in enumerate(V["precedence"]) for s in t["sources"]}
SELECTABLE = set(V["selectable_statuses"])
WEAK_BASES = set(V.get("strict_excluded_bases", []))     # e.g. tags-only: evidence, never a strict match
DATASET = ROOT / "artifacts" / "catalog" / "livostyle-products.json"
OUT = ROOT / "artifacts" / "catalog" / "catalog-attributes.json"


# ---------------- normalization ----------------
def clean(s, cut=None):
    """Plain text without styling-advice, sizing-advice and model-measurement sentences (they describe other things)."""
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    if cut and cut in s: s = s.split(cut)[0]
    return " ".join(x for x in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", s)) if not STYLING.search(x))


def sources(p):
    mf = p["metafields"]
    return {"title": p["title"], "supplier_title": mf.get("supplier_title", ""), "category": p["category"]["full_path"].split(" > ")[-1],
            "tags": " | ".join(p["tags"]), "supplier_description": clean(mf.get("supplier_description")),
            "description": clean(p["description"], "Wear it with"),
            **{f"mf.{k}": str(mf.get(k) or "") for k in ("fit", "silhouette", "length", "style", "closure", "material", "fabric", "pattern", "best_for")}}


def _snip(text, m, w=40):
    return ("…" if m.start() > w else "") + text[max(0, m.start() - w):m.end() + w].strip() + ("…" if m.end() + w < len(text) else "")


def rank(attr):
    d = A[attr]
    return {v: i for i, v in enumerate(d["order"])} if d.get("order") else d.get("partial_order", {}).get("ranks", {})


def compatible(attr, vals):
    """Same value, a declared compatible pair, or within the attribute's declared tolerance on its scale."""
    pairs, r, tol = {frozenset(x) for x in A[attr].get("compatible", [])}, rank(attr), A[attr].get("tolerance", 0)
    near = lambda a, b: a in r and b in r and abs(r[a] - r[b]) <= tol
    return all(a == b or frozenset((a, b)) in pairs or near(a, b) for a in vals for b in vals)


def _hits(attr, src, extra=()):
    """[(source, text, value)] from every source this attribute reads. `extra`: derived/vision/review evidence."""
    d, out = A[attr], list(extra)
    names = ["title", "supplier_title", "category", "tags"] if attr.endswith("_type") else \
            ["title", "supplier_title", "supplier_description", "description", "tags"]
    for s in names + d.get("metafields", []):
        txt = src.get(s, "")
        if s.startswith("mf.") and txt.strip().lower() in WEAK | set(d.get("weak_metafield_values", [])): continue
        mapped = d.get("metafield_map", {}).get(txt.strip().lower()) if s.startswith("mf.") else None
        if mapped: out.append((s, txt, mapped)); continue
        for v, rx in RX[attr].items():
            m = rx.search(txt)
            if m: out.append((s, _snip(txt, m) if len(txt) > 90 else txt, v))
    return out


def resolve(attr, hits):
    """Explicit precedence: the highest tier with a value decides; disagreement inside that tier is unresolved."""
    d, gen = A[attr], set(A[attr].get("generic", []))
    ev = [{"source": s, "text": t, "value": v} for s, t, v in hits]
    if not hits: return {"values": [], "status": "not_stated" if d["kind"] == "multi" else "unknown", "basis": None, "method": "rules", "evidence": [], "overridden": []}
    if d["kind"] == "multi":                 # details: union of the best tier that mentions any; absence is not_stated, never "absent"
        best = min(TIER[s][0] for s, _, _ in hits); tier = V["precedence"][best]
        vals = sorted({v for s, _, v in hits if TIER[s][0] == best})
        return {"values": vals, "status": tier["status"], "basis": tier["tier"], "method": "rules", "evidence": ev, "overridden": []}
    tiers = sorted({TIER[s][0] for s, _, _ in hits})
    for i in tiers:
        vals = {v for s, _, v in hits if TIER[s][0] == i}
        if vals <= gen and any((v not in gen) for s, _, v in hits if TIER[s][0] > i): continue   # generic word; a lower tier is specific
        vals = (vals - gen) or vals
        tier = V["precedence"][i]
        if not compatible(attr, vals):
            return {"values": sorted(vals), "status": "unresolved", "basis": tier["tier"], "method": "rules", "evidence": ev, "overridden": []}
        over = [e for e in ev if TIER[e["source"]][0] > i and not compatible(attr, vals | {e["value"]})]
        keep = [e for e in ev if e not in over]
        method = "vision" if tier["tier"] == "vision" else "review" if tier["tier"] == "reviewed" else "rules"
        return {"values": sorted(vals, key=lambda v: rank(attr).get(v, 0)), "status": tier["status"], "basis": tier["tier"],
                "method": method, "evidence": keep, "overridden": over}
    return {"values": [], "status": "unknown", "basis": None, "method": "rules", "evidence": ev, "overridden": []}


FIBER_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%\s?([A-Za-z]+)")


def fibers(src):
    """Fibre composition by precedence (supplier text > site copy > metafield). Percentages kept when stated."""
    def read(txt):
        found = {}
        for pct, word in FIBER_PCT.findall(txt):
            for f, r in V["fibers"].items():
                if re.fullmatch(r.replace("\\b", ""), word, re.I) or re.search(r, word, re.I): found.setdefault(f, float(pct))
        for f, r in V["fibers"].items():
            if re.search(r, txt, re.I): found.setdefault(f, None)
        return found
    by = [(s, read(src[s])) for s in ("supplier_description", "description", "mf.material")]
    by = [(s, f) for s, f in by if f]
    if not by: return {"values": [], "status": "unknown", "basis": None, "method": "rules", "evidence": [], "overridden": []}
    s0, f0 = by[0]
    over = [{"source": s, "text": ", ".join(f), "value": sorted(f)} for s, f in by[1:] if not set(f) & set(f0)]
    keep = [{"source": s, "text": ", ".join(f), "value": sorted(f)} for s, f in by if not any(o["source"] == s for o in over)]
    return {"values": [{"fiber": k, "pct": v} for k, v in sorted(f0.items(), key=lambda kv: -(kv[1] or 0))],
            "status": "explicit" if TIER[s0][1]["status"] == "explicit" else "inferred", "basis": TIER[s0][1]["tier"],
            "method": "rules", "evidence": keep, "overridden": over}


def category_of(p):
    for c, d in V["categories"].items():
        if re.search(d["path_rx"], p["category"]["full_path"]): return c


def permitted(p):
    return any(v["in_stock"] for v in p["variants"]) and p["metafields"].get("gender", "Female") != "Male" \
        and not re.search(r"\b(set|two[- ]piece|romper|jumpsuit)\b|\bmen'?s\b", p["title"], re.I)


def normalize(p, provenance=None, extra=None):
    """One product -> attribute record. `extra`: {attr: [(source, text, value)]} for vision or review evidence."""
    cat, src, extra = category_of(p), sources(p), extra or {}
    attrs, groups = {}, V["categories"][cat]["groups"]
    names = [x for g in groups for x in V.get("groups", {}).get(g, [g])]
    order = sorted((n for n in names if n != "fiber_composition"), key=lambda n: bool(A[n].get("derive")))
    for n in order:
        der = []
        for r in A[n].get("derive", []):
            (ka, kv), = r["if"].items()
            k = attrs.get(ka)
            if k and k["status"] in SELECTABLE and kv in k["values"]: der.append(("derived", f"{ka}={kv}", r["then"]))
        attrs[n] = resolve(n, _hits(n, src, der + list(extra.get(n, []))))
    attrs["fiber_composition"] = fibers(src)
    jr = A.get("pants_type", {}).get("jeans_rule")
    con, fib = attrs["construction"], {f["fiber"] for f in attrs["fiber_composition"]["values"]}
    denim_ok = set(con["values"]) & set(jr["requires_construction"]) and con["basis"] != "tags" and (not fib or "cotton" in fib) if jr else True
    if cat == "pants" and jr and attrs["pants_type"]["values"] == ["jeans"] and not denim_ok:
        attrs["pants_type"] = dict(attrs["pants_type"], values=[jr["else"]], status="inferred", basis="derived",
                                   overridden=attrs["pants_type"]["evidence"], evidence=[{"source": "derived", "text": jr["note"], "value": jr["else"]}])
    has_elastane = any(f["fiber"] == "spandex" for f in attrs["fiber_composition"]["values"]) or None
    pat_src = " ".join((src["title"], src["mf.pattern"]))
    sh = V["shared"]
    return {"productId": str(p["id"]), "handle": p["handle"], "title": p["title"], "url": p["url"], "vendor": p.get("vendor"),
            "category": cat, "categoryPath": p["category"]["full_path"],
            "variants": [{"sku": v["sku"], "title": v["title"], "options": v["options"], "inStock": v["in_stock"], "price": v["price_usd"]} for v in p["variants"]],
            "shared": {"listedColors": sorted({v["options"].get("Color", "") for v in p["variants"]} - {""}),
                       "price": {"min": p["price"]["min_usd"], "max": p["price"]["max_usd"]},
                       "pattern": "patterned" if re.search(sh["pattern"]["patterned"], pat_src, re.I) else ("solid" if re.search(sh["pattern"]["solid"], pat_src, re.I) else None),
                       "formality": sorted(k for k, r in sh["formality_from_style"].items() if re.search(r, " ".join((src["mf.style"], src["mf.best_for"], src["tags"])), re.I)),
                       "formality_status": "inferred"},
            "attributes": attrs, "evidenceOnly": {"hasElastane": has_elastane},
            "provenance": provenance}


def provenance(path):
    return {"dataset": "arturayupov/womens-fashion-catalog-open-data", "file": "data/products.json",
            "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "copiedFrom": "2026-10-02 colour spike download",
            "commit": None, "commitNote": "Not recorded for this copy. Gate-1 selection pinned e10572bf53be6bf0dd4ed99aa267aed50998e8aa.",
            "license": "MIT (dataset); product images remain © Livostyle", "vocabulary": V["version"],
            "vocabularySha256": hashlib.sha256((ROOT / "attributes.json").read_bytes()).hexdigest()[:16],
            "normalizedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def build(path=DATASET, out=OUT):
    prov = provenance(path)
    recs = {}
    for p in json.loads(Path(path).read_text()):
        if category_of(p) and permitted(p): recs[str(p["id"])] = normalize(p)
    Path(out).write_text(json.dumps({"provenance": prov, "records": recs}, ensure_ascii=False))
    return prov, recs


# ---------------- matching ----------------
def value(rec_attrs, attr):
    """Selectable values or None. Unresolved/unknown -> None (never satisfies)."""
    a = (rec_attrs or {}).get(attr)
    return a["values"] if a and a["status"] in SELECTABLE and a.get("basis") not in WEAK_BASES and a["values"] else None


def ordered(attr):
    return bool(rank(attr))


def check_pref(pref):
    """Structural check before matching. -> None, or an issue: no_order | anchor_unknown | edge_of_scale."""
    attr, op = pref["attribute"], pref["op"]
    if attr in V.get("computed_request_attributes", {}):     # e.g. lightness: needs the current piece's measured colour
        if op not in V["computed_request_attributes"][attr]["ops"]: return "anchor_unknown"
        return None if op == "eq" or pref.get("anchor", {}).get("values") else "anchor_unknown"      # a named colour needs no anchor
    if op in ("lt", "gt"):
        if not ordered(attr): return "no_order"
        r, anc = rank(attr), pref.get("anchor", {}).get("values")
        if not anc or not all(a in r for a in anc): return "anchor_unknown"
        if (op == "lt" and min(r[a] for a in anc) == min(r.values())) or (op == "gt" and max(r[a] for a in anc) == max(r.values())): return "edge_of_scale"
    if op == "neq" and not pref.get("anchor", {}).get("values"): return "anchor_unknown"
    return None


def satisfies(rec_attrs, pref):
    """True / False / None (item value unknown or unresolved: never counted as meeting the request)."""
    vals = value(rec_attrs, pref["attribute"])
    if vals is None: return None
    op = pref["op"]
    if op == "eq": return pref["value"] in vals
    if op == "in": return bool(set(vals) & set(pref["values"]))
    anc = pref["anchor"]["values"]
    if op == "neq": return not set(vals) & set(anc)
    r = rank(pref["attribute"])
    if not all(v in r for v in vals): return False          # unranked value (e.g. flare) is not "narrower" or "wider"
    return max(r[v] for v in vals) < min(r[a] for a in anc) if op == "lt" else min(r[v] for v in vals) > max(r[a] for a in anc)


def describe(pref):
    """Short shopper-facing phrase for a preference, e.g. 'a lower rise', 'a different neckline'."""
    w = pref["attribute"].replace("_", " ").replace("leg cut", "leg").replace("sleeve length", "sleeves")
    if pref["attribute"] == "color":
        return f"{pref['value']} " + {"bottom": "trousers", "shoes": "shoes", "top": "top", "necklace": "necklace", "dress": "dress"}.get(pref.get("slot"), "piece")
    if pref["op"] == "eq":
        v = pref["value"]
        piece = "dress" if pref.get("slot") == "dress" else "top"
        lab = V['requests']['labels'].get(v, v.replace('_', ' '))
        return {"rise": f"{v}-rise trousers", "leg_cut": f"{v.replace('_', '-')} trousers", "pants_type": v,
                "neckline": f"a {lab} {piece}", "sleeve_length": f"{v.replace('_', ' ')} sleeves",
                "dress_length": f"a {lab.lower()} dress", "silhouette": f"{'an' if lab[0].lower() in 'aeiou' else 'a'} {lab} dress"}.get(pref["attribute"], f"{v.replace('_', ' ')} {w}")
    if pref["op"] == "neq": return f"a different {w}"
    if pref["attribute"] == "lightness":
        piece = {"bottom": "trousers", "shoes": "shoes", "top": "top", "necklace": "necklace", "dress": "dress"}.get(pref.get("slot"), "piece")
        return f"{'darker' if pref['op'] == 'lt' else 'lighter'} {piece}"
    m = re.search(r"slimmer|narrower|skinnier|wider", pref.get("sourcePhrase", ""), re.I) if pref["attribute"] == "leg_cut" else None
    if m:
        return f"a {m.group(0).lower()} leg"                 # echo the shopper's own comparative
    word = {("rise", "lt"): "a lower rise", ("rise", "gt"): "a higher rise", ("leg_cut", "lt"): "a slimmer leg", ("leg_cut", "gt"): "a wider leg",
            ("sleeve_length", "gt"): "longer sleeves", ("sleeve_length", "lt"): "shorter sleeves",
            ("dress_length", "gt"): "a longer dress", ("dress_length", "lt"): "a shorter dress"}
    return word.get((pref["attribute"], pref["op"]), f"a {'lower' if pref['op'] == 'lt' else 'higher'} {w}")


def selfcheck():
    s = lambda **kw: {"title": "", "supplier_title": "", "category": "", "tags": "", "supplier_description": "", "description": "",
                      **{f"mf.{k}": "" for k in ("fit", "silhouette", "length", "style", "closure", "material", "fabric", "pattern", "best_for")}, **kw}
    # precedence: reliable title beats a noisy metafield; the conflict is preserved, the attribute stays usable
    r = resolve("leg_cut", _hits("leg_cut", s(title="High Rise Wide Leg Pants", **{"mf.silhouette": "Skinny"})))
    assert r["values"] == ["wide_leg"] and r["status"] == "explicit" and r["overridden"][0]["value"] == "skinny"
    # a metafield value known to be a default is ignored, not trusted
    assert resolve("leg_cut", _hits("leg_cut", s(**{"mf.silhouette": "Straight"})))["status"] == "unknown"
    # disagreement inside the deciding tier stays unresolved -> unknown for selection
    r = resolve("leg_cut", _hits("leg_cut", s(title="Wide Leg Pants", supplier_title="Barrel Woven Pants")))
    assert r["status"] == "unresolved" and value({"leg_cut": r}, "leg_cut") is None
    # stretch: stated only; elastane percentage alone gives no degree
    assert resolve("stretch", _hits("stretch", s(description="97% Cotton, 3% Spandex.")))["status"] == "unknown"
    assert resolve("stretch", _hits("stretch", s(supplier_description="Stretch: Slightly stretchy")))["values"] == ["slight"]
    # vision stays inferred, and cannot override text
    r = resolve("neckline", _hits("neckline", s(), [("vision", "photo", "scoop")]))
    assert r["status"] == "inferred" and r["method"] == "vision"
    assert resolve("neckline", _hits("neckline", s(title="V-Neck Top"), [("vision", "photo", "scoop")]))["values"] == ["v_neck"]
    # styling advice never describes this product
    assert resolve("leg_cut", _hits("leg_cut", s(description=clean("Soft tee. Pair it with wide leg jeans."))))["status"] == "unknown"
    # requests: ordering only where the scale has one; unknown anchors ask; edges are explicit
    assert check_pref({"attribute": "neckline", "op": "lt", "anchor": {"values": ["v_neck"]}}) == "no_order"
    assert check_pref({"attribute": "rise", "op": "lt", "anchor": {"values": []}}) == "anchor_unknown"
    assert check_pref({"attribute": "sleeve_length", "op": "gt", "anchor": {"values": ["long"]}}) == "edge_of_scale"
    assert satisfies({"rise": {"values": ["mid"], "status": "explicit"}}, {"attribute": "rise", "op": "lt", "anchor": {"values": ["high"]}}) is True
    assert satisfies({"rise": {"values": ["mid"], "status": "unresolved"}}, {"attribute": "rise", "op": "eq", "value": "mid"}) is None
    assert satisfies({"leg_cut": {"values": ["flare"], "status": "explicit"}}, {"attribute": "leg_cut", "op": "lt", "anchor": {"values": ["wide_leg"]}}) is False
    # tag-only labels never establish a strict match
    assert satisfies({"rise": {"values": ["mid"], "status": "inferred", "basis": "tags"}}, {"attribute": "rise", "op": "eq", "value": "mid"}) is None
    print("attributes self-check ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "build":
        prov, recs = build(*(sys.argv[2:4] or []))
        from collections import Counter
        print(prov["sha256"][:12], Counter(r["category"] for r in recs.values()))
        for c in ("pants", "tops", "dresses"):
            for a in V["integrated"] + ["silhouette", "dress_length"]:
                st = Counter(r["attributes"][a]["status"] for r in recs.values() if r["category"] == c and a in r["attributes"])
                if st: print(f"  {c:8} {a:14} {dict(st)}")
    selfcheck()
