// ── «Сделки» (TK-741, В-192): сделки базы шорта без выхода по времени по кластерам сетапа; вставляется в tk593_template.html сборкой (tk593_page.py, метка __TRADESJS__).
// Данные — data/trades/ (tk741_pagedata.py): index.json (сделки, кластеры) и c/<№>.json (минутки сделки ±2 ч). Числа кластеров считаются здесь из сделок и сверяются клик-тестом с БД (check.json).
const TR = {idx: null, rows: [], per: 'main', cl: 0, sk: 'time', sd: 'd', open: null, cv: {}, err: null, busy: false};
const TR_F = [['r15', 'За 15 мин, %', v => num(v, 1, true)], ['r60', 'За час, %', v => num(v, 1, true)], ['r240', 'За 4 ч, %', v => num(v, 1, true)], ['rv60', 'Размах за час, %', v => num(v, 1)],
  ['near', 'У уровня, % минут', v => num(v * 100, 0)], ['touch', 'Касаний за 4 ч', v => String(v)], ['age', 'Возраст уровня, ч', v => num(v, 0)], ['turn', 'Оборот к обычному, ×', v => num(v, 1)],
  ['stab', 'Проколов за час', v => String(v)], ['hi24', 'До максимума суток, %', v => num(v, 1)], ['lo24', 'До минимума суток, %', v => num(v, 1)], ['btc60', 'BTC за час, %', v => num(v, 2, true)]];
const TR_PER = [['main', 'фев–13.11', ['FJ', 'SEL']], ['JAN', 'январь', ['JAN']], ['N1', '14–24.11', ['N1']], ['all', 'все', ['FJ', 'SEL', 'JAN', 'N1']]];
const trG4 = ms => new Date(ms + 4 * 3600e3);
const trDT = ms => { const d = trG4(ms); return `${pad2(d.getUTCDate())}.${pad2(d.getUTCMonth() + 1)} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
const trHM = ms => { const d = trG4(ms); return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
const trPx = v => { if (v == null) return '—'; const a = Math.abs(v), d = a >= 100 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 7; return num(v, d); };
const trUrl = f => 'data/trades/' + f + (TR.idx ? '?v=' + TR.idx.v : '');
function trRow(a) { const R = {}; TR.idx.cols.forEach((c, k) => { R[c] = a[k]; }); R.f = a[TR.idx.cols.indexOf('f')]; return R; }
async function trLoad() {
  if (TR.idx || TR.busy) return;
  TR.busy = true;
  try {
    const r = await fetch('data/trades/index.json', {cache: 'no-cache'});
    if (!r.ok) throw new Error('index.json: HTTP ' + r.status);
    TR.idx = await r.json();
    TR.rows = TR.idx.trades.map(trRow);
  } catch (e) { TR.err = String(e.message || e); }
  TR.busy = false;
  if (ST.tab === 'trades') renderTrades();
}
function trAgg(rs) {
  const v = rs.map(x => x.pnl), w = v.filter(x => x > 0), l = v.filter(x => x < 0), st = rs.filter(x => x.kind === 'stop').map(x => x.pnl), s = a => a.reduce((x, y) => x + y, 0);
  return {n: v.length, sum: s(v), mean: v.length ? s(v) / v.length : null, rr: w.length && l.length ? (s(w) / w.length) / Math.abs(s(l) / l.length) : null, stops: st.length, lps: st.length ? s(st) / st.length : null};
}
const trPerSet = () => TR_PER.find(p => p[0] === TR.per)[2];
const trInPer = () => TR.rows.filter(x => trPerSet().includes(x.per));
function trHash() { return '/' + TR.per + '/' + TR.cl + (TR.open != null ? '/' + TR.open : ''); }
function trParse(b, c, d) {
  TR.per = TR_PER.some(p => p[0] === b) ? b : 'main';
  TR.cl = /^\d+$/.test(c || '') ? +c : 0;
  TR.open = /^\d+$/.test(d || '') ? +d : null;
}
function trSortVal(x, k) { return k === 'time' ? x.entry_ms : k === 'sym' ? x.sym : k === 'pnl' ? x.pnl : k === 'cl' ? x.cluster : k === 'kind' ? x.kind : k === 'ep' ? x.entry_px : k === 'xp' ? x.exit_px : TR_F.some(f => f[0] === k) ? x.f[TR.idx.feats.indexOf(k)] : x.entry_ms; }

function trSummary(rows) {
  const I = TR.idx, all = trAgg(rows), tb = h('table', {class: 'eqt trs', 'data-trs': '1'});
  tb.append(h('thead', {}, h('tr', {}, [h('th', {}, 'Кластер'), ...['Сделок', 'Доля, %', 'Сумма, $', 'На сделку, $', 'R:R', 'Стопов', '$ на стоп'].map(t => h('th', {class: 'num'}, t))])));
  const body = h('tbody');
  const line = (cid, name, sub, a) => {
    const tr = h('tr', {'data-cl': cid, class: TR.cl === cid ? 'sel' : ''}, [
      h('td', {'data-f': 'name'}, [h('b', {}, name), sub ? h('div', {class: 'muted small'}, sub) : null]),
      h('td', {class: 'num', 'data-f': 'n', 'data-lab': 'Сделок'}, ints(a.n)), h('td', {class: 'num', 'data-f': 'share', 'data-lab': 'Доля, %'}, rows.length ? num(a.n / rows.length * 100, 0) : '—'),
      h('td', {class: 'num', 'data-f': 'sum', 'data-lab': 'Сумма, $'}, money(a.sum, 2)), h('td', {class: 'num', 'data-f': 'mean', 'data-lab': 'На сделку, $'}, a.mean == null ? '—' : money(a.mean, 2)),
      h('td', {class: 'num', 'data-f': 'rr', 'data-lab': 'R:R'}, a.rr == null ? '—' : num(a.rr, 2)), h('td', {class: 'num', 'data-f': 'stops', 'data-lab': 'Стопов'}, String(a.stops)),
      h('td', {class: 'num', 'data-f': 'lps', 'data-lab': '$ на стоп'}, a.lps == null ? '—' : money(a.lps, 2))]);
    tr.onclick = () => { TR.cl = TR.cl === cid ? 0 : cid; TR.open = null; setHash(); renderTrades(); };
    return tr;
  };
  body.append(line(0, 'Все кластеры', '', all));
  I.clusters.forEach(c => {
    const m = c.med;
    body.append(line(c.id, `К${c.id} · ${c.name}`, `медианы: за час ${num(m.r60, 1, true)} %, оборот ×${num(m.turn, 1)}, уровню ${num(m.age, 0)} ч, до минимума суток ${num(m.lo24, 1)} %`, trAgg(rows.filter(x => x.cluster === c.id))));
  });
  tb.append(body);
  return h('div', {style: 'overflow-x:auto'}, tb);
}

function trTable(rows) {
  const I = TR.idx, cols = [['time', 'Время (GMT+4)'], ['sym', 'Монета'], ['ep', 'Вход'], ['xp', 'Выход'], ['kind', 'Как вышли'], ['pnl', '$'], ['cl', 'Кластер'], ...TR_F.map(f => [f[0], f[1]])];
  const dir = TR.sd === 'a' ? 1 : -1, ix = k => I.feats.indexOf(k);
  const sorted = rows.slice().sort((a, b) => { const x = trSortVal(a, TR.sk), y = trSortVal(b, TR.sk); return (x < y ? -1 : x > y ? 1 : 0) * dir || a.entry_ms - b.entry_ms; });
  const tb = h('table', {class: 'eqt trt', 'data-trt': '1'});
  tb.append(h('thead', {}, h('tr', {}, cols.map(([k, t]) => { const th = h('th', {'data-sort': k, class: k === 'time' || k === 'sym' || k === 'kind' ? '' : 'num'}, t + (TR.sk === k ? (TR.sd === 'a' ? ' ↑' : ' ↓') : '')); th.onclick = () => { if (TR.sk === k) TR.sd = TR.sd === 'a' ? 'd' : 'a'; else { TR.sk = k; TR.sd = k === 'sym' || k === 'kind' ? 'a' : 'd'; } renderTrades(); }; return th; }))));
  const body = h('tbody');
  sorted.forEach(x => {
    const tr = h('tr', {'data-i': x.i, 'data-cl': x.cluster, class: TR.open === x.i ? 'sel' : ''}, [
      h('td', {'data-f': 'time', 'data-lab': 'Время (GMT+4)'}, trDT(x.entry_ms)), h('td', {'data-f': 'sym', 'data-lab': 'Монета'}, x.sym.replace(/USDT$/, '')),
      h('td', {class: 'num', 'data-f': 'ep', 'data-lab': 'Вход'}, trPx(x.entry_px)), h('td', {class: 'num', 'data-f': 'xp', 'data-lab': 'Выход'}, trPx(x.exit_px)),
      h('td', {'data-f': 'kind', 'data-lab': 'Как вышли'}, I.kinds[x.kind] || x.kind), h('td', {class: 'num', 'data-f': 'pnl', 'data-lab': '$'}, money(x.pnl, 2)),
      h('td', {class: 'num', 'data-f': 'cl', 'data-lab': 'Кластер'}, x.cluster ? 'К' + x.cluster : '—'),
      ...TR_F.map(([k, t, f]) => h('td', {class: 'num', 'data-f': k, 'data-lab': t}, x.f[ix(k)] == null ? '—' : f(x.f[ix(k)])))]);
    tr.onclick = () => { TR.open = x.i; setHash(); renderTrades(); const d = document.getElementById('tr-detail'); if (d) d.scrollIntoView({block: 'start', behavior: 'auto'}); };
    body.append(tr);
  });
  tb.append(body);
  return h('div', {style: 'overflow-x:auto'}, tb);
}

async function trCandles(i) {
  if (TR.cv[i]) return TR.cv[i];
  const r = await fetch(trUrl('c/' + i + '.json'));
  if (!r.ok) throw new Error('c/' + i + '.json: HTTP ' + r.status);
  return (TR.cv[i] = await r.json());
}
function trChart(x, cd, w) {
  const n = cd.o.length, hh = w < 520 ? 300 : 380, m = {l: 62, r: 14, t: 16, b: 28};
  const P = a => a / 1e8;
  let lo = Infinity, hi = -Infinity;
  for (let k = 0; k < n; k++) if (cd.o[k] != null) { lo = Math.min(lo, P(cd.l[k])); hi = Math.max(hi, P(cd.h[k])); }
  [x.lvl, x.entry_px, x.exit_px].forEach(v => { lo = Math.min(lo, v); hi = Math.max(hi, v); });
  const pad = (hi - lo) * 0.06 || hi * 0.001; lo -= pad; hi += pad;
  const X = k => m.l + (k + 0.5) / n * (w - m.l - m.r), Y = sc(lo, hi, hh - m.b, m.t), bw = Math.max(1, (w - m.l - m.r) / n * 0.7);
  const svg = el('svg', {viewBox: `0 0 ${w} ${hh}`, width: '100%', role: 'img', 'data-chart': 'trade'});
  ticks(lo, hi, 5).forEach(v => { svg.append(el('line', {x1: m.l, x2: w - m.r, y1: Y(v), y2: Y(v), stroke: '#efeff1'})); svg.append(el('text', {x: m.l - 7, y: Y(v) + 3.5, 'text-anchor': 'end'}, trPx(v))); });
  const step = n > 360 ? 60 : 30, first = Math.ceil((cd.t0 + 4 * 3600e3) / 60000 / step) * step - (cd.t0 + 4 * 3600e3) / 60000;
  for (let k = Math.round(first); k < n; k += step) svg.append(el('text', {x: X(k), y: hh - m.b + 15, 'text-anchor': 'middle'}, trHM(cd.t0 + k * 60000)));
  svg.append(el('line', {x1: m.l, x2: w - m.r, y1: hh - m.b, y2: hh - m.b, stroke: '#d4d4d8'}));
  for (let k = 0; k < n; k++) {
    if (cd.o[k] == null) continue;
    const o = P(cd.o[k]), c = P(cd.c[k]), up = c >= o, col = '#52525b';
    svg.append(el('line', {x1: X(k), x2: X(k), y1: Y(P(cd.h[k])), y2: Y(P(cd.l[k])), stroke: col, 'stroke-width': 1}));
    svg.append(el('rect', {x: X(k) - bw / 2, y: Math.min(Y(o), Y(c)), width: bw, height: Math.max(1, Math.abs(Y(o) - Y(c))), fill: up ? '#fff' : col, stroke: col, 'stroke-width': 1}));
  }
  svg.append(el('line', {x1: m.l, x2: w - m.r, y1: Y(x.lvl), y2: Y(x.lvl), stroke: '#2563eb', 'stroke-width': 1.4, 'stroke-dasharray': '6 4', 'data-chart': 'trade.level'}));
  svg.append(el('text', {x: w - m.r - 2, y: Y(x.lvl) - 5, 'text-anchor': 'end', style: 'fill:#2563eb'}, 'уровень ' + trPx(x.lvl)));
  const ex = (x.entry_ms - cd.t0) / 60000, xx = (x.exit_ms - cd.t0) / 60000;
  const XE = m.l + ex / n * (w - m.l - m.r), XX = m.l + xx / n * (w - m.l - m.r), ye = Y(x.entry_px), yx = Y(x.exit_px);
  svg.append(el('line', {x1: XE, x2: XX, y1: ye, y2: yx, stroke: '#18181b', 'stroke-width': 1, 'stroke-dasharray': '2 3'}));
  svg.append(el('path', {d: `M${XE - 7} ${ye - 11}L${XE + 7} ${ye - 11}L${XE} ${ye}Z`, fill: '#2563eb', stroke: '#fff', 'stroke-width': 1.2, 'data-chart': 'trade.entry'}));
  svg.append(el('text', {x: XE, y: ye - 15, 'text-anchor': XE > w - 90 ? 'end' : 'middle', class: 'k'}, 'вход'));
  svg.append(el('path', {d: `M${XX} ${yx - 7}L${XX + 7} ${yx}L${XX} ${yx + 7}L${XX - 7} ${yx}Z`, fill: '#18181b', stroke: '#fff', 'stroke-width': 1.2, 'data-chart': 'trade.exit'}));
  svg.append(el('text', {x: XX, y: yx + 21, 'text-anchor': XX > w - 90 ? 'end' : 'middle', class: 'k'}, 'выход'));
  const r = el('rect', {x: m.l, y: m.t, width: w - m.l - m.r, height: hh - m.t - m.b, fill: 'transparent', 'data-chart': 'trade.hover'});
  svg.append(r);
  const show = ev => {
    const b = svg.getBoundingClientRect(), px = (ev.clientX - b.left) / b.width * w, k = Math.max(0, Math.min(n - 1, Math.floor((px - m.l) / (w - m.l - m.r) * n)));
    tip.innerHTML = `<div class="t">${trDT(cd.t0 + k * 60000)} GMT+4</div>` + (cd.o[k] == null ? '<div class="r"><span>нет сделок за минуту</span></div>' : [['открытие', cd.o[k]], ['максимум', cd.h[k]], ['минимум', cd.l[k]], ['закрытие', cd.c[k]]].map(([t, v]) => `<div class="r"><span>${t}</span><span>${trPx(P(v))}</span></div>`).join(''));
    tip.style.display = 'block'; moveTip(ev);
  };
  r.addEventListener('mouseenter', show); r.addEventListener('mousemove', show); r.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  return svg;
}
async function trDetail(box, w) {
  const x = TR.rows.find(r => r.i === TR.open);
  if (!x) { box.replaceChildren(); return; }
  const cl = TR.idx.clusters.find(c => c.id === x.cluster);
  const head = h('div', {class: 'card-h'}, [h('b', {'data-f': 'title'}, `${x.sym} · ${trDT(x.entry_ms)} GMT+4`), h('span', {'data-f': 'sub'}, `вход ${trPx(x.entry_px)} · выход ${trPx(x.exit_px)} (${TR.idx.kinds[x.kind] || x.kind}) · ${money(x.pnl, 2)} $ · ${cl ? 'К' + cl.id + ' · ' + cl.name : 'без кластера'} · уровень ${trPx(x.lvl)}`)]);
  const close = h('button', {class: 'tr-x', title: 'закрыть'}, '×'); close.onclick = () => { TR.open = null; setHash(); renderTrades(); };
  head.append(close);
  const body = h('div', {class: 'card-b', 'data-f': 'chart'}, h('div', {class: 'fan-msg'}, 'загрузка минуток…'));
  box.replaceChildren(h('div', {class: 'card eq-card', id: 'tr-detail', 'data-i': x.i}, [head, body]));
  try {
    const cd = await trCandles(x.i);
    if (TR.open !== x.i) return;
    body.replaceChildren(trChart(x, cd, Math.max(300, Math.floor(w - (w < 520 ? 22 : 42)))));
  } catch (e) { body.replaceChildren(h('div', {class: 'fan-msg'}, 'минутки не загрузились: ' + (e.message || e))); }
}
function renderTrades() {
  const root = document.getElementById('trades');
  if (!TR.idx) {
    root.replaceChildren(h('div', {class: 'eq-h'}, h('h2', {}, 'Сделки')), h('div', {class: 'fan-msg'}, TR.err ? 'не загрузилось: ' + TR.err : 'загрузка…'));
    trLoad(); return;
  }
  tip.style.display = 'none';
  const w = Math.max(300, root.clientWidth), per = trInPer(), rows = TR.cl ? per.filter(x => x.cluster === TR.cl) : per;
  if (TR.open != null && !TR.rows.some(r => r.i === TR.open)) TR.open = null;
  const pick = h('div', {class: 'tabs eq-ctl', role: 'tablist', id: 'tr-per'}, TR_PER.map(([k, t, set]) => { const b = h('button', {role: 'tab', 'data-per': k, 'aria-selected': String(TR.per === k)}, `${t} · ${TR.rows.filter(x => set.includes(x.per)).length}`); b.onclick = () => { TR.per = k; TR.open = null; setHash(); renderTrades(); }; return b; }));
  const detail = h('div', {id: 'tr-detail-box'});
  root.replaceChildren(
    h('div', {class: 'eq-h'}, [h('h2', {}, 'Сделки'), h('span', {class: 'muted small', id: 'tr-sub'}, 'шорт, база без выхода по времени · после комиссий · 500 $ на сделку · время GMT+4 · кластеры найдены на этих данных')]),
    pick,
    h('div', {class: 'card eq-card'}, [h('div', {class: 'card-b'}, trSummary(per))]),
    detail,
    h('div', {class: 'card eq-card'}, [h('div', {class: 'card-h'}, [h('b', {}, TR.cl ? `К${TR.cl} · ${TR.idx.clusters.find(c => c.id === TR.cl).name}` : 'Все сделки'), h('span', {id: 'tr-cnt'}, `${ints(rows.length)} сделок`)]), h('div', {class: 'card-b'}, trTable(rows))]));
  if (TR.open != null) trDetail(detail, w);
}
