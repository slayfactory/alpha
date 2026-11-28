import {useEffect, useLayoutEffect, useRef} from 'react';
import {ColorType, CrosshairMode, LineStyle, createChart, type IChartApi, type IPriceLine, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp} from 'lightweight-charts';
import {GMT4, TFS, aggregate, aggregateNat, bucket, type Bar, type Minutes} from '@/lib/bars';
import {ints, num, pad2, trPx} from '@/lib/fmt';
import {Overlay, ovColors} from '@/components/overlay';

export type Trade = {i: number; sym: string; entry_ms: number; exit_ms: number; entry_px: number; exit_px: number; lvl: number; kind: string; be_px: number};

// цвета трёх линий: вход / выход / безубыток (уровень и график — нейтральные)
export const lineColors = (dark: boolean) => dark ? {entry: '#60a5fa', exit: '#fb923c', be: '#c084fc'} : {entry: '#2563eb', exit: '#ea580c', be: '#9333ea'};
export const BE_TIP = 'Безубыток — цена, при которой P&L сделки равен нулю после комиссий (шорт: вход × (1 − комиссии / ставка 500 $)). Безубыточного стопа в базе нет (bm=1), поэтому это нулевая линия с учётом комиссий.';
// окно при открытии, по таймфреймам: запас в секундах до входа и после выхода; null — всё загруженное окно
export const RANGE_PAD: Record<string, number | null> = {'1m': 3600, '5m': 6 * 3600, '15m': 24 * 3600, '1h': null, '4h': null};

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function rgba(hex: string, a: number) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  return m ? `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${a})` : hex;
}
const utc = (t: number) => new Date(t * 1000);
const fmtTime = (t: number) => { const d = utc(t); return `${pad2(d.getUTCDate())}.${pad2(d.getUTCMonth() + 1)} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
const fixed = (v: number, d: number) => num(v, d);

function palette() {
  const c = {card: css('--card'), fg: css('--fg'), mfg: css('--mfg'), border: css('--border'), grid: css('--grid'), dash: css('--dash'), accent: css('--accent'), z400: css('--z400'), z600: css('--z600'), onfg: css('--onfg')};
  return c;
}
function chartOptions(p: ReturnType<typeof palette>, dark: boolean) {
  return {
    layout: {background: {type: ColorType.Solid, color: p.card}, textColor: p.mfg, fontSize: 11, fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'},
    grid: {vertLines: {color: p.grid}, horzLines: {color: p.grid}},
    crosshair: {mode: CrosshairMode.Normal, vertLine: {color: p.dash, labelBackgroundColor: p.z600}, horzLine: {color: p.dash, labelBackgroundColor: p.z600}},
    rightPriceScale: {borderColor: p.border, minimumWidth: 80},      // одна ширина шкалы у свечей и NATR — оси времени совпадают
    timeScale: {borderColor: p.border},
  };
}

// окно — по логическим номерам свечей (ряд идёт без пропусков, пустые бакеты — пустые точки): время ↔ номер линейно
type Ser = {chart: IChartApi; b0: number; tfSec: number};
const getView = (s: Ser) => { const r = s.chart.timeScale().getVisibleLogicalRange(); return r ? {mid: s.b0 + ((r.from + r.to) / 2 + 0.5) * s.tfSec, span: (r.to - r.from) * s.tfSec} : null; };
const setView = (s: Ser, mid: number, span: number) => s.chart.timeScale().setVisibleLogicalRange({from: (mid - span / 2 - s.b0) / s.tfSec - 0.5, to: (mid + span / 2 - s.b0) / s.tfSec - 0.5});
const setRange = (s: Ser, from: number, to: number) => setView(s, (from + to) / 2, to - from);

export type ChartApi = {
  tf: string; bars: (Bar | null)[]; markers: any[]; lines: {k: string; price: number; color: string}[]; orders: Record<string, number>;
  ov: {xms: (ms: number) => number | null; counts: () => ReturnType<Overlay['counts']>; drawn: () => Overlay['drawn']; hit: (px: number, py: number) => string[][]; prices: () => number[]; coord: (t: number, price: number) => {x: number; y: number} | null}; visible: () => {from: number; to: number} | null;
  nat: {values: (number | null)[]; sig: {time: number; value: number} | null; cross: () => number | null; visible: () => {from: number; to: number} | null; pix: (x: number, y: number, w: number, h: number, rgb: number[] | null, tol: number) => number; box: () => {w: number; h: number}};
  coord: (t: number, price: number) => {x: number; y: number} | null; ready: boolean;
  pix: (x: number, y: number, w: number, h: number, rgb: number[] | null, tol: number) => number;      // для клик-теста: сколько точек цвета rgb в прямоугольнике снимка графика
};
export const chartRef: {cur: ChartApi | null} = {cur: null};

export function TradeChart({x, minutes, tf, dark, resetKey, kindLabel, show}: {x: Trade; minutes: Minutes; tf: string; dark: boolean; resetKey: number; kindLabel: string; show: Record<string, boolean>}) {
  const box = useRef<HTMLDivElement>(null), nbox = useRef<HTMLDivElement>(null), tipRef = useRef<HTMLDivElement>(null);
  const st = useRef<{chart: IChartApi; candles: ISeriesApi<'Candlestick'>; vol: ISeriesApi<'Histogram'>; link: ISeriesApi<'Line'>; mkE: ISeriesApi<'Line'>; mkX: ISeriesApi<'Line'>; lines: IPriceLine[]; ov: Overlay; nchart: IChartApi; nat: ISeriesApi<'Line'>; natv: (number | null)[]; natLine: IPriceLine | null; natXT: number | null; xsync: boolean; mk: SeriesMarker<Time>[]; bars: (Bar | null)[]; tfSec: number; b0: number; view: {mid: number; span: number} | null; inited: boolean} | null>(null);
  const tfSec = TFS.find(t => t[0] === tf)![2];
  // окно по умолчанию для таймфрейма: [вход − запас, выход + запас] или всё загруженное
  const defRange = (s: {b0: number; tfSec: number; bars: unknown[]}, tfk: string) => {
    const pad = RANGE_PAD[tfk];
    return pad == null ? {from: s.b0, to: s.b0 + s.bars.length * s.tfSec} : {from: x.entry_ms / 1000 + GMT4 - pad, to: x.exit_ms / 1000 + GMT4 + pad};
  };
  const step = minutes.step, prec = Math.max(0, Math.ceil(-Math.log10(step) - 1e-9));

  // создание графика (раз на сделку)
  useLayoutEffect(() => {
    const el = box.current!;
    const p = palette();
    const chart = createChart(el, {
      width: el.clientWidth || 600, height: el.clientHeight || 576,
      ...chartOptions(p, dark),
      timeScale: {borderColor: p.border, timeVisible: true, secondsVisible: false, rightOffset: 0, fixLeftEdge: true, fixRightEdge: true, minBarSpacing: 0.4,
        tickMarkFormatter: (t: number, type: number) => { const d = utc(t); return type >= 3 ? `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}` : `${pad2(d.getUTCDate())}.${pad2(d.getUTCMonth() + 1)}`; }},
      localization: {timeFormatter: (t: number) => fmtTime(t), priceFormatter: (v: number) => fixed(v, prec)},
      handleScroll: {mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false},
      handleScale: {mouseWheel: true, pinch: true, axisPressedMouseMove: true},
      kineticScroll: {touch: true, mouse: false},
    } as any);
    const pf = {type: 'custom' as const, formatter: (v: number) => fixed(v, prec), minMove: step};
    const extra = [x.lvl, x.entry_px, x.exit_px, x.be_px], ov = new Overlay();
    const candles = chart.addCandlestickSeries({
      upColor: p.card, downColor: p.z600, borderUpColor: p.z600, borderDownColor: p.z600, wickUpColor: p.z600, wickDownColor: p.z600, priceFormat: pf, lastValueVisible: false, priceLineVisible: false,
      autoscaleInfoProvider: (orig: () => any) => {
        const r = orig(); if (!r) return r;
        const vr = chart.timeScale().getVisibleRange(), a = x.entry_ms / 1000 + GMT4, b = x.exit_ms / 1000 + GMT4;
        if (!vr || (+vr.from <= b && +vr.to >= a)) { const ex = extra.concat(ov.prices()); r.priceRange.minValue = Math.min(r.priceRange.minValue, ...ex); r.priceRange.maxValue = Math.max(r.priceRange.maxValue, ...ex); }
        return r;
      },
    });
    candles.attachPrimitive(ov);
    chart.priceScale('right').applyOptions({scaleMargins: {top: 0.07, bottom: 0.2}});
    const vol = chart.addHistogramSeries({priceFormat: {type: 'volume'}, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false});
    chart.priceScale('vol').applyOptions({scaleMargins: {top: 0.84, bottom: 0}});
    const link = chart.addLineSeries({color: p.mfg, lineWidth: 1, lineStyle: LineStyle.Dotted, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false});
    // стрелки входа и выхода — на своих рядах, созданных позже свечей: маркеры рядов рисуются поверх примитива заявок (одна точка на ряд — вход у максимума свечи, выход у минимума)
    const mkOpt = {color: p.mfg, lineWidth: 1 as const, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false};
    const mkE = chart.addLineSeries(mkOpt), mkX = chart.addLineSeries(mkOpt);
    const lc = lineColors(dark);
    const lines = [      // порядок = chartRef.lines
      candles.createPriceLine({price: x.lvl, color: p.mfg, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'уровень'}),
      candles.createPriceLine({price: x.entry_px, color: lc.entry, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'вход'}),
      candles.createPriceLine({price: x.exit_px, color: lc.exit, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'выход (ср.)'}),
      candles.createPriceLine({price: x.be_px, color: lc.be, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'безубыток'}),
    ];
    // NATR30 — отдельный график под свечами (lightweight-charts v4 без панелей): та же ширина шкалы, окно по времени и перекрестие синхронизированы
    const nel = nbox.current!;
    const nchart = createChart(nel, {
      width: nel.clientWidth || 600, height: nel.clientHeight || 92,
      ...chartOptions(p, dark),
      timeScale: {borderColor: p.border, visible: false, rightOffset: 0, fixLeftEdge: true, fixRightEdge: true, minBarSpacing: 0.4},
      localization: {timeFormatter: (t: number) => fmtTime(t), priceFormatter: (v: number) => fixed(v, 2)},
      handleScroll: {mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false},
      handleScale: {mouseWheel: true, pinch: true, axisPressedMouseMove: false},
      kineticScroll: {touch: true, mouse: false},
    } as any);
    nchart.priceScale('right').applyOptions({scaleMargins: {top: 0.16, bottom: 0.1}});
    const nat = nchart.addLineSeries({color: p.z600, lineWidth: 2, priceFormat: {type: 'custom', formatter: (v: number) => fixed(v, 2), minMove: 0.001}, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: true});
    let rs = false;
    const linkRange = (a: IChartApi, b: IChartApi) => a.timeScale().subscribeVisibleLogicalRangeChange(r => { if (rs || !r) return; rs = true; try { b.timeScale().setVisibleLogicalRange(r); } finally { rs = false; } });
    linkRange(chart, nchart); linkRange(nchart, chart);
    st.current = {chart, candles, vol, link, mkE, mkX, lines, ov, nchart, nat, natv: [], natLine: null, natXT: null, xsync: false, mk: [], bars: [], tfSec: 0, b0: 0, view: null, inited: false};
    nchart.subscribeCrosshairMove(param => {      // перекрестие с NATR → на свечи (цена — закрытие свечи под курсором)
      const s = st.current;
      if (!s || s.xsync) return;
      s.xsync = true;
      try {
        if (param.time == null || !param.point) { chart.clearCrosshairPosition(); return; }
        const b = s.bars[Math.round((+param.time - s.b0) / s.tfSec)];
        chart.setCrosshairPosition(b ? b.close : x.entry_px, param.time, candles);
      } finally { s.xsync = false; }
    });
    chart.subscribeCrosshairMove(param => {
      const tip = tipRef.current, s = st.current;
      if (!tip || !s) return;
      if (!s.xsync) {      // перекрестие со свечей → на NATR
        s.xsync = true;
        try {
          if (param.time == null || !param.point) { nchart.clearCrosshairPosition(); s.natXT = null; }
          else { const v = s.natv[Math.round((+param.time - s.b0) / s.tfSec)]; nchart.setCrosshairPosition(v ?? 0, param.time, nat); s.natXT = +param.time; }
        } finally { s.xsync = false; }
      }
      if (!param.point || param.time == null || param.point.x < 0 || param.point.y < 0) { tip.style.display = 'none'; return; }
      const k = Math.round((+param.time - s.b0) / s.tfSec), b = s.bars[k];
      const set = (key: string, v: string) => { const n = tip.querySelector(`[data-k="${key}"]`); if (n) n.textContent = v; };
      set('time', fmtTime(+param.time) + ' GMT+4');
      const pr = s.candles.coordinateToPrice(param.point.y); set('px', pr == null ? '—' : fixed(pr as number, prec));
      tip.querySelector('[data-rows]')!.toggleAttribute('hidden', !b);
      tip.querySelector('[data-none]')!.toggleAttribute('hidden', !!b);
      const nv = s.natv[k]; set('natr', nv == null ? '—' : fixed(nv, 2) + ' %');
      if (b) { set('open', fixed(b.open, prec)); set('high', fixed(b.high, prec)); set('low', fixed(b.low, prec)); set('close', fixed(b.close, prec)); set('q', ints(Math.round(b.q)) + ' $'); }
      {      // заявки и уровни сетапа под курсором (TK-745)
        const box2 = tip.querySelector('[data-ord]') as HTMLElement, hs = s.ov.hit(param.point.x, param.point.y);
        box2.textContent = '';
        hs.slice(0, el.clientWidth <= 640 ? 3 : 6).forEach(h => { const d = document.createElement('div'); d.className = 'mt-1 border-t pt-1'; h.title.forEach((t, k) => { const r = document.createElement('div'); r.textContent = t; if (k === 0) r.className = 'font-semibold'; d.appendChild(r); }); box2.appendChild(d); });
        if (hs.length > (el.clientWidth <= 640 ? 3 : 6)) { const d = document.createElement('div'); d.className = 'mt-1 text-muted-foreground'; d.textContent = 'ещё ' + (hs.length - (el.clientWidth <= 640 ? 3 : 6)); box2.appendChild(d); }
        box2.toggleAttribute('hidden', !hs.length);
      }
      tip.style.display = 'block';
      const W = el.clientWidth, H = el.clientHeight;
      tip.style.maxHeight = Math.max(80, H - 8) + 'px'; tip.style.overflow = 'hidden';      // подсказка с заявками не выходит за график (на узком экране — не закрывает панель NATR)
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let lx = param.point.x + 14, ly = param.point.y + 14;
      if (lx + tw > W - 60) lx = param.point.x - tw - 14;
      if (ly + th > H - 24) ly = Math.max(4, param.point.y - th - 14);
      tip.style.left = Math.max(4, lx) + 'px'; tip.style.top = Math.max(4, ly) + 'px';
    });
    el.addEventListener('mouseleave', () => { if (tipRef.current) tipRef.current.style.display = 'none'; });
    nel.addEventListener('mouseleave', () => { if (tipRef.current) tipRef.current.style.display = 'none'; });
    // размер — по контейнеру; при изменении ширины окно по времени сохраняется (иначе lightweight-charts держит ширину свечи и сдвигает окно)
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth, h = el.clientHeight, s = st.current;
      if (!s || !w || !h || (w === lastW && h === lastH)) return;
      const vr = s.inited ? getView(s) : null;
      lastW = w; lastH = h; s.chart.resize(w, h); s.nchart.resize(w, nel.clientHeight || 92);
      if (vr) setView(s, vr.mid, vr.span);
    });
    let lastW = el.clientWidth, lastH = el.clientHeight;
    ro.observe(el);
    return () => { ro.disconnect(); chart.remove(); nchart.remove(); st.current = null; chartRef.cur = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [x.i]);

  // заявки движка и уровни сетапа (TK-745): примитив на холсте графика; цвета — по теме, видимость групп — из легенды
  function paintOv(s: NonNullable<typeof st.current>) {
    const p = palette(), c = ovColors(dark);
    s.ov.set({ords: minutes.ords, lvs: minutes.lvs, show, b0: s.b0, tfSec: s.tfSec, exitMs: x.exit_ms, colors: {...c, setup: p.mfg || c.setup, card: p.card, fg: p.fg}});
    s.mkE.setMarkers(s.mk.filter(m => m.shape === 'arrowDown')); s.mkX.setMarkers(s.mk.filter(m => m.shape === 'arrowUp'));      // стрелки входа и выхода сделки (прежние, поверх заявок)
    s.chart.priceScale('right').applyOptions({autoScale: true});
  }

  // NATR: линия сигнала (значение движка на минуте сигнала) и отметка на свече сигнала текущего таймфрейма
  function paintNat(s: NonNullable<typeof st.current>) {
    if (s.natLine) { s.nat.removePriceLine(s.natLine); s.natLine = null; }
    const sv = natSig(s);
    if (!sv) { s.nat.setMarkers([]); return; }
    const col = lineColors(dark).entry;
    s.natLine = s.nat.createPriceLine({price: sv.value, color: col, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: 'сигнал'});
    s.nat.setMarkers([{time: sv.time as UTCTimestamp, position: 'inBar', color: col, shape: 'circle', text: 'сигнал ' + fixed(sv.value, 2) + ' %'}]);
  }
  const natSig = (s: {b0: number; tfSec: number; bars: unknown[]}) => {
    const k = Math.round((minutes.sig - (minutes.t0 / 1000 + GMT4)) / 60), v = minutes.nat[k];
    if (v == null || !s.bars.length) return null;
    return {time: Math.min(s.b0 + (s.bars.length - 1) * s.tfSec, Math.max(s.b0, Math.floor(minutes.sig / s.tfSec) * s.tfSec)), value: v};
  };

  // данные: таймфрейм и минутки
  useEffect(() => {
    const s = st.current; if (!s) return;
    const bars = aggregate(minutes, tfSec);
    const first = bars.findIndex(b => b), b0 = bars.length ? (bars.find(b => b) as Bar).time - first * tfSec : 0;
    s.bars = bars; s.tfSec = tfSec; s.b0 = b0;
    const p = palette(), lc = lineColors(dark);
    const data: any[] = bars.map((b, k) => b ? {time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close} : {time: (b0 + k * tfSec) as UTCTimestamp});
    s.candles.setData(data);
    s.vol.setData(bars.map((b, k) => b ? {time: b.time as UTCTimestamp, value: b.q, color: rgba(b.close >= b.open ? p.z400 : p.mfg, 0.5)} : {time: (b0 + k * tfSec) as UTCTimestamp}) as any);
    const near = (ms: number) => { let k = Math.min(bars.length - 1, Math.max(0, (bucket(ms, tfSec) - b0) / tfSec)); while (k > 0 && !bars[k]) k--; return bars[k] ? bars[k]!.time : null; };
    const te = near(x.entry_ms), tx = near(x.exit_ms);
    s.link.setData(te != null && tx != null && te < tx ? [{time: te as UTCTimestamp, value: x.entry_px}, {time: tx as UTCTimestamp, value: x.exit_px}] : []);
    const mk: SeriesMarker<Time>[] = [];
    if (te != null) mk.push({time: te as UTCTimestamp, position: 'aboveBar', color: lc.entry, shape: 'arrowDown', text: 'вход ' + trPx(x.entry_px)});
    if (tx != null) mk.push({time: tx as UTCTimestamp, position: 'belowBar', color: lc.exit, shape: 'arrowUp', text: 'выход (ср.) ' + trPx(x.exit_px) + ' · ' + kindLabel});
    mk.sort((a, b) => +a.time - +b.time);
    const barAt = (t: number | null) => t == null ? undefined : bars.find(b => b && b.time === t);
    s.mkE.setData(te != null && barAt(te) ? [{time: te as UTCTimestamp, value: barAt(te)!.high}] : []);
    s.mkX.setData(tx != null && barAt(tx) ? [{time: tx as UTCTimestamp, value: barAt(tx)!.low}] : []);
    s.mk = mk; paintOv(s);
    s.natv = aggregateNat(minutes, tfSec, b0, bars.length);
    s.nat.setData(s.natv.map((v, k) => v == null ? {time: (b0 + k * tfSec) as UTCTimestamp} : {time: (b0 + k * tfSec) as UTCTimestamp, value: v}) as any);
    paintNat(s);
    s.inited = true;
    const dr = defRange(s, tf); setRange(s, dr.from, dr.to);      // каждый таймфрейм открывается со своим окном
    s.chart.priceScale('right').applyOptions({autoScale: true});
    const ca = (t: number, price: number) => {
      const cx = s.chart.timeScale().timeToCoordinate(t as UTCTimestamp), cy = s.candles.priceToCoordinate(price);
      return cx == null || cy == null ? null : {x: cx, y: cy};
    };
    chartRef.cur = {tf, bars, markers: mk.map(m => ({time: +m.time, position: m.position, shape: m.shape, text: m.text})), lines: [{k: 'level', price: x.lvl, color: p.mfg}, {k: 'entry', price: x.entry_px, color: lc.entry}, {k: 'exit', price: x.exit_px, color: lc.exit}, {k: 'breakeven', price: x.be_px, color: lc.be}], orders: s.ov.counts().seg,
      ov: {xms: (ms: number) => s.ov.xms(ms), counts: () => s.ov.counts(), drawn: () => s.ov.drawn, hit: (px: number, py: number) => s.ov.hit(px, py).map(h => h.title), prices: () => s.ov.prices(), coord: ca},
      visible: () => { const r = s.chart.timeScale().getVisibleRange(); return r ? {from: +r.from, to: +r.to} : null; }, coord: ca, ready: true,
      nat: {values: s.natv, sig: natSig(s), cross: () => s.natXT, visible: () => { const r = s.nchart.timeScale().getVisibleRange(); return r ? {from: +r.from, to: +r.to} : null; }, box: () => ({w: nbox.current!.clientWidth, h: nbox.current!.clientHeight}),
        pix: (x0, y0, w0, h0, rgb, tol) => {
          const cv = s.nchart.takeScreenshot(), k = cv.width / nbox.current!.clientWidth, g = cv.getContext('2d')!;
          const d = g.getImageData(Math.max(0, Math.round(x0 * k)), Math.max(0, Math.round(y0 * k)), Math.max(1, Math.round(w0 * k)), Math.max(1, Math.round(h0 * k))).data;
          const ref = rgb ?? Array.from(g.getImageData(1, 1, 1, 1).data);
          let n = 0; for (let q = 0; q < d.length; q += 4) { const m = Math.abs(d[q] - ref[0]) <= tol && Math.abs(d[q + 1] - ref[1]) <= tol && Math.abs(d[q + 2] - ref[2]) <= tol; if (rgb ? m : !m) n++; }
          return n;
        }},
      pix: (x0, y0, w0, h0, rgb, tol) => {
        const cv = s.chart.takeScreenshot(), k = cv.width / box.current!.clientWidth, g = cv.getContext('2d')!;
        const d = g.getImageData(Math.max(0, Math.round(x0 * k)), Math.max(0, Math.round(y0 * k)), Math.max(1, Math.round(w0 * k)), Math.max(1, Math.round(h0 * k))).data;
        const ref = rgb ?? Array.from(g.getImageData(1, 1, 1, 1).data);      // rgb = null: считать точки, отличные от фона графика
        let n = 0; for (let q = 0; q < d.length; q += 4) { const m = Math.abs(d[q] - ref[0]) <= tol && Math.abs(d[q + 1] - ref[1]) <= tol && Math.abs(d[q + 2] - ref[2]) <= tol; if (rgb ? m : !m) n++; }
        return n;
      }};
    box.current!.dataset.ready = '1'; box.current!.dataset.tf = tf; box.current!.dataset.n = String(bars.filter(b => b).length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tf, minutes]);

  // видимость групп заявок и уровней (легенда)
  useEffect(() => {
    const s = st.current; if (!s || !s.inited) return;
    paintOv(s);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [show]);

  // «К сделке»: центр и масштаб как при открытии
  useEffect(() => {
    const s = st.current; if (!s || !resetKey) return;
    const dr = defRange(s, tf); setRange(s, dr.from, dr.to);
    s.chart.priceScale('right').applyOptions({autoScale: true});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  // тема
  const themed = useRef(dark);
  useEffect(() => {
    const s = st.current; if (!s || themed.current === dark) return;      // первая отрисовка уже в цветах темы
    themed.current = dark;
    const p = palette();
    s.chart.applyOptions(chartOptions(p, dark) as any); s.nchart.applyOptions(chartOptions(p, dark) as any); s.nat.applyOptions({color: p.z600}); paintNat(s);
    s.candles.applyOptions({upColor: p.card, downColor: p.z600, borderUpColor: p.z600, borderDownColor: p.z600, wickUpColor: p.z600, wickDownColor: p.z600});
    s.link.applyOptions({color: p.mfg});
    const vr = getView(s);      // setData сбрасывает окно к правому краю — вернуть
    s.vol.setData(s.bars.map((b, k) => b ? {time: b.time as UTCTimestamp, value: b.q, color: rgba(b.close >= b.open ? p.z400 : p.mfg, 0.5)} : {time: (s.b0 + k * s.tfSec) as UTCTimestamp}) as any);
    if (vr) setView(s, vr.mid, vr.span);
    const lc = lineColors(dark);
    s.lines[0].applyOptions({color: p.mfg}); s.lines[1].applyOptions({color: lc.entry}); s.lines[2].applyOptions({color: lc.exit}); s.lines[3].applyOptions({color: lc.be});
    s.mk = s.mk.map(m => ({...m, color: m.shape === 'arrowDown' ? lc.entry : lc.exit}));
    paintOv(s);
  }, [dark]);

  return (
    <div className="relative">
      <div ref={box} data-chart="trade" className="h-[576px] w-full overflow-hidden rounded-md border mob:h-[512px]" />
      <div className="relative mt-1.5">
        <div ref={nbox} data-chart="trade.natr" className="h-[92px] w-full overflow-hidden rounded-md border mob:h-[80px]" />
        <span data-f="natr-label" className="pointer-events-none absolute left-2 top-1 z-10 text-[11px] text-muted-foreground">NATR30, %</span>
      </div>
      <div ref={tipRef} data-chart="trade.tip" style={{display: 'none'}} className="pointer-events-none absolute z-10 min-w-[150px] max-w-[min(340px,86vw)] rounded-md border bg-popover px-2.5 py-1.5 text-xs leading-snug text-popover-foreground shadow-md">
        <div data-k="time" className="font-semibold" />
        <div className="flex justify-between gap-4"><span className="text-muted-foreground">цена</span><span data-k="px" /></div>
        <div data-none hidden className="text-muted-foreground">нет сделок</div>
        <div data-rows>
          {[['open', 'открытие'], ['high', 'максимум'], ['low', 'минимум'], ['close', 'закрытие'], ['q', 'оборот'], ['natr', 'NATR30']].map(([k, t]) => (
            <div key={k} className="flex justify-between gap-4"><span className="text-muted-foreground">{t}</span><span data-k={k} /></div>
          ))}
        </div>
        <div data-ord hidden className="mt-1 max-w-[320px] text-[11px] leading-snug" />
      </div>
    </div>
  );
}
