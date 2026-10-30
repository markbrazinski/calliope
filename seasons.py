"""Seasonal palette suggestion from a cached YouCam Facial Color Tones observation (method calliope-seasons@0.1.0).

Decisions and their evidence status: docs/seasonal-engineering-decisions.md. Every threshold below is an engineering
assumption, not styling science. Axis values are ranking scores in [-1, 1], never probabilities.

One colour space throughout: OKLCH (L 0..1, C >= 0, h degrees), via palette.py's verified sRGB -> OKLab conversion.

Records (kept separate):
  observation     observation_from_analysis(): provider output verbatim + photo hash + task metadata (immutable input)
  interpretation  interpret(observation): derived features, candidates, reasons (recomputed, never stored over prefs)
  preference      state/seasonal_preferences.json, written only by shopper actions (server.py)
  catalog         palettes.json (versioned, provenance included)

Run:  .venv/bin/python seasons.py        self-check + review outputs for every cached headshot
"""

import json
import statistics
from pathlib import Path

import palette as P

ROOT = Path(__file__).resolve().parent
METHOD_VERSION = "calliope-seasons@0.1.0"
CATALOG = json.loads((ROOT / "palettes.json").read_text())
PALETTES = {p["id"]: p for p in CATALOG["palettes"]}
SEASON_NAMES = {k: p["name"] for k, p in PALETTES.items()}

# ---- thresholds (all engineering assumptions; chosen after seeing 3 cached values, so not calibrated) ----
V_CENTER, V_SCALE = 0.55, 0.15     # overall facial lightness V -> value score
K_CENTER, K_SCALE = 0.25, 0.12     # partial contrast K (skin L minus eye/brow L) -> clarity score
H_CENTER, H_SCALE = 58.0, 12.0     # skin hue (deg) -> photo-warmth score
WARMTH_WEIGHT = 0.6                # warmth is the least trustworthy axis; discounted when picking the dominant trait
MIN_SKIN_C_FOR_HUE = 0.03          # below this, skin hue is too unstable to read
CLEAR, LEANING = 0.6, 0.3          # |score| cut-offs for evidence strength
TIEBREAK_MIN = 0.1                 # below this, the tie-break axis does not order the two options
DEFAULT_K = 0.25                   # start-colour target when contrast is unavailable
LEVELS = ["unavailable", "unclear", "leaning", "clear"]

# dominant trait -> (warm-side season, cool-side season); True rows pair by clarity instead (see docs §4.3)
DOMINANT = {"light": ("light_spring", "light_summer"), "dark": ("dark_autumn", "dark_winter"),
            "bright": ("bright_spring", "bright_winter"), "soft": ("soft_autumn", "soft_summer"),
            "warm": ("true_spring", "true_autumn"), "cool": ("true_winter", "true_summer")}
# dominant -> (tie-break axis, season when that axis is positive, season when negative). Our method's choice.
TIEBREAK = {"light": ("c", "light_spring", "light_summer"), "dark": ("c", "dark_winter", "dark_autumn"),
            "bright": ("v", "bright_spring", "bright_winter"), "soft": ("v", "soft_summer", "soft_autumn"),
            "warm": ("c", "true_spring", "true_autumn"), "cool": ("c", "true_winter", "true_summer")}
WARM_SIDE = {"light_spring", "bright_spring", "soft_autumn", "dark_autumn", "true_spring", "true_autumn"}
AXIS_WORD = {"v": "value", "c": "clarity", "t": "photo warmth"}


def clamp(x):
    return max(-1.0, min(1.0, x))


def level(score, cap="clear"):
    lv = "clear" if abs(score) >= CLEAR else "leaning" if abs(score) >= LEANING else "unclear"
    return min(lv, cap, key=LEVELS.index)


def observation_from_analysis(sid, a):
    """Photo-observation record: the provider's output as stored, never edited. None if the analysis did not succeed."""
    res = a.get("results") or {}
    return {"subject": sid, "status": a.get("status"), "error": a.get("error"),
            "photo": {"input": a.get("input"), "sha256": a.get("input_sha256")},
            "provider": {"endpoint": a.get("endpoint"), "task_id": a.get("task_id"), "settings": a.get("settings"),
                         "finished_at": a.get("finished_at")},
            "color": res.get("color"), "face_quality": res.get("face_quality")}


def interpret(obs):
    """Observation -> interpretation. Pure and deterministic."""
    color, q = obs.get("color") or {}, obs.get("face_quality") or {}
    base = {"method_version": METHOD_VERSION, "catalog_version": CATALOG["catalog_version"],
            "excluded": {"hair_color": "excluded_unreliable" if color.get("hair_color") else "not returned",
                         "lip_color": "recorded, not scored (cosmetics can change it; no makeup flag returned)",
                         "skin_chroma": "recorded, not scored (skin chroma is not clothing chroma)"},
            "quality": q, "reviewer_notes": []}
    if obs.get("status") != "success" or q.get("has_face") is False or not color.get("skin_color"):
        why = obs.get("error") or ("no face detected" if q.get("has_face") is False else "skin_color not returned")
        return {**base, "status": "unsuitable_photo", "candidates": [], "reasons": [f"No usable analysis: {why}"],
                "features": {}, "axes": {}}

    lab = {k: P.hex_to_oklch(color[k]) for k in ("skin_color", "eye_color", "eyebrow_color") if color.get(k)}
    skin_L, skin_C, skin_h = lab["skin_color"]
    feats = {k.replace("_color", "") + "_L": round(v[0], 3) for k, v in lab.items()}
    feats.update(skin_C=round(skin_C, 3), skin_h=round(skin_h, 1))
    feature_Ls = [lab[k][0] for k in ("eye_color", "eyebrow_color") if k in lab]

    # caps from the provider's actual face_quality flags (the only quality fields it returns)
    caps, cap_notes = {"v": "clear", "c": "clear", "t": "leaning"}, ["photo warmth capped at 'leaning': a pixel's hue is not an undertone"]
    if q.get("area") not in (None, "good"):
        caps = {k: min(v, "leaning", key=LEVELS.index) for k, v in caps.items()}
        cap_notes.append(f"face area '{q['area']}': all axes capped at 'leaning'")
    if q.get("lighting") not in (None, "good"):
        caps["t"] = "unavailable"
        caps["v"] = min(caps["v"], "leaning", key=LEVELS.index)
        cap_notes.append(f"lighting '{q['lighting']}': warmth unavailable, value capped at 'leaning'")
    turned = [f"{k} '{q[k]}'" for k in ("frontal", "faceangle") if q.get(k) not in (None, "good")]
    if turned:
        caps["c"] = min(caps["c"], "leaning", key=LEVELS.index)
        cap_notes.append(", ".join(turned) + ": clarity (brow/eye contrast) capped at 'leaning'")
    if not feature_Ls:
        caps["v"] = min(caps["v"], "leaning", key=LEVELS.index)
        cap_notes.append("no eye or brow colour: value from skin only, capped at 'leaning'")
    if skin_C < MIN_SKIN_C_FOR_HUE:
        caps["t"] = "unavailable"
        cap_notes.append("skin chroma too low to read hue: warmth unavailable")

    V = (2 * skin_L + sum(feature_Ls)) / (2 + len(feature_Ls))
    axes = {"v": {"score": clamp((V - V_CENTER) / V_SCALE), "input": {"V": round(V, 3)}}}
    if feature_Ls:
        K = statistics.mean(skin_L - x for x in feature_Ls)
        axes["c"] = {"score": clamp((K - K_CENTER) / K_SCALE),
                     "input": {"K": round(K, 3), "from": [k for k in ("eye_color", "eyebrow_color") if k in lab],
                               "partial": "hair excluded; contrast is skin vs available eye/brow only"}}
    if caps["t"] != "unavailable":
        axes["t"] = {"score": clamp((skin_h - H_CENTER) / H_SCALE), "input": {"skin_h": round(skin_h, 1)}}
    for k, ax in axes.items():
        ax["score"] = round(ax["score"], 3)
        ax["evidence"] = level(ax["score"], caps[k])
    for k in ("c", "t"):
        axes.setdefault(k, {"score": None, "evidence": "unavailable"})

    # dominant trait: largest effective magnitude; ties -> value, clarity, warmth
    eff = {k: abs(a["score"]) * (WARMTH_WEIGHT if k == "t" else 1) for k, a in axes.items() if a["score"] is not None}
    dom_axis = max(eff, key=lambda k: (round(eff[k], 6), -"vct".index(k)))
    s = axes[dom_axis]["score"]
    dominant = {"v": "light" if s > 0 else "dark", "c": "bright" if s > 0 else "soft", "t": "warm" if s > 0 else "cool"}[dom_axis]
    reasons = [f"{AXIS_WORD[k]}: score {a['score']} ({a['evidence']})" for k, a in axes.items() if a["score"] is not None]
    reasons += [f"{AXIS_WORD[k]}: not available" for k, a in axes.items() if a["score"] is None]
    out = {**base, "features": feats, "axes": axes, "caps": cap_notes, "dominant": {"trait": dominant, "axis": dom_axis,
           "effective": round(eff[dom_axis], 3)}, "reasons": reasons}
    dom_ev = level(eff[dom_axis], caps[dom_axis])
    if eff[dom_axis] < LEANING:
        return {**out, "status": "direction_only", "candidates": [], "direction": describe(axes),
                "reasons": reasons + [f"strongest axis ({AXIS_WORD[dom_axis]}) is below {LEANING}; no palette suggested"]}

    # family: tie-break axis (measured lightness/contrast) outranks photo warmth
    tb_axis, pos, neg = TIEBREAK[dominant]
    tb = axes[tb_axis]["score"]
    fam_x = (pos if tb > 0 else neg) if tb is not None and abs(tb) >= TIEBREAK_MIN else None
    fam_t = None
    if axes["t"]["evidence"] in ("leaning", "clear") and dom_axis != "t":
        fam_t = DOMINANT[dominant][0] if axes["t"]["score"] > 0 else DOMINANT[dominant][1]
    pair = [pos, neg]
    if fam_x:
        primary, basis = fam_x, f"{AXIS_WORD[tb_axis]} {tb:+.2f} points to {SEASON_NAMES[fam_x]}"
        if fam_t and fam_t != fam_x:
            basis += f"; photo warmth pointed to {SEASON_NAMES[fam_t]} (conflict recorded, measured {AXIS_WORD[tb_axis]} wins)"
    elif fam_t:
        primary, basis = fam_t, f"photo warmth ({axes['t']['evidence']}) points to {SEASON_NAMES[fam_t]}"
    else:
        primary, basis = None, f"{AXIS_WORD[tb_axis]} and warmth do not separate the two options"
    alt = next(p for p in pair if p != primary) if primary else None
    single = dom_ev == "clear" and fam_x and fam_t and fam_x == fam_t
    if primary is None:
        cands = [{"season": p, "role": "equal_standing"} for p in pair]
        status = "two_candidates"
    elif single:
        cands, status = [{"season": primary, "role": "primary"}], "one_candidate"
    else:
        cands = [{"season": primary, "role": "primary"}, {"season": alt, "role": "alternative"}]
        status = "two_candidates"
    for c in cands:
        c["name"] = SEASON_NAMES[c["season"]]
    why_alt = None
    if primary is None:
        why_alt = (f"Both are {dominant} options and differ mainly in warmth, which one photo can't read; "
                   f"{AXIS_WORD[tb_axis]} is too close to the middle to order them")
    elif len(cands) == 2:
        w = "warmer" if cands[1]["season"] in WARM_SIDE else "cooler"
        why_alt = (f"{cands[1]['name']} is the {w} option with the same dominant trait ({dominant}); "
                   f"warmth can't be read reliably from one photo" + ("" if dom_axis != "t" else
                   f", and the True-season pair is split by {AXIS_WORD[tb_axis]}"))
    return {**out, "status": status, "candidates": cands, "primary_basis": basis, "why_alternative": why_alt,
            "dominant_evidence": dom_ev, "direction": describe(axes)}


def describe(axes):
    words = []
    v = axes["v"]
    words.append("lighter coloring" if v["score"] >= LEANING else "deeper coloring" if v["score"] <= -LEANING else "medium depth")
    c = axes["c"]
    words.append("contrast not measurable" if c["score"] is None else "higher brow/eye contrast" if c["score"] >= LEANING
                 else "lower brow/eye contrast" if c["score"] <= -LEANING else "medium brow/eye contrast")
    t = axes["t"]
    words.append("warmth unavailable" if t["score"] is None else "warmth unclear" if t["evidence"] == "unclear"
                 else ("warmer-looking in this photo" if t["score"] > 0 else "cooler-looking in this photo"))
    return " · ".join(words)


def explanation(interp):
    """One short sentence grounded in the observed inputs."""
    if interp["status"] == "unsuitable_photo":
        return "I couldn't read coloring from this photo; any palette below can still be chosen."
    if interp["status"] == "direction_only":
        return f"This photo shows {interp['direction']}, which doesn't point clearly to one palette, so pick any direction you like."
    ax, d = interp["axes"], interp["dominant"]["trait"]
    much = "much" if interp.get("dominant_evidence") == "clear" else "noticeably"   # wording follows evidence level
    close = "close to" if interp.get("dominant_evidence") == "clear" else "fairly close to"
    because = {"bright": f"your brows and eyes are {much} darker than your skin (higher contrast)",
               "soft": f"your brows and eyes sit {close} your skin in lightness (lower contrast)",
               "light": "your facial colors read light overall", "dark": "your facial colors read deep overall",
               "warm": "the photo reads warm but lightness and contrast are in the middle",
               "cool": "the photo reads cool but lightness and contrast are in the middle"}[d]
    tail = " Warmth can't be read reliably from one photo." if ax["t"]["evidence"] != "clear" else ""
    return f"Suggested because {because}.{tail}"


def headline(interp):
    c = interp.get("candidates") or []
    if not c:
        return "Choose any palette to start."
    if c[0]["role"] == "equal_standing":
        return f"I'd start with either {c[0]['name']}- or {c[1]['name']}-inspired colors; this photo doesn't separate them. Or choose any palette you prefer."
    if len(c) == 1:
        return f"I'd start with these {c[0]['name']}-inspired colors, or choose any palette you prefer."
    w = "warmer" if c[1]["season"] in WARM_SIDE else "cooler"
    return f"I'd start with these {c[0]['name']}-inspired colors. Explore a {w} alternative, or choose any palette you prefer."


def start_here(season, interp, prefs=None, occasion=None):
    """Deterministic 'start here' swatch from one palette. Returns the swatch dict plus a one-sentence reason."""
    prefs = prefs or {}
    pal = PALETTES[season]["swatches"]
    disliked, liked = set(prefs.get("disliked") or []), set(prefs.get("preferred") or [])
    pool = [s for s in pal if s["id"] not in disliked]
    if not pool:
        return None
    # core colours first: neutrals and 'explore' swatches are for browsing, used only if nothing else is left
    colors = ([s for s in pool if not {"neutral", "explore"} & set(s["tags"])]
              or [s for s in pool if "neutral" not in s["tags"]] or pool)
    chosen_from = "your preferred colors" if liked & {s["id"] for s in colors} else None
    if chosen_from:
        colors = [s for s in colors if s["id"] in liked]
    feats = interp.get("features") or {}
    skin_L = feats.get("skin_L")
    K = (interp.get("axes", {}).get("c", {}).get("input") or {}).get("K", DEFAULT_K)
    medC = statistics.median(s["oklch"][1] for s in pal if "neutral" not in s["tags"])

    def score(s):
        L, C = s["oklch"][0], s["oklch"][1]
        sc = abs(abs(L - skin_L) - K) if skin_L is not None else 0.0
        sc += abs(C - medC)
        if occasion == "evening" and L > 0.6:
            sc += 0.2
        if occasion == "day" and L < 0.4:
            sc += 0.2
        return round(sc, 6)

    best = min(colors, key=lambda s: (score(s), pal.index(s)))
    if skin_L is None:
        why = "a mid-strength color from this palette (no photo measurements used)"
    else:
        rel = "darker" if best["oklch"][0] < skin_L else "lighter"
        src = "brows and eyes" if "c" in interp.get("axes", {}) and interp["axes"]["c"]["score"] is not None else "a medium default"
        why = f"{rel} than your skin by about as much as your {src} are, and one of the palette's mid-strength colors"
    if chosen_from:
        why = "from " + chosen_from + "; " + why
    if occasion:
        why += f", adjusted for {occasion}"
    return {"swatch": best, "reason": f"Start here: {best['name']}, {why}.", "label": "starting suggestion",
            "score": score(best), "inputs": {"skin_L": skin_L, "target_contrast": K, "palette_median_C": round(medC, 3),
                                             "disliked_excluded": sorted(disliked & {s['id'] for s in pal}),
                                             "occasion": occasion}}


# ---------------- verification ----------------

def check_catalog():
    assert len(PALETTES) == 12 and set(PALETTES) == {s for pair in DOMINANT.values() for s in pair}
    ids = set()
    for p in CATALOG["palettes"]:
        sw = p["swatches"]
        assert 10 <= len(sw) <= 12, p["id"]
        assert sum("neutral" in s["tags"] for s in sw) >= 3, p["id"]
        for s in sw:
            assert s["id"] not in ids and s["id"].startswith(p["id"] + "."), s["id"]
            ids.add(s["id"])
            assert P.oklch_to_hex(*s["oklch"])[0] == s["hex"], (s["id"], "hex does not match OKLCH")
        for i, a in enumerate(sw):
            for b in sw[i + 1:]:
                assert P.oklab_distance(a["hex"], b["hex"]) >= 0.045, (a["id"], b["id"], "near-duplicate")
    assert "cool autumn" not in json.dumps(CATALOG).lower()
    assert CATALOG["provenance"]["validated_against_fabric"] is False


def synth(skin, eye=None, brow=None, **q):
    """Synthetic observation from OKLCH triples. Exercises branches; validates nothing about styling."""
    col = {"skin_color": P.oklch_to_hex(*skin)[0]}
    if eye:
        col["eye_color"] = P.oklch_to_hex(*eye)[0]
    if brow:
        col["eyebrow_color"] = P.oklch_to_hex(*brow)[0]
    quality = {"has_face": True, "area": "good", "frontal": "good", "lighting": "good", "faceangle": "good", **q}
    return {"status": "success", "color": col, "face_quality": quality}


def self_check():
    check_catalog()
    reached = {}
    # (skin, eye, brow) grids: vary depth, contrast, hue so every season is reachable through its own rule path
    for sL in (0.45, 0.55, 0.62, 0.70, 0.80, 0.88):
        for fL in (0.20, 0.35, 0.50, 0.62, 0.75, 0.85):
            for h in (40, 52, 58, 64, 76):
                it = interpret(synth((sL, 0.06, h), (fL, 0.02, 60), (fL + 0.03, 0.02, 60)))
                for c in it["candidates"]:
                    reached.setdefault(c["season"], (sL, fL, h, it["status"]))
                for c in it["candidates"]:
                    assert c["season"] in PALETTES
                    st = start_here(c["season"], it)
                    assert st["swatch"] in PALETTES[c["season"]]["swatches"]          # swatch belongs to its palette
                if it["status"] == "one_candidate":
                    assert it["axes"]["t"]["evidence"] == "leaning" and it["dominant_evidence"] == "clear"
                assert it["axes"]["t"]["evidence"] != "clear"                       # warmth never 'clear'
    assert set(reached) == set(PALETTES), sorted(set(PALETTES) - set(reached))    # all twelve labels reachable

    # missing hair handled: hair present or absent gives the same interpretation apart from the exclusion note
    o = synth((0.66, 0.06, 56), (0.23, 0.02, 15), (0.35, 0.01, 56))
    o2 = json.loads(json.dumps(o)); o2["color"].update(hair_color="#FAF0BE", hair_color_name="Blonde")
    a, b = interpret(o), interpret(o2)
    assert a["candidates"] == b["candidates"] and b["excluded"]["hair_color"] == "excluded_unreliable"
    # no eye/brow: contrast is never fabricated
    nb = interpret(synth((0.40, 0.06, 56)))
    assert nb["axes"]["c"]["score"] is None and "K" not in json.dumps(nb["axes"]["c"])
    assert nb["axes"]["v"]["evidence"] in ("leaning", "unclear") and nb["dominant"]["trait"] in ("dark", "light", "warm", "cool")
    assert start_here("dark_winter", nb)["inputs"]["target_contrast"] == DEFAULT_K
    # quality flags propagate
    tl = interpret(synth((0.66, 0.06, 56), (0.23, 0.02, 15), (0.35, 0.01, 56), faceangle="leftward"))
    assert tl["axes"]["c"]["evidence"] == "leaning" and any("faceangle 'leftward'" in n for n in tl["caps"])
    lt = interpret(synth((0.66, 0.06, 70), (0.23, 0.02, 15), (0.35, 0.01, 56), lighting="notgood"))
    assert lt["axes"]["t"]["score"] is None and lt["quality"]["lighting"] == "notgood"
    assert interpret(synth((0.66, 0.06, 56), has_face=False))["status"] == "unsuitable_photo"
    assert interpret({"status": "error", "error": "error_face_position_too_small"})["status"] == "unsuitable_photo"
    # direction only: everything in the middle
    mid = interpret(synth((0.66, 0.06, 58), (0.44, 0.02, 60), (0.38, 0.02, 60)))
    assert mid["status"] == "direction_only" and mid["candidates"] == [], mid["axes"]
    # equal standing: dominant clear, tie-break axis ~0, warmth unclear
    eq = [interpret(synth((sL, 0.06, 58), (fL, 0.02, 60), (fL, 0.02, 60)))
          for sL in (0.55, 0.6, 0.65, 0.7) for fL in (0.20, 0.25, 0.3)]
    assert any(e["candidates"] and e["candidates"][0]["role"] == "equal_standing" for e in eq)
    # start here respects dislikes and preferences; changing palette needs only local data
    it = interpret(o)
    s1 = start_here("bright_winter", it)
    s2 = start_here("bright_winter", it, {"disliked": [s1["swatch"]["id"]]})
    assert s2["swatch"]["id"] != s1["swatch"]["id"]
    pick = PALETTES["bright_winter"]["swatches"][9]["id"]
    assert start_here("bright_winter", it, {"preferred": [pick]})["swatch"]["id"] == pick
    all_ids = [s["id"] for s in PALETTES["soft_summer"]["swatches"]]
    assert start_here("soft_summer", it, {"disliked": all_ids}) is None
    assert start_here("true_autumn", it, occasion="evening")["swatch"]["oklch"][0] <= 0.6
    assert interpret(o) == interpret(o)                                              # deterministic
    print("seasons self-check ok:", len(reached), "of 12 labels reachable;", sum(len(p["swatches"]) for p in CATALOG["palettes"]), "swatches")
    return reached


def sensitivity(obs, dL=(-0.04, -0.02, 0.02, 0.04), dh=(-10, -5, 5, 10)):
    """RULE SENSITIVITY only: nudge each returned colour's OKLCH value and see whether the candidate set changes.
    This says nothing about camera or API reliability."""
    base = [c["season"] for c in interpret(obs)["candidates"]]
    rows = []
    for key in ("skin_color", "eye_color", "eyebrow_color"):
        if not (obs["color"] or {}).get(key):
            continue
        L, C, h = P.hex_to_oklch(obs["color"][key])
        for d in dL:
            o = json.loads(json.dumps(obs)); o["color"][key] = P.oklch_to_hex(L + d, C, h)[0]
            got = [c["season"] for c in interpret(o)["candidates"]]
            rows.append({"input": key, "change": f"L {d:+.2f}", "candidates": got, "changed": got != base})
    L, C, h = P.hex_to_oklch(obs["color"]["skin_color"])
    for d in dh:
        o = json.loads(json.dumps(obs)); o["color"]["skin_color"] = P.oklch_to_hex(L, C, h + d)[0]
        got = [c["season"] for c in interpret(o)["candidates"]]
        rows.append({"input": "skin_color", "change": f"h {d:+d}°", "candidates": got, "changed": got != base})
    return {"baseline": base, "rows": rows, "changed": sum(r["changed"] for r in rows), "total": len(rows)}


if __name__ == "__main__":
    self_check()
    analyses = json.loads((ROOT / "state/analyses.json").read_text())
    out = ROOT / "artifacts/seasonal"
    out.mkdir(parents=True, exist_ok=True)
    for sid, a in analyses.items():
        obs = observation_from_analysis(sid, a)
        it = interpret(obs)
        rec = {"observation": obs, "interpretation": it, "headline": headline(it), "explanation": explanation(it),
               "start_here": {c["season"]: start_here(c["season"], it) for c in it["candidates"]},
               "sensitivity": sensitivity(obs) if it["status"] != "unsuitable_photo" else None}
        (out / f"review-{sid}.json").write_text(json.dumps(rec, indent=1))
        st = rec["start_here"].get(it["candidates"][0]["season"]) if it["candidates"] else None
        sens = rec["sensitivity"]
        print(f"{sid:12} {it['status']:16} {[c['name'] + '/' + c['role'] for c in it['candidates']]} "
              f"start={st['swatch']['name'] if st else '-'}  sensitivity={sens['changed']}/{sens['total'] if sens else '-'}" if sens else
              f"{sid:12} {it['status']:16} {it['reasons']}")
