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
    return c ? {tf: c.tf, bars: c.bars, markers: c.markers, lines: c.lines, visible: c.visible(), ready: c.ready} : null;
  },
  coord(t: number, price: number) { return chartRef.cur ? chartRef.cur.coord(t, price) : null; },
  pix(x: number, y: number, w: number, h: number, rgb: number[] | null, tol: number) { return chartRef.cur ? chartRef.cur.pix(x, y, w, h, rgb, tol) : -1; },
};
