"""The standing spend authorization (state/authorization.json) is cumulative: what is left comes from the engine's ledger
and Nova log, so restarting the app never grants it again; a used-up authorization blocks live work with a clear reason.
  .venv/bin/python tests/check_authorization.py        (temporary files only; no provider calls)"""
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine as R
import shopper as A

tmp = Path(tempfile.mkdtemp()); (tmp / "state").mkdir(); eng = tmp / "engine"
R.use_dirs(eng / "state", eng / "out"); A.ROOT = tmp
row = lambda u, c="consumed": {"units_expected": u, "consumption": c}
(eng / "state" / "ledger.json").write_text(json.dumps([row(8), row(2)]))
(eng / "state" / "nova_calls.jsonl").write_text(json.dumps({"cost": 0.008}) + "\n" + json.dumps({"cost": 0.002}) + "\n")
(tmp / "state" / "authorization.json").write_text(json.dumps({"authorizedOn": "2026-10-08", "youcamUnits": 50, "novaUsd": 0.10, "engine": "engine",
    "usedElsewhere": {"youcamUnits": 5, "novaUsd": 0.002}, "baseline": {"committedUnits": 8, "novaUsd": 0.008}}))

a = A.authorization(eng)
assert (a["unitsLeft"], a["novaUsdLeft"]) == (43, 0.096), a                 # 50 - 5 - (10 - 8); 0.10 - 0.002 - 0.002
(eng / "state" / "ledger.json").write_text(json.dumps([row(8), row(2), row(3, "uncertain")]))
assert A.authorization(eng)["unitsLeft"] == 40, "a restart after spending must not grant the units again"
assert A.authorization(tmp / "other-engine") is None, "the record applies only to its own engine"
(eng / "state" / "ledger.json").write_text(json.dumps([row(8), row(47)]))
a = A.authorization(eng); p = A.provider_problems(a["unitsLeft"], a["novaUsdLeft"], a)
assert a["unitsLeft"] == 0 and any("used up" in x for x in p), p
print("authorization check ok:", p[-1])
