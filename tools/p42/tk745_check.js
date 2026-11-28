// П-42 TK-745: клик-тест заявок и уровней сетапа на графиках вкладки «Сделки» — VPS София (node18, playwright-core 1.48.2), НЕ на ПК владельца.
// Запуск: PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node tk745_check.js <url страницы> <каталог выхода> [ширина]
// Три сделки (MAGMAUSDT 24.10 08:14 из набора main, сделка со стопом из main, сделка из набора «30 дней»), на каждой:
//  данные: файл сделки data/trades/<№>.json (независимый разбор `ord` / `lv`) == счёт по CSV журнала движка в check.json["orders"] (группы: заявок, отрезков, исполнено, снято; уровни по ролям);
//  график: debug().ov == тот же счёт; нарисовано (debug().drawn) == данные по всем группам; кнопки легенды [data-og]: ключи, счётчики data-n, нажатость;
//  кнопки: выключить группу → нарисовано по группе 0, прочие не тронуты, автомасштаб без её цен; включить → как было; то же для «уровни сетапа»;
//  подсказка: на отрезке и на исполнении каждой группы под курсором своя заявка (роль, лот, доля, цена, время GMT+4 мс), исполнение — с долей исполнения и временем из файла; реальный наведённый курсор → блок [data-ord];
//  цвета на холсте: отметка исполнения и отрезок каждой группы своего цвета (темы тёмная / светлая), пивоты — своим;
//  MAGMAUSDT 24.10 08:14: пивоты 22.10 01:00 0,31132 и 22.10 08:00 0,31025 в `lv`, линии на графике на своих ценах, подсказка «пивот-член»;
//  узкий экран (390): без перелива вправо, кнопки легенды в экране; снимки.
const {chromium} = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const [url, out, widthArg] = process.argv.slice(2);
const W = +(widthArg || 1280);
fs.mkdirSync(out, {recursive: true});
const base = url.split('#')[0];
const GMT4 = 14400;
const p2 = (n, w = 2) => String(n).padStart(w, '0');
const fmtMs = ms => { const d = new Date(ms + 4 * 3600e3); return `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)} ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}:${p2(d.getUTCSeconds())}.${p2(d.getUTCMilliseconds(), 3)}`; };
const fmtMin = ms => fmtMs(ms).slice(0, 11);
const shareTxt = pm => (pm / 10).toFixed(pm % 10 ? 1 : 0).replace('.', ',') + ' %';
const grpOf = r => r === 'e1' || r === 'e2' || r === 'stop' || r === 'rt' ? r : r.startsWith('take') ? 'take' : 'other';
const GRPS = ['e1', 'e2', 'take', 'stop', 'rt', 'other'];
const ROLE_RU = r => /^take\d+$/.test(r) ? 'тейк ' + r.slice(4) : ({e1: 'вход e1', e2: 'второй вход e2', stop: 'стоп', rt: 'возврат за уровень rt'})[r] || r;
const COL = {dark: {e1: '#22d3ee', e2: '#f472b6', take: '#4ade80', stop: '#f87171', rt: '#facc15', other: '#94a3b8', pivot: '#2dd4bf'}, light: {e1: '#0891b2', e2: '#db2777', take: '#16a34a', stop: '#dc2626', rt: '#ca8a04', other: '#64748b', pivot: '#0f766e'}};
const hex = h => [1, 3, 5].map(k => parseInt(h.slice(k, k + 2), 16));
const bar = ms => Math.floor(ms / 60000) * 60 + GMT4;            // время минутной свечи графика (сек, ось GMT+4)
const LV_ROLE = {level: 'L', ver: 'V', touch: 'T', touch_gone: 'G', next: 'X', other: 'O'};

// независимый счёт по файлу сделки: {g: {группа: [заявок, отрезков, исполнено, снято]}, lv: {вид: число}}
function rawCounts(j) {
  const g = {}, lv = {};
  for (const [role, , , , kind, , , , , path] of j.ord) {
    const c = g[grpOf(role)] = g[grpOf(role)] || [0, 0, 0, 0];
    c[0]++; c[1] += path.length / 3; if (kind === 'f') c[2]++; else if (kind === 'c') c[3]++;
  }
  for (const l of j.lv) lv[l[0]] = (lv[l[0]] || 0) + 1;
  return {g, lv};
}
// счёт по CSV (check.json) в тех же видах: роли setup → виды уровней, три строки зоны → одна Z
function csvCounts(c) {
  const lv = {};
  for (const [r, n] of Object.entries(c.lv)) { if (r.startsWith('zone_')) lv.Z = 1; else lv[LV_ROLE[r]] = n; }
  return {g: c.g, lv};
}
const sortObj = o => Object.fromEntries(Object.keys(o).sort().map(k => [k, o[k]]));

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME, args: ['--no-sandbox']});
  const errs = [];
  const rep = {url: base, width: W, errors: errs, fails: [], checks: 0, clicks: 0, shots: [], notes: [], trades: []};
  const fail = m => rep.fails.push(m);
  const ok = (c, m) => { rep.checks++; if (!c) fail(m); };
  const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b), m + ': ' + JSON.stringify(a).slice(0, 200) + ' ≠ ' + JSON.stringify(b).slice(0, 200));
  const newPage = async theme => {
    const ctx = await browser.newContext({viewport: {width: W, height: 900}, hasTouch: W <= 640, deviceScaleFactor: 1});
    const page = await ctx.newPage();
    page.on('pageerror', e => errs.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
    page.on('requestfailed', r => errs.push('requestfailed: ' + r.url()));
    await page.goto(base + '#trades/main/0'); await page.evaluate(t => localStorage.setItem('p42-theme', t), theme);
    await page.reload(); await page.waitForSelector('#tr-per button', {timeout: 20000});
    return {ctx, page};
  };
  const jsonAt = (page, p) => page.evaluate(async u => (await fetch(u, {cache: 'no-cache'})).json(), base + p);

  // ── выбор сделок
  const probe = await newPage('dark');
  const idx = await jsonAt(probe.page, 'data/trades/index.json'), chk = (await jsonAt(probe.page, 'data/trades/check.json')).orders;
  await probe.ctx.close();
  const T = idx.trades.map(a => Object.fromEntries(idx.cols.map((c, k) => [c, a[k]])));
  const magma = T.find(r => r.sym === 'MAGMAUSDT' && fmtMin(r.entry_ms) === '24.10 08:14' && r.i < 1000);
  ok(!!magma, 'в наборе main есть MAGMAUSDT 24.10 08:14');
  const stopT = T.filter(r => r.i < 1000 && r.kind === 'stop' && r !== magma)[3];
  const d30 = T.filter(r => r.i >= 1000);
  ok(d30.length > 5, 'набор «30 дней» есть: ' + d30.length);
  const d30T = d30.find(r => r.kind === 'trail') || d30[0];
  const pick = [[magma, 'main', 'dark'], [stopT, 'main', 'dark'], [d30T, 'd30', 'dark'], [magma, 'main', 'light']].filter(p => p[0]);
  rep.picked = pick.map(([x, s, th]) => ({i: x.i, sym: x.sym, t: fmtMin(x.entry_ms), kind: x.kind, set: s, theme: th}));

  for (const [x, set, theme] of pick) {
    const {ctx, page} = await newPage(theme);
    const tag = `${theme} #${x.i} ${x.sym}`;
    await page.goto(base + `#trades/${set}/0`); await page.waitForSelector('#tr-per button');
    await page.click(`table[data-trt] tbody tr[data-i="${x.i}"]`); rep.clicks++;
    await page.waitForSelector('#tr-detail [data-chart="trade"][data-ready="1"]', {timeout: 20000});
    const raw = await jsonAt(page, `data/trades/${x.i}.json`);
    const dbg = () => page.evaluate(() => window.P42Trades.debug());
    const frames = async () => (await dbg()).drawn.frames;
    const repaint = async f0 => { await page.waitForFunction(f => window.P42Trades.debug().drawn.frames > f, f0, {timeout: 5000}); await page.waitForTimeout(250); };      // кадр после клика + покой: данные и холст дошли до конечного состояния
    const pal = COL[theme];

    // A. данные: файл == CSV
    const ex = rawCounts(raw), cc = csvCounts(chk[String(x.i)]);
    eq(sortObj(ex.g), sortObj(cc.g), tag + ': заявки файла == CSV (группы: заявок, отрезков, исполнено, снято)');
    eq(sortObj(ex.lv), sortObj(cc.lv), tag + ': уровни файла == CSV');
    ok(raw.ord.length > 0 && raw.lv.length > 0, tag + ': заявки и уровни в файле: ' + raw.ord.length + ' / ' + raw.lv.length);
    // B. график: данные приложения == CSV
    let d = await dbg();
    for (const g of GRPS) {
      const c = cc.g[g] || [0, 0, 0, 0];
      eq([d.ov.ord[g], d.ov.seg[g], d.ov.fill[g], d.ov.cancel[g]], c, `${tag}: ${g}: график (заявок, отрезков, исполнено, снято) == CSV`);
    }
    eq(sortObj(d.ov.lv), sortObj(cc.lv), tag + ': уровни графика == CSV');
    // C. легенда
    const lg = await page.evaluate(() => [...document.querySelectorAll('#tr-detail [data-og]')].map(b => ({k: b.dataset.og, n: +b.dataset.n, on: b.getAttribute('aria-pressed') === 'true', txt: b.textContent, w: b.getBoundingClientRect().right, h: b.getBoundingClientRect().height})));
    const wantKeys = ['e1', 'e2', 'take', 'stop', 'rt'].concat((cc.g.other || [0])[0] > 0 ? ['other'] : [], ['lv']);
    eq(lg.map(b => b.k), wantKeys, tag + ': кнопки легенды');
    for (const b of lg) {
      const want = b.k === 'lv' ? raw.lv.length : (cc.g[b.k] || [0])[0];
      ok(b.n === want && b.txt.trim().endsWith(String(want)) && b.on, `${tag}: кнопка ${b.k}: число ${b.n} (ждали ${want}), нажата ${b.on}, «${b.txt}»`);
      ok(b.w <= W + 1, `${tag}: кнопка ${b.k} внутри экрана (${Math.round(b.w)} ≤ ${W})`);
    }
    // D. нарисовано == данные
    const same = (dr, o, m) => { for (const g of GRPS) eq([dr.seg[g] || 0, dr.fill[g] || 0, dr.cancel[g] || 0], [o.seg[g], o.fill[g], o.cancel[g]], `${m}: ${g} нарисовано (отрезков, исполнено, снято) == данные`); eq(sortObj(dr.lv), sortObj(o.lv), m + ': уровни нарисованы == данные'); };
    same(d.drawn, d.ov, tag);
    rep.shots.push(await shot(page, `${out}/tk745-${W}-${theme}-${x.i}.png`));      // исходный вид: 1м, все группы

    ok(d.tf === '1m', tag + ': по умолчанию 1м: ' + d.tf);
    // для всех исполнений: точка в области графика
    // E. кнопки групп
    for (const b of lg) {
      const before = (await dbg()), f0 = before.drawn.frames;
      await page.click(`#tr-detail [data-og="${b.k}"]`); rep.clicks++; await repaint(f0);
      let a = await dbg();
      const pr = await page.evaluate(k => document.querySelector(`#tr-detail [data-og="${k}"]`).getAttribute('aria-pressed'), b.k);
      ok(pr === 'false', `${tag}: ${b.k} выключена`);
      if (b.k === 'lv') eq(sortObj(a.drawn.lv), {}, tag + ': lv выключены — уровней нет');
      else eq([a.drawn.seg[b.k] || 0, a.drawn.fill[b.k] || 0, a.drawn.cancel[b.k] || 0], [0, 0, 0], `${tag}: ${b.k} выключена — на холсте 0`);
      for (const g of GRPS) if (g !== b.k) eq([a.drawn.seg[g] || 0, a.drawn.fill[g] || 0, a.drawn.cancel[g] || 0], [before.drawn.seg[g] || 0, before.drawn.fill[g] || 0, before.drawn.cancel[g] || 0], `${tag}: ${b.k} выключена — группа ${g} не тронута`);
      if (b.k !== 'lv') eq(sortObj(a.drawn.lv), sortObj(before.drawn.lv), `${tag}: ${b.k} выключена — уровни не тронуты`);
      if (b.n > 0) ok(a.ovPrices.length < before.ovPrices.length, `${tag}: ${b.k} выключена — автомасштаб без её цен (${a.ovPrices.length} < ${before.ovPrices.length})`);
      const f1 = a.drawn.frames;
      await page.click(`#tr-detail [data-og="${b.k}"]`); rep.clicks++; await repaint(f1);
      a = await dbg();
      same(a.drawn, a.ov, `${tag}: ${b.k} включена снова`);
      eq(a.ovPrices.length, before.ovPrices.length, `${tag}: ${b.k} включена — масштаб как был`);
    }
    // F. подсказки и цвета: по каждой группе — отрезок и исполнение; позиция — подбор вокруг координаты по времени и цене
    const probeAt = (t, price, label, second) => page.evaluate(({t, price, label, second}) => {
      const c = window.P42Trades.ovCoord(t, price); if (!c) return {nocoord: true};
      for (let r = 0; r <= 14; r++) for (let dx = -r; dx <= r; dx++) for (let dy = -Math.min(r, 4); dy <= Math.min(r, 4); dy++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r && Math.abs(dy) !== Math.min(r, 4)) continue;
        const hs = window.P42Trades.ovHit(c.x + dx, c.y + dy) || [];
        const h = hs.find(t => t[0].startsWith(label) && (!second || (t[1] || '').startsWith(second)));
        if (h) return {x: c.x + dx, y: c.y + dy, title: h};
      }
      return {miss: true, at: c, n: (window.P42Trades.ovHit(c.x, c.y) || []).length};
    }, {t, price, label, second});
    const pixAt = (x, y, rgb, rad, tol) => page.evaluate(({x, y, rgb, rad, tol}) => window.P42Trades.pix(x - rad, y - rad, 2 * rad + 1, 2 * rad + 1, rgb, tol), {x, y, rgb, rad, tol});
    const done = {};
    for (const [role, lot, typ, pm, kind, tend, pend, note, aux, path, fpm] of raw.ord) {
      const g = grpOf(role);
      const key = g + kind;
      if (done[key]) continue;
      done[key] = 1;
      const px0 = path[1] / 1e8, t0 = raw.t0 + path[0];
      const sg = await probeAt(bar(t0 + 1), px0, ROLE_RU(role) + ' ·', 'цена ');
      if (sg.nocoord) { rep.notes.push(`${tag}: ${role} отрезок вне окна 1м`); }
      else {
        ok(!!sg.title, `${tag}: подсказка на отрезке ${role} (${JSON.stringify(sg)})`);
        if (sg.title) {
          ok(sg.title[0].includes(`лот ${lot + 1}`) && sg.title[0].includes(shareTxt(pm) + ' ставки'), `${tag}: ${role}: подсказка «${sg.title[0]}»: лот ${lot + 1}, ${shareTxt(pm)}`);
          ok(sg.title.some(s => s.startsWith('цена ')) && sg.title.some(s => s.startsWith('с ' + fmtMs(t0) + ' до ')), `${tag}: ${role}: цена и время отрезка в подсказке: ${JSON.stringify(sg.title)}`);
        }
      }
      if (kind === 'f') {
        const ft = await probeAt(bar(raw.t0 + tend), pend / 1e8, ROLE_RU(role) + ' ·', 'исполнено');
        if (ft.nocoord) { rep.notes.push(`${tag}: ${role} исполнение вне окна 1м`); continue; }
        ok(!!ft.title, `${tag}: подсказка на исполнении ${role} (${JSON.stringify(ft)})`);
        if (ft.title) {
          const tm = fmtMs(raw.t0 + tend);
          ok(ft.title[1].includes('исполнено ' + shareTxt(fpm) + ' ставки') && ft.title[1].includes(' в ' + tm), `${tag}: ${role}: исполнение «${ft.title[1]}» — доля ${shareTxt(fpm)}, время ${tm}`);
          if (aux) ok(ft.title.some(s => s.includes('условие сработало в ' + fmtMs(raw.t0 + aux))), `${tag}: ${role}: время срабатывания условия ${fmtMs(raw.t0 + aux)}`);
          const n = await pixAt(ft.x, ft.y, hex(pal[g]), 10, 24);
          ok(n >= 3, `${tag}: ${role}: круг исполнения цвета ${g} (${n} точек)`);
          if (role === 'e1') {      // курсор мышью / касанием: блок [data-ord] у подсказки графика
            const box = await page.locator('#tr-detail [data-chart="trade"]').boundingBox();
            let cdp = null;
            if (W > 640) await page.mouse.move(box.x + ft.x, box.y + ft.y);
            else { cdp = await ctx.newCDPSession(page); await cdp.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x: box.x + ft.x, y: box.y + ft.y}]}); await page.waitForTimeout(800); }      // касание: перекрестие по долгому нажатию
            await page.waitForTimeout(400);
            const tip = await page.evaluate(() => { const e = document.querySelector('#tr-detail [data-ord]'); return e ? {hidden: e.hidden, txt: e.textContent} : null; });
            ok(tip && !tip.hidden && tip.txt.includes('вход e1') && tip.txt.includes('исполнено ' + shareTxt(fpm) + ' ставки') && tip.txt.includes(tm), `${tag}: подсказка под курсором — заявка e1 в блоке [data-ord]: ${JSON.stringify(tip).slice(0, 220)}`);
            rep.shots.push(await shot(page, `${out}/tk745-${W}-${theme}-${x.i}-tip.png`));
            if (cdp) await cdp.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
          }
        }
      }
    }
    // цвет отрезка группы: самый длинный по экрану отрезок группы (короткие закрыты кругами исполнения — в подсказке они есть)
    for (const g of GRPS) {
      let best = null;
      for (const [role, lot, , pm, kind, tend, , , , path] of raw.ord) {
        if (grpOf(role) !== g) continue;
        for (let i = 0; i < path.length / 3; i++) {
          const a0 = raw.t0 + path[3 * i], a1 = i + 1 < path.length / 3 ? raw.t0 + path[3 * i + 3] : kind === 'o' ? x.exit_ms : raw.t0 + tend;
          const xa = await page.evaluate(([a, b]) => [window.P42Trades.ovX(a), window.P42Trades.ovX(b)], [a0, a1]);
          const len = Math.min(xa[1], 1e4) - Math.max(xa[0], -1e4);
          if (!best || len > best.len) best = {len, role, a0, a1, px: path[3 * i + 1] / 1e8, lot};
        }
      }
      if (!best) continue;
      if (best.len < 30) { rep.notes.push(`${tag}: ${g}: самый длинный отрезок ${Math.round(best.len)} px — цвет не проверяем (закрыт отметками)`); continue; }
      const sg = await probeAt(bar((best.a0 + best.a1) / 2), best.px, ROLE_RU(best.role) + ' ·', 'цена ');
      ok(!!sg.title, `${tag}: ${g}: подсказка на самом длинном отрезке (${JSON.stringify(sg).slice(0, 140)})`);
      if (sg.title) {
        const n = await page.evaluate(({x, y, rgb}) => window.P42Trades.pix(x - 10, y - 2, 21, 5, rgb, 60), {x: sg.x, y: sg.y, rgb: hex(pal[g])});
        ok(n > 0, `${tag}: ${g}: цвет ${pal[g]} у отрезка (${n})`);
      }
    }
    // G. MAGMAUSDT 24.10 08:14: пивоты 22.10 01:00 0,31132 и 22.10 08:00 0,31025
    if (x === magma) {
      const piv = raw.lv.filter(l => l[0] === 'T');
      for (const [pe8, when] of [[31132000, '22.10 01:00'], [31025000, '22.10 08:00']]) {
        const p = piv.find(l => l[1] === pe8 && fmtMin(raw.t0 + l[2]) === when);
        ok(!!p, `${tag}: в lv пивот ${pe8 / 1e8} от ${when} GMT+4`);
        if (!p) continue;
        const pt = await probeAt(bar(x.entry_ms), pe8 / 1e8, 'пивот-член', null);
        ok(!!pt.title, `${tag}: линия пивота ${pe8 / 1e8} на графике, подсказка (${JSON.stringify(pt).slice(0, 160)})`);
        if (pt.title) {
          ok(pt.title[1].includes(fmtMin(raw.t0 + p[2])), `${tag}: подсказка пивота: бар ${when}: ${JSON.stringify(pt.title)}`);
          const n = await pixAt(pt.x, pt.y, hex(pal.pivot), 40, 40);
          ok(n > 0, `${tag}: пивот ${pe8 / 1e8}: цвет пивота ${pal.pivot} на холсте (${n})`);
        }
      }
      const lvl = raw.lv.find(l => l[0] === 'L');
      const lt = await probeAt(bar(x.entry_ms), lvl[1] / 1e8, 'торгуемый уровень', null);
      ok(!!lt.title, `${tag}: торгуемый уровень ${lvl[1] / 1e8} — подсказка (${JSON.stringify(lt).slice(0, 120)})`);
    }
    // H. узкий экран, снимки; смена таймфрейма не меняет счёт
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    ok(over <= 1, `${tag}: перелив вправо ${over}`);
    const cb = await page.locator('#tr-detail [data-chart="trade"]').boundingBox();
    ok(cb.width <= W + 1, `${tag}: график не шире экрана (${cb.width})`);
    await page.click('#tr-detail button[data-tf="5m"]'); rep.clicks++;
    await page.waitForFunction(() => window.P42Trades.debug() && window.P42Trades.debug().tf === '5m' && window.P42Trades.debug().ready, null, {timeout: 8000});
    await page.waitForTimeout(300);
    d = await dbg();
    eq(sortObj(d.ov.lv), sortObj(cc.lv), tag + ': 5м: уровни те же');
    for (const g of GRPS) eq([d.ov.ord[g], d.ov.seg[g], d.ov.fill[g], d.ov.cancel[g]], cc.g[g] || [0, 0, 0, 0], `${tag}: 5м: ${g} == CSV`);
    same(d.drawn, d.ov, tag + ': 5м');
    rep.trades.push({i: x.i, sym: x.sym, theme, ord: raw.ord.length, lv: raw.lv.length, segs: Object.values(ex.g).reduce((a, c) => a + c[1], 0), bytes: JSON.stringify(raw).length});
    await ctx.close();
  }

  async function shot(page, f) { await page.screenshot({path: f}); return f; }
  rep.errorsCount = errs.length;
  fs.writeFileSync(out + '/report.json', JSON.stringify(rep, null, 1));
  rep.fails.forEach(f => console.log('ПРОВАЛ ' + f.slice(0, 330)));
  errs.forEach(e => console.log('ОШИБКА ' + e.slice(0, 200)));
  console.log(`ИТОГ ширина ${W}: проверок ${rep.checks}, кликов ${rep.clicks}, провалов ${rep.fails.length}, ошибок страницы ${errs.length}`);
  await browser.close();
  process.exit(rep.fails.length || errs.length ? 1 : 0);
})().catch(e => { console.error('СБОЙ', e); process.exit(2); });
