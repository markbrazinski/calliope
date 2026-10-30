// The price-confirmation sheet in the page (stub providers; the /api/handoff answer is intercepted to inject price
// changes, so this checks the page's behaviour only; shopper.handoff itself is checked by tests/check_handoff.py).
//   node tests/verify-prices-ui.mjs [out dir]
import { chromium } from '../node_modules/playwright/index.mjs';
import { spawn, execFileSync } from 'child_process';
import fs from 'fs'; import os from 'os'; import path from 'path'; import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), PY = path.join(ROOT, '.venv/bin/python');
const OUT = path.resolve(process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'calliope-prices-'))), TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'calliope-prices-state-'));
fs.mkdirSync(OUT, { recursive: true }); execFileSync(PY, [path.join(ROOT, 'tests/make_images.py'), TMP], { stdio: 'ignore' });
const PORT = 8899, B = `http://127.0.0.1:${PORT}`, sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = []; const ok = (c, m) => { if (!c) fail.push(m); console.log((c ? '  ✓ ' : '  ✗ ') + m); };
const srv = spawn(PY, ['app.py'], { cwd: ROOT, env: { ...process.env, CALLIOPE_PROVIDER: 'stub', CALLIOPE_STUB_DELAY: '0', CALLIOPE_STATE_DIR: path.join(TMP, 'app'), CALLIOPE_ENGINE_DIR: path.join(TMP, 'engine'), PORT: String(PORT) }, stdio: 'ignore' });
for (let i = 0; i < 100; i++) { try { await fetch(B + '/api/status'); break; } catch (e) { await sleep(100); } }

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 1000 } });
  await ctx.addInitScript(() => { window.__opened = []; window.open = () => { const w = { closed: false, close() { this.closed = true; }, location: {} };
    Object.defineProperty(w.location, 'href', { set: (u) => window.__opened.push(u) }); return w; }; });
  const page = await ctx.newPage(); const errors = []; page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(B + '/'); await sleep(500);
  const j = async (m, p, d) => (await page.request.fetch(B + p, { method: m, data: d, headers: { 'Content-Type': 'application/json' } })).json();
  await page.request.fetch(B + '/api/photo?consent=1', { method: 'POST', data: fs.readFileSync(path.join(TMP, 'shopper-a.jpg')), headers: { 'Content-Type': 'image/jpeg' } });
  await j('POST', '/api/journey/start', { occasion: 'dinner with friends', budget: 250, heels: true, structure: 'separates' });
  const until = async (f) => { for (let i = 0; i < 300; i++) { const v = await j('GET', '/api/session'); if (f(v)) return v; await sleep(150); } throw new Error('timeout'); };
  await until((x) => x.journey.analysis.status !== 'running'); await j('POST', '/api/journey/palette', { name: 'No palette' });
  await until((x) => Object.values(x.journey.looks).every((l) => l.job.status !== 'running')); await j('POST', '/api/journey/keep', { round: 1, id: 'A', pins: {} });
  await until((x) => x.journey.looks.C && x.journey.looks.C.job.status !== 'running'); await j('POST', '/api/journey/keep', { round: 2, id: 'A', pins: {} });
  await j('POST', '/api/journey/finalize', { keys: [], text: '' }); await until((x) => x.journey.looks.P2 && x.journey.looks.P2.job.status !== 'running');
  await j('POST', '/api/journey/choose', { id: 'A' });
  const sv = await until((x) => x.versions[0] && x.versions[0].previewJob.status !== 'running');
  for (const it of sv.versions[0].items.filter((i) => i.sizes)) await j('PUT', '/api/sizes', { slot: it.slot, size: it.sizes.find((z) => !(it.unavailable || []).includes(z)) });

  let mode = 'increase'; const bodies = [];
  await page.route('**/api/handoff', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}'); bodies.push(body);
    const resp = await route.fetch(), real = await resp.json().catch(() => ({}));
    if (!resp.ok() || !(real.items || []).length) { console.log('  (real handoff answered', resp.status(), real.code || real.problems, '- passed through)'); return route.fulfill({ response: resp, json: real }); }
    const item = real.items.find((i) => i.slot !== 'necklace') || real.items[0];
    const was = item.shownPrice, now = mode === 'over' ? was + 200 : was + 5, sub = Math.round((real.subtotalShown - was + now) * 100) / 100;
    const changed = { ...real, priceChanges: [{ slot: item.slot, productId: item.productId, title: item.variantTitle, variantTitle: item.variantTitle, was, now, delta: now - was }],
      subtotalNow: sub, quote: 'pq_test_' + mode, cartUrl: null, needsConfirmation: true, confirmationExpired: false, budgetConflict: null };
    let res = changed;
    if (mode === 'unchanged') res = { ...real, priceChanges: [], needsConfirmation: false, budgetConflict: null, cartUrl: 'https://livostyle.com/cart/1:1', agree: true };
    else if (mode === 'over') res = body.acceptQuote === changed.quote && body.raiseBudgetTo >= sub ? { ...real, cartUrl: 'https://livostyle.com/cart/2:1', agree: true, needsConfirmation: false }
      : { ...changed, needsConfirmation: !body.acceptQuote, budgetConflict: { budget: 250, mode: 'firm', limit: 250, subtotal: sub, over: Math.round((sub - 250) * 100) / 100 } };
    else if (body.acceptQuote === changed.quote) res = { ...real, cartUrl: 'https://livostyle.com/cart/3:1', agree: true, needsConfirmation: false };
    await route.fulfill({ json: res });
  });
  await page.reload(); await page.getByText('Picked, not bought').first().waitFor();
  const bag = page.getByRole('button', { name: /(Add \d+ (items )?to (your )?|Sent to your )Livostyle bag/ }).first();

  await bag.click(); await page.getByText('Prices changed at Livostyle').waitFor();
  ok(/→/.test(await page.locator('body').innerText()) && /before tax and shipping/.test(await page.locator('body').innerText()), 'increase: the sheet shows old → new prices and both subtotals, before tax and shipping');
  ok((await page.evaluate(() => window.__opened.length)) === 0, 'increase: no cart opened before she accepts');
  await page.screenshot({ path: `${OUT}/prices-increase.png` });
  await page.getByRole('button', { name: /Accept these prices and open the bag/ }).click(); await sleep(800);
  ok(bodies.at(-1).acceptQuote === 'pq_test_increase' && (await page.evaluate(() => window.__opened)).includes('https://livostyle.com/cart/3:1'), 'increase: Accept sends that exact quote, then the cart opens');

  mode = 'over'; await bag.click(); await page.getByText(/over your budget/).waitFor();
  await page.screenshot({ path: `${OUT}/prices-over-budget.png` });
  ok(await page.getByRole('button', { name: /Change a piece instead/ }).isVisible(), 'over budget: she can change a piece instead');
  const opened0 = await page.evaluate(() => window.__opened.length);
  await page.getByRole('button', { name: /Raise my budget to \$[\d.]+ and open the bag/ }).click(); await sleep(800);
  const last = bodies.at(-1);
  ok(last.acceptQuote === 'pq_test_over' && last.raiseBudgetTo > 250 && (await page.evaluate(() => window.__opened)).includes('https://livostyle.com/cart/2:1') && (await page.evaluate(() => window.__opened.length)) === opened0 + 1,
    `over budget: raising the budget is explicit (raiseBudgetTo ${last.raiseBudgetTo}) and only then the cart opens`);

  mode = 'unchanged'; await bag.click(); await sleep(800);
  ok(!(await page.getByText('Prices changed at Livostyle').isVisible()) && (await page.evaluate(() => window.__opened)).includes('https://livostyle.com/cart/1:1'), 'unchanged: no sheet; the cart opens at once');
  ok(errors.length === 0, `no page errors (${errors.join(' | ')})`);
} catch (e) { fail.push('aborted: ' + e.message); console.log('ABORTED', e); }
await browser.close(); srv.kill('SIGKILL');
console.log(fail.length ? `FAIL · ${fail.length} failed` : 'price sheet checks ok'); process.exit(fail.length ? 1 : 0);
