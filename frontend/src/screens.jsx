import { useEffect, useRef, useState } from 'react';
import { sx } from './sx.js';
import { Look, Mark, MicIcon, ShareIcon } from './parts.jsx';
import { useDictation } from './voice.js';
import { MERCHANT, SLOT_LABEL, SLOTS, subtotal, priceLine, label, shortName, picture, keys, interpretText, list } from './model.js';

// ---------- colors ----------
// The colors arrive gently once the reading is done: the headline, then each swatch rising into the strip, then why,
// the shopper's top against it, the note and the button. Reduced motion shows everything at once.
const SWATCH_GAP = 170;
export function Palette({ c }) {
  const { sv, ui, A, wide, garment } = c, jr = sv.journey, pal = jr.palette, an = jr.analysis || {};
  const phase = pal ? 'ready' : an.status === 'unavailable' ? 'none' : 'reading';
  const lead = wide ? '24px' : '19px';
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (phase !== 'ready') { setStep(0); return undefined; }
    if (c.reduced) { setStep(9); return undefined; }
    const sw = 500 + pal.swatches.length * SWATCH_GAP;
    const ts = [[1, 50], [2, 500], [3, sw + 250], [4, sw + 1000], [5, sw + 1700], [6, sw + 2300]].map(([n, ms]) => setTimeout(() => setStep(n), ms));
    return () => ts.forEach(clearTimeout);
  }, [phase]);
  const fade = (n, extra = '') => sx(`opacity:${step >= n ? 1 : 0};transform:translateY(${step >= n ? '0' : '8px'});transition:opacity .6s ease,transform .6s ease${extra}`);
  return (
    <div style={sx(`display:grid;grid-template-columns:${wide ? 'minmax(0,1fr) minmax(0,1fr)' : '1fr'};gap:20px;align-items:start`)}>
      <div style={sx(`height:${wide ? 'max(440px, min(620px, calc(100vh - 240px)))' : '300px'};background:#efe8db url('${sv.photo ? sv.photo.url : ''}') center / contain no-repeat;border:1px solid #d3d0c8;border-radius:6px;display:flex;flex-direction:column;justify-content:space-between;overflow:hidden`)}>
        <div style={sx("font-family:'IBM Plex Mono',monospace;font-size:10px;color:#55534d;padding:8px;background:rgba(252,249,242,.85);align-self:flex-start;border-radius:0 0 4px 0")}>YOUR PHOTO</div>
        {phase === 'ready' && <div style={sx(`background:#f3f2ee;border-top:1px solid #d3d0c8;padding:8px;display:flex;flex-direction:column;gap:6px;opacity:${step >= 2 ? 1 : 0};transition:opacity .5s ease`)}>
          <div style={sx(`display:flex;height:${wide ? '52px' : '36px'};align-items:flex-end`)}>{pal.swatches.map((w, n) => <div key={n} style={sx(`flex:1;height:100%;background:${w};transform-origin:bottom;transform:scaleY(${step >= 2 ? 1 : 0});opacity:${step >= 2 ? 1 : 0};transition:transform .7s cubic-bezier(.2,.7,.2,1) ${n * SWATCH_GAP}ms,opacity .7s ease ${n * SWATCH_GAP}ms`)} />)}</div>
          <div style={fade(3)}><span style={sx("font-family:'IBM Plex Mono',monospace;font-size:10px;color:#55534d")}>{pal.name.toUpperCase()} · SUGGESTED, NOT A VERDICT</span></div>
        </div>}
      </div>
      <div style={sx('display:flex;flex-direction:column;gap:14px;min-width:0')}>
        {phase === 'reading' && <div style={sx('display:flex;gap:12px;align-items:flex-start')}><Mark />
          <div aria-live="polite" style={sx('display:flex;flex-direction:column;gap:8px;flex:1')}><div style={sx(`font-weight:600;letter-spacing:-0.025em;font-size:${lead};line-height:1.3`)}>Looking at the colors in your photo…</div></div></div>}
        {phase === 'ready' && <>
          <div style={fade(1)}><div style={sx('display:flex;gap:12px;align-items:flex-start')}><Mark /><div aria-live="polite" style={sx(`font-weight:600;letter-spacing:-0.025em;font-size:${lead};line-height:1.3;text-wrap:pretty`)}>{pal.headline}</div></div></div>
          <div style={fade(3)}><div style={sx('font-size:14.5px;line-height:1.55;text-wrap:pretty')}>{pal.explanation}</div></div>
          {garment && pal.blouseLine && <div style={fade(4)}><div style={sx(`font-size:14.5px;line-height:1.55;border-left:3px solid ${(sv.owned && sv.owned.hex) || '#2b4aa3'};padding-left:12px;text-wrap:pretty`)}>{pal.blouseLine}</div></div>}
          <div style={fade(5)}><div style={sx('font-size:12.5px;color:#55534d;line-height:1.5')}>{pal.note}</div></div>
          <div style={fade(6)}><button onClick={() => A.palette(pal.name)} disabled={!!ui.pend || step < 6} style={sx('background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:13px 22px;min-height:48px;cursor:pointer;font-weight:600;font-size:15px')}>Show me my first looks</button></div>
        </>}
        {phase === 'none' && <>
          <div style={sx('display:flex;gap:12px;align-items:flex-start')}><Mark /><div style={sx(`font-weight:600;letter-spacing:-0.025em;font-size:${lead};line-height:1.3`)}>I can't suggest colors from this photo.</div></div>
          <div style={sx('font-size:14.5px;line-height:1.55;text-wrap:pretty')}>{(an.message || 'I couldn’t read your colors from this photo.') + ' Your looks don’t depend on it.'}</div>
          <div style={sx('display:flex;flex-direction:column;gap:8px;align-items:flex-start')}>
            <button onClick={() => A.palette('No palette')} disabled={!!ui.pend} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:8px 0;font-size:13.5px')}>Continue without colors</button>
          </div>
        </>}
      </div>
    </div>
  );
}

// ---------- shared card pieces ----------
const pieceList = (c, items, o = {}) => items.map((i, k) => {
  const hi = o.hi && o.hi[k], fixed = !i.owned && c.ui.pins[i.slot] === i.key;
  return (
    <button key={i.slot} onClick={() => c.A.set({ piece: { item: i, ctx: o.ctx } })} style={sx(`display:flex;gap:7px;align-items:center;text-align:left;border:${hi ? '2px solid #151514' : '1px solid #ddd4c4'};background:#fcf9f2;border-radius:6px;padding:6px 8px;min-height:34px;cursor:pointer;font-size:12.5px;line-height:1.25;font-weight:${hi ? 600 : 400}`)}>
      <span style={sx(`width:12px;height:12px;border-radius:2px;background:${i.color};flex:none;border:1px solid #d6d3cc`)} />
      <span style={sx('flex:1;min-width:0')}>{shortName(i)}</span>
      {hi && <span style={sx('font-size:10.5px;letter-spacing:.04em;color:#151514;border:1px solid #151514;border-radius:3px;padding:0 4px')}>{o.hiWord || 'DIFFERENT'}</span>}
      <span style={sx('font-size:11.5px;color:#55534d;font-variant-numeric:tabular-nums')}>{i.owned ? 'Yours' : fixed ? 'Fixed' : '$' + i.price}</span>
    </button>
  );
});

function Card({ c, col }) {
  const { wide } = c, pieces = col.items.length;
  if (col.ask) {
    const a = col.ask;
    return (
      <div style={sx(`scroll-snap-align:start;display:flex;flex-direction:column;gap:7px;min-width:0;border:2px solid #151514;border-radius:6px;padding:${wide ? '6px' : '3px'};background:#fcf9f2`)}>
        <div style={sx(`display:flex;flex-direction:column;gap:10px;border:1px solid #ddd4c4;border-radius:6px;padding:14px;background:#fcf9f2;min-height:${col.h};box-sizing:border-box`)}>
          <div style={sx("font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.06em;color:#55534d")}>{a.kicker}</div>
          <div role="status" style={sx('font-size:15.5px;font-weight:600;line-height:1.4')}>{a.title}</div>
          {a.note && <div style={sx('font-size:13px;color:#55534d;line-height:1.45')}>{a.note}</div>}
          <div style={sx('display:flex;flex-wrap:wrap;gap:6px')}>{a.choices.map((ch) => <button key={ch} onClick={() => c.A.answer(ch)} style={sx('border:1px solid #151514;background:#fcf9f2;border-radius:6px;padding:10px 14px;min-height:44px;cursor:pointer;font-size:14px;font-weight:600')}>{ch}</button>)}</div>
          {a.alt && <div style={sx('border-top:1px solid #ddd4c4;padding-top:10px;display:flex;flex-direction:column;gap:6px')}><div style={sx('font-size:13px;line-height:1.45')}>{a.alt}</div>
            <button onClick={c.A.showAlt} style={sx('align-self:flex-start;border:1px dashed #151514;background:#fcf9f2;border-radius:6px;padding:9px 12px;min-height:40px;cursor:pointer;font-size:13px')}>See the alternative</button></div>}
          <button onClick={c.A.back} style={sx('margin-top:auto;align-self:flex-start;border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:6px 0;font-size:13px')}>Back to your look</button>
        </div>
      </div>
    );
  }
  const retry = col.pic.job && col.pic.job.retry;
  return (
    <div style={sx(`scroll-snap-align:start;display:flex;flex-direction:column;gap:7px;min-width:0;border:${col.frame || '2px solid transparent'};border-radius:6px;padding:${wide ? '6px' : '3px'};background:${col.cardBg || 'transparent'};opacity:${col.op || 1};transform:scale(${col.sc || 1});transition:border-color .2s,opacity .3s,transform .3s`)}>
      <Look items={col.items} status={col.pic.status} label={col.label} height={col.h} image={col.pic.image} imageTag={col.pic.imageTag} tag={col.sketchTag} note={col.pic.note} />
      <div style={sx('font-size:13px;line-height:1.4;min-height:18px')}>{col.tag}</div>
      {!wide && <button onClick={() => c.A.set({ plist: { items: col.items, price: col.priceLine, title: col.label + ' · pieces' } })} style={sx('text-align:left;border:none;background:none;color:#0d6b66;padding:6px 0;font-size:13px;text-decoration:underline;cursor:pointer')}>See the {pieces} pieces</button>}
      {wide && <div style={sx('display:flex;flex-direction:column;gap:4px')}>{pieceList(c, col.items, { ctx: col.ctx, hi: col.hi, hiWord: col.hiWord })}</div>}
      <div style={sx('font-size:12px;color:#55534d;font-variant-numeric:tabular-nums')}>{wide ? col.priceLine : '$' + subtotal(col.items) + ' new'}</div>
      {col.pic.status === 'failed' && col.pic.job && <button onClick={() => c.A.retry(col.pic.job)} disabled={!!(retry && !retry.allowed)} style={sx('background:#fcf9f2;border:1px solid #151514;border-radius:6px;padding:9px;min-height:40px;cursor:pointer;font-size:13px')}>{retry && !retry.allowed ? 'Tried as many times as allowed' : 'Try the picture again'}</button>}
      <button onClick={col.pick} disabled={col.pickDisabled} aria-label={col.pickAria} style={sx(`margin-top:auto;border:${col.pBorder || '1px solid #151514'};background:${col.pBg || '#fcf9f2'};color:${col.pFg || '#151514'};border-radius:6px;padding:12px 0;min-height:48px;cursor:pointer;font-weight:600;font-size:15px;transition:background .15s`)}>{col.pickText}</button>
    </div>
  );
}

// ---------- rounds, finalists, change requests ----------
export function Stage({ c }) {
  const { sv, ui, A, wide, budget, step, rev, cur } = c, jr = sv.journey, win = jr.win || {}, H = wide ? '400px' : '330px', pend = ui.pend;
  const lookCol = (id, key, lbl) => {
    const l = jr.looks[id], pic = picture(l.job, l.compositionId), pending = pic.status === 'pending';
    const on = pend && pend.key === key && pend.id === id, off = pend && pend.key === key && pend.id !== id;
    return { items: l.items, pic, label: lbl, h: H, tag: l.tag, priceLine: priceLine(l.items, budget), ctx: 'round',
      frame: on ? '2px solid #151514' : '2px solid transparent', cardBg: on ? '#fcf9f2' : 'transparent', op: off ? 0.65 : 1, sc: on && !c.reduced ? 1.01 : 1,
      pBg: on ? '#151514' : '#fcf9f2', pFg: on ? '#fcf9f2' : '#151514', pBorder: pending ? '1px dashed #837d73' : '1px solid #151514',
      pickText: pending ? 'Making picture…' : on ? '✓ Kept' : off ? 'Passed' : '♥ Keep', pickDisabled: pending || !!pend, pickAria: 'Keep ' + lbl,
      pick: () => { if (!pending) A.keep(key, id); } };
  };
  let cols = [], title = '', sub = '';
  if (step === 'round1') {
    const ids = jr.round1 || ['A', 'B'], waiting = ids.filter((id) => jr.looks[id].job.status === 'running'), ready = ids.filter((id) => !waiting.includes(id));
    const names = (a) => (a.length > 1 ? 'Looks ' + list(a) : 'Look ' + a[0]);
    title = 'Which would you rather wear?';
    sub = !waiting.length ? 'Keep one. The ' + (ids.length > 2 ? 'others are' : 'other is') + ' passed for this round only.'
      : !ready.length ? 'Making ' + (ids.length > 2 ? 'all three' : 'both') + ' pictures. You can keep a look once its picture is ready.'
        : names(ready) + (ready.length > 1 ? ' are' : ' is') + ' ready. ' + names(waiting) + (waiting.length > 1 ? ' are' : ' is') + ' still being made.';
    cols = ids.map((id) => lookCol(id, 'r1', 'Look ' + id));
  } else if (step === 'round2') {
    const base = win.r1, ch = jr.challenger || 'C', a = keys(jr.looks[base].items), b = keys(jr.looks[ch].items);
    const diff = SLOTS.filter((k) => a[k] && b[k] && a[k] !== b[k]).map((k) => SLOT_LABEL[k].toLowerCase());
    title = 'Your pick vs a new look';
    sub = !!a.dress !== !!b.dress ? `Look ${base} is back. Look ${ch} is ${b.dress ? 'a dress' : 'a top and trousers'} instead.` : `Look ${base} is back. Look ${ch} is a new option with different ${list(diff)}.`;
    cols = [lookCol(base, 'r2', 'Look ' + base + ' · your pick'), lookCol(ch, 'r2', 'Look ' + ch + ' · new')];
  } else if (step === 'final') {
    const wid = win.r2, carry = jr.carry || {}, trade = (carry.tradeoffs || []).map((t) => t.message).join(' ');
    title = 'Your final outfits';
    sub = ((carry.learned || []).length ? `Built from Look ${wid} and what you told me.` : `Built from Look ${wid} and your brief.`) + ' Keep your favorite; you can still change pieces after.';
    const p1 = lookCol(wid, 'fin', 'Proposal 1'), p2 = lookCol('P2', 'fin', 'Proposal 2'), k1 = keys(p1.items), k2 = keys(p2.items);
    cols = [{ ...p1, tag: `Your Look ${wid}, as you kept it`, hi: p1.items.map((i) => i.key !== k2[i.slot]) },
      { ...p2, tag: carry.tag + (trade ? ' ' + trade : ''), hi: p2.items.map((i) => i.key !== k1[i.slot]) }];
  } else if (step === 'revise') {
    const pic = picture(cur.previewJob, cur.compositionId), alt = ui.showAlt === rev.revisionId && rev.noMatch && rev.noMatch.alternative;
    const opts = rev.status === 'open' ? rev.candidates.filter((x) => x.kind === 'match') : alt ? rev.candidates.filter((x) => x.candidateId === alt.candidateId) : [];
    // The choice stays visible before the next screen: the kept card, and the others passed.
    const picked = pend && pend.key === 'accept' ? pend.id : pend === 'dismiss' ? 'mine' : null;
    const look = (on, off) => ({ frame: on ? '2px solid #151514' : undefined, cardBg: on ? '#fcf9f2' : undefined, op: off ? 0.65 : 1, sc: on && !c.reduced ? 1.01 : 1,
      pBg: on ? '#151514' : undefined, pFg: on ? '#fcf9f2' : undefined });
    const mine = { items: cur.items, pic, label: 'Your winner', h: H, tag: 'As it is now', priceLine: priceLine(cur.items, budget), ctx: 'option',
      pickText: picked === 'mine' ? '✓ Kept' : picked ? 'Passed' : '♥ Keep this one', pickAria: 'Keep your winner as it is', pick: A.back, pickDisabled: !!pend,
      ...look(picked === 'mine', picked && picked !== 'mine') };
    title = opts.length > 1 ? 'Your winner vs two new versions' : 'Your winner vs a new version';
    if (!opts.length) {
      sub = 'You asked: “' + rev.requestText + '”';
      const head = rev.status === 'needs_clarification' ? rev.clarification.question : rev.status === 'no_match' ? rev.noMatch.message
        : rev.message || '';
      cols = [mine, { items: [], h: H, ask: { kicker: { needs_clarification: 'ONE QUESTION', no_match: 'NO MATCH', cannot: 'CAN’T DO THAT', unsupported: 'NOT SURE WHAT TO CHANGE', kept: 'NOTHING CHANGED', size_change: 'SIZE, NOT STYLE' }[rev.status] || '',
        title: head, note: [...(rev.notes || []), rev.suggestion || ''].filter(Boolean).join(' '),
        choices: rev.status === 'needs_clarification' ? rev.clarification.options : [], alt: rev.status === 'no_match' && rev.noMatch.alternative ? rev.noMatch.alternative.label : '' } }];
    } else {
      const k0 = keys(cur.items), many = opts.length > 1;
      sub = 'You asked: “' + rev.requestText + '” ' + (alt ? rev.noMatch.message + ' ' : rev.acknowledgement ? rev.acknowledgement + ' ' : '')
        + (many ? rev.scopeSentence || '' : opts[0].changeSummary || rev.scopeSentence || '') + (rev.onlyOption && !alt ? ' ' + rev.onlyOption : '') + ((rev.notes || []).length ? ' ' + rev.notes.join(' ') : '');
      cols = [mine, ...opts.map((o, n) => {
        const d = o.priceDelta, delta = !d ? 'Same price' : (d > 0 ? '+$' : '−$') + Math.round(Math.abs(d) * 100) / 100;
        const opic = o.previewJob ? picture(o.previewJob, o.compositionId) : { status: 'ready', image: '' }, waiting = opic.status === 'pending';
        const on = picked === o.candidateId, name = alt ? 'Alternative' : many ? 'New option ' + (n + 1) : 'New · ' + o.label;
        return { items: o.items, pic: opic, sketchTag: o.previewJob ? '' : 'COLOR SKETCH · PICTURE AFTER YOU KEEP IT', label: name, h: H, ctx: 'option',
          tag: (alt ? alt.label + ' · ' + delta : (many ? o.changeSummary.split('. ')[0] + ' · ' : o.label + ' · ') + delta + (/Outside/.test(o.why) ? ' · outside your suggested palette' : '')) + (o.sizeNote ? ' · ' + o.sizeNote : ''),
          priceLine: priceLine(o.items, budget), hi: o.items.map((i) => i.key !== k0[i.slot]), hiWord: 'NEW', ...look(on, picked && !on),
          pFg: waiting ? '#55534d' : on ? '#fcf9f2' : undefined, pBorder: waiting ? '1px dashed #837d73' : '1px solid #151514',
          pickText: on ? '✓ Kept' : picked ? 'Passed' : waiting ? 'Making picture…' : opic.status === 'failed' ? '♥ Keep this one (picture later)' : alt ? '♥ Keep this alternative' : '♥ Keep this one',
          pickAria: alt ? 'Keep the alternative' : 'Keep ' + name, pickDisabled: waiting || !!pend, pick: () => A.accept(o.candidateId) };
      })];
    }
  }
  const n = cols.length;
  return <>
    <div style={sx('display:flex;justify-content:space-between;align-items:baseline;gap:4px 12px;flex-wrap:wrap')}>
      <h1 style={sx(`margin:0;font-weight:600;letter-spacing:-0.025em;font-size:${wide ? '24px' : '19px'};line-height:1.2`)}>{title}</h1>
      <div style={sx('font-size:13px;color:#55534d;line-height:1.4')}>{sub}</div>
    </div>
    <div style={sx(`display:grid;grid-template-columns:${n === 3 ? (wide ? 'repeat(3,minmax(0,1fr))' : 'repeat(3,78%)') : 'minmax(0,1fr) minmax(0,1fr)'};gap:${wide ? '16px' : '8px'};align-items:stretch;overflow-x:${n === 3 && !wide ? 'auto' : 'visible'};scroll-snap-type:x mandatory`)}>
      {cols.map((col, k) => <Card key={k} c={c} col={col} />)}
    </div>
  </>;
}

// ---------- what to carry (after round 2 only) ----------
export function Why({ c }) {
  const { sv, ui, A, wide, budget } = c, jr = sv.journey, id = jr.win.r2, l = jr.looks[id], pic = picture(l.job, l.compositionId);
  const sel = ui.why[id] || [], reasons = jr.reasons || [];
  const carry = [...new Set([...sel, ...interpretText(ui.whyText)])], heard = reasons.filter((r) => carry.includes(r.k));
  const before = useRef('');
  const mic = useDictation({ speech: sv.status && sv.status.speech, onText: (t) => A.set({ whyText: t }),
    onEnd: (t) => { if (t === null) A.set({ whyText: before.current }); }, onError: A.voiceError });
  const toggle = (k) => A.set((u) => ({ why: { ...u.why, [id]: sel.includes(k) ? sel.filter((x) => x !== k) : [...sel, k] } }));
  const typed = !!ui.whyText.trim() && !ui.whyTyping;
  return (
    <div style={sx(`display:grid;grid-template-columns:${wide ? 'minmax(0,1fr) minmax(0,1fr)' : '1fr'};gap:${wide ? '28px' : '14px'};align-items:start`)}>
      <div style={sx(`display:flex;flex-direction:column;gap:6px;min-width:0;position:${wide ? 'sticky' : 'static'};top:0`)}>
        <Look items={l.items} label={'Look ' + id + ' · your winner'} height={wide ? 'max(520px, calc(100vh - 220px))' : '250px'} status={pic.status} image={pic.image} imageTag={pic.imageTag} note={pic.note} />
        <div style={sx('display:flex;justify-content:space-between;gap:8px;align-items:center')}>
          <button onClick={() => A.set({ plist: { items: l.items, price: priceLine(l.items, budget), title: 'Look ' + id + ' · pieces' } })} style={sx('border:none;background:none;color:#0d6b66;padding:6px 0;font-size:13px;text-decoration:underline;cursor:pointer;white-space:nowrap')}>See the {l.items.length} pieces</button>
          <span style={sx('font-size:12px;color:#55534d;font-variant-numeric:tabular-nums;text-align:right')}>{wide ? priceLine(l.items, budget) : '$' + subtotal(l.items) + ' new'}</span>
        </div>
      </div>
      <div style={sx('display:flex;flex-direction:column;gap:10px;min-width:0')}>
        <div style={sx('display:flex;gap:10px;align-items:flex-start')}><Mark />
          <div><div style={sx(`font-weight:600;letter-spacing:-0.025em;font-size:${wide ? '24px' : '19px'};line-height:1.25`)}>What should I carry into your final looks?</div><div style={sx('font-size:13px;color:#55534d;margin-top:3px')}>Pick any, say it in your own words, or skip.</div></div></div>
        <div style={sx('display:grid;grid-template-columns:1fr 1fr;gap:8px')}>
          {reasons.map((r) => { const on = sel.includes(r.k);
            return <button key={r.k} onClick={() => toggle(r.k)} aria-pressed={on} style={sx(`text-align:left;border:${on ? '2px solid #151514' : '1px solid #837d73'};background:${on ? '#151514' : '#fcf9f2'};color:${on ? '#fcf9f2' : '#151514'};border-radius:6px;padding:10px 12px;min-height:72px;cursor:pointer;display:flex;flex-direction:column;justify-content:center;gap:2px;transition:background .15s`)}>
              <span style={sx('font-weight:600;font-size:14px')}>{(on ? '✓ ' : '') + r.label}</span><span style={sx('font-size:12.5px;line-height:1.35')}>{r.short}</span></button>; })}
          <button onClick={() => { if (!mic.on) before.current = ui.whyText; mic.toggle(); }} aria-pressed={mic.on} style={sx(`text-align:left;border:1px dashed #151514;background:${mic.on ? '#151514' : '#fcf9f2'};color:${mic.on ? '#fcf9f2' : '#151514'};border-radius:6px;padding:10px 12px;min-height:72px;cursor:pointer;display:flex;flex-direction:column;justify-content:center;gap:2px`)}>
            <span style={sx('display:flex;align-items:center;gap:7px;font-weight:600;font-size:14px')}><MicIcon /><span style={sx('white-space:nowrap')}>{mic.busy ? 'Finishing…' : mic.on ? 'Listening…' : 'Say more'}</span></span>
            <span style={sx('font-size:12.5px;line-height:1.35')}>{mic.on ? `Tap to stop · 0:${String(mic.secs).padStart(2, '0')}` : 'Tell me more in your words'}</span></button>
        </div>
        {mic.on && <button onClick={mic.cancel} style={sx('align-self:flex-start;border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:4px 0;font-size:12.5px')}>Cancel recording</button>}
        {typed && <div style={sx('font-size:13px;line-height:1.45;color:#151514')}>“{ui.whyText}”</div>}
        {ui.whyTyping && <input value={ui.whyText} onChange={(e) => A.set({ whyText: e.target.value })} aria-label="Anything else you love about it" placeholder="Anything else you love about it?" style={sx('border:1px solid #837d73;background:#fffdf8;border-radius:6px;padding:10px 12px;font-size:14px')} />}
        {!ui.whyTyping && <button onClick={() => A.set({ whyTyping: true })} style={sx('align-self:flex-start;border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:4px 0;font-size:12.5px')}>Type instead</button>}
        <div style={sx('display:flex;flex-wrap:wrap;gap:5px;align-items:center')}><span style={sx('font-size:12.5px;color:#55534d')}>Final outfits will use:</span>
          {(heard.length ? heard.map((h) => ({ t: '✓ ' + h.value, on: true })) : [{ t: 'Nothing yet. Tap above, or say it.' }]).map((x) => <span key={x.t} style={sx(`border:1px solid ${x.on ? '#151514' : '#ddd4c4'};background:${x.on ? '#efe8db' : '#fcf9f2'};border-radius:4px;padding:3px 8px;font-size:12.5px;white-space:nowrap`)}>{x.t}</span>)}</div>
        <div style={sx('display:flex;gap:14px;align-items:center')}>
          <button onClick={() => A.finalize(carry, ui.whyText || '')} disabled={!!ui.pend} style={sx(`flex:${wide ? '0 0 auto' : '1'};background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:13px 22px;min-height:48px;cursor:pointer;font-weight:600;font-size:15px;white-space:nowrap`)}>Show me final outfits</button>
          <button onClick={() => A.finalize([], '')} disabled={!!ui.pend} style={sx('border:none;background:none;color:#0d6b66;padding:10px 4px;text-decoration:underline;cursor:pointer;font-size:13px')}>Skip</button>
        </div>
      </div>
    </div>
  );
}

// ---------- the selected look: versions, sizes, changes, share, bag ----------
export function shopOf(c) {
  const { cur, ui, sv } = c, items = cur.items.filter((i) => !i.owned), missing = items.filter((i) => i.sizes && !i.savedSize).length;
  const sent = !!ui.cart[cur.versionId], n = items.length;
  return { items, owned: cur.items.filter((i) => i.owned), missing, allSized: !missing, sent, n, sub: subtotal(cur.items),
    text: sent ? `Sent to your ${MERCHANT} bag · open again ↗` : !missing ? `Add ${n} items to your ${MERCHANT} bag ↗` : `Choose ${missing} size${missing > 1 ? 's' : ''} to continue`,
    short: sent ? `Sent to ${MERCHANT} bag ↗` : !missing ? `Add ${n} to ${MERCHANT} bag ↗` : `Choose ${missing} size${missing > 1 ? 's' : ''}`,
    budgetText: c.budget == null ? '$' + subtotal(cur.items) + ' · no budget limit' : '$' + subtotal(cur.items) + ' of $' + c.budget + ' budget',
    note: `Sends these exact items and sizes to your ${MERCHANT} bag. You check out and pay there; the final price is set at checkout. Calliope never places orders.`, sv };
}

function Sizes({ c, i, narrow }) {
  const { ui, A } = c, size = i.savedSize || '', un = i.unavailable || [];
  const open = ui.sizeOpen[i.slot] !== undefined ? ui.sizeOpen[i.slot] : !size;
  const btns = (i.sizes || []).map((z) => { const sel = size === z, so = un.includes(z);
    return <button key={z} onClick={() => { if (!so) A.size(i.slot, z); }} aria-label={'Size ' + z + (so ? ', sold out' : '') + (sel ? ', selected' : '')} aria-pressed={sel}
      style={sx(`${narrow ? 'min-height:36px' : 'min-width:40px;min-height:38px'};border:${sel ? '2px solid #151514' : '1px solid #837d73'};background:${sel ? '#151514' : '#fcf9f2'};color:${so ? '#55534d' : sel ? '#fcf9f2' : '#151514'};text-decoration:${so ? 'line-through' : 'none'};border-radius:4px;padding:${narrow ? '4px 2px' : '5px 6px'};font-size:${narrow ? '12.5px' : '13px'};cursor:pointer;font-variant-numeric:tabular-nums`)}>{z}</button>; });
  const src = '(Livostyle data; checked live when you open or send)';
  const avail = !i.sizes ? '✓ One size · in stock ' + src : size ? '✓ Size ' + size + ' in stock ' + src + (un.length ? ' · size ' + un.join(', ') + ' sold out' : '') : 'Choose a size' + (un.length ? ' · size ' + un.join(', ') + ' sold out' : '');
  const group = SLOT_LABEL[i.slot] + ' size';
  if (!narrow) return <>
    {i.sizes && <div role="group" aria-label={group} style={sx('display:flex;flex-wrap:wrap;gap:5px;align-items:center')}><span style={sx('font-size:12.5px;color:#55534d;margin-right:2px')}>{size ? 'Your size' : 'Size'}</span>{btns}</div>}
    <div style={sx('font-size:12.5px;color:#55534d;line-height:1.45')}>{avail}</div>
  </>;
  return <>
    {i.sizes && <button onClick={() => A.set((u) => ({ sizeOpen: { ...u.sizeOpen, [i.slot]: !open } }))} aria-expanded={open} style={sx(`display:flex;justify-content:space-between;align-items:center;border:${size ? '1px solid #151514' : '1.5px dashed #151514'};background:#fffdf8;color:#151514;border-radius:4px;padding:6px 8px;min-height:36px;cursor:pointer;font-size:12.5px`)}><span style={sx('white-space:nowrap')}>{size ? '✓ Size ' + size : 'Choose size'}</span><span aria-hidden="true">{open ? '▴' : '▾'}</span></button>}
    {i.sizes && open && <div role="group" aria-label={group} style={sx('display:grid;grid-template-columns:repeat(4,1fr);gap:4px')}>{btns}</div>}
    {(!i.sizes || open) && <div style={sx('font-size:11.5px;color:#55534d;line-height:1.35')}>{avail}</div>}
  </>;
}

export function Winner({ c }) {
  const { sv, ui, A, wide, cur } = c, vers = sv.versions, pic = picture(cur.previewJob, cur.compositionId), shop = shopOf(c);
  const lookEl = <Look items={cur.items} status={pic.status} label={cur.label} height={wide ? '600px' : '340px'} image={pic.image} imageTag={pic.imageTag} note={pic.note} />;
  const lines = [];
  if (vers.length > 1) {
    if (sv.cur > 0) {
      const a = keys(vers[0].items), b = keys(cur.items), byB = Object.fromEntries(cur.items.map((i) => [i.slot, i])), ch = SLOTS.filter((k) => a[k] !== b[k]);
      const s0 = subtotal(vers[0].items), s1 = subtotal(cur.items);
      if (ch.length) lines.push('Changed: ' + ch.map((k) => SLOT_LABEL[k].toLowerCase() + (b[k] ? ' → ' + byB[k].variant : ' → replaced')).join(', ') + '. Everything else unchanged.');
      lines.push('Subtotal $' + s1 + (s1 === s0 ? ', same as the original.' : ' (' + (s1 > s0 ? '+' : '−') + '$' + Math.round(Math.abs(s1 - s0) * 100) / 100 + ' vs original).'));
      const ms = b.dress ? 'dress' : 'bottom', it = byB[ms];
      if (it && it.savedSize) lines.push('Your ' + (ms === 'dress' ? 'dress' : 'trouser') + ' size ' + it.savedSize + ' carried over.');
    } else lines.push('This is the look you first picked. Your updated version is saved.');
  }
  const retryOn = pic.status === 'failed' || !!(pic.partial && pic.job.retry && pic.job.retry.allowed);
  useEffect(() => { if (pic.status === 'ready' && pic.image) A.prepShare(pic.image); }, [pic.status, pic.image]);
  const dress = cur.items.some((i) => i.slot === 'dress');
  const askRef = useRef(null), askBefore = useRef('');
  // The words stay in the box to check or edit and Go sends them like typing.
  const mic = useDictation({ speech: sv.status && sv.status.speech,
    onText: (t) => A.set({ askText: t }), onEnd: (t) => { if (t === null) A.set({ askText: askBefore.current }); else if (t && askRef.current) askRef.current.focus(); },
    onError: (m) => { A.set((u) => ({ askText: u.askText || askBefore.current })); A.voiceError(m); } });
  const asking = ui.pend === 'ask';
  const pieces = shop.items.map((i) => (
    <div key={i.slot} style={sx('border-top:1px solid #ddd4c4;padding-top:10px;display:flex;flex-direction:column;gap:7px')}>
      <div style={sx('display:flex;gap:10px;align-items:center')}><span style={sx(`width:34px;height:42px;border-radius:4px;background:${i.color};flex:none;border:1px solid #d6d3cc`)} />
        <div style={sx('flex:1;min-width:0')}><div style={sx('font-weight:600;font-size:14px')}>{label(i)}</div><div style={sx('font-size:12.5px;color:#55534d')}>{SLOT_LABEL[i.slot]}</div></div>
        <div style={sx('font-variant-numeric:tabular-nums;font-weight:600')}>${i.price}</div></div>
      <Sizes c={c} i={i} />
      <button onClick={() => A.openProduct(i)} style={sx('align-self:flex-start;border:1px solid #151514;background:#fcf9f2;border-radius:6px;padding:7px 11px;min-height:38px;cursor:pointer;font-size:12.5px;white-space:nowrap')}>{(ui.opened[i.slot] ? '✓ Opened · ' : '') + `Open at ${MERCHANT} ↗`}</button>
    </div>));
  const ownedRows = shop.owned.map((o) => (
    <div key={o.slot} style={sx('border-top:1px solid #ddd4c4;padding-top:10px;display:flex;gap:10px;align-items:center;font-size:13.5px')}><span style={sx(`width:34px;height:42px;border-radius:4px;background:${o.color};flex:none;border:1px solid #d6d3cc`)} />
      <span style={sx('flex:1;line-height:1.4')}>{label(o)}<br /><span style={sx('font-size:12.5px;color:#55534d')}>Yours · not included in what you buy</span></span><span style={sx('font-variant-numeric:tabular-nums;color:#55534d')}>$0</span></div>));
  return <>
    <div style={sx('display:flex;justify-content:space-between;align-items:center;gap:8px 12px')}>
      <div style={sx('display:flex;flex-direction:column;gap:2px;min-width:0')}><h1 style={sx(`margin:0;font-weight:600;letter-spacing:-0.025em;font-size:${wide ? '26px' : '21px'}`)}>{sv.cur > 0 ? 'Your winner, updated' : 'Your winner'}</h1><div style={sx('font-size:13px;color:#55534d')}>Picked, not bought. You check out on {MERCHANT}.</div></div>
      <button onClick={A.share} aria-label="Share this look" style={sx('flex:none;display:flex;align-items:center;gap:7px;border:1px solid #151514;background:#fcf9f2;border-radius:6px;padding:10px 14px;min-height:44px;cursor:pointer;font-weight:600;font-size:14px;white-space:nowrap')}><ShareIcon />Share</button>
    </div>
    <div style={sx(`display:grid;grid-template-columns:${wide ? 'minmax(0,1.05fr) minmax(0,1fr)' : '1fr'};gap:20px;align-items:start`)}>
      <div style={sx('display:flex;flex-direction:column;gap:10px;min-width:0')}>
        {wide && lookEl}
        {!wide && <div style={sx('display:grid;grid-template-columns:minmax(0,0.82fr) minmax(0,1.18fr);gap:10px;align-items:start')}>
          <div style={sx('position:sticky;top:0')}>{lookEl}</div>
          <section aria-label="The pieces" style={sx('display:flex;flex-direction:column;gap:6px;min-width:0')}>
            <div style={sx('display:flex;justify-content:space-between;align-items:baseline')}><h2 style={sx('margin:0;font-weight:600;letter-spacing:-0.025em;font-size:15px')}>The pieces</h2><span style={sx('font-size:12px;color:#55534d;white-space:nowrap')}>{shop.n} to buy</span></div>
            {shop.items.map((i) => (
              <div key={i.slot} style={sx('background:#fcf9f2;border:1px solid #ddd4c4;border-radius:6px;padding:8px;display:flex;flex-direction:column;gap:5px')}>
                <div style={sx('display:flex;gap:7px;align-items:flex-start')}><span style={sx(`width:16px;height:22px;border-radius:3px;background:${i.color};flex:none;border:1px solid #d6d3cc;margin-top:1px`)} /><div style={sx('flex:1;min-width:0;font-weight:600;font-size:12.5px;line-height:1.3')}>{label(i)}</div><div style={sx('font-variant-numeric:tabular-nums;font-weight:600;font-size:13px')}>${i.price}</div></div>
                <Sizes c={c} i={i} narrow />
                <button onClick={() => A.openProduct(i)} style={sx('align-self:flex-start;border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:2px 0;font-size:12px;white-space:nowrap')}>Open at {MERCHANT} ↗</button>
              </div>))}
            {shop.owned.map((o) => <div key={o.slot} style={sx('border:1px dashed #837d73;border-radius:6px;padding:8px;display:flex;gap:7px;align-items:flex-start;font-size:12.5px')}><span style={sx(`width:16px;height:22px;border-radius:3px;background:${o.color};flex:none;border:1px solid #d6d3cc`)} /><span style={sx('flex:1;line-height:1.3')}><strong>{label(o)}</strong><br /><span style={sx('color:#55534d')}>Yours · not in what you buy</span></span><span style={sx('font-variant-numeric:tabular-nums;color:#55534d')}>$0</span></div>)}
            <div style={sx('border-top:1px solid #151514;padding-top:6px;display:flex;justify-content:space-between;font-weight:700;font-size:13.5px')}><span>Subtotal</span><span style={sx('font-variant-numeric:tabular-nums')}>${shop.sub}</span></div>
            <div style={sx('font-size:11.5px;color:#55534d;line-height:1.35')}>{shop.budgetText}</div>
          </section>
        </div>}
        {vers.length > 1 && <div role="group" aria-label="Look versions" style={sx('display:flex;gap:6px;flex-wrap:wrap;align-items:center')}><span style={sx('font-size:12.5px;color:#55534d')}>Versions</span>
          {vers.map((x, i) => { const on = i === sv.cur;
            return <button key={x.versionId} onClick={() => A.version(i)} aria-pressed={on} style={sx(`border:${on ? '2px solid #151514' : '1px solid #837d73'};background:${on ? '#151514' : '#fcf9f2'};color:${on ? '#fcf9f2' : '#151514'};border-radius:4px;padding:7px 12px;min-height:38px;font-size:13px;cursor:pointer;white-space:nowrap`)}>{(on ? '✓ ' : '') + x.label}</button>; })}</div>}
        {lines.length > 0 && <div style={sx('display:flex;flex-direction:column;gap:3px;font-size:13px;line-height:1.45')}>{lines.map((t) => <div key={t}>{t}</div>)}</div>}
        {retryOn && <button onClick={() => A.retry(pic.job)} style={sx('align-self:flex-start;border:1px solid #151514;background:#fcf9f2;border-radius:6px;padding:9px 14px;min-height:40px;cursor:pointer;font-size:13px;white-space:nowrap')}>{pic.partial ? 'Try adding the necklace again' : 'Try the picture again'}</button>}
        <div style={sx('background:#fcf9f2;border:1px solid #ddd4c4;border-radius:6px;padding:12px;display:flex;flex-direction:column;gap:8px')}>
          <div style={sx('font-size:13.5px;font-weight:600')}>Want to change anything?</div>
          <div style={sx('display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px')}>{(dress ? ['Darker dress', 'Longer dress', 'Shorter dress'] : ['Darker trousers', 'Slimmer leg', 'Longer sleeves']).map((q) =>
            <button key={q} onClick={() => A.ask(q)} disabled={asking} style={sx('border:1px solid #837d73;background:#f5f0e6;border-radius:4px;padding:7px 4px;min-height:40px;cursor:pointer;font-size:12.5px;line-height:1.2;text-align:center')}>{q}</button>)}</div>
          <div style={sx('display:flex;gap:6px;align-items:center')}>
            <button onClick={() => { if (!mic.on && !mic.busy) askBefore.current = ui.askText; mic.toggle(); }} aria-pressed={mic.on} style={sx(`display:flex;align-items:center;gap:6px;border:1px solid #151514;background:${mic.on ? '#151514' : '#fcf9f2'};color:${mic.on ? '#fcf9f2' : '#151514'};border-radius:999px;padding:9px 12px;min-height:40px;cursor:pointer;font-size:13px;white-space:nowrap`)}><MicIcon w={12} h={16} />{mic.busy ? 'Finishing…' : mic.on ? `Stop · 0:${String(mic.secs).padStart(2, '0')}` : 'Speak'}</button>
            {mic.on && <button onClick={mic.cancel} aria-label="Cancel recording" style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:9px 2px;font-size:12.5px')}>Cancel</button>}
            <input ref={askRef} value={ui.askText} onChange={(e) => A.set({ askText: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter') A.ask(ui.askText); }} aria-label="Ask for a change" placeholder="Or say it: “darker trousers”" style={sx('flex:1;min-width:0;border:1px solid #837d73;background:#fffdf8;border-radius:6px;padding:10px 12px;font-size:14px')} />
            <button onClick={() => A.ask(ui.askText)} disabled={asking || mic.on || mic.busy} style={sx('background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:10px 14px;min-height:40px;cursor:pointer;font-size:13px;font-weight:600;white-space:nowrap')}>{asking ? 'Checking…' : 'Go'}</button>
          </div>
          <div style={sx('font-size:12px;color:#55534d')}>I'll change only what you ask for and keep the rest.</div>
        </div>
        {!wide && <div style={sx('font-size:11.5px;color:#55534d;line-height:1.45')}>{shop.note}</div>}
      </div>
      {wide && <section aria-label="The pieces" style={sx('background:#fcf9f2;border:1px solid #ddd4c4;border-radius:6px;padding:14px;display:flex;flex-direction:column;gap:10px;min-width:0')}>
        <div style={sx('display:flex;justify-content:space-between;align-items:baseline')}><h2 style={sx('margin:0;font-weight:600;letter-spacing:-0.025em;font-size:17px')}>The pieces · {MERCHANT}</h2><div style={sx('font-size:12.5px;color:#55534d')}>{shop.n} to buy</div></div>
        {pieces}{ownedRows}
        <div style={sx('border-top:1px solid #151514;padding-top:9px;display:flex;justify-content:space-between;font-weight:700;font-size:15px')}><span>Merchandise subtotal</span><span style={sx('font-variant-numeric:tabular-nums')}>${shop.sub}</span></div>
        <div style={sx('font-size:12.5px;color:#55534d;margin-top:-5px')}>{shop.budgetText}. Tax and shipping set at checkout on {MERCHANT}.</div>
        <button onClick={() => A.sendBag(shop.allSized)} disabled={!shop.allSized || ui.pend === 'bag'} style={sx(`background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:13px;min-height:48px;cursor:pointer;font-weight:600;font-size:14.5px;opacity:${shop.allSized ? 1 : 0.4}`)}>{shop.text}</button>
        <div style={sx('font-size:12px;color:#55534d;line-height:1.5')}>{shop.note}</div>
      </section>}
    </div>
  </>;
}

export function PhoneBag({ c }) {
  const shop = shopOf(c);
  return (
    <div style={sx('flex:none;background:#fcf9f2;border-top:1px solid #151514;padding:10px 16px 14px;display:flex;gap:12px;align-items:center')}>
      <div style={sx('flex:none')}><div style={sx('font-weight:700;font-variant-numeric:tabular-nums;font-size:16px')}>${shop.sub}</div><div style={sx('font-size:11.5px;color:#55534d')}>before tax &amp; shipping</div></div>
      <button onClick={() => c.A.sendBag(shop.allSized)} disabled={!shop.allSized || c.ui.pend === 'bag'} style={sx(`flex:1;background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:13px 10px;min-height:48px;cursor:pointer;font-weight:600;opacity:${shop.allSized ? 1 : 0.4}`)}>{shop.short}</button>
    </div>
  );
}
