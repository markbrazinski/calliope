import { useEffect, useRef, useState } from 'react';

// Voice input: the microphone is captured here as 16 kHz mono PCM and streamed to the server, which sends it to Amazon
// Transcribe (AWS credentials stay on the server). The words appear as they're recognised; when the shopper stops, the
// transcript stays in the text box to check or edit, and is sent with the same Go as typing. Nothing is sent by itself.
const ERRORS = {
  NotAllowedError: 'Calliope can’t use the microphone. Allow it in your browser settings, or type instead.',
  SecurityError: 'Calliope can’t use the microphone. Allow it in your browser settings, or type instead.',
  NotFoundError: 'No microphone was found. Type it instead.',
  NotReadableError: 'The microphone is busy in another app. Close it there, or type instead.',
  insecure: 'The microphone only works on a secure (https) page. Type it instead.',
  unsupported: 'This browser can’t record from the microphone. Type it instead.',
};

const RATE = 16000, SEND_MS = 250;

async function post(path, body, method = 'POST') {
  const r = await fetch(path, { method, credentials: 'same-origin', headers: { 'Content-Type': body ? 'application/octet-stream' : 'application/json' }, body: body || '{}' });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.message || 'Voice input stopped. Type it instead.'), { code: j.code, status: r.status });
  return j;
}

// Float32 samples at the device rate -> 16-bit PCM at 16 kHz (box-filter downsampling; speech needs no more).
function toPcm16(chunks, inRate) {
  const n = chunks.reduce((t, c) => t + c.length, 0), all = new Float32Array(n); let o = 0;
  chunks.forEach((c) => { all.set(c, o); o += c.length; });
  const ratio = inRate / RATE, out = new Int16Array(Math.floor(n / ratio));
  for (let i = 0; i < out.length; i++) {
    const a = Math.floor(i * ratio), b = Math.min(n, Math.floor((i + 1) * ratio)); let s = 0;
    for (let j = a; j < b; j++) s += all[j];
    const v = Math.max(-1, Math.min(1, s / Math.max(1, b - a))); out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out.buffer;
}

export function useDictation({ onText, onEnd, onError, speech }) {
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);                  // waiting for the final words after Stop
  const [level, setLevel] = useState(0);
  const [secs, setSecs] = useState(0);
  const cb = useRef(); cb.current = { onText, onEnd, onError };
  const live = useRef(null);
  useEffect(() => () => { if (live.current) live.current.abort(); }, []);

  const start = async () => {        // Amazon Transcribe through the server
    if (speech && !speech.available) return cb.current.onError(speech.message || 'Voice input isn’t available right now. Type it instead.');
    if (!window.isSecureContext) return cb.current.onError(ERRORS.insecure);
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) || !(window.AudioContext || window.webkitAudioContext)) return cb.current.onError(ERRORS.unsupported);
    const st = { chunks: [], sending: false, id: null, done: false, text: '', t0: Date.now(), heard: 0 };
    live.current = { stop: () => finish(false), abort: () => finish(true), cancel: () => finish(true) };
    setOn(true); setSecs(0); setLevel(0);
    let stream, ctx, node, src, timer;
    const cleanup = () => { clearInterval(timer); try { node && node.disconnect(); src && src.disconnect(); } catch (e) { /* closed */ }
      if (stream) stream.getTracks().forEach((x) => x.stop()); if (ctx && ctx.state !== 'closed') ctx.close(); live.current = null; setOn(false); setLevel(0); };
    const flush = async () => {                 // one chunk request at a time, in order
      if (st.sending || !st.id || !st.chunks.length) return;
      st.sending = true; const buf = toPcm16(st.chunks.splice(0), ctx.sampleRate);
      try { const r = await post(`/api/speech/${st.id}/audio`, buf); if (!st.failed && r.text) { st.text = r.text; cb.current.onText(r.text); } }
      catch (e) { if (!st.failed) { st.failed = st.done = true; cleanup(); cb.current.onError(e.message); post(`/api/speech/${st.id}`, null, 'DELETE').catch(() => {}); } }
      finally { st.sending = false; }
    };
    async function finish(cancel) {
      if (st.done) return; st.done = true; clearInterval(timer);
      const id = st.id;
      if (cancel || !id) { cleanup(); if (id) post(`/api/speech/${id}`, null, 'DELETE').catch(() => {}); return cancel && cb.current.onEnd(null); }
      setBusy(true);
      try {
        while (st.sending) await new Promise((r) => setTimeout(r, 50));
        await flush();                           // the last words spoken before Stop
        if (st.failed) return;
        cleanup();
        const r = await post(`/api/speech/${id}/end`);
        cb.current.onText(r.text); cb.current.onEnd(r.text);
      } catch (e) { cleanup(); cb.current.onError(st.heard < 3 && e.code === 'speech_silence' ? 'I didn’t hear anything. Check the microphone, or type it.' : e.message); }
      finally { setBusy(false); }
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      if (st.done) { stream.getTracks().forEach((x) => x.stop()); return undefined; }
      st.id = (await post('/api/speech')).speechId;
      if (st.done) { post(`/api/speech/${st.id}`, null, 'DELETE').catch(() => {}); stream.getTracks().forEach((x) => x.stop()); return undefined; }
      const AC = window.AudioContext || window.webkitAudioContext; ctx = new AC();
      if (ctx.state === 'suspended') await ctx.resume();
      src = ctx.createMediaStreamSource(stream); node = ctx.createScriptProcessor(4096, 1, 1);   // universal (incl. iOS Safari); a worklet isn't needed for 16 kHz speech
      node.onaudioprocess = (e) => {
        if (st.done) return;
        const x = e.inputBuffer.getChannelData(0); st.chunks.push(new Float32Array(x));
        let s = 0; for (let i = 0; i < x.length; i += 8) s += x[i] * x[i];
        const rms = Math.sqrt(s / (x.length / 8)); if (rms > 0.02) st.heard += 1;
        setLevel(Math.min(1, rms * 8));
      };
      src.connect(node); node.connect(ctx.destination);
      timer = setInterval(() => { const t = (Date.now() - st.t0) / 1000; setSecs(Math.floor(t)); if (t >= (speech && speech.maxSeconds || 60) - 0.5) finish(false); else flush(); }, SEND_MS);
    } catch (e) {
      cleanup(); if (st.id) post(`/api/speech/${st.id}`, null, 'DELETE').catch(() => {});
      cb.current.onError(e.status ? e.message : ERRORS[e.name] || ERRORS.unsupported);     // server answers keep their words; browser errors get plain advice
    }
    return undefined;
  };

  return { on, busy, level, secs,
    toggle: () => (busy ? undefined : live.current ? live.current.stop() : start()),
    cancel: () => live.current && live.current.cancel && live.current.cancel() };
}
