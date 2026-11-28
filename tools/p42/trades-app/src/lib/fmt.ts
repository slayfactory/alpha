// Формат чисел — как на странице (запятая, минус «−», пробел в тысячах); время — GMT+4.
export const MINUS = '−';
export function num(v: number | null | undefined, d = 3, sign = false): string {
  if (v == null || v !== v) return '—';
  const s = Math.abs(v).toFixed(d).replace('.', ',');
  const z = +s.replace(',', '.') === 0;
  return (v < 0 && !z ? MINUS : (sign && !z ? '+' : '')) + s;
}
export const ints = (n: number) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
export const money = (v: number, d = 2) => num(v, d, true);
export const pad2 = (n: number) => String(n).padStart(2, '0');
const g4 = (ms: number) => new Date(ms + 4 * 3600e3);
export const trDT = (ms: number) => { const d = g4(ms); return `${pad2(d.getUTCDate())}.${pad2(d.getUTCMonth() + 1)} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
export const trPx = (v: number | null) => { if (v == null) return '—'; const a = Math.abs(v), d = a >= 100 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 7; return num(v, d); };
