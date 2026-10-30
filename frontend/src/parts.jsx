import { useEffect, useRef } from 'react';
import { sx } from './sx.js';

/** The "c." avatar mark (identity spec). */
export function Mark({ size = 32 }) {
  return (
    <span aria-hidden="true" style={sx(`width:${size}px;height:${size}px;border-radius:50%;background:#151514;display:inline-flex;align-items:center;justify-content:center;flex:none`)}>
      <span style={sx(`font-family:'DM Serif Display',serif;font-size:${Math.round(size * 0.66)}px;line-height:1;color:#fcf9f2;margin-top:-3px`)}>c<span style={sx('color:#6fd3c8')}>.</span></span>
    </span>
  );
}

export const Wordmark = ({ size = 22 }) => (
  <span aria-label="Calliope" style={sx(`font-family:'DM Serif Display',serif;font-size:${size}px;line-height:1;letter-spacing:-0.01em;color:#151514`)}>calliope<span style={sx('color:#0d6b66')}>.</span></span>
);

export const MicIcon = ({ w = 14, h = 18 }) => (
  <svg aria-hidden="true" width={w} height={h} viewBox="0 0 14 18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" style={{ flex: 'none' }}>
    <rect x="4" y="1" width="6" height="10" rx="3" fill="currentColor" /><path d="M1.5 8.5a5.5 5.5 0 0 0 11 0" /><path d="M7 14v3" />
  </svg>
);

/** The standard share symbol (a box with an arrow leaving it). */
export const ShareIcon = () => (
  <svg aria-hidden="true" width="16" height="19" viewBox="0 0 16 19" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none' }}>
    <path d="M8 1.6v10.4" /><path d="M4.4 5.2 8 1.6l3.6 3.6" /><path d="M5.2 8.4H3.6a1.2 1.2 0 0 0-1.2 1.2v6.6a1.2 1.2 0 0 0 1.2 1.2h8.8a1.2 1.2 0 0 0 1.2-1.2V9.6a1.2 1.2 0 0 0-1.2-1.2h-1.6" />
  </svg>
);

/** Outfit picture (CalliopeLook): ready (real picture or a labelled color sketch), pending or failed. */
export function Look({ items = [], status = 'ready', label, height = '100%', image, imageTag, tag, note }) {
  const by = {}; items.forEach((i) => { by[i.slot] = i; });
  const hasImage = !!image && status === 'ready';
  const wide = (by.bottom && /wide/i.test(by.bottom.name)) || !!by.dress;            // a dress is drawn as one shape to the hem
  const names = items.map((i) => (i.variant ? i.variant + ' ' : '') + (i.name || '').toLowerCase()).join(', ');
  const alt = status === 'pending' ? 'Outfit picture in progress' : status === 'failed' ? 'Outfit picture could not be made' : (image ? 'Outfit preview: ' : 'Color sketch of the outfit: ') + names;
  const showTag = !(parseInt(height) < 160) && !hasImage && !(status === 'pending' && !tag);
  const showFigure = status === 'ready' && !hasImage;
  const top = by.top ? by.top.color : by.dress ? by.dress.color : '#ccc', bottom = by.bottom ? by.bottom.color : by.dress ? by.dress.color : '#bbb';
  return (
    <div role="img" aria-label={alt} style={sx(`position:relative;width:100%;height:${height};background:repeating-linear-gradient(135deg,#f0efea 0 7px,#e8e6e0 7px 14px);border:1px solid #d3d0c8;border-radius:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;box-sizing:border-box`)}>
      {showTag && <div style={sx("position:absolute;bottom:6px;left:7px;font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.05em;color:#55534d;background:rgba(252,249,242,.88);padding:1px 4px;border-radius:2px")}>{tag || 'COLOR SKETCH · NOT A TRY-ON'}</div>}
      {hasImage && <>
        <img src={image} alt={alt} style={sx('position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#efe8db')} />
        <div style={sx("position:absolute;bottom:6px;left:7px;font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.05em;color:#151514;background:rgba(252,249,242,.92);padding:1px 4px;border-radius:2px")}>{imageTag}</div>
      </>}
      {label && <div style={sx("position:absolute;top:6px;right:7px;font-family:'Schibsted Grotesk',sans-serif;font-size:11px;font-weight:600;color:#151514;background:#fcf9f2;padding:2px 6px;border-radius:4px;border:1px solid #ddd4c4;white-space:nowrap;max-width:calc(100% - 14px);overflow:hidden;text-overflow:ellipsis")}>{label}</div>}
      {showFigure && (
        <div style={sx('height:80%;aspect-ratio:0.36;max-width:64%;display:flex;flex-direction:column;align-items:center;gap:2%')}>
          <div style={sx('width:30%;aspect-ratio:1;border-radius:50%;background:#bdb9b0;flex:none')} />
          <div style={sx(`position:relative;width:100%;flex:33;background:${top};border-radius:${by.dress ? '14% 14% 0 0' : '14% 14% 4% 4%'}`)}>
            {by.necklace && <div style={sx(`position:absolute;top:6%;left:34%;width:32%;height:8%;border-radius:0 0 50% 50%;border:2px solid ${by.necklace.color};border-top:none;box-sizing:border-box`)} />}
          </div>
          <div style={sx(`width:${wide ? '100%' : '72%'};flex:52;display:flex;gap:${by.dress ? '0' : '4%'}`)}>
            <div style={sx(`flex:1;background:${bottom};border-radius:2px`)} /><div style={sx(`flex:1;background:${bottom};border-radius:2px`)} />
          </div>
          <div style={sx('width:100%;flex:5;display:flex;justify-content:center;gap:10%')}>
            {[0, 1].map((n) => <div key={n} style={sx(`width:34%;background:${by.shoes ? by.shoes.color : '#999'};border-radius:40% 40% 3px 3px;border:1px solid rgba(0,0,0,.15)`)} />)}
          </div>
        </div>
      )}
      {status === 'pending' && (
        <div style={sx("max-width:84%;display:flex;flex-direction:column;align-items:center;gap:4px;text-align:center;font-family:'Schibsted Grotesk',sans-serif;background:#fcf9f2;border:1px solid #ddd4c4;border-radius:6px;padding:10px 14px")}>
          <span style={sx('font-size:13.5px;font-weight:600;color:#151514')}>Making picture…</span>
          <span style={sx('font-size:12px;color:#55534d;line-height:1.4')}>{note || 'The pieces are already chosen.'}</span>
        </div>
      )}
      {status === 'failed' && (
        <div style={sx("max-width:84%;text-align:center;font-family:'Schibsted Grotesk',sans-serif;background:#fcf9f2;border:1.5px solid #b42318;border-radius:6px;padding:10px 12px")}>
          <div style={sx('font-size:13.5px;font-weight:600;color:#b42318')}>✕ Couldn't make this picture</div>
          {note && <div style={sx('font-size:12px;color:#55534d;margin-top:3px;line-height:1.4')}>{note}</div>}
          <div style={sx('font-size:12px;color:#55534d;margin-top:3px;line-height:1.4')}>The pieces and price below are still accurate.</div>
        </div>
      )}
    </div>
  );
}

/** Sheet: scrim + dialog. Escape closes, focus moves in, Tab stays inside, focus returns to the opener (OPEN-11). */
export function Sheet({ title, closeText = 'Close', onClose, centered, wide, children }) {
  const box = useRef(null), close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement, el = box.current;
    const focusable = () => [...el.querySelectorAll('button:not([disabled]),a[href],input,textarea,select,[tabindex]:not([tabindex="-1"])')];
    (focusable()[0] || el).focus();
    const key = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); close.current(); return; }
      if (e.key !== 'Tab') return;
      const f = focusable(); if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    };
    el.addEventListener('keydown', key);
    return () => { el.removeEventListener('keydown', key); if (opener && opener.focus && document.contains(opener)) opener.focus(); };
  }, []);
  const sh = centered;
  return (
    <div onClick={onClose} style={sx(`position:absolute;inset:0;background:rgba(21,21,20,.38);z-index:8;display:flex;align-items:${sh ? (wide ? 'center' : 'flex-end') : (wide ? 'stretch' : 'flex-end')};justify-content:${sh ? 'center' : (wide ? 'flex-end' : 'center')}`)}>
      <div ref={box} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
        style={sx(`background:#fcf9f2;width:${sh ? (wide ? '480px' : '100%') : (wide ? '420px' : '100%')};height:${sh ? 'auto' : (wide ? '100%' : 'auto')};max-height:${sh ? '92%' : (wide ? '100%' : '86%')};overflow:auto;border-radius:${sh ? (wide ? '8px' : '14px 14px 0 0') : (wide ? '0' : '14px 14px 0 0')};padding:18px;display:flex;flex-direction:column;gap:12px;outline:none`)}>
        <div style={sx('display:flex;justify-content:space-between;align-items:center;gap:10px')}>
          <div style={sx('font-weight:600;letter-spacing:-0.025em;font-size:17px')}>{title}</div>
          <button onClick={onClose} style={sx('border:none;background:none;color:#0d6b66;cursor:pointer;font-size:13.5px;text-decoration:underline;padding:8px 2px')}>{closeText}</button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** What a friend sees for a shared snapshot (SharedLookPage). Embedded in the share preview, or the page at /s/<id>. */
export function SharedLook({ share, embedded }) {
  const occ = (share.occasion || 'occasion').toLowerCase(), image = share.imageUrl || '';
  return (
    <>
      <div><h1 style={sx('font-size:26px;font-weight:600;letter-spacing:-0.025em;margin:0;line-height:1.15')}>{(/^[aeiou]/.test(occ) ? 'An ' : 'A ') + occ + ' look'}</h1>
        <p style={sx('margin:6px 0 0;font-size:14px;color:#55534d;line-height:1.5;text-wrap:pretty')}>Shared {share.at} · “{share.label}”. This is a saved snapshot. It won't change if the sender edits their look, and it can't be edited from here.</p></div>
      <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;align-items:start')}>
        <Look items={share.items} height={embedded ? '380px' : '460px'} image={image} imageTag={image ? (share.simulated ? 'SIMULATED PREVIEW (STUB) · NOT A TRY-ON' : 'TRY-ON PREVIEW') : ''} status="ready" />
        <div style={sx('background:#fcf9f2;border:1px solid #ddd4c4;border-radius:6px;padding:14px;display:flex;flex-direction:column;gap:4px')}>
          <h2 style={sx('font-size:16px;font-weight:600;letter-spacing:-0.025em;margin:0 0 4px')}>In this look</h2>
          <ul style={sx('list-style:none;margin:0;padding:0;display:flex;flex-direction:column')}>
            {share.items.map((i) => (
              <li key={i.slot} style={sx('display:flex;gap:10px;padding:9px 0;border-top:1px solid #ddd4c4;font-size:14px;align-items:center')}>
                <span style={sx(`width:28px;height:34px;border-radius:4px;background:${i.color};flex:none;border:1px solid #d6d3cc`)} />
                <span style={sx('flex:1;line-height:1.35')}>{i.label}<br /><span style={sx('font-size:12.5px;color:#55534d')}>{i.slotLabel} · {i.where}</span></span>
                <span style={sx('font-variant-numeric:tabular-nums;font-weight:600')}>{i.priceText}</span>
              </li>
            ))}
          </ul>
          <div style={sx('border-top:1px solid #151514;padding-top:8px;display:flex;justify-content:space-between;font-weight:700')}><span>New pieces</span><span style={sx('font-variant-numeric:tabular-nums')}>${share.subtotal}</span></div>
          <div style={sx('font-size:12.5px;color:#55534d;line-height:1.5;margin-top:4px')}>Prices as of the share date, before tax and shipping. Product pages on Livostyle may show different prices or availability.</div>
          {share.note && <div style={sx('font-size:12.5px;color:#55534d;line-height:1.5')}>{share.note}</div>}
        </div>
      </div>
    </>
  );
}
