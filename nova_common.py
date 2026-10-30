"""Shared Bedrock/Nova helper for the spikes: forced tool call, usage-based cost meter with a hard stop."""
import json, os, time

MODEL = "us.amazon.nova-2-lite-v1:0"
IN_PER_M, OUT_PER_M = 0.30, 2.50          # USD per 1M tokens (secondary sources; verify on the bill)


class Budget(Exception):
    pass


class Nova:
    def __init__(self, cap_usd, log_path, dry=False):
        self.cap, self.log_path, self.dry = cap_usd, log_path, dry
        self.spent, self.calls, self.tin, self.tout = 0.0, 0, 0, 0
        self.tool_choice_mode = "tool"
        if not dry:
            import boto3
            self.c = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "calliope"),
                                   region_name="us-east-1").client("bedrock-runtime")

    def call(self, system, content, tool, max_tokens=600):
        if self.spent >= self.cap:
            raise Budget(f"cap ${self.cap} reached")
        cfg = {"tools": [{"toolSpec": {"name": tool["name"], "description": tool["description"],
                                       "inputSchema": {"json": tool["schema"]}}}]}
        if self.dry:
            return {"_dry": True}, 0.0
        for attempt in range(2):
            cfg["toolChoice"] = {"tool": {"name": tool["name"]}} if self.tool_choice_mode == "tool" else {"any": {}}
            t0 = time.time()
            try:
                for wait in (0, 5, 15, 30, 60, 0):                 # throttling backoff; last 0 = give up
                    time.sleep(max(0.0, 1.2 - (time.time() - getattr(self, "_last", 0))))   # pace ~1 call/s
                    try:
                        self._last = time.time()
                        r = self.c.converse(modelId=MODEL, system=[{"text": system}],
                                            messages=[{"role": "user", "content": content}], toolConfig=cfg,
                                            inferenceConfig={"maxTokens": max_tokens, "temperature": 0})
                        break
                    except self.c.exceptions.ThrottlingException:
                        self.throttled = getattr(self, "throttled", 0) + 1
                        if not wait:
                            raise
                        time.sleep(wait)
            except self.c.exceptions.ValidationException as e:
                if self.tool_choice_mode == "tool" and "toolChoice" in str(e):
                    self.tool_choice_mode = "any"          # record and fall back once
                    continue
                raise
            u = r["usage"]
            cost = u["inputTokens"] / 1e6 * IN_PER_M + u["outputTokens"] / 1e6 * OUT_PER_M
            self.spent += cost; self.calls += 1; self.tin += u["inputTokens"]; self.tout += u["outputTokens"]
            out = next((b["toolUse"]["input"] for b in r["output"]["message"]["content"] if "toolUse" in b), None)
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"t": time.time(), "latency_s": round(time.time() - t0, 2), "usage": u,
                                    "cost": round(cost, 6), "stop": r.get("stopReason"), "out": out}) + "\n")
            return out, time.time() - t0
        return None, 0

    def summary(self):
        return {"calls": self.calls, "input_tokens": self.tin, "output_tokens": self.tout,
                "est_cost_usd": round(self.spent, 4), "tool_choice_mode": self.tool_choice_mode,
                "throttled": getattr(self, "throttled", 0)}
