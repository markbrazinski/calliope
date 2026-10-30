"""Colour intent for "darker" (engine.same_family / color_named; the real catalog, no network, no paid calls).

    .venv/bin/python tests/check_color_intent.py

Checked: "darker" alone stays in the current colour family (a coloured piece keeps its hue; a neutral stays neutral);
when nothing in the family fits, the colour change is a labelled alternative, never a match; a named colour replaces the
family rule; "darker with a narrower leg" meets both in the same family; non-target pieces and the budget always hold.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import engine as R  # noqa: E402
import palette as P  # noqa: E402
import shopper as A  # noqa: E402

LCH = lambda it: P.hex_to_oklch(it["hex"])
NAME = lambda it: f"{it.get('pictured_color') or ''} {it['title'][:38]} ({it['hex']})".strip()
KEEP = ("id", "slot", "title", "handle", "price", "hex", "cls", "dist", "family", "pictured_color", "tryon_image", "url", "heel_height")


def pref(attr, op, base=None, value=None, **kw):
    p = {"slot": "bottom", "attribute": attr, "op": op, "sourcePhrase": attr, **kw}
    if value is not None: p["value"] = value
    if base is not None: p["anchor"] = {"values": [base]}
    return p


def ok_items(slot):
    return [i for i in R.INDEX if i["slot"] == slot and i["in_stock"] and i.get("hex") and i.get("pattern") == "solid" and i.get("price") and R.passes(i, R.DATA["constraints"]["always"])[0]]


def look_with(bottom):
    rest = {sl: ok_items(sl)[0] for sl in ("top", "shoes", "necklace")}
    return {sl: {f: it.get(f) for f in KEEP} for sl, it in {**rest, "bottom": bottom}.items()}


def revise(bottom, requested, idx=None):
    look = look_with(bottom)
    r = R.revise_attrs(look, requested, None, idx or R.INDEX, set())
    if r["status"] == "match":
        assert all(r["look"][sl] == look[sl] for sl in ("top", "shoes", "necklace")), "non-target pieces are kept exactly"
        assert r["subtotal"] <= R.budget_limit(R.DATA["brief"]) + 1e-9
    return look, r


def main():
    R.DATA["brief"] = dict(A.BASE_BRIEF, constraints=[], budgetNew=400, budgetMode="firm", palette_required=False)
    # 1. the policy itself
    assert R.same_family("#4a3426", "#d9cdb8") and not R.same_family("#e8789a", "#d9cdb8"), "oatmeal: darker neutral yes, flamingo pink no"
    assert R.same_family("#1f2a44", "#9ec3e6") and not R.same_family("#ff1493", "#9ec3e6"), "light blue: navy yes, hot pink no"
    assert not R.same_family("#111111", "#1f2a44") and R.same_family("#3a3a3a", "#808080"), "navy -> black is a colour change; grey -> charcoal isn't"
    print("policy: oatmeal->dark brown, light blue->navy, grey->charcoal stay in family; ->pink, ->hot pink, navy->black don't")

    bottoms = ok_items("bottom")
    # 2. same-family success on a coloured pair
    hit = None
    for b in sorted((x for x in bottoms if LCH(x)[1] >= R.NEUTRAL_C), key=lambda x: -LCH(x)[0]):
        look, r = revise(b, [pref("lightness", "lt", b["hex"])])
        if r["status"] == "match":
            hit = (b, r["look"]["bottom"]); break
    b, n = hit
    assert R.same_family(n["hex"], b["hex"]) and LCH(n)[0] < LCH(b)[0], (b["pictured_color"], n["pictured_color"])
    print(f"same family: {NAME(b)} -> {NAME(n)} (hue {LCH(b)[2]:.0f} -> {LCH(n)[2]:.0f} deg, L {LCH(b)[0]:.2f} -> {LCH(n)[0]:.2f})")

    # 3. a neutral stays neutral
    neu = next(x for x in sorted(bottoms, key=lambda x: -LCH(x)[0]) if LCH(x)[1] < R.NEUTRAL_C and LCH(x)[0] > 0.6)
    look, r = revise(neu, [pref("lightness", "lt", neu["hex"])])
    assert r["status"] == "match" and LCH(r["look"]["bottom"])[1] < R.NEUTRAL_C and R.same_family(r["look"]["bottom"]["hex"], neu["hex"]), r.get("message")
    assert not R.same_family("#ba898d", neu["hex"]), "a dusty rose is a colour change for a neutral"
    print(f"neutral: {NAME(neu)} -> {NAME(r['look']['bottom'])} (chroma {LCH(r['look']['bottom'])[1]:.3f}, hue not used)")

    # 4. nothing darker in the family: no match, the colour change is a labelled alternative
    idx = [i for i in R.INDEX if not (i["slot"] == "bottom" and i["id"] != b["id"] and i.get("hex") and R.same_family(i["hex"], b["hex"]))]
    look, r = revise(b, [pref("lightness", "lt", b["hex"])], idx)
    assert r["status"] == "no_match" and "same colour family" in r["message"], r
    alt = r["alternative"]
    assert alt and "different colour" in alt["label"] and not R.same_family(alt["look"]["bottom"]["hex"], b["hex"]), alt and alt["label"]
    assert all(alt["look"][sl] == look[sl] for sl in ("top", "shoes", "necklace"))
    print("no same-family match:", r["message"], "|", alt["label"])

    # 5. a named colour replaces the family rule (an explicit hue change)
    light = next(x for x in sorted(bottoms, key=lambda x: -LCH(x)[0]) if not R.color_named(x, "black"))
    look, r = revise(light, [pref("lightness", "lt", light["hex"]), pref("color", "eq", value="black")])
    assert r["status"] == "match" and R.color_named(r["look"]["bottom"], "black"), r.get("message")
    look, r2 = revise(light, [pref("color", "eq", value="navy")])
    assert r2["status"] != "match" or R.color_named(r2["look"]["bottom"], "navy")
    print(f"named colour: {NAME(light)} + 'darker in black' -> {NAME(r['look']['bottom'])}; 'navy' -> "
          f"{NAME(r2['look']['bottom']) if r2['status'] == 'match' else r2['message']}")

    # 6. darker with a narrower leg, same family
    wide = [x for x in bottoms if R.shape_of(x) == "wide_leg"]
    done = None
    for w in sorted(wide, key=lambda x: -LCH(x)[0]):
        req = [pref("lightness", "lt", w["hex"]), pref("leg_cut", "lt", "wide_leg")]
        look, r = revise(w, req)
        if r["status"] == "match":
            n = r["look"]["bottom"]
            assert R.same_family(n["hex"], w["hex"]) and LCH(n)[0] < LCH(w)[0] and R.shape_of(n) in ("skinny", "slim", "straight"), n
            done = (w, n, r.get("runnerUp")); break
        assert r["status"] == "no_match" and (r["alternative"] is None or "different colour" in r["alternative"]["label"] or "but not" in r["alternative"]["label"]), r
    w, n, ru = done
    print(f"darker + narrower: {NAME(w)} wide-leg -> {NAME(n)} {R.shape_word(n)}" + (f"; option 2: {NAME(ru['look']['bottom'])} {R.shape_word(ru['look']['bottom'])}" if ru else ""))
    if ru:
        assert R.same_family(ru["look"]["bottom"]["hex"], w["hex"]), "the second option keeps the family too"

    # 7. "any colour" accepts a hue change
    look, r = revise(b, [pref("lightness", "lt", b["hex"], anyHue=True)], idx)
    assert r["status"] == "match", r
    print("'any colour': the darker piece in another colour is a match because she asked for it")
    print("colour intent checks ok")


if __name__ == "__main__":
    main()
