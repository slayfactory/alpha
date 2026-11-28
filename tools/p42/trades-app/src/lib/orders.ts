// TK-745: заявки движка и уровни сетапа сделки (журнал TK-743 → файл сделки `ord` / `lv`, формат — tools/p42/tk745_orders.py) → модель для графика.
// Времена в модели — мс эпохи; на график идут как сек + GMT4 (ось GMT+4).
import {trPx} from './fmt';

export type Grp = 'e1' | 'e2' | 'take' | 'stop' | 'rt' | 'other';
export const GRPS: Grp[] = ['e1', 'e2', 'take', 'stop', 'rt', 'other'];
export const GRP_RU: Record<Grp, string> = {e1: 'вход e1', e2: 'второй вход e2', take: 'тейки', stop: 'стоп', rt: 'возврат rt', other: 'прочие выходы'};
export const GRP_TIP: Record<Grp, string> = {
  e1: 'Первый вход (лот 1): рыночная заявка в момент сигнала → исполнена в момент входа.',
  e2: 'Второй вход (лот 2): условная заявка на «уровень − d», выставляется в момент входа лота 1; исполняется, когда цена дошла до дистанции, или снимается.',
  take: 'Лестница тейков (лимитные заявки take1…take8) по каждому лоту; при пересчёте цены — новая цена.',
  stop: 'Стоп-заявка по каждому лоту: выставляется на входе, дальше сдвиги — трейл и остаток после тейков (каждый сдвиг — новый отрезок).',
  rt: 'Возврат за уровень: условная заявка после касания уровня (срок — миллисекунды), исполняется или снимается при выходе.',
  other: 'Прочие правила выхода (nx, tu, tx, trail_limit, конец данных).',
};

export type Seg = {t0: number; t1: number; px: number; why: number};      // t1 = NaN — до выхода сделки
export type Ord = {role: string; grp: Grp; lot: number; type: string; pm: number; kind: 'f' | 'c' | 'o'; end: number; endPx: number; note: string; trig: number; segs: Seg[]};
export type LvKind = 'L' | 'V' | 'Z' | 'T' | 'G' | 'X' | 'O';
export type Lv = {k: LvKind; px: number; a: number | null; b: number | null; x?: any};      // a / b — мс, null — «с начала / до конца данных»

export const grpOf = (role: string): Grp => role === 'e1' || role === 'e2' || role === 'stop' || role === 'rt' ? role : role.startsWith('take') ? 'take' : 'other';

export function decodeOrders(raw: any[] | undefined, t0: number): Ord[] {
  return (raw || []).map(r => {
    const [role, lot, type, pm, kind, end, endPx, note, trig, path] = r as [string, number, string, number, 'f' | 'c' | 'o', number, number, string, number, number[]];
    const segs: Seg[] = [], n = path.length / 3;
    for (let i = 0; i < n; i++) segs.push({t0: t0 + path[3 * i], t1: i + 1 < n ? t0 + path[3 * i + 3] : kind === 'o' ? NaN : t0 + end, px: path[3 * i + 1] / 1e8, why: path[3 * i + 2]});
    return {role, grp: grpOf(role), lot, type, pm, kind, end: kind === 'o' ? NaN : t0 + end, endPx: endPx / 1e8, note, trig: trig ? t0 + trig : 0, segs};
  });
}

export function decodeLevels(raw: any[] | undefined, t0: number): Lv[] {
  const T = (v: number | null) => v == null ? null : t0 + v;
  return (raw || []).map(r => {
    const k = r[0] as LvKind;
    if (k === 'L') return {k, px: r[1] / 1e8, a: T(r[2]), b: T(r[3]), x: {end: r[4], bar: r[5]}};
    if (k === 'V') return {k, px: r[1] / 1e8, a: T(r[2]), b: T(r[3]), x: {active: !!r[4], n: r[5]}};
    if (k === 'Z') return {k, px: r[1] / 1e8, a: T(r[4]), b: T(r[5]), x: {entry: r[2] / 1e8, far: r[3] / 1e8}};
    if (k === 'X') return {k, px: r[1] / 1e8, a: T(r[2]), b: null};
    if (k === 'O') return {k, px: r[1] / 1e8, a: T(r[2]), b: T(r[3]), x: {state: r[4], st: r[5] == null ? null : t0 + r[5], lvl: r[6], end: r[7]}};
    return {k, px: r[1] / 1e8, a: T(r[2]), b: T(r[3])};      // T, G — бар пивота [a, b]
  });
}

// ── русские подписи
export const ROLE_RU = (role: string) => /^take\d+$/.test(role) ? 'тейк ' + role.slice(4) : ({e1: 'вход e1', e2: 'второй вход e2', stop: 'стоп', rt: 'возврат за уровень rt', nx: 'выход nx (до касания следующего уровня)', tu: 'выход tu (до касания)', tx: 'выход tx (по времени после касания)', trail_limit: 'лимитный трейл', exit: 'выход в конце данных'} as Record<string, string>)[role] || role;
export const TYPE_RU: Record<string, string> = {limit: 'лимитная', market: 'рыночная', stop: 'стоп-заявка', cond: 'условная'};
export const END_RU = {f: 'исполнена', c: 'снята', o: 'без конца в журнале'};
export const NOTE_RU: Record<string, string> = {pc: 'позиция закрыта', l1: 'лот 1 закрыт первым', pr: 'цена дошла до уровня раньше дистанции e2', rj: 'отклонена: нет ленты или цена за уровнем', im: 'сразу: вход лота 1 уже внутри дистанции e2',
  lt: 'позже: цена вошла в дистанцию e2', rt: 'возврат за уровень', stop: 'стоп', trail: 'трейл', be: 'безубыток', rest: 'остаток после тейков'};
export const WHY_RU = ['выставлена', 'сдвиг стопа: трейл', 'сдвиг стопа: остаток после тейков', 'пересчёт цены'];
export const noteRu = (c: string) => NOTE_RU[c] || c;
export const LV_END_RU: Record<string, string> = {aged: 'уровень состарился (членов стало меньше n)', pk: 'прокол цены уровня', close_above: 'закрытие бара за уровнем', alive: 'жив до конца данных'};
export const LV_STATE_RU: Record<string, string> = {live: 'жив на сигнале', pk: 'убит проколом (pk)', pb: 'убит сделкой на уровне или за ним (pb)', ban: 'запрет после выхода цены за уровень и возврата (ban)', consumed: 'уже торговался'};

const g4 = (ms: number) => new Date(ms + 4 * 3600e3);
const p2 = (n: number, w = 2) => String(n).padStart(w, '0');
/** дд.мм чч:мм:сс.ммм GMT+4 */
export const fmtMs = (ms: number) => { const d = g4(ms); return `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)} ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}:${p2(d.getUTCSeconds())}.${p2(d.getUTCMilliseconds(), 3)}`; };
export const fmtMin = (ms: number) => { const d = g4(ms); return `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)} ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`; };
export const share = (pm: number) => (pm / 10).toFixed(pm % 10 ? 1 : 0).replace('.', ',') + ' %';

export const lvLabel = (l: Lv): string => {
  switch (l.k) {
    case 'L': return 'уровень ' + trPx(l.px);
    case 'V': return 'версия ' + l.x.n + ' ' + trPx(l.px);
    case 'Z': return 'зона до ' + trPx(l.x.far);
    case 'T': return 'пивот ' + fmtMin(l.a as number) + ' ' + trPx(l.px);
    case 'G': return 'выпал ' + fmtMin(l.a as number) + ' ' + trPx(l.px);
    case 'X': return 'следующий уровень ' + trPx(l.px);
    default: return 'ур. ' + l.x.lvl + ' ' + trPx(l.px) + (l.x.state === 'live' ? '' : ' · ' + l.x.state);
  }
};
export const lvTip = (l: Lv): string[] => {
  switch (l.k) {
    case 'L': return [`торгуемый уровень ${trPx(l.px)}`, `жил с ${l.a == null ? '—' : fmtMin(l.a)} до ${l.b == null ? '—' : fmtMin(l.b)}`, 'конец: ' + (LV_END_RU[l.x.end] || l.x.end), `первый пивот — бар ${l.x.bar}`];
    case 'V': return [`версия цены ${l.x.n}${l.x.active ? ' (активная на сигнале)' : ''}: ${trPx(l.px)}`, `с ${l.a == null ? '—' : fmtMin(l.a)} до ${l.b == null ? '—' : fmtMin(l.b)}`];
    case 'Z': return [`зона: ближний край (цена версии) ${trPx(l.px)}`, `нижний край зоны входа ${trPx(l.x.entry)}`, `дальний край P_far ${trPx(l.x.far)}`];
    case 'T': return [`пивот-член активной версии ${trPx(l.px)}`, `бар ${fmtMin(l.a as number)} … ${fmtMin(l.b as number)} (касание засчитано)`];
    case 'G': return [`выпавший член (старше l суток) ${trPx(l.px)}`, `бар ${fmtMin(l.a as number)}`];
    case 'X': return [`следующий уровень ${trPx(l.px)} (цель тейка tn)`];
    default: return [`уровень ${l.x.lvl} монеты ${trPx(l.px)} на сигнале: ${LV_STATE_RU[l.x.state] || l.x.state}${l.x.st ? ' с ' + fmtMin(l.x.st) : ''}`, `жил с ${l.a == null ? '—' : fmtMin(l.a)} до ${l.b == null ? '—' : fmtMin(l.b)}`, 'конец: ' + (LV_END_RU[l.x.end] || l.x.end)];
  }
};
export const ordTip = (o: Ord, s: Seg, si: number, fill: boolean): string[] => {
  const a = [`${ROLE_RU(o.role)} · ${TYPE_RU[o.type] || o.type} · лот ${o.lot + 1} · ${share(o.pm)} ставки`];
  if (fill) { a.push(`исполнена по ${trPx(o.endPx)} в ${fmtMs(o.end)}${o.note ? ' (' + noteRu(o.note) + ')' : ''}`); if (o.trig) a.push('условие сработало в ' + fmtMs(o.trig)); return a; }
  a.push(`цена ${trPx(s.px)} · ${WHY_RU[s.why] || 'пересчёт'} (отрезок ${si + 1} из ${o.segs.length})`);
  a.push(`с ${fmtMs(s.t0)} до ${Number.isNaN(s.t1) ? 'конца сделки' : fmtMs(s.t1)}`);
  if (si === o.segs.length - 1 && o.kind !== 'f') a.push(`${END_RU[o.kind]}${o.note ? ': ' + noteRu(o.note) : ''}`);
  return a;
};
