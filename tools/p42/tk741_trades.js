// ── «Сделки» (TK-741, В-192; TK-742): вкладка — приложение React + shadcn/ui (tools/p42/trades-app), собранное в trades-app/trades.js и trades.css рядом со страницей.
// Здесь только мост: подгрузка файлов при первом входе на вкладку и общее состояние TR (период, кластер, открытая сделка) с хешем #trades/<период: main|JAN|N1|all>/<кластер, 0 — все>[/<№ сделки>].
// Вставляется в tk593_template.html сборкой (tk593_page.py, метка __TRADESJS__). Данные — data/trades/ (tk741_pagedata.py): index.json и <№>.json (минутки сделки).
const TR = {per: 'main', cl: 0, open: null};
const TR_SUB = 'шорт, база без выхода по времени · после комиссий · 500 $ на сделку · время GMT+4 · кластеры найдены на этих данных';
const TR_PER_IDS = ['main', 'JAN', 'N1', 'all'], TR_APP_V = /*__TRADESV__*/'';
let trMounted = false, trBoot = null;
function trHash() { return '/' + TR.per + '/' + TR.cl + (TR.open != null ? '/' + TR.open : ''); }
function trParse(b, c, d) {
  TR.per = TR_PER_IDS.includes(b) ? b : 'main';
  TR.cl = /^\d+$/.test(c || '') ? +c : 0;
  TR.open = /^\d+$/.test(d || '') ? +d : null;
}
function trAssets() {
  if (trBoot) return trBoot;
  const q = TR_APP_V ? '?v=' + TR_APP_V : '';
  trBoot = new Promise((ok, no) => {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = 'trades-app/trades.css' + q;
    const js = document.createElement('script'); js.src = 'trades-app/trades.js' + q; js.onload = ok; js.onerror = () => no(new Error('trades-app/trades.js не загрузился'));
    document.head.append(css, js);
  });
  trBoot.catch(() => { trBoot = null; });
  return trBoot;
}
function renderTrades() {
  const root = document.getElementById('trades');
  if (trMounted) { window.P42Trades.sync(); return; }
  if (!root.firstChild) root.append(h('div', {class: 'eq-h'}, [h('h2', {}, 'Сделки'), h('span', {class: 'muted small'}, TR_SUB)]), h('div', {class: 'fan-msg'}, 'загрузка…'));
  trAssets().then(() => {
    if (!trMounted) { trMounted = true; const app = h('div', {id: 'trades-root'}); root.replaceChildren(app); window.P42Trades.mount(app, TR, setHash); }
    else window.P42Trades.sync();
  }).catch(e => { root.replaceChildren(h('div', {class: 'fan-msg'}, 'не загрузилось: ' + (e.message || e))); });
}
