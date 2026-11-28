// П-42 TK-684: клик-тест вида «Варианты» вкладки «Эквити» на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk684_check.js <url страницы> <каталог выхода> [ширина]
// Делает: ошибки консоли; прежние хеши #equity/short|long|both открывают «Варианты»; клик по каждому варианту лонга и шорта — кнопка, хеш, таблица == data/variants_check.json
// (метрики линии и суммы, посчитанные tk684_metrics.py на srv) по числу и по тексту; 5 сочетаний «шорт + лонг» (линии графика, итог линии == сумма); «нет»; виды лонг / шорт / шорт + лонг;
// ссылка с хешем воспроизводит вид; на виде нет абзацев, списков и медиан; ключи клеток — только в подсказках; наведение на график; прочие виды и вкладки живы; перелив; снимки.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const base = url.split('#')[0];
const MINUS = '−';
function num(v, d, sign) { const s = Math.abs(v).toFixed(d).replace('.', ','), z = +s.replace(',', '.') === 0; return (v < 0 && !z ? MINUS : (sign && !z ? '+' : '')) + s; }
const ints = n => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
const FMT = {n: ints, sum: v => ints(num(v, 0, true)), mean: v => num(v, 2, true), sharpe: v => num(v, 2), dd: v => num(v, 0), ddp: v => num(v, 0) + ' %', gap: String, rr: v => num(v, 2), worst: v => num(v, 2, true)};
const COLS = ['n', 'sum', 'mean', 'sharpe', 'dd', 'ddp', 'gap', 'rr', 'worst'];
const HEAD = ['Линия', 'Статус', 'Сделок', 'Сумма, $', 'На сделку, $', 'Шарп (год)', 'Просадка, $', 'Просадка, % маржи', 'Перехай, сут.', 'R:R', 'Худшая сделка, $'];
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
  const rep = {url: base, width: W, errors: errs, fails: [], checks: 0, clicks: 0};
  const fail = m => rep.fails.push(m);
  const ok = (c, m) => { rep.checks++; if (!c) fail(m); };
  const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b), m + ': ' + JSON.stringify(a).slice(0, 200) + ' ≠ ' + JSON.stringify(b).slice(0, 200));
  const dom = () => page.evaluate(() => ({
    hash: location.hash,
    tab: [...document.querySelectorAll('#toptabs button[aria-selected="true"]')].map(b => b.dataset.tab).join(), pane: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id).join(),
    side: (document.querySelector('#eq-tabs button[aria-selected="true"]') || {dataset: {}}).dataset.side, views: document.querySelectorAll('#eq-views button, #eq-cmp, #eq-desc, #where-box').length,
    pick: Object.fromEntries([...document.querySelectorAll('.vr-pick')].map(r => [r.dataset.pick, [...r.querySelectorAll('button[aria-selected="true"]')].map(b => b.dataset.var)])),
    head: [...document.querySelectorAll('table[data-vtable] thead th')].map(t => t.textContent),
    rows: [...document.querySelectorAll('table[data-vtable] tbody tr:not(.fwd)')].map(r => ({line: r.dataset.line, ids: r.dataset.ids, name: r.querySelector('[data-f=name]').textContent, status: r.querySelector('[data-f=status]').textContent, title: r.querySelector('[data-f=name]').title,
      v: Object.fromEntries([...r.querySelectorAll('td.num')].map(c => [c.dataset.f, c.dataset.v])), t: Object.fromEntries([...r.querySelectorAll('td.num')].map(c => [c.dataset.f, c.textContent]))})),
    fwdRows: [...document.querySelectorAll('table[data-vtable] tbody tr.fwd')].map(r => ({line: r.dataset.line, ids: r.dataset.ids, name: r.querySelector('[data-f=name]').textContent, status: r.querySelector('[data-f=status]').textContent,
      v: Object.fromEntries([...r.querySelectorAll('td.num')].map(c => [c.dataset.f, c.dataset.v])), t: Object.fromEntries([...r.querySelectorAll('td.num')].map(c => [c.dataset.f, c.textContent]))})),
    fwdPaths: [...document.querySelectorAll('#eq-view path[data-chart^="fwd."]')].map(p => ({k: p.dataset.chart.slice(4), fin: +p.dataset.final, w: +p.getAttribute('stroke-width'), len: p.getAttribute('d').length})),
    fwdMark: [...document.querySelectorAll('#eq-view [data-chart="fwd.label"]')].map(t => t.textContent),
    paths: [...document.querySelectorAll('#eq-view path[data-chart^="var."]')].map(p => ({k: p.dataset.chart.slice(4), fin: +p.dataset.final, w: +p.getAttribute('stroke-width'), len: p.getAttribute('d').length})),
    legend: [...document.querySelectorAll('#eq-view .legend span')].map(s => s.textContent),
    prose: document.querySelectorAll('#eq-view p, #eq-view ul, #eq-view ol, #eq-view li, #eq-view .eq-note, #eq-view .badge, #eq-view a').length,
    tableOver: (t => t ? t.scrollWidth - t.parentElement.clientWidth : 0)(document.querySelector('table[data-vtable]')), cards: document.querySelectorAll('#eq-view .card').length, tables: document.querySelectorAll('#eq-view table').length,
    svgs: document.querySelectorAll('#eq-view svg[data-chart="var"]').length, text: document.getElementById('equity').innerText, overflow: document.documentElement.scrollWidth - innerWidth,
  }));
  await page.goto(base + '#equity/short', {waitUntil: 'load'});
  await page.waitForSelector('table[data-vtable]');
  const J = await page.evaluate(async () => (await fetch('data/variants.json')).json());
  const K = await page.evaluate(async () => (await fetch('data/variants_check.json')).json());
  const BY = Object.fromEntries(J.variants.map(v => [v.id, v]));
  const longs = J.variants.filter(v => v.side === 'long'), shorts = J.variants.filter(v => v.side === 'short');
  const close = (a, b) => Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(b));
  // TK-696 (25.11): четыре замороженные линии — группа «заморожено 25.11», числа == принятым в летописи (S1 / S2 — L-428; L1 / L2 — настоящие значения признаков, L-434)
  const FROZEN = {fs1: [2158, 1358.97, 11.275, 40.28, 13], fs2: [1457, 1254.2, 11.373, 40.37, 16], fl1: [174, 348.07, 7.169, 17.1, 11], fl2: [227, 382.62, 7.073, 19.13, 11]};
  for (const [id, [n, sum, sh, dd, gap]] of Object.entries(FROZEN)) { const k = K.single[id]; ok(BY[id] && BY[id].group === 'frozen' && BY[id].status === 'заморожено 25.11' && k && k.n === n && Math.abs(k.sum - sum) < 0.006 && Math.abs(k.sharpe - sh) < 0.006 && Math.abs(k.dd - dd) < 0.006 && k.gap === gap, 'замороженная линия ' + id + ': ' + JSON.stringify([BY[id] && BY[id].status, k])); }
  eq(J.groups.map(g => g.label), ['', 'заморожено 25.11'], 'группы вариантов');
  // дни вперёд (чтение «по вчера» 17–24.11): числа линий == принятым в летописи (L-438)
  const FWD = {fs1: [116, -11.39, -1.96, 43.2, 5], fs2: [79, -36.47, -8.1, 51.6, 5], fl1: [9, 25.65, 12.38, 0, 2], fl2: [12, 25.49, 11.44, 0.8, 2]};
  for (const [id, [n, sum, sh, dd, gap]] of Object.entries(FWD)) { const k = K.fwd.single[id]; ok(BY[id].fwd && k && k.n === n && Math.abs(k.sum - sum) < 0.006 && Math.abs(k.sharpe - sh) < 0.006 && Math.abs(k.dd - dd) < 0.06 && k.gap === gap && BY[id].fwd.t.length === n, 'вперёд ' + id + ': ' + JSON.stringify(k)); }
  eq(J.variants.filter(v => v.fwd).map(v => v.id).sort(), Object.keys(FWD).sort(), 'дни вперёд — только у замороженных линий');
  eq([J.forward.label, J.forward.days], ['17–24.11', 8], 'описание дней вперёд');
  const fwdIds = ids => ids.split('|').every(i => BY[i] && BY[i].fwd);
  // таблица == данные: строки по выбранным линиям, числа (data-v) и текст ячеек
  const checkTable = (d, l, s, m) => {
    const want = [];
    const stOf = ids => [...new Set(ids.map(i => BY[i].status).filter(Boolean))].join(' · ');
    if (l) want.push(['long', l, 'Лонг · ' + BY[l].label, K.single[l], BY[l].key]);
    if (s) want.push(['short', s, 'Шорт · ' + BY[s].label, K.single[s], BY[s].key]);
    if (l && s) want.push(['both', l + '|' + s, 'Шорт + лонг', K.both[l + '|' + s], BY[l].key + ' + ' + BY[s].key]);
    eq(d.head, HEAD, m + ' шапка');
    eq(d.rows.map(r => [r.line, r.ids, r.name, r.title, r.status]), want.map(w => [w[0], w[1], w[2], w[4], stOf(w[1].split('|'))]), m + ' строки');
    want.forEach((w, i) => { const r = d.rows[i]; if (!r) return; COLS.forEach(c => {
      ok(w[3][c] == null ? (r.v[c] === '' || r.v[c] == null) : close(+r.v[c], w[3][c]), `${m} ${w[0]} ${c}: ${r.v[c]} ≠ ${w[3][c]}`);      // TK-744: у линии без сделок (sv1) Шарп, на сделку, R:R, худшая — null, на странице «—»
      eq(r.t[c], w[3][c] == null ? '—' : FMT[c](w[3][c]), `${m} ${w[0]} ${c} текст`);
    }); });
    const wf = want.filter(w => fwdIds(w[1]));      // строки «вперёд» — у линий, где дни вперёд есть у всех частей
    eq(d.fwdRows.map(r => [r.line, r.ids, r.name, r.status]), wf.map(w => [w[0] + '-fwd', w[1], w[2], 'вперёд ' + J.forward.label]), m + ' строки «вперёд»');
    wf.forEach((w, i) => { const r = d.fwdRows[i]; if (!r) return; const fk = w[0] === 'both' ? K.fwd.both[w[1]] : K.fwd.single[w[1]]; COLS.forEach(c => {
      ok(fk[c] != null && close(+r.v[c], fk[c]), `${m} вперёд ${w[0]} ${c}: ${r.v[c]} ≠ ${fk[c]}`);
      eq(r.t[c], FMT[c](fk[c]), `${m} вперёд ${w[0]} ${c} текст`);
    }); });
    ok(d.prose === 0 && d.views === 2 && d.tableOver <= 0 && d.cards === 1 && d.tables === 1 && d.svgs === 1, m + ' на виде только выбор, график и одна таблица: ' + JSON.stringify([d.prose, d.views, d.tableOver, d.cards, d.tables, d.svgs]));
    ok(!/медиан/i.test(d.text) && !/TK-\d|L-\d{3}/.test(d.text), m + ' медиана или номер задачи в тексте');
    ok(d.overflow <= 0, m + ' горизонтальный перелив ' + d.overflow);
  };
  // график: линии вида и их итог == сумма сделок линии
  const checkChart = (d, side, l, s, m) => {
    const want = [];
    if (side === 'both' && l && s) want.push(['both', K.both[l + '|' + s].sum, 2.6, K.both[l + '|' + s].n]);
    if (side !== 'short' && l) want.push(['long', K.single[l].sum, side === 'both' && s ? 1.3 : 2.6, K.single[l].n]);
    if (side !== 'long' && s) want.push(['short', K.single[s].sum, side === 'both' && l ? 1.3 : 2.6, K.single[s].n]);
    eq(d.paths.map(p => p.k).sort(), want.map(w => w[0]).sort(), m + ' линии графика');
    want.forEach(w => { const p = d.paths.find(x => x.k === w[0]); if (p) ok(close(p.fin, w[1]) && p.w === w[2] && p.len > (w[3] ? 50 : 10), `${m} линия ${w[0]}: итог ${p.fin} ≠ ${w[1]} или толщина ${p.w} ≠ ${w[2]}`); });
    eq(d.legend.length, want.length, m + ' легенда');
    const wf = want.filter(w => w[0] === 'both' ? fwdIds(l + '|' + s) : fwdIds(w[0] === 'long' ? l : s));      // дни вперёд: свой путь у линий, где они есть; итог = итог окна + сделки чтения
    eq(d.fwdPaths.map(p => p.k).sort(), wf.map(w => w[0]).sort(), m + ' пути «вперёд»');
    wf.forEach(w => { const p = d.fwdPaths.find(x => x.k === w[0]); const fk = w[0] === 'both' ? K.fwd.both[l + '|' + s] : K.fwd.single[w[0] === 'long' ? l : s]; if (p) ok(close(p.fin, w[1] + fk.sum) && p.w === (d.paths.find(x => x.k === w[0]) || {}).w, `${m} путь вперёд ${w[0]}: итог ${p.fin} ≠ ${w[1]} + ${fk.sum}`); });
    eq(d.fwdMark, wf.length ? ['вперёд ' + J.forward.label] : [], m + ' метка «вперёд»');
  };
  const state = async (side, l, s, m) => {
    const d = await dom();
    eq([d.hash, d.tab, d.pane, d.side], [`#equity/${side}/${l || '-'}/${s || '-'}`, 'equity', 'pane-equity', side], m + ' состояние');
    eq(d.pick, {long: [l || '-'], short: [s || '-']}, m + ' выбранные кнопки');
    checkTable(d, l, s, m); checkChart(d, side, l, s, m);
    if (d.svgs === 1) {      // «Эквити» — на всю ширину окна; график +20 % (высота поля графика 360 / 432 вместо 300 / 360)
      const gm = await page.evaluate(() => { const vb = document.querySelector('#eq-view svg[data-chart="var"]').viewBox.baseVal; return {vw: vb.width, vh: vb.height, ww: document.querySelector('.wrap').getBoundingClientRect().width, cw: document.documentElement.clientWidth}; });
      ok(Math.abs(gm.ww - gm.cw) <= 1 && gm.vh === (gm.vw < 520 ? 360 : 432), m + ' ширина листа и высота графика: ' + JSON.stringify(gm));
    }
    return d;
  };
  const clickVar = async (side, id) => { await page.click(`.vr-pick[data-pick="${side}"] button[data-var="${id}"]`); rep.clicks++; };
  const clickSide = async side => { await page.click(`#eq-tabs button[data-side="${side}"]`); rep.clicks++; };
  const shot = n => page.screenshot({path: `${out}/${n}-${W}.png`, fullPage: true});

  // прежний хеш #equity/short → «Варианты», выбор по умолчанию
  const dl = J.default.long, ds = J.default.short;
  await state('short', dl, ds, 'хеш #equity/short');
  // кнопки: все варианты, подписи ≤ 6 слов без ключей, ключ клетки — в подсказке; группа замороженных подписана
  const btns = await page.evaluate(() => [...document.querySelectorAll('.vr-pick')].map(r => ({side: r.dataset.pick, b: [...r.querySelectorAll('button')].map(b => [b.dataset.var, b.textContent, b.title]), grp: [...r.querySelectorAll('.vr-grp')].map(g => g.textContent)})));
  for (const [side, list] of [['long', longs], ['short', shorts]]) {
    const r = btns.find(x => x.side === side);
    eq(r.b, [['-', 'нет', '']].concat(list.map(v => [v.id, v.label, v.key])), side + ' кнопки выбора');
    list.forEach(v => ok(v.group === 'frozen' ? /заморожено/.test(v.status) : /пара|тройка|четвёрка|двумя/.test(v.label) ? v.status === 'подобрано на этих данных' : v.status === '', side + ' статус ' + v.id));
    eq(r.grp, J.groups.filter(g => g.label && list.some(v => v.group === g.id)).map(g => g.label), side + ' подпись группы');
    list.forEach(v => ok(v.label.replace(/·/g, ' ').trim().split(/\s+/).length <= 6 && !v.label.includes(v.key), side + ' подпись ' + v.label));
  }
  await shot('default-short');
  // каждый вариант лонга (вид «шорт» → клик по лонгу переводит в «шорт + лонг»)
  let first = true;
  for (const v of longs) { await clickVar('long', v.id); await state(first ? 'both' : 'both', v.id, ds, 'лонг ' + v.id); first = false; }
  await clickSide('long');
  for (const v of longs) { await clickVar('long', v.id); await state('long', v.id, ds, 'вид лонг, лонг ' + v.id); }
  // каждый вариант шорта
  await clickSide('short');
  const lastL = longs[longs.length - 1].id;
  for (const v of shorts) { await clickVar('short', v.id); await state('short', lastL, v.id, 'вид шорт, шорт ' + v.id); }
  await clickSide('both');
  for (const v of shorts) { await clickVar('short', v.id); await state('both', lastL, v.id, 'шорт ' + v.id); }
  // 5 сочетаний «шорт + лонг»
  const combos = [[longs[0].id, shorts[0].id], [longs[1 % longs.length].id, shorts[1 % shorts.length].id], [longs[3 % longs.length].id, shorts[4 % shorts.length].id], [longs[4 % longs.length].id, shorts[shorts.length - 1].id], [longs[longs.length - 1].id, shorts[3 % shorts.length].id]];
  for (const [i, [l, s]] of combos.entries()) {
    await clickVar('long', l); await clickVar('short', s);
    await state('both', l, s, `сочетание ${l} + ${s}`);
    if (i === 2) {
      await shot('both-' + l + '-' + s);
      const loc = page.locator('rect[data-chart="var.hover"]'); await loc.scrollIntoViewIfNeeded(); const b = await loc.boundingBox();
      await page.mouse.move(b.x + b.width * 0.6, b.y + b.height / 2); await page.waitForTimeout(150);
      const tip = await page.evaluate(() => { const t = document.getElementById('tip'); return {shown: t.style.display === 'block', rows: t.querySelectorAll('.r').length, text: t.innerText}; });
      ok(tip.shown && tip.rows === 3 && /UTC/.test(tip.text), 'наведение на график: ' + JSON.stringify(tip));
      await page.screenshot({path: `${out}/hover-${W}.png`});
      await page.mouse.move(2, 2);
    }
  }
  // виды лонг / шорт / шорт + лонг на одном выборе
  const [cl, cs] = combos[combos.length - 1];
  for (const side of ['long', 'short', 'both']) { await clickSide(side); await state(side, cl, cs, 'вид ' + side); if (side !== 'both') await shot('view-' + side); }
  // «нет»
  await clickVar('long', '-'); await state('both', null, cs, 'лонг нет');
  await clickVar('short', '-'); const empty = await state('both', null, null, 'оба нет'); eq(empty.rows.length, 0, 'оба нет — строк нет');
  await clickVar('long', cl); await state('both', cl, null, 'шорт нет');
  await clickSide('short'); await state('short', cl, null, 'вид шорт без шорта');
  await clickVar('short', cs); await state('short', cl, cs, 'шорт возвращён');
  await clickSide('long'); await clickVar('short', shorts[0].id); await state('both', cl, shorts[0].id, 'клик по шорту в виде «лонг» → шорт + лонг');
  // ссылка с хешем воспроизводит вид (новая загрузка) и переход по хешу без загрузки
  for (const [side, l, s] of [['both', combos[2][0], combos[2][1]], ['long', combos[3][0], combos[3][1]], ['short', null, combos[4][1]], ['both', combos[1][0], null]]) {
    const hsh = `#equity/${side}/${l || '-'}/${s || '-'}`;
    await page.goto('about:blank'); await page.goto(base + hsh, {waitUntil: 'load'}); await page.waitForSelector('#eq-view svg[data-chart="var"]');
    await state(side, l, s, 'загрузка по ссылке ' + hsh);
  }
  await page.evaluate(h => { location.hash = h; }, `#equity/both/${combos[0][0]}/${combos[0][1]}`); await page.waitForTimeout(250);
  await state('both', combos[0][0], combos[0][1], 'переход по хешу');
  // прежние хеши: стороны — «Варианты» с выбором по умолчанию; неизвестный id — выбор по умолчанию; прочие виды живы
  for (const side of ['short', 'long', 'both']) { await page.goto('about:blank'); await page.goto(base + '#equity/' + side, {waitUntil: 'load'}); await page.waitForSelector('table[data-vtable]'); await state(side, dl, ds, 'прежний хеш #equity/' + side); }
  await page.goto('about:blank'); await page.goto(base + '#equity/both/zzz/' + shorts[1].id, {waitUntil: 'load'}); await page.waitForSelector('table[data-vtable]'); await state('both', dl, shorts[1].id, 'неизвестный id лонга');
  // убранные виды («Где мы», «Кривая», «Базы», «Форма»): прежние ссылки открывают «Варианты» с базами
  for (const [hsh, side] of [['#equity/short/where', 'short'], ['#equity/long/bases', 'long'], ['#equity/short/shape', 'short'], ['#equity/both/curve', 'both'], ['#equity/long/comp/dep=7000', 'long']]) {
    await page.goto('about:blank'); await page.goto(base + hsh, {waitUntil: 'load'}); await page.waitForSelector('table[data-vtable]'); await state(side, dl, ds, 'прежний вид ' + hsh);
  }
  // верхние вкладки — ровно три (TK-696: «Цели TK-598» убрана; TK-741: «Сделки»); прежние хеши убранных вкладок (#live, #progress, #model, …) и пустой хеш открывают «Эквити» · «Варианты» без ошибок консоли
  eq(await page.evaluate(() => [...document.querySelectorAll('#toptabs button')].map(b => b.innerText)), ['Эквити', 'Сделки', 'Признаки'], 'верхние вкладки');
  for (const hsh of ['#live', '#progress', '#model', '#model/long', '#long', '#short', '#targets', '']) {
    const e0 = errs.length;
    await page.goto('about:blank'); await page.goto(base + hsh, {waitUntil: 'load'}); await page.waitForSelector('table[data-vtable]');
    await state('short', dl, ds, 'старый хеш «' + hsh + '» → «Эквити» · «Варианты»');
    eq(errs.slice(e0), [], 'старый хеш «' + hsh + '»: ошибки консоли');
  }
  // остальные вкладки живы
  const tabs = await page.evaluate(() => [...document.querySelectorAll('#toptabs button')].map(b => b.dataset.tab));
  for (const k of tabs) {
    await page.click(`#toptabs button[data-tab="${k}"]`); await page.waitForTimeout(250);
    const d = await page.evaluate(() => ({pane: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id).join(), h: [...document.querySelectorAll('.pane')].filter(p => !p.hidden)[0].innerText.length}));
    ok(d.pane === 'pane-' + k && d.h > 40, 'вкладка ' + k + ': ' + JSON.stringify(d));
    const ww = await page.evaluate(() => [document.querySelector('.wrap').getBoundingClientRect().width, document.documentElement.clientWidth]);      // остальные вкладки — прежней ширины (не шире 1180 + поля)
    ok(k === 'equity' || k === 'trades' ? Math.abs(ww[0] - ww[1]) <= 1 : ww[0] <= 1180 + 1 && Math.abs(ww[0] - Math.min(1180, ww[1])) <= 1, 'ширина листа вкладки ' + k + ': ' + JSON.stringify(ww));
  }
  await page.click('#toptabs button[data-tab="equity"]'); await page.waitForSelector('table[data-vtable]');
  await clickSide('both'); await state('both', dl, ds, 'возврат на «Эквити»');
  await shot('final-both');
  rep.variants = {long: longs.map(v => v.id), short: shorts.map(v => v.id)};
  fs.writeFileSync(`${out}/report684-${W}.json`, JSON.stringify(rep, null, 1));
  console.log(JSON.stringify({width: W, checks: rep.checks, clicks: rep.clicks, errors: errs, fails: rep.fails.slice(0, 20), nfails: rep.fails.length}, null, 1));
  await browser.close();
  process.exit(errs.length || rep.fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
