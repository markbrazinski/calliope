"""One-off migration (2026-10-13): keep the pinned v1 index's classifications instead of paying for them again.

The v1 index (data/catalog-index.json until 2026-10-13, 760 products; kept locally in state/catalog/legacy-v1/) was classified from the dataset copy of 2026-10-02: Nova colour,
pattern, gender, heel and best photo for trousers, shoes and necklaces (artifacts/spikes/2026-10-02/color/nova_color.py),
a Nova formality pass for trousers and shoes whose prompt wasn't kept, a Nova item count for necklaces, and photo pixels
plus attribute rules for tops and dresses (tools/build_catalog.py). This tool reads the catalog through the MCP now and
records each v1 classification under the product's current input hash, only when the inputs are verifiably the ones it
was made from: same id, title and listed colours, and the v1 try-on photo is still one of the product's photos.
Products that fail the check are simply classified again by the next build.

    .venv/bin/python tools/seed_classifications.py [state/catalog/legacy-v1/catalog-index.json]   # writes data/classifications.json (already done)
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import catalog_sync as C  # noqa: E402

FIELDS = ("pictured_color", "hex", "color_source", "pattern", "gender", "heel_height", "formality", "formality_details", "single_item")

if __name__ == "__main__":
    v1 = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "state/catalog/legacy-v1/catalog-index.json").read_text())
    F = C.fetch(progress=lambda **k: None)
    out, skipped = {}, {}
    for it in v1:
        p = F["prods"].get(it["handle"])
        why = "not in the catalog now" if not p else "id differs" if p["id"] != it["id"] else "title differs" if p["title"] != it["title"] else None
        if not why:
            colors = list(dict.fromkeys(v["options"].get("Color") for v in p["variants"] if v["options"].get("Color")))
            urls = [i["url"] for i in p["images"]]
            why = "listed colours differ" if set(colors) != set(it["colors"]) else "try-on photo no longer listed" if it["tryon_image"] not in urls else \
                "slot differs" if C.slot_of(p) != it["slot"] else None
        if why:
            skipped[it["handle"]] = why; continue
        made = "tools/build_catalog.py (photo pixels + attribute rules)" if it.get("catalog_source") == "build_catalog.py" else \
            "2026-10-02 spike (Nova describe" + (", Nova formality" if it["slot"] in ("bottom", "shoes") else "") + (", Nova item count" if it["slot"] == "necklace" else "") + \
            (", photo pixels for colour" if it["slot"] in ("bottom", "top") else "") + ")"
        rec = {f: it.get(f) for f in FIELDS if f in it} | {"image": urls.index(it["tryon_image"]), "method": C.METHODS[it["slot"]],
                                                          "source": "seed: pinned v1 index", "madeBy": made, "at": "2026-10-05"}
        out[C.key(it["slot"], F["products"][it["handle"]]["inputHash"])] = rec
    doc = {"note": __doc__.split("\n\n")[0], "builtAt": C.now(), "fromIndex": "data/catalog-index.json (v1, 760 products)",
           "verifiedAgainst": {"mcp": "livostyle-catalog-mcp@0.1.0", "lastSynced": F["stats"].get("last_synced"), "supplementSha256": F["supSha"]},
           "seeded": len(out), "skipped": skipped, "records": out}
    (ROOT / "data" / "classifications.json").write_text(json.dumps(doc, indent=0))
    print(f"seeded {len(out)} of {len(v1)}; skipped {len(skipped)}:", json.dumps(skipped, indent=1)[:2000])
