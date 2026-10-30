"""Prototype palette derivation from YouCam Facial Color Tones Analyzer observations.

These are prototype styling rules, not validated personal-colour science. They produce two palettes that
differ mainly in LIGHTNESS and CHROMA relative to the photographed skin colour, with hue held roughly
constant, so a person can compare them side by side. No flattery claim is made by the maths.

Colour space: OKLab / OKLCH (Björn Ottosson, 2020, https://bottosson.github.io/posts/oklab/),
sRGB D65 with the standard sRGB transfer function. Verified against published reference values in
self_check().

Inputs (only fields YouCam actually returned are used; each is optional except skin_color):
  skin_color     required. Its OKLCH lightness L_s anchors both palettes.
  eye_color      optional hue cue: first choice for the palette hue family if its chroma >= HUE_CUE_MIN_C.
  eyebrow_color  optional hue cue (v2): second choice for the hue family; also tints the neutral and accent.
                 It is a measured hex from the photo, and usually tracks hair colour.
  hair_color     recorded but NOT used as a cue (v2). YouCam quantizes it to one of 7 fixed swatches, and in the
                 2026-09-30 test it was visibly wrong in both photos where it was returned (dark hair ->
                 "Blonde", brown hair -> "Grey/White").
  lip_color: recorded, not used (kept out to limit variables).

Rule history (local only; never alters YouCam observations):
  v1: hue family from eye, then hair; neutral/accent tint from hair.
  v2 (2026-09-30, after the first live results, no new API call): hue from eye, then eyebrow, then DEFAULT_HUE;
      tint from eyebrow; hair not used. Reason: v1 built one person's palette on a misclassified hair swatch.

Rules (all thresholds below are prototype choices):
  hue family h0 = the first of eye, eyebrow hue with chroma >= 0.03, else DEFAULT_HUE.
  Palette A "Suggested starting palette" (clear lightness separation, moderate chroma):
    main      L = L_s - 0.38 if L_s >= 0.55 else L_s + 0.38   (|ΔL| ≈ 0.38 from skin), C 0.10, h0
    support   L = main L + 0.14 toward skin side (still ≥ 0.20 from skin), C 0.08, h0 + 30°
    neutral   L = 0.30 if main is lighter than skin else 0.93, C 0.015, hue of hair cue (else h0)
    accent    L = 0.62, C 0.13, hue of hair cue if present else h0 - 50°
  Palette B "Soft / low-contrast alternative" (same hues; near skin lightness; low chroma):
    main      L = L_s - 0.04, C 0.035, h0
    support   L = L_s + 0.05, C 0.03, h0 + 30°
    neutral   L = L_s + 0.10, C 0.012, neutral hue of A
    accent    L = L_s - 0.10, C 0.05, accent hue of A
  All lightness values are clamped to [0.18, 0.96]. Colours outside sRGB are brought into gamut by
  reducing chroma only (binary search), keeping L and h. Any clamping is recorded per swatch.
  Guard: if A.main and B.main differ by < MIN_MAIN_DELTA in OKLab distance, A.main is pushed further in
  lightness (recorded as an adjustment). This never alters any YouCam observation.
"""

import json
import math

HUE_CUE_MIN_C = 0.03
DEFAULT_HUE = 250.0          # fallback hue family (a blue) when no usable eye/hair hue is returned
MIN_MAIN_DELTA = 0.15
L_MIN, L_MAX = 0.18, 0.96


# ---------- colour conversion (sRGB <-> OKLab <-> OKLCH) ----------

def _to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_oklab(hx):
    r, g, b = (_to_linear(int(hx[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (math.copysign(abs(v) ** (1 / 3), v) for v in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklab_to_linear_rgb(L, a, b):
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def oklab_to_oklch(L, a, b):
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def oklch_to_oklab(L, C, h):
    return L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))


def hex_to_oklch(hx):
    return oklab_to_oklch(*hex_to_oklab(hx))


def in_gamut(L, C, h, eps=1e-6):
    return all(-eps <= v <= 1 + eps for v in oklab_to_linear_rgb(*oklch_to_oklab(L, C, h)))


def oklch_to_hex(L, C, h):
    """Clamp to sRGB by reducing chroma only; returns (hex, chroma_used, clamped?)."""
    lo, hi, clamped = 0.0, C, False
    if not in_gamut(L, C, h):
        clamped = True
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if in_gamut(L, mid, h) else (lo, mid)
        C = lo
    rgb = oklab_to_linear_rgb(*oklch_to_oklab(L, C, h))
    return "#" + "".join(f"{round(min(1, max(0, _to_srgb(min(1, max(0, v))))) * 255):02x}" for v in rgb), C, clamped


def oklab_distance(h1, h2):
    return math.dist(hex_to_oklab(h1), hex_to_oklab(h2))


# ---------- heuristic ----------

def _swatch(role, name, L, C, h, why):
    L = min(L_MAX, max(L_MIN, L))
    hx, c_used, clamped = oklch_to_hex(L, C, h)
    return {"role": role, "name": name, "hex": hx, "oklch": [round(L, 3), round(c_used, 3), round(h % 360, 1)],
            "requested_chroma": C, "gamut_clamped": clamped, "rule": why}


def derive(obs):
    """obs: dict of YouCam colour fields (hex strings). Returns observations used, palettes and notes."""
    if not obs.get("skin_color"):
        raise ValueError("skin_color not returned; no personalised palette can be derived")
    Ls, Cs, hs = hex_to_oklch(obs["skin_color"])
    cues, notes = {}, []
    for k in ("eye_color", "eyebrow_color", "hair_color"):
        if obs.get(k):
            cues[k] = hex_to_oklch(obs[k])
        else:
            notes.append(f"{k} not returned")
    usable = [k for k in ("eye_color", "eyebrow_color") if k in cues and cues[k][1] >= HUE_CUE_MIN_C]
    if usable:
        h0, h0_src = cues[usable[0]][2], f"{usable[0]} hue" + ("" if usable[0] == "eye_color" else " (earlier cues missing or near-neutral)")
    else:
        h0, h0_src = DEFAULT_HUE, f"fallback DEFAULT_HUE {DEFAULT_HUE}° (no chromatic eye/eyebrow colour)"
    if "hair_color" in cues:
        notes.append("hair_color recorded but not used as a cue (quantized swatch; see rule history)")
    tint = "eyebrow_color" if "eyebrow_color" in usable else None
    tint_h = cues[tint][2] if tint else None
    neutral_h = tint_h if tint_h is not None else h0
    accent_h = tint_h if tint_h is not None else h0 - 50

    darker = Ls >= 0.55
    sign = -1 if darker else 1
    a_main_L = Ls + sign * 0.38
    a_support_L = a_main_L - sign * 0.14
    A = [_swatch("main", "Main garment", a_main_L, 0.10, h0, "skin L ± 0.38, chroma 0.10, hue family"),
         _swatch("support", "Supporting garment", a_support_L, 0.08, h0 + 30, "main L + 0.14 toward skin, chroma 0.08, hue + 30°"),
         _swatch("neutral", "Neutral", 0.93 if darker else 0.30, 0.015, neutral_h, f"very low chroma, far from skin lightness, {tint or 'palette'} hue"),
         _swatch("accent", "Accent", 0.62, 0.13, accent_h, f"mid lightness, higher chroma, {tint + ' hue' if tint else 'hue − 50°'}")]
    B = [_swatch("main", "Main garment", Ls - 0.04, 0.035, h0, "skin L − 0.04, chroma 0.035, same hue as A"),
         _swatch("support", "Supporting garment", Ls + 0.05, 0.03, h0 + 30, "skin L + 0.05, chroma 0.03, same hue as A"),
         _swatch("neutral", "Neutral", Ls + 0.10, 0.012, neutral_h, "skin L + 0.10, chroma 0.012, same hue as A"),
         _swatch("accent", "Accent", Ls - 0.10, 0.05, accent_h, "skin L − 0.10, chroma 0.05, same hue as A")]
    adjustments = []
    d = oklab_distance(A[0]["hex"], B[0]["hex"])
    if d < MIN_MAIN_DELTA:
        A[0] = _swatch("main", "Main garment", a_main_L + sign * 0.08, 0.10, h0, "pushed 0.08 further from skin (distinctness guard)")
        adjustments.append(f"A.main moved 0.08 further in L: main-to-main distance was {d:.3f} < {MIN_MAIN_DELTA}")
    return {
        "method": "palette.derive v2 (OKLCH heuristic; prototype rules and rule history documented in palette.py)",
        "observations_used": {"skin_color": obs["skin_color"], "skin_oklch": [round(Ls, 3), round(Cs, 3), round(hs, 1)],
                              **{k: {"hex": obs[k], "oklch": [round(v, 3) for v in cues[k]]} for k in cues}},
        "hue_family": {"h0": round(h0 % 360, 1), "source": h0_src},
        "skin_lightness_side": "main garments darker than skin" if darker else "main garments lighter than skin",
        "palettes": {
            "A": {"title": "Suggested starting palette", "swatches": A,
                  "main_vs_skin_dL": round(A[0]["oklch"][0] - Ls, 3)},
            "B": {"title": "Soft / low-contrast alternative", "swatches": B,
                  "main_vs_skin_dL": round(B[0]["oklch"][0] - Ls, 3)}},
        "main_to_main_oklab_distance": round(oklab_distance(A[0]["hex"], B[0]["hex"]), 3),
        "adjustments": adjustments, "notes": notes,
    }


def self_check():
    # Published OKLab values for sRGB primaries (Ottosson 2020 / CSS Color 4 examples)
    for hx, ref in (("#ffffff", (1.0, 0.0, 0.0)), ("#ff0000", (0.62796, 0.22486, 0.12585)),
                    ("#00ff00", (0.86644, -0.23389, 0.17950)), ("#0000ff", (0.45201, -0.03246, -0.31153))):
        got = hex_to_oklab(hx)
        assert all(abs(g - r) < 5e-4 for g, r in zip(got, ref)), (hx, got, ref)
    for hx in ("#b9947c", "#293f9b", "#123456", "#fafafa", "#000000"):  # round trip
        assert oklch_to_hex(*hex_to_oklch(hx))[0] == hx, hx
    assert oklch_to_hex(0.7, 0.4, 150)[2] is True          # out-of-gamut request gets clamped
    p = derive({"skin_color": "#b9947c", "eye_color": "#293F9B", "hair_color": "#FAF0BE"})  # spec example values
    a, b = p["palettes"]["A"], p["palettes"]["B"]
    assert abs(a["main_vs_skin_dL"]) >= 0.3 and abs(b["main_vs_skin_dL"]) <= 0.08
    assert p["main_to_main_oklab_distance"] >= MIN_MAIN_DELTA
    assert derive({"skin_color": "#b9947c"})["hue_family"]["h0"] == DEFAULT_HUE     # fallback path
    assert derive({"skin_color": "#b9947c", "hair_color": "#FAF0BE"})["hue_family"]["h0"] == DEFAULT_HUE  # hair unused
    assert derive({"skin_color": "#b9947c"}) == derive({"skin_color": "#b9947c"})    # deterministic
    # v2: a chromatic eyebrow outranks the quantized hair swatch
    v2 = derive({"skin_color": "#af896f", "eye_color": "#28191a", "eyebrow_color": "#a78b78", "hair_color": "#FAF0BE"})
    assert v2["hue_family"]["source"].startswith("eyebrow_color")
    print("palette self-check ok")
    print(json.dumps({k: [s["hex"] for s in v["swatches"]] for k, v in p["palettes"].items()}))


if __name__ == "__main__":
    self_check()
