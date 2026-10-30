// One scripted shopper journey for the scheduling experiment (current vs early), recording what the shopper waits for.
//   node tests/schedule-run.mjs <base url> <run.json> <out dir>
// run.json: { name, photo, top: {file, color, category, label} | null, brief: {text, fill}, r1: 'A'|'B'|'C', r2: 'pick'|'new',
//             reasons: [...], fin: 1|2, dwell: {photo, brief, palette, compare, reasons} (seconds; scripted assumptions) }
// Visible wait = time the shopper has nothing to do: from their action until the screen is usable (all choices pictured).
import { chromium } from '../node_modules/playwright/index.mjs';
import fs from 'fs';
const [B, cfgFile, OUT] = process.argv.slice(2); const cfg = JSON.parse(fs.readFileSync(cfgFile)); fs.mkdirSync(OUT, { recursive: true });
const D = { photo: 5, brief: 20, palette: 8, compare: 10, reasons: 5, ...(cfg.dwell || {}) };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await chromium.launch(); const page = await (await browser.newContext({ viewport: { width: 1360, height: 1000 } })).newPage();
const errors = []; page.on('pageerror', (e) => errors.push(String(e)));
const T = {}, t0 = Date.now(), mark = (k) => { T[k] = Date.now() - t0; };
const session = () => page.evaluate(() => fetch('/api/session').then((r) => r.json()));
const until = async (fn, ms = 900000) => { const a = Date.now(); for (;;) { const v = await fn(); if (v) return v; if (Date.now() - a > ms) throw new Error('timed out'); await sleep(200); } };
const done = (ids) => async () => { const s = await session(); return s.journey && ids.every((id) => s.journey.looks[id] && s.journey.looks[id].job.status !== 'running') && s; };
const readyTimes = async (ids, k) => { const seen = {}; await until(async () => { const s = await session();
  ids.forEach((id) => { const j = s.journey.looks[id] && s.journey.looks[id].job; if (j && j.status !== 'running' && !seen[id]) seen[id] = Date.now() - t0; }); return ids.every((id) => seen[id]); });
  Object.values(seen).sort((a, b) => a - b).forEach((t, i) => { T[`${k}_${i + 1}`] = t; }); };
const upload = async (name, file) => { const fc = page.waitForEvent('filechooser'); await page.getByRole('button', { name }).click(); await (await fc).setFiles(file); };
const out = { name: cfg.name, base: B, dwell: D, T, errors };
try {
  await page.goto(B + '/'); await page.getByRole('button', { name: /Add your full-length photo/ }).waitFor();
  mark('upload'); await upload(/Add your full-length photo/, cfg.photo); await page.getByText('Photo added').first().waitFor(); mark('upload_done');
  await sleep(D.photo * 1000);
  if (cfg.top) {
    await upload('+ Add clothes to pair', cfg.top.file);
    await page.waitForFunction(() => /· Yours|Use this top/.test(document.body.innerText), null, { timeout: 60000 });   // read from the photo, or asked
    if (await page.getByRole('button', { name: 'Use this top' }).count()) {
      await page.getByLabel('Color of your top').fill(cfg.top.color); await page.getByRole('button', { name: cfg.top.category, exact: true }).click(); await page.getByRole('button', { name: 'Use this top' }).click(); }
    await page.getByText(/· Yours/).first().waitFor(); mark('top_confirmed');
  }
  await page.getByLabel('Tell Calliope about the occasion').fill(cfg.brief.text); await sleep(D.brief * 1000);
  await page.getByRole('button', { name: 'Go' }).click(); await sleep(3500);
  for (const [chip, value] of cfg.brief.fill || []) { await page.getByText(chip + ' · needed').locator('xpath=ancestor::div[2]').getByRole('button', { name: 'Add' }).click(); await page.getByRole('button', { name: value }).click(); await sleep(300); }
  await page.getByRole('button', { name: 'Open fitting room' }).click(); mark('brief_confirm');
  const go = await until(async () => { for (const n of ['Show me my first looks', 'Continue without colors']) { const b = page.getByRole('button', { name: n }); if (await b.count() && await b.isVisible()) return n; } });
  mark('palette_shown'); await page.screenshot({ path: `${OUT}/${cfg.name}-palette.png` });
  await sleep(D.palette * 1000); await page.getByRole('button', { name: go }).click(); mark('palette_continue');
  await page.getByText('Round 1 of 2').first().waitFor();
  let s = await session(); const r1 = s.journey.round1, ch = s.journey.challenger; out.round1 = r1;
  await sleep(1200); await page.screenshot({ path: `${OUT}/${cfg.name}-round1-pending.png` });
  await readyTimes(r1, 'r1_ready'); await sleep(800); await page.screenshot({ path: `${OUT}/${cfg.name}-round1.png` });
  await sleep(D.compare * 1000); await page.getByRole('button', { name: 'Keep Look ' + cfg.r1, exact: true }).click(); mark('keep1');
  await page.getByText('Round 2 of 2').first().waitFor(); await readyTimes([ch], 'challenger_ready'); await page.screenshot({ path: `${OUT}/${cfg.name}-round2.png` });
  await sleep(D.compare * 1000); await page.getByRole('button', { name: cfg.r2 === 'new' ? `Keep Look ${ch} · new` : `Keep Look ${cfg.r1} · your pick` }).click(); mark('keep2');
  await page.getByText('What should I carry into your final looks?').waitFor(); await sleep(D.reasons * 1000);
  for (const k of cfg.reasons) await page.getByRole('button', { name: { colors: /The colors/, shape: /The (trouser|dress) shape/, feel: /The overall feel/ }[k] }).click();
  await page.getByRole('button', { name: cfg.reasons.length ? 'Show me final outfits' : 'Skip' }).click(); mark('reasons');
  await page.getByText('Your final outfits').first().waitFor(); await readyTimes(['P2'], 'final_ready'); await page.screenshot({ path: `${OUT}/${cfg.name}-final.png` });
  await sleep(D.compare * 1000); await page.getByRole('button', { name: 'Keep Proposal ' + cfg.fin }).click(); await page.getByText('Picked, not bought').first().waitFor(); mark('selected');
  s = await session(); out.sessionId = s.sessionId; out.looks = Object.fromEntries(Object.entries(s.journey.looks).map(([k, l]) => [k, { structure: l.structure, ids: l.items.map((i) => i.productId || 'owned'), job: l.job.status, note: l.job.note }]));
  out.brief = s.brief; out.palette = s.journey.palette && s.journey.palette.name; out.photoChecks = s.photo.checks;
} catch (e) { out.error = String(e.message || e); await page.screenshot({ path: `${OUT}/${cfg.name}-error.png` }).catch(() => {}); }
const w = (a, b) => (T[a] != null && T[b] != null ? Math.max(0, T[b] - T[a]) / 1000 : null);
out.visible = { palette: w('brief_confirm', 'palette_shown'), round1_first: w('palette_continue', 'r1_ready_1'), round1_all: w('palette_continue', `r1_ready_${(out.round1 || []).length}`),
  challenger: w('keep1', 'challenger_ready_1'), final: w('reasons', 'final_ready_1') };
out.visible.total = ['palette', 'round1_all', 'challenger', 'final'].map((k) => out.visible[k]).filter((x) => x != null).reduce((a, b) => a + b, 0);   // by key, never by value
out.totalMs = Date.now() - t0; fs.writeFileSync(`${OUT}/${cfg.name}.json`, JSON.stringify(out, null, 1)); await browser.close();
console.log(out.error ? 'FAILED ' + out.error : 'DONE', cfg.name, JSON.stringify(out.visible));
