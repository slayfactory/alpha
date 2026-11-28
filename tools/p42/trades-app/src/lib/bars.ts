// Минутки сделки: data/trades/<№>.json (tk741_pagedata.py, pack_bars) → свечи любого таймфрейма (сбор на клиенте из минут).
// Время на графике — секунды UTC + 4 ч, чтобы оси lightweight-charts (они показывают UTC) показывали GMT+4.
export const GMT4 = 4 * 3600;

export type Raw = {v: number; n: number; tick: number; o: number[]; h: number[]; l: number[]; c: number[]; q: number[]; g: number[]; t0: number};
export type Bar = {time: number; open: number; high: number; low: number; close: number; q: number};          // q — оборот, $
export type Minutes = {t0: number; step: number; bars: (Bar | null)[]};

export const TFS: [string, string, number][] = [['1m', '1м', 60], ['5m', '5м', 300], ['15m', '15м', 900], ['1h', '1ч', 3600], ['4h', '4ч', 14400]];

/** Целые столбцы → минутные свечи (null — минута без сделок). Цена = шаги × tick / 1e8, оборот = центы / 100. */
export function decode(j: Raw): Minutes {
  const gap = new Set(j.g), step = j.tick / 1e8, bars: (Bar | null)[] = new Array(j.n);
  let prev = 0;
  for (let k = 0; k < j.n; k++) {
    if (gap.has(k)) { bars[k] = null; continue; }
    const o = prev + j.o[k], c = o + j.c[k], hi = Math.max(o, c) + j.h[k], lo = Math.min(o, c) - j.l[k];
    prev = c;
    bars[k] = {time: j.t0 / 1000 + k * 60 + GMT4, open: o * step, high: hi * step, low: lo * step, close: c * step, q: j.q[k] / 100};
  }
  return {t0: j.t0, step, bars};
}

/** Сбор минут в свечи таймфрейма tf (сек): бакеты по часам GMT+4 (сдвиг кратен 4 ч, поэтому границы 5м…4ч те же, что по UTC). Пустые бакеты — null. */
export function aggregate(m: Minutes, tf: number): (Bar | null)[] {
  const first = m.bars.find(b => b) as Bar | undefined;
  if (!first) return [];
  const t0 = m.t0 / 1000 + GMT4, b0 = Math.floor(t0 / tf) * tf, bN = Math.floor((t0 + (m.bars.length - 1) * 60) / tf) * tf;
  const out: (Bar | null)[] = new Array((bN - b0) / tf + 1).fill(null);
  for (const b of m.bars) {
    if (!b) continue;
    const k = (Math.floor(b.time / tf) * tf - b0) / tf, a = out[k];
    if (!a) out[k] = {time: b0 + k * tf, open: b.open, high: b.high, low: b.low, close: b.close, q: b.q};
    else { a.high = Math.max(a.high, b.high); a.low = Math.min(a.low, b.low); a.close = b.close; a.q += b.q; }
  }
  return out;
}

export const bucket = (ms: number, tf: number) => Math.floor((ms / 1000 + GMT4) / tf) * tf;
