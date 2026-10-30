"""Nothing unbuyable is offered (shopper._sellable; development stub, the store mocked: no network, no paid calls).

    .venv/bin/python tests/check_sellable.py

Checked: a product the live store no longer has (404), and one with no variant available in its pictured colour, never
appear in the first looks or in a change request's options; the selection simply moves on to buyable products.
"""
import sys, tempfile, time, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
import check_revisions as C  # noqa: E402

A = C.A
GONE, SOLD_OUT = set(), set()


def merchant(handle):
    it = next(i for i in A.IDX if i["handle"] == handle)
    if it["id"] in GONE:
        raise urllib.error.HTTPError(handle, 404, "Not Found", {}, None)
    vs = [{"id": n + 1, "title": v["title"], "available": v["in_stock"] and it["id"] not in SOLD_OUT, "price": int(v["price_usd"] * 100),
           "option1": v["options"].get("Color"), "option2": v["options"].get("Size"), "option3": None} for n, v in enumerate(it["variants"])]
    return {"id": int(it["id"]), "options": [{"name": "Color"}, {"name": "Size"}], "variants": vs}, time.time()


def ids(s):
    return {it.get("id") for l in s["journey"]["looks"].values() for it in l["look"].values() if isinstance(it, dict)}


def main():
    tmp = Path(tempfile.mkdtemp())
    A.configure(tmp / "app", provider="stub", nlu="stub", stub_delay=0, engine_dir=tmp / "engine")
    A.merchant = merchant
    brief = {"occasion": "dinner with friends", "budget": 250, "heels": True, "structure": "separates"}
    s = C.journey(brief)
    r1 = {sl: it["id"] for sl, it in s["journey"]["looks"]["A"]["look"].items() if isinstance(it, dict) and not it.get("owned")}
    GONE.add(r1["bottom"]); SOLD_OUT.add(r1["shoes"]); A._UNSELLABLE.clear()
    s2 = C.journey(brief)
    assert not ids(s2) & {r1["bottom"], r1["shoes"]}, "a gone or sold-out product is never offered"
    print("first looks: the trousers the store dropped and the shoes with nothing available are replaced")
    r = A.request_change(s2, "darker shoes")
    offered = {c["items"][[i["slot"] for i in c["items"]].index("shoes")]["productId"] for c in r["candidates"] if any(i["slot"] == "shoes" for i in c["items"])}
    SOLD_OUT.update(offered); A._UNSELLABLE.clear(); A.dismiss(s2, r["revisionId"])
    r2 = A.request_change(s2, "darker shoes")
    again = {c["items"][[i["slot"] for i in c["items"]].index("shoes")]["productId"] for c in r2["candidates"] if any(i["slot"] == "shoes" for i in c["items"])}
    assert not again & offered, (offered, again)
    print(f"change request: options that became unbuyable are replaced ({len(offered)} out, now {r2['status']})")
    print("sellable checks ok")


if __name__ == "__main__":
    main()
