// П-42 TK-696: страница с кэшем браузера «до обновления» — веер загружается без ручной очистки (VPS София, node18, playwright-core; НЕ на ПК владельца).
// Запуск: PW=… CHROME=… node tk696_stale_check.js <url страницы> <каталог профиля> <goto|reload> <short|long> — профиль постоянный: кэш живёт между вызовами.
// Печатает одну строку JSON: {side, mode, err (текст узла data-fanerr или null), n (линий на холсте), fetched: [адреса пакетов с сети]}; код 1, если err или нет холста.
const {chromium} = require(process.env.PW || 'playwright-core');
const [base, prof, mode, side] = process.argv.slice(2);
(async () => {
  const ctx = await chromium.launchPersistentContext(prof, {executablePath: process.env.CHROME || undefined, args: ['--no-sandbox'], viewport: {width: 1280, height: 900}});
  const page = ctx.pages()[0] || await ctx.newPage();
  const fetched = [];
  page.on('requestfinished', r => { if (/\/data\/fan\//.test(r.url())) fetched.push(r.url().replace(/^https?:\/\/[^/]+/, '')); });
  const url = base + '#equity/fan/' + side + '/lv=all';
  if (mode === 'reload') { await page.goto(base, {waitUntil: 'load'}); await page.evaluate(h => { location.hash = h; }, '#equity/fan/' + side + '/lv=all'); await page.reload({waitUntil: 'load'}); }
  else await page.goto(url, {waitUntil: 'load'});
  await page.waitForFunction(() => document.querySelector('[data-fanerr]') || (document.querySelector('canvas[data-fan="base"]') || {dataset: {}}).dataset.n != null && !document.querySelector('[data-fanload]'), null, {timeout: 90000}).catch(() => {});
  const r = await page.evaluate(() => ({err: (document.querySelector('[data-fanerr]') || {}).textContent || null, n: +((document.querySelector('canvas[data-fan="base"]') || {dataset: {}}).dataset.n || -1)}));
  console.log(JSON.stringify({side, mode, ...r, fetched: fetched.length ? fetched.slice(0, 6) : []}));
  await ctx.close();
  process.exit(r.err || r.n < 0 ? 1 : 0);
})();
