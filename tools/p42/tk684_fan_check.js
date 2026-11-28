// П-42 TK-684: клик-тест вида «Веер» вкладки «Эквити» на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk684_fan_check.js <url страницы> <каталог выхода> [ширина]
// Делает: ошибки консоли; пакеты data/fan == выборка летописи (check.json) — независимая распаковка; каждый уровень и «все» обеих сторон (число линий == index.json);
// ползунки, признак, топ N — число линий == независимый счёт по пакету; наведение, закрепление образца, «похожие», избранное в хеше, «открыть в эквити» (строка «Вариантов» == числа пакета);
// ссылка с хешем воспроизводит вид; на виде нет абзацев, медиан и номеров задач; перелива нет; время кадра на самом большом наборе; снимки.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [base, out, wArg] = process.argv.slice(2);
const W = +(wArg || 1280);
const MS_MAX = 250;      // кадр на самом большом наборе, мс

(async () => {
  fs.mkdirSync(out, {recursive: true});
  const browser = await chromium.launch({executablePath: process.env.CHROME || undefined, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [];
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', e => errs.push(String(e)));
  page.on('requestfailed', r => errs.push('request failed: ' + r.url()));
  const rep = {url: base, width: W, errors: errs, fails: [], checks: 0, clicks: 0, ms: {}, brk: {}};
  const ok = (c, m) => { rep.checks++; if (!c) rep.fails.push(m); };
  const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b), m + ': ' + JSON.stringify(a).slice(0, 200) + ' ≠ ' + JSON.stringify(b).slice(0, 200));
  const shot = n => page.screenshot({path: `${out}/fan-${n}-${W}.png`, fullPage: true});
  const ready = () => page.waitForFunction(() => { const c = document.querySelector('canvas[data-fan="base"]'); return c && c.dataset.n != null && !document.querySelector('[data-fanload]'); }, null, {timeout: 60000});
  const dom = () => page.evaluate(() => {
    const c = document.querySelector('canvas[data-fan="base"]'), q = s => [...document.querySelectorAll(s)];
    return {hash: decodeURIComponent(location.hash), n: +c.dataset.n, pass: +c.dataset.pass, total: +c.dataset.total, ms: +c.dataset.ms, msSel: +c.dataset.msSel, msDraw: +c.dataset.msDraw, mode: c.dataset.mode, cnt: document.getElementById('fan-cnt').textContent,
      side: (document.querySelector('#eq-tabs button[aria-selected="true"]') || {dataset: {}}).dataset.side, bothHidden: document.querySelector('#eq-tabs button[data-side="both"]').hidden,
      view: (document.querySelector('#eq-views button[aria-selected="true"]') || {dataset: {}}).dataset.view, lv: (document.querySelector('#fan-lv button[aria-selected="true"]') || {dataset: {}}).dataset.lv,
      head: q('table[data-fantable] thead th').map(t => t.textContent), rows: q('table[data-fantable] tbody tr').map(r => ({tok: r.dataset.tok, kind: r.dataset.kind, pin: r.classList.contains('pin'), name: r.querySelector('[data-f=name]').textContent,
        fav: r.querySelector('.fan-star').getAttribute('aria-pressed'), v: Object.fromEntries([...r.querySelectorAll('td.num')].map(x => [x.dataset.f, x.dataset.v])), t: Object.fromEntries([...r.querySelectorAll('td.num')].map(x => [x.dataset.f, x.textContent]))})),
      legend: q('#eq-view .legend span').map(s => s.textContent), pinbar: (document.querySelector('[data-pinbar]') || {dataset: {}}).dataset.pinbar || null,
      prose: q('#eq-view p, #eq-view ul, #eq-view ol, #eq-view li, #eq-view a, #eq-view .badge').length, text: document.getElementById('equity').innerText, overflow: document.documentElement.scrollWidth - innerWidth,
      tableOver: (t => t ? t.scrollWidth - t.parentElement.clientWidth : 0)(document.querySelector('table[data-fantable]')), ink: (() => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let k = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) k++; return k; })()};
  });
  // независимая распаковка пакетов в странице: поля клеток стороны и счёт под пороги
  await page.goto(base + '#equity/fan/short', {waitUntil: 'load'});
  const installT = () => page.evaluate(() => {
    window.__T = {packs: {}};
    // независимая распаковка пакета (TK-696: файлы по ≤ 150 000 клеток, оси — пары UInt16 + число пар); накопленные ряды и суточные центы в кэше не держатся (1,5 млн клеток), только поля, id, оси и ключи
    const unpack = async name => {
      const I = window.__T.I || (window.__T.I = await (await fetch('data/fan/index.json')).json());
      const gz = async u => new Response((await fetch(u)).body.pipeThrough(new DecompressionStream('gzip')));
      const buf = await (await gz('data/fan/' + name + '.bin.gz')).arrayBuffer(), meta = await (await gz('data/fan/' + name + '.json.gz')).json(), N = I.files[name].n, T = I.files[name].pairs, D = I.days, NF = I.fields.length, cb = N * D * 2, fb = N * NF * 4;
      if (buf.byteLength !== cb + fb + N * 4 + T * 4 + N || meta.k.length !== N) throw new Error(name + ': размер пакета');
      const axn = new Uint8Array(buf, cb + fb + N * 4 + T * 4, N), aoff = new Uint32Array(N + 1); for (let i = 0; i < N; i++) aoff[i + 1] = aoff[i] + axn[i];
      return {N, D, NF, level: I.files[name].level, cents: new Int16Array(buf, 0, N * D), F: new Float32Array(buf, cb, N * NF), ids: new Uint32Array(buf, cb + fb, N), axp: new Uint16Array(buf, cb + fb + N * 4, T * 2), aoff, meta, bytes: buf.byteLength};
    };
    window.__T.load = async name => {
      if (window.__T.packs[name]) return window.__T.packs[name];
      const P = await unpack(name);
      return (window.__T.packs[name] = {N: P.N, D: P.D, NF: P.NF, level: P.level, F: new Float32Array(P.F), ids: new Uint32Array(P.ids), axp: new Uint16Array(P.axp), aoff: P.aoff, meta: P.meta, bytes: P.bytes});
    };
    window.__T.find = async (p, key) => { for (const name of Object.keys(window.__T.I.files).filter(n => n[0] === p)) { const P = await window.__T.load(name); const i = P.meta.k.indexOf(key); if (i >= 0) return {name, level: P.level, f: [...P.F.subarray(i * P.NF, i * P.NF + 5)], ext: P.NF > 13 && P.F[i * P.NF + 13] > 0, tail: P.NF > 14 && P.F[i * P.NF + 14] > 0}; } return null; };
    window.__T.hasAx = (P, i, axi) => { for (let q = P.aoff[i]; q < P.aoff[i + 1]; q++) if (P.axp[2 * q] === axi) return true; return false; };
    // клетки файла по id: ключ, поля, накопленный итог суточных центов (файл распаковывается заново и не кэшируется)
    window.__T.sample = async (name, ids) => { const P = await unpack(name); return {bytes: P.bytes, rows: ids.map(id => { const i = P.ids.indexOf(id); let c = 0; for (let d = 0; d < P.D; d++) c += P.cents[i * P.D + d]; return {key: P.meta.k[i], f: [...P.F.subarray(i * P.NF, i * P.NF + P.NF)], cum: c / 100}; })}; };
    // число клеток стороны и уровня, проходящих пороги (f: {dd, gap, n, sum, sh, ax}); лучший по полю
    window.__T.count = async (side, lv, f) => {
      const I = window.__T.I, p = side === 'long' ? 'L' : 'S', axi = f.ax ? I.axes.findIndex(a => a.name === f.ax) : -1; let k = 0, tot = 0, best = null;
      for (const name of Object.keys(I.files).filter(n => n[0] === p)) {
        const P = await window.__T.load(name);
        for (let i = 0; i < P.N; i++) {
          const o = i * P.NF, F = P.F;
          if (!(lv === 'all' || (lv === 'split' ? F[o + 10] > 0 : P.level === +lv))) continue;
          tot++;
          if (f.dd != null && F[o + 3] > f.dd) continue; if (f.gap != null && F[o + 4] > f.gap) continue; if (f.n != null && F[o] < f.n) continue; if (f.sum != null && F[o + 1] < f.sum) continue; if (f.sh != null && !(F[o + 2] >= f.sh)) continue;
          if (axi >= 0 && !window.__T.hasAx(P, i, axi)) continue;
          k++; if (f.by != null && (best == null || F[o + f.by] > best.v)) best = {v: F[o + f.by], key: P.meta.k[i]};
        }
      }
      return {k, tot, best};
    };
    // первые K значений столбца k среди клеток стороны и уровня, проходящих пороги f (без признака), по убыванию desc или возрастанию; пустые значения — в конце
    window.__T.topCol = async (side, lv, f, k, desc, K) => {
      const I = window.__T.I, p = side === 'long' ? 'L' : 'S', vals = [];
      for (const name of Object.keys(I.files).filter(n => n[0] === p)) {
        const P = await window.__T.load(name);
        for (let i = 0; i < P.N; i++) {
          const o = i * P.NF, F = P.F;
          if (!(lv === 'all' || (lv === 'split' ? F[o + 10] > 0 : P.level === +lv))) continue;
          if (f.dd != null && F[o + 3] > f.dd) continue; if (f.gap != null && F[o + 4] > f.gap) continue; if (f.n != null && F[o] < f.n) continue; if (f.sum != null && F[o + 1] < f.sum) continue; if (f.sh != null && !(F[o + 2] >= f.sh)) continue;
          const n = F[o], nw = F[o + 5], nl = F[o + 7], ml = F[o + 8];
          const v = {name: P.meta.k[i], n, sum: F[o + 1], mean: n ? F[o + 1] / n : null, sharpe: Number.isNaN(F[o + 2]) ? null : F[o + 2], dd: F[o + 3], ddp: F[o + 3] / I.margin * 100, gap: F[o + 4], rr: nw && nl && ml < 0 ? F[o + 6] / Math.abs(ml) : null, worst: Number.isNaN(F[o + 9]) ? null : F[o + 9]}[k];
          if (v != null) vals.push(v);
        }
      }
      vals.sort((a, b) => a < b ? -1 : a > b ? 1 : 0); if (desc) vals.reverse();
      return vals.slice(0, K);
    };
  });
  await installT();
  await ready();
  const I = await page.evaluate(async () => { await window.__T.load('S-0'); return window.__T.I; });
  // 1. пакеты == выборка летописи
  const CHK = await page.evaluate(async () => (await fetch('data/fan/check.json')).json());
  for (const [name, list] of Object.entries(CHK)) {
    const got = await page.evaluate(([name, ids]) => window.__T.sample(name, ids), [name, list.map(x => x.id)]);
    eq(got.bytes, I.files[name].bytes, name + ' размер пакета');
    list.forEach((x, k) => {
      const g = got.rows[k];
      ok(g.key === x.cell && Math.abs(g.cum - x.cum) < 0.005 && Math.abs(g.cum - x.f[1]) < 0.7 && x.f.every((v, q) => v == null ? Number.isNaN(g.f[q]) : Math.abs(g.f[q] - v) <= 1e-5 * Math.max(1, Math.abs(v))), `${name} клетка ${x.id}: ${JSON.stringify(g).slice(0, 200)} ≠ ${JSON.stringify(x).slice(0, 200)}`);
    });
  }
  eq(Object.values(I.files).reduce((a, f) => a + f.n, 0), I.n, 'index.json: сумма по файлам');
  // 2. вид по умолчанию: пары шорта, «шорт + лонг» скрыт, на виде только управление, холст и одна таблица
  let d = await dom();
  const lvN = (side, lv) => Object.entries(I.files).filter(([n, f]) => n[0] === side && f.level === lv).reduce((a, [, f]) => a + f.n, 0);      // клеток уровня во всех частях
  eq([d.view, d.side, d.lv, d.bothHidden, d.total, d.n, d.hash], ['fan', 'short', '2', true, lvN('S', 2), lvN('S', 2), '#equity/fan/short/lv=2'], 'вид по умолчанию');
  eq(d.cnt.replace(/\s/g, ''), `линий${d.n}из${d.total}`, 'счётчик линий');
  ok(d.rows[0].kind === 'anchor' && d.rows.length === 31 && d.head[1] === 'Линия' && d.ink > 2000, 'таблица: база и 30 лучших; холст не пуст: ' + JSON.stringify([d.rows.length, d.ink]));
  const clean = (d, m) => { ok(d.prose === 0 && !/медиан/i.test(d.text) && !/TK-\d|L-\d{3}/.test(d.text), m + ': абзацы, медианы или номера задач'); ok(d.overflow <= 0 && d.tableOver <= 0, m + ': перелив ' + d.overflow + ' / таблица ' + d.tableOver); };
  clean(d, 'по умолчанию');
  const gm = await page.evaluate(() => ({ch: document.querySelector('canvas[data-fan="base"]').getBoundingClientRect().height, ww: document.querySelector('.wrap').getBoundingClientRect().width, cw: document.documentElement.clientWidth}));      // лист на всю ширину, холст +20 %
  ok(Math.abs(gm.ww - gm.cw) <= 1 && Math.abs(gm.ch - (W < 520 ? 384 : 552)) <= 1, 'ширина листа и высота холста веера: ' + JSON.stringify(gm));
  // база в таблице == база «Вариантов» (числа летописи)
  const K = await page.evaluate(async () => (await fetch('data/variants_check.json')).json());
  const J = await page.evaluate(async () => (await fetch('data/variants.json')).json());
  const anchorRow = (d, side) => { const a = K.single[J.default[side]], r = d.rows[0]; ok(r.kind === 'anchor' && +r.v.n === a.n && Math.abs(+r.v.sum - a.sum) < 0.01 && Math.abs(+r.v.sharpe - a.sharpe) < 1e-4 && Math.abs(+r.v.dd - a.dd) < 0.01 && +r.v.gap === a.gap, side + ' база веера == база «Вариантов»: ' + JSON.stringify(r.v)); };
  anchorRow(d, 'short');
  await shot('default');
  // 2б. замороженные линии TK-674 (L-428) есть в веере: шорт S1 / S2 — четвёрки, лонг L1 — тройка, L2 — четвёрка (в веере — значения исследования, +382,52; в «Вариантах» — настоящие, +382,62, L-434); числа == летописи
  for (const [p, key, lv, want] of [['S', 'e20+e2t2+n_bounce1+natr_p60+rr1+s4', 4, [2158, 1358.97, 11.275, 40.28, 13]], ['S', 'e20+e2t2+n_bounce1+natr_min0.6+nf1+q100+s4', 4, [1457, 1254.2, 11.373, 40.37, 16]],
    ['L', 's3+CN60_q50+big1800.le_q80+vpin1h.ge_q20', 3, [174, 348.07, 7.169, 17.1, 11]], ['L', 'busy0+s3+tpk3+big1800.le_q80+vpin1h.ge_q20', 4, [227, 382.52, 7.072, 19.13, 11]]]) {
    const r = await page.evaluate(([p, key]) => window.__T.find(p, key), [p, key]);
    // клетка с рядом января–июня или хвостом 14–24.11 считается по своему окну (01.01–24.11): числа 01.07–13.11 остаются у клеток без них; у остальных — сделок не меньше
    const win = r && !r.ext && !r.tail;
    ok(r && r.level === lv && (win ? r.f[0] === want[0] && Math.abs(r.f[1] - want[1]) < 0.006 && Math.abs(r.f[2] - want[2]) < 0.006 && Math.abs(r.f[3] - want[3]) < 0.006 && r.f[4] === want[4] : r.f[0] >= want[0]), 'замороженная линия в веере ' + key + ': ' + JSON.stringify(r));
  }
  // 3. уровни обеих сторон: число линий == независимый счёт; время кадра
  const clickLv = async lv => { await page.click(`#fan-lv button[data-lv="${lv}"]`); rep.clicks++; await ready(); };
  for (const side of ['short', 'long']) {
    if (side === 'long') { await page.click('#eq-tabs button[data-side="long"]'); rep.clicks++; await ready(); }
    for (const lv of ['1', '2', '3', '4', 'split', 'all']) {
      await clickLv(lv); d = await dom();
      const want = await page.evaluate(([s, l]) => window.__T.count(s, l, {}), [side, lv]);
      eq([d.total, d.n, d.lv, d.side], [want.tot, want.tot, lv, side], `${side} уровень ${lv}`);
      ok((await page.$$('[data-fanerr]')).length === 0, `${side} уровень ${lv}: узел ошибки данных на виде`);
      ok(d.ink > 2000 && d.mode === (d.n > 300 ? 'density' : 'lines'), `${side} ${lv}: холст ${d.ink}, режим ${d.mode}`);
      rep.ms[side + '/' + lv] = d.ms; rep.brk[side + '/' + lv] = [d.msSel, d.msDraw];
    }
    d = await dom(); anchorRow(d, side); clean(d, side + ' все');
    await shot('all-' + side);
  }
  // 4. ползунки на самом большом наборе: число линий == независимый счёт; кадр при каждом движении
  await page.click('#eq-tabs button[data-side="short"]'); rep.clicks++; await ready(); await clickLv('all');
  const setSl = async (k, v) => { await page.evaluate(([k, v]) => { const i = document.querySelector(`input[data-sl="${k}"]`); i.value = v; i.dispatchEvent(new Event('input', {bubbles: true})); }, [k, v]); await page.waitForTimeout(350); return page.evaluate(k => +document.querySelector(`input[data-sl="${k}"]`).value, k); };
  const f = {}; let frames = [];
  for (const [k, v] of [['dd', 80], ['gap', 30], ['n', 200], ['sum', 300], ['sh', 5]]) {
    f[k] = await setSl(k, v); d = await dom(); frames.push(d.ms);
    const want = await page.evaluate(f => window.__T.count('short', 'all', f), f);
    eq([d.pass, d.n, d.total], [want.k, want.k, want.tot], 'ползунок ' + k + ' = ' + f[k]);
    ok(d.hash.includes(k + '=' + f[k]), 'ползунок ' + k + ' в хеше: ' + d.hash);
  }
  await shot('sliders');
  // движение ползунка по самому большому набору (все линии шорта): кадры
  for (const k of ['sh', 'sum', 'n', 'gap']) { await setSl(k, k === 'gap' ? 1e9 : -1e9); }
  for (const v of [400, 300, 200, 150, 120, 100, 90]) { await setSl('dd', v); frames.push((await dom()).ms); }
  rep.ms.frames = frames; rep.ms.max = Math.max(...frames, ...Object.values(rep.ms).filter(x => typeof x === 'number'));
  ok(rep.ms.max < MS_MAX, `время кадра ${rep.ms.max.toFixed(0)} мс ≥ ${MS_MAX}`);
  // 5. топ N и порядок; признак
  await setSl('dd', 1e9);
  await page.selectOption('#fan-top', '100'); await ready(); await page.selectOption('#fan-by', 'sum'); await ready(); d = await dom();
  let want = await page.evaluate(() => window.__T.count('short', 'all', {by: 1}));
  eq([d.n, d.mode, d.rows[1].name], [100, 'lines', want.best.key.replace(/_bp(?![a-z])/g, '')], 'топ 100 по сумме');
  ok(d.hash.includes('top=100') && d.hash.includes('by=sum'), 'топ в хеше: ' + d.hash);
  await shot('top100');
  await page.selectOption('#fan-top', '0'); await ready();
  const axes = await page.evaluate(() => [...document.querySelectorAll('#fan-ax option')].map(o => [o.value, o.textContent]));
  ok(axes.length > 20 && axes.slice(1).filter(a => /^№ \d+ · /.test(a[1])).length >= axes.length - 12, 'признаки с номерами: ' + JSON.stringify(axes.slice(0, 6)));
  for (const ax of ['rf', 'TPM60', 'split']) {
    await page.selectOption('#fan-ax', ax); await ready(); d = await dom();
    want = await page.evaluate(ax => window.__T.count('short', 'all', {ax}), ax);
    eq([d.pass, d.n], [want.k, want.k], 'признак ' + ax);
    const vals = I.axes.find(a => a.name === ax).vals;
    ok(d.legend.length === 1 + vals.length && d.hash.includes('ax=' + ax), 'признак ' + ax + ': легенда значений ' + JSON.stringify(d.legend).slice(0, 160));
    if (ax === 'rf') await shot('axis-rf');
  }
  await page.selectOption('#fan-ax', ''); await ready();
  // 5б. порядок таблицы по столбцу: клик по заголовку, повторный — наоборот; по всем линиям набора, не по нарисованной выборке; переживает ползунки, уровень, ссылку; «порядок» его сбрасывает
  const SORT = ['name', 'n', 'sum', 'mean', 'sharpe', 'dd', 'ddp', 'gap', 'rr', 'worst'], ASC1 = new Set(['name', 'dd', 'ddp', 'gap']);
  const sortSeq = d => d.rows.filter(r => r.kind === 'row').map(r => r.v);
  const sortWant = async (side, lv, fl, k, asc) => {      // ожидаемые значения 30 строк; базовая строка таблицы выпадает из них, если сама вошла в первые 30
    const exp = (await page.evaluate(([s, l, fl, k, desc]) => window.__T.topCol(s, l, fl, k, desc, 30), [side, lv, fl, k, !asc]));
    const a = (await dom()).rows[0], av = k === 'name' ? null : +a.v[k], i = k === 'name' ? exp.findIndex(x => x.replace(/_bp(?![a-z])/g, '') === a.name) : exp.findIndex(x => Math.abs(x - av) <= 1e-9 * Math.max(1, Math.abs(av)));
    if (i >= 0) exp.splice(i, 1); return exp;
  };
  const sortCheck = async (side, lv, fl, k, asc, m) => {
    const d = await dom(), th = await page.evaluate(k => { const t = document.querySelector(`th[data-sort="${k}"]`); return {as: t.getAttribute('aria-sort'), text: t.textContent}; }, k);
    const exp = await sortWant(side, lv, fl, k, asc), got = d.rows.filter(r => r.kind === 'row').map(r => k === 'name' ? r.name : +r.v[k]);
    const want = k === 'name' ? exp.map(x => x.replace(/_bp(?![a-z])/g, '')) : exp;
    ok(th.as === (asc ? 'ascending' : 'descending') && th.text.endsWith(asc ? '▲' : '▼') && d.hash.includes(`so=${k}:${asc ? 'a' : 'd'}`), `${m}: заголовок ${JSON.stringify(th)}, хеш ${d.hash}`);
    ok(got.length === want.length && got.every((x, q) => k === 'name' ? x === want[q] : Math.abs(x - want[q]) <= 1e-9 * Math.max(1, Math.abs(want[q]))), `${m}: значения ${JSON.stringify(got.slice(0, 5))} ≠ ожидаемым ${JSON.stringify(want.slice(0, 5))}`);
  };
  await clickLv('all');
  for (const k of SORT) {
    await page.click(`th[data-sort="${k}"]`); rep.clicks++; await ready(); await sortCheck('short', 'all', {}, k, ASC1.has(k), `шорт все, ${k}, 1-й клик`);
    await page.click(`th[data-sort="${k}"]`); rep.clicks++; await ready(); await sortCheck('short', 'all', {}, k, !ASC1.has(k), `шорт все, ${k}, 2-й клик`);
  }
  // состояние переживает ползунок и уровень; ссылка воспроизводит порядок
  await page.click('th[data-sort="sharpe"]'); rep.clicks++; await ready();
  const sf = {}; sf.dd = await setSl('dd', 80); sf.n = await setSl('n', 200); d = await dom();
  await sortCheck('short', 'all', sf, 'sharpe', false, 'шорт все, Шарп, после ползунков');
  const sortHash = (await dom()).hash; await page.goto('about:blank'); await page.goto(base + sortHash, {waitUntil: 'load'}); await ready(); await installT(); await page.evaluate(() => window.__T.load('S-0'));
  eq([(await dom()).hash, sortSeq(await dom()).length > 0], [sortHash, true], 'ссылка с порядком');
  await sortCheck('short', 'all', sf, 'sharpe', false, 'шорт все, Шарп, по ссылке');
  await clickLv('3'); await sortCheck('short', '3', {}, 'sharpe', false, 'шорт тройки, Шарп, после смены уровня (ползунки сброшены)');
  await clickLv('all');
  await page.click('th[data-sort="n"]'); rep.clicks++; await ready(); await page.selectOption('#fan-top', '100'); await ready(); d = await dom();
  ok(d.n === 100 && d.rows.filter(r => r.kind === 'row').map(r => +r.v.n).every((x, q, a) => !q || x <= a[q - 1]), 'топ 100 + порядок по «Сделок»: ' + JSON.stringify([d.n, d.hash]));
  await page.selectOption('#fan-top', '0'); await ready();
  await page.selectOption('#fan-by', 'sum'); await ready(); d = await dom();
  ok(!/so=/.test(d.hash) && d.rows.filter(r => r.kind === 'row').map(r => +r.v.sum).every((x, q, a) => !q || x <= a[q - 1]), '«порядок» сбрасывает порядок столбца: ' + d.hash);
  await page.selectOption('#fan-by', 'sh'); await ready();
  await page.click('th[data-sort="sum"]'); rep.clicks++; await ready(); await sortCheck('short', 'all', {}, 'sum', false, 'шорт все, Сумма');
  await shot('sorted');
  await page.selectOption('#fan-by', 'sh'); await ready();      // порядок по умолчанию для дальнейших шагов — «порядок» сбрасывает порядок столбца
  // 6. наведение, образец, «похожие», избранное, «открыть в эквити»
  await clickLv('2');
  const cv = page.locator('canvas[data-fan="over"]'); await cv.scrollIntoViewIfNeeded(); const bb = await cv.boundingBox();
  await page.mouse.move(bb.x + bb.width * 0.7, bb.y + bb.height * 0.45); await page.waitForTimeout(150);
  const tp = await page.evaluate(() => { const t = document.getElementById('tip'); return {shown: t.style.display === 'block', rows: t.querySelectorAll('.r').length, text: t.innerText}; });
  ok(tp.shown && tp.rows === 6 && /Сделок/.test(tp.text), 'наведение: ' + JSON.stringify(tp));
  await page.screenshot({path: `${out}/fan-hover-${W}.png`});
  await page.mouse.click(bb.x + bb.width * 0.7, bb.y + bb.height * 0.45); rep.clicks++; await ready(); d = await dom();
  const tok = d.pinbar;
  ok(tok && /^S2[0-9a-z]+$/.test(tok) && d.hash.includes('pin=' + tok) && d.rows[1].tok === tok && d.rows[1].pin && d.legend.includes('образец'), 'закрепление образца: ' + JSON.stringify([tok, d.hash, d.rows[1]]).slice(0, 300));
  const pinRow = d.rows[1];
  await page.click('[data-pinbar] button[data-act="sim"]'); rep.clicks++; await ready(); d = await dom();
  ok(d.hash.includes('sim=1') && d.n === 50 && d.head.includes('Сходство') && d.rows[1].tok === tok, 'похожие: ' + JSON.stringify([d.hash, d.n, d.head.slice(-1)]));
  const sims = d.rows.filter(r => r.kind === 'row').map(r => +r.t.sim.replace(',', '.').replace('−', '-'));
  ok(sims.every((x, i) => x <= 1.0001 && (!i || x <= sims[i - 1] + 1e-9)) && sims[0] > 0.9, 'сходство по убыванию: ' + JSON.stringify(sims.slice(0, 5)));
  await shot('similar');
  await page.click('[data-pinbar] button[data-act="fav"]'); rep.clicks++; await ready(); d = await dom();
  ok(d.hash.endsWith('/f=' + tok) && d.rows.find(r => r.tok === tok).fav === 'true' && d.legend.includes(pinRow.name), 'избранное в хеше: ' + d.hash);
  // вторая в избранное звездой из таблицы
  const tok2 = d.rows.find(r => r.kind === 'row' && r.tok !== tok).tok;
  await page.click(`button[data-fav="${tok2}"]`); rep.clicks++; await ready(); d = await dom();
  ok(d.hash.endsWith('/f=' + tok + '.' + tok2) && d.rows.filter(r => r.fav === 'true').length === 2, 'две в избранном: ' + d.hash);
  const fanHash = d.hash, passBefore = d.pass;
  // ссылка воспроизводит вид
  await page.goto('about:blank'); await page.goto(base + fanHash, {waitUntil: 'load'}); await ready(); d = await dom();
  eq([d.hash, d.pinbar, d.pass, d.n, d.rows.filter(r => r.fav === 'true').length], [fanHash, tok, passBefore, 50, 2], 'ссылка с хешем');
  await page.click('[data-pinbar] button[data-act="open"]'); rep.clicks++; await page.waitForSelector('table[data-vtable]');
  await page.waitForFunction(t => [...document.querySelectorAll('table[data-vtable] tbody tr')].some(r => r.dataset.ids && r.dataset.ids.includes(t)), tok);
  const v = await page.evaluate(() => ({hash: decodeURIComponent(location.hash), rows: [...document.querySelectorAll('table[data-vtable] tbody tr')].map(r => ({line: r.dataset.line, ids: r.dataset.ids, name: r.querySelector('[data-f=name]').textContent, status: r.querySelector('[data-f=status]').textContent,
    v: Object.fromEntries([...r.querySelectorAll('td.num')].map(c => [c.dataset.f, c.dataset.v]))})), fan: [...document.querySelectorAll('.vr-pick[data-pick="short"] button')].filter(b => /^S\d/.test(b.dataset.var)).map(b => [b.dataset.var, b.textContent, b.getAttribute('aria-selected')]),
    paths: [...document.querySelectorAll('#eq-view path[data-chart^="var."]')].map(p => [p.dataset.chart, +p.dataset.final])}));
  const vr = v.rows.find(r => r.line === 'short');
  eq([v.hash, vr && vr.name, vr && vr.status], [`#equity/both/${J.default.long}/${tok}/f=${tok}.${tok2}`, 'Шорт · ' + pinRow.name, 'подобрано на этих данных'], 'открыть в эквити');
  ok(vr && ['n', 'sum', 'sharpe', 'dd', 'gap', 'rr', 'worst'].every(k => vr.v[k] === pinRow.v[k]), 'строка «Вариантов» == строка веера: ' + JSON.stringify([vr && vr.v, pinRow.v]).slice(0, 300));
  eq(v.fan.map(x => [x[0], x[2]]), [[tok, 'true'], [tok2, 'false']], 'группа «из веера»');
  const both = v.rows.find(r => r.line === 'both'), la = K.single[J.default.long];
  ok(both && +both.v.n === la.n + +pinRow.v.n && Math.abs(+both.v.sum - (la.sum + +pinRow.v.sum)) < 0.01 && v.paths.length === 3 && Math.abs(v.paths.find(p => p[0] === 'var.both')[1] - (la.sum + +pinRow.v.sum)) < 0.7, 'шорт + лонг с клеткой веера: ' + JSON.stringify([both && both.v, v.paths]).slice(0, 300));
  await shot('open-in-variants');
  // 6б. веер с 01.01 (TK-696): ось 328 суток (01.01–24.11), поля ext и tail, подпись окна, ось вида «Варианты» у клетки с рядом января–июня и хвостом 14–24.11
  const I2 = await page.evaluate(() => ({days: FAN.index.days, start: FAN.index.start_ms, off: FAN.index.off, ext_n: FAN.index.ext_n, tail_n: FAN.index.tail_n, tail_from: FAN.index.tail_from, nf: FAN.index.fields.length, last: FAN.index.fields[FAN.index.fields.length - 1]}));
  eq([I2.days, I2.start, I2.off, I2.tail_from, I2.last, I2.nf], [328, Date.UTC(2026, 0, 1), 181, 317, 'tail', 15], 'ось веера с 01.01');
  ok(I2.ext_n > 100000 && I2.tail_n > 100000, 'клеток с январём–июнем и с хвостом: ' + I2.ext_n + ' / ' + I2.tail_n);
  const [pinExt, pinTail] = await page.evaluate(t => { const c = fanCell(t); return [fanIsExt(c.P, c.i), fanIsTail(c.P, c.i)]; }, tok);
  const sub0 = await page.evaluate(() => document.getElementById('eq-sub').textContent);
  ok(/01\.01–/.test(sub0) === pinExt && /–24\.11\.2026/.test(sub0) === pinTail, 'подпись окна у выбранной клетки (ext ' + pinExt + ', tail ' + pinTail + '): ' + sub0);
  const xt = await page.evaluate(() => {      // клетка с рядом января–июня из загруженных пакетов шорта: токен и числа пакета
    for (const n in FAN.packs) { const P = FAN.packs[n]; if (P.side !== 'short') continue; const NF = FAN.index.fields.length; for (let i = 0; i < P.N; i++) if (P.F[i * NF + F_EXT] > 0 && P.F[i * NF + F_TAIL] > 0 && !Number.isNaN(P.F[i * NF + F_SH])) { let jan = 0; for (let d = 0; d < FAN.index.off; d++) jan += P.cents[i * FAN.index.days + d]; if (jan) { const D = FAN.index.days, x = []; for (let k = 0; k < D; k++) x.push(P.cents[i * D + k] / 100); const mean = x.reduce((a, b) => a + b, 0) / D, sd = Math.sqrt(x.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (D - 1)); let c = 0, pk = 0, dd = 0, hi = 0, last = -1, gap = 0; x.forEach((v, k) => { c += v; pk = Math.max(pk, c); dd = Math.max(dd, pk - c); if (c > hi + 1e-9) { gap = Math.max(gap, k - last); last = k; hi = c; } }); gap = Math.max(gap, D - 1 - last);
      return {js: [mean / sd * Math.sqrt(365), dd, gap], pk: [P.F[i * NF + F_SH], P.F[i * NF + F_DD], P.F[i * NF + F_GAP]], tok: fanTok(P, i), n: P.F[i * NF + F_N], sum: P.F[i * NF + F_USD], jan: jan / 100, cum: fanCumRow(P, i, new Float64Array(FAN.index.days))[FAN.index.days - 1]}; } } }
    return null; });
  ok(xt && xt.tok, 'в загруженных пакетах шорта нет клетки с рядом января–июня');
  await page.evaluate(t => fanOpen(t), xt.tok); await page.waitForFunction(t => [...document.querySelectorAll('table[data-vtable] tbody tr')].some(r => r.dataset.ids && r.dataset.ids.includes(t)), xt.tok);
  const xv = await page.evaluate(() => ({sub: document.getElementById('eq-sub').textContent, rows: [...document.querySelectorAll('table[data-vtable] tbody tr')].map(r => ({line: r.dataset.line, v: Object.fromEntries([...r.querySelectorAll('td.num')].map(c => [c.dataset.f, c.dataset.v]))})),
    paths: [...document.querySelectorAll('#eq-view path[data-chart^="var."]')].map(p => [p.dataset.chart, +p.dataset.final]), ticks: [...document.querySelectorAll('#eq-view svg text')].map(t => t.textContent).filter(t => /^\d\d\.\d\d$/.test(t) || /\d\d\.\d\d\./.test(t)).slice(0, 3)}));
  const xs = xv.rows.find(r => r.line === 'short'), xb = xv.rows.find(r => r.line === 'both'), xl = xv.rows.find(r => r.line === 'long');
  ok(/01\.01–24\.11\.2026/.test(xv.sub), 'подпись «Вариантов» с клеткой с рядом января–июня и хвостом: ' + xv.sub);
  ok(xs && +xs.v.n === xt.n && Math.abs(+xs.v.sum - xt.sum) < 0.01 && xs.v.rr === '' && xs.v.worst === '', 'строка клетки с рядом января–июня == пакет, R:R и худшая «—»: ' + JSON.stringify([xs && xs.v, xt]).slice(0, 300));
  ok(xb && +xb.v.n === +xl.v.n + xt.n && Math.abs(+xb.v.sum - (+xl.v.sum + xt.sum)) < 0.01 && xb.v.rr === '' && xb.v.worst === '' && xb.v.sharpe !== '' && +xb.v.gap > 0, 'шорт + лонг с клеткой с рядом января–июня: ' + JSON.stringify(xb && xb.v).slice(0, 300));
  const xp = Object.fromEntries(xv.paths);
  ok(Math.abs(xp['var.short'] - xt.cum) < 0.01 && Math.abs(xp['var.both'] - (xt.cum + xp['var.long'])) < 0.7, 'кривые: шорт == суточные центы пакета, сумма == лонг + шорт: ' + JSON.stringify([xv.paths, xt.cum]));
  ok(Math.abs(xt.cum - xt.sum) < 0.7, 'сумма клетки == накопленный ряд: ' + JSON.stringify(xt));
  ok(Math.abs(xt.js[0] - xt.pk[0]) < 1e-3 && Math.abs(xt.js[1] - xt.pk[1]) < 1e-3 && xt.js[2] === xt.pk[2], 'Шарп / просадка / перехай клетки == независимый счёт по суточным центам (328 суток): ' + JSON.stringify([xt.js, xt.pk]));
  await shot('open-ext-in-variants');
  await page.evaluate(t => fanOpen(t), tok); await page.waitForFunction(t => [...document.querySelectorAll('table[data-vtable] tbody tr')].some(r => r.dataset.ids && r.dataset.ids.includes(t)), tok);      // назад к образцу — дальше шаги прежнего теста
  // вторая избранная — кнопкой в «Вариантах»; ссылка «Вариантов» с токеном воспроизводится
  await page.click(`.vr-pick[data-pick="short"] button[data-var="${tok2}"]`); rep.clicks++; await page.waitForTimeout(200);
  const h2 = await page.evaluate(() => decodeURIComponent(location.hash));
  eq(h2, `#equity/both/${J.default.long}/${tok2}/f=${tok}.${tok2}`, 'вторая избранная в «Вариантах»');
  await page.goto('about:blank'); await page.goto(base + h2, {waitUntil: 'load'});
  await page.waitForFunction(t => [...document.querySelectorAll('table[data-vtable] tbody tr')].some(r => r.dataset.ids && r.dataset.ids.includes(t)), tok2);
  // назад в веер: избранное на месте
  await page.click('#eq-views button[data-view="fan"]'); rep.clicks++; await ready(); d = await dom();
  ok(d.rows.filter(r => r.fav === 'true').length === 2 && d.hash.endsWith('/f=' + tok + '.' + tok2), 'избранное после возврата в веер: ' + d.hash);
  // снять образец, убрать избранное
  for (const t of [tok, tok2]) { await page.click(`button[data-fav="${t}"]`); rep.clicks++; await ready(); }
  d = await dom(); ok(!/\/f=/.test(d.hash) && d.rows.every(r => r.fav === 'false'), 'избранное снято: ' + d.hash);
  await shot('final');
  fs.writeFileSync(`${out}/report684fan-${W}.json`, JSON.stringify(rep, null, 1));
  console.log(JSON.stringify({width: W, checks: rep.checks, clicks: rep.clicks, cells: I.n, ms: rep.ms, brk: rep.brk, errors: errs, fails: rep.fails.slice(0, 12), nfails: rep.fails.length}));
  await browser.close();
  process.exit(errs.length || rep.fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
