"""Free checks for the early-generation schedule (development stub, temporary state; no paid calls).
    .venv/bin/python tests/check_schedule.py
Checks: concurrency at upload, deduplication (analysis, owned layer, early candidates), budget reservations under load,
stale-result handling after a palette change, the cross-session provider concurrency bound, and API responsiveness."""
import io, json, os, sys, tempfile, threading, time
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CALLIOPE_PROVIDER_CONCURRENCY"] = "3"
import engine as R  # noqa: E402
import shopper as A  # noqa: E402

R.PROVIDER_SLOTS = threading.BoundedSemaphore(3)
live, peak = [0], [0]
inner = R._run_task
def counted(*a, **k):                     # how many provider tasks are in flight at once
    live[0] += 1; peak[0] = max(peak[0], live[0])
    try: return inner(*a, **k)
    finally: live[0] -= 1
R._run_task = counted

def img(color, size=(700, 1200)):
    im = Image.new("RGB", size, color); ImageDraw.Draw(im).ellipse([300, 80, 400, 200], fill=(200, 160, 130)); b = io.BytesIO(); im.save(b, "JPEG"); return b.getvalue()

def until(fn, t=60):
    for _ in range(int(t / 0.05)):
        v = fn()
        if v: return v
        time.sleep(0.05)
    raise AssertionError("timed out")

def ledger(): return R.jl("ledger.json", [])
def events(s, name): return [e for e in s["events"] if e["event"] == name]
ok = lambda c, m: print(("  ✓ " if c else "  ✗ ") + m) or (c or fails.append(m))
fails = []

tmp = Path(tempfile.mkdtemp())
A.configure(tmp / "app", provider="stub", stub_delay=0.5, engine_dir=tmp / "engine", schedule="early", round1=3)

# 1. upload starts analysis + necklace check at once; the owned top layer starts when photo and top are both confirmed
tok, _ = A.new_session(); s = A.owner(tok)
t0 = time.time(); A.upload_photo(s, img((180, 180, 190)), consent=True); up = time.time() - t0
ok(up < 1.5, f"upload returns in {up:.2f} s, before the checks")
A.upload_owned_top(s, img((40, 120, 60), (600, 600)), "my green top"); A.confirm_owned_top(s, "blouse", "green")
until(lambda: s["photo"]["checks"]["necklace"] != "checking")
until(lambda: events(s, "owned_layer_ready"))
rows = ledger(); kinds = [r["endpoint"].rsplit("/", 1)[-1] for r in rows]
ok("skin-tone-analysis" in kinds and kinds.count("necklace") == 2 and kinds.count("cloth-v4") == 1,
   f"before the brief: analysis, photo necklace check, owned layer and its necklace check ran ({kinds})")

# 2. brief -> colours (already analysed: no second analysis) -> early candidates for the suggested palette
v = A.journey_start(s, {"occasion": "Anniversary dinner", "budget": 200, "approx": True, "heels": True})
until(lambda: s["journey"]["analysis"]["status"] != "running")
until(lambda: events(s, "prestart"))
ok([r["endpoint"].rsplit("/", 1)[-1] for r in ledger()].count("skin-tone-analysis") == 1, "the colour analysis ran once (upload and fitting room shared it)")
pre = s["journey"]["prestart"]["jobs"]
ok(len(pre) == 3, "three initial pictures started while the palette is on screen")
t1 = time.time(); A.session_view(s); lat = time.time() - t1
ok(lat < 0.5, f"session reads stay responsive while pictures run ({lat * 1000:.0f} ms)")
n_before = len(ledger())
A.journey_palette(s, s["journey"]["palette"]["name"])
ok(sorted(s["journey"]["jobs"].values()) == sorted(pre), "continuing with the suggested palette reuses the early pictures (same jobs)")
ok(len(ledger()) == n_before, "no new submission at palette continue")
ok(s["journey"]["round1"] == ["A", "B", "C"] and s["journey"]["challenger"] == "D", "round 1 = A, B, C; the challenger will be D")

# 3. stale results: a different palette after early starts -> other looks; early ones never shown
tok2, _ = A.new_session(); s2 = A.owner(tok2)
A.upload_photo(s2, img((150, 160, 175)), consent=True)
A.journey_start(s2, {"occasion": "Anniversary dinner", "budget": 200, "approx": True, "heels": True, "structure": "either"})
until(lambda: events(s2, "prestart") or s2["journey"]["analysis"]["status"] == "unavailable")
early = s2["journey"].get("prestart", {}).get("jobs", [])
A.journey_palette(s2, "No palette")
shown = set(s2["journey"]["jobs"].values())
ok(early and not (shown & set(early)) or not early, f"after choosing No palette, none of the {len(early)} early pictures is shown")
view = json.dumps(A.session_view(s2))
ok(all(j not in view for j in early if j not in shown), "stale early pictures don't appear anywhere in the session view")

# 4. concurrency bound across sessions, and budget reservations under load
until(lambda: all(A.S["jobs"][j]["status"] != "running" for j in list(shown) + early + pre), 120)
ok(peak[0] <= 3, f"provider tasks in flight never exceeded the bound (peak {peak[0]} of 3)")
R.CAP_UNITS = R.units_committed() + 5                     # room for about two stages
tok3, _ = A.new_session(); s3 = A.owner(tok3)
A.upload_photo(s3, img((120, 110, 100)), consent=True)
until(lambda: s3["photo"]["checks"]["necklace"] != "checking")
A.journey_start(s3, {"occasion": "Work dinner", "budget": 0})
until(lambda: s3["journey"]["analysis"]["status"] != "running")
A.journey_palette(s3, (s3["journey"].get("palette") or {}).get("name") or "No palette")
until(lambda: all(A.S["jobs"][j]["status"] != "running" for j in s3["journey"]["jobs"].values()), 120)
ok(R.units_committed() <= R.CAP_UNITS, f"committed units stay within the cap under concurrent pictures ({R.units_committed()} ≤ {R.CAP_UNITS})")
ok(any(A.S["jobs"][j]["status"] == "failed" and "cap" in (A.S["jobs"][j].get("error") or "") for j in s3["journey"]["jobs"].values()),
   "pictures beyond the cap fail honestly (cap reached), nothing is half-submitted")
dup = [r["step"] for r in ledger()]
ok(len(dup) == len(set(dup)), "no stage submitted twice")
print("FAIL" if fails else "PASS", f"· {len(fails)} failed")
sys.exit(1 if fails else 0)
