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
  await page.evaluate(() => {
    window.__T = {packs: {}};
    window.__T.load = async name => {
      if (window.__T.packs[name]) return window.__T.packs[name];
      const I = window.__T.I || (window.__T.I = await (await fetch('data/fan/index.json')).json());
      const gz = async u => new Response((await fetch(u)).body.pipeThrough(new DecompressionStream('gzip')));
      const buf = await (await gz('data/fan/' + name + '.bin.gz')).arrayBuffer(), meta = await (await gz('data/fan/' + name + '.json.gz')).json(), N = I.files[name].n, D = I.days, NF = I.fields.length;
      return (window.__T.packs[name] = {N, D, NF, level: I.files[name].level, cents: new Int16Array(buf, 0, N * D), F: new Float32Array(buf, N * D * 2, N * NF), ids: new Uint32Array(buf, N * D * 2 + N * NF * 4, N), meta, bytes: buf.byteLength});
    };
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
          if (axi >= 0 && !P.meta.a[i].some(a => a[0] === axi)) continue;
          k++; if (f.by != null && (best == null || F[o + f.by] > best.v)) best = {v: F[o + f.by], key: P.meta.k[i]};
        }
      }
      return {k, tot, best};
    };
  });
  await ready();
  const I = await page.evaluate(async () => { await window.__T.load('S-0'); return window.__T.I; });
  // 1. пакеты == выборка летописи
  const CHK = await page.evaluate(async () => (await fetch('data/fan/check.json')).json());
  for (const [name, list] of Object.entries(CHK)) {
    const got = await page.evaluate(async ([name, ids]) => { const P = await window.__T.load(name); return {bytes: P.bytes, rows: ids.map(id => { const i = P.ids.indexOf(id); let c = 0; for (let d = 0; d < P.D; d++) c += P.cents[i * P.D + d]; return {key: P.meta.k[i], f: [...P.F.subarray(i * P.NF, i * P.NF + P.NF)], cum: c / 100}; })}; }, [name, list.map(x => x.id)]);
    eq(got.bytes, I.files[name].bytes, name + ' размер пакета');
    list.forEach((x, k) => {
      const g = got.rows[k];
      ok(g.key === x.cell && Math.abs(g.cum - x.cum) < 0.005 && Math.abs(g.cum - x.f[1]) < 0.7 && x.f.every((v, q) => v == null ? Number.isNaN(g.f[q]) : Math.abs(g.f[q] - v) <= 1e-5 * Math.max(1, Math.abs(v))), `${name} клетка ${x.id}: ${JSON.stringify(g).slice(0, 200)} ≠ ${JSON.stringify(x).slice(0, 200)}`);
    });
  }
  eq(Object.values(I.files).reduce((a, f) => a + f.n, 0), I.n, 'index.json: сумма по файлам');
  // 2. вид по умолчанию: пары шорта, «шорт + лонг» скрыт, на виде только управление, холст и одна таблица
  let d = await dom();
  eq([d.view, d.side, d.lv, d.bothHidden, d.total, d.n, d.hash], ['fan', 'short', '2', true, I.files['S-2'].n, I.files['S-2'].n, '#equity/fan/short/lv=2'], 'вид по умолчанию');
  eq(d.cnt.replace(/\s/g, ''), `линий${d.n}из${d.total}`, 'счётчик линий');
  ok(d.rows[0].kind === 'anchor' && d.rows.length === 31 && d.head[1] === 'Линия' && d.ink > 2000, 'таблица: база и 30 лучших; холст не пуст: ' + JSON.stringify([d.rows.length, d.ink]));
  const clean = (d, m) => { ok(d.prose === 0 && !/медиан/i.test(d.text) && !/TK-\d|L-\d{3}/.test(d.text), m + ': абзацы, медианы или номера задач'); ok(d.overflow <= 0 && d.tableOver <= 0, m + ': перелив ' + d.overflow + ' / таблица ' + d.tableOver); };
  clean(d, 'по умолчанию');
  // база в таблице == база «Вариантов» (числа летописи)
  const K = await page.evaluate(async () => (await fetch('data/variants_check.json')).json());
  const J = await page.evaluate(async () => (await fetch('data/variants.json')).json());
  const anchorRow = (d, side) => { const a = K.single[J.default[side]], r = d.rows[0]; ok(r.kind === 'anchor' && +r.v.n === a.n && Math.abs(+r.v.sum - a.sum) < 0.01 && Math.abs(+r.v.sharpe - a.sharpe) < 1e-4 && Math.abs(+r.v.dd - a.dd) < 0.01 && +r.v.gap === a.gap, side + ' база веера == база «Вариантов»: ' + JSON.stringify(r.v)); };
  anchorRow(d, 'short');
  await shot('default');
  // 3. уровни обеих сторон: число линий == независимый счёт; время кадра
  const clickLv = async lv => { await page.click(`#fan-lv button[data-lv="${lv}"]`); rep.clicks++; await ready(); };
  for (const side of ['short', 'long']) {
    if (side === 'long') { await page.click('#eq-tabs button[data-side="long"]'); rep.clicks++; await ready(); }
    for (const lv of ['1', '2', '3', '4', 'split', 'all']) {
      await clickLv(lv); d = await dom();
      const want = await page.evaluate(([s, l]) => window.__T.count(s, l, {}), [side, lv]);
      eq([d.total, d.n, d.lv, d.side], [want.tot, want.tot, lv, side], `${side} уровень ${lv}`);
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
  for (const k of ['sh', 'sum', 'n', 'gap']) { await setSl(k, k === 'gap' ? 136 : -1e9); }
  for (const v of [400, 300, 200, 150, 120, 100, 90]) { await setSl('dd', v); frames.push((await dom()).ms); }
  rep.ms.frames = frames; rep.ms.max = Math.max(...frames, ...Object.values(rep.ms).filter(x => typeof x === 'number'));
  ok(rep.ms.max < MS_MAX, `время кадра ${rep.ms.max.toFixed(0)} мс ≥ ${MS_MAX}`);
  // 5. топ N и порядок; признак
  await setSl('dd', 1e9);
  await page.selectOption('#fan-top', '100'); await ready(); await page.selectOption('#fan-by', 'sum'); await ready(); d = await dom();
  let want = await page.evaluate(() => window.__T.count('short', 'all', {by: 1}));
  eq([d.n, d.mode, d.rows[1].name], [100, 'lines', want.best.key], 'топ 100 по сумме');
  ok(d.hash.includes('top=100') && d.hash.includes('by=sum'), 'топ в хеше: ' + d.hash);
  await shot('top100');
  await page.selectOption('#fan-top', '0'); await ready();
  const axes = await page.evaluate(() => [...document.querySelectorAll('#fan-ax option')].map(o => [o.value, o.textContent]));
  ok(axes.length > 20 && axes.slice(1).filter(a => /^№ \d+ · /.test(a[1])).length >= axes.length - 6, 'признаки с номерами: ' + JSON.stringify(axes.slice(0, 6)));
  for (const ax of ['rf', 'TPM60', 'split']) {
    await page.selectOption('#fan-ax', ax); await ready(); d = await dom();
    want = await page.evaluate(ax => window.__T.count('short', 'all', {ax}), ax);
    eq([d.pass, d.n], [want.k, want.k], 'признак ' + ax);
    const vals = I.axes.find(a => a.name === ax).vals;
    ok(d.legend.length === 1 + vals.length && d.hash.includes('ax=' + ax), 'признак ' + ax + ': легенда значений ' + JSON.stringify(d.legend).slice(0, 160));
    if (ax === 'rf') await shot('axis-rf');
  }
  await page.selectOption('#fan-ax', ''); await ready();
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
