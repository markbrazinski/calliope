"""Open wishes ("more fun shoes"): Nova names the piece and her words, the engine shortlists that piece's eligible products
(every hard rule applied), Nova chooses from that list only (development stub pictures; Nova scripted: no paid calls).

    .venv/bin/python tests/check_wish.py

Checked: the wish becomes a two-option comparison where only the shoes change; every option is on the shortlist (so within
budget and rules) and is pictured; an id Nova invents is ignored; when nothing fits it says so; without Nova the request
gets a plain message instead of a stale one.
"""
import re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
import check_revisions as C  # noqa: E402

A = C.A; R = A.R


class FakeNova:
    mode = "nova"

    def __init__(self, pick="first two"):
        self.pick, self.calls = pick, []

    def call(self, system, content, tool, max_tokens=300):
        self.calls.append(tool["name"])
        if tool["name"] == "record_change_request":
            return {"status": "supported", "requested": [], "wish": {"slot": "shoes", "words": "more fun"}}, 0.0
        ids = re.findall(r"^(\d+) \|", content[0]["text"], re.M)
        if self.pick == "none":
            return {"choices": [], "none_fit": True}, 0.0
        if self.pick == "invented":
            return {"choices": [{"id": "999", "why": "made up"}, {"id": ids[0], "why": "rhinestone straps"}]}, 0.0
        return {"choices": [{"id": ids[0], "why": "metallic finish"}, {"id": ids[1], "why": "bright colour"}]}, 0.0


def main():
    tmp = Path(tempfile.mkdtemp())
    A.configure(tmp / "app", provider="stub", nlu="stub", stub_delay=0, engine_dir=tmp / "engine")
    s = C.journey({"occasion": "anniversary dinner", "budget": 200, "heels": True, "structure": "separates"})
    A.NOVA, A.CFG["nlu"] = FakeNova(), "nova"
    base = A._cur(s)["composition"]
    r = A.request_change(s, "I would like more fun shoes")
    ms = [c for c in r["candidates"] if c["kind"] == "match"]
    assert r["status"] == "open" and len(ms) == 2 and r["acknowledgement"] == "Got it: more fun shoes.", r
    R.DATA["brief"] = s["brief"]
    allowed = {x["id"] for x in R.shortlist(base, "shoes", s.get("palette"), A.IDX)}
    for c in ms:
        comp = s["revisions"][r["revisionId"]]["candidates"][int(c["candidateId"][4:]) - 1]["composition"]
        assert c["changedSlots"] == ["shoes"] and all(comp[sl] == base[sl] for sl in base if sl != "shoes"), c["changedSlots"]
        assert comp["shoes"]["id"] in allowed and c["previewJob"] and c["subtotal"] <= 200, c
        assert all(R.passes(A.BY_ID[comp["shoes"]["id"]], R.DATA["constraints"]["always"] + s["brief"]["constraints"])[0] for _ in [0]), "no heels holds"
    print("wish -> 2 options, only the shoes change, both from the shortlist (budget and no-heels hold), both pictured:",
          [c["why"] for c in ms])
    A.dismiss(s, r["revisionId"])
    A.NOVA = FakeNova("invented"); r = A.request_change(s, "I would like more fun shoes")
    assert r["status"] == "open" and len([c for c in r["candidates"] if c["kind"] == "match"]) == 1 and r["onlyOption"], r
    print("an id Nova invents is ignored; one real option, said so")
    A.dismiss(s, r["revisionId"])
    A.NOVA = FakeNova("none"); r = A.request_change(s, "I would like more fun shoes")
    assert r["status"] == "no_match" and "more fun" in r["noMatch"]["message"], r
    print("nothing fits:", r["noMatch"]["message"])
    A.dismiss(s, r["revisionId"])
    A.NOVA, A.CFG["nlu"] = None, "rules"
    r = A.request_change(s, "I would like more fun shoes")
    assert r["status"] == "unsupported" and "Name the piece" in r["message"] and "aren’t connected" not in r["message"], r
    print("without Nova:", r["message"])
    print("wish checks ok")


if __name__ == "__main__":
    main()
