"""Local stand-in for the YouCam API: no network, no units, no ledger impact.

Implements the three adapter calls the generation pipeline makes (upload, yc, balance) with the same shapes as the
real API, so the real pipeline code (submit once, poll, download, write-once outputs) runs unchanged.
Outputs are the parent image with a SIMULATED banner and the garment thumbnail; they can't pass for a try-on.
    python3 provider_stub.py      # self-check
"""
import hashlib, json, time, uuid
from pathlib import Path
from PIL import Image, ImageDraw


class StubYouCam:
    def __init__(self, out_dir, delay_s=1.0, fail=(), analysis_results=None, fail_message="simulated provider failure (0 units)"):
        self.out, self.delay, self.fail, self.fail_message = Path(out_dir), delay_s, set(fail), fail_message   # fail: endpoint substrings that return a provider error
        self.analysis_results = analysis_results       # colour analysis: a result, or a function of the input image (synthetic)
        self.out.mkdir(parents=True, exist_ok=True)
        self.calls, self._db = [], self.out / "stub-tasks.json"      # tasks survive a restart, like real task ids do
        d = json.loads(self._db.read_text()) if self._db.exists() else {"files": {}, "tasks": {}}
        self.files = {k: Path(v) for k, v in d["files"].items()}
        self.tasks = {k: {**v, **({"output": Path(v["output"])} if v.get("output") else {})} for k, v in d["tasks"].items()}

    def _persist(self):
        self._db.write_text(json.dumps({"files": {k: str(v) for k, v in self.files.items()},
                                        "tasks": {k: {**v, **({"output": str(v["output"])} if v.get("output") else {})} for k, v in self.tasks.items()}}))

    def upload(self, path):
        path = Path(path)
        fid = "stubfile_" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        self.files[fid] = path
        self._persist()
        return fid

    def balance(self):
        return None

    def yc(self, method, path, body=None, timeout=30):
        self.calls.append((method, path))
        if path.endswith("/client/credit"):
            return {"results": [{"amount_dec": "0"}]}
        if method == "POST":
            tid = "stubtask_" + uuid.uuid4().hex[:12]
            self.tasks[tid] = {"endpoint": path, "body": body, "t": time.time()}
            self._persist()
            return {"data": {"task_id": tid}}
        tid = path.rsplit("/", 1)[1]
        t = self.tasks[tid]
        if time.time() - t["t"] < self.delay:
            return {"data": {"task_status": "running"}}
        if any(f in t["endpoint"] for f in self.fail):
            return {"data": {"task_status": "error", "error": "stub_error", "error_message": self.fail_message}}
        if "skin-tone-analysis" in t["endpoint"]:
            res = self.analysis_results(self.files[t["body"]["src_file_id"]]) if callable(self.analysis_results) else self.analysis_results
            if not res:
                return {"data": {"task_status": "error", "error": "stub_error", "error_message": "no analysis for the stub"}}
            return {"data": {"task_status": "success", "results": {**res, "stub": True}}}
        if "output" not in t:
            t["output"] = self._render(tid, t); self._persist()
        return {"data": {"task_status": "success", "results": {"url": t["output"].as_uri()}}}

    def _render(self, tid, t):
        b = t["body"]
        src = self.files[b.get("src_file_id") or b["src_file_ids"][0]]
        rid = (b.get("ref_file_ids") or [b.get("ref_file_id")])[0]
        ref = self.files[rid] if rid else None                  # image-to-image (the tuck edit) has no reference
        im = Image.open(src).convert("RGB")
        W, H = im.size
        if "necklace" in t["endpoint"]:      # thin line + thin text: necklaces are thin, and the real QA gate rejects solid blocks
            col = Image.open(ref).convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
            d = ImageDraw.Draw(im)
            d.arc([W * 0.30, H * 0.05, W * 0.70, H * 0.55], 20, 160, fill=col, width=max(2, W // 160))
            d.text((W // 40, H // 40), "SIMULATED necklace (stub)", fill=(21, 21, 20))
            p = self.out / f"stub-{tid}.png"; im.save(p); return p
        if ref:
            th = Image.open(ref).convert("RGB"); th.thumbnail((W // 3, H // 3))
            im.paste(th, (W - th.width - W // 40, H - th.height - W // 40))
        d = ImageDraw.Draw(im)
        band = max(28, H // 14)
        d.rectangle([0, 0, W, band], fill=(21, 21, 20))
        what = b.get("garment_category") or t["endpoint"].rsplit("/", 1)[-1]
        d.text((W // 60, band // 4), f"SIMULATED PROVIDER (stub) - not a try-on - {what}", fill=(252, 249, 242))
        p = self.out / f"stub-{tid}.png"
        im.save(p)
        return p


if __name__ == "__main__":
    import tempfile
    d = Path(tempfile.mkdtemp())
    a, b = d / "a.jpg", d / "b.jpg"
    Image.new("RGB", (600, 900), "white").save(a); Image.new("RGB", (300, 300), "red").save(b)
    s = StubYouCam(d, delay_s=0)
    tid = s.yc("POST", "/s2s/v2.0/task/cloth-v4", {"src_file_id": s.upload(a), "ref_file_id": s.upload(b), "garment_category": "lower_body"})["data"]["task_id"]
    r = s.yc("GET", f"/s2s/v2.0/task/cloth-v4/{tid}")["data"]
    assert r["task_status"] == "success" and Path(r["results"]["url"][7:]).exists()
    f = StubYouCam(d, delay_s=0, fail=["necklace"])
    tid = f.yc("POST", "/s2s/v2.0/task/2d-vto/necklace", {"src_file_id": f.upload(a), "ref_file_ids": [f.upload(b)]})["data"]["task_id"]
    assert f.yc("GET", f"/s2s/v2.0/task/2d-vto/necklace/{tid}")["data"]["task_status"] == "error"
    again = StubYouCam(d, delay_s=0)          # a restarted stub still knows its tasks
    assert again.yc("GET", f"/s2s/v2.0/task/cloth-v4/{list(s.tasks)[0]}")["data"]["task_status"] == "success"
    print("provider_stub self-check ok")
