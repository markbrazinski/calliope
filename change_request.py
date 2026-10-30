"""Attribute-level change requests: parse -> validate -> plan (anchors, clarifications) -> answers.

The model path (Nova tool use) and the rules fallback produce the same raw shape; validate() treats both as untrusted.
Stdlib only; no network. The model itself is not called here (checks use stubbed outputs).
    python3 change_request.py     # self-check
"""
import re
import attributes as AT

Q = AT.V["requests"]
INTEGRATED = AT.V["request_attributes"]
OPS = ("eq", "neq", "lt", "gt")

SLOT_NAME = {"top": "top", "bottom": "trousers", "dress": "dress", "shoes": "shoes", "necklace": "necklace"}

# Tool schema for the model path (forced tool use); validate() below is the authority, not the model.
TOOL = {"name": "record_change_request", "description": "Classify an outfit change request into attribute-level preferences.",
        "schema": {"type": "object", "additionalProperties": False, "required": ["status", "requested"], "properties": {
            "status": {"enum": ["supported", "needs_clarification", "unsupported"]},
            "requested": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                     "required": ["slot", "attribute", "op", "sourcePhrase"], "properties": {
                "slot": {"enum": ["top", "bottom", "dress"]}, "attribute": {"enum": INTEGRATED}, "op": {"enum": list(OPS)},
                "value": {"type": "string"}, "sourcePhrase": {"type": "string", "description": "exact words from the shopper"}}}},
            "clarify": {"type": "object", "properties": {"kind": {"enum": ["cut_or_size", "which_value"]}, "attribute": {"type": "string"},
                                                         "hint": {"type": "string"}, "direction": {"enum": ["smaller", "bigger"]}}},
            "wish": {"type": "object", "description": "a wish the attributes can't express ('more fun shoes', 'a more elegant top', 'sparkly necklace')",
                     "properties": {"slot": {"enum": ["top", "bottom", "dress", "shoes", "necklace"]},
                                    "words": {"type": "string", "description": "her words for the wish, copied, e.g. 'more fun'"}}}}}}


SYSTEM = ("You classify a shopper's change request for an outfit (either top + bottom = trousers, or a dress; plus shoes and a necklace) into attribute-level "
          "preferences. Attributes: rise (low < mid < high); leg_cut (skinny < slim < straight < wide_leg; flare, bootcut, tapered and barrel "
          "have no order); neckline (no order); sleeve_length (sleeveless < cap < short < elbow < three_quarter < long); pants_type (jeans, "
          "trousers, leggings, joggers, sweatpants); dress_length (mini < knee < midi < maxi, slot dress); silhouette (a_line, fit_and_flare, sheath, bodycon, "
          "shift, slip, wrap, shirt, babydoll, mermaid; slot dress). Use op lt or gt only for 'lower/higher', 'slimmer/wider', 'shorter/longer' on ordered "
          "attributes; eq for a named value; neq for 'different'. Never use lt or gt for neckline: for 'lower' or 'deeper neckline' set "
          "clarify.kind which_value. 'Tighter' or 'looser' could mean the cut or the size: set clarify.kind cut_or_size. sourcePhrase must be "
          "copied exactly from the shopper's words. lightness: 'darker' = op lt, 'lighter' = op gt, on the piece named (trousers = bottom). "
          "color: only when the shopper names a colour (black, white, grey, navy, blue, red, pink, purple, green, olive, brown, beige, cream, "
          "burgundy, orange, yellow): op eq, value that word, on the piece named; 'darker' alone is not a colour. "
          "A request can contain several changes; record every one. A wish about a piece that these attributes can't express "
          "('more fun shoes', 'a more elegant top', 'something sparkly') goes in 'wish' with the piece and her words, status supported. "
          "Anything else: status unsupported.")


class StubNova:
    """Local stand-in for the Bedrock model call (no network, $0): returns the fixture's canned model output for known
    texts, otherwise the rules parse dressed as a model output. Clearly labelled as a stub in every response."""
    mode = "nova-stub"

    def __init__(self, fixtures=None):
        self.canned = {c["text"]: c["model_stub"] for c in (fixtures or []) if "model_stub" in c}
        self.calls = []

    def call(self, system, content, tool, max_tokens=300):
        text = content[0]["text"].split("\n", 1)[-1]
        self.calls.append(text)
        return (self.canned[text] if text in self.canned else parse_rules(text)), 0.0


# Switching the outfit structure is decided from the shopper's words, never by the model alone.
STRUCTURE = {"dress": re.compile(r"\b(a|an|try a|wear a|in a)\s+(\w+[\s-]+){0,3}dress\b(?!\s+(shoes?|up))|\bdress instead\b", re.I),
             "separates": re.compile(r"\bseparates\b|\b(trousers|pants|jeans)\s+(and a top\s+)?instead\b|\btop and (trousers|pants)\b", re.I)}


def structure_of(text):
    return next((k for k, rx in STRUCTURE.items() if rx.search(text)), None)


def interpret(text, client=None):
    """Model path when a client is configured, else rules. Every output goes through validate(); a model failure
    falls back to rules and says so."""
    mode = getattr(client, "mode", "nova") if client else "rules"
    if client is None:
        raw = parse_rules(text)
    else:
        try:
            raw, _ = client.call(SYSTEM, [{"text": f"Shopper said:\n{text}"}], TOOL, 300)
        except Exception as e:
            raw, mode = parse_rules(text), f"rules (model unavailable: {type(e).__name__})"
    clean, problems = validate(raw, text)
    if client is not None and clean["status"] == "unsupported":
        rules = validate(parse_rules(text), text)[0]          # e.g. the model refused "darker shoes"; the vocabulary supports it
        if rules["status"] == "supported":
            clean = rules; problems.append("model said unsupported; the rules found a supported request")
    elif client is not None and clean["status"] in ("supported", "needs_clarification"):
        # never silently process only part of a compound request: conditions the rules can see must be present (also
        # when the model asks a question: "darker and tighter" -> "cut or size?" keeps "darker")
        have = {(p["slot"], p["attribute"]) for p in clean["requested"]}
        for p in validate(parse_rules(text), text)[0]["requested"]:
            if (p["slot"], p["attribute"]) not in have:
                clean["requested"].append(p); problems.append(f"model omitted {p['attribute']} ('{p['sourcePhrase']}'); added from rules")
    if re.search(r"\b(any|another|a different|other)\s+colou?r", text, re.I):     # she accepts a colour change: darker in any hue
        clean["requested"] = [dict(p, anyHue=True) if p["attribute"] == "lightness" else p for p in clean["requested"]]
    st = structure_of(text)
    if st:
        clean["structure"] = st
        if clean["status"] == "unsupported":
            clean["status"] = "supported"
    return clean, {"interpreter": mode, "problems": problems}


def label(v):
    return Q["labels"].get(v, v.replace("_", " "))


def parse_rules(text):
    """Rules fallback. Returns the TOOL shape."""
    req, clarify, seen = [], None, set()
    for r in Q["rules"]:
        m = re.search(r["rx"], text, re.I)
        if not m: continue
        if "clarify" in r:
            clarify = clarify or {k: r[k] for k in ("attribute", "hint", "direction") if k in r} | {"kind": r["clarify"],
                      "slot": Q["slot_of"][r["attribute"]] if r.get("attribute") else "top" if re.search(Q["top_words"], text, re.I) else "bottom", "sourcePhrase": m.group(0)}
            if r.get("attribute"): seen.add(r["attribute"])
            continue
        if r["attribute"] in seen: continue
        seen.add(r["attribute"])
        slot = Q["slot_of"][r["attribute"]]
        named = None
        if r.get("slot_from_words"):
            named = next((s for s, rx in Q["slot_words"].items() if re.search(rx, text, re.I)), None)
            slot = named or slot
        p = {"slot": slot, "attribute": r["attribute"], "op": r["op"], "sourcePhrase": m.group(0), **({"namedSlot": True} if named else {})}
        if "value" in r: p["value"] = r["value"]
        if r.get("value_from_match"):                  # a named colour: the word itself, aliases folded ("gray" -> grey)
            w = m.group(1).lower(); p["value"] = AT.V["computed_request_attributes"][r["attribute"]].get("aliases", {}).get(w, w)
        req.append(p)
    status = "needs_clarification" if clarify else "supported" if req else "unsupported"
    return {"status": status, "requested": req, **({"clarify": clarify} if clarify else {})}


def validate(raw, text):
    """Deterministic checks on model or rules output. Returns (clean_raw, problems). Never trusts the model."""
    problems, out = [], []
    if not isinstance(raw, dict) or raw.get("status") not in ("supported", "needs_clarification", "unsupported"):
        return {"status": "unsupported", "requested": []}, ["malformed"]
    for p in raw.get("requested", []):
        a, op = p.get("attribute"), p.get("op")
        if a not in INTEGRATED or op not in OPS: problems.append(f"dropped {a}/{op}: not supported"); continue
        if p.get("sourcePhrase", "").lower() not in text.lower(): problems.append(f"dropped {a}: sourcePhrase not in text"); continue
        computed = a in AT.V.get("computed_request_attributes", {})
        if computed and op not in AT.V["computed_request_attributes"][a]["ops"]: problems.append(f"dropped {a}/{op}: not an allowed comparison"); continue
        if not computed and p.get("slot") != Q["slot_of"][a]: problems.append(f"slot for {a} corrected to {Q['slot_of'][a]}"); p = dict(p, slot=Q["slot_of"][a])
        allowed = AT.V["computed_request_attributes"][a].get("values", []) if computed else AT.A[a]["values"]
        if op == "eq" and p.get("value") not in allowed: problems.append(f"dropped {a}: unknown value {p.get('value')}"); continue
        if op in ("lt", "gt") and not computed and not AT.ordered(a):
            # e.g. "lower neckline": valid request, but neckline depth isn't representable -> ask which style
            problems.append(f"{a} has no order; asking which style")
            return {"status": "needs_clarification", "requested": out, "clarify": {"kind": "which_value", "attribute": a, "slot": p["slot"],
                    "hint": "lower|deeper|more open|plunging|lower[- ]cut" if op == "lt" else "higher|more coverage|less (low|revealing)|closed",
                    "sourcePhrase": p["sourcePhrase"]}}, problems
        named = p.get("namedSlot") or any(re.search(rx, p["sourcePhrase"], re.I) for rx in Q["slot_words"].values())
        out.append({k: p[k] for k in ("slot", "attribute", "op", "value", "sourcePhrase") if k in p and (k != "value" or op == "eq")} | ({"namedSlot": True} if named else {}))
    w = raw.get("wish") if isinstance(raw.get("wish"), dict) else {}
    words = re.sub(r"\s+(pair of\s+)?(shoes?|sandals?|heels?|flats?|tops?|blouses?|shirts?|trousers|pants|jeans|dress(es)?|necklaces?)\s*$", "", str(w.get("words") or "").strip(), flags=re.I)[:60]
    wish = {"slot": w["slot"], "words": words} if w.get("slot") in ("top", "bottom", "dress", "shoes", "necklace") and words else None
    st = raw["status"] if raw["status"] != "supported" or out or wish else "unsupported"
    if st == "unsupported" and wish:
        st = "supported"
    clean = {"status": st, "requested": out, **({"wish": wish} if wish else {})}
    c = raw.get("clarify")
    if c and c.get("kind") in ("cut_or_size", "which_value") and (c["kind"] == "cut_or_size" or c.get("attribute") == "neckline"):
        clean["clarify"] = {**c, "slot": c.get("slot") or ("top" if c["kind"] == "which_value" else "bottom")}
    elif c:
        problems.append("dropped clarify: not an allowed kind/attribute")
        if st == "needs_clarification": clean["status"] = "unsupported" if not out else "supported"
    return clean, problems


def _clar(kind, question, options, **kw):
    return {"status": "needs_clarification", "clarification": {"kind": kind, "question": question, "options": options[:3], **kw}}


def plan(raw, look, available, released=()):
    """Fill anchors from the current look and decide: ready | needs_clarification | cannot | unsupported.
    look: {slot: {"attributes": {...}, "owned": bool, "pinned": bool, "label": "your blouse"}}
    available(slot, attr) -> set of values present in the selectable pool (used only to offer real options)."""
    c = raw.get("clarify")
    if c and c["kind"] == "cut_or_size":
        piece = "top" if c.get("slot") == "top" else "trousers"
        if piece == "trousers" and "dress" in look and raw.get("structure") != "separates":      # nothing to ask: there are no trousers
            return {"status": "cannot", "unmet": [], "suggestion": None,
                    "message": "This look has a dress, so there are no trousers to change. Ask for separates if you'd like to switch."}
        if c.get("direction") == "bigger":
            return _clar("cut_or_size", f"Do you mean a roomier cut, or the same {piece} in a bigger size?", ["A roomier cut", "A bigger size"], slot=c.get("slot"))
        return _clar("cut_or_size", f"Do you mean a slimmer cut, or the same {piece} in a smaller size?", ["A slimmer cut", "A smaller size"], slot=c.get("slot"))
    if c and c["kind"] == "which_value":
        a, slot = c["attribute"], c.get("slot", Q["slot_of"].get(c["attribute"], "top"))
        if a in ("neckline", "sleeve_length"):
            slot = "dress" if "dress" in look else "top"          # a neckline belongs to the top, or to the dress
        hint = next((h for k, h in AT.A[a].get("request_hints", {}).items() if re.search(k, c.get("hint", "") + " " + c.get("sourcePhrase", ""), re.I)), None)
        cur = set(AT.value(look.get(slot, {}).get("attributes"), a) or [])
        offer = hint["options"] if hint else list(AT.A[a]["values"])
        opts = [v for v in offer if v not in cur and v in available(slot, a)]
        if not opts:     # never ask a question with no real answers
            return {"status": "cannot", "unmet": [], "suggestion": None,
                    "message": (f"I can't tell exactly how low a neckline sits, and the more open styles ({', '.join(label(v) for v in offer if v not in cur)}) aren't available right now."
                                if hint and "open" in hint["label"] else f"There isn't another {a.replace('_', ' ')} available in your colors right now.")}
        q = (f"I can't tell exactly how low a neckline sits, but I can change its style. Which would you like?" if hint and "open" in hint["label"]
             else f"Which {a.replace('_', ' ')} would you like?")
        return _clar("which_value", q, [label(v) for v in opts], attribute=a, slot=slot, values=opts[:3])
    target, cur = raw.get("structure"), "dress" if "dress" in look else "separates"
    switch = target if target and target != cur else None
    wish = raw.get("wish")
    if wish and not raw["requested"] and not switch:      # an open wish: the app searches that piece's eligible products and Nova chooses
        if wish["slot"] not in look:
            have = "a dress" if "dress" in look else "trousers and a top"
            return {"status": "cannot", "unmet": [], "suggestion": None,
                    "message": f"This look has {have}, so there's no {SLOT_NAME[wish['slot']]} to change. Ask for {'separates' if 'dress' in look else 'a dress'} to switch."}
        if look[wish["slot"]].get("owned") and wish["slot"] not in released:
            return _clar("release_owned", f"Your {look[wish['slot']].get('label') or 'top'} is in every look. Should I swap it for a new one?",
                         ["Swap it for a new top", f"Keep your {look[wish['slot']].get('label') or 'top'}"], slot=wish["slot"])
        return {"status": "wish", "wish": wish}
    if raw["status"] == "unsupported" or not (raw["requested"] or switch):
        return {"status": "unsupported"}
    if switch == "dress":
        top = look.get("top", {})
        if (top.get("owned") or top.get("pinned")) and "top" not in released:
            kind = "your " + (top.get("label") or "top") if top.get("owned") else "your pinned top"
            return _clar("release_owned", f"A dress replaces {kind}. Should I swap it for a dress?", ["Swap it for a dress", f"Keep {kind}"], slot="top")
    new_slots = {"dress": ("dress", "shoes", "necklace"), "separates": ("top", "bottom", "shoes", "necklace")}[switch or cur]
    prefs = []
    for p in raw["requested"]:
        if p["slot"] in ("top", "bottom") and "dress" in new_slots and (p["attribute"] in ("neckline", "sleeve_length") or (p["attribute"] in ("lightness", "color") and not p.get("namedSlot"))):
            p = dict(p, slot="dress")                     # on a dress, neckline, sleeves and colour belong to the dress
        if p["slot"] not in new_slots:
            have = "a dress" if "dress" in new_slots else "trousers and a top"
            return {"status": "cannot", "unmet": [p], "suggestion": None,
                    "message": f"This look has {have}, so I can't make {AT.describe(p)}. Ask for {'separates' if 'dress' in new_slots else 'a dress'} if you'd like to switch."}
        piece = {} if switch else look.get(p["slot"], {})
        if (piece.get("owned") or piece.get("pinned")) and p["slot"] not in released:
            what = " and ".join(AT.describe(x) for x in raw["requested"] if x["slot"] == p["slot"])
            kind = "your " + ((piece.get("label") or "top") if piece.get("owned") else "pinned piece")
            return _clar("release_owned", f"{kind[0].upper() + kind[1:]} is in every look. Should I swap it for a new top with {what}?",
                         ["Swap it for a new top", f"Keep {kind}"], slot=p["slot"])
        q = dict(p)
        if p["attribute"] in AT.V.get("computed_request_attributes", {}):
            q["anchor"] = {"productId": piece.get("id"), "values": [piece["hex"]] if piece.get("hex") else []}
        elif p["op"] in ("lt", "gt", "neq"):
            q["anchor"] = {"productId": piece.get("id"), "values": AT.value(piece.get("attributes"), p["attribute"]) or []}
        issue = AT.check_pref(q)
        if issue == "anchor_unknown" and p["attribute"] in AT.V.get("computed_request_attributes", {}):
            return {"status": "cannot", "unmet": [q], "suggestion": None, "message": f"I don't have a measured color for your current {p['slot']}, so I can't tell what's darker or lighter."}
        if issue == "anchor_unknown":
            r = AT.rank(p["attribute"])
            fits = lambda v: p["op"] not in ("lt", "gt") or v not in r or (r[v] < max(r.values()) if p["op"] == "lt" else r[v] > min(r.values()))
            # Owner correction: with an unknown anchor, ask for an absolute target and never claim it is lower/higher than the current piece.
            targets = [v for v in AT.A[p["attribute"]]["values"] if fits(v)]          # "lower" never offers the top of the scale
            opts = [v for v in targets if v in available(p["slot"], p["attribute"])]
            noun = {"rise": "rise", "leg_cut": "leg shape", "neckline": "neckline", "sleeve_length": "sleeve length"}.get(p["attribute"], p["attribute"].replace("_", " "))
            piece, dont, their, pl = ("trousers", "don't", "their", "trousers") if p["slot"] == "bottom" else (p["slot"], "doesn't", "its", p["slot"] + "s")
            if not opts:
                return {"status": "cannot", "unmet": [q], "suggestion": None,
                        "message": f"Your {piece} {dont} list {their} {noun}. None of the {pl} I can show you are listed as " + " or ".join(label(v).lower() for v in targets) + "."}
            return _clar("anchor_unknown", f"Your {piece} {dont} list {their} {noun}, so I can't tell what's lower. Which {noun} would you like?",
                         [label(v) for v in opts], attribute=p["attribute"], slot=p["slot"], values=opts[:3])
        if issue == "edge_of_scale":
            have = label(q["anchor"]["values"][-1])
            others = [x for x in raw["requested"] if x is not p]
            return {"status": "cannot", "unmet": [q],
                    "message": Q["edge"][f"{p['attribute']}:{p['op']}"].format(piece=piece.get("label", "piece")),
                    "suggestion": ("Want " + " and ".join(AT.describe(x) for x in others) + f", keeping {have.lower()} {p['attribute'].split('_')[0]}s?") if others else None}
        prefs.append(q)
    return {"status": "ready", "requested": prefs, "notes": raw.get("notes", []), "structure": switch}


def answer(clarification, choice, raw):
    """Turn a tapped clarification answer into a new raw request (or a size change, which is not an attribute query)."""
    k = clarification["kind"]
    if k == "cut_or_size":                          # the rest of the request ("darker and tighter") is kept, never dropped
        keep = [p for p in raw.get("requested", []) if p["attribute"] != "leg_cut"]
        if "size" in choice.lower(): return {"status": "size_change", "direction": "smaller" if "smaller" in choice.lower() else "bigger", "slot": clarification.get("slot") or "bottom", "requested": keep}
        op = "lt" if "slimmer" in choice.lower() else "gt"
        return {"status": "supported", "requested": keep + [{"slot": "bottom", "attribute": "leg_cut", "op": op, "sourcePhrase": choice}]}
    if k in ("which_value", "anchor_unknown"):
        v = clarification["values"][clarification["options"].index(choice)]
        keep = [p for p in raw.get("requested", []) if p["attribute"] != clarification["attribute"]]
        notes = [f"Your current pair doesn't list its {clarification['attribute'].replace('_', ' ')}, so I can't say this is lower."] if k == "anchor_unknown" else []
        return {"status": "supported", "notes": notes,
                "requested": keep + [{"slot": clarification["slot"], "attribute": clarification["attribute"], "op": "eq", "value": v, "sourcePhrase": choice}]}
    if k == "release_owned":
        return dict(raw, released=[clarification["slot"]] if choice.startswith("Swap") else [], kept=not choice.startswith("Swap"))
    raise ValueError(k)


if __name__ == "__main__":
    t = "Can I get a lower neckline?"
    raw, _ = validate(parse_rules(t), t)
    assert raw["status"] == "needs_clarification" and raw["clarify"]["kind"] == "which_value"
    bad, probs = validate({"status": "supported", "requested": [{"slot": "top", "attribute": "neckline", "op": "lt", "sourcePhrase": "lower neckline"}]}, "a lower neckline please")
    assert bad["status"] == "needs_clarification" and "no order" in probs[0]
    inv, probs = validate({"status": "supported", "requested": [{"slot": "bottom", "attribute": "rise", "op": "lt", "sourcePhrase": "invented"}]}, "lower rise")
    assert inv["status"] == "unsupported" and "sourcePhrase" in probs[0]
    assert parse_rules("make them tighter")["clarify"]["kind"] == "cut_or_size"
    # model path: canned stub output that invents an order is corrected by validate(); failure falls back to rules
    stub = StubNova([{"text": "a lower neckline", "model_stub": {"status": "supported", "requested": [{"slot": "top", "attribute": "neckline", "op": "lt", "sourcePhrase": "lower neckline"}]}}])
    out, meta = interpret("a lower neckline", stub)
    assert out["status"] == "needs_clarification" and meta["interpreter"] == "nova-stub" and meta["problems"]
    class Down:
        mode = "nova"
        def call(self, *a, **k): raise TimeoutError()
    out, meta = interpret("Can I try a lower rise?", Down())
    assert out["requested"][0]["op"] == "lt" and meta["interpreter"].startswith("rules (model unavailable")
    t = "Show me darker trousers with a narrower leg. Keep everything else."
    both = validate(parse_rules(t), t)[0]["requested"]
    assert {(p["slot"], p["attribute"], p["op"]) for p in both} == {("bottom", "lightness", "lt"), ("bottom", "leg_cut", "lt")}, both
    class Half:
        mode = "nova"
        def call(self, *a, **k): return {"status": "supported", "requested": [{"slot": "bottom", "attribute": "leg_cut", "op": "lt", "sourcePhrase": "narrower"}]}, 0
    out, meta = interpret(t, Half())
    assert len(out["requested"]) == 2 and "omitted lightness" in meta["problems"][0], "a dropped condition is restored, never silently ignored"
    class Ask:
        mode = "nova"
        def call(self, *a, **k): return {"status": "needs_clarification", "clarify": {"kind": "cut_or_size", "attribute": "lightness"}}, 0
    out, meta = interpret("darker and tighter trousers", Ask())
    assert out["status"] == "needs_clarification" and [p["attribute"] for p in out["requested"]] == ["lightness"], "kept while asking"
    # composition: a dress request on separates switches (asking first about an owned top); dress attributes route to the dress
    av = lambda slot, a: set(AT.A[a]["values"])
    sep = {"top": {"owned": True, "label": "blue blouse", "attributes": {}}, "bottom": {"attributes": {}}, "shoes": {}, "necklace": {}}
    r = interpret("Can I see a dress instead?")[0]
    assert r["structure"] == "dress" and plan(r, sep, av)["clarification"]["kind"] == "release_owned"
    assert plan(r, sep, av, released=("top",))["structure"] == "dress"
    drs = {"dress": {"attributes": {"dress_length": {"values": ["midi"], "status": "explicit"}, "neckline": {"values": ["crew"], "status": "explicit"}}}, "shoes": {}, "necklace": {}}
    r = interpret("a longer dress with a v-neck")[0]
    pl = plan(r, drs, av); assert pl["status"] == "ready" and {p["slot"] for p in pl["requested"]} == {"dress"} and pl["structure"] is None, pl
    assert plan(interpret("darker trousers")[0], drs, av)["status"] == "cannot"
    assert plan(interpret("darker and tighter trousers")[0], drs, av)["status"] == "cannot", "no cut-or-size question on a dress"
    assert interpret("darker dress shoes")[0].get("structure") is None
    assert interpret("a lower neckline")[0]["clarify"]["slot"] == "top", "a neckline question is about the top"
    assert {p.get("value") for p in interpret("low rise jeans with a bootcut leg")[0]["requested"]} == {"jeans", "low", "bootcut"}, "no condition dropped"
    raw = interpret("darker and tighter trousers")[0]; cl = {"kind": "cut_or_size", "slot": "bottom"}
    assert [p["attribute"] for p in answer(cl, "A slimmer cut", raw)["requested"]] == ["lightness", "leg_cut"], "answering keeps the other conditions"
    assert [p["attribute"] for p in answer(cl, "A smaller size", raw)["requested"]] == ["lightness"]
    print("change_request self-check ok")
