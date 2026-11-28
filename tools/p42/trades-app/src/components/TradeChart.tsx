import {useEffect, useLayoutEffect, useRef} from 'react';
import {ColorType, CrosshairMode, LineStyle, createChart, type IChartApi, type IPriceLine, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp} from 'lightweight-charts';
import {GMT4, TFS, aggregate, bucket, type Bar, type Minutes} from '@/lib/bars';
import {ints, num, pad2, trPx} from '@/lib/fmt';

export type Trade = {i: number; sym: string; entry_ms: number; exit_ms: number; entry_px: number; exit_px: number; lvl: number; kind: string};

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
    rightPriceScale: {borderColor: p.border},
    timeScale: {borderColor: p.border},
  };
}

// окно — по логическим номерам свечей (ряд идёт без пропусков, пустые бакеты — пустые точки): время ↔ номер линейно
type Ser = {chart: IChartApi; b0: number; tfSec: number};
const getView = (s: Ser) => { const r = s.chart.timeScale().getVisibleLogicalRange(); return r ? {mid: s.b0 + ((r.from + r.to) / 2 + 0.5) * s.tfSec, span: (r.to - r.from) * s.tfSec} : null; };
const setView = (s: Ser, mid: number, span: number) => s.chart.timeScale().setVisibleLogicalRange({from: (mid - span / 2 - s.b0) / s.tfSec - 0.5, to: (mid + span / 2 - s.b0) / s.tfSec - 0.5});

export type ChartApi = {
  tf: string; bars: (Bar | null)[]; markers: any[]; lines: {k: string; price: number}[]; visible: () => {from: number; to: number} | null;
  coord: (t: number, price: number) => {x: number; y: number} | null; ready: boolean;
  pix: (x: number, y: number, w: number, h: number, rgb: number[] | null, tol: number) => number;      // для клик-теста: сколько точек цвета rgb в прямоугольнике снимка графика
};
export const chartRef: {cur: ChartApi | null} = {cur: null};

export function TradeChart({x, minutes, tf, dark, resetKey, kindLabel}: {x: Trade; minutes: Minutes; tf: string; dark: boolean; resetKey: number; kindLabel: string}) {
  const box = useRef<HTMLDivElement>(null), tipRef = useRef<HTMLDivElement>(null);
  const st = useRef<{chart: IChartApi; candles: ISeriesApi<'Candlestick'>; vol: ISeriesApi<'Histogram'>; link: ISeriesApi<'Line'>; lines: IPriceLine[]; mk: SeriesMarker<Time>[]; bars: (Bar | null)[]; tfSec: number; b0: number; view: {mid: number; span: number} | null; inited: boolean} | null>(null);
  const tfSec = TFS.find(t => t[0] === tf)![2];
  const hold = Math.max(0, (x.exit_ms - x.entry_ms) / 1000), mid = (x.entry_ms + x.exit_ms) / 2000 + GMT4;
  const step = minutes.step, prec = Math.max(0, Math.ceil(-Math.log10(step) - 1e-9));

  // создание графика (раз на сделку)
  useLayoutEffect(() => {
    const el = box.current!;
    const p = palette();
    const chart = createChart(el, {
      width: el.clientWidth || 600, height: el.clientHeight || 360,
      ...chartOptions(p, dark),
      timeScale: {borderColor: p.border, timeVisible: true, secondsVisible: false, rightOffset: 0, fixLeftEdge: true, fixRightEdge: true, minBarSpacing: 0.4,
        tickMarkFormatter: (t: number, type: number) => { const d = utc(t); return type >= 3 ? `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}` : `${pad2(d.getUTCDate())}.${pad2(d.getUTCMonth() + 1)}`; }},
      localization: {timeFormatter: (t: number) => fmtTime(t), priceFormatter: (v: number) => fixed(v, prec)},
      handleScroll: {mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false},
      handleScale: {mouseWheel: true, pinch: true, axisPressedMouseMove: true},
      kineticScroll: {touch: true, mouse: false},
    } as any);
    const pf = {type: 'custom' as const, formatter: (v: number) => fixed(v, prec), minMove: step};
    const extra = [x.lvl, x.entry_px, x.exit_px];
    const candles = chart.addCandlestickSeries({
      upColor: p.card, downColor: p.z600, borderUpColor: p.z600, borderDownColor: p.z600, wickUpColor: p.z600, wickDownColor: p.z600, priceFormat: pf, lastValueVisible: false, priceLineVisible: false,
      autoscaleInfoProvider: (orig: () => any) => {
        const r = orig(); if (!r) return r;
        const vr = chart.timeScale().getVisibleRange(), a = x.entry_ms / 1000 + GMT4, b = x.exit_ms / 1000 + GMT4;
        if (!vr || (+vr.from <= b && +vr.to >= a)) { r.priceRange.minValue = Math.min(r.priceRange.minValue, ...extra); r.priceRange.maxValue = Math.max(r.priceRange.maxValue, ...extra); }
        return r;
      },
    });
    chart.priceScale('right').applyOptions({scaleMargins: {top: 0.07, bottom: 0.2}});
    const vol = chart.addHistogramSeries({priceFormat: {type: 'volume'}, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false});
    chart.priceScale('vol').applyOptions({scaleMargins: {top: 0.84, bottom: 0}});
    const link = chart.addLineSeries({color: p.mfg, lineWidth: 1, lineStyle: LineStyle.Dotted, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false});
    const lines = [
      candles.createPriceLine({price: x.lvl, color: p.accent, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'уровень'}),
      candles.createPriceLine({price: x.entry_px, color: p.mfg, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: 'вход'}),
      candles.createPriceLine({price: x.exit_px, color: p.mfg, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: 'выход (ср.)'}),
    ];
    st.current = {chart, candles, vol, link, lines, mk: [], bars: [], tfSec: 0, b0: 0, view: null, inited: false};
    chart.subscribeCrosshairMove(param => {
      const tip = tipRef.current, s = st.current;
      if (!tip || !s) return;
      if (!param.point || param.time == null || param.point.x < 0 || param.point.y < 0) { tip.style.display = 'none'; return; }
      const k = Math.round((+param.time - s.b0) / s.tfSec), b = s.bars[k];
      const set = (key: string, v: string) => { const n = tip.querySelector(`[data-k="${key}"]`); if (n) n.textContent = v; };
      set('time', fmtTime(+param.time) + ' GMT+4');
      const pr = s.candles.coordinateToPrice(param.point.y); set('px', pr == null ? '—' : fixed(pr as number, prec));
      tip.querySelector('[data-rows]')!.toggleAttribute('hidden', !b);
      tip.querySelector('[data-none]')!.toggleAttribute('hidden', !!b);
      if (b) { set('open', fixed(b.open, prec)); set('high', fixed(b.high, prec)); set('low', fixed(b.low, prec)); set('close', fixed(b.close, prec)); set('q', ints(Math.round(b.q)) + ' $'); }
      tip.style.display = 'block';
      const W = el.clientWidth, H = el.clientHeight, tw = tip.offsetWidth, th = tip.offsetHeight;
      let lx = param.point.x + 14, ly = param.point.y + 14;
      if (lx + tw > W - 60) lx = param.point.x - tw - 14;
      if (ly + th > H - 24) ly = Math.max(4, param.point.y - th - 14);
      tip.style.left = Math.max(4, lx) + 'px'; tip.style.top = Math.max(4, ly) + 'px';
    });
    el.addEventListener('mouseleave', () => { if (tipRef.current) tipRef.current.style.display = 'none'; });
    // размер — по контейнеру; при изменении ширины окно по времени сохраняется (иначе lightweight-charts держит ширину свечи и сдвигает окно)
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth, h = el.clientHeight, s = st.current;
      if (!s || !w || !h || (w === lastW && h === lastH)) return;
      const vr = s.inited ? getView(s) : null;
      lastW = w; lastH = h; s.chart.resize(w, h);
      if (vr) setView(s, vr.mid, vr.span);
    });
    let lastW = el.clientWidth, lastH = el.clientHeight;
    ro.observe(el);
    return () => { ro.disconnect(); chart.remove(); st.current = null; chartRef.cur = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [x.i]);

  // данные: таймфрейм и минутки
  useEffect(() => {
    const s = st.current; if (!s) return;
    const old = s.inited ? getView(s) : null;
    const bars = aggregate(minutes, tfSec);
    const first = bars.findIndex(b => b), b0 = bars.length ? (bars.find(b => b) as Bar).time - first * tfSec : 0;
    s.bars = bars; s.tfSec = tfSec; s.b0 = b0;
    const p = palette();
    const data: any[] = bars.map((b, k) => b ? {time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close} : {time: (b0 + k * tfSec) as UTCTimestamp});
    s.candles.setData(data);
    s.vol.setData(bars.map((b, k) => b ? {time: b.time as UTCTimestamp, value: b.q, color: rgba(b.close >= b.open ? p.z400 : p.mfg, 0.5)} : {time: (b0 + k * tfSec) as UTCTimestamp}) as any);
    const near = (ms: number) => { let k = Math.min(bars.length - 1, Math.max(0, (bucket(ms, tfSec) - b0) / tfSec)); while (k > 0 && !bars[k]) k--; return bars[k] ? bars[k]!.time : null; };
    const te = near(x.entry_ms), tx = near(x.exit_ms);
    s.link.setData(te != null && tx != null && te < tx ? [{time: te as UTCTimestamp, value: x.entry_px}, {time: tx as UTCTimestamp, value: x.exit_px}] : []);
    const mk: SeriesMarker<Time>[] = [];
    if (te != null) mk.push({time: te as UTCTimestamp, position: 'aboveBar', color: p.accent, shape: 'arrowDown', text: 'вход ' + trPx(x.entry_px)});
    if (tx != null) mk.push({time: tx as UTCTimestamp, position: 'belowBar', color: p.fg, shape: 'arrowUp', text: 'выход (ср.) ' + trPx(x.exit_px) + ' · ' + kindLabel});
    mk.sort((a, b) => +a.time - +b.time);
    s.candles.setMarkers(mk); s.mk = mk;
    const bars30 = 30 * tfSec;
    const v = old ? {mid: old.mid, span: Math.max(old.span, bars30)} : {mid, span: Math.max(3 * 3600, hold * 1.8 + 3600, bars30)};
    s.inited = true;
    setView(s, v.mid, v.span);
    const ca = (t: number, price: number) => {
      const cx = s.chart.timeScale().timeToCoordinate(t as UTCTimestamp), cy = s.candles.priceToCoordinate(price);
      return cx == null || cy == null ? null : {x: cx, y: cy};
    };
    chartRef.cur = {tf, bars, markers: mk.map(m => ({time: +m.time, position: m.position, shape: m.shape, text: m.text})), lines: [{k: 'level', price: x.lvl}, {k: 'entry', price: x.entry_px}, {k: 'exit', price: x.exit_px}],
      visible: () => { const r = s.chart.timeScale().getVisibleRange(); return r ? {from: +r.from, to: +r.to} : null; }, coord: ca, ready: true,
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

  // «К сделке»: центр и масштаб как при открытии
  useEffect(() => {
    const s = st.current; if (!s || !resetKey) return;
    const span = Math.max(3 * 3600, hold * 1.8 + 3600, 30 * tfSec);
    setView(s, mid, span);
    s.chart.priceScale('right').applyOptions({autoScale: true});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  // тема
  const themed = useRef(dark);
  useEffect(() => {
    const s = st.current; if (!s || themed.current === dark) return;      // первая отрисовка уже в цветах темы
    themed.current = dark;
    const p = palette();
    s.chart.applyOptions(chartOptions(p, dark) as any);
    s.candles.applyOptions({upColor: p.card, downColor: p.z600, borderUpColor: p.z600, borderDownColor: p.z600, wickUpColor: p.z600, wickDownColor: p.z600});
    s.link.applyOptions({color: p.mfg});
    const vr = getView(s);      // setData сбрасывает окно к правому краю — вернуть
    s.vol.setData(s.bars.map((b, k) => b ? {time: b.time as UTCTimestamp, value: b.q, color: rgba(b.close >= b.open ? p.z400 : p.mfg, 0.5)} : {time: (s.b0 + k * s.tfSec) as UTCTimestamp}) as any);
    if (vr) setView(s, vr.mid, vr.span);
    s.lines[0].applyOptions({color: p.accent}); s.lines[1].applyOptions({color: p.mfg}); s.lines[2].applyOptions({color: p.mfg});
    s.mk = s.mk.map(m => ({...m, color: m.shape === 'arrowDown' ? p.accent : p.fg}));
    s.candles.setMarkers(s.mk);
  }, [dark]);

  return (
    <div className="relative">
      <div ref={box} data-chart="trade" className="h-[360px] w-full overflow-hidden rounded-md border mob:h-[320px]" />
      <div ref={tipRef} data-chart="trade.tip" style={{display: 'none'}} className="pointer-events-none absolute z-10 min-w-[150px] rounded-md border bg-popover px-2.5 py-1.5 text-xs leading-snug text-popover-foreground shadow-md">
        <div data-k="time" className="font-semibold" />
        <div className="flex justify-between gap-4"><span className="text-muted-foreground">цена</span><span data-k="px" /></div>
        <div data-none hidden className="text-muted-foreground">нет сделок</div>
        <div data-rows>
          {[['open', 'открытие'], ['high', 'максимум'], ['low', 'минимум'], ['close', 'закрытие'], ['q', 'оборот']].map(([k, t]) => (
            <div key={k} className="flex justify-between gap-4"><span className="text-muted-foreground">{t}</span><span data-k={k} /></div>
          ))}
        </div>
      </div>
    </div>
  );
}
