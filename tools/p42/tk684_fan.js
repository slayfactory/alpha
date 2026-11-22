// ── веер (TK-684, часть 2): все клетки перебора одной стороны на одном холсте; вставляется в tk593_template.html сборкой (tk593_page.py, метка __FANJS__).
// Данные — data/fan/ (tk684_fan.py: суточные ряды и метрики клеток из летописи), подгружаются по уровням при открытии вида.
// Клетка = «токен» <L|S><уровень><id строки metric в 36-ричной>: закреплённый образец и избранное живут в хеше, открываются в виде «Варианты».
const FAN = {index: null, packs: {}, sets: {}, busy: 0, err: null, view: null, hover: null};
const F_N = 0, F_USD = 1, F_SH = 2, F_DD = 3, F_GAP = 4, F_NW = 5, F_MW = 6, F_NL = 7, F_ML = 8, F_WORST = 9, F_SPLIT = 10;
const FAN_LV = [['1', 'единички'], ['2', 'пары'], ['3', 'тройки'], ['4', 'четвёрки'], ['split', 'вход двумя частями'], ['all', 'все']];
const FAN_SL = [['dd', 'Просадка ≤, $', F_DD, 'max', 1], ['gap', 'Перехай ≤, сут.', F_GAP, 'max', 1], ['n', 'Сделок ≥', F_N, 'min', 1], ['sum', 'Сумма ≥, $', F_USD, 'min', 1], ['sh', 'Шарп ≥', F_SH, 'min', 0.1]];
const FAN_TOP = [0, 20, 50, 100, 500], FAN_ROWS = 30, FAN_LINES = 300, FAN_SIM_N = 50;
const FAN_COL = {anchor: '#18181b', pin: '#c0265b', fav: ['#1baf7a', '#6250d6', '#b8860b', '#0e7490', '#a3369c', '#008300']};
const FAN_DEF = () => ({lv: '2', ax: null, top: 0, by: 'sh', dd: null, gap: null, n: null, sum: null, sh: null, pin: null, sim: false});
const FAN_TOK = /^[LS][0-5][0-9a-z]+$/;
const fanP = side => side === 'long' ? 'L' : 'S';
const fanTok = (P, i) => fanP(P.side) + P.level + P.ids[i].toString(36);
const fanFile = tok => tok[0] + '-' + tok[1];
const fanAxName = a => a === 'split' ? 'вход двумя частями' : a;

async function fanFetch(name, json) {
  const r = await fetch('data/fan/' + name + (json ? '.json.gz' : '.bin.gz'));
  if (!r.ok) throw new Error(name + ': HTTP ' + r.status);
  const resp = new Response(r.body.pipeThrough(new DecompressionStream('gzip')));
  return json ? resp.json() : resp.arrayBuffer();
}
async function fanLoad(names) {
  if (!FAN.index) { const r = await fetch('data/fan/index.json'); if (!r.ok) throw new Error('index.json: HTTP ' + r.status); FAN.index = await r.json(); }
  const I = FAN.index, D = I.days, NF = I.fields.length;
  await Promise.all(names(I).filter(n => I.files[n] && !FAN.packs[n]).map(async n => {
    const [buf, meta] = await Promise.all([fanFetch(n), fanFetch(n, true)]);
    const N = I.files[n].n, cents = new Int16Array(buf, 0, N * D), F = new Float32Array(buf, N * D * 2, N * NF), ids = new Uint32Array(buf, N * D * 2 + N * NF * 4, N), cum = new Float32Array(N * D), mn = new Float32Array(N), mx = new Float32Array(N);
    for (let i = 0; i < N; i++) { let c = 0, lo = 0, hi = 0; for (let d = 0; d < D; d++) { c += cents[i * D + d]; const v = c / 100; cum[i * D + d] = v; if (v < lo) lo = v; if (v > hi) hi = v; } mn[i] = lo; mx[i] = hi; }
    FAN.packs[n] = {side: I.files[n].side, level: I.files[n].level, N, cents, cum, mn, mx, F, ids, keys: meta.k, axes: meta.a, byId: null};
  }));
}
// файлы стороны под выбранный уровень (база стороны лежит в уровне 0 и нужна всегда)
const fanNames = (side, lv) => I => Object.keys(I.files).filter(n => n[0] === fanP(side) && (lv === 'all' || I.files[n].level === 0 || (lv === 'split' ? I.files[n].split > 0 : I.files[n].level === +lv)));
function fanCell(tok) {      // {P, i} по токену или null, пока пакет не загружен
  const P = FAN.packs[fanFile(tok)]; if (!P) return null;
  if (!P.byId) { P.byId = new Map(); for (let i = 0; i < P.N; i++) P.byId.set(P.ids[i], i); }
  const i = P.byId.get(parseInt(tok.slice(2), 36));
  return i == null ? null : {P, i};
}
const fanAnchor = side => { const a = FAN.index.anchor[side]; return fanCell(fanP(side) + a.level + a.id.toString(36)); };
function fanSet(side, lv) {
  const key = side + '/' + lv; if (FAN.sets[key]) return FAN.sets[key];
  const NF = FAN.index.fields.length, sp = [], si = [];
  fanNames(side, lv)(FAN.index).forEach(n => { const P = FAN.packs[n]; for (let i = 0; i < P.N; i++) if (lv === 'all' || (lv === 'split' ? P.F[i * NF + F_SPLIT] > 0 : P.level === +lv)) { sp.push(P); si.push(i); } });
  const lim = FAN_SL.map(([, , fi]) => { let lo = Infinity, hi = -Infinity; for (let j = 0; j < sp.length; j++) { const v = sp[j].F[si[j] * NF + fi]; if (v < lo) lo = v; if (v > hi) hi = v; } return [lo, hi]; });
  const ax = new Set(); sp.forEach((P, j) => P.axes[si[j]].forEach(a => ax.add(a[0])));
  return (FAN.sets[key] = {n: sp.length, sp, si, lim, ax: [...ax]});
}
function fanM(c) {      // метрики клетки из пакета — числа летописи
  const NF = FAN.index.fields.length, F = c.P.F, o = c.i * NF, n = F[o + F_N], nw = F[o + F_NW], nl = F[o + F_NL];
  return {n, sum: F[o + F_USD], mean: n ? F[o + F_USD] / n : null, sharpe: Number.isNaN(F[o + F_SH]) ? null : F[o + F_SH], dd: F[o + F_DD], ddp: F[o + F_DD] / FAN.index.margin * 100, gap: F[o + F_GAP],
    rr: nw && nl && F[o + F_ML] < 0 ? F[o + F_MW] / Math.abs(F[o + F_ML]) : null, worst: Number.isNaN(F[o + F_WORST]) ? null : F[o + F_WORST]};
}
// клетка веера как вариант вида «Варианты»: суточный ряд вместо сделок (точка — конец суток входа)
function fanVariant(tok) {
  const c = fanCell(tok); if (!c) return null;
  const D = FAN.index.days, NF = FAN.index.fields.length, F = c.P.F, o = c.i * NF, d = [], v = [], t = [];
  for (let k = 0; k < D; k++) { const x = c.P.cents[c.i * D + k] / 100; if (x) { d.push(k); v.push(x); t.push(V.start_ms + (k + 1) * 86400e3 - 1); } }
  return {id: tok, side: c.P.side, group: 'fan', label: c.P.keys[c.i], key: c.P.keys[c.i], status: c.P.level ? 'подобрано на этих данных' : '', d, v, t, m: fanM(c),
    agg: {n: F[o + F_N], sum: F[o + F_USD], nw: F[o + F_NW], sw: F[o + F_NW] * F[o + F_MW], nl: F[o + F_NL], sl: F[o + F_NL] * F[o + F_ML], worst: F[o + F_WORST]}};
}
// догрузить пакеты токенов (образец, избранное, выбранные в «Вариантах») и перерисовать вид
function fanNeed(toks) {
  const miss = [...new Set(toks.filter(t => t && FAN_TOK.test(t) && !FAN.packs[fanFile(t)]).map(fanFile))];
  if (!miss.length || FAN.err) return false;
  fanLoad(() => miss).then(() => { if (ST.tab === 'equity') renderEquity(); }, e => { FAN.err = String(e.message || e); if (ST.tab === 'equity') renderEquity(); });
  return true;
}
const fanFavs = side => ST.fav.filter(t => t[0] === fanP(side));

// параметры вида в хеше: #equity/fan/<сторона>/lv=2;ax=rf;top=100;by=sum;dd=60;gap=30;n=100;sum=0;sh=3;pin=<токен>;sim=1
function fanParse(s) {
  const f = FAN_DEF();
  (s || '').split(';').forEach(kv => {
    const [k, v] = kv.split('=');
    if (k === 'lv' && FAN_LV.some(x => x[0] === v)) f.lv = v;
    else if (k === 'ax' && v) f.ax = decodeURIComponent(v);
    else if (k === 'top' && FAN_TOP.includes(+v)) f.top = +v;
    else if (k === 'by' && (v === 'sum' || v === 'sh')) f.by = v;
    else if (FAN_SL.some(x => x[0] === k) && v !== '' && Number.isFinite(+v)) f[k] = +v;
    else if (k === 'pin' && FAN_TOK.test(v)) f.pin = v;
    else if (k === 'sim') f.sim = v === '1';
  });
  return f;
}
function fanHash() {
  const f = ST.fan, d = FAN_DEF(), out = ['lv=' + f.lv];
  if (f.ax) out.push('ax=' + encodeURIComponent(f.ax));
  if (f.top) out.push('top=' + f.top);
  if (f.by !== d.by) out.push('by=' + f.by);
  FAN_SL.forEach(([k]) => { if (f[k] != null) out.push(k + '=' + f[k]); });
  if (f.pin) out.push('pin=' + f.pin);
  if (f.sim && f.pin) out.push('sim=1');
  return out.join(';');
}

// видимый набор: фильтры и ползунки → порядок (сходство с образцом или Шарп / сумма) → топ N
function fanSelect(S, side) {
  const f = ST.fan, I = FAN.index, NF = I.fields.length, D = I.days, axi = f.ax ? I.axes.findIndex(a => a.name === f.ax) : -1, pass = [];
  for (let j = 0; j < S.n; j++) {
    const P = S.sp[j], o = S.si[j] * NF, F = P.F;
    if (f.dd != null && F[o + F_DD] > f.dd) continue;
    if (f.gap != null && F[o + F_GAP] > f.gap) continue;
    if (f.n != null && F[o + F_N] < f.n) continue;
    if (f.sum != null && F[o + F_USD] < f.sum) continue;
    if (f.sh != null && !(F[o + F_SH] >= f.sh)) continue;
    if (axi >= 0 && !P.axes[S.si[j]].some(a => a[0] === axi)) continue;
    pass.push(j);
  }
  const pin = f.pin && f.pin[0] === fanP(side) ? fanCell(f.pin) : null, score = new Float64Array(S.n);
  if (f.sim && pin) {
    // сходство формы: корреляция накопленных суточных рядов минус штраф за разницу просадки и перехая (в долях)
    const a = pin.P.cum.subarray(pin.i * D, pin.i * D + D), pm = fanM(pin); let ma = 0; for (let d = 0; d < D; d++) ma += a[d]; ma /= D;
    let va = 0; for (let d = 0; d < D; d++) va += (a[d] - ma) * (a[d] - ma);
    const rel = (x, y) => Math.abs(x - y) / Math.max(Math.abs(x), Math.abs(y), 1e-9);
    pass.forEach(j => {
      const P = S.sp[j], b = P.cum, o = S.si[j] * D; let mb = 0; for (let d = 0; d < D; d++) mb += b[o + d]; mb /= D;
      let vb = 0, ab = 0; for (let d = 0; d < D; d++) { const y = b[o + d] - mb; vb += y * y; ab += (a[d] - ma) * y; }
      const F = P.F, q = S.si[j] * NF;
      score[j] = (va > 0 && vb > 0 ? ab / Math.sqrt(va * vb) : 0) - 0.25 * rel(F[q + F_DD], pm.dd) - 0.25 * rel(F[q + F_GAP], pm.gap);
    });
  } else {
    const fi = f.by === 'sum' ? F_USD : F_SH;
    pass.forEach(j => { const v = S.sp[j].F[S.si[j] * NF + fi]; score[j] = Number.isNaN(v) ? -Infinity : v; });
  }
  const order = pass.slice().sort((x, y) => score[y] - score[x]);
  const top = f.sim && pin ? (f.top || FAN_SIM_N) : f.top;
  return {pass: pass.length, shown: top ? order.slice(0, top) : order, score, sim: !!(f.sim && pin), pin, axi};
}

const fanRGB = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
function fanHue(i, k) {      // цвет значения признака: k оттенков по кругу
  const hh = (i * 360 / Math.max(k, 1) + 210) % 360, s = 0.72, l = 0.45, a = s * Math.min(l, 1 - l), g = n => { const q = (n + hh / 30) % 12; return Math.round(255 * (l - a * Math.max(-1, Math.min(q - 3, 9 - q, 1)))); };
  return [g(0), g(8), g(4)];
}
// холст: плотность линий (накопление в сетку) или сами линии, когда их немного; поверх — база, избранное, образец, линия под курсором
function fanDraw() {
  const X = FAN.view; if (!X) return;
  const {S, sel, side, cv, ov} = X, I = FAN.index, D = I.days, w = X.w, hh = X.h, m = {l: 50, r: 14, t: 12, b: 26}, pw = w - m.l - m.r, ph = hh - m.t - m.b;
  const extra = [X.anchor, sel.pin, ...fanFavs(side).map(fanCell)].filter(Boolean);
  let lo = 0, hi = 0;
  for (let q = 0; q < sel.shown.length; q++) { const j = sel.shown[q], P = S.sp[j], i = S.si[j]; if (P.mn[i] < lo) lo = P.mn[i]; if (P.mx[i] > hi) hi = P.mx[i]; }
  extra.forEach(c => { if (c.P.mn[c.i] < lo) lo = c.P.mn[c.i]; if (c.P.mx[c.i] > hi) hi = c.P.mx[c.i]; });
  const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  [cv, ov].forEach(c => { c.width = Math.round(w * dpr); c.height = Math.round(hh * dpr); c.style.height = hh + 'px'; });
  const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, hh);
  const Y = v => m.t + (hi - v) / (hi - lo) * ph, Xd = d => m.l + d / D * pw;
  Object.assign(X, {lo, hi, m, pw, ph, Y, Xd});
  g.font = '11px Inter,ui-sans-serif,system-ui,sans-serif'; g.fillStyle = '#71717a'; g.strokeStyle = '#efeff1'; g.lineWidth = 1;
  ticks(lo, hi, 5).forEach(v => { g.beginPath(); g.moveTo(m.l, Y(v) + .5); g.lineTo(w - m.r, Y(v) + .5); g.stroke(); g.textAlign = 'right'; g.fillText(num(v, 0), m.l - 7, Y(v) + 3.5); });
  g.textAlign = 'center'; for (let d = 0; d <= D; d += w < 560 ? 28 : 14) g.fillText(fday(V.start_ms + d * 86400e3), Xd(d), hh - m.b + 15);
  const base = fanRGB(V_COL[side]), axv = sel.axi >= 0 ? I.axes[sel.axi].vals.length : 0;
  const colOf = j => { if (sel.axi < 0) return base; const a = S.sp[j].axes[S.si[j]].find(x => x[0] === sel.axi); return a ? fanHue(a[1], axv) : base; };
  if (sel.shown.length > FAN_LINES) {
    const gw = Math.min(Math.round(pw), D * 3), gh = Math.round(ph), cnt = new Uint32Array(gw * gh), mix = axv ? new Float32Array(gw * gh * 3) : null, ky = gh / (hi - lo);
    for (let s = 0; s < sel.shown.length; s++) {
      const j = sel.shown[s], b = S.sp[j].cum, o = S.si[j] * D, c = mix && colOf(j); let y0 = (hi - 0) * ky, x0 = 0;
      for (let d = 0; d < D; d++) {
        const y1 = (hi - b[o + d]) * ky, x1 = (d + 1) * gw / D, dy = (y1 - y0) / (x1 - x0);
        for (let x = Math.ceil(x0); x < x1 && x < gw; x++) {
          let y = (y0 + dy * (x - x0)) | 0; if (y < 0) y = 0; else if (y >= gh) y = gh - 1;
          const q = y * gw + x; cnt[q]++;
          if (mix) { mix[q * 3] += c[0]; mix[q * 3 + 1] += c[1]; mix[q * 3 + 2] += c[2]; }
        }
        y0 = y1; x0 = x1;
      }
    }
    let mx = 1; for (let q = 0; q < cnt.length; q++) if (cnt[q] > mx) mx = cnt[q];
    const img = new ImageData(gw, gh), px = img.data, lm = Math.log1p(mx);
    for (let q = 0; q < cnt.length; q++) {
      const c = cnt[q]; if (!c) continue;
      if (mix) { px[q * 4] = mix[q * 3] / c; px[q * 4 + 1] = mix[q * 3 + 1] / c; px[q * 4 + 2] = mix[q * 3 + 2] / c; } else { px[q * 4] = base[0]; px[q * 4 + 1] = base[1]; px[q * 4 + 2] = base[2]; }
      px[q * 4 + 3] = 255 * (0.12 + 0.88 * Math.log1p(c) / lm);
    }
    const off = document.createElement('canvas'); off.width = gw; off.height = gh; off.getContext('2d').putImageData(img, 0, 0);
    g.drawImage(off, m.l, m.t, pw, ph);
  } else {
    g.lineWidth = 1; g.globalAlpha = sel.shown.length > 60 ? 0.45 : 0.75;
    sel.shown.forEach(j => { const c = colOf(j); g.strokeStyle = `rgb(${c[0]},${c[1]},${c[2]})`; fanPath(g, X, {P: S.sp[j], i: S.si[j]}); });
    g.globalAlpha = 1;
  }
  g.strokeStyle = '#a1a1aa'; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(m.l, Y(0) + .5); g.lineTo(w - m.r, Y(0) + .5); g.stroke(); g.setLineDash([]);
  fanOverlay();
  cv.dataset.n = sel.shown.length; cv.dataset.pass = sel.pass; cv.dataset.total = S.n; cv.dataset.mode = sel.shown.length > FAN_LINES ? 'density' : 'lines';
}
function fanPath(g, X, c) {
  const D = FAN.index.days, b = c.P.cum, o = c.i * D;
  g.beginPath(); g.moveTo(X.Xd(0), X.Y(0)); for (let d = 0; d < D; d++) g.lineTo(X.Xd(d + 1), X.Y(b[o + d])); g.stroke();
}
function fanOverlay() {
  const X = FAN.view, g = X.ov.getContext('2d'), dpr = X.ov.width / X.w;
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, X.w, X.h); g.lineJoin = 'round';
  const line = (c, col, lw) => { g.strokeStyle = '#fff'; g.lineWidth = lw + 2; fanPath(g, X, c); g.strokeStyle = col; g.lineWidth = lw; fanPath(g, X, c); };
  fanFavs(X.side).forEach((t, k) => { const c = fanCell(t); if (c) line(c, FAN_COL.fav[k % FAN_COL.fav.length], 1.8); });
  if (X.anchor) line(X.anchor, FAN_COL.anchor, 2.6);
  if (X.sel.pin) line(X.sel.pin, FAN_COL.pin, 2.6);
  if (FAN.hover) line(FAN.hover, V_COL[X.side], 2);
}
// ближайшая к курсору линия видимого набора (по значению в сутках под курсором)
function fanNearest(ev) {
  const X = FAN.view, r = X.ov.getBoundingClientRect(), x = (ev.clientX - r.left) * X.w / r.width, y = (ev.clientY - r.top) * X.h / r.height, D = FAN.index.days;
  if (x < X.m.l - 4 || x > X.w - X.m.r + 4 || !X.sel.shown.length) return null;
  const d = Math.max(0, Math.min(D - 1, Math.round((x - X.m.l) / X.pw * D) - 1)), v = X.hi - (y - X.m.t) / X.ph * (X.hi - X.lo);
  let best = -1, bd = Infinity;
  X.sel.shown.forEach(j => { const q = Math.abs(X.S.sp[j].cum[X.S.si[j] * D + d] - v); if (q < bd) { bd = q; best = j; } });
  return best < 0 ? null : {P: X.S.sp[best], i: X.S.si[best], d};
}
function fanTip(c, ev) {
  const mm = fanM(c), D = FAN.index.days;
  tip.innerHTML = `<div class="t">${c.P.keys[c.i]}</div>` + (c.d == null ? '' : `<div class="r"><span>${fday(V.start_ms + c.d * 86400e3)}</span><span>${money(c.P.cum[c.i * D + c.d])} $</span></div>`)
    + [['Сделок', V_FMT.n(mm.n)], ['Сумма, $', V_FMT.sum(mm.sum)], ['Шарп (год)', mm.sharpe == null ? '—' : V_FMT.sharpe(mm.sharpe)], ['Просадка, $', V_FMT.dd(mm.dd)], ['Перехай, сут.', mm.gap]].map(([k, v]) => `<div class="r"><span>${k}</span><span>${v}</span></div>`).join('');
  tip.style.display = 'block'; moveTip(ev);
}
function fanPin(tok) { ST.fan.pin = ST.fan.pin === tok ? null : tok; if (!ST.fan.pin) ST.fan.sim = false; setHash(); renderEquity(); }
function fanFav(tok) { ST.fav = ST.fav.includes(tok) ? ST.fav.filter(t => t !== tok) : ST.fav.concat([tok]); setHash(); renderEquity(); }
function fanOpen(tok) {      // открыть клетку в виде «Варианты»; если на другой стороне что-то выбрано — сразу «шорт + лонг»
  const side = tok[0] === 'L' ? 'long' : 'short';
  ST[side === 'long' ? 'vl' : 'vs'] = tok; ST.view = 'var'; ST.eq = (side === 'long' ? ST.vs : ST.vl) ? 'both' : side;
  setHash(); renderEquity(); window.scrollTo(0, 0);
}
function fanTable(X) {
  const {S, sel, side} = X, rows = [], seen = new Set();
  const add = (c, kind) => { if (!c) return; const tok = fanTok(c.P, c.i); if (seen.has(tok)) return; seen.add(tok); rows.push([c, kind, tok]); };
  add(X.anchor, 'anchor'); add(sel.pin, 'pin'); fanFavs(side).forEach(t => add(fanCell(t), 'fav'));
  sel.shown.slice(0, FAN_ROWS).forEach(j => add({P: S.sp[j], i: S.si[j], j}, 'row'));
  const tb = h('table', {class: 'eqt fan-t', 'data-fantable': '1'});
  tb.append(h('thead', {}, h('tr', {}, [h('th', {}, ''), h('th', {}, 'Линия'), ...V_COLS.map(([, t]) => h('th', {class: 'num'}, t)), sel.sim ? h('th', {class: 'num'}, 'Сходство') : null])));
  const body = h('tbody'), favs = fanFavs(side);
  rows.forEach(([c, kind, tok]) => {
    const st = fanM(c), fav = ST.fav.includes(tok), col = kind === 'anchor' ? FAN_COL.anchor : tok === ST.fan.pin ? FAN_COL.pin : fav ? FAN_COL.fav[favs.indexOf(tok) % FAN_COL.fav.length] : V_COL[side];
    const star = h('button', {class: 'fan-star', 'data-fav': tok, 'aria-pressed': String(fav), title: fav ? 'убрать из избранного' : 'в избранное'}, fav ? '★' : '☆');
    star.onclick = ev => { ev.stopPropagation(); fanFav(tok); };
    const tr = h('tr', {'data-tok': tok, 'data-kind': kind, class: (kind === 'anchor' ? 'base' : '') + (tok === ST.fan.pin ? ' pin' : '')}, [h('td', {class: 'fan-s'}, star),
      h('td', {'data-f': 'name'}, [h('span', {class: 'sw', style: `border-color:${col}`}), kind === 'anchor' ? V_BY.get(V.default[side]).label : c.P.keys[c.i]]),
      ...V_COLS.map(([k, lab]) => h('td', {class: 'num', 'data-f': k, 'data-lab': lab, 'data-v': st[k] == null ? '' : st[k]}, st[k] == null ? '—' : V_FMT[k](st[k]))),
      sel.sim ? h('td', {class: 'num', 'data-f': 'sim', 'data-lab': 'Сходство'}, c.j == null ? '' : num(sel.score[c.j], 2)) : null]);
    tr.onclick = () => fanPin(tok);
    tr.onmouseenter = () => { FAN.hover = c; fanOverlay(); }; tr.onmouseleave = () => { FAN.hover = null; fanOverlay(); };
    body.append(tr);
  });
  tb.append(body);
  return h('div', {style: 'overflow-x:auto'}, tb);
}
function fanView(side, width) {
  const f = ST.fan, box = h('div', {'data-fanview': side});
  const lv = h('div', {class: 'tabs', role: 'tablist', id: 'fan-lv'});
  FAN_LV.forEach(([k, t]) => { const b = h('button', {role: 'tab', 'data-lv': k, 'aria-selected': String(f.lv === k)}, t); b.onclick = () => { ST.fan.lv = k; FAN_SL.forEach(([s]) => { ST.fan[s] = null; }); ST.fan.ax = null; setHash(); renderEquity(); }; lv.append(b); });
  const ctl = h('div', {class: 'fan-ctl'}, lv); box.append(ctl);
  FAN.view = null; FAN.hover = null;
  const names = fanNames(side, f.lv), ready = FAN.index && names(FAN.index).every(n => FAN.packs[n]);
  if (FAN.err) { box.append(h('div', {class: 'fan-msg', 'data-fanerr': '1'}, 'Данные не загрузились: ' + FAN.err)); return box; }
  if (!ready) {
    box.append(h('div', {class: 'fan-msg', 'data-fanload': '1'}, 'Загрузка…'));
    fanLoad(names).then(() => { if (ST.tab === 'equity' && ST.view === 'fan') renderEquity(); }, e => { FAN.err = String(e.message || e); renderEquity(); });
    return box;
  }
  if (fanNeed([f.pin, ...fanFavs(side)])) { box.append(h('div', {class: 'fan-msg', 'data-fanload': '1'}, 'Загрузка…')); return box; }
  const I = FAN.index, S = fanSet(side, f.lv);
  if (f.ax && !S.ax.includes(I.axes.findIndex(a => a.name === f.ax))) f.ax = null;
  // признак: № со вкладки «Признаки» (по имени), цвет линий — по значению
  const ftN = new Map(FEAT.sets.flatMap(s => s.groups.flatMap(g => g.features.map(x => [x.name.toLowerCase(), x.n]))));
  const axs = S.ax.map(i => ({name: I.axes[i].name, n: ftN.get(I.axes[i].name.toLowerCase())})).sort((a, b) => (a.n || 1e9) - (b.n || 1e9) || a.name.localeCompare(b.name));
  const sAx = h('select', {id: 'fan-ax', 'aria-label': 'признак'}, [h('option', {value: ''}, 'признак: все'), ...axs.map(a => h('option', {value: a.name}, (a.n ? '№ ' + a.n + ' · ' : '') + fanAxName(a.name)))]);
  sAx.value = f.ax || ''; sAx.onchange = () => { ST.fan.ax = sAx.value || null; setHash(); renderEquity(); };
  const sTop = h('select', {id: 'fan-top', 'aria-label': 'топ'}, FAN_TOP.map(n => h('option', {value: n}, n ? 'топ ' + n : 'все линии')));
  sTop.value = f.top; sTop.onchange = () => { ST.fan.top = +sTop.value; setHash(); renderEquity(); };
  const sBy = h('select', {id: 'fan-by', 'aria-label': 'порядок'}, [h('option', {value: 'sh'}, 'по Шарпу'), h('option', {value: 'sum'}, 'по сумме')]);
  sBy.value = f.by; sBy.onchange = () => { ST.fan.by = sBy.value; setHash(); renderEquity(); };
  ctl.append(sAx, sTop, sBy);
  const cnt = h('span', {class: 'fan-cnt', id: 'fan-cnt'}), sl = h('div', {class: 'fan-sl'});
  const X = {S, side, w: width, h: width < 520 ? 320 : 460, anchor: fanAnchor(side), cv: h('canvas', {'data-fan': 'base'}), ov: h('canvas', {'data-fan': 'over'})};
  const tblBox = h('div'), legBox = h('div');
  const refresh = () => {
    const t0 = performance.now(); X.sel = fanSelect(S, side); fanDraw();
    cnt.textContent = `линий ${ints(X.sel.shown.length)} из ${ints(S.n)}`;
    const items = [[swLine(FAN_COL.anchor, 3), 'база']];
    if (X.sel.pin) items.push([swLine(FAN_COL.pin, 3), 'образец']);
    fanFavs(side).forEach((t, k) => { const c = fanCell(t); if (c) items.push([swLine(FAN_COL.fav[k % FAN_COL.fav.length], 2), c.P.keys[c.i]]); });
    if (X.sel.axi >= 0) I.axes[X.sel.axi].vals.forEach((v, k, a) => { const c = fanHue(k, a.length); items.push([swLine(`rgb(${c[0]},${c[1]},${c[2]})`, 2), fanAxName(I.axes[X.sel.axi].name) + ' ' + v]); });
    legBox.replaceChildren(legend(items)); tblBox.replaceChildren(fanTable(X));
    X.cv.dataset.ms = (performance.now() - t0).toFixed(1);      // весь кадр: отбор, холст, таблица
  };
  let raf = 0;
  FAN_SL.forEach(([k, lab, fi, dir, step], q) => {
    const lo = Math.floor(S.lim[q][0] / step) * step, hi = Math.ceil(S.lim[q][1] / step) * step, def = dir === 'max' ? hi : lo, out = h('output', {}, '');
    const inp = h('input', {type: 'range', min: lo, max: hi, step, 'data-sl': k, 'aria-label': lab});
    const show = () => { out.textContent = num(+inp.value, step < 1 ? 1 : 0); };
    inp.value = f[k] == null ? def : Math.max(lo, Math.min(hi, f[k])); show();
    inp.oninput = () => { const v = +inp.value; ST.fan[k] = v === def ? null : +v.toFixed(1); show(); if (!raf) raf = requestAnimationFrame(() => { raf = 0; setHash(); refresh(); }); };
    sl.append(h('label', {class: 'fan-s1'}, [h('span', {}, lab), inp, out]));
  });
  box.append(sl, h('div', {class: 'fan-row'}, cnt));
  if (f.pin) {
    const pinned = fanCell(f.pin), bar = h('div', {class: 'fan-pin', 'data-pinbar': f.pin}, [h('span', {class: 'sw', style: `border-color:${FAN_COL.pin}`}), h('b', {}, pinned ? pinned.P.keys[pinned.i] : f.pin)]);
    const btn = (t, attr, fn, on) => { const b = h('button', {'data-act': attr, 'aria-pressed': on == null ? null : String(on)}, t); b.onclick = fn; bar.append(b); };
    btn('похожие', 'sim', () => { ST.fan.sim = !ST.fan.sim; setHash(); renderEquity(); }, f.sim);
    btn(ST.fav.includes(f.pin) ? 'убрать из избранного' : 'в избранное', 'fav', () => fanFav(f.pin));
    btn('открыть в эквити', 'open', () => fanOpen(f.pin));
    btn('снять', 'unpin', () => fanPin(f.pin));
    box.append(bar);
  }
  const wrap = h('div', {class: 'fan-cv', style: `height:${X.h}px`}, [X.cv, X.ov]);
  X.ov.addEventListener('mousemove', ev => { const c = fanNearest(ev); FAN.hover = c; fanOverlay(); if (c) fanTip(c, ev); else tip.style.display = 'none'; });
  X.ov.addEventListener('mouseleave', () => { FAN.hover = null; fanOverlay(); tip.style.display = 'none'; });
  X.ov.addEventListener('click', ev => { const c = fanNearest(ev); if (c) { tip.style.display = 'none'; fanPin(fanTok(c.P, c.i)); } });
  const card = h('div', {class: 'card eq-card', 'data-fancard': side}, h('div', {class: 'card-b'}, [legBox, wrap, tblBox]));
  box.append(card);
  FAN.view = X; refresh();
  return box;
}
