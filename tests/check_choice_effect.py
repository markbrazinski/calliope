"""Does a different round-1 choice change what Calliope shows next, with everything else equal?
Same photo, same brief, stub pictures (ranking never depends on pictures). Prints the evidence as JSON; no paid calls.
    .venv/bin/python tests/check_choice_effect.py"""
import io, json, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
import shopper as A, engine as R  # noqa: E402
from PIL import Image, ImageDraw

tmp = Path(tempfile.mkdtemp()); A.configure(tmp / "app", provider="stub", stub_delay=0, engine_dir=tmp / "engine")
im = Image.new("RGB", (700, 1200), (170, 165, 160)); ImageDraw.Draw(im).ellipse([300, 80, 400, 200], fill=(200, 160, 130)); b = io.BytesIO(); im.save(b, "JPEG"); PHOTO = b.getvalue()
BRIEF = {"occasion": "Anniversary dinner", "budget": 200, "approx": True, "heels": True, "structure": None}

def wait(jid):
    while A.S["jobs"][jid]["status"] == "running": time.sleep(0.02)

def ids(look): return {sl: v.get("id") for sl, v in look.items() if isinstance(v, dict)}

def session(structure, r1, r2="keep", keys=()):
    tok, _ = A.new_session(); s = A.owner(tok); A.upload_photo(s, PHOTO, consent=True)
    A.journey_start(s, dict(BRIEF, structure=structure))
    while s["journey"]["analysis"]["status"] == "running": time.sleep(0.02)
    A.journey_palette(s, "No palette"); jr = s["journey"]
    for l in jr["round1"]: wait(jr["jobs"][l])
    A.journey_keep(s, 1, r1)
    sig1 = [dict(text=R.signal_text(x), conf=x["confidence"], net=x["net"]) for x in jr["learning"]["inferred"]]
    ch = jr.get("challenger", "C"); wait(jr["jobs"][ch])
    # what the challenger would have been with no signals (same pool, structure rule only)
    n = len(jr["round1"]); kept = jr["looks"][r1]["look"]; shown = {A._key(jr["looks"][x]["look"]) for x in jr["round1"]}
    pool = [l for l in jr["reserve"][n:] if A._key(l) not in shown]
    nosig = sorted(pool, key=lambda l: R.structure_of(l) != R.structure_of(kept))[0] if pool else None
    A.journey_keep(s, 2, r1 if r2 == "keep" else ch)
    A.journey_finalize(s, list(keys)); wait(jr["jobs"]["P2"])
    L = jr["learning"]
    return {"round1": {k: ids(jr["looks"][k]["look"]) for k in jr["round1"]}, "kept": r1,
            "differences_r1": [f"{d['slot']}.{d['attribute']}" for d in L["observed"][0]["differences"]],
            "signals_after_r1": sig1, "challenger": ids(jr["looks"][ch]["look"]), "challenger_without_signals": ids(nosig) if nosig else None,
            "signals_final": [dict(text=R.signal_text(x), conf=x["confidence"], status=x["status"]) for x in L["inferred"]],
            "stated": L["stated"], "P2": ids(jr["looks"]["P2"]["look"]), "P2_tag": jr["looks"]["P2"]["tag"], "signalsFollowed": jr["carry"].get("signalsFollowed")}

out = {"A": session("separates", "A"), "B": session("separates", "B")}
out["same_challenger"] = out["A"]["challenger"] == out["B"]["challenger"]
out["same_P2"] = out["A"]["P2"] == out["B"]["P2"]
out["either_dress_vs_separates"] = session("either", "B")              # a whole-structure choice: should give weak signals only
out["A_with_stated_colors"] = session("separates", "A", keys=("colors",))
out["A_with_stated_shape"] = session("separates", "A", keys=("shape",))
print(json.dumps(out, indent=1))
assert not out["same_challenger"] and not out["same_P2"], "opposite round-1 picks should lead to different next looks"
assert all(x["conf"] == "weak" for x in out["either_dress_vs_separates"]["signals_after_r1"]), "a dress-vs-separates pick gives weak signals only"
