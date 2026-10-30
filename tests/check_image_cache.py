"""Image-dependent caches (engine.product_ref, render_look; development stub, a fake CDN: no network, no paid calls).

    .venv/bin/python tests/check_image_cache.py

Checked, by counting provider submissions in the ledger: an unchanged image reuses every stage (also after re-validation
with the CDN); new bytes at the same URL re-render that stage and every stage after it, and keep the ones before; a new URL
with the same bytes reuses; a new URL with new bytes re-renders; a changed first garment re-renders the whole chain; old
reference files are never overwritten; the picture-job identity follows the image; a stage made before fingerprints is
reused only when its saved reference is byte-identical, and never relabelled otherwise.
"""
import io, json, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import shopper as A  # noqa: E402
import engine as R  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

CDN, CALLS = {}, []                    # url -> (bytes, etag)


def img(color):
    b = io.BytesIO(); Image.new("RGB", (400, 600), color).save(b, "JPEG", quality=95); return b.getvalue()


def fake_fetch(url, meta):
    data, etag = CDN[url]; CALLS.append(url)
    return (None, etag, None) if meta.get("etag") == etag else (data, etag, None)


def subs():
    return len(R.jl("ledger.json", []))


def main():
    tmp = Path(tempfile.mkdtemp())
    A.configure(tmp / "app", provider="stub", stub_delay=0, engine_dir=tmp / "engine")
    R._fetch_ref = fake_fetch
    im = Image.new("RGB", (700, 1200), (170, 165, 160)); ImageDraw.Draw(im).ellipse([300, 80, 400, 200], fill=(200, 160, 130))
    photo = tmp / "photo.jpg"; im.save(photo, "JPEG")
    fb = {"box": [294, 72, 406, 192], "image_size": [700, 1200], "source": "stub"}
    pick = lambda sl: next(i for i in R.INDEX if i["slot"] == sl and i["in_stock"] and i.get("tryon_image"))
    look = {sl: {k: pick(sl).get(k) for k in A.KEEP} for sl in ("top", "bottom", "shoes", "necklace")}
    colors = {"top": (200, 40, 40), "bottom": (40, 40, 200), "shoes": (40, 160, 40), "necklace": (220, 190, 60)}
    for sl, it in look.items():
        CDN[R.ref_url(it)] = (img(colors[sl]), f"e-{sl}-1")
    render = lambda: R.render_look("pcheck", photo, look, fb, tuck="untucked")   # the cloth chain (the tuck edit: tests/check_tuck.py)
    keys = lambda steps: {x["slot"]: x["stage"] for x in steps}

    n0 = subs(); s1 = render(); n1 = subs()
    assert n1 - n0 == 4, n1 - n0                                             # trousers, top, shoes, necklace
    first_refs = {p.name for p in R.OUT.glob("ref-*.jpg")}
    s2 = render(); assert subs() == n1 and keys(s2) == keys(s1)
    R.REF_TTL = 0; R._REFS.clear(); c0 = len(CALLS)
    s2 = render(); assert subs() == n1 and keys(s2) == keys(s1) and len(CALLS) - c0 == 4   # re-validated (304), all reused
    print("unchanged images: 4 stages made once; re-render and re-validation reuse all 4 (0 submissions)")

    jid_before = R.ref_ids(look)
    u = R.ref_url(look["shoes"]); CDN[u] = (img((10, 90, 10)), "e-shoes-2")        # new bytes at the same URL
    s3 = render(); n3 = subs()
    assert n3 - n1 == 2, n3 - n1                                             # shoes + necklace (downstream); top and trousers kept
    assert keys(s3)["top"] == keys(s1)["top"] and keys(s3)["bottom"] == keys(s1)["bottom"] and keys(s3)["shoes"] != keys(s1)["shoes"]
    assert R.ref_ids(look) != jid_before, "a picture-job identity follows the image"
    assert first_refs <= {p.name for p in R.OUT.glob("ref-*.jpg")}, "old reference files are never overwritten"
    print("same URL, new bytes: shoes and necklace re-rendered (2), top and trousers reused; job identity changed; old refs kept")

    old_url = look["shoes"]["tryon_image"]
    look["shoes"]["tryon_image"] = old_url + ("&" if "?" in old_url else "?") + "v=moved"
    CDN[R.ref_url(look["shoes"])] = (CDN[u][0], "e-moved")                   # new URL, the same bytes
    s4 = render(); assert subs() == n3 and keys(s4) == keys(s3)
    CDN[R.ref_url(look["shoes"])] = (img((0, 60, 0)), "e-moved-2")           # new URL, new bytes
    s5 = render(); n5 = subs(); assert n5 - n3 == 2
    print("new URL, same bytes: reused (0); new URL, new bytes: re-rendered (2)")

    CDN[R.ref_url(look["bottom"])] = (img((20, 20, 150)), "e-bottom-2")      # the first garment: everything after it depends on it
    s6 = render(); n6 = subs(); assert n6 - n5 == 4 and all(keys(s6)[k] != keys(s5)[k] for k in ("top", "bottom", "shoes")), n6 - n5
    print("first garment changed: the whole chain re-rendered (4)")

    # stages made before fingerprints (keyed by product ID only): convert this state to that shape, then render again
    st = R.jl("stages.json", {}); old, lineage = {}, f"photo:{__import__('hashlib').sha256(photo.read_bytes()).hexdigest()[:16]}"
    for step in s6[:-1]:
        lk = R.skey(lineage, step["slot"], step["productId"], step["category"]); old[lk] = st.pop(step["stage"]); lineage = f"step:{lk}"
    box = R.neck_box(fb, Image.open(R.OUT / s6[-2]["output"]).size)
    old[R.skey(R.skey(lineage, "neck-crop", box), "necklace", s6[-1]["productId"])] = st.pop(s6[-1]["stage"])
    st.update(old); R.js("stages.json", st)
    for sl, it in look.items():
        (R.OUT / f"ref-{it['id']}.jpg").write_bytes(R.product_ref(it)[0].read_bytes())    # the legacy reference file
    s7 = render(); n7 = subs()
    assert n7 == n6 and all(x.get("adoptedFrom") for x in s7), [x.get("adoptedFrom") for x in s7]
    print("pre-fingerprint stages with byte-identical references: adopted (0 submissions, marked adoptedFrom)")
    st = R.jl("stages.json", {}); [st.pop(x["stage"]) for x in s7]; st.update(old); R.js("stages.json", st)
    (R.OUT / f"ref-{look['shoes']['id']}.jpg").write_bytes(img((1, 2, 3)))  # this legacy picture used a different image
    s8 = render(); n8 = subs()
    assert n8 - n7 == 2 and s8[0].get("adoptedFrom") and s8[1].get("adoptedFrom") and not s8[2].get("adoptedFrom"), n8 - n7
    print("a pre-fingerprint stage whose reference differs: not reused; it and the necklace re-rendered (2)")
    print("image cache checks ok")


if __name__ == "__main__":
    main()
