"""The shared experiment budget holds across processes: concurrent reservations never exceed the cap, uncertain outcomes
count, and only provider-reported errors release units.   .venv/bin/python tests/check_budget.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
d = Path(tempfile.mkdtemp()); f = d / "budget.json"
child = r'''
import os, sys; sys.path.insert(0, sys.argv[1]); import engine as R
from pathlib import Path; R.STATE = Path(sys.argv[2])
ok = 0
for i in range(40):
    try: R._budget("reserve", f"{sys.argv[2]}|s{i}", 2); ok += 1
    except R.ProviderError: pass
    if i % 3 == 0: R._budget("set", f"{sys.argv[2]}|s{i}", status="uncertain")
    elif i % 3 == 1: R._budget("set", f"{sys.argv[2]}|s{i}", status="released")
print(ok)
'''
env = dict(os.environ, CALLIOPE_BUDGET_FILE=str(f), CALLIOPE_BUDGET_UNITS="50")
ps = [subprocess.Popen([sys.executable, "-c", child, str(ROOT), f"ns{k}"], env=env, stdout=subprocess.PIPE, text=True) for k in range(4)]
got = [int(p.communicate()[0]) for p in ps]
rows = json.loads(f.read_text())["rows"]
held = sum(r["units"] for r in rows if r["status"] in ("reserved", "submitted", "consumed", "uncertain"))
assert held <= 50, held
assert any(r["status"] == "released" for r in rows) and any(r["status"] == "uncertain" for r in rows)
assert sum(got) > 25, got          # released units were reusable, uncertain ones were not
print(f"budget self-check ok | 4 processes, {sum(got)} reservations granted, {held}/50 units held, {len(rows)} rows")

# run_task wiring with a fake provider: consumed holds, a provider error releases, an unknown submit holds, the cap blocks the POST
import tempfile as _t
os.environ.update(CALLIOPE_BUDGET_FILE=str(d / "b2.json"), CALLIOPE_BUDGET_UNITS="5")
sys.path.insert(0, str(ROOT)); import engine as R
R.STATE = Path(_t.mkdtemp()); R.OUT = Path(_t.mkdtemp()); R.CAP_UNITS = 999; R.time.sleep = lambda s: None
R.upload = lambda p: "file"; R.balance = lambda: None
posts, outcome = [], {}
def fake(method, path, body=None, timeout=30):
    if method == "POST":
        posts.append(path)
        if outcome.get("post") == "boom": raise R.ProviderError("HTTP 429 on submit")
        return {"data": {"task_id": f"t{len(posts)}"}}
    return {"data": {"task_status": outcome.get("poll", "success"), "results": {"x": 1}, "error": "pose"}}
R.yc = fake
src = d / "src.jpg"; src.write_bytes(b"x")
R.run_task("ok", "/e", lambda a, b: {}, src, None, 2, "o")
outcome["poll"] = "error"
try: R.run_task("err", "/e", lambda a, b: {}, src, None, 2, "o")
except R.ProviderError: pass
outcome.update(post="boom")
try: R.run_task("unk", "/e", lambda a, b: {}, src, None, 2, "o")
except R.ProviderError: pass
n = len(posts)
try: R.run_task("over", "/e", lambda a, b: {}, src, None, 2, "o"); raise SystemExit("cap not enforced")
except R.ProviderError as e: assert "experiment budget" in str(e), e
st = {r["key"].split("|")[1]: r["status"] for r in json.loads((d / "b2.json").read_text())["rows"]}
assert st == {"ok": "consumed", "err": "released", "unk": "uncertain"}, st
assert len(posts) == n == 3, posts                     # the over-cap attempt never reached the provider
print("run_task budget wiring ok |", st, "| over-cap attempt blocked before the POST (4 of 5 units held)")
