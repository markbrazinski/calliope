"""Price reconciliation at the merchant handoff (development stub, merchant mocked: no network, no paid calls).

    .venv/bin/python tests/check_handoff.py

Checked: unchanged prices open the cart at once; an increase or a decrease withholds the cart until the shopper accepts
that exact quote; an increase over the budget needs a revision or an explicit budget raise (never bypassed); an
unavailable variant makes no cart; a size change after confirming invalidates the confirmation; the shopper's own top is
never sent; a product-ID mismatch and a missing price are named problems.
"""
import sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
import check_revisions as C  # noqa: E402

A = C.A
PRICE, GONE, IDS = {}, set(), {}          # productId -> live price (USD); variant ids sold out; productId -> merchant id override


def merchant(handle):
    it = next(i for i in A.IDX if i["handle"] == handle)
    vs = []
    for v in it["variants"]:
        vid = int(v["sku"][-12:]) if v["sku"][-12:].isdigit() else abs(hash(v["sku"])) % 10**12
        price = PRICE.get(it["id"], v["price_usd"])
        vs.append({"id": vid, "title": v["title"], "available": v["in_stock"] and vid not in GONE, "price": None if price is None else int(round(price * 100)),
                   "option1": v["options"].get("Color"), "option2": v["options"].get("Size"), "option3": None})
    return {"id": IDS.get(it["id"], int(it["id"])), "options": [{"name": "Color"}, {"name": "Size"}], "variants": vs}, time.time()


def sized(s):
    v = A._cur(s)
    for sl in A.buyable(A.shown(v["composition"], v["previewJobId"])):
        sizes, un = A.sizes_of(v["composition"][sl])
        if sizes: A.set_size(s, sl, next(z for z in sizes if z not in un))
    return v


def at_shown(s):
    """Live prices equal to what Calliope shows for the chosen variants."""
    PRICE.clear(); GONE.clear(); IDS.clear()
    for sl, it in A._cur(s)["composition"].items():
        if not it.get("owned"): PRICE[it["id"]] = it["price"]


def main():
    tmp = Path(tempfile.mkdtemp())
    A.configure(tmp / "app", provider="stub", nlu="stub", stub_delay=0, engine_dir=tmp / "engine")
    A.merchant = merchant
    s = C.journey({"occasion": "dinner with friends", "budget": 250, "heels": True, "structure": "separates"}, owned=True)
    v = sized(s); vid = v["versionId"]; comp = v["composition"]
    bottom = comp["bottom"]

    at_shown(s); h = A.handoff(s, vid)                                       # 1. unchanged
    assert h["cartUrl"] and not h["priceChanges"] and not h["needsConfirmation"] and h["ownedNotSent"] == ["top"], h
    assert h["subtotalNow"] == h["subtotalShown"] and h["currency"] == "USD" and "top" not in [i["slot"] for i in h["items"]]
    print("unchanged prices: cart opens at once; own top not sent; subtotal", h["subtotalNow"])

    PRICE[bottom["id"]] = round(bottom["price"] + 5, 2); h = A.handoff(s, vid)          # 2. an increase within budget
    assert not h["cartUrl"] and h["needsConfirmation"] and h["priceChanges"][0]["delta"] == 5 and not h["budgetConflict"], h
    assert h["subtotalNow"] == round(h["subtotalShown"] + 5, 2)
    ok = A.handoff(s, vid, accept_quote=h["quote"])
    assert ok["cartUrl"] and not ok["needsConfirmation"], ok
    print("increase: cart withheld, then opened after accepting quote", h["quote"], f"({h['subtotalShown']} -> {h['subtotalNow']})")

    PRICE[bottom["id"]] = round(bottom["price"] - 3, 2); h = A.handoff(s, vid)          # 3. a decrease also needs an OK
    assert not h["cartUrl"] and h["needsConfirmation"] and h["priceChanges"][0]["delta"] == -3, h
    stale = A.handoff(s, vid, accept_quote=ok["quote"])                                   # the earlier (higher) quote no longer applies
    assert not stale["cartUrl"] and stale["confirmationExpired"], stale
    assert A.handoff(s, vid, accept_quote=h["quote"])["cartUrl"]
    print("decrease: cart withheld until accepted; the earlier quote is refused as expired")

    limit = s["brief"]["budgetNew"]                                                       # 4. an increase over the budget
    PRICE[bottom["id"]] = round(bottom["price"] + (limit - h["subtotalShown"]) + 20, 2)
    h = A.handoff(s, vid)
    bc = h["budgetConflict"]
    assert bc and bc["over"] > 0 and not h["cartUrl"], h
    acc = A.handoff(s, vid, accept_quote=h["quote"])                                      # accepting the price alone isn't enough
    assert not acc["cartUrl"] and acc["budgetConflict"] and s["brief"]["budgetNew"] == limit, acc
    try:
        A.handoff(s, vid, accept_quote=h["quote"], raise_budget_to=h["subtotalNow"] - 1); raise AssertionError("raise below the subtotal accepted")
    except ValueError: pass
    raised = A.handoff(s, vid, accept_quote=h["quote"], raise_budget_to=h["subtotalNow"])
    assert raised["cartUrl"] and s["brief"]["budgetNew"] >= h["subtotalNow"] and s["brief"]["budgetMode"] == "firm", raised
    assert any(e["event"] == "budget_raised" for e in s["events"])
    print(f"over budget: ${bc['over']} over; price acceptance alone refused; budget raised explicitly to ${s['brief']['budgetNew']} -> cart")

    at_shown(s); PRICE[bottom["id"]] = round(bottom["price"] + 2, 2)                     # 5. a selection change after confirming
    h = A.handoff(s, vid)
    alt = next(((sl, z) for sl, it in comp.items() if not it.get("owned") for z in (lambda su: [x for x in su[0] if x not in su[1] and x != s["sizes"].get(sl)])(A.sizes_of(it))), None)
    if alt:
        A.set_size(s, *alt)
        moved = A.handoff(s, vid, accept_quote=h["quote"])
        assert not moved["cartUrl"] and moved["confirmationExpired"] and moved["quote"] != h["quote"], moved
        print(f"selection change: {alt[0]} size {alt[1]} after confirming -> confirmation expired, new quote required")
    r = A.request_change(s, "darker shoes")                                               # another outfit version
    if r["status"] == "open":
        A.accept(s, r["revisionId"], r["candidates"][0]["candidateId"]); C.wait(A._cur(s)["previewJobId"]); v2 = sized(s)
        at_shown(s); moved = A.handoff(s, v2["versionId"], accept_quote=h["quote"])
        assert moved["cartUrl"] and not moved["confirmationExpired"], moved              # unchanged prices need no OK on the new version...
        PRICE[v2["composition"]["shoes"]["id"]] = round(v2["composition"]["shoes"]["price"] + 1, 2)
        moved = A.handoff(s, v2["versionId"], accept_quote=h["quote"])                   # ...and the old version's quote never confirms it
        assert not moved["cartUrl"] and moved["confirmationExpired"], moved
        print("selection change: a new outfit version -> the earlier version's confirmation doesn't apply")
        A.choose_version(s, 0)
    assert alt or r["status"] == "open", "no selection change could be made"

    at_shown(s); GONE.add(next(i["variantId"] for i in A.handoff(s, vid)["items"] if i["slot"] == "bottom"))   # 6. unavailable variant
    h = A.handoff(s, vid)
    assert not h["cartUrl"] and h["unavailable"] == ["bottom"], h
    print("unavailable variant: no cart; named:", h["unavailable"])

    at_shown(s); IDS[bottom["id"]] = 1; h = A.handoff(s, vid)                            # 7. identity and price checks
    assert not h["cartUrl"] and any("merchant product" in p for p in h["problems"]), h
    at_shown(s); PRICE[bottom["id"]] = None; h = A.handoff(s, vid)
    assert not h["cartUrl"] and any("current price" in p for p in h["problems"]), h
    print("identity mismatch and missing price: no cart, named problems")
    print("handoff checks ok")


if __name__ == "__main__":
    main()
