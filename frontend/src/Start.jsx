import { useEffect, useRef, useState } from 'react';
import { sx } from './sx.js';
import { Mark } from './parts.jsx';
import { useDictation } from './voice.js';

const GREETING = "Hi, I'm Calliope. Let's find you something you'll love wearing. Start with a full-length photo so you can see the clothes on you. Then tell me what we're dressing you up for, your budget, and anything you already love or want to avoid. Say it or type it, however it comes out.";
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// The brief: Nova reads it on the server (POST /api/brief/read, checked fields). These simple rules are the fallback when
// Nova isn't available. Both give the same fields, and chipsFrom() turns them into the chips the shopper checks.
export function rulesFields(t) {
  const L = t.toLowerCase(), f = { notes: [] };
  const occ = L.match(/\b(anniversary|wedding|birthday|date night|interview|graduation|work|office|holiday|gala|cocktail|conference|meeting|presentation|funeral|christening|theat(?:er|re)|concert|night out|dinner|lunch|brunch|party|picnic|vacation|trip|date)(?: (?:holiday|christmas|birthday|work|office|team|summer|winter|engagement|retirement))?( dinner| party| event| guest| lunch| night| trip)?( with (?:my )?(?:friends|family|colleagues|coworkers|partner|husband|wife|boyfriend|girlfriend|team|parents))?\b/);
  if (occ) f.occasion = { value: occ[0], phrase: occ[0] };
  const dress = L.match(/somewhere nice|fancy|upscale|dressy|black tie|casual/);
  if (dress) f.dress = { value: /casual/.test(dress[0]) ? 'Relaxed' : /black tie/.test(dress[0]) ? 'Formal' : 'Smart evening', phrase: dress[0] };
  const outfit = L.match(/\b(a|an|in a|wear a|wearing a)\s+([a-z-]+\s+){0,2}dress\b(?!\s+(shoes?|up))|\bdress instead\b|\b(trousers|pants|jeans|separates)\b/);
  if (outfit) f.structure = { value: /dress/.test(outfit[0]) ? 'dress' : 'separates', phrase: outfit[0] };
  const bud = t.match(/(about|around|roughly|under|up to|no more than|max)?\s*\$\s?(\d{2,5})/i)
    || t.match(/(about|around|roughly|under|up to|no more than|max)?\s*()(\d{2,5})\s*(?:dollars?|bucks|budget)\b/i);
  if (bud) f.budget = { n: Number(bud[2] || bud[3]), approx: /about|around|roughly/i.test(bud[1] || ''), phrase: bud[0].trim() };
  const heel = L.match(/no heels?|not? (wear(ing)? )?heels?|without heels?|flats only/);
  if (heel) f.heels = { phrase: heel[0] };
  const walk = L.match(/walk\w*( a lot)?/);
  if (walk) f.comfort = { phrase: walk[0] };
  const own = t.match(/(i have|i own|my) (an? )?([a-z ]{0,24}?)(blouse|top|shirt|dress|jacket|skirt)/i);
  if (own) f.owned = { value: (own[3] + own[4]).trim().toLowerCase(), phrase: own[0] };
  return f;
}

export function chipsFrom(f, garment) {
  const out = [], b = f.budget;
  out.push(f.occasion ? { k: 'occasion', label: 'Occasion', value: cap(f.occasion.value), phrase: f.occasion.phrase, sure: true, guess: !!f.occasion.corrected, text: true }
    : { k: 'occasion', label: 'Occasion', value: 'Not mentioned', sure: false, missing: true, text: true, ask: "What's the occasion?" });
  if (f.dress) out.push({ k: 'dress', label: 'How dressy', value: f.dress.value, phrase: f.dress.phrase, sure: true,
    ask: f.dress.phrase ? 'From "' + f.dress.phrase + '". Sound right?' : 'Sound right?', options: ['Relaxed', 'Smart evening', 'Formal'] });
  if (f.structure) out.push({ k: 'structure', label: 'Outfit', value: f.structure.value === 'dress' ? 'A dress' : 'Separates', phrase: f.structure.phrase, sure: true, options: ['A dress', 'Separates', 'Either'] });
  const budOpts = ['Up to $150', 'Up to $200', 'Up to $300', 'No limit'];
  if (b && b.n) out.push({ k: 'budget', label: 'Budget for new pieces', value: b.approx ? '$' + b.n + ', give or take $10' : 'Up to $' + b.n, n: b.n, phrase: b.phrase, sure: true,
    options: ['$' + b.n + ', give or take $10', 'Up to $' + b.n, 'No limit'] });
  else if (b && b.none) out.push({ k: 'budget', label: 'Budget for new pieces', value: 'No limit', phrase: b.phrase, sure: true, options: budOpts });
  else out.push({ k: 'budget', label: 'Budget for new pieces', value: 'No limit', sure: true, guess: true, ask: 'You didn’t mention a budget, so there’s no limit. Set one?', options: budOpts });
  if (f.heels) out.push({ k: 'avoid', label: 'Avoid', value: 'Heels', phrase: f.heels.phrase, sure: true, options: ['Avoid heels', 'Heels are fine'] });
  if (f.comfort) out.push({ k: 'note', label: 'Noted', value: 'Comfortable for walking', phrase: f.comfort.phrase, sure: false, guess: true, ask: 'Should I favor comfort for shoes?', options: ['Yes, comfort first', 'Not important'] });
  (f.notes || []).forEach((n) => out.push({ k: 'note', label: 'Noted', value: cap(n), sure: true }));
  if (f.owned) out.push({ k: 'owned', label: 'Something you own', value: 'Your ' + f.owned.value, phrase: f.owned.phrase, sure: !!garment, owned: true, cta: 'Add photo', ask: 'Add a photo so I can style around it and show it in your previews.' });
  return out;
}

export const extract = (t, garment) => chipsFrom(rulesFields(t), garment);

export function Start(p) {
  const wide = !!p.wide, fast = !!p.reduced || !!p.unavailable;      // unavailable: show the reason at once, not after the greeting
  const [g, setG] = useState(fast ? GREETING.length : 0);
  const [phase, setPhase] = useState(fast ? 'ready' : 'greet');
  const [text, setText] = useState('');
  const [quote, setQuote] = useState('');
  const [chips, setChips] = useState([]);
  const [shown, setShown] = useState(0);
  const [open, setOpen] = useState(null);
  const [fields, setFields] = useState(false);
  const [drafts, setDrafts] = useState({});
  const [ownColor, setOwnColor] = useState(null);
  const [ownCat, setOwnCat] = useState(null);
  const [editTop, setEditTop] = useState(false);              // correcting a top that was read automatically
  const [pulse, setPulse] = useState(1);
  const [heardNote, setHeardNote] = useState('');
  const boxRef = useRef(null), startRef = useRef(null), timers = useRef([]), taRef = useRef(null), before = useRef(''), readSeq = useRef(0);
  const later = (fn, ms) => { const t = setTimeout(fn, ms); timers.current.push(t); return t; };
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  useEffect(() => {                                        // the greeting types itself; a tap skips it
    if (phase !== 'greet') return undefined;
    const t0 = Date.now(), t = setInterval(() => { const n = Math.min(GREETING.length, Math.round((Date.now() - t0) * 0.075)); setG(n); if (n >= GREETING.length) setPhase('ready'); }, 22);
    return () => clearInterval(t);
  }, [phase]);
  const skip = () => { if (phase === 'greet') { setG(GREETING.length); setPhase('ready'); } };

  const reveal = (ref, pad = 12) => {                       // keep the box or the start button in view as content grows
    const el = ref.current; if (!el) return;
    let a = el.parentElement;
    while (a && !(/(auto|scroll)/.test(getComputedStyle(a).overflowY) && a.scrollHeight > a.clientHeight)) a = a.parentElement;
    if (!a) return;
    const over = el.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom + pad;
    if (over > 0) a.scrollTo({ top: a.scrollTop + over, behavior: fast ? 'auto' : 'smooth' });
  };

  const apply = (cs, q) => {
    const by = {}; cs.forEach((c) => { by[c.k] = c; });
    p.onApply({ occasion: by.occasion && !by.occasion.missing ? by.occasion.value : '',
      budget: by.budget && by.budget.n ? by.budget.n : by.budget && by.budget.value === 'No limit' ? 0 : null,
      heels: by.avoid ? by.avoid.value === 'Heels' : null,
      approx: by.budget && by.budget.n ? /give or take/.test(by.budget.value) : null, dress: by.dress && by.dress.sure ? by.dress.value : null,
      notes: cs.filter((c) => c.k === 'note' && c.value !== 'Not important').map((c) => c.value), text: q,
      structure: by.structure ? { 'A dress': 'dress', Separates: 'separates' }[by.structure.value] || 'either' : 'either' });
  };

  const read = (t0 = text) => {
    const t = t0.trim(); if (!t) return;
    skip(); timers.current.forEach(clearTimeout); timers.current = [];
    setPhase('reading'); setChips([]); setShown(0); setOpen(null); setQuote(t);
    later(() => reveal(boxRef), 0);
    const t1 = Date.now(), my = ++readSeq.current;                // Nova reads it; the page's rules if Nova can't
    (p.readBrief ? p.readBrief(t).catch(() => null) : Promise.resolve(null)).then((r) => {
      if (my !== readSeq.current) return;                        // a newer Go replaced this one
      const cs = r && r.fields ? chipsFrom(r.fields, p.garment) : extract(t, p.garment), gap = fast ? 1 : 520;
      setChips(cs);
      later(() => {
        setPhase('done');
        cs.forEach((_, i) => later(() => { setShown(i + 1); later(() => reveal(startRef, 16), 60); }, (i + 1) * gap));
        later(() => { setShown(cs.length); apply(cs, t); later(() => reveal(startRef, 16), 60); }, cs.length * gap + 10);
      }, Math.max(0, (fast ? 100 : 650) - (Date.now() - t1)));
    });
  };

  const setChip = (i, patch) => { const cs = chips.map((c, j) => (j === i ? { ...c, ...patch } : c)); setChips(cs); setOpen(null); apply(cs, quote); };

  const mic = useDictation({
    speech: p.speech,
    onText: (t) => { setText(t); reveal(boxRef); },
    // Live: the words stay in the box to check or edit; Go sends them like typing (never sent by themselves). Cancel puts back what was there.
    onEnd: (t) => { if (t === null) { setText(before.current); setHeardNote(''); return; }
      if (t) { setHeardNote('Check the words, change anything, then tap Go.'); later(() => taRef.current && taRef.current.focus(), 0); } },
    onError: (m) => { setText((x) => x || before.current); setHeardNote(''); p.onVoiceError(m); },
  });
  const micToggle = () => { if (!mic.on && !mic.busy) { skip(); before.current = text; setText(''); setChips([]); setShown(0); setHeardNote(''); } mic.toggle(); };
  useEffect(() => {
    if (!mic.on && phase !== 'greet') return undefined;
    const t = setInterval(() => setPulse((x) => (x ? 0.25 : 1)), 420); return () => clearInterval(t);
  }, [mic.on, phase]);

  const greetDone = phase !== 'greet';
  const revealed = chips.length > 0 && shown >= chips.length;
  const opt = (o, on, pick) => ({ label: (on ? '✓ ' : '') + o, bg: on ? '#151514' : '#fcf9f2', fg: on ? '#fcf9f2' : '#151514', border: on ? '1px solid #151514' : '1px solid #837d73', pick });
  const view = chips.map((c, i) => {
    const vis = i < shown, isOpen = open === i, needs = !c.sure;
    const opts = (c.options || []).map((o) => opt(o, c.value === o, () => {
      const patch = { sure: true, guess: false, missing: false, value: o };
      if (c.k === 'budget') { const m = o.match(/\$(\d+)/); patch.n = m ? Number(m[1]) : 0; }
      if (c.k === 'avoid') patch.value = o === 'Avoid heels' ? 'Heels' : 'Nothing';
      setChip(i, patch);
    }));
    const ownedOpts = c.owned && !c.sure ? [opt('Add photo', false, () => { if (!p.garment) p.onGarment(c.value); setChip(i, { sure: true, value: c.value }); }),
      opt('Not now', false, () => setChip(i, { sure: true, value: 'Not added · I’ll suggest a top, and it counts toward your budget' }))] : [];
    return { ...c, i, vis, isOpen, needs, opts: c.owned ? ownedOpts : opts,
      guessTag: c.missing ? ' · needed' : c.guess ? ' · my guess, please check' : c.owned && needs ? ' · please confirm' : '',
      cta: c.missing ? 'Add' : c.owned ? 'Add photo' : 'Check', editable: !needs && (!!c.options || c.text), draft: drafts[i] ?? (c.missing ? '' : c.value) };
  });
  if (revealed && !p.photo) view.unshift({ k: 'photo', i: -1, vis: true, label: 'Your photo', value: 'Needed so you can see the clothes on you', needs: true, guessTag: ' · needed', cta: 'Add photo', opts: [], photo: true });
  const mentionedOwn = chips.some((c) => c.owned);
  const segs = [];
  if (quote && chips.length) {                              // the words each detail came from, underlined as it appears
    const marks = [];
    chips.forEach((c, i) => { if (!c.phrase) return; const at = quote.toLowerCase().indexOf(c.phrase.toLowerCase()); if (at >= 0) marks.push({ at, end: at + c.phrase.length, i, sure: c.sure }); });
    marks.sort((a, b) => a.at - b.at);
    let pos = 0;
    marks.forEach((m) => { if (m.at < pos) return; if (m.at > pos) segs.push({ t: quote.slice(pos, m.at) });
      const on = m.i < shown; segs.push({ t: quote.slice(m.at, m.end), on, sure: m.sure }); pos = m.end; });
    if (pos < quote.length) segs.push({ t: quote.slice(pos) });
  }
  const toCheck = revealed ? view.filter((c) => c.needs && !c.missing && c.k !== 'photo').length : 0;
  const missingOcc = !(p.occasion || '').trim(), op = p.ownedPending || (editTop && p.ownedNow);
  const canStart = !!p.photo && !missingOcc && (revealed || fields) && !p.ownedPending && !p.unavailable && !p.busy;     // no budget = no limit
  const oc = ownCat || (op && op.category) || '', ocol = ownColor ?? ((op && op.colorName) || '');
  const ph = !!p.photo, issue = !!p.photoIssue;
  const startHint = p.unavailable ? 'Calliope isn’t available right now.' : p.busy === 'top' ? 'Adding your top…' : !ph ? 'Add your photo to start.' : p.ownedPending ? 'Confirm your top to start.'
    : missingOcc && (revealed || fields) ? 'Tell me the occasion to start.'
      : !revealed && !fields ? 'Tell me about the occasion first.' : toCheck ? toCheck + ' guess' + (toCheck > 1 ? 'es' : '') + ' to check. Skip them and I’ll use my best guess.' : 'All set. You can change any of this later.';
  const photoLabelBg = ph || issue ? 'rgba(252,249,242,.9)' : 'transparent';

  return (
    <div style={sx("display:flex;flex-direction:column;gap:16px;font-family:'Schibsted Grotesk',sans-serif;color:#151514;font-size:14px")}>
      <div onClick={skip} style={sx('display:flex;gap:12px;align-items:flex-start')}>
        <Mark />
        <div style={sx(`flex:1;font-size:${wide ? '19px' : '16.5px'};font-weight:500;letter-spacing:-0.01em;line-height:1.5;min-height:48px;text-wrap:pretty`)}>
          {/* the whole greeting is laid out from the start (the untyped rest is invisible), so words never jump lines as it types */}
          {GREETING.slice(0, g)}<span aria-hidden="true" style={sx(`display:inline-block;width:0;opacity:${greetDone ? 0 : pulse}`)}>▍</span><span aria-hidden="true" style={sx('visibility:hidden')}>{GREETING.slice(g)}</span></div>
      </div>

      <div style={sx(`display:grid;grid-template-columns:${wide ? 'minmax(0,0.8fr) minmax(0,1.2fr)' : '1fr'};gap:18px;align-items:start;opacity:${greetDone ? 1 : 0};transform:translateY(${greetDone ? '0' : '10px'});transition:opacity .45s ease,transform .45s ease;pointer-events:${greetDone ? 'auto' : 'none'}`)}>
        <div style={sx('display:flex;flex-direction:column;gap:10px;min-width:0')}>
          {p.unavailable && <div role="alert" style={sx('background:#fcf9f2;border:1.5px solid #b42318;border-radius:6px;padding:10px 12px;font-size:13.5px;line-height:1.45')}><strong style={sx('color:#b42318')}>Calliope isn’t available right now.</strong> {p.unavailable}</div>}
          <button onClick={p.onPhoto} disabled={!ph && (!!p.busy || !!p.unavailable)} aria-label={ph ? 'Photo added. Remove photo' : 'Add your full-length photo'}
            style={sx(`min-height:${wide ? '300px' : '128px'};border:${ph ? '1.5px solid #151514' : issue ? '1.5px solid #8a5800' : '1.5px dashed #837d73'};border-radius:6px;background:${ph && p.photoUrl ? `#efe8db url('${p.photoUrl}') center / contain no-repeat` : ph || issue ? 'repeating-linear-gradient(135deg,#f0efea 0 7px,#e8e6e0 7px 14px)' : '#fcf9f2'};cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:14px;text-align:center`)}>
            <span style={sx(`font-weight:600;font-size:14px;background:${photoLabelBg};padding:2px 6px;border-radius:4px`)}>{p.busy === 'photo' ? 'Checking your photo…' : ph ? '✓ Photo added' : 'Add your full-length photo'}</span>
            <span style={sx(`font-size:12.5px;color:#55534d;line-height:1.4;max-width:240px;background:${photoLabelBg};padding:0 6px;border-radius:4px`)}>{ph ? (issue ? 'See the note below' : 'Tap to remove') : 'Head to shoes, even light, head level, hair behind your shoulders. No necklace or sunglasses.'}</span>
          </button>
          {issue && p.issue && (
            <div role="alert" style={sx('background:#fcf9f2;border:1.5px solid #8a5800;border-radius:6px;padding:12px;display:flex;flex-direction:column;gap:8px')}>
              <div style={sx('font-weight:600;color:#8a5800')}>{p.issue.title}</div>
              <div style={sx('font-size:13px;line-height:1.5')}>{p.issue.body}</div>
              <div style={sx('display:flex;gap:8px;flex-wrap:wrap')}>
                <button onClick={p.onPhotoFix} style={sx('background:#151514;color:#fcf9f2;border:1px solid #151514;border-radius:6px;padding:10px 14px;min-height:44px;cursor:pointer;font-weight:600;font-size:13.5px;white-space:nowrap')}>Choose another photo</button>
                <button onClick={p.onIssueKeep} style={sx('background:#fcf9f2;border:1px solid #151514;border-radius:6px;padding:10px 14px;min-height:44px;cursor:pointer;font-size:13.5px;white-space:nowrap')}>Keep this photo</button>
              </div>
            </div>
          )}
        </div>

        <div style={sx('display:flex;flex-direction:column;gap:12px;min-width:0')}>
          <div ref={boxRef} style={sx(`background:#fffdf8;border:1px solid ${mic.on ? '#151514' : '#837d73'};border-radius:6px;padding:10px 10px 8px;display:flex;flex-direction:column;gap:8px`)}>
            <textarea ref={taRef} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); setHeardNote(''); read(); } }}
              rows={Math.max(3, Math.ceil((text || '').length / (wide ? 62 : 36)))} aria-label="Tell Calliope about the occasion" placeholder="Say it or type it: the occasion, your budget, anything to avoid…"
              style={sx('border:none;outline:none;resize:none;font:inherit;font-size:15px;line-height:1.5;background:transparent;color:#151514')} />
            <div style={sx('display:flex;align-items:center;gap:8px')}>
              <button onClick={micToggle} aria-pressed={mic.on} style={sx(`display:flex;align-items:center;gap:6px;border:1px solid #151514;background:${mic.on ? '#151514' : '#fcf9f2'};color:${mic.on ? '#fcf9f2' : '#151514'};border-radius:999px;padding:8px 13px;min-height:40px;cursor:pointer;font-size:13px;white-space:nowrap`)}>
                <span style={sx(`width:8px;height:8px;border-radius:50%;background:${mic.on ? '#fcf9f2' : '#151514'};opacity:${mic.on ? 0.35 + mic.level * 0.65 : 1};transform:scale(${mic.on ? 1 + mic.level * 0.6 : 1});transition:opacity .35s`)} />{mic.busy ? 'Finishing…' : mic.on ? 'Listening… tap to stop' : 'Speak'}</button>
              {mic.on && <button onClick={mic.cancel} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:8px 2px;font-size:12.5px')}>Cancel</button>}
              <span aria-live="polite" style={sx('flex:1;font-size:12px;color:#55534d')}>{mic.on ? `0:${String(mic.secs).padStart(2, '0')}` : heardNote}</span>
              <button onClick={() => { setHeardNote(''); read(); }} disabled={!text.trim() || mic.on || mic.busy} style={sx(`background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:9px 16px;min-height:40px;cursor:pointer;font-size:13px;font-weight:600;opacity:${text.trim() ? 1 : 0.4}`)}>Go</button>
            </div>
          </div>

          {!p.garment && !op && <div style={sx('display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:0 2px')}>
            <button onClick={() => p.onGarment('')} disabled={!!p.busy || !!p.unavailable} style={sx('border:1px solid #151514;background:#fcf9f2;border-radius:6px;padding:8px 12px;min-height:40px;cursor:pointer;font-size:13px;white-space:nowrap')}>+ Add clothes to pair</button>
            <span style={sx('flex:1;min-width:180px;font-size:12.5px;color:#55534d;line-height:1.45')}>Optional. Without one, I’ll suggest every piece and all of them count toward your budget.</span>
          </div>}
          {p.garment && !editTop && <div style={sx('display:flex;gap:10px;align-items:center;font-size:13px;padding:0 2px')}>
            <span style={sx(`width:22px;height:28px;border-radius:3px;background:${p.ownedColor};flex:none;border:1px solid #d6d3cc`)} />
            <span style={sx('flex:1;line-height:1.4')}>{p.ownedLabel} · <strong>Yours</strong><br /><span style={sx('font-size:12px;color:#55534d')}>Shown in every look, never counted toward your budget</span></span>
            <button onClick={() => setEditTop(true)} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;font-size:12.5px;padding:10px 2px')}>Change</button>
            <button onClick={p.onOwnedRemove} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;font-size:12.5px;padding:10px 2px')}>Remove</button>
          </div>}
          {op && <div role="group" aria-label="Confirm your top" style={sx('border:1.5px dashed #151514;border-radius:6px;padding:10px;display:flex;flex-direction:column;gap:8px;background:#fcf9f2')}>
            <div style={sx('display:flex;gap:10px;align-items:center')}>
              <img src={p.ownedImage} alt="Your top" style={sx('width:44px;height:56px;object-fit:cover;border-radius:4px;border:1px solid #d6d3cc;flex:none')} />
              <span style={sx(`width:22px;height:28px;border-radius:3px;background:${p.ownedColor};flex:none;border:1px solid #d6d3cc`)} />
              <div style={sx('flex:1;font-size:13px;line-height:1.4')}>Is this right? I'll keep your top in every look and never count it toward your budget.</div>
            </div>
            <label style={sx('display:flex;flex-direction:column;gap:4px;font-size:12px;color:#55534d')}>Its color<input value={ocol} onChange={(e) => setOwnColor(e.target.value)} aria-label="Color of your top" placeholder="e.g. green" style={sx('border:1px solid #837d73;background:#fffdf8;border-radius:6px;padding:8px;font:inherit;font-size:13.5px;color:#151514')} /></label>
            <div style={sx('display:flex;flex-wrap:wrap;gap:6px')} role="group" aria-label="What kind of top">
              {(op.categories || []).map((c) => { const on = c === oc;
                return <button key={c} onClick={() => setOwnCat(c)} aria-pressed={on} style={sx(`border:${on ? '1px solid #151514' : '1px solid #837d73'};background:${on ? '#151514' : '#fcf9f2'};color:${on ? '#fcf9f2' : '#151514'};border-radius:4px;padding:7px 11px;min-height:36px;cursor:pointer;font-size:13px`)}>{(on ? '✓ ' : '') + cap(c)}</button>; })}
            </div>
            <div style={sx('display:flex;gap:8px;align-items:center')}>
              <button onClick={() => { if (oc && ocol.trim()) { p.onOwnedConfirm(oc, ocol.trim()); setOwnCat(null); setOwnColor(null); setEditTop(false); } }} disabled={!oc || !ocol.trim()} style={sx(`background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:9px 14px;min-height:40px;cursor:pointer;font-size:13px;font-weight:600;opacity:${oc && ocol.trim() ? 1 : 0.4}`)}>Use this top</button>
              {editTop ? <button onClick={() => { setEditTop(false); setOwnCat(null); setOwnColor(null); }} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;font-size:12.5px;padding:10px 2px')}>Cancel</button>
                : <button onClick={p.onOwnedRemove} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;font-size:12.5px;padding:10px 2px')}>Remove</button>}
            </div>
          </div>}

          {segs.length > 0 && <div style={sx('font-size:13.5px;line-height:1.75;color:#55534d;padding:0 2px')}>“{segs.map((s, n) => (
            <span key={n} style={sx(`background:${s.on ? (s.sure ? '#e7dfcf' : '#f5f0e6') : 'transparent'};border-bottom:${s.on === undefined ? 'none' : s.on ? (s.sure ? '2px solid #151514' : '2px dashed #151514') : '2px solid transparent'};color:${s.on ? '#151514' : '#55534d'};transition:background .4s;border-radius:2px;padding:1px 0`)}>{s.t}</span>))}”</div>}
          {phase === 'reading' && <div aria-live="polite" style={sx('font-size:13px;color:#55534d')}>Picking out the details…</div>}

          {phase === 'done' && view.length > 0 && <div style={sx('display:flex;flex-direction:column;gap:6px')}>
            {view.map((c) => (
              <div key={c.k + c.i} style={sx(`opacity:${c.vis ? 1 : 0};transform:translateY(${c.vis ? '0' : '6px'});transition:opacity .35s ease,transform .35s ease;border:${c.needs ? '1.5px dashed #151514' : '1px solid #ddd4c4'};background:${c.needs ? '#f5f0e6' : '#fcf9f2'};border-radius:6px;padding:8px 10px;display:flex;flex-direction:column;gap:6px`)}>
                <div style={sx('display:flex;align-items:center;gap:10px')}>
                  <span aria-hidden="true" style={sx(`width:20px;height:20px;border-radius:50%;border:1px solid #151514;background:${c.needs ? '#fcf9f2' : '#151514'};color:${c.needs ? '#151514' : '#fcf9f2'};display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex:none`)}>{c.needs ? '?' : '✓'}</span>
                  <div style={sx('flex:1;min-width:0')}><div style={sx('font-size:12px;color:#55534d')}>{c.label}{c.guessTag}</div><div style={sx('font-weight:600')}>{c.value}</div></div>
                  {c.needs && <button onClick={() => (c.photo ? p.onPhoto() : setOpen(c.isOpen ? null : c.i))} style={sx('background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:8px 12px;min-height:36px;cursor:pointer;font-size:12.5px;font-weight:600;white-space:nowrap')}>{c.cta}</button>}
                  {c.editable && <button onClick={() => setOpen(c.isOpen ? null : c.i)} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;font-size:12.5px;padding:8px 4px')}>Edit</button>}
                </div>
                {c.isOpen && <div style={sx('display:flex;flex-direction:column;gap:6px;padding-left:30px')}>
                  {c.ask && <div style={sx('font-size:13px')}>{c.ask}</div>}
                  {c.text && <div style={sx('display:flex;gap:6px')}>
                    <input value={c.draft} onChange={(e) => setDrafts({ ...drafts, [c.i]: e.target.value })} aria-label={c.label} style={sx('flex:1;min-width:0;border:1px solid #837d73;background:#fffdf8;border-radius:6px;padding:8px;font:inherit;font-size:13.5px')} />
                    <button onClick={() => { const v = (drafts[c.i] ?? c.value).trim(); if (v) setChip(c.i, { value: v, sure: true, missing: false }); }} style={sx('background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:8px 12px;cursor:pointer;font-size:12.5px;font-weight:600')}>Save</button>
                  </div>}
                  <div style={sx('display:flex;flex-wrap:wrap;gap:6px')}>{c.opts.map((o) => <button key={o.label} onClick={o.pick} style={sx(`border:${o.border};background:${o.bg};color:${o.fg};border-radius:4px;padding:7px 11px;min-height:36px;cursor:pointer;font-size:13px`)}>{o.label}</button>)}</div>
                </div>}
              </div>
            ))}
          </div>}

          {fields && <div style={sx(`background:#fcf9f2;border:1px solid #ddd4c4;border-radius:6px;padding:12px;display:grid;grid-template-columns:${wide ? '1fr 1fr 1fr' : '1fr'};gap:10px`)}>
            <label style={sx('display:flex;flex-direction:column;gap:4px;font-size:12px;color:#55534d')}>Occasion<input value={p.occasion} onChange={p.onOccasion} style={sx('border:1px solid #837d73;background:#fffdf8;border-radius:6px;padding:9px;font:inherit;font-size:14px;color:#151514')} /></label>
            <label style={sx('display:flex;flex-direction:column;gap:4px;font-size:12px;color:#55534d')}>Budget for new pieces ($)<input value={p.budget ?? ''} onChange={p.onBudget} inputMode="numeric" style={sx('border:1px solid #837d73;background:#fffdf8;border-radius:6px;padding:9px;font:inherit;font-size:14px;color:#151514')} /></label>
            <div style={sx('display:flex;flex-direction:column;gap:4px;font-size:12px;color:#55534d')}>Avoid<button onClick={p.onHeels} aria-pressed={!!p.noHeels} style={sx(`align-self:flex-start;border:${p.noHeels ? '1px solid #151514' : '1px solid #837d73'};background:${p.noHeels ? '#151514' : '#fcf9f2'};color:${p.noHeels ? '#fcf9f2' : '#151514'};border-radius:4px;padding:8px 12px;font-size:13px;cursor:pointer`)}>{p.noHeels ? '✓ No heels' : 'No heels'}</button></div>
          </div>}

          <div ref={startRef} style={sx('display:flex;flex-direction:column;gap:6px;margin-top:2px')}>
            <button onClick={p.onBegin} disabled={!canStart} style={sx(`background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:13px;min-height:48px;cursor:pointer;font-weight:600;font-size:15px;opacity:${canStart ? 1 : 0.4}`)}>Open fitting room</button>
            <div style={sx('display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px;color:#55534d')}>
              <span aria-live="polite">{startHint}</span>
              <button onClick={() => setFields(!fields)} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;font-size:12.5px;padding:8px 0')}>{fields ? 'Hide fields' : 'Fill in fields instead'}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
