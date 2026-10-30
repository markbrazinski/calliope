// The approved design is written as inline CSS strings (Calliope Prototype.dc.html). sx() turns one into a React style
// object, so the strings can be carried over verbatim. Cached: most are constants.
const cache = new Map();
export function sx(css) {
  let o = cache.get(css);
  if (!o) {
    o = {};
    for (const decl of css.split(';')) {
      const i = decl.indexOf(':'), k = decl.slice(0, i).trim(), v = decl.slice(i + 1).trim();
      if (i > 0 && k && v) o[k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    }
    if (cache.size > 4000) cache.clear();       // ponytail: dynamic strings (sizes, colors) are few; a cap is enough
    cache.set(css, o);
  }
  return o;
}
