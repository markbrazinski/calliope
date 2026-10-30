import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './fonts.css';
import './styles.css';
import { sx } from './sx.js';
import { App } from './App.jsx';
import { liveApi } from './api.js';
import { SharedLook, Wordmark } from './parts.jsx';
import { when } from './model.js';

// One bundle, two entry points:  /  the app · /s/<id>  a friend's read-only view of a shared look.
const shr = (location.pathname.match(/^\/s\/(shr_[a-z2-7]{26})$/) || [])[1];

function Friend() {
  const [s, setS] = useState({ loading: true });
  useEffect(() => {
    fetch('/api/shares/' + shr).then((r) => r.json().then((b) => { if (!r.ok) throw new Error(b.message || 'This shared look isn’t available.'); return b; }))
      .then((x) => setS({ share: { ...x, at: when(x.createdAt) } })).catch((e) => setS({ gone: e.message || 'This shared look couldn’t be opened. Check your connection and try again.' }));
  }, []);
  return <div style={sx("min-height:100vh;background:#efe8db;font-family:'Schibsted Grotesk',sans-serif;color:#151514;padding:22px 16px 40px")}>
    <main style={sx('max-width:780px;margin:0 auto;display:flex;flex-direction:column;gap:16px')}>
      <div style={sx('display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap')}><Wordmark size={24} /></div>
      {s.loading && <div role="status" style={sx('font-size:14px;color:#55534d;padding:30px 0')}>Opening the shared look…</div>}
      {s.gone && <div role="status" style={sx('font-size:16px;line-height:1.5;padding:30px 0')}>{s.gone}</div>}
      {s.share && <SharedLook share={s.share} />}
    </main></div>;
}

createRoot(document.getElementById('root')).render(shr ? <Friend /> : <App api={liveApi()} />);
