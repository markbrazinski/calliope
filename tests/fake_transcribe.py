"""A stand-in for Amazon Transcribe streaming (tests only; nothing is recognised). It speaks the real WebSocket protocol:
upgrade, event-stream AudioEvents in, TranscriptEvents (partial, then final) or an exception out.

    .venv/bin/python tests/fake_transcribe.py <port> <control.json>

control.json (re-read for every stream): {"text": "words to return", "mode": "speech" | "error"}. Words come back only if
the audio received is at least 0.5 s long and not silent, so a test proves real microphone audio reached the service.
Each stream's stats (seconds, rms) are appended to <control.json>.log.
"""
import base64, hashlib, json, math, socket, struct, sys, threading, zlib
from pathlib import Path

from botocore.eventstream import EventStreamBuffer


def es(headers, payload):
    hb = b"".join(bytes([len(k)]) + k.encode() + b"\x07" + struct.pack(">H", len(v)) + v.encode() for k, v in headers.items())
    pre = struct.pack(">II", 16 + len(hb) + len(payload), len(hb)); m = pre + struct.pack(">I", zlib.crc32(pre)) + hb + payload
    return m + struct.pack(">I", zlib.crc32(m))


def frame(data, op=2):
    n = len(data)
    return bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)) + data


def transcript(text, partial):
    return frame(es({":content-type": "application/json", ":event-type": "TranscriptEvent", ":message-type": "event"},
                    json.dumps({"Transcript": {"Results": [{"IsPartial": partial, "Alternatives": [{"Transcript": text}]}]}}).encode()))


def serve(conn, ctl):
    f = conn.makefile("rb")
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        head += f.read(1)
    key = next(l.split(b":", 1)[1].strip() for l in head.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key"))
    acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")
    c = json.loads(Path(ctl).read_text()); words = c.get("text", "").split()
    buf, pcm, sent = EventStreamBuffer(), bytearray(), 0
    while True:
        b0, b1 = f.read(2); n = b1 & 0x7F
        n = struct.unpack(">H", f.read(2))[0] if n == 126 else struct.unpack(">Q", f.read(8))[0] if n == 127 else n
        mask = f.read(4); data = bytes(x ^ mask[i % 4] for i, x in enumerate(f.read(n)))
        if b0 & 0x0F == 8:
            break
        buf.add_data(data)
        for m in buf:
            if m.payload:
                pcm += m.payload
                secs = len(pcm) / 32000
                if c.get("mode") == "error" and secs > 0.3:
                    conn.sendall(frame(es({":content-type": "application/json", ":exception-type": "BadRequestException", ":message-type": "exception"}, b'{"Message":"test failure"}')))
                    conn.sendall(frame(struct.pack(">H", 1011), 8)); conn.close(); return
                k = min(len(words), int(secs * 2.5))
                if words and k > sent and rms(pcm) > 300:
                    sent = k; conn.sendall(transcript(" ".join(words[:k]), True))
            else:                                       # end of audio: the final result (only for real, audible audio)
                secs, level = len(pcm) / 32000, rms(pcm)
                with open(ctl + ".log", "a") as lg:
                    lg.write(json.dumps({"seconds": round(secs, 2), "rms": round(level), "returned": bool(words and secs >= 0.5 and level > 300)}) + "\n")
                if words and secs >= 0.5 and level > 300:
                    conn.sendall(transcript(" ".join(words), False))
                conn.sendall(frame(struct.pack(">H", 1000), 8)); conn.close(); return


def safe(conn, ctl):
    try:
        serve(conn, ctl)
    except (OSError, ValueError, StopIteration):
        conn.close()                                     # the client went away mid-stream (e.g. a cancelled recording)


def rms(pcm):
    n = len(pcm) // 2
    if not n: return 0
    s = struct.unpack(f"<{n}h", bytes(pcm[:n * 2]))
    return math.sqrt(sum(x * x for x in s[::4]) / max(1, len(s[::4])))


if __name__ == "__main__":
    port, ctl = int(sys.argv[1]), sys.argv[2]
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", port)); srv.listen(8)
    print(f"fake transcribe on ws://127.0.0.1:{port}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=safe, args=(conn, ctl), daemon=True).start()
