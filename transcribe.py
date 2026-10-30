"""Speech to text for the live app: Amazon Transcribe streaming, called by this server (credentials never reach the browser).

The page captures the microphone as 16 kHz mono 16-bit PCM and posts it in short chunks while the shopper speaks; the
server forwards each chunk to Transcribe over its WebSocket API (SigV4-presigned URL, event-stream frames) and returns
the words so far. When the shopper stops, the final transcript is returned for review: the page puts it in the same text
box as typing, and nothing is sent until the shopper sends it. Requirements checked 2026-10-13 against the AWS docs:
PCM must be signed 16-bit little-endian (not WAV), 16 kHz recommended, chunks of 50-200 ms sent close to real time;
IAM action transcribe:StartStreamTranscriptionWebSocket; presigned URLs expire after at most 300 s.
Why not an SDK: boto3 has no streaming transcription, the amazon-transcribe package says it is no longer developed, and
AWS's new Python SDK needs Python 3.12. botocore decodes the response frames; the rest is ~100 lines below.

Spending: billed per second, no minimum. Metered here at $0.024 per minute (the highest published Tier 1 streaming rate;
the bill may be lower), logged per utterance, and refused once CALLIOPE_TRANSCRIBE_USD is used up. At most 60 s per utterance.
    .venv/bin/python transcribe.py                 # self-check (framing and signing; no network)
    .venv/bin/python transcribe.py file.pcm        # one real transcription of 16 kHz mono s16le audio (paid, seconds of audio)
"""
import base64, datetime, hashlib, hmac, json, os, secrets, socket, ssl, struct, threading, time, zlib
from pathlib import Path
from urllib.parse import quote, urlparse

RATE, LANG = 16000, os.environ.get("CALLIOPE_SPEECH_LANGUAGE", "en-US")
REGION = os.environ.get("CALLIOPE_TRANSCRIBE_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
USD_PER_SECOND = 0.024 / 60
MAX_SECONDS = 60
CFG = {"cap": float(os.environ.get("CALLIOPE_TRANSCRIBE_USD") or 0), "log": None, "problem": None,
       "endpoint": os.environ.get("CALLIOPE_TRANSCRIBE_ENDPOINT")}          # tests only: a local stand-in (ws://127.0.0.1:port)
DENIED = "the AWS identity isn't allowed to use Amazon Transcribe streaming (IAM action transcribe:StartStreamTranscriptionWebSocket)"
STREAMS, _LOCK = {}, threading.Lock()


class SpeechError(Exception):
    """code: not_configured | budget | auth | provider | silence | too_long | gone"""
    def __init__(self, code, message):
        super().__init__(message); self.code = code


def configure(log_path, cap_usd=None):
    CFG.update(log=Path(log_path), cap=float(os.environ.get("CALLIOPE_TRANSCRIBE_USD") or 0) if cap_usd is None else float(cap_usd))
    CFG["problem"] = None if CFG["cap"] > 0 else "CALLIOPE_TRANSCRIBE_USD (the spending cap for speech recognition) is not set"


def spent():
    p = CFG["log"]
    return sum(json.loads(l).get("usd", 0) for l in p.read_text().splitlines() if l.strip()) if p and p.exists() else 0.0


def status():
    if not CFG["log"]:
        return {"available": False, "engine": "amazon-transcribe", "message": "Speech recognition isn't configured on this server."}
    left = CFG["cap"] - spent()
    why = CFG["problem"] or (None if left > 0 else f"the speech recognition budget (${CFG['cap']}) is used up")
    return {"available": not why, "engine": "amazon-transcribe", "language": LANG, "sampleRate": RATE, "maxSeconds": MAX_SECONDS,
            "message": None if not why else "Voice input isn't available on this server right now, so please type instead.", "reason": why}


# ---------------------------------------------------------------- AWS: credentials, presigned URL, event stream
def credentials():
    try:
        import boto3
        c = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "calliope")).get_credentials()
    except Exception as e:
        raise SpeechError("auth", f"AWS credentials for speech: {type(e).__name__}")
    if not c:
        raise SpeechError("auth", "no AWS credentials for speech recognition")
    return c.get_frozen_credentials()


def presign(creds, region=REGION, lang=LANG, rate=RATE, when=None):
    t = when or datetime.datetime.now(datetime.timezone.utc)
    host = f"transcribestreaming.{region}.amazonaws.com:8443"
    amz, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    scope = f"{day}/{region}/transcribe/aws4_request"
    q = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{creds.access_key}/{scope}", "X-Amz-Date": amz, "X-Amz-Expires": "300",
         "X-Amz-SignedHeaders": "host", "language-code": lang, "media-encoding": "pcm", "sample-rate": str(rate)}
    if creds.token:
        q["X-Amz-Security-Token"] = creds.token
    qs = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(q.items()))
    creq = "\n".join(["GET", "/stream-transcription-websocket", qs, f"host:{host}\n", "host", hashlib.sha256(b"").hexdigest()])
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = ("AWS4" + creds.secret_key).encode()
    for part in (day, region, "transcribe", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    base = CFG["endpoint"] or f"wss://{host}"
    return f"{base}/stream-transcription-websocket?{qs}&X-Amz-Signature={hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()}"


def audio_event(pcm):
    """One AudioEvent in AWS event-stream encoding (empty pcm = end of audio)."""
    hb = b"".join(bytes([len(k)]) + k.encode() + b"\x07" + struct.pack(">H", len(v)) + v.encode()
                  for k, v in ((":content-type", "application/octet-stream"), (":event-type", "AudioEvent"), (":message-type", "event")))
    prelude = struct.pack(">II", 16 + len(hb) + len(pcm), len(hb))
    msg = prelude + struct.pack(">I", zlib.crc32(prelude)) + hb + pcm
    return msg + struct.pack(">I", zlib.crc32(msg))


# ---------------------------------------------------------------- minimal WebSocket client (RFC 6455, client side)
def ws_connect(url, timeout=10):
    u = urlparse(url)
    raw = socket.create_connection((u.hostname, u.port or 443), timeout=timeout)
    s = raw if u.scheme == "ws" else ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {u.path}?{u.query} HTTP/1.1\r\nHost: {u.hostname}:{u.port or 443}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: https://{u.hostname}\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        b = s.recv(4096)
        if not b:
            raise SpeechError("provider", "speech service closed the connection during the handshake")
        head += b
    status_line, _, rest = head.partition(b"\r\n")
    if b" 101 " not in status_line + b" ":
        code = status_line.split(b" ")[1].decode() if len(status_line.split(b" ")) > 1 else "?"
        raise SpeechError("auth" if code in ("403", "401") else "provider", f"speech service refused the stream (HTTP {code}); check the IAM permission transcribe:StartStreamTranscriptionWebSocket")
    want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode().lower()
    if want not in head.decode(errors="replace").lower():
        raise SpeechError("provider", "speech service handshake didn't match")
    return s, head.split(b"\r\n\r\n", 1)[1]


def ws_frame(payload, opcode=2):
    n, mask = len(payload), os.urandom(4)
    hdr = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
    return hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class _Reader:
    def __init__(self, sock, pending=b""):
        self.s, self.buf = sock, pending

    def exact(self, n):
        while len(self.buf) < n:
            b = self.s.recv(65536)
            if not b:
                raise EOFError
            self.buf += b
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def frame(self):
        b0, b1 = self.exact(2)
        n = b1 & 0x7F
        n = struct.unpack(">H", self.exact(2))[0] if n == 126 else struct.unpack(">Q", self.exact(8))[0] if n == 127 else n
        mask = self.exact(4) if b1 & 0x80 else None
        data = self.exact(n)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return b0 & 0x0F, data


# ---------------------------------------------------------------- one utterance
class Stream:
    def __init__(self, owner):
        if CFG["problem"]:
            raise SpeechError("not_configured", CFG["problem"])
        if spent() >= CFG["cap"]:
            raise SpeechError("budget", f"the speech recognition budget (${CFG['cap']}) is used up")
        self.id, self.owner, self.t0 = "spk_" + secrets.token_hex(8), owner, time.time()
        self.finals, self.partial, self.error, self.bytes, self.closed = [], "", None, 0, False
        self.send_lock, self.done = threading.Lock(), threading.Event()
        self.sock, pending = ws_connect(presign(credentials()))
        self.sock.settimeout(None)
        self.reader = _Reader(self.sock, pending)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        from botocore.eventstream import EventStreamBuffer
        buf = EventStreamBuffer()
        try:
            while True:
                op, data = self.reader.frame()
                if op == 8:
                    break
                if op == 9:
                    with self.send_lock: self.sock.sendall(ws_frame(data, 10))
                    continue
                if op not in (1, 2):
                    continue
                buf.add_data(data)
                for m in buf:
                    hd = m.headers
                    if hd.get(":message-type") == "exception":
                        kind = hd.get(":exception-type") or ""
                        if kind in ("AccessDeniedException", "UnrecognizedClientException"):
                            CFG["problem"] = DENIED                  # voice input is turned off (and says why) until the permission exists
                            self.error = SpeechError("auth", DENIED)
                        else:
                            self.error = SpeechError("provider", f"{kind}: {m.payload.decode(errors='replace')[:200]}")
                        continue
                    for r in json.loads(m.payload or b"{}").get("Transcript", {}).get("Results", []):
                        text = ((r.get("Alternatives") or [{}])[0].get("Transcript") or "").strip()
                        if r.get("IsPartial"):
                            self.partial = text
                        elif text:
                            self.finals.append(text); self.partial = ""
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self.done.set()

    def text(self):
        return " ".join(self.finals + ([self.partial] if self.partial else [])).strip()

    def seconds(self):
        return self.bytes / (2 * RATE)

    def feed(self, pcm):
        if self.closed:
            raise SpeechError("gone", "this recording has ended")
        if len(pcm) % 2:
            raise ValueError("PCM chunks must be whole 16-bit samples")
        if self.seconds() + len(pcm) / (2 * RATE) > MAX_SECONDS:
            raise SpeechError("too_long", f"recordings stop at {MAX_SECONDS} seconds")
        step = RATE * 2 // 10                                         # 100 ms frames (the recommended 50-200 ms)
        with self.send_lock:
            for i in range(0, len(pcm), step):
                self.sock.sendall(ws_frame(audio_event(pcm[i:i + step])))
        self.bytes += len(pcm)
        if self.error:
            raise self.error
        return self.text()

    def finish(self, cancel=False, wait=8):
        if self.closed:
            return self.text()
        self.closed = True
        try:
            if not cancel:
                with self.send_lock: self.sock.sendall(ws_frame(audio_event(b"")))
                self.done.wait(wait)
            with self.send_lock: self.sock.sendall(ws_frame(struct.pack(">H", 1000), 8))
        except OSError:
            pass
        finally:
            try: self.sock.close()
            except OSError: pass
            self._bill(cancel)
        return self.text()

    def _bill(self, cancel):
        secs = -(-self.bytes // (2 * RATE))                           # billed per second, no minimum
        if CFG["log"]:
            CFG["log"].parent.mkdir(parents=True, exist_ok=True)
            with open(CFG["log"], "a") as f:
                f.write(json.dumps({"t": time.time(), "id": self.id, "seconds": secs, "usd": round(secs * USD_PER_SECOND, 6), "cancelled": cancel,
                                    "chars": len(self.text()), "error": str(self.error) if self.error else None}) + "\n")


def start(owner):
    with _LOCK:
        for k, st in list(STREAMS.items()):                            # an abandoned recording never lingers
            if time.time() - st.t0 > MAX_SECONDS + 30:
                STREAMS.pop(k).finish(cancel=True)
    st = Stream(owner)
    with _LOCK:
        STREAMS[st.id] = st
    return st


def get(sid, owner):
    st = STREAMS.get(sid)
    if not st or st.owner != owner:
        raise SpeechError("gone", "no such recording")
    return st


def end(sid, owner, cancel=False):
    st = get(sid, owner)
    with _LOCK:
        STREAMS.pop(sid, None)
    text = st.finish(cancel=cancel)
    if st.error and not text:
        raise st.error
    if st.error is None and not st.done.is_set() and not cancel and not text:
        raise SpeechError("provider", "the speech service didn't answer in time; try again, or type it")
    if not cancel and not text:
        raise SpeechError("silence", "I didn't catch any words. Try again a little closer to the microphone, or type it.")
    return {"text": text, "seconds": round(st.seconds(), 1), "final": True}


def _selfcheck():
    ev = audio_event(b"\x01\x02")
    from botocore.eventstream import EventStreamBuffer
    b = EventStreamBuffer(); b.add_data(ev); m = next(iter(b))
    assert m.headers[":event-type"] == "AudioEvent" and m.payload == b"\x01\x02", "event-stream frame round-trips through botocore's decoder"
    assert audio_event(b"")[:4] == struct.pack(">I", 16 + len(audio_event(b"")) - 16), "the end-of-audio frame is a valid empty event"

    class Cr:
        access_key, secret_key, token = "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", None
    u = presign(Cr, "us-west-2", "en-US", 16000, datetime.datetime(2022, 1, 27, 0, 0, 0, tzinfo=datetime.timezone.utc))
    assert u.startswith("wss://transcribestreaming.us-west-2.amazonaws.com:8443/stream-transcription-websocket?X-Amz-Algorithm=AWS4-HMAC-SHA256")
    assert "media-encoding=pcm" in u and "X-Amz-Credential=AKIDEXAMPLE%2F20220127%2Fus-west-2%2Ftranscribe%2Faws4_request" in u and len(u.rsplit("=", 1)[1]) == 64
    fr = ws_frame(b"x" * 300)
    assert fr[0] == 0x82 and fr[1] == 0x80 | 126 and struct.unpack(">H", fr[2:4])[0] == 300, "client frames are masked, binary, extended length"
    print("transcribe self-check ok")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        _selfcheck(); sys.exit(0)
    configure(Path(__file__).resolve().parent / "state" / "engine" / "state" / "transcribe_calls.jsonl")
    pcm = Path(sys.argv[1]).read_bytes()
    st = start("cli")
    for i in range(0, len(pcm), RATE * 2 // 4):                       # 250 ms chunks, paced like a browser
        print("partial:", st.feed(pcm[i:i + RATE * 2 // 4])); time.sleep(0.25)
    print(json.dumps(end(st.id, "cli")))
