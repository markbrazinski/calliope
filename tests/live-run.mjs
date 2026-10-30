// One shopper journey through the normal app on a running server, recording what the shopper waits for.
//   node tests/live-run.mjs <base url> <run.json> <out dir>
// run.json: { name, photo, top: { file, color, category, label } | null, brief: { text, fill: [[chip, value]], spoken: <16-bit wav> }, phone: bool, stopAfter: 'round1'?,
//             r1: 'A'|'B', r2: 'pick'|'C', reasons: [...], fin: 1|2, requests: ['...'], option: 1|2, share: bool, bag: bool, saveState: <file> }
// Against a live server this spends provider units: it is run only inside an authorized, capped paid run.
import { chromium, devices } from '../node_modules/playwright/index.mjs';
import fs from 'fs'; import path from 'path';

const [B, cfgFile, OUT] = process.argv.slice(2); const cfg = JSON.parse(fs.readFileSync(cfgFile)); fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// brief.spoken: the brief is said into Chromium's fake microphone (that wav), recognised by the server's speech path, checked, then sent with Go
const browser = await chromium.launch({ args: cfg.brief.spoken ? ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${cfg.brief.spoken}`] : [] });
const ctx = await browser.newContext({ ...(cfg.phone ? devices['iPhone 13'] : { viewport: { width: 1360, height: 1000 } }), ...(cfg.brief.spoken ? { permissions: ['microphone'] } : {}) });
await ctx.addInitScript(() => { window.__shared = []; navigator.canShare = () => true; navigator.share = (d) => { window.__shared.push({ text: d.text, files: (d.files || []).length }); return Promise.resolve(); }; });   // record the system share sheet
const page = await ctx.newPage(); const errors = []; page.on('pageerror', (e) => errors.push(String(e)));
const waits = [], marks = {}, t0 = Date.now(); const now = () => Date.now() - t0;
const shot = (n) => page.screenshot({ path: `${OUT}/${cfg.name}-${n}.png` });
const session = () => page.evaluate(() => fetch('/api/session').then((r) => r.json()));
async function until(label, fn, ms = 600000) { const a = Date.now(); for (;;) { const v = await fn(); if (v) { const w = Date.now() - a; waits.push({ wait: label, ms: w, atMs: now() }); console.log(`  ${label}: ${(w / 1000).toFixed(1)} s`); return v; } if (Date.now() - a > ms) throw new Error('timed out: ' + label); await sleep(250); } }
const jobReady = (id) => async () => { const s = await session(); const j = s.journey && s.journey.looks[id] && s.journey.looks[id].job; return j && j.status !== 'running' && j; };
const cardShows = (id) => async () => (await page.locator(`img[alt^="Outfit preview"]`).count()) > 0 && (await jobReady(id)());
const upload = async (name, file) => { const fc = page.waitForEvent('filechooser'); await page.getByRole('button', { name }).click(); await (await fc).setFiles(file); };
const out = { name: cfg.name, base: B, startedAt: new Date().toISOString(), waits, errors };
try {
  await page.goto(B + '/'); await page.getByRole('button', { name: /Add your full-length photo/ }).waitFor();
  let a = Date.now(); await upload(/Add your full-length photo/, cfg.photo); await page.getByText('Photo added').first().waitFor(); waits.push({ wait: 'photo upload + checks', ms: Date.now() - a });
  let s = await session(); out.photoChecks = s.photo.checks; out.photoIssue = s.photo.issue;
  if (cfg.top) {
    await upload('+ Add clothes to pair', cfg.top.file);
    await page.waitForFunction(() => /· Yours|Use this top/.test(document.body.innerText), null, { timeout: 60000 });   // read from the photo, or asked
    if (await page.getByRole('button', { name: 'Use this top' }).count()) {
      await page.getByLabel('Color of your top').fill(cfg.top.color); await page.getByRole('button', { name: cfg.top.category, exact: true }).click(); await page.getByRole('button', { name: 'Use this top' }).click(); }
    await page.getByText(/· Yours/).first().waitFor();
    out.topRead = (await session()).owned;
  }
  if (cfg.brief.spoken) {
    await page.getByRole('button', { name: 'Speak' }).click(); await page.getByRole('button', { name: /Listening/ }).waitFor(); await sleep(cfg.brief.speakMs || 4500);
    await page.getByRole('button', { name: /Listening/ }).click(); await page.getByRole('button', { name: 'Speak' }).waitFor({ timeout: 20000 });
    out.spokenBrief = await page.getByLabel('Tell Calliope about the occasion').inputValue(); out.spokenSentBeforeGo = !!(await session()).journey;
    console.log('  heard:', out.spokenBrief);
    if (cfg.brief.text) await page.getByLabel('Tell Calliope about the occasion').fill(out.spokenBrief + ' ' + cfg.brief.text);      // the shopper adds to what was heard
  } else await page.getByLabel('Tell Calliope about the occasion').fill(cfg.brief.text);
  await page.getByRole('button', { name: 'Go', exact: true }).click(); await sleep(4000);
  for (const [chip, value] of cfg.brief.fill || []) {
    await page.getByText(chip + ' · needed').locator('xpath=ancestor::div[2]').getByRole('button', { name: 'Add' }).click();
    if (chip === 'Occasion') { await page.getByLabel('Occasion', { exact: true }).fill(value); await page.getByRole('button', { name: 'Save' }).click(); } else await page.getByRole('button', { name: value }).click();
    await sleep(400);
  }
  await shot('01-start');
  await page.getByRole('button', { name: 'Open fitting room' }).click(); marks.open = now();
  s = await until('colour reading (face box, analysis, eye check)', async () => { const x = await session(); return x.journey && x.journey.analysis.status !== 'running' && x; });
  out.analysis = s.journey.analysis; out.brief = s.brief; out.notUsed = s.notUsed; out.palette = s.journey.palette && s.journey.palette.name;
  await shot('02-palette');
  const go = s.journey.palette ? 'Show me my first looks' : 'Continue without colors';
  await page.getByRole('button', { name: go }).click(); await page.getByText('Round 1 of 2').first().waitFor(); marks.round1 = now();
  s = await session(); out.looks = Object.fromEntries(Object.entries(s.journey.looks).map(([k, l]) => [k, l.items.map((i) => i.title || i.name)]));
  await sleep(1500); await shot('03-round1-pending');
  const firstOf = async () => { const x = await session(); const r = ['A', 'B'].map((id) => x.journey.looks[id].job).filter((j) => j.status !== 'running'); return r.length && r; };
  await until('round 1: first picture ready', firstOf);
  await until('round 1: second picture ready', async () => (await jobReady('A')()) && (await jobReady('B')()));
  await sleep(2000); await shot('04-round1');
  if (cfg.stopAfter === 'round1') { s = await session(); out.sessionId = s.sessionId; out.round1 = Object.fromEntries(Object.entries(s.journey.looks).map(([k, l]) => [k, { structure: l.structure, items: l.items.map((i) => [i.slot, i.title || i.name, i.price]), job: { status: l.job.status, note: l.job.note, steps: l.job.steps } }])); throw new Error('STOP_AFTER_ROUND1'); }
  await page.getByRole('button', { name: 'Keep Look ' + cfg.r1, exact: true }).click(); await page.getByText('Round 2 of 2').first().waitFor();
  await until('round 2: challenger picture', jobReady('C')); await sleep(1500); await shot('05-round2');
  await page.getByRole('button', { name: cfg.r2 === 'C' ? 'Keep Look C · new' : `Keep Look ${cfg.r1} · your pick` }).click();
  await page.getByText('What should I carry into your final looks?').waitFor(); await shot('06-carry');
  out.learningBeforeAnswer = (await session()).journey.learning;
  for (const k of cfg.reasons) await page.getByRole('button', { name: { colors: /The colors/, shape: /The (trouser|dress) shape/, feel: /The overall feel/ }[k] }).click();
  await page.getByRole('button', { name: cfg.reasons.length ? 'Show me final outfits' : 'Skip' }).click(); await page.getByText('Your final outfits').first().waitFor();
  { const x = await session(); out.learningAfterAnswer = x.journey.learning; out.proposal2Tag = x.journey.looks.P2.tag; out.carry = x.journey.carry; }
  await until('finalists: Proposal 2 picture', jobReady('P2')); await sleep(1500); await shot('07-finalists');
  await page.getByRole('button', { name: 'Keep Proposal ' + cfg.fin }).click(); await page.getByText('Picked, not bought').first().waitFor(); await shot('08-selected');
  s = await session(); const cur = s.versions[s.cur];
  for (const it of cur.items.filter((i) => i.sizes)) { const z = it.sizes.find((x) => !(it.unavailable || []).includes(x)), zb = page.getByRole('button', { name: 'Size ' + z, exact: true }).first();
    if (!(await zb.isVisible())) await page.locator('button[aria-expanded="false"]').filter({ hasText: 'Choose size' }).first().click(); await zb.click(); await sleep(400); }
  if (cfg.share) {
    await sleep(1500); await page.getByRole('button', { name: 'Share this look' }).click(); await sleep(500); out.shareSheet = await page.evaluate(() => window.__shared.slice(-1)[0]);
    out.share = await page.evaluate((v) => fetch('/api/shares', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ versionId: v }) }).then((r) => r.json()), (await session()).versions[0].versionId);   // read-only link (API) for the friend view
    const f = await (await browser.newContext(devices['iPhone 13'])).newPage(); await f.goto(B + out.share.publicUrl); await f.getByText('In this look').waitFor(); await sleep(1500);
    await f.screenshot({ path: `${OUT}/${cfg.name}-10-friend.png` }); out.friendSees = (await f.locator('body').innerText()).slice(0, 400);
  }
  out.requests = [];
  for (const text of cfg.requests || (cfg.request ? [cfg.request] : [])) {      // each request in turn; an open option is kept
    const before = await session(), v0 = before.versions[before.cur];
    await page.getByLabel('Ask for a change').fill(text); a = Date.now(); await page.getByLabel('Ask for a change').press('Enter');
    await page.getByText(/Your winner vs (a new version|two new versions)/).waitFor(); waits.push({ wait: `request interpreted: ${text}`, ms: Date.now() - a });
    let rev = (await session()).activeRevision; const rec = { text, status: rev.status, interpreter: rev.interpreter, requested: rev.requested };
    if (rev.status === 'needs_clarification') { rec.clarification = rev.clarification.question; await page.getByRole('button', { name: rev.clarification.options[0] }).click(); await sleep(2500); rev = (await session()).activeRevision; rec.after = rev.status; }
    await shot(`11-request-${out.requests.length + 1}`);
    if (rev.status === 'open') {
      const opts = rev.candidates.filter((k) => k.kind === 'match'), pick = Math.min(cfg.option || 1, opts.length);     // run.json "option": 2 keeps the second one
      rec.options = opts.map((k) => k.changeSummary); rec.option = opts[pick - 1].changeSummary; rec.onlyOption = rev.onlyOption;
      await page.getByRole('button', { name: /^Keep (?!this one|your)/ }).nth(pick - 1).click(); await page.getByText('Your winner, updated').waitFor();
      await until(`new picture: ${text}`, async () => { const x = await session(); return x.versions[x.cur].previewJob.status !== 'running'; }); await sleep(1500); await shot(`12-revised-${out.requests.length + 1}`);
      const s2 = await session(), v1 = s2.versions[s2.cur];
      rec.before = v0.productIds; rec.after = v1.productIds; rec.structure = v1.structure; rec.picture = { status: v1.previewJob.status, note: v1.previewJob.note, steps: v1.previewJob.steps };
      for (const it of v1.items.filter((i) => i.sizes && !s2.sizes[i.slot])) { const z = it.sizes.find((x) => !(it.unavailable || []).includes(x)), zb = page.getByRole('button', { name: 'Size ' + z, exact: true }).first();
        if (!(await zb.isVisible())) await page.locator('button[aria-expanded="false"]').filter({ hasText: 'Choose size' }).first().click(); await zb.click(); await sleep(400); }
    } else { rec.message = rev.message || (rev.noMatch || {}).message; await page.getByRole('button', { name: /Back to your look|Keep your winner as it is/ }).first().click(); await sleep(800); }
    out.requests.push(rec); console.log('  request', JSON.stringify(rec).slice(0, 220));
  }
  if (cfg.bag) {
    await page.evaluate(() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; });
    const hResp = page.waitForResponse((r) => r.url().endsWith('/api/handoff'));
    await page.getByRole('button', { name: /Add \d+ (items )?to (your )?Livostyle bag/ }).first().click();
    const h = await (await hResp).json(); await sleep(500);
    out.handoff = { items: h.items.map((i) => [i.slot, i.productId, i.variantTitle, i.size, i.available]), problems: h.problems, agree: h.agree, cart: !!h.cartUrl, cartUrl: h.cartUrl,
      ownedNotSent: h.ownedNotSent, opened: await page.evaluate(() => window.__opened.length) };
    await shot('13-bag');
  }
  s = await session(); out.sessionId = s.sessionId; out.final = { versions: s.versions.map((v) => ({ label: v.label, job: v.previewJob && v.previewJob.status })), sizes: s.sizes };
} catch (e) { if (String(e.message) !== 'STOP_AFTER_ROUND1') { out.error = String(e.message || e); await shot('error').catch(() => {}); console.log('ERROR', out.error); } }
if (cfg.saveState) await ctx.storageState({ path: cfg.saveState });      // a later browser (another fake microphone clip) can continue this session
out.totalMs = now(); fs.writeFileSync(`${OUT}/${cfg.name}.json`, JSON.stringify(out, null, 1)); await browser.close();
console.log(out.error ? 'FAILED' : 'DONE', cfg.name, (out.totalMs / 1000).toFixed(0) + ' s', '| errors:', errors.length);
