// Real microphone audio through the live app's voice input, on desktop Chrome and a phone (Pixel 7 emulation, Chromium).
//   node tests/verify-speech.mjs <base url> <speech.wav> <control.json> <photo.jpg> [out dir]
// The server runs with CALLIOPE_TRANSCRIBE_ENDPOINT pointing at tests/fake_transcribe.py (same protocol as Amazon
// Transcribe; it returns words only when it received audible audio). Chromium's fake microphone plays <speech.wav>, so the
// page captures, converts and streams real audio. Checked: the transcript appears while speaking and stays in the box to
// check and edit; nothing is sent until Go; Go runs the same handler as typing (brief chips; a change request); Cancel
// restores the earlier text; silence, a provider failure, a denied microphone and a non-https page each say what to do,
// and typing still works. A real phone's Safari can't be automated here (no fake microphone in WebKit): see README.
import { chromium, devices } from '../node_modules/playwright/index.mjs';
import fs from 'fs'; import path from 'path';

const [B, WAV, CTL, PHOTO] = process.argv.slice(2, 6), OUT = path.resolve(process.argv[6] || 'artifacts/verify-speech');
fs.mkdirSync(OUT, { recursive: true });
const say = (text, mode = 'speech') => fs.writeFileSync(CTL, JSON.stringify({ text, mode }));
const fail = []; const ok = (c, m) => { if (!c) fail.push(m); console.log((c ? '  ✓ ' : '  ✗ ') + m); };
const launch = (extra = []) => chromium.launch({ args: ['--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${WAV}`, ...extra] });

async function startScreen(ctx) {
  const page = await ctx.newPage();
  await page.goto(B + '/?new=1'); await page.waitForTimeout(400); await page.mouse.click(30, 120);       // skip the greeting
  await page.getByLabel('Tell Calliope about the occasion').waitFor();
  return page;
}

async function drive(page) {          // the API, with this browser's session cookie, up to the selected look
  const r = page.request, j = async (m, p, d) => { const x = await r.fetch(B + p, { method: m, data: d, headers: { 'Content-Type': 'application/json' } }); return x.json(); };
  await r.fetch(B + '/api/photo?consent=1', { method: 'POST', data: fs.readFileSync(PHOTO), headers: { 'Content-Type': 'image/jpeg' } });
  await j('POST', '/api/journey/start', { occasion: 'dinner with friends', budget: 0, heels: true, structure: 'separates' });
  const until = async (f) => { for (let i = 0; i < 200; i++) { const v = await j('GET', '/api/session'); if (f(v)) return v; await page.waitForTimeout(150); } throw new Error('timeout'); };
  let v = await until((x) => x.journey.analysis.status !== 'running');
  await j('POST', '/api/journey/palette', { name: 'No palette' });
  await until((x) => Object.values(x.journey.looks).every((l) => l.job.status !== 'running'));
  await j('POST', '/api/journey/keep', { round: 1, id: 'A', pins: {} });
  await until((x) => x.journey.looks.C && x.journey.looks.C.job.status !== 'running');
  await j('POST', '/api/journey/keep', { round: 2, id: 'A', pins: {} });
  await j('POST', '/api/journey/finalize', { keys: [], text: '' });
  await until((x) => x.journey.looks.P2 && x.journey.looks.P2.job.status !== 'running');
  await j('POST', '/api/journey/choose', { id: 'A' });
  return until((x) => x.versions[0] && x.versions[0].previewJob.status !== 'running');
}

for (const [name, opts] of [['desktop', { viewport: { width: 1280, height: 860 } }], ['phone (Pixel 7)', devices['Pixel 7']]]) {
  console.log(name);
  const br = await launch(['--use-fake-ui-for-media-stream']), ctx = await br.newContext({ ...opts, permissions: ['microphone'] });
  const page = await startScreen(ctx), box = page.getByLabel('Tell Calliope about the occasion');
  const sent = []; page.on('request', (q) => { if (/\/journey\/start|\/revisions$/.test(q.url())) sent.push(q.url()); });

  say('Anniversary dinner, about $200, no heels');
  await page.getByRole('button', { name: 'Speak' }).click();
  await page.getByRole('button', { name: /Listening/ }).waitFor();
  await page.waitForFunction(() => document.querySelector('textarea').value.length > 0, null, { timeout: 8000 });
  const mid = await box.inputValue();
  await page.waitForTimeout(2600);
  await page.getByRole('button', { name: /Listening/ }).click();
  await page.getByRole('button', { name: 'Speak' }).waitFor({ timeout: 10000 });
  const heard = await box.inputValue();
  ok(mid.length > 0 && heard === 'Anniversary dinner, about $200, no heels', `${name}: words appear while speaking ("${mid}") and the final transcript is in the box`);
  ok(!(await page.getByText('Budget for new pieces').isVisible()) && /Check the words/.test(await page.textContent('body')), `${name}: nothing is read or sent by itself; it asks to check the words`);
  await box.fill(heard + ', in a dress');
  await page.getByRole('button', { name: 'Go', exact: true }).click();
  await page.getByText('Budget for new pieces').waitFor();
  ok(/\$200, give or take \$10/.test(await page.textContent('body')) && /A dress/.test(await page.textContent('body')), `${name}: the edited transcript goes through the same Go as typing (budget and the typed "in a dress" read)`);
  await page.screenshot({ path: path.join(OUT, `${name.split(' ')[0]}-brief.png`) });

  const prior = await box.inputValue();
  await page.getByRole('button', { name: 'Speak' }).click(); await page.getByRole('button', { name: /Listening/ }).waitFor(); await page.waitForTimeout(900);
  await page.getByRole('button', { name: 'Cancel' }).click(); await page.getByRole('button', { name: 'Speak' }).waitFor();
  ok((await box.inputValue()) === prior, `${name}: Cancel stops recording and puts back the earlier text`);

  say('');                                         // the service hears nothing it can transcribe
  await page.getByRole('button', { name: 'Speak' }).click(); await page.waitForTimeout(1500); await page.getByRole('button', { name: /Listening/ }).click();
  await page.waitForFunction(() => /didn.t (catch|hear) any/.test(document.body.innerText), null, { timeout: 10000 }).then(() => ok(true, `${name}: silence says so and suggests typing`), () => ok(false, `${name}: silence message`));
  say('anything', 'error');
  await page.getByRole('button', { name: 'Speak' }).click(); await page.waitForTimeout(1500);
  await page.waitForFunction(() => /BadRequestException|stopped|try again|type it/i.test(document.body.innerText), null, { timeout: 10000 })
    .then(() => ok(true, `${name}: a transcription failure is reported and recording stops`), () => ok(false, `${name}: failure message`));
  await page.getByRole('button', { name: 'Speak' }).waitFor({ timeout: 8000 });
  ok((await box.inputValue()).length > 0, `${name}: typed input is still there after the failure`);

  // a revision request on the selected look, by voice, through the same handler as typing
  await drive(page); await page.goto(B + '/'); await page.getByLabel('Ask for a change').waitFor({ timeout: 20000 });
  say('darker trousers please');
  await page.getByRole('button', { name: 'Speak' }).click(); await page.waitForTimeout(2600);
  await page.getByRole('button', { name: /Stop/ }).click(); await page.getByRole('button', { name: 'Speak' }).waitFor({ timeout: 10000 });
  const askVal = await page.getByLabel('Ask for a change').inputValue(), before = sent.filter((u) => /revisions$/.test(u)).length;
  ok(askVal === 'darker trousers please' && before === 0, `${name}: the spoken change request waits in the box ("${askVal}"), not sent`);
  await page.getByRole('button', { name: 'Go', exact: true }).click();
  await page.waitForFunction(() => /darker trousers|Darker trousers/.test(document.body.innerText) && /Keep|Alternative|couldn.t|option/i.test(document.body.innerText), null, { timeout: 20000 })
    .then(() => ok(sent.filter((u) => /revisions$/.test(u)).length === 1, `${name}: Go sends it through the change-request handler (one request)`), () => ok(false, `${name}: revision shown`));
  await page.screenshot({ path: path.join(OUT, `${name.split(' ')[0]}-revision.png`) });
  await br.close();

  // a denied microphone (the browser's own NotAllowedError, as when the shopper taps Block), no microphone at all, and a page that isn't secure
  const br2 = await launch(), c2 = await br2.newContext({ ...opts });
  const p2 = await c2.newPage();
  await p2.addInitScript(() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError')); });
  await p2.goto(B + '/?new=1'); await p2.waitForTimeout(400); await p2.mouse.click(30, 120);
  await p2.getByRole('button', { name: 'Speak' }).click();
  await p2.waitForFunction(() => /can.t use the microphone\. Allow it/i.test(document.body.innerText), null, { timeout: 10000 })
    .then(() => ok(true, `${name}: a denied microphone says how to allow it, or type`), () => ok(false, `${name}: denied message`));
  const p4 = await c2.newPage(); await p4.goto(B + '/'); await p4.waitForTimeout(400); await p4.mouse.click(30, 120);    // headless without a granted device
  await p4.getByRole('button', { name: 'Speak' }).click();
  await p4.waitForFunction(() => /can.t record from the microphone|No microphone was found/.test(document.body.innerText), null, { timeout: 10000 })
    .then(() => ok(true, `${name}: no usable microphone says so, and to type`), () => ok(false, `${name}: no-microphone message`));
  const p3 = await c2.newPage(); await p3.addInitScript(() => Object.defineProperty(window, 'isSecureContext', { value: false }));
  await p3.goto(B + '/'); await p3.waitForTimeout(400); await p3.mouse.click(30, 120);
  await p3.getByRole('button', { name: 'Speak' }).click();
  await p3.waitForFunction(() => /secure \(https\) page/.test(document.body.innerText), null, { timeout: 8000 })
    .then(() => ok(true, `${name}: an http page (a phone on the LAN) explains https is needed`), () => ok(false, `${name}: insecure message`));
  await p3.getByLabel('Tell Calliope about the occasion').fill('Wedding guest, up to $150');
  await p3.getByRole('button', { name: 'Go', exact: true }).click(); await p3.getByText('Budget for new pieces').waitFor();
  ok(true, `${name}: typing works without the microphone`);
  await br2.close();
}
console.log(fail.length ? `${fail.length} FAILED` : 'speech checks ok');
process.exit(fail.length ? 1 : 0);
