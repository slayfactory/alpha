// П-42 TK-741: клик-тест вкладки «Сделки» на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk741_check.js <url страницы> <каталог выхода> [ширина]
// Делает: ошибки консоли; вкладка и хеш #trades/<период>/<кластер>[/<№>]; кнопки периодов (число сделок == data/trades/check.json из БД); сводка кластеров (числа == БД по тексту) для всех периодов;
// клик по кластеру — фильтр (число строк, кластер в каждой строке, Σ $ строк == БД), повторный клик снимает; сортировка по столбцам (время, монета, $, кластер, признак) в обе стороны;
// клик по сделке — график ±2 ч (свечи == data/trades/c/<№>.json, уровень, вход, выход на своих местах по цене и времени), числа сетапа == index.json; закрытие; ссылка с хешем воспроизводит вид;
// узкий экран — без перелива; «Эквити» и «Признаки» живы; вариант «база, без выхода по времени» в выборе; снимки.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const base = url.split('#')[0];
const MINUS = '−';
function num(v, d, sign) { const s = Math.abs(v).toFixed(d).replace('.', ','), z = +s.replace(',', '.') === 0; return (v < 0 && !z ? MINUS : (sign && !z ? '+' : '')) + s; }
const ints = n => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
const parse = t => { const s = t.replace(MINUS, '-').replace(/\s/g, '').replace(',', '.').replace('+', ''); return s === '—' ? null : +s; };
const PER = [['main', 'фев–13.11'], ['JAN', 'январь'], ['N1', '14–24.11'], ['all', 'все']];
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
  const rep = {url: base, width: W, errors: errs, fails: [], checks: 0, clicks: 0, shots: []};
  const fail = m => rep.fails.push(m);
  const ok = (c, m) => { rep.checks++; if (!c) fail(m); };
  const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b), m + ': ' + JSON.stringify(a).slice(0, 200) + ' ≠ ' + JSON.stringify(b).slice(0, 200));
  const shot = async n => { const f = `${out}/tk741-${W}-${n}.png`; await page.screenshot({path: f, fullPage: false}); rep.shots.push(f); };
  const json = async p => page.evaluate(async u => (await fetch(u, {cache: 'no-cache'})).json(), base + p);
  await page.goto(base + '#trades/main/0'); await page.waitForSelector('#tr-per button', {timeout: 20000});
  const chk = await json('data/trades/check.json');
  const idx = await json('data/trades/index.json');
  const cols = idx.cols, T = {};
  idx.trades.forEach(a => { const r = {}; cols.forEach((c, k) => { r[c] = a[k]; }); T[r.i] = r; });
  const cids = [0, ...idx.clusters.map(c => c.id)];
  const PERSET = {main: ['FJ', 'SEL'], JAN: ['JAN'], N1: ['N1'], all: ['FJ', 'SEL', 'JAN', 'N1']};
  const dom = () => page.evaluate(() => ({
    hash: location.hash, tab: [...document.querySelectorAll('#toptabs button[aria-selected="true"]')].map(b => b.dataset.tab).join(), pane: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id).join(),
    per: [...document.querySelectorAll('#tr-per button')].map(b => ({k: b.dataset.per, sel: b.getAttribute('aria-selected') === 'true', t: b.textContent})),
    sum: [...document.querySelectorAll('table[data-trs] tbody tr')].map(r => ({cl: +r.dataset.cl, sel: r.classList.contains('sel'), t: Object.fromEntries([...r.querySelectorAll('td')].map(c => [c.dataset.f, c.textContent]))})),
    rows: [...document.querySelectorAll('table[data-trt] tbody tr')].map(r => ({i: +r.dataset.i, cl: +r.dataset.cl, sel: r.classList.contains('sel'), t: Object.fromEntries([...r.querySelectorAll('td')].map(c => [c.dataset.f, c.textContent]))})),
    cnt: (document.getElementById('tr-cnt') || {}).textContent, heads: [...document.querySelectorAll('table[data-trt] thead th')].map(t => t.dataset.sort),
    detail: document.getElementById('tr-detail') ? {i: +document.getElementById('tr-detail').dataset.i, title: document.querySelector('#tr-detail [data-f=title]').textContent, sub: document.querySelector('#tr-detail [data-f=sub]').textContent,
      setup: Object.fromEntries([...document.querySelectorAll('#tr-detail .tr-feats > div')].map(d => [d.dataset.f, d.querySelector('b').textContent]))} : null,
    over: document.documentElement.scrollWidth - document.documentElement.clientWidth, text: document.getElementById('trades').innerText}));
  const go = async (hash) => { await page.goto(base + hash); await page.waitForSelector('#tr-per button', {timeout: 20000}); };
  await go('#trades/main/0');
  let d = await dom();
  eq(d.tab, 'trades', 'вкладка'); eq(d.pane, 'pane-trades', 'панель');
  eq(d.per.map(p => p.k), PER.map(p => p[0]), 'кнопки периодов');
  // периоды: кнопка, сводка, таблица == БД
  for (const [k, lab] of PER) {
    await page.click(`#tr-per button[data-per="${k}"]`); rep.clicks++;
    d = await dom();
    eq(d.hash, `#trades/${k}/0`, 'хеш периода ' + k);
    eq(d.per.filter(p => p.sel).map(p => p.k), [k], 'выбран период ' + k);
    eq(d.per.find(p => p.k === k).t, `${lab} · ${chk.clusters[k + '/0'].n}`, 'число сделок на кнопке ' + k);
    for (const r of d.sum) {
      const c = chk.clusters[`${k}/${r.cl}`];
      eq([r.t.n, r.t.sum, r.t.mean, r.t.rr, r.t.stops, r.t.lps],
        [ints(c.n), c.n ? num(c.sum, 2, true) : num(0, 2, true), c.mean == null ? '—' : num(c.mean, 2, true), c.rr == null ? '—' : num(c.rr, 2), String(c.stops), c.loss_per_stop == null ? '—' : num(c.loss_per_stop, 2, true)], `сводка ${k} К${r.cl}`);
      eq(r.t.share, chk.clusters[`${k}/0`].n ? num(c.n / chk.clusters[`${k}/0`].n * 100, 0) : '—', `доля ${k} К${r.cl}`);
    }
    eq(d.sum.map(r => r.cl), cids, 'строки сводки ' + k);
    eq(d.rows.length, chk.clusters[k + '/0'].n, 'строк таблицы ' + k);
    const s = d.rows.reduce((a, r) => a + parse(r.t.pnl), 0);
    ok(Math.abs(s - chk.clusters[k + '/0'].sum) < 0.006 * d.rows.length + 0.01, `Σ $ таблицы ${k}: ${s.toFixed(2)} ≠ ${chk.clusters[k + '/0'].sum.toFixed(2)}`);
    const sub = d.rows.every(r => PERSET[k].includes(T[r.i].per));
    ok(sub, 'строки не из периода ' + k);
  }
  // фильтр по кластеру (основной период)
  await page.click('#tr-per button[data-per="main"]'); rep.clicks++;
  for (const cid of cids.filter(c => c)) {
    await page.click(`table[data-trs] tbody tr[data-cl="${cid}"]`); rep.clicks++;
    d = await dom();
    eq(d.hash, `#trades/main/${cid}`, 'хеш кластера');
    eq(d.sum.filter(r => r.sel).map(r => r.cl), [cid], 'выбран кластер ' + cid);
    const c = chk.clusters[`main/${cid}`];
    eq(d.rows.length, c.n, 'строк кластера ' + cid);
    ok(d.rows.every(r => r.cl === cid && r.t.cl === 'К' + cid), 'в таблице чужой кластер ' + cid);
    const s = d.rows.reduce((a, r) => a + parse(r.t.pnl), 0);
    ok(Math.abs(s - c.sum) < 0.006 * d.rows.length + 0.01, `Σ $ кластера ${cid}: ${s.toFixed(2)} ≠ ${c.sum.toFixed(2)}`);
    eq(d.cnt, `${ints(c.n)} сделок`, 'счётчик кластера ' + cid);
    const stops = d.rows.filter(r => r.t.kind === 'стоп');
    eq(stops.length, c.stops, 'стопов в таблице кластера ' + cid);
    if (cid === cids[1]) await shot('cluster-' + cid);
    await page.click(`table[data-trs] tbody tr[data-cl="${cid}"]`); rep.clicks++;
    d = await dom();
    eq(d.hash, '#trades/main/0', 'повторный клик снимает фильтр');
    eq(d.rows.length, chk.clusters['main/0'].n, 'после снятия фильтра');
  }
  // сортировка
  const SK = {time: r => T[r.i].entry_ms, sym: r => T[r.i].sym, pnl: r => parse(r.t.pnl), cl: r => r.cl, kind: r => T[r.i].kind, r60: r => parse(r.t.r60), turn: r => parse(r.t.turn), age: r => parse(r.t.age)};
  const sorted = (rows, f, dir) => rows.every((r, k) => k === 0 || (dir === 'a' ? f(rows[k - 1]) <= f(r) : f(rows[k - 1]) >= f(r)));
  const DEF = {time: 'a', sym: 'a', kind: 'a'};
  for (const k of Object.keys(SK)) {
    await page.click(`table[data-trt] thead th[data-sort="${k}"]`); rep.clicks++;
    d = await dom();
    const first = DEF[k] || 'd', second = first === 'd' ? 'a' : 'd';
    ok(sorted(d.rows, SK[k], first), `порядок ${k}: первый клик → ${first}`);
    await page.click(`table[data-trt] thead th[data-sort="${k}"]`); rep.clicks++;
    d = await dom();
    ok(sorted(d.rows, SK[k], second), `порядок ${k}: второй клик → ${second}`);
  }
  await page.click('table[data-trt] thead th[data-sort="time"]'); rep.clicks++;
  // сделки: график
  d = await dom();
  const pickI = [];
  const byKind = {};
  d.rows.forEach(r => { (byKind[r.t.kind] = byKind[r.t.kind] || []).push(r.i); });
  Object.values(byKind).forEach(a => pickI.push(a[0], a[a.length >> 1]));
  const forCl = d.rows.find(r => r.cl === cids[cids.length - 1]); if (forCl) pickI.push(forCl.i);
  let shotted = false;
  for (const i of [...new Set(pickI)]) {
    await page.click(`table[data-trt] tbody tr[data-i="${i}"]`); rep.clicks++;
    await page.waitForSelector('#tr-detail [data-chart="trade"][data-ready="1"]', {timeout: 15000});
    d = await dom();
    const x = T[i];
    eq(d.hash, `#trades/main/0/${i}`, 'хеш сделки ' + i);
    ok(d.detail && d.detail.i === i, 'карточка сделки ' + i);
    ok(d.detail.title.startsWith(x.sym), 'заголовок: ' + d.detail.title);
    ok(d.detail.sub.includes(idx.kinds[x.kind]), 'вид выхода в подписи ' + i);
    ok(d.rows.find(r => r.i === i).sel, 'строка выделена ' + i);
    const ix = k => idx.feats.indexOf(k);
    const setup = d.detail.setup, fm = {r15: v => num(v, 1, true), r60: v => num(v, 1, true), r240: v => num(v, 1, true), rv60: v => num(v, 1), near: v => num(v * 100, 0), touch: v => String(v), age: v => num(v, 0), turn: v => num(v, 1), stab: v => String(v), hi24: v => num(v, 1), lo24: v => num(v, 1), btc60: v => num(v, 2, true)};
    eq(setup, Object.fromEntries(idx.feats.map(k => [k, x.f[ix(k)] == null ? '—' : fm[k](x.f[ix(k)])])), 'числа сетапа ' + i);
    // TK-742: график — lightweight-charts (подробные проверки — tk742_check.js); здесь: свечи, уровень, вход, выход есть и совпадают с данными сделки
    const g = await page.evaluate(() => ({dbg: window.P42Trades.debug(), box: document.querySelector('#tr-detail [data-chart="trade"]').getBoundingClientRect().width, n: +document.querySelector('#tr-detail [data-chart="trade"]').dataset.n}));
    ok(g.dbg && g.dbg.tf === '1m', 'график: таймфрейм 1м по умолчанию ' + i);
    ok(g.n > 0 && g.dbg.bars.filter(b => b).length === g.n, 'свечей на графике ' + i);
    eq(g.dbg.lines.map(l => [l.k, +l.price.toFixed(8)]), [['level', x.lvl], ['entry', x.entry_px], ['exit', x.exit_px]], 'уровень / вход / выход на графике ' + i);
    ok(g.dbg.markers.length === 2 && g.dbg.markers.some(m => m.shape === 'arrowDown') && g.dbg.markers.some(m => m.shape === 'arrowUp'), 'отметки входа и выхода ' + i);
    ok(x.exit_ms >= x.entry_ms, 'выход не раньше входа ' + i);
    ok(g.box <= W + 1, 'график шире окна ' + i);
    if (!shotted) { await shot('detail-' + i); shotted = true; }
    // наведение: подсказка со временем GMT+4
    await page.waitForTimeout(900);      // страница прокручивается к карточке плавно (scroll-behavior: smooth)
    const cb = await page.locator('#tr-detail [data-chart="trade"]').boundingBox();
    await page.mouse.move(cb.x + cb.width / 2, cb.y + cb.height / 2); await page.mouse.move(cb.x + cb.width / 2 + 5, cb.y + cb.height / 2 + 3);
    const tip = await page.evaluate(() => { const t = document.querySelector('#tr-detail [data-chart="trade.tip"]'); return t && t.style.display === 'block' ? t.textContent : ''; });
    ok(/GMT\+4/.test(tip), 'подсказка графика ' + i + ': ' + tip.slice(0, 60));
    await page.mouse.move(2, 2);
  }
  // закрытие карточки
  await page.click('#tr-detail .tr-x'); rep.clicks++;
  d = await dom();
  eq(d.hash, '#trades/main/0', 'хеш после закрытия'); ok(!d.detail, 'карточка закрыта');
  // ссылка с хешем воспроизводит вид
  const anyJan = Object.values(T).find(r => r.per === 'JAN' && r.cluster);
  await go(`#trades/JAN/${anyJan.cluster}/${anyJan.i}`);
  await page.waitForSelector('#tr-detail [data-chart="trade"][data-ready="1"]', {timeout: 15000});
  d = await dom();
  eq(d.hash, `#trades/JAN/${anyJan.cluster}/${anyJan.i}`, 'ссылка воспроизводит хеш');
  eq([d.per.filter(p => p.sel).map(p => p.k), d.sum.filter(r => r.sel).map(r => r.cl), d.detail && d.detail.i], [['JAN'], [anyJan.cluster], anyJan.i], 'ссылка воспроизводит вид');
  eq(d.rows.length, chk.clusters[`JAN/${anyJan.cluster}`].n, 'строк в ссылке');
  // не из периода: сделка чужого периода в хеше — карточка не открывается
  await go(`#trades/JAN/0/${Object.values(T).find(r => r.per === 'FJ').i}`);
  d = await dom();
  ok(d.rows.length === chk.clusters['JAN/0'].n, 'чужая сделка в хеше: таблица периода');
  // текст вкладки
  await go('#trades/main/0');
  d = await dom();
  ok(/найдены на этих данных/.test(d.text), 'метка «найдены на этих данных»');
  ok(!/\bbp\b|\bбп\b|\bмедиан/i.test(d.text.replace(/медианы:/g, '')), 'в тексте вкладки bp / медиана');
  ok(/GMT\+4/.test(d.text), 'время GMT+4');
  ok(d.over <= 1, 'перелив вправо ' + d.over);
  await shot('summary');
  // узкий экран: перелив, заголовки для сортировки
  if (W <= 640) {
    const hv = await page.evaluate(() => getComputedStyle(document.querySelector('table[data-trt] thead')).display);
    ok(hv !== 'none', 'заголовки сортировки на узком экране скрыты');
    await page.click('table[data-trt] tbody tr:nth-child(3)'); rep.clicks++;
    await page.waitForSelector('#tr-detail [data-chart="trade"][data-ready="1"]', {timeout: 15000});
    d = await dom(); ok(d.over <= 1, 'перелив с карточкой ' + d.over);
    await shot('mobile-detail');
  }
  // «Эквити»: вариант без выхода по времени в выборе; «Признаки» жив
  await page.goto(base + '#equity/short/-/san'); await page.waitForSelector('#eq-view', {timeout: 20000});
  const san = await page.evaluate(() => ({btn: [...document.querySelectorAll('.vr-pick button[data-var="san"]')].map(b => b.textContent), sel: [...document.querySelectorAll('.vr-pick button[aria-selected="true"]')].map(b => b.dataset.var), hash: location.hash}));
  ok(san.btn.length === 1 && /без выхода по времени/.test(san.btn[0]), 'вариант san в выборе: ' + JSON.stringify(san.btn));
  ok(san.sel.includes('san'), 'san выбран по хешу: ' + JSON.stringify(san.sel));
  await shot('equity-san');
  await page.goto(base + '#features'); await page.waitForTimeout(500);
  const ft = await page.evaluate(() => [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id).join());
  eq(ft, 'pane-features', 'вкладка «Признаки»');
  await page.click('#toptabs button[data-tab="trades"]'); rep.clicks++;
  await page.waitForSelector('#tr-per button'); d = await dom();
  eq(d.tab, 'trades', 'переход на «Сделки» кнопкой');
  eq(errs, [], 'ошибки консоли');
  await browser.close();
  rep.ok = rep.fails.length === 0 && errs.length === 0;
  fs.writeFileSync(`${out}/tk741-check-${W}.json`, JSON.stringify(rep, null, 1));
  console.log(JSON.stringify({width: W, checks: rep.checks, clicks: rep.clicks, fails: rep.fails.slice(0, 20), nfails: rep.fails.length, errors: errs.slice(0, 5), ok: rep.ok}));
  process.exit(rep.ok ? 0 : 1);
})().catch(e => { console.error('CRASH', e); process.exit(2); });
