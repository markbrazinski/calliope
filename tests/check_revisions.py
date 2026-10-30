"""General change requests on the normal product path (not the filmed sentence), then the bag.

    .venv/bin/python tests/check_revisions.py            # development stub, rules interpreter: no paid calls, merchant mocked
    CALLIOPE_NLU=nova .venv/bin/python tests/check_revisions.py   # the same requests through the live Nova interpreter (cents)

For each request: what was understood, the status, which slots changed. Checked: non-target pieces keep their exact
product records; totals and the candidate's preview are recomputed; nothing changes until the option is kept; an
impossible request is said plainly (never silently reinterpreted); ambiguous wording asks. Then the bag: every piece
resolves to the merchant variant in exactly the pictured colour and the chosen size, the shopper's own top is never
sent, and a product that left the catalog is reported instead of breaking the session.
"""
import io, json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import shopper as A  # noqa: E402
import engine as R  # noqa: E402
import palette as P  # noqa: E402

L = lambda h: P.hex_to_oklch(h)[0]


def photo(color=(150, 140, 130)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (700, 1200), color); ImageDraw.Draw(im).ellipse([300, 80, 400, 200], fill=(200, 160, 130)); b = io.BytesIO(); im.save(b, "JPEG"); return b.getvalue()


def wait(jid):
    for _ in range(600):
        if A.S["jobs"][jid]["status"] != "running": return
        time.sleep(0.05)


def journey(brief, owned=False):
    tok, _ = A.new_session(); s = A.owner(tok)
    A.upload_photo(s, photo(), consent=True)
    if owned:
        A.upload_owned_top(s, photo((40, 120, 60)), "my green silk blouse"); A.confirm_owned_top(s, "blouse", "green")
    A.journey_start(s, brief)
    for _ in range(200):
        if s["journey"]["analysis"]["status"] != "running": break
        time.sleep(0.05)
    A.journey_palette(s, "No palette")
    jr = s["journey"]
    for lid in jr["round1"]: wait(jr["jobs"][lid])
    A.journey_keep(s, 1, "A"); wait(jr["jobs"]["C"]); A.journey_keep(s, 2, "A")
    A.journey_finalize(s, []); wait(jr["jobs"]["P2"]); A.journey_choose(s, "A"); wait(A._cur(s)["previewJobId"])
    return s


def ask(s, text, expect, answer=None, accept=False):
    base = A._cur(s); comp0 = json.loads(json.dumps(base["composition"]))
    r = A.request_change(s, text)
    if answer and r["status"] == "needs_clarification":
        r = A.answer(s, r["revisionId"], next(o for o in r["clarification"]["options"] if answer.lower() in o.lower()))
    assert A._cur(s)["versionId"] == base["versionId"] and A._cur(s)["composition"] == comp0, "nothing changes before an option is kept"
    ok = r["status"] in expect
    line = f"  {text!r:52} -> {r['status']:19} understood={r['requested'] or (r.get('clarification') or {}).get('question') or r.get('message')}"
    for c in r["candidates"]:
        comp = next(x for x in A.S["sessions"][s["sessionId"]]["revisions"][r["revisionId"]]["candidates"] if x["candidateId"] == c["candidateId"])["composition"]
        for sl in comp0:
            if sl in comp and sl not in c["changedSlots"]:
                assert comp[sl] == comp0[sl], f"{sl} kept its exact record"
        assert abs(c["subtotal"] - A.subtotal(A.shown(comp, None, s.get("ctx")))) < 0.01, "total recomputed"
        assert c["previewJob"] or c["kind"] == "alternative", "a matching option's picture starts at once (an alternative's when kept)"
        line += f" | {c['kind']}: changed {c['changedSlots']} ${c['priceDelta']:+}"
    print(line)
    assert ok, (text, r["status"], r)
    ms = [c for c in r["candidates"] if c["kind"] == "match"]
    if r["status"] == "open":                    # a new comparison: distinct options, never padded; a single one is said to be the only one
        assert 1 <= len(ms) <= 2 and len({c["compositionId"] for c in ms}) == len(ms) and all(c["compositionId"] != base["compositionId"] for c in ms), ms
        assert (len(ms) == 1) == bool(r.get("onlyOption")), (len(ms), r.get("onlyOption"))
    if accept and r["candidates"]:
        c = r["candidates"][-1 if accept == "last" else 0]
        A.accept(s, r["revisionId"], c["candidateId"]); wait(A._cur(s)["previewJobId"])
        assert A._cur(s)["compositionId"] == c["compositionId"]
    elif r["status"] not in ("accepted",):
        A.dismiss(s, r["revisionId"])
    return r


def merchant_mock():
    """The merchant's .js answers from the active catalog: options named, variant ids derived from the SKU (offline)."""
    def m(handle):
        it = next((i for i in A.IDX if i["handle"] == handle), None)
        if not it:
            import urllib.error
            raise urllib.error.HTTPError(handle, 404, "Not Found", {}, None)
        vs = [{"id": int(v["sku"][-12:]) if v["sku"][-12:].isdigit() else abs(hash(v["sku"])) % 10**12, "title": v["title"], "available": v["in_stock"], "price": int(v["price_usd"] * 100),
               "option1": v["options"].get("Color"), "option2": v["options"].get("Size"), "option3": None} for v in it["variants"]]
        return {"id": int(it["id"]), "options": [{"name": "Color"}, {"name": "Size"}], "variants": vs}, time.time()
    return m


def main():
    tmp = Path(tempfile.mkdtemp())
    nlu = os.environ.get("CALLIOPE_NLU")
    A.configure(tmp / "app", provider="stub", nlu=nlu or "stub", stub_delay=0, engine_dir=tmp / "engine",
                nova_usd=float(os.environ.get("CALLIOPE_NOVA_USD") or 0.01))
    if nlu == "nova":
        from nova_common import Nova
        A.NOVA = Nova(cap_usd=float(os.environ.get("CALLIOPE_NOVA_USD") or 0.01), log_path=ROOT / "state/engine/state/nova_calls.jsonl"); A.NOVA.mode = "nova"
    print(f"interpreter: {nlu or 'rules (stub)'}")

    print("separates with a catalog top, budget $250:")
    s = journey({"occasion": "dinner with friends", "budget": 250, "heels": True, "structure": "separates"})
    r = ask(s, "darker and tighter trousers", {"open", "no_match", "cannot"}, answer="slimmer cut")
    assert r["status"] != "cannot" or "darker" in (r.get("suggestion") or ""), "the darker half is kept after the clarification"
    r = ask(s, "darker and tighter trousers", {"open", "no_match", "cannot", "size_change"}, answer="smaller size", accept=True)
    assert "darker trousers" in r["requested"] and r["notes"] and "size" in r["notes"][0], "a smaller size, and still darker trousers"
    r = ask(s, "darker trousers", {"open", "no_match"})                  # no_match: nothing darker in the same colour family
    if r["status"] == "no_match":
        alt = r["noMatch"]["alternative"]
        assert "same colour family" in r["noMatch"]["message"] and (alt is None or "different colour" in alt["label"]), r["noMatch"]
        assert not [c for c in r["candidates"] if c["kind"] == "match"], "a colour change is never presented as a match"
    # the shopper keeps option 2 of a two-option comparison; then the catalog is narrowed until one option is left
    two = None
    for text in ("wide leg trousers", "darker trousers", "a lighter top", "darker shoes", "a v-neck top"):
        r = A.request_change(s, text); A.dismiss(s, r["revisionId"])
        if len([c for c in r["candidates"] if c["kind"] == "match"]) == 2: two = text; break
    assert two, "some request offers two options"
    slot = next(c for c in r["candidates"] if c["kind"] == "match")["changedSlots"][0]
    comp0 = json.loads(json.dumps(A._cur(s)["composition"]))
    r = ask(s, two, {"open"}, accept="last")
    now = A._cur(s)["composition"]
    assert all(now[sl] == comp0[sl] for sl in comp0 if sl != slot), "non-target pieces kept after choosing option 2"
    keep_idx = list(A.IDX); gone = set()
    for _ in range(60):
        r = A.request_change(s, two); A.dismiss(s, r["revisionId"])
        ms = [c for c in s["revisions"][r["revisionId"]]["candidates"] if c["kind"] == "match"]
        if len(ms) < 2: break
        gone.add(ms[1]["composition"][slot]["id"]); A.use_catalog({"items": [i for i in keep_idx if i["id"] not in gone], "attributes": R._ATTRS})
    r = ask(s, two, {"open", "no_match"})
    assert r["status"] == "no_match" or (len(r["candidates"]) == 1 and r["onlyOption"]), r
    print(f"  kept option 2 of {two!r}; with {len(gone)} rivals removed ->", r["status"], "|", r.get("onlyOption") or r["noMatch"]["message"])
    A.use_catalog({"items": keep_idx, "attributes": R._ATTRS})
    ask(s, "wide leg trousers", {"open", "no_match"})
    ask(s, "a lighter top", {"open", "no_match", "cannot"})
    ask(s, "darker shoes", {"open", "no_match", "cannot"})
    ask(s, "a v-neck top with long sleeves", {"open", "no_match"})
    ask(s, "low rise jeans with a bootcut leg", {"no_match", "open", "needs_clarification"})
    ask(s, "something nicer", {"unsupported"})
    ask(s, "a lower neckline", {"needs_clarification", "cannot"})
    r = ask(s, "make them tighter", {"needs_clarification"})
    print("separates, firm budget at the current subtotal (any pricier piece breaks it):")
    t = journey({"occasion": "dinner with friends", "budget": 250, "heels": True, "structure": "separates"})
    t["brief"]["budgetNew"] = A.subtotal(A._cur(t)["composition"]); t["brief"]["budgetMode"] = "firm"
    for text in ("darker trousers", "wide leg trousers", "a v-neck top"):
        r = ask(t, text, {"open", "no_match", "cannot"})
        assert all(c["subtotal"] <= t["brief"]["budgetNew"] + 0.01 for c in r["candidates"] if c["kind"] == "match"), "never over a firm budget"
        if r["status"] == "no_match": assert "budget" in r["noMatch"]["message"], r["noMatch"]
    print("a dress, own top kept aside:")
    d = journey({"occasion": "wedding guest", "budget": 0, "heels": True, "structure": "dress"})
    ask(d, "a longer dress", {"open", "no_match", "cannot"})
    ask(d, "darker trousers", {"cannot"})
    ask(d, "separates instead", {"open", "no_match"})

    # the bag: exact colour + chosen size; owned top never sent; a product that left the catalog is reported
    A.merchant = merchant_mock()
    o = journey({"occasion": "dinner with friends", "budget": 0, "heels": True, "structure": "separates"}, owned=True)
    v = A._cur(o)
    for sl in A.buyable(A.shown(v["composition"], v["previewJobId"])):
        sizes, un = A.sizes_of(v["composition"][sl])
        if sizes: A.set_size(o, sl, next(z for z in sizes if z not in un))
    h = A.handoff(o, v["versionId"])
    assert h["cartUrl"] and not h["problems"] and h["ownedNotSent"] == ["top"] and h["agree"], h
    for it in h["items"]:
        c = v["composition"][it["slot"]]
        col, size = it["variantTitle"].split(" / ")[0], it["size"]
        assert col == c["pictured_color"] or (not c["pictured_color"]), (it, c["pictured_color"])
        assert not size or it["variantTitle"].endswith(size), it
    print("bag:", h["cartUrl"][:60] + "…", [(i["slot"], i["variantTitle"]) for i in h["items"]], "| own top not sent:", h["ownedNotSent"])
    # a pictured "Blue" never resolves to "Light Blue"
    fake = {"id": 1, "options": [{"name": "Color"}, {"name": "Size"}], "variants": [{"id": 1, "title": "Light Blue / S", "option1": "Light Blue", "option2": "S"},
            {"id": 2, "title": "Blue / XS", "option1": "Blue", "option2": "XS"}, {"id": 3, "title": "Blue / S", "option1": "Blue", "option2": "S"}]}
    assert A.variant_for(fake, "Blue", "S")["id"] == 3 and A.variant_for(fake, "Blue", "XS")["id"] == 2 and A.variant_for(fake, "Red", "S") is None
    # a product leaves the catalog: the saved look still renders, the bag names it
    gone = v["composition"]["bottom"]["id"]
    doc = {"items": [i for i in A.IDX if i["id"] != gone], "attributes": R._ATTRS}
    A.use_catalog(doc)
    sv = A.session_view(o)
    assert any(i.get("noLongerSold") for i in sv["versions"][-1]["items"]), "a removed piece is marked, the look still shows"
    h = A.handoff(o, v["versionId"])
    assert not h["cartUrl"] and any("no longer" in p for p in h["problems"]), h
    print("removed product: look still shows (marked no longer sold); bag says:", h["problems"])
    print("revision checks ok")


if __name__ == "__main__":
    main()
