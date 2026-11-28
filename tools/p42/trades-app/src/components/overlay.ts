// TK-745: все заявки сделки и все уровни сетапа — примитив серии свечей (lightweight-charts 4.2): рисуется на холсте графика по времени в мс, не по свечам.
// Заявка = отрезки по цене от выставления до исполнения / снятия (пересчёт цены — новый отрезок), отметка исполнения — круг размером по доле ставки; уровни сетапа — линии со времени рождения / пивота.
import type {ISeriesPrimitivePaneRenderer, ISeriesPrimitivePaneView, ISeriesApi, ISeriesPrimitive, Logical, SeriesAttachedParameter, Time} from 'lightweight-charts';
import {GMT4} from '@/lib/bars';
import {trPx} from '@/lib/fmt';
import {GRPS, lvLabel, lvTip, ordTip, type Grp, type Lv, type Ord} from '@/lib/orders';

export type OvColors = {grp: Record<Grp, string>; setup: string; pivot: string; card: string; fg: string};
export const ovColors = (dark: boolean): OvColors => dark
  ? {grp: {e1: '#22d3ee', e2: '#f472b6', take: '#4ade80', stop: '#f87171', rt: '#facc15', other: '#94a3b8'}, setup: '#94a3b8', pivot: '#2dd4bf', card: '', fg: ''}
  : {grp: {e1: '#0891b2', e2: '#db2777', take: '#16a34a', stop: '#dc2626', rt: '#ca8a04', other: '#64748b'}, setup: '#64748b', pivot: '#0f766e', card: '', fg: ''};

export type OvData = {ords: Ord[]; lvs: Lv[]; show: Record<string, boolean>; b0: number; tfSec: number; exitMs: number; colors: OvColors};
export type Drawn = {seg: Record<string, number>; fill: Record<string, number>; cancel: Record<string, number>; lv: Record<string, number>; frames: number};
export type Hit = {title: string[]; d: number};

const rgba = (hex: string, a: number) => { const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex); return m ? `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${a})` : hex; };
const R_FILL = (pm: number) => 3 + 5 * Math.sqrt(Math.min(1, pm / 500));      // радиус отметки исполнения: доля ставки 500 ‰ = 8 px, тейк 31 ‰ ≈ 4,2 px
const MINW = 5;                                                                  // минимальная длина отрезка на экране (рыночные заявки живут миллисекунды)

type Seg2 = {o: Ord; si: number; x0: number; x1: number; y: number};
type Fill2 = {o: Ord; x: number; y: number; r: number};
type Line2 = {l: Lv; x0: number; x1: number; y: number; y2?: number; y3?: number};
type Lay = {W: number; H: number; segs: Seg2[]; fills: Fill2[]; ends: Fill2[]; lines: Line2[]};

export class Overlay implements ISeriesPrimitive<Time> {
  data: OvData | null = null;
  drawn: Drawn = {seg: {}, fill: {}, cancel: {}, lv: {}, frames: 0};
  private p: SeriesAttachedParameter<Time> | null = null;
  private series: ISeriesApi<'Candlestick'> | null = null;
  private W = 0;
  private H = 0;
  private views: ISeriesPrimitivePaneView[];
  constructor() {
    const mk = (z: 'bottom' | 'normal', f: (ctx: CanvasRenderingContext2D) => void): ISeriesPrimitivePaneView => ({zOrder: () => z, renderer: (): ISeriesPrimitivePaneRenderer => ({draw: (t: any) => t.useMediaCoordinateSpace((s: any) => { this.W = s.mediaSize.width; this.H = s.mediaSize.height; f(s.context); })})});
    this.views = [mk('bottom', c => this.paintBands(c)), mk('normal', c => this.paintMain(c))];
  }
  attached(p: SeriesAttachedParameter<Time>) { this.p = p; this.series = p.series as ISeriesApi<'Candlestick'>; }
  detached() { this.p = null; this.series = null; }
  updateAllViews() {}
  paneViews() { return this.views; }
  set(d: OvData | null) { this.data = d; this.p?.requestUpdate(); }

  /** x по времени в мс. `logicalToCoordinate` (lightweight-charts 4.2.3) даёт 0 для нецелого индекса — берём два соседних целых и интерполируем */
  private x(ms: number | null, edge: number): number {
    const d = this.data!, ts = this.p!.chart.timeScale();
    if (ms == null) return edge;
    const lg = (ms / 1000 + GMT4 - d.b0) / d.tfSec - 0.5, k = Math.floor(lg);
    const a = ts.logicalToCoordinate(k as Logical), b = ts.logicalToCoordinate((k + 1) as Logical);
    return a == null || b == null ? edge : (a as number) + (lg - k) * ((b as number) - (a as number));
  }
  private y(px: number): number | null { const c = this.series!.priceToCoordinate(px); return c == null ? null : (c as number); }

  /** x (пиксели области цены) для времени в мс эпохи — для клик-теста; null — вне данных */
  xms(ms: number): number | null { return this.data && this.p ? this.x(ms, NaN) : null; }

  /** все цены видимых групп — для автомасштаба цены */
  prices(): number[] {
    const d = this.data; if (!d) return [];
    const out: number[] = [];
    for (const o of d.ords) if (d.show[o.grp]) { for (const s of o.segs) out.push(s.px); if (o.kind === 'f') out.push(o.endPx); }
    if (d.show.lv) for (const l of d.lvs) { if (l.k === 'L' || l.k === 'T') out.push(l.px); else if (l.k === 'Z') out.push(l.px, l.x.entry, l.x.far); }
    return out.filter(v => Number.isFinite(v) && v > 0);
  }

  private layout(): Lay | null {
    const d = this.data; if (!d || !this.p || !this.series || !this.W) return null;
    const W = this.W, segs: Seg2[] = [], fills: Fill2[] = [], ends: Fill2[] = [], lines: Line2[] = [];
    for (const o of d.ords) {
      if (!d.show[o.grp]) continue;
      o.segs.forEach((s, si) => {
        const y = this.y(s.px); if (y == null) return;
        const x0 = this.x(s.t0, 0), x1 = Math.max(this.x(Number.isNaN(s.t1) ? d.exitMs : s.t1, W), x0 + MINW);
        segs.push({o, si, x0, x1, y});
      });
      if (o.kind === 'f' || o.kind === 'c') {
        const y = this.y(o.kind === 'f' ? o.endPx : o.segs[o.segs.length - 1].px);
        if (y != null) (o.kind === 'f' ? fills : ends).push({o, x: this.x(o.end, 0), y, r: o.kind === 'f' ? R_FILL(o.fpm) : 3});
      }
    }
    if (d.show.lv) {
      const lend = (d.lvs.find(l => l.k === 'L') || {b: null}).b;
      for (const l of d.lvs) {
        const y = this.y(l.px); if (y == null) continue;
        let a = l.a, b = l.b;
        if (l.k === 'T' || l.k === 'G') { const t = (b as number); a = (a as number); b = l.k === 'T' ? lend : t; }      // пивот: от бара пивота до конца уровня
        const x0 = this.x(a, -4), x1 = this.x(b, W + 4);
        lines.push({l, x0, x1, y, y2: l.k === 'Z' ? this.y(l.x.entry) ?? undefined : undefined, y3: l.k === 'Z' ? this.y(l.x.far) ?? undefined : undefined});
      }
    }
    return {W, H: this.H, segs, fills, ends, lines};
  }

  private paintBands(c: CanvasRenderingContext2D) {
    const L = this.layout(); if (!L) return;
    const col = this.data!.colors;
    for (const n of L.lines) if (n.l.k === 'Z' && n.y3 != null) {
      c.fillStyle = rgba(col.setup, 0.13); c.fillRect(n.x0, Math.min(n.y, n.y3), Math.max(0, n.x1 - n.x0), Math.abs(n.y3 - n.y));
      if (n.y2 != null) { c.strokeStyle = rgba(col.setup, 0.7); c.lineWidth = 1; c.setLineDash([2, 3]); c.beginPath(); c.moveTo(n.x0, n.y2); c.lineTo(n.x1, n.y2); c.stroke(); c.setLineDash([]); }
    }
  }

  private paintMain(c: CanvasRenderingContext2D) {
    const L = this.layout(), d = this.data;
    const dr: Drawn = {seg: {}, fill: {}, cancel: {}, lv: {}, frames: this.drawn.frames + 1};
    if (!L || !d) { this.drawn = dr; return; }
    const col = d.colors, inc = (m: Record<string, number>, k: string) => { m[k] = (m[k] || 0) + 1; };
    c.save(); c.beginPath(); c.rect(0, 0, L.W, L.H); c.clip();
    // уровни сетапа (под заявками)
    const labels: {y: number; x: number; text: string; color: string}[] = [];
    for (const n of L.lines) {
      const k = n.l.k; inc(dr.lv, k);
      if (k === 'Z') continue;
      const color = k === 'T' || k === 'G' ? col.pivot : col.setup;
      c.strokeStyle = k === 'L' ? col.setup : rgba(color, k === 'O' ? 0.45 : 0.9);      // торгуемый уровень — цветом прежней линии «уровень» (та же цена, не перекрашивает её)
      c.lineWidth = k === 'L' ? 2 : 1;
      c.setLineDash(k === 'L' ? [7, 3] : k === 'T' ? [1, 3] : k === 'G' ? [4, 3] : k === 'X' ? [1, 4] : k === 'V' ? [3, 3] : [2, 2]);
      c.beginPath(); c.moveTo(n.x0, n.y); c.lineTo(n.x1, n.y); c.stroke(); c.setLineDash([]);
      if (k === 'T' || k === 'G') {      // пивот — ромб в середине бара пивота
        const px = this.x(((n.l.a as number) + (n.l.b as number)) / 2, n.x0);
        c.fillStyle = color; c.beginPath(); c.moveTo(px, n.y - 4.5); c.lineTo(px + 4.5, n.y); c.lineTo(px, n.y + 4.5); c.lineTo(px - 4.5, n.y); c.closePath(); c.fill();
      }
      if (n.x1 > 30 && n.x0 < L.W) labels.push({y: n.y, x: Math.min(n.x1, L.W - 4), text: lvLabel(n.l), color: k === 'L' ? col.fg : color});
    }
    // заявки
    const segsBy = new Map<Ord, Seg2[]>();
    for (const s of L.segs) { inc(dr.seg, s.o.grp); (segsBy.get(s.o) || segsBy.set(s.o, []).get(s.o)!).push(s); }
    for (const [o, ss] of segsBy) {
      const color = col.grp[o.grp];
      ss.forEach((s, i) => {
        c.strokeStyle = color; c.lineWidth = o.kind === 'f' ? 1.8 : 1.2;
        c.setLineDash(o.kind === 'f' || i + 1 < ss.length ? [] : o.kind === 'c' ? [5, 3] : [2, 3]);
        c.beginPath(); c.moveTo(s.x0, s.y); c.lineTo(s.x1, s.y); c.stroke(); c.setLineDash([]);
        if (i + 1 < ss.length) {      // пересчёт цены: тонкая вертикаль к следующему отрезку
          const n = ss[i + 1]; c.strokeStyle = rgba(color, 0.4); c.lineWidth = 1; c.beginPath(); c.moveTo(s.x1, s.y); c.lineTo(n.x0, n.y); c.stroke();
        }
      });
    }
    for (const e of L.ends) {      // снятие — короткая засечка
      inc(dr.cancel, e.o.grp); c.strokeStyle = col.grp[e.o.grp]; c.lineWidth = 1.2; c.beginPath(); c.moveTo(e.x, e.y - 4); c.lineTo(e.x, e.y + 4); c.stroke();
    }
    // исполнение — круг, размер по доле исполненного; второй вход e2 — кольцо вокруг (e1 и e2 исполняются в секундах друг от друга у одной цены), мелкие сверху
    const fills = L.fills.slice().sort((a, b) => (a.o.grp === 'e2' ? 0 : 1) - (b.o.grp === 'e2' ? 0 : 1) || b.r - a.r);
    for (const f of fills) {
      inc(dr.fill, f.o.grp); const color = col.grp[f.o.grp];
      c.beginPath();
      if (f.o.grp === 'e2') { c.fillStyle = rgba(color, 0.15); c.strokeStyle = color; c.lineWidth = 2.5; c.arc(f.x, f.y, f.r + 2.5, 0, Math.PI * 2); c.fill(); c.stroke(); }
      else { c.fillStyle = color; c.strokeStyle = col.card; c.lineWidth = 1.5; c.arc(f.x, f.y, f.r, 0, Math.PI * 2); c.fill(); c.stroke(); }
    }
    // подписи уровней: справа, без наложения по высоте
    labels.sort((a, b) => a.y - b.y);
    c.font = '10.5px Inter, ui-sans-serif, system-ui, sans-serif'; c.textAlign = 'right'; c.textBaseline = 'middle'; c.lineJoin = 'round';
    let last = -99;
    for (const t of labels) {
      const y = Math.max(t.y - 7, last + 11); last = y;
      c.strokeStyle = col.card; c.lineWidth = 3; c.strokeText(t.text, t.x, y); c.fillStyle = t.color; c.fillText(t.text, t.x, y);
    }
    c.restore();
    this.drawn = dr;
  }

  /** что под курсором (x, y — пиксели области цены): заявки, отметки исполнения, уровни сетапа */
  hit(px: number, py: number): Hit[] {
    const L = this.layout(), out: Hit[] = [];
    if (!L) return out;
    for (const f of L.fills) { const dd = Math.hypot(px - f.x, py - f.y); if (dd <= f.r + 3) out.push({title: ordTip(f.o, f.o.segs[f.o.segs.length - 1], f.o.segs.length - 1, true), d: dd - 6}); }
    for (const s of L.segs) { const dy = Math.abs(py - s.y); if (dy <= 5 && px >= s.x0 - 3 && px <= s.x1 + 3) out.push({title: ordTip(s.o, s.o.segs[s.si], s.si, false), d: dy}); }
    for (const n of L.lines) {
      if (n.l.k === 'Z') { if (n.y3 != null && px >= n.x0 && px <= n.x1 && py >= Math.min(n.y, n.y3) - 3 && py <= Math.max(n.y, n.y3) + 3) out.push({title: lvTip(n.l), d: 8}); continue; }
      const dy = Math.abs(py - n.y); if (dy <= 4 && px >= n.x0 - 3 && px <= n.x1 + 3) out.push({title: lvTip(n.l), d: dy + 2});
    }
    return out.sort((a, b) => a.d - b.d);
  }

  /** счёт по данным (видимые группы) — для клик-теста: заявки, отрезки, исполнения, снятия по группам и уровни по видам */
  counts() {
    const d = this.data, o = {ord: {} as Record<string, number>, seg: {} as Record<string, number>, fill: {} as Record<string, number>, cancel: {} as Record<string, number>, lv: {} as Record<string, number>};
    if (!d) return o;
    const inc = (m: Record<string, number>, k: string, n = 1) => { m[k] = (m[k] || 0) + n; };
    for (const g of GRPS) { o.ord[g] = 0; o.seg[g] = 0; o.fill[g] = 0; o.cancel[g] = 0; }
    for (const r of d.ords) { inc(o.ord, r.grp); inc(o.seg, r.grp, r.segs.length); if (r.kind === 'f') inc(o.fill, r.grp); else if (r.kind === 'c') inc(o.cancel, r.grp); }
    for (const l of d.lvs) inc(o.lv, l.k);
    return o;
  }
  pxLabel(v: number) { return trPx(v); }
}
