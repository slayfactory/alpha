// П-42 TK-606: клик-тест вкладки «Признаки» на VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk606_check.js <url страницы> <каталог выхода> [ширина]
// Делает: ошибки консоли; вкладка «Признаки» по клику и по хешу (#features, #features/<набор>, #features/<набор>/<№>); сверка DOM с data/features.json
// (наборы, группы, строки: №, имя, «что значит», база, перебор); TK-678: на вкладке только заголовки и таблицы — никаких абзацев, списков, плашек, ссылок на отчёты;
// колонка «База» / «Перебор» есть только там, где в наборе есть значение, «—» и «нет» не печатаются; сквозная нумерация 1…N; строка по номеру подсвечивается
// и встаёт под липкие панели; клик по номеру ставит хеш; текст таблицы копируется как «№ ⇥ имя ⇥ что значит ⇥ база ⇥ перебор»;
// кнопки навигации; остальные вкладки живы; повторный заход не дублирует содержимое; горизонтальный перелив; снимки.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const base = url.split('#')[0];
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: W, height: 900}});
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
  const rep = {url: base, width: W, errors: errs, checks: {}, fails: []};
  const C = rep.checks;
  const fail = m => rep.fails.push(m);
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  const state = () => page.evaluate(() => ({hash: location.hash, selected: [...document.querySelectorAll('#toptabs button[aria-selected="true"]')].map(b => b.dataset.tab), visible: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id)}));
  const settle = async () => { let last = -1, same = 0; for (let k = 0; k < 80 && same < 4; k++) { await page.waitForTimeout(150); const y = await page.evaluate(() => scrollY); same = y === last ? same + 1 : 0; last = y; } };
  await page.goto(base, {waitUntil: 'load'});
  const J = await page.evaluate(async () => (await fetch('data/features.json')).json());
  C.tabs = await page.evaluate(() => [...document.querySelectorAll('#toptabs button')].map(b => b.dataset.tab + '|' + b.innerText));
  C.default = await state();
  if (!C.tabs.includes('features|Признаки')) fail('нет вкладки «Признаки»');
  await page.click('#toptabs button[data-tab="features"]');
  C.afterClick = await state();
  if (C.afterClick.hash !== '#features' || C.afterClick.visible.join() !== 'pane-features' || C.afterClick.selected.join() !== 'features') fail('клик по вкладке: ' + JSON.stringify(C.afterClick));
  const dom = await page.evaluate(() => ({
    title: document.querySelector('#features .prog-h h2').innerText, meta: document.querySelector('#features .prog-h .muted').innerText,
    navs: [...document.querySelectorAll('.ft-bar button')].map(b => b.dataset.go + '|' + b.innerText),
    // всё, что не заголовок, не навигация и не таблица
    prose: [...document.querySelectorAll('#features p, #features ul, #features ol, #features li, #features .badge, #features .lbl, #features .nums, #features a, #features [data-f=foot], #features [data-f=status], #features [data-f=mark], #features [data-f=src]')].length,
    kids: [...document.getElementById('features').children].map(e => e.tagName + '.' + e.className),
    sets: [...document.querySelectorAll('section.ft-set')].map(s => ({
      id: s.dataset.set, title: s.querySelector('.card-h').innerText, body: [...s.querySelector('.card-b').children].map(e => e.className), wrap: [...s.querySelector('.ft-tw').children].map(e => e.tagName),
      head: [...s.querySelectorAll('table.ft thead th')].map(t => t.textContent), ncell: [...new Set([...s.querySelectorAll('tr[data-feature]')].map(r => r.children.length))], groups: [...s.querySelectorAll('tr.grp')].map(g => g.innerText),
      rows: [...s.querySelectorAll('tr[data-feature]')].map(r => ({name: r.dataset.feature, id: r.id, cells: [...r.children].map(c => c.dataset.f), n: r.querySelector('[data-f=n]').textContent, code: r.querySelector('[data-f=name]').textContent, what: r.querySelector('[data-f=what]').textContent,
        base: r.querySelector('[data-f=base]') ? r.querySelector('[data-f=base]').textContent : null, search: r.querySelector('[data-f=search]') ? r.querySelector('[data-f=search]').textContent : null, text: r.innerText}))}))}));
  const total = J.sets.reduce((s, x) => s + x.groups.reduce((a, g) => a + g.features.length, 0), 0);
  let nChecks = 0;
  const eq = (a, b, m) => { nChecks++; if (JSON.stringify(a) !== JSON.stringify(b)) fail(m + ': ' + JSON.stringify(a).slice(0, 160) + ' ≠ ' + JSON.stringify(b).slice(0, 160)); };
  eq(dom.title, 'Признаки', 'заголовок'); eq(dom.meta, `${total} признаков · ${J.sets.length} наборов · ${J.updated}`, 'мета');
  eq(dom.prose, 0, 'текстовые блоки на вкладке'); eq(dom.kids, ['DIV.prog-h', 'DIV.bar ft-bar', ...J.sets.map(() => 'SECTION.ft-set')], 'состав вкладки');
  eq(dom.navs, J.sets.map(s => `${s.id}|${s.short}`), 'навигация');
  eq(dom.sets.map(s => s.id), J.sets.map(s => s.id), 'порядок наборов');
  eq(dom.sets.flatMap(s => s.rows.map(r => +r.n)), Array.from({length: total}, (_, i) => i + 1), 'сквозная нумерация 1…N в порядке страницы');
  J.sets.forEach((s, i) => {
    const d = dom.sets[i]; if (!d) return;
    const feats = s.groups.flatMap(g => g.features), hasB = feats.some(f => f.base !== '—'), hasS = feats.some(f => f.search !== 'нет');
    eq(d.title, s.title, s.id + ' заголовок'); eq(d.body, ['ft-tw'], s.id + ' в карточке только таблица'); eq(d.wrap, ['TABLE'], s.id + ' обёртка таблицы');
    eq(d.head, ['№', 'Признак', 'Что значит', ...(hasB ? ['База'] : []), ...(hasS ? ['Перебор'] : [])], s.id + ' шапка'); eq(d.ncell, [3 + hasB + hasS], s.id + ' ячеек в строке');
    eq(d.groups, s.groups.filter(g => g.title).map(g => g.title), s.id + ' группы');
    eq(d.rows.map(r => r.name), feats.map(f => f.name), s.id + ' имена'); eq(d.rows.map(r => r.code), feats.map(f => f.name.replace(/_bp$/, '')), s.id + ' код');
    eq(d.rows.map(r => +r.n), feats.map(f => f.n), s.id + ' номера'); eq(d.rows.map(r => r.id), feats.map(f => 'ft-n-' + f.n), s.id + ' якоря строк');
    eq(d.rows.map(r => r.what), feats.map(f => f.what), s.id + ' что значит');
    eq(d.rows.map(r => r.base), feats.map(f => hasB ? (f.base === '—' ? '' : f.base) : null), s.id + ' база');
    eq(d.rows.map(r => r.search), feats.map(f => hasS ? (f.search === 'нет' ? '' : f.search) : null), s.id + ' перебор');
    // копирование: на широком экране строка — ячейки через табуляцию
    if (W > 820) eq(d.rows.map(r => r.text.replace(/\t+$/, '')), feats.map(f => [f.n, f.name.replace(/_bp$/, ''), f.what, ...(hasB ? [f.base === '—' ? '' : f.base] : []), ...(hasS ? [f.search === 'нет' ? '' : f.search] : [])].join('\t').replace(/\t+$/, '')), s.id + ' текст строки');
  });
  eq(JSON.stringify(dom.sets).includes('—"') || dom.sets.some(s => s.rows.some(r => r.base === '—' || r.search === 'нет')), false, 'пустышки «—» / «нет» в ячейках');
  C.nChecks = nChecks; C.total = total; C.rowCounts = dom.sets.map(s => s.id + ':' + s.rows.length);
  C.overflow = await overflow();
  C.cellOverflow = await page.evaluate(() => [...document.querySelectorAll('.ft td, .ft-rep code')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).display !== 'inline').length);
  await page.screenshot({path: `${out}/features_top_${W}.png`});
  C.nav = [];
  for (const s of J.sets) {
    await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(250);
    await page.click(`button[data-go="${s.id}"]`); await settle();
    const r = await page.evaluate(i => { const e = document.getElementById('ft-' + i).getBoundingClientRect(); const bar = document.querySelector('.ft-bar').getBoundingClientRect(); return {top: Math.round(e.top), barBottom: Math.round(bar.bottom), hash: location.hash, atEnd: Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight - 2}; }, s.id);
    r.id = s.id; r.ok = r.top >= r.barBottom - 2 && (r.top < 260 || r.atEnd) && r.hash === '#features/' + s.id;
    if (!r.ok) fail('навигация ' + JSON.stringify(r));
    C.nav.push(r);
    await page.screenshot({path: `${out}/features_nav_${s.id}_${W}.png`});
  }
  C.others = {};
  for (const k of ['progress', 'targets', 'equity', 'model', 'features']) {
    await page.click(`#toptabs button[data-tab="${k}"]`); await page.waitForTimeout(400);
    const st = await state();
    C.others[k] = {hash: st.hash, visible: st.visible.join(), overflow: await overflow()};
    if (st.visible.join() !== 'pane-' + k) fail('вкладка ' + k + ': ' + JSON.stringify(st));
  }
  C.rowsAfterReturn = await page.evaluate(() => document.querySelectorAll('tr[data-feature]').length);
  if (C.rowsAfterReturn !== total) fail(`после возврата строк ${C.rowsAfterReturn} ≠ ${total}`);
  // TK-678: строка по номеру — #features/<набор>/<№>: подсвечена одна, стоит под липкой панелью; чужой или несуществующий номер — просто набор
  const pick = J.sets.flatMap(s => { const fs = s.groups.flatMap(g => g.features); return [fs[0], fs[fs.length - 1]].map(f => [s.id, f.n]); });
  C.rowLinks = [];
  for (const [sid, n] of [...pick, [J.sets[0].id, total], [J.sets[0].id, 99999]]) {
    await page.goto(base + `#features/${sid}/${n}`, {waitUntil: 'load'}); await page.reload({waitUntil: 'load'}); await settle();
    const r = await page.evaluate(() => { const hit = [...document.querySelectorAll('#features tr.hit')]; const bar = document.querySelector('.ft-bar').getBoundingClientRect(); return {hash: location.hash, hit: hit.map(x => x.dataset.n), top: hit[0] ? Math.round(hit[0].getBoundingClientRect().top) : null, barBottom: Math.round(bar.bottom), vh: innerHeight, atEnd: Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight - 2}; });
    const own = J.sets.find(s => s.id === sid).groups.some(g => g.features.some(f => f.n === n));
    r.want = `${sid}/${n}`;
    r.ok = own ? r.hash === `#features/${sid}/${n}` && r.hit.join() === String(n) && r.top >= r.barBottom - 2 && (r.top < 300 || r.atEnd) && r.top < r.vh
               : r.hash === '#features/' + sid && r.hit.length === 0;
    if (!r.ok) fail('строка по номеру ' + JSON.stringify(r));
    C.rowLinks.push(r);
  }
  await page.screenshot({path: `${out}/features_row_${W}.png`});
  // клик по номеру ставит хеш и подсветку, кнопка набора — снимает
  const [cs, cn] = pick[pick.length - 1];
  await page.click(`#ft-n-${cn} [data-f=n]`);
  C.noClick = await page.evaluate(() => ({hash: location.hash, hit: [...document.querySelectorAll('#features tr.hit')].map(x => x.dataset.n)}));
  if (C.noClick.hash !== `#features/${cs}/${cn}` || C.noClick.hit.join() !== String(cn)) fail('клик по номеру ' + JSON.stringify(C.noClick));
  await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(250);
  await page.click(`button[data-go="${J.sets[1].id}"]`); await settle();
  C.navAfterNo = await page.evaluate(() => ({hash: location.hash, hit: document.querySelectorAll('#features tr.hit').length}));
  if (C.navAfterNo.hash !== '#features/' + J.sets[1].id || C.navAfterNo.hit) fail('кнопка набора после номера ' + JSON.stringify(C.navAfterNo));
  for (const h of ['#features', ...J.sets.map(s => '#features/' + s.id), '#features/нет']) {
    await page.goto(base + h, {waitUntil: 'load'}); await page.reload({waitUntil: 'load'}); await settle();
    const r = await page.evaluate(() => { const bar = document.querySelector('.ft-bar'); const ids = [...document.querySelectorAll('section.ft-set')].map(x => ({id: x.dataset.set, top: Math.round(x.getBoundingClientRect().top)})); return {hash: location.hash, visible: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id).join(), barBottom: bar ? Math.round(bar.getBoundingClientRect().bottom) : null, ids, atEnd: Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight - 2}; });
    C['hash ' + h] = r;
    if (r.visible !== 'pane-features') fail(h + ': не вкладка Признаки ' + r.visible);
    const want = h.startsWith('#features/') && h !== '#features/нет' ? h.slice(10) : null;
    if (want) { const e = r.ids.find(x => x.id === want); if (!e || e.top < r.barBottom - 2 || (e.top > 260 && !r.atEnd)) fail(h + ': секция не наверху ' + JSON.stringify(e)); }
    else if (r.hash !== '#features') fail(h + ': хеш ' + r.hash);
  }
  await page.screenshot({path: `${out}/features_deeplink_${W}.png`});
  fs.writeFileSync(`${out}/features_report_${W}.json`, JSON.stringify(rep, null, 1));
  console.log(JSON.stringify({errors: errs, fails: rep.fails, nChecks, total, overflow: C.overflow, cellOverflow: C.cellOverflow, nav: C.nav.map(n => n.id + ':' + n.ok).join(' '), rowLinks: C.rowLinks.map(r => r.want + ':' + r.ok).join(' '), noClick: C.noClick, others: C.others, rows: C.rowCounts.join(' '), tabs: C.tabs}, null, 1));
  await browser.close();
})();
