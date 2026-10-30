import { useEffect, useRef, useState } from 'react';
import { sx } from './sx.js';
import { Sheet, Wordmark, Mark } from './parts.jsx';
import { Start } from './Start.jsx';
import { Palette, Why, Stage, Winner, PhoneBag } from './screens.jsx';
import { MERCHANT, SLOT_LABEL, picture, running } from './model.js';

const UI = { intro: null, note: null, pend: null, busy: null, issueKept: false, topCats: null, sheet: null, prices: null, piece: null, plist: null,
  why: {}, whyText: '', whyTyping: false, askText: '', sizeOpen: {}, opened: {}, cart: {}, pins: {}, showAlt: null,
  brief: { occasion: '', budget: null, approx: false, heels: false, dress: '', structure: 'either', notes: [], text: '' } };
const SAVED = 'calliope-ui-v2';
const SHARE_TEXT = 'I would love some feedback on my outfit';
const TOPS = ['blouse', 'shirt', 't-shirt', 'knit top', 'camisole'];
const PROGRESS = { round1: 'Round 1 of 2', round2: 'Round 2 of 2', why: 'What to carry', final: 'Final outfits', revise: 'New version' };

// First-run catalog setup: what is happening, how far along, and a retry when it failed (the server keeps any earlier index).
const PHASE = { start: 'Starting', enumerate: 'Listing the Livostyle catalog through its MCP', details: 'Reading product details through the MCP',
  supplement: 'Reading product IDs and supplier details', classify: 'Classifying products (colour, pattern, formality)', failed: 'Setup stopped' };
function CatalogSetup({ cat, onRetry }) {
  const pct = cat.total ? Math.round((100 * (cat.done || 0)) / cat.total) : 0, failed = cat.state === 'failed';
  return <div role={failed ? 'alert' : 'status'} aria-live="polite" style={sx(`flex:none;background:#fcf9f2;border-bottom:1.5px solid ${failed ? '#b42318' : '#151514'};padding:10px 16px;font-size:13px;display:flex;flex-direction:column;gap:6px`)}>
    <div style={sx('display:flex;gap:10px;align-items:center;flex-wrap:wrap')}>
      <strong style={sx(failed ? 'color:#b42318' : '')}>{failed ? 'The catalog couldn’t be set up.' : 'Setting up the Livostyle catalog'}</strong>
      <span style={sx('color:#55534d')}>{failed ? cat.error || cat.message : (PHASE[cat.phase] || 'Starting') + (cat.total ? ` · ${cat.done || 0} of ${cat.total}` : '')}</span>
      {failed && cat.retry && <button onClick={onRetry} style={sx('margin-left:auto;background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:8px 14px;min-height:38px;cursor:pointer;font-weight:600;font-size:13px')}>Try again</button>}
    </div>
    {!failed && <div aria-hidden="true" style={sx('height:4px;background:#ddd4c4;border-radius:2px;overflow:hidden')}><div style={sx(`height:100%;width:${Math.max(4, pct)}%;background:#151514;transition:width .4s`)} /></div>}
  </div>;
}

export function App({ api }) {
  const [sv, setSv] = useState(null);
  const [fatal, setFatal] = useState(null);
  const [ui, setUi] = useState(UI);
  const [seed, setSeed] = useState(0);
  const [vw, setVw] = useState(window.innerWidth);
  const [tick, setTick] = useState(0);
  const epoch = useRef(0), pend = useRef(null), hold = useRef(0), noteT = useRef(null), mainRef = useRef(null), introT = useRef({});
  const shareFile = useRef(null);
  const go = (w, url) => { if (w && !w.closed) { w.opener = null; w.location.href = url; } else window.open(url, '_blank', 'noopener'); };
  const set = (patch) => setUi((u) => ({ ...u, ...(typeof patch === 'function' ? patch(u) : patch) }));
  const flash = (note) => { clearTimeout(noteT.current); set({ note }); if (note) noteT.current = setTimeout(() => set({ note: null }), 3200); };
  const err = (e) => flash(e.message || String(e));
  // A choice stays on screen before the next screen (contract §2.6: 650 ms, 200 ms with reduced motion).
  const holdFrom = (t0) => new Promise((r) => setTimeout(r, Math.max(0, (reduced ? 200 : 650) - (Date.now() - t0))));
  const reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  // Final intro (contract §4.6 F1): before the final outfits, what they were built from ticks on one by one. Only real values: the stated preferences (or the starting look after Skip), the shopper's own
  // top, the brief's rules and the budget.
  const endIntro = () => { const t = introT.current; clearInterval(t.i); clearTimeout(t.h); set({ intro: null }); };
  const startIntro = (v) => {
    const jr = v.journey, c = jr.carry || {}, b = v.brief || {}, o = v.owned;
    const items = [...((c.learned || []).length ? c.learned.map((h) => h.value) : ['Look ' + jr.win.r2 + ' as your starting point']),
      ...(b.ownedTopUsed && o ? ['Your ' + o.label.toLowerCase()] : []), ...(b.constraints || []).filter((x) => !/unisex/i.test(x)),
      b.budgetNew == null ? 'No budget limit' : (b.budgetMode === 'approx10' ? 'About $' : 'Under $') + b.budgetNew];
    const t = introT.current, gap = reduced ? 120 : 520, keep = reduced ? 500 : 900;
    clearInterval(t.i); clearTimeout(t.h); let n = 0;
    set({ intro: { items, n: 0 } });
    t.i = setInterval(() => { n += 1; set((u) => (u.intro ? { intro: { ...u.intro, n } } : {}));
      if (n >= items.length) { clearInterval(t.i); t.h = setTimeout(endIntro, keep); } }, gap);
  };
  useEffect(() => () => { clearInterval(introT.current.i); clearTimeout(introT.current.h); }, []);

  // ---------- session: boot, refresh, polling ----------
  const begin = (v, saved) => { epoch.current++; setSv(v); setUi({ ...UI, ...(saved || {}), pins: { ...((v.journey && v.journey.pins) || {}) } }); setSeed((n) => n + 1); };
  useEffect(() => {
    const fresh = new URLSearchParams(location.search).get('new') === '1';
    if (fresh) history.replaceState(null, '', location.pathname);
    api.boot(fresh).then((v) => {
      let saved = null;
      try { const s = JSON.parse(localStorage.getItem(SAVED) || 'null'); if (s && s.sessionId === v.sessionId) saved = { why: s.why || {}, opened: s.opened || {}, cart: s.cart || {} }; } catch (e) { /* none */ }
      begin(v, saved);
    }).catch((e) => setFatal(e));
    const onR = () => setVw(window.innerWidth); window.addEventListener('resize', onR);
    return () => window.removeEventListener('resize', onR);
  }, []);
  useEffect(() => {
    if (!sv) return;
    try { localStorage.setItem(SAVED, JSON.stringify({ sessionId: sv.sessionId, why: ui.why, opened: ui.opened, cart: ui.cart })); } catch (e) { /* convenience only */ }
  }, [sv && sv.sessionId, ui.why, ui.opened, ui.cart]);
  const refresh = () => { const e = epoch.current; return api.session().then((v) => { if (e === epoch.current) setSv(v); return v; }); };
  useEffect(() => {                                   // follow running work (color reading, pictures) until it settles
    if (!running(sv) && !settingUp) return undefined;
    // Not while an action is in flight: its own answer moves the screen on. A poll landing in a choice's hold would show the
    // next screen while the action is still pending, and a tap or Enter there would be dropped as "busy".
    const t = setTimeout(() => (pend.current ? Promise.resolve(setTick((n) => n + 1)) : refresh()).catch(() => setTimeout(() => setTick((n) => n + 1), 3000)), api.pollMs);
    return () => clearTimeout(t);
  }, [sv, tick]);
  // One action at a time; a repeated tap while one is in flight does nothing (the server is idempotent as well).
  const act = async (key, fn, ok, fail) => {
    if (pend.current) return;
    pend.current = key; set({ pend: key }); epoch.current++;
    try { const r = await fn(); epoch.current++; await (ok && ok(r)); } catch (e) { (fail || err)(e); } finally { pend.current = null; set({ pend: null }); }
  };
  const view = (v) => { epoch.current++; setSv(v); };

  // ---------- derived ----------
  const jr = sv && sv.journey, vers = (sv && sv.versions) || [];
  const rev = sv && sv.activeRevision;
  const step = !sv ? null : !jr ? 'start' : vers.length ? (rev ? 'revise' : 'look') : jr.step;
  useEffect(() => { if (mainRef.current) mainRef.current.scrollTop = 0; }, [step]);
  const wide = vw >= 900;
  const budget = sv && sv.brief ? sv.brief.budgetNew : null;
  const owned = sv && sv.owned, garment = !!(owned && owned.confirmed);
  const cur = vers[sv ? sv.cur : 0];
  const status = (sv && sv.status) || {};
  const cat = status.catalog, settingUp = !!(cat && !cat.ready);          // first run: the catalog index is being built from the Livostyle MCP
  const unavailable = settingUp ? 'The Livostyle catalog is still being set up, so styling can’t start yet. Nothing has been charged.'
    : status.available === false ? 'Try-on pictures can’t be made right now, so nothing has been started or charged. ' + (status.action || '') : '';

  // ---------- actions ----------
  const A = {
    set, flash, err, refresh, act, view,
    photo: (replace) => {
      if (sv.photo && !replace) return api.removePhoto().then(refresh).catch(err);
      api.pickFile().then((file) => {
        if (!file) return null;
        set({ busy: 'photo', issueKept: false });
        return (replace && sv.photo ? api.removePhoto() : Promise.resolve()).then(() => api.uploadPhoto(file))
          .then((r) => { set({ busy: null }); if (!r.ok) return flash(r.message); return refresh(); });
      }).catch((e) => { set({ busy: null }); flash('Couldn’t add your photo: ' + e.message); });
    },
    garment: (hint) => api.pickFile().then((file) => {
      if (!file) return null;
      set({ busy: 'top' });
      return api.uploadTop(file, typeof hint === 'string' ? hint : '').then((r) => { set({ busy: null }); if (!r.ok) return flash(r.message); set({ topCats: r.categories });
        return refresh(); });
    }).catch((e) => { set({ busy: null }); flash('Couldn’t add your top: ' + e.message); }),
    ownedConfirm: (cat, color) => api.confirmTop(cat, color).then(refresh).catch(err),
    ownedRemove: () => api.removeTop().then(refresh).catch(err),
    applyBrief: (o) => set((u) => ({ brief: { ...u.brief, occasion: o.occasion || u.brief.occasion, budget: o.budget === undefined ? u.brief.budget : o.budget, approx: !!o.approx,
      heels: !!o.heels, dress: o.dress || '', structure: o.structure || 'either', notes: o.notes || [], text: o.text || '' } })),
    begin: () => { const b = ui.brief;
      act('start', () => api.journeyStart({ occasion: b.occasion, budget: b.budget ?? 0, approx: !!b.approx, heels: !!b.heels, dress: b.dress || '', structure: b.structure || 'either', notes: b.notes || [], text: b.text || '' }),
        view,                                               // what wasn't used stays listed in the brief panel
        (e) => flash(e.code === 'budget_required' ? 'Set a budget, or choose No limit.' : 'Couldn’t start: ' + e.message)); },
    palette: (name) => act('palette', () => api.journeyPalette(name), view),
    keep: (key, id) => {
      if (pend.current || Date.now() < hold.current) return;      // a double tap can't also decide the next screen
      const t0 = Date.now();
      act({ key, id }, () => (key === 'fin' ? api.journeyChoose(id) : api.journeyKeep(key === 'r1' ? 1 : 2, id, ui.pins)), async (v) => {
        await holdFrom(t0); hold.current = Date.now() + 600; view(v); set({ sheet: null });
      });
    },
    pin: (item) => set((u) => { const p = { ...u.pins }; if (p[item.slot] === item.key) delete p[item.slot]; else p[item.slot] = item.key; return { pins: p }; }),
    finalize: (keys, text) => act('finalize', () => api.journeyFinalize(keys, text), (v) => { view(v); set({ whyText: '', whyTyping: false }); startIntro(v); }),
    size: (slot, z) => api.size(slot, z).then(refresh).catch(err),
    version: (i) => api.version(i).then(view).catch(err),
    ask: (text) => { const t = (text || '').trim(); if (!t || !cur) return;
      act('ask', () => api.ask(t, cur.versionId), () => { set({ askText: '', showAlt: null }); return refresh(); }, (e) => flash('Couldn’t check that: ' + e.message)); },
    answer: (choice) => act('answer', () => api.answer(rev.revisionId, choice), refresh),
    showAlt: () => set({ showAlt: rev.revisionId }),
    accept: (cid) => { const t0 = Date.now(); act({ key: 'accept', id: cid }, () => api.accept(rev.revisionId, cid), async (v) => { await holdFrom(t0); view(v); set({ showAlt: null, sizeOpen: {} }); },
      (e) => flash('Couldn’t keep that: ' + e.message)); },
    back: () => { const t0 = Date.now(); act('dismiss', () => api.dismiss(rev.revisionId), async (v) => { await holdFrom(t0); view(v); }); },
    retry: (job, ack) => { if (!job) return;
      api.retry(job.jobId, ack).then(refresh).catch((e) => {
        if (e.code === 'needs_ack') { if (window.confirm('The picture service didn’t confirm it got this picture, so it may already be charged. Try again anyway? It could be charged twice.')) A.retry(job, true); return; }
        flash('Couldn’t try again: ' + e.message); }); },
    // Share: the system share sheet (Messages, WhatsApp, X, Facebook, Mail… whatever the device offers) with the outfit
    // picture and one line; no link, prices or pieces. Without picture sharing, a small sheet to save it instead.
    share: () => {
      const f = shareFile.current, data = f && { files: [f], text: SHARE_TEXT };
      if (!f) return flash('The picture isn’t ready yet. Share it once it is.');
      if (navigator.canShare && navigator.canShare(data)) return navigator.share(data).catch(() => {});      // closing the sheet isn't an error
      set({ sheet: 'share' });
    },
    prepShare: (src) => {                   // fetched ahead of the tap: share() must run inside it
      if (!src || shareFile.current_src === src) return;
      shareFile.current_src = src; shareFile.current = null;
      fetch(src).then((r) => r.blob()).then(createImageBitmap).then((bm) => {      // a 1600px JPEG, not the multi-MB picture
        const k = Math.min(1, 1600 / Math.max(bm.width, bm.height)), cv = document.createElement('canvas');
        cv.width = Math.round(bm.width * k); cv.height = Math.round(bm.height * k); cv.getContext('2d').drawImage(bm, 0, 0, cv.width, cv.height);
        cv.toBlob((bl) => { if (bl && shareFile.current_src === src) shareFile.current = new File([bl], 'my-outfit.jpg', { type: 'image/jpeg' }); }, 'image/jpeg', 0.88);
      }).catch(() => {});
    },
    // A tab is opened inside the tap (so it isn't blocked) and sent to the merchant page once the server has the link.
    openProduct: (item) => { const w = window.open('', '_blank');
      api.productLink(cur.versionId, item).then((url) => { set((u) => ({ opened: { ...u.opened, [item.slot]: true } })); go(w, url); }).catch((e) => { if (w) w.close(); err(e); }); },
    // The bag: a tab is opened inside the tap; the server checks live variants and prices. Changed prices (or a total over
    // the budget) are shown first and need her explicit OK, bound to exactly those prices and pieces (h.quote).
    sendBag: (allSized, confirm) => { if (!allSized || pend.current) return;
      const w = window.open('', '_blank');
      act('bag', () => api.handoff(cur.versionId, confirm), (h) => {
        if (h.cartUrl && h.agree) { set((u) => ({ cart: { ...u.cart, [cur.versionId]: true }, sheet: null, prices: null })); go(w, h.cartUrl); return refresh(); }
        if (w) w.close();
        if (h.needsConfirmation || h.budgetConflict) return set({ sheet: 'prices', prices: h });
        flash('Couldn’t send to the bag: ' + ([...h.problems, ...h.unavailable.map((x) => x + ' unavailable')].join('; ') || 'pieces don’t match this picture'));
      }, (e) => { if (w) w.close(); err(e); }); },
    startOver: (ask = true) => { if (ask && !window.confirm('Start over with a new photo and brief? This look stays saved for 7 days but won’t be shown here again.')) return;
      api.newSession().then((v) => { begin(v); window.scrollTo(0, 0); }).catch(err); },
    voiceError: (m) => flash(m),
  };

  if (fatal) return <Shell><div role="alert" style={sx('padding:28px 18px;max-width:560px;margin:0 auto;font-size:15px;line-height:1.5')}><strong>Calliope couldn’t start.</strong> {fatal.message} Reload the page to try again.</div></Shell>;
  if (!sv) return <Shell />;

  const ctx = { api, sv, ui, A, wide, budget, owned, garment, cur, step, rev, reduced };
  const sheetTitle = ui.piece ? ui.piece.item.slotLabel || SLOT_LABEL[ui.piece.item.slot] : ui.plist ? ui.plist.title : { brief: 'Your details', share: 'Share your outfit', prices: 'Prices changed at Livostyle' }[ui.sheet] || '';
  const closeAll = () => { if (ui.piece || ui.plist) return set({ piece: null, plist: null }); return set({ sheet: null }); };
  const sheetOpen = !!(ui.piece || ui.plist || ui.sheet);
  const centered = ui.sheet === 'share' && !ui.piece && !ui.plist;
  const pal = jr && jr.palette;

  return (
    <div data-reduced={reduced ? '1' : '0'} style={sx("height:100vh;height:100dvh;display:flex;flex-direction:column;background:#d6d2c9;font-family:'Schibsted Grotesk',sans-serif;color:#151514")}>
      <div style={sx('flex:1;min-height:0;display:flex;justify-content:center;align-items:center')}>
        <div style={sx('width:100%;height:100%;max-width:100%;display:flex;flex-direction:column;background:#efe8db;position:relative;overflow:hidden;font-size:14px')}>
          {status.simulated && <div role="note" style={sx("flex:none;background:#8a5800;color:#fcf9f2;padding:4px 12px;font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.05em")}>DEVELOPMENT STUB · PICTURES AND COLOR READINGS ARE SIMULATED</div>}
          <header style={sx('flex:none;background:#fcf9f2;border-bottom:1px solid #ddd4c4;padding:10px 16px;display:flex;align-items:center;gap:8px;flex-wrap:wrap')}>
            <button onClick={() => A.startOver(step !== 'start')} aria-label="Calliope: start a new session" style={sx('border:none;background:none;padding:0;cursor:pointer;display:flex')}><Wordmark /></button>
            <span style={sx('flex:1')} />
            {PROGRESS[step] && <span style={sx('font-size:12.5px;color:#55534d;white-space:nowrap')}>{PROGRESS[step]}</span>}
            {step !== 'start' && <button onClick={() => set({ sheet: 'brief' })} aria-label="Your occasion, budget and colors" style={sx('display:flex;align-items:center;gap:6px;border:1px solid #cdc4b4;background:#efe8db;border-radius:4px;padding:7px 10px;min-height:36px;font-size:12.5px;cursor:pointer;white-space:nowrap')}>
              {pal && <span style={sx('display:flex')}>{pal.swatches.slice(0, 4).map((c, n) => <span key={n} style={sx(`width:5px;height:12px;background:${c}`)} />)}</span>}Details</button>}
          </header>
          {settingUp && <CatalogSetup cat={cat} onRetry={() => api.catalogRetry().then(refresh).catch(err)} />}
          {ui.note && <div role="status" style={sx('flex:none;background:#151514;color:#fcf9f2;padding:8px 16px;font-size:13px;display:flex;gap:12px;align-items:center')}><span style={sx('flex:1')}>{ui.note}</span></div>}
          {unavailable && step !== 'start' && <div role="alert" style={sx('flex:none;background:#fcf9f2;border-bottom:1.5px solid #b42318;padding:8px 16px;font-size:13px')}><strong style={sx('color:#b42318')}>Calliope isn’t available right now.</strong> {unavailable}</div>}

          <main ref={mainRef} style={sx(`flex:1;min-height:0;overflow:auto;padding:${wide ? '20px 28px 28px' : '12px 12px 18px'}`)}>
            <div style={sx(`max-width:${wide ? '1180px' : '100%'};margin:0 auto;display:flex;flex-direction:column;gap:14px`)}>
              {step === 'start' && <Start key={seed} wide={wide} reduced={reduced} photo={!!sv.photo} photoUrl={sv.photo ? sv.photo.url : ''}
                photoIssue={!!(sv.photo && sv.photo.issue && !ui.issueKept)} issue={sv.photo && sv.photo.issue} onIssueKeep={() => set({ issueKept: true })}
                garment={garment} ownedLabel={garment ? owned.label : ''} ownedColor={owned ? owned.hex : ''} ownedImage={owned ? owned.url : ''}
                ownedPending={owned && !owned.confirmed ? { colorName: owned.colorName, category: owned.category, categories: ui.topCats || TOPS } : null}
                ownedNow={garment ? { colorName: owned.colorName, category: owned.category, categories: ui.topCats || TOPS } : null}
                onOwnedConfirm={A.ownedConfirm} onOwnedRemove={A.ownedRemove} onGarment={A.garment} onPhoto={() => A.photo()} onPhotoFix={() => A.photo(true)}
                occasion={ui.brief.occasion} budget={ui.brief.budget} noHeels={ui.brief.heels} busy={ui.busy || (ui.pend === 'start' ? 'start' : null)} unavailable={unavailable}
                onApply={A.applyBrief} onBegin={A.begin} onOccasion={(e) => set((u) => ({ brief: { ...u.brief, occasion: e.target.value } }))}
                onBudget={(e) => { const t = e.target.value.trim(); set((u) => ({ brief: { ...u.brief, budget: t === '' ? null : Math.max(0, Number(t) || 0) } })); }}
                onHeels={() => set((u) => ({ brief: { ...u.brief, heels: !u.brief.heels } }))} onVoiceError={A.voiceError} speech={status.speech} readBrief={api.readBrief} />}
              {step === 'palette' && <Palette c={ctx} />}
              {step === 'why' && <Why c={ctx} />}
              {['round1', 'round2', 'final', 'revise'].includes(step) && <Stage c={ctx} />}
              {step === 'look' && cur && <Winner c={ctx} />}
            </div>
          </main>

          {ui.intro && step === 'final' && <FinalIntro intro={ui.intro} onSkip={endIntro} />}
          {!wide && step === 'look' && cur && <PhoneBag c={ctx} />}

          {sheetOpen && <Sheet key={sheetTitle} title={sheetTitle} onClose={closeAll} centered={centered} wide={wide}>
            {ui.piece ? <PieceSheet c={ctx} /> : ui.plist ? <PiecesSheet plist={ui.plist} /> : ui.sheet === 'brief' ? <BriefSheet c={ctx} /> : ui.sheet === 'share' ? <ShareSheet c={ctx} /> : ui.sheet === 'prices' && ui.prices ? <PricesSheet c={ctx} /> : null}
          </Sheet>}
        </div>
      </div>
    </div>
  );
}

function FinalIntro({ intro, onSkip }) {
  const done = intro.n >= intro.items.length;
  return (
    <div role="status" aria-live="polite" style={sx('position:absolute;inset:0;z-index:7;background:#efe8db;display:flex;flex-direction:column;justify-content:center;padding:28px')}>
      <div style={sx('max-width:420px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:18px')}>
        <div style={sx('display:flex;gap:12px;align-items:center')}><Mark size={36} /><div style={sx('font-weight:600;letter-spacing:-0.025em;font-size:24px;line-height:1.2')}>{done ? 'Here are your final outfits.' : 'Building your final outfits…'}</div></div>
        <div style={sx('display:flex;flex-direction:column;gap:10px;padding-left:4px')}>
          {intro.items.map((text, k) => { const on = k < intro.n;
            return <div key={k} style={sx(`display:flex;gap:12px;align-items:center;opacity:${on ? 1 : 0.35};transform:translateY(${on ? '0' : '4px'});transition:opacity .35s ease,transform .35s ease`)}>
              <span aria-hidden="true" style={sx(`width:24px;height:24px;border-radius:50%;border:1.5px solid #151514;background:${on ? '#151514' : 'transparent'};color:#fcf9f2;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex:none;transition:background .25s`)}>{on ? '✓' : ''}</span>
              <span style={sx('font-size:17px;font-weight:500')}>{text}</span></div>; })}
        </div>
        <button onClick={onSkip} style={sx('align-self:flex-start;border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:8px 0;font-size:13.5px')}>Show them now</button>
      </div>
    </div>
  );
}

const Shell = ({ children }) => <div style={sx("min-height:100vh;background:#efe8db;font-family:'Schibsted Grotesk',sans-serif;color:#151514")}>{children}</div>;

function PieceSheet({ c }) {
  const { ui, A } = c, i = c.ui.piece.item, pinned = ui.pins[i.slot] === i.key;
  const canPin = c.ui.piece.ctx === 'round' && !i.owned;
  return <>
    <div style={sx('display:flex;gap:12px')}>
      <div style={sx(`width:48px;height:58px;border-radius:4px;background:${i.color};flex:none;border:1px solid #d6d3cc`)} />
      <div style={sx('flex:1')}><div style={sx('font-weight:600')}>{i.name}</div><div style={sx('font-size:13px;color:#55534d')}>{SLOT_LABEL[i.slot]} · {i.variant} · {i.owned ? 'yours' : MERCHANT}</div></div>
      <div style={sx('font-variant-numeric:tabular-nums;font-weight:600')}>{i.owned ? 'Yours' : '$' + i.price}</div>
    </div>
    {canPin && <button onClick={() => A.pin(i)} aria-pressed={pinned} style={sx(`background:${pinned ? '#151514' : '#fcf9f2'};color:${pinned ? '#fcf9f2' : '#151514'};border:1px solid #151514;border-radius:6px;padding:12px;min-height:44px;cursor:pointer;text-align:left;font-size:14px`)}>{pinned ? '✓ Fixed in every look · tap to undo' : 'Keep this piece in every look'}</button>}
    {i.owned && <div style={sx('font-size:13px;color:#55534d')}>Yours. Shown in the outfit, never added to what you buy.</div>}
    {!i.owned && <div style={sx('font-size:12.5px;color:#55534d')}>Size is chosen on your winner page.</div>}
  </>;
}

const PiecesSheet = ({ plist }) => <>
  {plist.items.map((i) => (
    <div key={i.slot} style={sx('display:flex;gap:10px;border-bottom:1px solid #ddd4c4;padding-bottom:9px')}>
      <div style={sx(`width:36px;height:44px;border-radius:4px;background:${i.color};flex:none;border:1px solid #d6d3cc`)} />
      <div style={sx('flex:1')}><div style={sx('font-weight:600')}>{i.name}</div><div style={sx('font-size:13px;color:#55534d')}>{SLOT_LABEL[i.slot]} · {i.variant}</div></div>
      <div style={sx('font-variant-numeric:tabular-nums;font-weight:600;font-size:13px')}>{i.owned ? 'Yours' : '$' + i.price}</div>
    </div>))}
  <div style={sx('font-size:12.5px;color:#55534d')}>{plist.price}</div>
</>;

function BriefSheet({ c }) {
  const { sv, A } = c, b = sv.brief, jr = sv.journey, pal = jr && jr.palette, name = jr && jr.paletteName;
  const lines = b ? [{ k: 'Occasion', v: b.occasion }, { k: 'Budget for new pieces', v: b.budgetNew == null ? 'No limit' : '$' + b.budgetNew + (b.budgetMode === 'approx10' ? ', give or take $10' : '') },
    { k: 'Rules', v: b.constraints.join(', ') }, ...(sv.owned ? [{ k: 'Your top', v: sv.owned.label }] : []), ...(sv.notUsed || []).map((n) => ({ k: 'Not used', v: n }))] : [];
  return <>
    <div style={sx('display:flex;flex-direction:column;gap:6px;font-size:14px;line-height:1.45')}>
      {lines.map((l, n) => <div key={n}><span style={sx('color:#55534d;font-size:12.5px')}>{l.k}</span><br />{l.v}</div>)}
      <div style={sx('font-size:12.5px;color:#55534d')}>These are fixed for this session. Start over to change them. <button onClick={A.startOver} style={sx('border:none;background:none;color:#0d6b66;text-decoration:underline;cursor:pointer;padding:0;font-size:12.5px')}>Start over</button></div>
    </div>
    {pal && name && name !== 'No palette' && <div style={sx('display:flex;gap:8px;align-items:center;font-size:12.5px;color:#55534d')}><span style={sx('display:flex')}>{pal.swatches.slice(0, 4).map((x, n) => <span key={n} style={sx(`width:6px;height:12px;background:${x}`)} />)}</span>Styling in {name}</div>}
  </>;
}

// Live prices differ from the ones shown (or the new total is over her budget): what changed, both totals, and an explicit choice.
function PricesSheet({ c }) {
  const { ui, A, cur } = c, h = ui.prices, bc = h.budgetConflict, money = (x) => '$' + x.toFixed(2), slot = (k) => SLOT_LABEL[k] || k;
  const raiseTo = bc ? Math.ceil(h.subtotalNow) : null, busy = ui.pend === 'bag', stale = h.versionId !== cur.versionId;
  return <>
    {h.confirmationExpired && <div role="alert" style={sx('border:1.5px solid #8a5800;border-radius:6px;padding:10px 12px;font-size:13.5px;line-height:1.45')}><strong style={sx('color:#8a5800')}>Something changed since you confirmed.</strong> These are the current prices and pieces; check them again.</div>}
    <div style={sx('display:flex;flex-direction:column;gap:8px;font-size:14px')}>
      {h.priceChanges.map((x) => <div key={x.slot} style={sx('display:flex;justify-content:space-between;gap:10px;border-bottom:1px solid #ddd4c4;padding-bottom:7px')}>
        <span><strong>{slot(x.slot)}</strong> · {x.variantTitle}</span>
        <span style={sx('font-variant-numeric:tabular-nums;white-space:nowrap')}><s style={sx('color:#55534d')}>{money(x.was)}</s> → <strong>{money(x.now)}</strong></span></div>)}
      <div style={sx('display:flex;justify-content:space-between;font-weight:700')}><span>Merchandise subtotal</span>
        <span style={sx('font-variant-numeric:tabular-nums')}><s style={sx('color:#55534d;font-weight:400')}>{money(h.subtotalShown)}</s> → {money(h.subtotalNow)} {h.currency}</span></div>
    </div>
    {bc && <div role="alert" style={sx('border:1.5px solid #b42318;border-radius:6px;padding:10px 12px;font-size:13.5px;line-height:1.45')}><strong style={sx('color:#b42318')}>That’s {money(bc.over)} over your budget</strong> (up to {money(bc.limit)}). Change a piece instead, or raise your budget to {money(raiseTo)}.</div>}
    <div style={sx('font-size:12.5px;color:#55534d;line-height:1.45')}>Prices as Livostyle shows them now, before tax and shipping. Livostyle sets the final price at checkout.</div>
    <div style={sx('display:flex;gap:8px;flex-wrap:wrap')}>
      <button disabled={busy || stale} onClick={() => A.sendBag(true, { acceptQuote: h.quote, ...(bc ? { raiseBudgetTo: raiseTo } : {}) })} style={sx('flex:1;background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:12px;min-height:46px;cursor:pointer;font-weight:600')}>
        {bc ? `Raise my budget to ${money(raiseTo)} and open the bag ↗` : 'Accept these prices and open the bag ↗'}</button>
      <button onClick={() => A.set({ sheet: null, prices: null })} style={sx('flex:1;border:1px solid #151514;background:#fcf9f2;border-radius:6px;padding:12px;min-height:46px;cursor:pointer')}>{bc ? 'Change a piece instead' : 'Not now'}</button>
    </div>
  </>;
}

function ShareSheet({ c }) {               // only where the browser can't share a picture to apps
  const { A, cur } = c, pic = picture(cur.previewJob, cur.compositionId), img = pic.status === 'ready' ? pic.image : '';
  return <>
    {img && <img src={img} alt="Your outfit" style={sx('width:100%;max-height:360px;object-fit:contain;background:#efe8db;border-radius:6px;display:block')} />}
    <div role="status" style={sx('border:1.5px solid #8a5800;border-radius:6px;padding:10px 12px;font-size:13.5px;line-height:1.45')}><span style={sx('font-weight:600;color:#8a5800')}>! This browser can’t share pictures to apps.</span> Save the picture and send it with: “{SHARE_TEXT}”</div>
    <div style={sx('display:flex;gap:8px;flex-wrap:wrap')}>
      <button onClick={() => { if (!img) return; const a = document.createElement('a'); a.href = img; a.download = 'my-outfit.jpg'; a.click(); }} style={sx('flex:1;background:#151514;color:#fcf9f2;border:none;border-radius:6px;padding:12px;min-height:46px;cursor:pointer;font-weight:600')}>Save picture</button>
      <button onClick={() => (navigator.clipboard ? navigator.clipboard.writeText(SHARE_TEXT) : Promise.reject()).then(() => A.flash('Copied.'), () => window.prompt('Copy this', SHARE_TEXT))} style={sx('flex:1;border:1px solid #151514;background:#fcf9f2;border-radius:6px;padding:12px;min-height:46px;cursor:pointer')}>Copy text</button>
    </div>
  </>;
}

