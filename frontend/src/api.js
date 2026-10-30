// The application API served by app.py (/api/*). The session belongs to this browser through an HttpOnly cookie.
async function call(method, path, body) {
  const r = await fetch(path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.message || j.error || 'HTTP ' + r.status), { code: j.code, status: r.status });
  return j;
}
async function upload(path, file) {        // raw image bytes; 422 = the photo didn't pass a check (returned, not thrown)
  const r = await fetch(path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && r.status !== 422) throw Object.assign(new Error(j.message || 'HTTP ' + r.status), { code: j.code, status: r.status });
  return j;
}

export function liveApi() {
  const api = {
    pollMs: 1500,
    // Restore this browser's session, or start one (also for ?new=1, "start over").
    async boot(fresh) {
      if (!fresh) { const sv = await api.session(); if (sv.sessionId) return sv; }
      return api.newSession();
    },
    status: () => call('GET', '/api/status'),
    catalogRetry: () => call('POST', '/api/catalog/refresh', {}),          // retry a failed catalog setup (the server allows one build at a time)
    newSession: () => call('POST', '/api/sessions', {}),
    session: () => call('GET', '/api/session'),
    // The file picker opens inside the click that asked for it.
    pickFile: () => new Promise((res) => { const i = document.createElement('input'); i.type = 'file'; i.accept = 'image/jpeg,image/png';
      i.onchange = () => res(i.files && i.files[0] ? i.files[0] : null); i.click(); }),
    uploadPhoto: (file) => upload('/api/photo?consent=1', file),
    removePhoto: () => call('DELETE', '/api/photo'),
    uploadTop: (file, hint) => upload('/api/owned-top?hint=' + encodeURIComponent(hint || ''), file),
    confirmTop: (category, colorName) => call('PUT', '/api/owned-top', { category, colorName }),
    removeTop: () => call('DELETE', '/api/owned-top'),
    readBrief: (text) => call('POST', '/api/brief/read', { text }),          // Nova's reading (fields), or {interpreter: 'rules'}
    journeyStart: (brief) => call('POST', '/api/journey/start', brief),
    journeyPalette: (name) => call('POST', '/api/journey/palette', { name }),
    journeyKeep: (round, id, pins) => call('POST', '/api/journey/keep', { round, id, pins }),
    journeyFinalize: (keys, text) => call('POST', '/api/journey/finalize', { keys, text }),
    journeyChoose: (id) => call('POST', '/api/journey/choose', { id }),
    size: (slot, size) => call('PUT', '/api/sizes', { slot, size }),
    version: (index) => call('POST', '/api/version', { index }),
    ask: (text, versionId) => call('POST', '/api/revisions', { text, versionId }),
    answer: (id, choice) => call('POST', `/api/revisions/${id}/answer`, { choice }),
    accept: (id, candidateId) => call('POST', `/api/revisions/${id}/accept`, { candidateId }),
    dismiss: (id) => call('POST', `/api/revisions/${id}/dismiss`, {}),
    retry: (jobId, ack) => call('POST', `/api/jobs/${jobId}/retry`, { acknowledgePossibleDuplicateCharge: !!ack }),
    handoff: (versionId, confirm) => call('POST', '/api/handoff', { versionId, ...(confirm || {}) }),   // confirm: { acceptQuote, raiseBudgetTo }
    share: (versionId) => call('POST', '/api/shares', { versionId }),
    unshare: (id) => call('DELETE', `/api/shares/${id}`),
    publicShare: (id) => call('GET', `/api/shares/${id}`),
    shareUrl: (publicUrl) => location.origin + publicUrl,
    // The exact product page for this piece in the chosen size (stock read live by the server).
    async productLink(versionId, item) {
      if (item.sizes && !item.savedSize) return item.url;          // no size chosen yet: the product's own page
      const h = await api.handoff(versionId), l = h.items.find((x) => x.slot === item.slot);
      if (!l) throw new Error('Couldn’t find this size at Livostyle: ' + h.problems.join('; '));
      return l.url;
    },
  };
  return api;
}
