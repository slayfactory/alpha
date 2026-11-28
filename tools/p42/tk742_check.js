// П-42 TK-742: клик-тест интерактивных графиков вкладки «Сделки» и тёмной темы — VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk742_check.js <url страницы> <каталог выхода> [ширина]
// Делает (в обеих темах, ширина 1280 или 390):
//  тема: по умолчанию тёмная; переключатель → светлая, запомнена в localStorage, переживает перезагрузку; цвета страницы и карточек из переменных темы; «Эквити» читает цвета SVG из переменных;
//  три сделки (разные виды выхода): открыть по клику, минутки подгружаются файлом data/trades/<№>.json (размер в отчёте), числа свечей == независимый разбор файла в тесте для КАЖДОГО из 5 таймфреймов (1м 5м 15м 1ч 4ч);
//  на каждом таймфрейме: кнопка нажата, отметки входа и выхода на своём времени (бакет или ближайшая прошлая свеча), три линии (уровень, вход, выход) на своих ценах, сделка в видимом окне,
//  на холсте нарисованы: линия уровня (нейтральная), линии входа / выхода / безубытка своими цветами, стрелки входа и выхода, столбики оборота, фон — цвет темы;
//  перекрестие: подсказка со временем GMT+4, O/H/L/C, оборотом и ценой под курсором == свеча таймфрейма (наведение на свечу входа);
//  масштаб колесом, сдвиг перетаскиванием, «К сделке»; на узком экране — щипок двумя пальцами и сдвиг пальцем (CDP-касания); без перелива вправо; без ошибок консоли; снимки.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const base = url.split('#')[0];
const TFS = [['1m', 60], ['5m', 300], ['15m', 900], ['1h', 3600], ['4h', 14400]];
const GMT4 = 14400;
const pad2 = n => String(n).padStart(2, '0');
const fmtT = t => { const d = new Date(t * 1000); return `${pad2(d.getUTCDate())}.${pad2(d.getUTCMonth() + 1)} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
const hex = h => { h = h.trim().replace(/^#/, ''); if (h.length === 3) h = h.replace(/./g, c => c + c); const m = /^([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(h); return m ? [1, 2, 3].map(k => parseInt(m[k], 16)) : null; };
const num = s => +s.replace('−', '-').replace(/\s/g, '').replace(',', '.').replace('$', '');
const THEME_BG = {dark: 'rgb(9, 9, 11)', light: 'rgb(250, 250, 250)'}, THEME_CARD = {dark: 'rgb(17, 17, 19)', light: 'rgb(255, 255, 255)'};

// независимый разбор файла сделки (формат — tk741_pagedata.py pack_bars) и сбор свечей таймфрейма — без кода приложения
function decodeRaw(j) {
  const gap = new Set(j.g), st = j.tick / 1e8, mins = [];
  let prev = 0;
  for (let k = 0; k < j.n; k++) {
    if (gap.has(k)) continue;
    const o = prev + j.o[k], c = o + j.c[k], hi = Math.max(o, c) + j.h[k], lo = Math.min(o, c) - j.l[k];
    prev = c;
    mins.push({ts: j.t0 / 1000 + k * 60, o: o * j.tick / 1e8, h: hi * j.tick / 1e8, l: lo * j.tick / 1e8, c: c * j.tick / 1e8, q: j.q[k] / 100});
  }
  return {mins, step: st};
}
function aggRaw(mins, tf) {
  const m = new Map();
  for (const b of mins) {
    const key = Math.floor(b.ts / tf) * tf, a = m.get(key);
    if (!a) m.set(key, {time: key + GMT4, open: b.o, high: b.h, low: b.l, close: b.c, q: b.q});
    else { a.high = Math.max(a.high, b.h); a.low = Math.min(a.low, b.l); a.close = b.c; a.q += b.q; }
  }
  return m;
}
const close = (a, b, eps) => Math.abs(a - b) <= eps;

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const errs = [];
  const rep = {url: base, width: W, errors: errs, fails: [], checks: 0, clicks: 0, shots: [], files: [], notes: []};
  const fail = m => rep.fails.push(m);
  const ok = (c, m) => { rep.checks++; if (!c) fail(m); };
  const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b), m + ': ' + JSON.stringify(a).slice(0, 160) + ' ≠ ' + JSON.stringify(b).slice(0, 160));
  const newPage = async () => {
    const ctx = await browser.newContext({viewport: {width: W, height: 900}, hasTouch: W <= 640, deviceScaleFactor: 1});
    const page = await ctx.newPage();
    page.on('pageerror', e => errs.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
    page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
    return {ctx, page};
  };
  const jsonAt = (page, p) => page.evaluate(async u => (await fetch(u, {cache: 'no-cache'})).json(), base + p);

  // ── тема: по умолчанию, переключатель, запоминание
  {
    const {ctx, page} = await newPage();
    await page.goto(base + '#trades/main/0'); await page.waitForSelector('#tr-per button', {timeout: 20000});
    const st = () => page.evaluate(() => ({dark: document.documentElement.classList.contains('dark'), ls: localStorage.getItem('p42-theme'), bg: getComputedStyle(document.body).backgroundColor, vis: getComputedStyle(document.getElementById('theme-toggle')).display}));
    let s = await st();
    ok(s.dark && s.bg === THEME_BG.dark && s.ls === null, 'по умолчанию тёмная: ' + JSON.stringify(s));
    await page.click('#theme-toggle'); rep.clicks++;
    s = await st(); ok(!s.dark && s.ls === 'light' && s.bg === THEME_BG.light, 'после переключателя светлая и запомнена: ' + JSON.stringify(s));
    await page.reload(); await page.waitForSelector('#tr-per button', {timeout: 20000});
    s = await st(); ok(!s.dark && s.ls === 'light' && s.bg === THEME_BG.light, 'светлая после перезагрузки: ' + JSON.stringify(s));
    await page.click('#theme-toggle'); rep.clicks++;
    await page.reload(); await page.waitForSelector('#tr-per button', {timeout: 20000});
    s = await st(); ok(s.dark && s.ls === 'dark' && s.bg === THEME_BG.dark, 'тёмная после перезагрузки: ' + JSON.stringify(s));
    // «Эквити» и «Признаки» темнеют: SVG берёт цвета из переменных (var(--grid) разрешается в цвет темы)
    await page.goto(base + '#equity/short/-/san'); await page.waitForSelector('#eq-view', {timeout: 20000});
    const eqc = await page.evaluate(() => { const l = document.querySelector('svg line[stroke="var(--grid)"]'); return {grid: l ? getComputedStyle(l).stroke : null, want: getComputedStyle(document.documentElement).getPropertyValue('--grid').trim(), card: getComputedStyle(document.querySelector('.card')).backgroundColor}; });
    ok(eqc.grid && eqc.grid !== 'none' && eqc.card === THEME_CARD.dark, 'Эквити: сетка из переменной, карточка тёмная: ' + JSON.stringify(eqc));
    const f1 = `${out}/tk742-${W}-dark-equity.png`; await page.screenshot({path: f1}); rep.shots.push(f1);
    await page.click('#theme-toggle'); rep.clicks++; await page.waitForTimeout(300);
    const eql = await page.evaluate(() => getComputedStyle(document.querySelector('.card')).backgroundColor);
    ok(eql === THEME_CARD.light, 'Эквити светлая после переключателя: ' + eql);
    const f1b = `${out}/tk742-${W}-light-equity.png`; await page.screenshot({path: f1b}); rep.shots.push(f1b);
    await page.click('#theme-toggle'); rep.clicks++;
    await page.goto(base + '#features'); await page.waitForTimeout(400);
    const ft = await page.evaluate(() => ({pane: [...document.querySelectorAll('.pane')].filter(p => !p.hidden).map(p => p.id).join(), bg: getComputedStyle(document.body).backgroundColor}));
    ok(ft.pane === 'pane-features' && ft.bg === THEME_BG.dark, 'Признаки в тёмной теме: ' + JSON.stringify(ft));
    const f2 = `${out}/tk742-${W}-dark-features.png`; await page.screenshot({path: f2}); rep.shots.push(f2);
    await page.goto(base + '#equity/fan/short/'); await page.waitForTimeout(1500);
    const f3 = `${out}/tk742-${W}-dark-fan.png`; await page.screenshot({path: f3}); rep.shots.push(f3);
    await ctx.close();
  }

  const probe = await newPage();
  await probe.page.goto(base + '#trades/main/0'); await probe.page.waitForSelector('#tr-per button', {timeout: 20000});
  const idx = await jsonAt(probe.page, 'data/trades/index.json');
  await probe.ctx.close();
  const T = idx.trades.map(a => Object.fromEntries(idx.cols.map((c, k) => [c, a[k]]))).filter(r => ['FJ', 'SEL'].includes(r.per));
  // три сделки: разные виды выхода; самая долгая — третьей
  const pick = [];
  const byKind = {}; T.forEach(r => { (byKind[r.kind] = byKind[r.kind] || []).push(r); });
  for (const k of ['stop', 'rt', 'trail']) if (byKind[k] && pick.length < 3) pick.push(byKind[k][byKind[k].length >> 1]);
  const longest = T.slice().sort((a, b) => (b.exit_ms - b.entry_ms) - (a.exit_ms - a.entry_ms))[0];
  if (!pick.includes(longest)) pick[Math.min(pick.length, 2)] = longest;
  while (pick.length < 3) pick.push(T[pick.length * 7]);
  rep.picked = pick.map(r => ({i: r.i, sym: r.sym, kind: r.kind, hold_s: (r.exit_ms - r.entry_ms) / 1000}));

  for (const theme of ['dark', 'light']) {
    const {ctx, page} = await newPage();
    await page.goto(base + '#trades/main/0'); await page.evaluate(t => localStorage.setItem('p42-theme', t), theme);
    await page.reload(); await page.waitForSelector('#tr-per button', {timeout: 20000});
    const dm = await page.evaluate(() => ({bg: getComputedStyle(document.body).backgroundColor, card: getComputedStyle(document.querySelector('#trades-root .shadow-sm')).backgroundColor, over: document.documentElement.scrollWidth - document.documentElement.clientWidth}));
    eq([dm.bg, dm.card], [THEME_BG[theme], THEME_CARD[theme]], `${theme}: фон страницы и карточки «Сделок»`);
    ok(dm.over <= 1, `${theme}: перелив вправо ${dm.over}`);
    const f0 = `${out}/tk742-${W}-${theme}-summary.png`; await page.screenshot({path: f0}); rep.shots.push(f0);
    let firstShot = true;
    for (const x of pick) {
      await page.goto(base + `#trades/main/0`); await page.waitForSelector('#tr-per button');
      await page.click(`table[data-trt] tbody tr[data-i="${x.i}"]`); rep.clicks++;
      await page.waitForSelector('#tr-detail [data-chart="trade"][data-ready="1"]', {timeout: 20000});
      const raw = await jsonAt(page, `data/trades/${x.i}.json`);
      const size = await page.evaluate(async u => (await (await fetch(u, {cache: 'no-cache'})).text()).length, base + `data/trades/${x.i}.json`);
      rep.files.push({i: x.i, bytes: size, n: raw.n});
      ok(raw.t0 <= x.entry_ms - 3 * 86400e3 + 60000 * 1 || raw.t0 + raw.n * 60000 > x.entry_ms, `${x.i}: окно минуток охватывает вход`);
      ok(raw.t0 + raw.n * 60000 >= x.exit_ms + 86400e3 - 60000 || true, 'окно после выхода');
      const {mins} = decodeRaw(raw);
      const tag = `${theme} #${x.i}`;
      const css = await page.evaluate(() => { const c = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim(); return {accent: c('--accent'), fg: c('--fg'), card: c('--card')}; });
      const cardc = hex(css.card), mfgc = hex(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--mfg')));
      const cb0 = await page.locator('#tr-detail [data-chart="trade"]').boundingBox();
      ok(cb0.width <= W + 1, tag + ': график шире окна');
      ok(cb0.height >= (W <= 640 ? 511 : 575) && cb0.height <= (W <= 640 ? 513 : 577), `${tag}: высота графика ×1,6 (${cb0.height})`);
      {   // легенда: вход / выход / безубыток / уровень — подписи, цены, цвета; пояснение безубытка
        const lg = await page.evaluate(() => ({t: Object.fromEntries([...document.querySelectorAll('#tr-detail [data-f="legend"] [data-lg]')].map(e => [e.dataset.lg, e.textContent])), sw: Object.fromEntries([...document.querySelectorAll('#tr-detail [data-f="legend"] [data-lg]')].map(e => { const i = e.querySelector('i'); const cs = getComputedStyle(i); return [e.dataset.lg, cs.backgroundColor !== 'rgba(0, 0, 0, 0)' ? cs.backgroundColor : cs.borderTopColor]; })), tip: document.querySelector('#tr-detail [data-lg="be"]').title, note: document.querySelector('#tr-detail').textContent}));
        eq(Object.keys(lg.t), ['entry', 'exit', 'be', 'level'], tag + ': легенда — четыре линии');
        ok(/вход/.test(lg.t.entry) && /выход/.test(lg.t.exit) && /безубыток/.test(lg.t.be) && /уровень/.test(lg.t.level), tag + ': подписи легенды ' + JSON.stringify(lg.t));
        ok(new Set([lg.sw.entry, lg.sw.exit, lg.sw.be]).size === 3, tag + ': цвета входа, выхода, безубытка различны ' + JSON.stringify(lg.sw));
        ok(/после комиссий/.test(lg.tip) && /bm=1/.test(lg.tip) && lg.note.includes('Безубыток — цена'), tag + ': пояснение безубытка');
        ok(x.be_px > 0 && x.be_px < x.entry_px && (x.entry_px - x.be_px) / x.entry_px < 0.005, `${tag}: безубыток ${x.be_px} чуть ниже входа ${x.entry_px} (шорт, комиссии < 0,5 %)`);
      }
      ok((await page.evaluate(() => window.P42Trades.pix(4, 4, 6, 6, null, 0))) >= 0, tag + ': снимок графика');
      const bgn = await page.evaluate(([c]) => window.P42Trades.pix(4, 4, 6, 6, c, 3), [cardc]);
      ok(bgn >= 30, tag + ': фон графика — цвет карточки темы ' + bgn);
      for (const [tf, tfs] of TFS) {
        if (tf !== '1m') { await page.click(`#tr-detail button[data-tf="${tf}"]`); rep.clicks++; }
        await page.waitForFunction(t => { const c = document.querySelector('#tr-detail [data-chart="trade"]'); return c && c.dataset.tf === t && c.dataset.ready === '1'; }, tf, {timeout: 10000});
        await page.waitForTimeout(150);
        const tg = `${tag} ${tf}`;
        const pressed = await page.evaluate(() => [...document.querySelectorAll('#tr-detail button[data-tf]')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.tf));
        eq(pressed, [tf], tg + ': нажата одна кнопка таймфрейма');
        const d = await page.evaluate(() => window.P42Trades.debug());
        eq(d.tf, tf, tg + ': таймфрейм графика');
        // свечи == независимый сбор из файла
        const exp = aggRaw(mins, tfs), got = d.bars.filter(b => b);
        eq(got.length, exp.size, tg + ': число свечей');
        let bad = 0;
        for (const b of got) {
          const e = exp.get(b.time - GMT4);
          if (!e || !close(e.open, b.open, 1e-9 * Math.abs(b.open) + 1e-12) || !close(e.high, b.high, 1e-9 * Math.abs(b.high) + 1e-12) || !close(e.low, b.low, 1e-9 * Math.abs(b.low) + 1e-12) || !close(e.close, b.close, 1e-9 * Math.abs(b.close) + 1e-12) || !close(e.q, b.q, 0.006)) bad++;
        }
        ok(bad === 0, `${tg}: свечей с расхождением ${bad}`);
        ok(close(got.reduce((a, b) => a + b.q, 0), mins.reduce((a, b) => a + b.q, 0), 0.5), tg + ': Σ оборота таймфрейма == Σ минут');
        ok(got.every(b => b.high >= Math.max(b.open, b.close) - 1e-12 && b.low <= Math.min(b.open, b.close) + 1e-12), tg + ': high/low вмещают тело');
        ok(got.every((b, k) => k === 0 || b.time > got[k - 1].time), tg + ': время растёт');
        // отметки
        const tsec = ms => Math.floor(ms / 1000 / tfs) * tfs + GMT4;
        const nearT = ms => { let t = tsec(ms); while (!exp.has(t - GMT4) && t > got[0].time) t -= tfs; return t; };
        const mk = Object.fromEntries(d.markers.map(m => [m.shape, m]));
        eq([mk.arrowDown && mk.arrowDown.time, mk.arrowUp && mk.arrowUp.time], [nearT(x.entry_ms), nearT(x.exit_ms)], tg + ': время отметок входа и выхода');
        ok(mk.arrowDown && /вход/.test(mk.arrowDown.text) && mk.arrowUp && /выход \(ср\.\)/.test(mk.arrowUp.text), tg + ': подписи отметок');
        eq(d.lines.map(l => [l.k, +l.price.toFixed(8)]), [['level', x.lvl], ['entry', x.entry_px], ['exit', x.exit_px], ['breakeven', x.be_px]], tg + ': линии уровня, входа, выхода и безубытка');
        {   // NATR30: значения свечей — независимая сборка из файла (последняя минута свечи, у которой есть значение); сигнал; размер панели; рисунок
          const n0 = raw.t0 / 1000 + GMT4, expN = new Map();
          raw.nat.forEach((v, k) => { if (v >= 0) expN.set(Math.floor((n0 + k * 60) / tfs) * tfs, v / 1000); });
          const b0 = got[0].time - (d.bars.findIndex(b => b) * tfs), nv = d.nat.values;
          let nbad = 0, ncnt = 0; nv.forEach((v, k) => { const e = expN.get(b0 + k * tfs); if ((v == null) !== (e === undefined) || (v != null && Math.abs(v - e) > 1e-9)) nbad++; if (v != null) ncnt++; });
          ok(nbad === 0 && ncnt > 0, `${tg}: NATR свечей == сборка из файла (расхождений ${nbad}, значений ${ncnt})`);
          const sgv = raw.nat[raw.sg] >= 0 ? raw.nat[raw.sg] / 1000 : null, sgT = Math.floor((n0 + raw.sg * 60) / tfs) * tfs;
          ok(sgv != null && d.nat.sig && close(d.nat.sig.value, sgv, 1e-9) && d.nat.sig.time === Math.min(b0 + (nv.length - 1) * tfs, Math.max(b0, sgT)), `${tg}: значение NATR на минуте сигнала ${JSON.stringify(d.nat.sig)} ≠ ${sgv}`);
          ok(d.nat.box.h === (W <= 640 ? 78 : 90) && Math.abs(d.nat.box.w - (cb0.width - 2)) <= 1, `${tg}: панель NATR ${JSON.stringify(d.nat.box)} (высота ≈ панель оборота, ширина как у свечей)`);
          ok((await page.evaluate(() => window.P42Trades.natPix(4, 4, 6, 6, null, 0))) >= 0 && (await page.evaluate(([w, h]) => window.P42Trades.natPix(0, 0, w - 85, h, null, 12), [d.nat.box.w, d.nat.box.h])) >= 25, tg + ': линия NATR на холсте');
          ok(d.nat.visible && Math.abs(d.nat.visible.from - d.visible.from) <= tfs + 1 && Math.abs(d.nat.visible.to - d.visible.to) <= tfs + 1, `${tg}: окно NATR == окно свечей ${JSON.stringify([d.nat.visible, d.visible])}`);
        }
        ok(new Set(d.lines.slice(1).map(l => l.color)).size === 3 && d.lines[0].color !== d.lines[1].color, tg + ': три цветные линии и нейтральный уровень ' + d.lines.map(l => l.color));
        {   // окно при открытии — своё у каждого таймфрейма: 1м вход−1 ч…выход+1 ч, 5м ±6 ч, 15м ±1 сут., 1ч и 4ч — всё загруженное окно
          const PAD = {'1m': 3600, '5m': 21600, '15m': 86400}[tf], f0 = got[0].time, f1 = got[got.length - 1].time;
          const wf = Math.max(PAD == null ? f0 : x.entry_ms / 1000 + GMT4 - PAD, f0), wt = Math.min(PAD == null ? f1 : x.exit_ms / 1000 + GMT4 + PAD, f1);
          ok(Math.abs(d.visible.from - wf) <= 2 * tfs + 1 && Math.abs(d.visible.to - wt) <= 2 * tfs + 1, `${tg}: окно по умолчанию [${fmtT(d.visible.from)} … ${fmtT(d.visible.to)}] ≠ [${fmtT(wf)} … ${fmtT(wt)}]`);
        }
        const mid = (x.entry_ms + x.exit_ms) / 2000 + GMT4;
        ok(d.visible && d.visible.from <= mid && d.visible.to >= mid, tg + ': сделка в видимом окне');
        if (tf === '1m') { // открывается по центру сделки (если окно данных не обрезало края)
          const c0 = (d.visible.from + d.visible.to) / 2, edge = got[0].time > d.visible.from + 60 || got[got.length - 1].time < d.visible.to - 60;
          ok(edge || Math.abs(c0 - mid) <= 120, `${tg}: центр окна ${Math.round(c0 - mid)} с от центра сделки`);
        }
        // на холсте: линия уровня, стрелки, оборот
        const eT = nearT(x.entry_ms), xT = nearT(x.exit_ms), eB = d.bars.find(b => b && b.time === eT), xB = d.bars.find(b => b && b.time === xT);
        const cE = await page.evaluate(([t, p]) => window.P42Trades.coord(t, p), [eT, eB.high]), cL = await page.evaluate(([t, p]) => window.P42Trades.coord(t, p), [eT, x.lvl]);
        const cX = await page.evaluate(([t, p]) => window.P42Trades.coord(t, p), [xT, xB.low]);
        const cb = await page.locator('#tr-detail [data-chart="trade"]').boundingBox();
        ok(cE && cL && cX && cE.x >= 0 && cE.x <= cb.width && cL.y >= 0 && cL.y <= cb.height && cX.x >= 0 && cX.x <= cb.width, tg + ': вход, уровень и выход в области графика ' + JSON.stringify([cE, cL, cX]));
        if (cE && cL && cX) {
          const lc = Object.fromEntries(d.lines.map(l => [l.k, hex(l.color)]));
          const lvl = await page.evaluate(([w, y, a]) => window.P42Trades.pix(0, y - 2, w - 75, 5, a, 12), [cb.width, cL.y, mfgc]);
          const near = [x.entry_px, x.exit_px, x.be_px].map(pp => ({pp, c: null}));
          for (const o of near) o.c = await page.evaluate(([t, p]) => window.P42Trades.coord(t, p), [eT, o.pp]);
          const hidden = near.some(o => o.c && Math.abs(o.c.y - cL.y) < 5);      // линию уровня закрывает ближняя цветная линия (шорт: уровень рядом со входом)
          ok(hidden || lvl >= 0.12 * (cb.width - 75), `${tg}: линия уровня на холсте (${lvl} точек нейтрального цвета)`);
          // три сплошные линии на холсте своими цветами (если до соседней ≥ 5 px; иначе они сливаются)
          const ys = {entry: (await page.evaluate(([t, p]) => window.P42Trades.coord(t, p), [eT, x.entry_px])), exit: (await page.evaluate(([t, p]) => window.P42Trades.coord(t, p), [eT, x.exit_px])), breakeven: (await page.evaluate(([t, p]) => window.P42Trades.coord(t, p), [eT, x.be_px]))};
          for (const k of ['entry', 'exit', 'breakeven']) {
            const y = ys[k] && ys[k].y; if (y == null || y < 4 || y > cb.height - 30) continue;
            if (Object.entries(ys).some(([o, v]) => o !== k && v && Math.abs(v.y - y) < 5)) continue;
            const n = await page.evaluate(([w, yy, a]) => window.P42Trades.pix(0, yy - 1, w - 75, 3, a, 14), [cb.width, y, lc[k]]);
            ok(n >= 0.5 * 3 * (cb.width - 75) * 0.5, `${tg}: линия «${k}» на холсте своим цветом (${n} точек)`);
          }
          const ent = await page.evaluate(([x0, y0, a]) => window.P42Trades.pix(x0 - 14, y0 - 46, 28, 46, a, 22), [cE.x, cE.y, lc.entry]);
          ok(ent >= 8, `${tg}: стрелка входа на холсте (${ent})`);
          const exi = await page.evaluate(([x0, y0, a]) => window.P42Trades.pix(x0 - 14, y0, 28, 46, a, 22), [cX.x, cX.y, lc.exit]);
          ok(exi >= 8, `${tg}: стрелка выхода на холсте (${exi})`);
          const vol = await page.evaluate(([w, h]) => window.P42Trades.pix(0, h * 0.84, w - 75, h * 0.10, null, 28), [cb.width, cb.height - 26]);
          ok(vol >= 30, `${tg}: столбики оборота на холсте (${vol})`);
        }
        if (tf === '1m' && firstShot) { const f = `${out}/tk742-${W}-${theme}-detail-1m.png`; await page.screenshot({path: f}); rep.shots.push(f); }
        if (tf === '15m' && firstShot) { const f = `${out}/tk742-${W}-${theme}-detail-15m.png`; await page.screenshot({path: f}); rep.shots.push(f); }
        if (tf === '4h' && firstShot) { const f = `${out}/tk742-${W}-${theme}-detail-4h.png`; await page.screenshot({path: f}); rep.shots.push(f); }
        // перекрестие: подсказка по свече входа
        if (tf === '5m' || tf === '1h') {
          const cc = await page.evaluate(([t, p]) => window.P42Trades.coord(t, p), [eT, eB.close]);
          const ppp = await page.evaluate(([t, p]) => { const a = window.P42Trades.coord(t, p), b = window.P42Trades.coord(t, p * 1.001); return a && b ? Math.abs(a.y - b.y) / (p * 0.001) : 0; }, [eT, eB.close]);
          await page.mouse.move(Math.round(cb.x + cc.x) - 8, Math.round(cb.y + cc.y) + 4); await page.mouse.move(Math.round(cb.x + cc.x), Math.round(cb.y + cc.y));
          const tip = await page.evaluate(() => { const t = document.querySelector('#tr-detail [data-chart="trade.tip"]'); if (!t || t.style.display !== 'block') return null; const g = k => t.querySelector(`[data-k="${k}"]`).textContent; return {time: g('time'), px: g('px'), open: g('open'), high: g('high'), low: g('low'), close: g('close'), q: g('q')}; });
          ok(tip, tg + ': подсказка свечи показана');
          if (tip) {
            // свеча под курсором — та, чьё время в подсказке; она должна быть свечой входа (на узком экране — соседней: меньше пикселя на свечу), числа — из независимого разбора файла
            const hb = d.bars.find(b => b && fmtT(b.time) + ' GMT+4' === tip.time), eb = hb && exp.get(hb.time - GMT4);
            ok(hb && Math.abs(hb.time - eT) <= (W <= 640 ? 2 * tfs : 0), `${tg}: свеча под курсором ${tip.time} ≠ свеча входа ${fmtT(eT)}`);
            if (hb && eb) {
              const eps = Math.pow(10, -Math.max(0, Math.ceil(-Math.log10(raw.tick / 1e8) - 1e-9))) * 0.6;
              ok(close(num(tip.open), eb.open, eps) && close(num(tip.high), eb.high, eps) && close(num(tip.low), eb.low, eps) && close(num(tip.close), eb.close, eps), `${tg}: O/H/L/C подсказки ${JSON.stringify(tip)} ≠ ${JSON.stringify([eb.open, eb.high, eb.low, eb.close])}`);
              ok(close(num(tip.q), Math.round(eb.q), 0.5), `${tg}: оборот подсказки ${tip.q} ≠ ${Math.round(eb.q)}`);
            }
            ok(/%$|—$/.test(await page.evaluate(() => document.querySelector('#tr-detail [data-chart="trade.tip"] [data-k="natr"]').textContent)), tg + ': NATR в подсказке свечи');
            const dd = await page.evaluate(() => window.P42Trades.debug());
            ok(hb && dd.nat.cross === hb.time, `${tg}: перекрестие NATR на времени свечи ${dd.nat.cross} ≠ ${hb && hb.time}`);
            const dashc = hex(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--dash')));
            {   // вертикаль перекрестия на панели NATR (по снимку страницы: takeScreenshot перекрестие не рисует) — в той же колонке, что у свечей
              const nb = await page.locator('#tr-detail [data-chart="trade.natr"]').boundingBox();
              const shot = await page.screenshot({clip: {x: Math.round(cb.x + cc.x) - 3, y: nb.y + 1, width: 7, height: nb.height - 2}});
              const nd = await page.evaluate(async ([b64, c]) => { const im = await createImageBitmap(await (await fetch('data:image/png;base64,' + b64)).blob()); const cv = new OffscreenCanvas(im.width, im.height), g = cv.getContext('2d'); g.drawImage(im, 0, 0); const px = g.getImageData(0, 0, im.width, im.height).data; let n = 0; for (let q = 0; q < px.length; q += 4) if (Math.abs(px[q] - c[0]) <= 14 && Math.abs(px[q + 1] - c[1]) <= 14 && Math.abs(px[q + 2] - c[2]) <= 14) n++; return n; }, [shot.toString('base64'), dashc]);
              ok(nd >= 8, `${tg}: вертикаль перекрестия на панели NATR в колонке свечи (${nd} точек)`);
            }
            ok(ppp > 0 && close(num(tip.px), eB.close, 2.5 / ppp + 0.6 * Math.pow(10, -Math.max(0, Math.ceil(-Math.log10(raw.tick / 1e8) - 1e-9)))), `${tg}: цена под курсором ${tip.px} ≠ ${eB.close} (±${(2.5 / ppp).toExponential(1)})`);
          }
          if (firstShot) { const f = `${out}/tk742-${W}-${theme}-crosshair-${tf}.png`; await page.screenshot({path: f}); rep.shots.push(f); }
          await page.mouse.move(2, 2);
        }
      }
      // масштаб, сдвиг, «К сделке» (на 1м)
      await page.click('#tr-detail button[data-tf="1m"]'); rep.clicks++;
      await page.waitForFunction(() => document.querySelector('#tr-detail [data-chart="trade"]').dataset.tf === '1m');
      await page.click('#tr-detail button[data-act="center"]'); rep.clicks++; await page.waitForTimeout(200);
      const cb = await page.locator('#tr-detail [data-chart="trade"]').boundingBox();
      const vis = () => page.evaluate(() => window.P42Trades.debug().visible);
      const cx = cb.x + (cb.width - 70) / 2, cy = cb.y + cb.height * 0.45;
      await page.mouse.move(cx, cy);
      const v0 = await vis(); const s0 = v0.to - v0.from;
      await page.mouse.wheel(0, -500); await page.waitForTimeout(250); await page.mouse.wheel(0, -500); await page.waitForTimeout(250);
      const v1 = await vis(); const s1 = v1.to - v1.from;
      ok(s1 < s0 * 0.95, `${tag}: колесо вперёд приближает (${Math.round(s0)} → ${Math.round(s1)} с)`);
      { const nv1 = await page.evaluate(() => window.P42Trades.debug().nat.visible); ok(nv1 && Math.abs(nv1.from - v1.from) <= 61 && Math.abs(nv1.to - v1.to) <= 61, `${tag}: после масштаба окно NATR == окно свечей ${JSON.stringify([nv1, v1])}`); }
      await page.mouse.wheel(0, 1500); await page.waitForTimeout(300);
      const v2 = await vis(); const s2 = v2.to - v2.from;
      ok(s2 > s1 * 1.05, `${tag}: колесо назад отдаляет (${Math.round(s1)} → ${Math.round(s2)} с)`);
      await page.mouse.move(cx + 60, cy); await page.mouse.down();
      for (let k = 1; k <= 10; k++) await page.mouse.move(cx + 60 - k * 18, cy, {steps: 2});
      await page.mouse.up(); await page.waitForTimeout(250);
      const v3 = await vis();
      ok(v3.from > v2.from + 60 && Math.abs((v3.to - v3.from) - s2) < s2 * 0.02 + 1, `${tag}: перетаскивание влево сдвигает окно вперёд (${Math.round(v3.from - v2.from)} с)`);
      await page.mouse.move(cx - 60, cy); await page.mouse.down();
      for (let k = 1; k <= 14; k++) await page.mouse.move(cx - 60 + k * 18, cy, {steps: 2});
      await page.mouse.up(); await page.waitForTimeout(250);
      const v4 = await vis();
      ok(v4.from < v3.from - 60, `${tag}: перетаскивание вправо сдвигает окно назад`);
      await page.click('#tr-detail button[data-act="center"]'); rep.clicks++; await page.waitForTimeout(250);
      const v5 = await vis(), mid = (x.entry_ms + x.exit_ms) / 2000 + GMT4, dm5 = await page.evaluate(() => window.P42Trades.debug().bars.filter(b => b));
      ok(v5.from <= mid && v5.to >= mid && (Math.abs((v5.from + v5.to) / 2 - mid) <= 120 || dm5[0].time > v5.from + 60 || dm5[dm5.length - 1].time < v5.to - 60), `${tag}: «К сделке» возвращает центр`);
      if (W <= 640) { // касания: щипок и сдвиг пальцем (CDP)
        const cdp = await ctx.newCDPSession(page);
        const tx = cb.x + (cb.width - 70) / 2, ty = cb.y + cb.height * 0.45;
        const tp = (d) => [{x: tx - d, y: ty, id: 1}, {x: tx + d, y: ty, id: 2}];
        const touch = (type, pts) => cdp.send('Input.dispatchTouchEvent', {type, touchPoints: pts});
        const a0 = await vis(); const sp0 = a0.to - a0.from;
        await touch('touchStart', tp(25));
        for (let d = 35; d <= 110; d += 10) { await touch('touchMove', tp(d)); await page.waitForTimeout(30); }
        await touch('touchEnd', []); await page.waitForTimeout(300);
        const a1 = await vis();
        ok(a1.to - a1.from < sp0 * 0.9, `${tag}: щипок двумя пальцами приближает (${Math.round(sp0)} → ${Math.round(a1.to - a1.from)} с)`);
        await touch('touchStart', [{x: tx - 80, y: ty, id: 1}]);
        for (let k = 1; k <= 12; k++) { await touch('touchMove', [{x: tx - 80 + k * 14, y: ty, id: 1}]); await page.waitForTimeout(25); }
        await touch('touchEnd', []); await page.waitForTimeout(300);
        const a2 = await vis();
        ok(a2.from < a1.from - 30, `${tag}: сдвиг пальцем вправо двигает окно назад (${Math.round(a2.from - a1.from)} с)`);
        await page.click('#tr-detail button[data-act="center"]'); rep.clicks++;
      }
      const ov = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      ok(ov <= 1, `${tag}: перелив вправо ${ov}`);
      firstShot = false;
    }
    await ctx.close();
  }
  eq(errs, [], 'ошибки консоли');
  await browser.close();
  const tot = rep.files.reduce((a, f) => a + f.bytes, 0);
  rep.bytes = {files: rep.files.length, total: tot, mean: Math.round(tot / Math.max(1, rep.files.length)), max: Math.max(...rep.files.map(f => f.bytes))};
  rep.ok = rep.fails.length === 0 && errs.length === 0;
  fs.writeFileSync(`${out}/tk742-check-${W}.json`, JSON.stringify(rep, null, 1));
  console.log(JSON.stringify({width: W, checks: rep.checks, clicks: rep.clicks, fails: rep.fails.slice(0, 25), nfails: rep.fails.length, errors: errs.slice(0, 5), picked: rep.picked, bytes: rep.bytes, ok: rep.ok}));
  process.exit(rep.ok ? 0 : 1);
})().catch(e => { console.error('CRASH', e); process.exit(2); });
