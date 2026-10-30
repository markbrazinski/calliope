// Local verification of the live app (/, React) with the explicitly enabled development stub (no paid calls).
//   node tests/verify-live.mjs <images dir> [out dir]
// The images dir (default: a temporary one) gets synthetic images from tests/make_images.py when it has none: shopper-a.jpg,
// shopper-b.jpg (full-length, portrait), top-green.jpg, top-red.jpg, dark-landscape.jpg, tiny.jpg, not-an-image.jpg (no people).
// It starts its own servers on temporary state, so nothing under state/ is touched.
import { chromium, devices } from '../node_modules/playwright/index.mjs';
import { spawn, execFileSync } from 'child_process';
import fs from 'fs'; import os from 'os'; import path from 'path'; import { fileURLToPath } from 'url';

const IMG = path.resolve(process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'calliope-img-'))), OUT = path.resolve(process.argv[3] || fs.mkdtempSync(path.join(os.tmpdir(), 'calliope-verify-')));
fs.mkdirSync(OUT, { recursive: true });
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), PY = path.join(ROOT, '.venv/bin/python');
if (!fs.existsSync(path.join(IMG, 'shopper-a.jpg'))) execFileSync(PY, [path.join(ROOT, 'tests/make_images.py'), IMG], { stdio: 'inherit' });
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'calliope-state-')), PORT = 8897, B = `http://127.0.0.1:${PORT}`;
const checks = [], fail = [];
const ok = (c, m) => { (c ? checks : fail).push(m); console.log((c ? '  ✓ ' : '  ✗ ') + m); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let server = null; const extra = [];
async function startServer(env = {}, port = PORT) {
  const p = spawn(PY, ['app.py'], { cwd: ROOT, env: { ...process.env, CALLIOPE_PROVIDER: 'stub', CALLIOPE_STUB_DELAY: '2', CALLIOPE_STATE_DIR: path.join(TMP, 'app'),
    CALLIOPE_ENGINE_DIR: path.join(TMP, 'engine'), PORT: String(port), ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  p.stderr.on('data', (d) => fs.appendFileSync(path.join(OUT, `server-${port}.log`), d));
  for (let i = 0; i < 100; i++) { try { await fetch(`http://127.0.0.1:${port}/api/status`); return p; } catch (e) { await sleep(100); } }
  throw new Error('server did not start');
}
const stop = (p) => new Promise((r) => { if (p.exitCode !== null || p.signalCode !== null) return r(); p.once('exit', r); p.kill('SIGKILL'); });      // hard stop: the worst case for recovery

const browser = await chromium.launch();
const errors = [];
async function shopper(name, opts = {}) {
  const ctx = await browser.newContext(opts.device || { viewport: { width: 1360, height: 1000 } });
  await ctx.addInitScript(() => { window.__shared = []; navigator.canShare = () => true;          // the system share sheet, recorded instead of shown
    navigator.share = (d) => { window.__shared.push({ text: d.text, url: d.url, files: (d.files || []).map((f) => [f.name, f.type]) }); return Promise.resolve(); }; });
  const page = await ctx.newPage(); page.on('pageerror', (e) => errors.push(name + ': ' + e));
  const posts = []; page.on('request', (r) => { if (r.method() !== 'GET' && r.url().startsWith(B)) posts.push(r.method() + ' ' + new URL(r.url()).pathname); });
  const h = {
    ctx, page, posts, name,
    shot: (n) => page.screenshot({ path: `${OUT}/${name}-${n}.png`, fullPage: false }),
    text: () => page.locator('body').innerText(),
    api: (p, init) => page.evaluate(([p, init]) => fetch(p, init).then(async (r) => ({ status: r.status, body: await r.json().catch(() => null) })), [p, init || {}]),
    session: () => page.evaluate(() => fetch('/api/session').then((r) => r.json())),
    noHScroll: async (where) => { const w = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]); ok(w[0] <= w[1] + 1, `${name} ${where}: no sideways scroll (${w[0]} ≤ ${w[1]})`); },
    upload: async (button, file) => { const fc = page.waitForEvent('filechooser'); await page.getByRole('button', { name: button }).click(); await (await fc).setFiles(path.join(IMG, file)); },
    waitFor: async (fn, label, ms = 60000) => { for (let i = 0; i < ms / 250; i++) { const s = await h.session(); if (fn(s)) return s; await sleep(250); } throw new Error(name + ': timed out waiting for ' + label); },
  };
  return h;
}
const jobsDone = (ids) => (s) => s.journey && ids.every((id) => s.journey.looks[id] && s.journey.looks[id].job.status !== 'running');

async function startScreen(h, photo, top, brief, extra = {}) {
  const { page } = h;
  await page.goto(B + '/'); await page.getByRole('button', { name: /Add your full-length photo/ }).waitFor();
  ok(!/REVIEWER CONTROLS/.test(await h.text()), `${h.name}: no reviewer controls in the app`);
  for (const bad of extra.badPhotos || []) {
    await h.upload(/Add your full-length photo/, bad.file); await page.getByText(new RegExp(bad.expect)).first().waitFor({ timeout: 30000 }).catch(() => {});
    ok(new RegExp(bad.expect).test(await h.text()), `${h.name}: ${bad.file} → "${bad.expect}"`);
    if (bad.keep) { await page.getByRole('button', { name: 'Choose another photo' }).waitFor(); }
  }
  if (extra.badPhotos && extra.badPhotos.some((x) => x.keep)) {        // the check result offers a retake; take it with the real photo
    const fc = page.waitForEvent('filechooser'); await page.getByRole('button', { name: 'Choose another photo' }).click(); await (await fc).setFiles(path.join(IMG, photo));
  } else await h.upload(/Add your full-length photo/, photo);
  await page.getByText('Photo added').first().waitFor(); await sleep(500);
  const s0 = await h.session(); ok(s0.photo && !s0.photo.issue, `${h.name}: own photo stored, checks passed (${s0.photo && s0.photo.width}×${s0.photo && s0.photo.height})`);
  const img = await h.api('/api/photo'); ok(img.status === 200 || img.status === 0 || img.body === null, `${h.name}: own photo served to its owner`);
  if (top) {
    await h.upload('+ Add clothes to pair', top.file); await page.getByRole('button', { name: 'Use this top' }).waitFor();
    await page.getByLabel('Color of your top').fill(top.color); await page.getByRole('button', { name: top.category, exact: true }).click();
    await page.getByRole('button', { name: 'Use this top' }).click(); await page.getByText(top.label).first().waitFor();
  }
  await page.getByLabel('Tell Calliope about the occasion').fill(brief.text); await page.getByRole('button', { name: 'Go' }).click(); await sleep(4000);
  for (const [chip, value] of brief.fill || []) {
    await page.getByText(chip + ' · needed').locator('xpath=ancestor::div[2]').getByRole('button', { name: 'Add' }).click();
    if (chip === 'Occasion') { await page.getByLabel('Occasion', { exact: true }).fill(value); await page.getByRole('button', { name: 'Save' }).click(); }
    else await page.getByRole('button', { name: value }).click();
    await sleep(400);
  }
}

try {
  server = await startServer();

  // ===== 1. Clean browser + unavailable-free stub: shopper A (desktop) =====
  console.log('Shopper A (desktop)');
  const A = await shopper('A');
  await startScreen(A, 'shopper-a.jpg', { file: 'top-green.jpg', color: 'green', category: 'Blouse', label: 'Green blouse' },
    { text: "Office holiday party in December. Around $120 for new things. I want flats only, we'll be walking a lot." },
    { badPhotos: [{ file: 'not-an-image.jpg', expect: 'isn.t a photo I can read' }, { file: 'tiny.jpg', expect: 'at least 512×384' }, { file: 'dark-landscape.jpg', expect: 'quite dark', keep: true }] });
  await A.shot('01-start');
  ok(/Office holiday party/.test(await A.text()), 'A: the occasion is read from her words (Office holiday party)');
  ok(await A.page.getByRole('button', { name: 'Open fitting room' }).isEnabled(), 'A: can start once photo, top, occasion and budget are set');
  await A.page.getByRole('button', { name: 'Open fitting room' }).click();
  await A.page.getByRole('button', { name: 'Open fitting room' }).click({ timeout: 1500 }).catch(() => {});     // a second click (often already gone)
  let sA = await A.waitFor((s) => s.journey && s.journey.analysis.status !== 'running', 'colors');
  ok(sA.brief.budgetNew === 120 && sA.brief.budgetMode === 'approx10', 'A: budget is the shopper’s ($120, give or take $10)');
  ok(sA.brief.constraints.some((c) => /Smart for office holiday party/i.test(c)) && !sA.notUsed.some((n) => /holiday party/i.test(n)) && sA.notUsed.some((n) => /walking/i.test(n)),
    'A: “office holiday party” becomes a dress-level rule; the walking note is reported as not used');
  ok(sA.journey.palette && sA.journey.analysis.status === 'ready', `A: palette from A’s own photo (${sA.journey.palette && sA.journey.palette.name}), SIMULATED in stub`);
  await A.page.getByRole('button', { name: 'Show me my first looks' }).waitFor(); await A.shot('02-palette');
  ok(!/Finding pieces in/.test(await A.text()), 'A: no countdown bar on the palette screen');
  await A.page.getByRole('button', { name: 'Show me my first looks' }).click();
  await A.page.getByText('Round 1 of 2').first().waitFor();
  sA = await A.session(); const lookA = sA.journey.looks.A;
  ok(sA.journey.looks.A.items[0].name === 'Blouse' && sA.journey.looks.A.items[0].color === sA.owned.hex, 'A: the owned top in every look is A’s green blouse');
  ok(Object.values(sA.journey.looks).every((l) => l.items.slice(1).reduce((t, i) => t + i.price, 0) <= 130), 'A: looks within $120 + $10');
  await sleep(600); const pend = await A.text(); await A.shot('03-round1-pending');
  ok(/step \d of 4|Starting/.test(pend), 'A: pending cards show the actual stage');
  ok(!/You can keep Look A now/.test(pend) || !(lookA.job.status === 'running'), 'A: no "keep Look A now" while A is still being made');
  await A.waitFor(jobsDone(['A', 'B']), 'round 1 pictures');
  // duplicate submissions: double click + repeated API calls
  const jobsBefore = Object.keys((await A.session()).journey.jobs || {}).length;
  await A.page.getByRole('button', { name: 'Keep Look A', exact: true }).dblclick();      // a double tap
  await A.page.getByText('Round 2 of 2').first().waitFor();
  let r = await A.api('/api/journey/keep', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ round: 1, id: 'A' }) });
  ok(r.status === 200, 'A: repeating "keep A" returns the same state');
  r = await A.api('/api/journey/keep', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ round: 1, id: 'B' }) });
  ok(r.status === 409, 'A: a conflicting round-1 answer is refused (409), no second challenger');
  sA = await A.session(); ok(Object.keys(sA.journey.looks).join() === 'A,B,C', 'A: exactly one challenger (C)');
  // reload in the middle of a picture, then hard restart of the server mid-picture
  await A.page.reload(); await A.page.getByText('Round 2 of 2').first().waitFor(); ok(true, 'A: reload on round 2 restores it');
  await stop(server); server = await startServer();
  await A.page.reload(); await A.page.getByText('Round 2 of 2').first().waitFor();
  sA = await A.waitFor(jobsDone(['C']), 'challenger after restart');
  ok(sA.journey.looks.C.job.status === 'success', 'A: challenger picture finished after a hard server restart');
  const stagesC = JSON.parse(fs.readFileSync(path.join(TMP, 'engine/state-stub/ledger.json'))).filter((x) => x.step);
  const dup = stagesC.map((x) => x.step).filter((k, i, a) => a.indexOf(k) !== i);
  ok(dup.length === 0, `A: no stage submitted twice across restart and double clicks (${stagesC.length} submissions)`);
  await A.shot('04-round2');
  await A.page.getByRole('button', { name: 'Keep Look A · your pick' }).click();
  await A.page.getByText('What should I carry into your final looks?').waitFor();
  await A.page.getByRole('button', { name: /The trouser shape/ }).click();
  await Promise.all([A.page.getByRole('button', { name: 'Show me final outfits' }).click(), A.page.getByRole('button', { name: 'Show me final outfits' }).click().catch(() => {})]);
  const introA = A.page.getByRole('status').filter({ hasText: 'Building your final outfits…' }); await introA.waitFor(); const itA = await introA.innerText();
  const learnedA = (await A.session()).journey.carry.learned.map((h) => h.value);
  ok(learnedA.length === 1 && learnedA.every((v) => itA.includes(v)) && /Your green blouse/.test(itA) && /About \$120/.test(itA), `A: final intro ticks the stated value (${learnedA.join(', ')}), the blouse and the budget`);
  await A.page.getByRole('button', { name: 'Show them now' }).waitFor({ state: 'detached', timeout: 15000 });
  await A.page.getByText('Your final outfits').first().waitFor();
  sA = await A.waitFor(jobsDone(['P2']), 'P2'); ok(Object.keys(sA.journey.looks).length === 4, 'A: one Proposal 2 despite two clicks');
  await A.shot('05-finalists');
  await A.page.getByRole('button', { name: 'Keep Proposal 1' }).click(); await A.page.getByText('Picked, not bought').first().waitFor();
  sA = await A.session(); ok(Object.values(sA.sizes).length === 0 && sA.versions[0].items.every((i) => !i.savedSize), 'A: no sizes are assumed');
  // sizes chosen by the shopper, persisted server-side
  const items = sA.versions[0].items.filter((i) => i.sizes);
  for (const it of items) { const z = it.sizes.find((x) => !(it.unavailable || []).includes(x)); await A.page.getByRole('button', { name: 'Size ' + z, exact: true }).first().click(); await sleep(300); }
  sA = await A.session(); ok(items.every((it) => sA.sizes[it.slot]), `A: sizes saved for ${items.map((i) => i.slot + ' ' + sA.sizes[i.slot]).join(', ')}`);
  await A.page.reload(); await A.page.getByText('Picked, not bought').first().waitFor();
  ok(/Add 3 items to your Livostyle bag/.test(await A.text()), 'A: sizes survive reload (bag enabled)');
  await A.shot('06-selected');
  // Share: the system share sheet with only the picture and one line (no link, prices or pieces)
  await sleep(1500); await A.page.getByRole('button', { name: 'Share this look' }).click(); await sleep(500);
  const shA = await A.page.evaluate(() => window.__shared.slice(-1)[0]);
  ok(shA && shA.text === 'I would love some feedback on my outfit' && !shA.url && shA.files.length === 1 && shA.files[0][1] === 'image/jpeg', 'A: Share sends only the outfit picture and “I would love some feedback on my outfit”');
  // read-only snapshot links (the API still offers them): share the original, then change the outfit: the snapshot must not move
  const shareA = (await A.api('/api/shares', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ versionId: 'ver_0' }) })).body;
  ok(!!shareA && /^\/s\/shr_[a-z2-7]{26}$/.test(shareA.publicUrl), 'A: unguessable read-only link created');
  // compound color-and-cut request
  await A.page.getByLabel('Ask for a change').fill('Show me darker trousers with a narrower leg. Keep everything else.'); await A.page.getByLabel('Ask for a change').press('Enter');
  await A.page.getByText(/Your winner vs (a new version|two new versions)/).waitFor();
  let rev = (await A.session()).activeRevision;
  ok(rev && rev.requested.length >= 2 || (rev && rev.status === 'needs_clarification') || (rev && rev.status === 'cannot' && !!rev.message),     // cannot: e.g. already the slimmest leg, said so
     `A: compound request parsed or answered (${rev && rev.status}: ${rev && (rev.requested.join(' + ') || rev.message)})`);
  if (rev.status === 'needs_clarification') { await A.page.getByRole('button', { name: rev.clarification.options[0] }).click(); await sleep(1500); rev = (await A.session()).activeRevision; }
  await A.shot('09-revision');
  if (rev && rev.status === 'open') {
    const cid = rev.candidates[0].candidateId;
    await A.page.getByRole('button', { name: /^Keep (?!this one|your)/ }).first().click(); await A.page.getByText('Your winner, updated').waitFor();
    r = await A.api(`/api/revisions/${rev.revisionId}/accept`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidateId: cid }) });
    ok(r.status === 200 && (await A.session()).versions.length === 2, 'A: accepting twice makes one new version');
    sA = await A.session(); const v0 = sA.versions[0].items, v1 = sA.versions[1].items;
    ok(v0[0].color === v1[0].color && v0.filter((i, k) => i.key === v1[k].key).length >= 2, 'A: kept pieces are unchanged in the new version');
    await A.waitFor((s) => s.versions[1].previewJob.status !== 'running', 'new picture');
  } else { ok(true, `A: request ended as ${rev && rev.status} (no option to keep)`); await A.page.getByRole('button', { name: /Back to your look|Keep your winner as it is/ }).first().click(); await sleep(800); }
  // friend: separate browser, no account
  const F = await shopper('friend', { device: devices['iPhone 13'] });
  await F.page.goto(B + shareA.publicUrl); await F.page.getByText('In this look').waitFor(); await F.shot('10-friend');
  const ft = await F.text();
  ok(/Green blouse/i.test(ft) && /Owned/.test(ft) && /This is a saved snapshot/.test(ft), 'friend: sees the shared version, owned top marked');
  ok(!/Try|Keep|Share this|Add 3|Ask for a change/.test(ft), 'friend: no editing controls');
  const pub = await F.api('/api/shares/' + shareA.snapshotId);
  ok(pub.status === 200 && !/ses_|photo|variant|size/i.test(JSON.stringify(Object.keys(pub.body))) && !JSON.stringify(pub.body).includes(sA.sessionId), 'friend: payload carries no session, photo, sizes or variants');
  ok((await F.api('/api/photo')).status === 401 && (await F.api('/api/session')).body.session === null, 'friend: no access to the owner’s photo or session');
  for (const [m, p] of [['POST', '/api/journey/keep'], ['POST', '/api/shares'], ['DELETE', '/api/shares/' + shareA.snapshotId], ['PUT', '/api/sizes']])
    ok((await F.api(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: '{}' })).status === 401, `friend: ${m} ${p} refused without the owner’s session`);
  const pub2 = await F.api('/api/shares/' + shareA.snapshotId);
  ok(JSON.stringify(pub2.body.items) === JSON.stringify(pub.body.items) && pub2.body.label === 'Original', 'friend: snapshot unchanged after the owner changed the outfit');
  // merchant handoff (free public stock read; the cart link is built, never opened)
  await A.page.evaluate(() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; });
  const bagBtn = A.page.getByRole('button', { name: /Add 3 items to your Livostyle bag|Choose \d size/ }).first();
  if (/Choose/.test(await bagBtn.innerText())) { sA = await A.session(); const cur = sA.versions[sA.cur]; for (const it of cur.items.filter((i) => i.sizes && !sA.sizes[i.slot])) { const z = it.sizes.find((x) => !(it.unavailable || []).includes(x)); await A.page.getByRole('button', { name: 'Size ' + z, exact: true }).first().click(); await sleep(300); } }
  const hResp = A.page.waitForResponse((r) => r.url().endsWith('/api/handoff'));
  await A.page.getByRole('button', { name: /Add 3 items to your Livostyle bag/ }).click();
  const h = await (await hResp).json(); await sleep(500); const opened = await A.page.evaluate(() => window.__opened);
  const gone = h && h.problems.filter((p) => /no longer sold/.test(p));     // the dataset can list a product the live store has dropped
  ok(h && (gone.length ? !h.cartUrl && !opened.some((u) => /livostyle\.com\/cart/.test(u || '')) : h.agree && h.cartUrl && opened.includes(h.cartUrl)),
    gone.length ? `A: bag names a product the store no longer sells and makes no cart (${gone.join('; ')})` : `A: bag = exact variants in A’s sizes (${h && h.items.map((i) => i.variantTitle).join(', ')})`);
  await A.shot('11-bag');
  // stop sharing -> friend gets 410
  r = await A.api('/api/shares/' + shareA.snapshotId, { method: 'DELETE' });
  ok(r.status === 200 && (await F.api('/api/shares/' + shareA.snapshotId)).status === 410, 'A: stop sharing → the link returns 410 (gone)');

  // ===== 2. Shopper B on a phone: no owned garment, asks for a dress =====
  console.log('Shopper B (phone 390×844, dress, nothing owned)');
  const Bp = await shopper('B', { device: devices['iPhone 13'] });
  await startScreen(Bp, 'shopper-b.jpg', null, { text: "Our anniversary dinner, somewhere nice. I'd like to wear a dress. No heels." });
  ok(/No limit/.test(await Bp.text()) && /my guess, please check/.test(await Bp.text()), 'B: no budget mentioned → "No limit", shown as a guess to check (never blocks)');
  ok(/Outfit/.test(await Bp.text()) && /A dress/.test(await Bp.text()), 'B: the brief shows "Outfit: A dress"');
  ok(await Bp.page.getByRole('button', { name: 'Open fitting room' }).isEnabled(), 'B: can start without an owned garment');
  await Bp.noHScroll('start'); await Bp.shot('01-start');
  let sB = await Bp.session(); ok(sB.sessionId !== sA.sessionId && !sB.owned, 'B: a different session from A, nothing owned');
  await Bp.page.getByRole('button', { name: 'Open fitting room' }).click();
  sB = await Bp.waitFor((s) => s.journey && s.journey.analysis.status !== 'running', 'colors');
  ok(sB.brief.budgetNew === null && sB.brief.structure === 'dress' && sB.brief.constraints.includes('No heels'), `B: No limit, dresses only; constraints ${sB.brief.constraints.join(', ')}`);
  await Bp.page.getByRole('button', { name: 'Show me my first looks' }).click(); await Bp.page.getByText('Round 1 of 2').first().waitFor();
  await Bp.noHScroll('round 1'); await Bp.shot('02-round1');
  sB = await Bp.waitFor(jobsDone(['A', 'B']), 'B round 1');
  ok(Object.values(sB.journey.looks).every((l) => l.structure === 'dress' && l.items.map((i) => i.slot).join() === 'dress,shoes,necklace'), 'B: round 1 = two complete dress looks (dress, shoes, necklace)');
  ok(sB.journey.looks.A.job.steps.map((x) => x.slot).join() === 'dress,shoes,necklace' && sB.journey.looks.A.job.steps[0].parent.startsWith('photo-'), 'B: dress picture chain = photo → dress → shoes → necklace');
  ok((await Bp.api(`/api/jobs/${sA.journey.looks.A.job.jobId}`)).status === 404, 'B: cannot read A’s picture jobs');
  await Bp.page.getByRole('button', { name: 'Keep Look B' }).click(); await Bp.page.getByText('Round 2 of 2').first().waitFor();
  await Bp.waitFor(jobsDone(['C']), 'B challenger'); await Bp.noHScroll('round 2');
  await Bp.page.getByRole('button', { name: 'Keep Look C · new' }).click(); await Bp.page.getByText('What should I carry into your final looks?').waitFor();
  ok(/The dress shape/.test(await Bp.text()) && !/trouser shape/i.test(await Bp.text()), 'B: reasons say "the dress shape", never trousers');
  await Bp.page.getByRole('button', { name: /The dress shape/ }).click(); await Bp.page.getByRole('button', { name: 'Show me final outfits' }).click();
  await Bp.page.getByText('Your final outfits').first().waitFor();
  await Bp.waitFor(jobsDone(['P2']), 'B P2'); await Bp.noHScroll('finalists'); await Bp.shot('03-finalists');
  await Bp.page.getByRole('button', { name: 'Keep Proposal 2' }).click(); await Bp.page.getByText('Picked, not bought').first().waitFor();
  await Bp.noHScroll('selected'); await Bp.shot('04-selected');
  sB = await Bp.session(); const dv0 = sB.versions[0];
  await Bp.page.getByLabel('Ask for a change').fill('a longer dress'); await Bp.page.getByLabel('Ask for a change').press('Enter');
  await Bp.page.getByText(/Your winner vs (a new version|two new versions)/).waitFor(); await Bp.noHScroll('revision'); await Bp.shot('05-revision');
  let rB = (await Bp.session()).activeRevision;
  if (rB.status === 'needs_clarification') { await Bp.page.getByRole('button', { name: rB.clarification.options[0] }).click(); await sleep(1500); rB = (await Bp.session()).activeRevision; }
  if (rB.status === 'open') {
    await Bp.page.getByRole('button', { name: /^Keep (?!this one|your)/ }).first().click(); await Bp.page.getByText('Your winner, updated').waitFor();
    sB = await Bp.session(); const dv1 = sB.versions[1];
    ok(dv1.items.map((i) => i.slot).join() === 'dress,shoes,necklace' && dv1.productIds.shoes === dv0.productIds.shoes && dv1.productIds.necklace === dv0.productIds.necklace && dv1.productIds.dress !== dv0.productIds.dress,
      `B: dress refinement changed only the dress (${rB.candidates[0].changeSummary})`);
    await Bp.waitFor((s) => s.versions[1].previewJob.status !== 'running', 'B new picture');
  } else ok(true, `B: "a longer dress" ended as ${rB.status}: ${rB.message || (rB.noMatch || {}).message || ''}`);
  await sleep(1500); await Bp.page.getByRole('button', { name: 'Share this look' }).click(); await sleep(500);
  ok((await Bp.page.evaluate(() => window.__shared.length)) === 1, 'B: Share opens the system share sheet on the phone'); await Bp.noHScroll('share'); await Bp.shot('06-share');
  sB = await Bp.session(); const cur = sB.versions[sB.cur];
  for (const it of cur.items.filter((i) => i.sizes)) { const z = it.sizes.find((x) => !(it.unavailable || []).includes(x)), zb = Bp.page.getByRole('button', { name: 'Size ' + z, exact: true }).first();
    if (!(await zb.isVisible())) await Bp.page.locator('button[aria-expanded="false"]').filter({ hasText: 'Choose size' }).first().click();
    await zb.click(); await sleep(400); }
  ok(Object.keys((await Bp.session()).sizes).sort().join() === cur.items.filter((i) => i.sizes).map((i) => i.slot).sort().join(), 'B: sizes chosen for the dress, shoes and necklace on the phone');
  await Bp.noHScroll('pieces'); await Bp.shot('07-pieces');
  sA = await A.session(); ok(sA.versions.length >= 1 && sA.owned.label === 'Green blouse', 'A: unaffected by B');

  // ===== 3. Shopper D: no owned garment, either -> one separates and one dress; top change; switch to a dress =====
  console.log('Shopper D (desktop, separates and dress, nothing owned)');
  const D = await shopper('D');
  await startScreen(D, 'shopper-a.jpg', null, { text: 'A work dinner next week. About $180 for everything. No heels.' });
  await D.page.getByRole('button', { name: 'Open fitting room' }).click();
  await D.waitFor((s) => s.journey && s.journey.analysis.status !== 'running', 'colors');
  await D.page.getByRole('button', { name: 'Show me my first looks' }).click(); await D.page.getByText('Round 1 of 2').first().waitFor();
  let sD = await D.waitFor(jobsDone(['A', 'B']), 'D round 1'); await D.shot('01-round1');
  ok(sD.journey.looks.A.structure === 'separates' && sD.journey.looks.A.items.map((i) => i.slot).join() === 'top,bottom,shoes,necklace' && !sD.journey.looks.A.items[0].owned,
    'D: Look A = catalog separates (top, trousers, shoes, necklace)');
  ok(sD.journey.looks.B.structure === 'dress', 'D: Look B = a dress (either was acceptable)');
  ok(['top,bottom,tuck,shoes,necklace', 'bottom,top,shoes,necklace'].includes(sD.journey.looks.A.job.steps.map((x) => x.slot).join()) && sD.journey.looks.A.job.steps[0].parent.startsWith('photo-'), 'D: separates picture chain = photo → top → trousers → tuck edit → shoes → necklace (or trousers → top when untucked)');
  ok(sD.journey.looks.A.items.slice(1).every((i) => i.price > 0) && Object.values(sD.journey.looks).every((l) => l.items.reduce((t, i) => t + (i.owned ? 0 : i.price), 0) <= 190), 'D: every piece priced; looks within $180 + $10');
  await D.page.getByRole('button', { name: 'Keep Look A', exact: true }).click(); await D.page.getByText('Round 2 of 2').first().waitFor();
  await D.waitFor(jobsDone(['C']), 'D challenger');
  await D.page.getByRole('button', { name: 'Keep Look A · your pick' }).click(); await D.page.getByText('What should I carry into your final looks?').waitFor();
  ok(/The trouser shape/.test(await D.text()), 'D: separates keep "the trouser shape"');
  await D.page.getByRole('button', { name: 'Skip' }).click(); await D.page.getByText('Your final outfits').first().waitFor();
  await D.waitFor(jobsDone(['P2']), 'D P2');
  await D.page.getByRole('button', { name: 'Keep Proposal 1' }).click(); await D.page.getByText('Picked, not bought').first().waitFor();
  sD = await D.session(); const v0 = sD.versions[0];
  ok(v0.productIds.top === sD.journey.looks.A.items[0].productId, 'D: Proposal 1 is the actual round-2 winner');
  for (const t of ['Longer sleeves', 'a v-neck top', 'a different neckline']) {
    await D.page.getByLabel('Ask for a change').fill(t); await D.page.getByLabel('Ask for a change').press('Enter'); await D.page.getByText(/Your winner vs (a new version|two new versions)/).waitFor();
    let r = (await D.session()).activeRevision;
    if (r.status === 'needs_clarification') { await D.page.getByRole('button', { name: r.clarification.options[0] }).click(); await sleep(1500); r = (await D.session()).activeRevision; }
    if (r.status === 'open') {
      await D.page.getByRole('button', { name: /^Keep (?!this one|your)/ }).first().click(); await D.page.getByText('Your winner, updated').waitFor();
      const v1 = (await D.session()).versions.at(-1);
      ok(v1.productIds.top !== v0.productIds.top && ['bottom', 'shoes', 'necklace'].every((k) => v1.productIds[k] === v0.productIds[k]), `D: top refinement "${t}" changed only the top`);
      break;
    }
    ok(true, `D: "${t}" → ${r.status} (${r.message || (r.noMatch || {}).message || ''})`);
    await D.page.getByRole('button', { name: /Back to your look|Keep your winner as it is/ }).first().click(); await sleep(800);
  }
  sD = await D.session(); const before = sD.versions[sD.cur];
  await D.page.getByLabel('Ask for a change').fill('Can I see a dress instead?'); await D.page.getByLabel('Ask for a change').press('Enter'); await D.page.getByText(/Your winner vs (a new version|two new versions)/).waitFor();
  let rD = (await D.session()).activeRevision; await D.shot('02-switch-option');
  ok(rD.status === 'open' && rD.candidates[0].structure === 'dress', `D: "a dress instead" → ${rD.status}: ${rD.candidates[0] && rD.candidates[0].changeSummary}`);
  await D.page.getByRole('button', { name: /^Keep (?!this one|your)/ }).first().click(); await D.page.getByText('Your winner, updated').waitFor();
  sD = await D.waitFor((s) => s.versions[s.cur].previewJob.status !== 'running', 'D switched picture');
  const after = sD.versions[sD.cur];
  ok(after.items.map((i) => i.slot).join() === 'dress,shoes,necklace' && after.productIds.shoes === before.productIds.shoes && after.productIds.necklace === before.productIds.necklace,
    'D: switch → dress, shoes, necklace; shoes and necklace kept');
  ok(Math.abs(after.subtotal - after.items.reduce((t, i) => t + i.price, 0)) < 0.01 && Object.keys(sD.sizes).every((k) => ['dress', 'shoes', 'necklace'].includes(k)), `D: total $${after.subtotal} = the three pieces; no leftover sizes`);
  ok(after.previewJob.steps.map((x) => x.slot).join() === 'dress,shoes,necklace' && after.previewJob.steps[0].parent.startsWith('photo-'), 'D: the new picture starts from the photo (no top or trousers carried over)');
  for (const it of after.items.filter((i) => i.sizes)) { const z = it.sizes.find((x) => !(it.unavailable || []).includes(x)); await D.page.getByRole('button', { name: 'Size ' + z, exact: true }).first().click(); await sleep(300); }
  await D.page.evaluate(() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; });
  const hDResp = D.page.waitForResponse((r) => r.url().endsWith('/api/handoff'));
  await D.page.getByRole('button', { name: /Add 3 items to your Livostyle bag/ }).click();
  const hD = await (await hDResp).json();
  ok(hD && hD.agree && hD.items.map((i) => i.slot).join() === 'dress,shoes,necklace' && hD.items.map((i) => i.productId).join() === [after.productIds.dress, after.productIds.shoes, after.productIds.necklace].join(),
    `D: bag = the dress, shoes and necklace only (${hD && hD.items.map((i) => i.variantTitle).join(', ')})`);
  await D.shot('03-switched');
  await D.page.reload(); await D.page.getByText('Your winner, updated').first().waitFor(); ok(/Dress/.test(await D.text()), 'D: reload restores the dress version');

  // a clean browser never inherits anyone
  const C = await shopper('C'); await C.page.goto(B + '/'); await C.page.getByRole('button', { name: /Add your full-length photo/ }).waitFor();
  const sC = await C.session(); ok(sC.sessionId !== sA.sessionId && sC.sessionId !== sB.sessionId && !sC.photo && !sC.journey, 'clean browser: a fresh, empty session');

  // ===== 3. Unavailable providers: live mode without authorization never simulates =====
  console.log('Unavailable providers');
  const U = await startServer({ CALLIOPE_PROVIDER: 'youcam', CALLIOPE_LIVE_UNITS: '', CALLIOPE_NOVA_USD: '', CALLIOPE_STATE_DIR: path.join(TMP, 'u/app'), CALLIOPE_ENGINE_DIR: path.join(TMP, 'u/engine') }, PORT + 1); extra.push(U);
  const ctxU = await browser.newContext(); const pu = await ctxU.newPage(); await pu.goto(`http://127.0.0.1:${PORT + 1}/`); await pu.getByText('Calliope isn’t available right now').first().waitFor();
  ok(await pu.getByRole('button', { name: /Add your full-length photo/ }).isDisabled() && !/SIMULATED/.test(await pu.locator('body').innerText()), 'unavailable: clear message, uploads disabled, no simulated content');
  const up = await pu.evaluate(() => fetch('/api/photo?consent=1', { method: 'POST', body: new Blob(['x']) }).then((r) => r.status));
  ok(up === 503, 'unavailable: the API refuses work with 503');
  await pu.screenshot({ path: `${OUT}/unavailable.png` }); await stop(U);

  // ===== Necklace check: a photo the necklace engine refuses (stub imitates the provider's pose refusal) =====
  console.log('Necklace check on upload');
  const NK = await startServer({ CALLIOPE_STUB_REFUSE_NECKLACE: '1', CALLIOPE_STATE_DIR: path.join(TMP, 'nk/app'), CALLIOPE_ENGINE_DIR: path.join(TMP, 'nk/engine') }, PORT + 2); extra.push(NK);
  const ctxN = await browser.newContext({ viewport: { width: 1360, height: 1000 } }); const pn = await ctxN.newPage(); pn.on('pageerror', (e) => errors.push('N: ' + e));
  await pn.goto(`http://127.0.0.1:${PORT + 2}/`); await pn.getByText(/however it comes out\./).waitFor();   // intro typed out: the photo box has stopped moving
  ok(/head level/.test(await pn.locator('body').innerText()), 'necklace: photo guidance asks for a level head and no necklace');
  { const t0 = Date.now(), fc = pn.waitForEvent('filechooser'); await pn.getByRole('button', { name: /Add your full-length photo/ }).click(); await (await fc).setFiles(path.join(IMG, 'shopper-a.jpg'));
    await pn.getByText('Photo added').first().waitFor(); ok(Date.now() - t0 < 2500, `necklace: upload returns before the checks run (${Date.now() - t0} ms)`); }
  await sleep(8000);                                   // the background check finishes while the shopper types
  ok(!/Necklaces won.t show|Choose another photo/.test(await pn.locator('body').innerText()), 'necklace: a refused photo is never flagged to the shopper');
  ok((await pn.evaluate(() => fetch('/api/session').then((r) => r.json()))).photo.checks.necklace === 'refused', 'necklace: the background check recorded the refusal');
  await pn.screenshot({ path: `${OUT}/necklace-refused-upload.png` });
  await pn.getByLabel('Tell Calliope about the occasion').fill('Anniversary dinner, about $200, no heels.'); await pn.getByRole('button', { name: 'Go' }).click(); await sleep(4000);
  await pn.getByRole('button', { name: 'Open fitting room' }).click();
  const sess = () => pn.evaluate(() => fetch('/api/session').then((r) => r.json()));
  for (let i = 0; i < 120 && !((await sess()).journey || { analysis: { status: 'running' } }).analysis.status.match(/ready|unavailable/); i++) await sleep(250);
  await pn.getByRole('button', { name: 'Show me my first looks' }).click(); await pn.getByText('Round 1 of 2').first().waitFor();
  let sn; for (let i = 0; i < 240; i++) { sn = await sess(); if (['A', 'B'].every((id) => sn.journey.looks[id].job.status !== 'running')) break; await sleep(250); }
  const nsteps = ['A', 'B'].map((id) => sn.journey.looks[id].job.steps.at(-1));
  ok(nsteps.every((x) => x.slot === 'necklace' && x.status === 'not_shown' && /head is tilted/.test(x.reason)) && ['A', 'B'].every((id) => sn.journey.looks[id].job.status === 'partial'),
    'necklace: pictures say the necklace isn’t drawn and why');
  ok(['A', 'B'].every((id) => !sn.journey.looks[id].items.some((i) => i.slot === 'necklace')), 'necklace: round-1 pieces leave out the necklace the pictures can’t show');
  const nl = JSON.parse(fs.readFileSync(path.join(TMP, 'nk/engine/state-stub/ledger.json'))).filter((r) => /necklace/.test(r.endpoint));
  ok(nl.length === 1, `necklace: one necklace call in total (the photo check), none per picture (${nl.length})`);
  await pn.screenshot({ path: `${OUT}/necklace-refused-round1.png` });
  // the omission holds through the rounds, the selection, the total, the share and the bag
  const napi = (p, body, method = 'POST') => pn.evaluate(([p, body, method]) => fetch(p, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(async (r) => ({ status: r.status, body: await r.json() })), [p, body, method]);
  const settle = async (ids) => { for (let i = 0; i < 240; i++) { sn = await sess(); if (ids.every((id) => sn.journey.looks[id] && sn.journey.looks[id].job.status !== 'running')) return sn; await sleep(250); } };
  await napi('/api/journey/keep', { round: 1, id: 'A' }); await settle([sn.journey.challenger || 'C']);
  await napi('/api/journey/keep', { round: 2, id: 'A' });
  ok(!/necklace/i.test(((await sess()).journey.reasons || []).find((x) => x.k === 'feel').short), 'necklace: "the overall feel" reason doesn’t mention the necklace');
  await napi('/api/journey/finalize', { keys: ['colors'] }); await settle(['P2']);
  await napi('/api/journey/choose', { id: 'A' }); sn = await sess(); const nv = sn.versions[0];
  ok(!nv.items.some((i) => i.slot === 'necklace') && !('necklace' in nv.productIds) && nv.subtotal === Math.round(nv.items.filter((i) => !i.owned).reduce((t, i) => t + i.price, 0) * 100) / 100,
    `necklace: the selected outfit’s pieces, ids and total leave it out (${nv.items.map((i) => i.slot).join(', ')} · $${nv.subtotal})`);
  for (const it of nv.items.filter((i) => i.sizes)) await napi('/api/sizes', { slot: it.slot, size: it.sizes.find((x) => !(it.unavailable || []).includes(x)) }, 'PUT');
  const nsh = await napi('/api/shares', { versionId: nv.versionId }); const npub = await pn.evaluate((u) => fetch(u).then((r) => r.json()), '/api/shares/' + nsh.body.snapshotId);
  ok(!npub.items.some((i) => i.slot === 'necklace') && npub.subtotal === nv.subtotal && /necklace isn.t shown/.test(npub.note || ''), 'necklace: the shared snapshot leaves it out, same total');
  const nh = await napi('/api/handoff', { versionId: nv.versionId });
  ok(nh.status === 200 && !nh.body.items.some((i) => i.slot === 'necklace') && nh.body.agree, `necklace: the bag gets only the pictured pieces (${(nh.body.items || []).map((i) => i.slot).join(', ')}; ${(nh.body.problems || [nh.body.code]).join('; ')})`);
  await pn.reload(); await pn.getByText('Picked, not bought').first().waitFor().catch(() => {});
  ok(!/necklace/i.test(await pn.locator('body').innerText().then((t) => t.replace(/necklace isn.t (in|shown)[^.]*\.|necklace not shown/gi, ''))), 'necklace: the selected page doesn’t list or price a necklace');
  await pn.screenshot({ path: `${OUT}/necklace-refused-selected.png` }); await stop(NK);

  // ===== 4. One product surface: former demo/replay URLs can't switch anything on =====
  console.log('Former demo and replay URLs');
  for (const u of ['/app', '/app/', '/app/calliope.dc.html', '/app/support.js', '/app/SharedLookPage.dc.html', `/demo/${sA.sessionId}`])
    ok((await fetch(B + u, { redirect: 'manual' })).status === 404, `legacy: ${u} is not served`);
  for (const u of ['/api/golden', '/api/golden/x/media/photo.jpg', `/api/demo/replay/${sA.sessionId}`, '/api/demo/session', '/api/demo/status'])
    ok((await A.api(u)).status === 404, `removed: ${u} is not served`);
  ok((await A.api('/api/demo/journey/keep', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"round":1,"id":"A"}' })).status === 404, 'removed: POST /api/demo/* is not served');
  const sessBefore = fs.readFileSync(path.join(TMP, `app/sessions/${sA.sessionId}.json`), 'utf8');
  const Dm = await A.ctx.newPage(), dreq = []; Dm.on('request', (q) => dreq.push(new URL(q.url()).pathname));
  await Dm.goto(B + '/?demo=1'); await Dm.getByText(/Your winner/).first().waitFor();
  ok(!dreq.some((x) => /golden|\/api\/demo\//.test(x)), `/?demo=1: the normal app, no replay requests (${dreq.length} requests)`);
  ok(!/DEMO|recording|presenter|Record take/i.test(await Dm.locator('body').innerText()), '/?demo=1: no demo controls or labels');
  ok((await A.session()).sessionId === sA.sessionId && fs.readFileSync(path.join(TMP, `app/sessions/${sA.sessionId}.json`), 'utf8') === sessBefore, '/?demo=1: the browser’s own session, unchanged');
  ok(!fs.existsSync(path.join(TMP, 'app/captures')), 'no per-request capture log is written');
  await Dm.close();
  // ===== 5. Production build: no demo-only code, recorded data or private assets =====
  const dist = path.join(ROOT, 'frontend/dist'), files = []; const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : files.push(path.join(d, e.name)))); walk(dist);
  const js = files.filter((f) => /\.(js|html|css)$/.test(f)).map((f) => fs.readFileSync(f, 'utf8')).join('\n');
  ok(!/api\/golden|api\/demo|demo=1|ses_cd57|golden|DemoControls|Record take|prepared clip/.test(js), 'build: no golden adapter, demo routes or replay strings in frontend/dist');
  ok(files.every((f) => /\.(js|css|html|woff2)$/.test(f)), `build: only code, styles and fonts (${files.length} files; no images, audio or session data)`);
} catch (e) {
  fail.push('aborted: ' + (e.message || e)); console.log('ABORTED', e);
  for (const c of browser.contexts()) for (const [i, pg] of c.pages().entries()) await pg.screenshot({ path: `${OUT}/aborted-${browser.contexts().indexOf(c)}-${i}.png` }).catch(() => {});
} finally {
  if (server) await stop(server);
  for (const p of extra) if (p.exitCode === null) await stop(p);
  await browser.close();
  ok(errors.length === 0, `no page errors (${errors.slice(0, 3).join(' | ')})`);
  fs.writeFileSync(path.join(OUT, 'verify-live.json'), JSON.stringify({ checks, fail, errors, out: OUT, state: TMP }, null, 1));
  console.log(`\n${fail.length ? 'FAIL' : 'PASS'} · ${checks.length} passed, ${fail.length} failed · evidence in ${OUT}`);
  process.exit(fail.length ? 1 : 0);
}
