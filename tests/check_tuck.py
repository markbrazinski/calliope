"""Tucked or untucked (data/tuck_rule.json, engine.tuck_decision, render_look, tuck_edit; stub pictures, a fake CDN: no
network, no paid calls).

    .venv/bin/python tests/check_tuck.py

Checked: the rule picks tucked for a full, fluid, hip-length top with high-rise trousers and untucked for a cropped one;
unknown facts fall to the default; untucked = trousers then top; tucked = top, trousers, then one YouCam image-edit task
that tucks it in (instruction names the bottom; output size keeps the aspect within the provider's limits); the necklace
uses the face found on the edited picture (the original box scaled when none is found); a repeat costs nothing;
when the edit fails the untucked picture is kept and says so; the shopper's own top goes through the same edit.
"""
import io, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import shopper as A  # noqa: E402
import engine as R  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

CDN = {}


def img(color, size=(400, 600)):
    b = io.BytesIO(); Image.new("RGB", size, color).save(b, "JPEG", quality=95); return b.getvalue()


def subs():
    return len(R.jl("ledger.json", []))


def main():
    tmp = Path(tempfile.mkdtemp())
    A.configure(tmp / "app", provider="stub", stub_delay=0, engine_dir=tmp / "engine")
    stub = R.yc.__self__
    R._fetch_ref = lambda url, meta: (None, "e", None) if meta.get("etag") == "e" else (CDN.setdefault(url, img((len(CDN) * 40 % 255, 90, 160))), "e", None)
    im = Image.new("RGB", (700, 1200), (170, 165, 160)); ImageDraw.Draw(im).ellipse([300, 80, 400, 200], fill=(200, 160, 130))
    photo = tmp / "photo.jpg"; im.save(photo, "JPEG")
    fb = {"box": [294, 72, 406, 192], "image_size": [700, 1200], "source": "stub"}
    high = next(i for i in R.INDEX if i["slot"] == "bottom" and R.AT.value(R.attrs_of(i), "rise") == ["high"] and i.get("tryon_image"))
    mid = next(i for i in R.INDEX if i["slot"] == "bottom" and R.AT.value(R.attrs_of(i), "rise") == ["mid"] and i.get("tryon_image"))
    pick = lambda sl: next(i for i in R.INDEX if i["slot"] == sl and i["in_stock"] and i.get("tryon_image"))
    full_top = {"slot": "top", "fit": {"length": "hip", "volume": "high", "fabric_weight": "fluid"}}
    cropped = {"slot": "top", "fit": {"length": "cropped", "volume": "fitted", "fabric_weight": "medium"}}

    d = R.tuck_decision(full_top, high, "smart"); assert d["tuck"] == "tucked" and d["rule"] == 0, d
    d = R.tuck_decision(cropped, mid, None); assert d["tuck"] == "untucked", d
    d = R.tuck_decision({"slot": "top"}, None, None); assert d["tuck"] == R.TUCK["default"] and d["rule"] is None, d
    print("rule: full fluid hip-length top + high rise -> tucked (rule 0); cropped -> untucked; unknown -> default", R.TUCK["default"])
    assert R._edit_size(2731, 4096, 2016) == "1344*2016" and R._edit_size(4096, 2731, 2016) == "2016*1344"

    look = {sl: {k: pick(sl).get(k) for k in A.KEEP} for sl in ("top", "shoes", "necklace")}
    look["bottom"] = {k: high.get(k) for k in A.KEEP}
    order = lambda steps: [x["slot"] for x in steps]
    R._face_on = lambda p: dict(fb, source="test")

    n0 = subs(); s1 = R.render_look("ptuck", photo, look, fb, tuck="untucked")
    assert order(s1) == ["bottom", "top", "shoes", "necklace"] and subs() - n0 == 4, (order(s1), subs() - n0)
    n1 = subs(); s2 = R.render_look("ptuck", photo, look, fb, tuck="tucked")
    assert order(s2) == ["top", "bottom", "tuck", "shoes", "necklace"] and subs() - n1 == 5, (order(s2), subs() - n1)
    t = next(x for x in s2 if x["slot"] == "tuck")
    task = next(v for v in stub.tasks.values() if "image-to-image" in v["endpoint"])
    assert t["tuck"]["method"] == "youcam image edit" and task["body"]["model"] == R.TUCK["edit"]["model"] and "trousers" in task["body"]["prompt"], task["body"]
    assert s2[3]["parent"] == t["output"] and s2[-1].get("status") != "not_shown", s2[-2:]
    print("order: untucked = trousers then top (4 tasks); tucked = top, trousers, tuck edit, shoes, necklace (5 tasks)")
    n2 = subs(); R.render_look("ptuck", photo, look, fb, tuck="tucked"); assert subs() == n2, "a repeat costs nothing"
    print("repeat: 0 submissions")

    R._face_on = lambda p: None
    look["bottom"] = {k: mid.get(k) for k in A.KEEP}
    s3 = R.render_look("ptuck", photo, look, fb, tuck="tucked")
    assert s3[-1].get("status") != "not_shown" and s3[-1]["slot"] == "necklace", s3[-1]
    print("no face found on the edited picture: the original face box, scaled, places the necklace")

    stub.fail.add("image-to-image"); R._face_on = lambda p: dict(fb)
    look["bottom"] = {k: next(i for i in R.INDEX if i["slot"] == "bottom" and i.get("tryon_image") and i["id"] not in (high["id"], mid["id"])).get(k) for k in A.KEEP}   # new trousers: no cached edit
    s4 = R.render_look("ptuck", photo, look, fb, tuck="tucked")
    assert order(s4)[:3] == ["top", "bottom", "shoes"] and s4[1]["tuck"]["tucked"] is False and s4[1].get("tuckNote"), s4[1]
    print("edit fails: the untucked picture is kept and says so:", s4[1]["tuckNote"])
    stub.fail.discard("image-to-image")

    # the shopper's own top: its shared layer is the base; the trousers and the edit follow
    own_path = tmp / "own.jpg"; own_path.write_bytes(img((30, 60, 180), (600, 600)))
    look["top"] = {"slot": "top", "owned": True, "ownedGarmentId": "own_test", "hex": "#1e3cb4", "price": 0, "fit": full_top["fit"]}
    k = R.skey(f"photo:{__import__('hashlib').sha256(photo.read_bytes()).hexdigest()[:16]}", "top", "owned:own_test", "upper_body")
    owned_out, _ = R.run_task(k, "/s2s/v2.0/task/cloth-v4", lambda s_, r_: {"src_file_id": s_, "ref_file_id": r_, "garment_category": "upper_body", "change_shoes": False},
                              photo, own_path, R.UNITS["cloth"], "ptuck-top")
    s5 = R.render_look("ptuck", photo, look, fb, owned_out=owned_out, tuck="tucked", owned={"ref": own_path, "id": "own_test"})
    assert order(s5) == ["bottom", "tuck", "shoes", "necklace"] and s5[0]["parent"] == owned_out.name, (order(s5), s5[0]["parent"])
    s6 = R.render_look("ptuck", photo, look, fb, tuck="untucked", owned={"ref": own_path, "id": "own_test"})
    assert order(s6)[:3] == ["bottom", "top", "shoes"], order(s6)
    print("own top: tucked = its layer -> trousers -> tuck edit; untucked layers it over the trousers")
    print("tuck checks ok")


if __name__ == "__main__":
    main()
