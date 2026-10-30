// Pure view helpers shared by every screen. Everything shown comes from the session view.
export const MERCHANT = 'Livostyle';
export const SLOTS = ['top', 'dress', 'bottom', 'shoes', 'necklace'];
export const SLOT_LABEL = { top: 'Top', dress: 'Dress', bottom: 'Trousers', shoes: 'Shoes', necklace: 'Necklace' };
const r2 = (x) => Math.round(x * 100) / 100;

export const subtotal = (items) => r2(items.reduce((t, i) => t + (i.owned ? 0 : i.price), 0));
export const label = (i) => i.variant + ' ' + i.name.toLowerCase() + (i.noLongerSold ? ' · no longer sold at Livostyle' : '');   // left the catalog after it was picked
export const shortName = (i) => i.variant + ' ' + i.name.toLowerCase().replace(' necklace', '').replace(' trouser', 's').replace('fine ', '');

/** "$143.97 new · blouse is yours · within $200" (budget null = no limit). */
export function priceLine(items, budget) {
  const t = subtotal(items), owned = items.filter((i) => i.owned);
  return `$${t} new` + (owned.length ? ` · ${owned.map((o) => o.name.toLowerCase()).join(', ')} is yours` : '')
    + (budget == null ? ' · no budget limit' : t <= budget ? ` · within $${budget}` : ` · $${r2(t - budget)} over`);
}
export const bySlot = (items) => Object.fromEntries(items.map((i) => [i.slot, i]));
export const keys = (items) => Object.fromEntries(items.map((i) => [i.slot, i.key]));

// What a picture card shows for a job: only that composition's own job; never "ready" without its result.
const STAGE = { queued: 'Starting', owned_top: 'Layering your top', top: 'Putting on the top', dress: 'Putting on the dress', bottom: 'Putting on the trousers', shoes: 'Adding the shoes', necklace: 'Adding the necklace' };
const FAILED = { budget_cap_reached: 'The spending limit for pictures was reached.', submit_unknown: 'The picture service didn’t confirm it got this picture.',
  provider_timeout: 'The picture service is taking too long.', provider_error: 'The picture service couldn’t make it.' };
export const stageText = (j) => (j && STAGE[j.stage] ? STAGE[j.stage] + (j.stageIndex ? ' · step ' + j.stageIndex + ' of ' + (j.stageCount || 4) : '') : '');
export function picture(job, compositionId) {
  if (!job) return { status: 'ready', image: '' };
  if (job.compositionId !== compositionId) return { status: 'failed', image: '', note: 'This picture belongs to another outfit.' };
  if (job.status === 'running') return { status: 'pending', image: '', note: stageText(job), job };
  if (job.status === 'failed') return { status: 'failed', image: '', note: FAILED[job.errorCode] || '', job };
  const tag = /stub/.test(job.provider) ? 'SIMULATED PROVIDER (STUB) · NOT A TRY-ON' : 'YOUCAM PREVIEW';
  return { status: 'ready', image: job.imageUrl, imageTag: tag + (job.status === 'partial' ? ' · NECKLACE NOT SHOWN' : ''), job, partial: job.status === 'partial', note: job.note || '' };
}
export const running = (sv) => !!sv && ((sv.journey && (sv.journey.analysis.status === 'running' || Object.values(sv.journey.looks).some((l) => l.job.status === 'running')))
  || sv.versions.some((v) => v.previewJob && v.previewJob.status === 'running')
  || !!(sv.activeRevision && sv.activeRevision.candidates.some((c) => c.previewJob && c.previewJob.status === 'running')));

/** Words in free text that name a reason (production would use Nova; never invents a reason the words don't name). */
export function interpretText(text) {
  const t = (text || '').toLowerCase(), out = [];
  if (/colou?r|tone|shade|rust|cognac|deep|dark|warm|brown|chocolate|olive|taupe/.test(t)) out.push('colors');
  if (/trouser|pant|\bleg|shape|\bfit|\bcut\b|wide|slim|relaxed/.test(t)) out.push('shape');
  if (/\bfeel|vibe|overall|whole|polished|style/.test(t)) out.push('feel');
  return out;
}
export const list = (a) => (a.length > 1 ? a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1] : a[0] || '');
export const when = (t) => new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
