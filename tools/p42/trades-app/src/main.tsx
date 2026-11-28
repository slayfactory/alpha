import {createRoot, type Root} from 'react-dom/client';
import {App} from './App';
import {bump, shared} from './store';
import {chartRef} from './components/TradeChart';
import './styles.css';

// Мост со страницей (tk741_trades.js): mount(элемент #trades-root, TR, setHash) — один раз; sync() — после смены хеша.
let root: Root | null = null;
(window as any).P42Trades = {
  mount(el: HTMLElement, state: any, onChange: () => void) {
    shared.state = state; shared.onChange = onChange;
    root = createRoot(el);
    root.render(<App />);
  },
  sync() { bump(); },
  // для клик-теста: что сейчас на графике (свечи таймфрейма, отметки, линии, видимый диапазон, координаты точки)
  debug() {
    const c = chartRef.cur;
    return c ? {tf: c.tf, bars: c.bars, markers: c.markers, lines: c.lines, visible: c.visible(), ready: c.ready, nat: {values: c.nat.values, sig: c.nat.sig, cross: c.nat.cross(), visible: c.nat.visible(), box: c.nat.box()}, ov: c.ov.counts(), drawn: c.ov.drawn(), ovPrices: c.ov.prices()} : null;
  },
  ovHit(px: number, py: number) { return chartRef.cur ? chartRef.cur.ov.hit(px, py) : null; },
  ovX(ms: number) { return chartRef.cur ? chartRef.cur.ov.xms(ms) : null; },
  ovCoord(t: number, price: number) { return chartRef.cur ? chartRef.cur.ov.coord(t, price) : null; },
  coord(t: number, price: number) { return chartRef.cur ? chartRef.cur.coord(t, price) : null; },
  pix(x: number, y: number, w: number, h: number, rgb: number[] | null, tol: number) { return chartRef.cur ? chartRef.cur.pix(x, y, w, h, rgb, tol) : -1; },
  natPix(x: number, y: number, w: number, h: number, rgb: number[] | null, tol: number) { return chartRef.cur ? chartRef.cur.nat.pix(x, y, w, h, rgb, tol) : -1; },
};
