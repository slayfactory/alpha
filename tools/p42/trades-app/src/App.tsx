import {useEffect, useMemo, useRef, useState} from 'react';
import {Button} from '@/components/ui/button';
import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table';
import {Tabs, TabsList, TabsTrigger} from '@/components/ui/tabs';
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger} from '@/components/ui/tooltip';
import {BE_TIP, TradeChart, lineColors} from '@/components/TradeChart';
import {ovColors} from '@/components/overlay';
import {GRPS, GRP_RU, GRP_TIP, type Grp} from '@/lib/orders';
import {TFS, decode, type Minutes} from '@/lib/bars';
import {ints, money, num, trDT, trPx} from '@/lib/fmt';
import {cn} from '@/lib/utils';
import {shared, update, useDark, useStore} from '@/store';

// ── вкладка «Сделки» (TK-741 / TK-742): сделки базы шорта без выхода по времени по кластерам сетапа; данные — data/trades/ (tk741_pagedata.py)
const TR_F: [string, string, (v: number) => string][] = [
  ['r15', 'За 15 мин, %', v => num(v, 1, true)], ['r60', 'За час, %', v => num(v, 1, true)], ['r240', 'За 4 ч, %', v => num(v, 1, true)], ['rv60', 'Размах за час, %', v => num(v, 1)],
  ['near', 'У уровня, % минут', v => num(v * 100, 0)], ['touch', 'Касаний за 4 ч', v => String(v)], ['age', 'Возраст уровня, ч', v => num(v, 0)], ['turn', 'Оборот к обычному, ×', v => num(v, 1)],
  ['stab', 'Проколов за час', v => String(v)], ['hi24', 'До максимума суток, %', v => num(v, 1)], ['lo24', 'До минимума суток, %', v => num(v, 1)], ['btc60', 'BTC за час, %', v => num(v, 2, true)]];
const TR_PER: [string, string, string[]][] = [['d30', '30 дней', ['D30']], ['main', 'фев–13.11', ['FJ', 'SEL']], ['JAN', 'январь', ['JAN']], ['N1', '14–24.11', ['N1']], ['all', 'все', ['FJ', 'SEL', 'JAN', 'N1']]];

const SUB = 'шорт, база без выхода по времени · после комиссий · 500 $ на сделку · время GMT+4 · кластеры найдены на этих данных';
type Row = Record<string, any>;
const cellLab = 'mob:before:mb-px mob:before:block mob:before:text-[11.5px] mob:before:font-normal mob:before:text-muted-foreground mob:before:content-[attr(data-lab)]';
const mobRow = 'mob:grid mob:grid-cols-2 mob:gap-x-3 mob:gap-y-1 mob:py-2.5';
const mobCell = 'mob:whitespace-normal mob:p-0 mob:text-left';

function agg(rs: Row[]) {
  const v = rs.map(x => x.pnl), w = v.filter(x => x > 0), l = v.filter(x => x < 0), st = rs.filter(x => x.kind === 'stop').map(x => x.pnl), s = (a: number[]) => a.reduce((x, y) => x + y, 0);
  return {n: v.length, sum: s(v), mean: v.length ? s(v) / v.length : null, rr: w.length && l.length ? (s(w) / w.length) / Math.abs(s(l) / l.length) : null, stops: st.length, lps: st.length ? s(st) / st.length : null};
}

function Summary({idx, rows, cl}: {idx: any; rows: Row[]; cl: number}) {
  const all = agg(rows);
  const line = (cid: number, name: string, sub: string, a: ReturnType<typeof agg>) => (
    <TableRow key={cid} data-cl={cid} className={cn('cursor-pointer', cl === cid && 'sel bg-hi', mobRow)} onClick={() => update({cl: cl === cid ? 0 : cid, open: null})}>
      <TableCell data-f="name" className={cn('whitespace-normal mob:col-span-full', mobCell)}><b>{name}</b>{sub ? <div className="text-xs text-muted-foreground">{sub}</div> : null}</TableCell>
      <TableCell data-f="n" data-lab="Сделок" className={cn('text-right', cellLab, mobCell)}>{ints(a.n)}</TableCell>
      <TableCell data-f="share" data-lab="Доля, %" className={cn('text-right', cellLab, mobCell)}>{rows.length ? num(a.n / rows.length * 100, 0) : '—'}</TableCell>
      <TableCell data-f="sum" data-lab="Сумма, $" className={cn('text-right', cellLab, mobCell)}>{money(a.sum, 2)}</TableCell>
      <TableCell data-f="mean" data-lab="На сделку, $" className={cn('text-right', cellLab, mobCell)}>{a.mean == null ? '—' : money(a.mean, 2)}</TableCell>
      <TableCell data-f="rr" data-lab="R:R" className={cn('text-right', cellLab, mobCell)}>{a.rr == null ? '—' : num(a.rr, 2)}</TableCell>
      <TableCell data-f="stops" data-lab="Стопов" className={cn('text-right', cellLab, mobCell)}>{String(a.stops)}</TableCell>
      <TableCell data-f="lps" data-lab="$ на стоп" className={cn('text-right', cellLab, mobCell)}>{a.lps == null ? '—' : money(a.lps, 2)}</TableCell>
    </TableRow>
  );
  return (
    <Table data-trs="1">
      <TableHeader className="mob:hidden">
        <TableRow className="hover:bg-transparent">
          <TableHead>Кластер</TableHead>
          {['Сделок', 'Доля, %', 'Сумма, $', 'На сделку, $', 'R:R', 'Стопов', '$ на стоп'].map(t => <TableHead key={t} className="whitespace-normal text-right">{t}</TableHead>)}
        </TableRow>
      </TableHeader>
      <TableBody>
        {line(0, 'Все кластеры', '', all)}
        {idx.clusters.map((c: any) => {
          const m = c.med;
          return line(c.id, `К${c.id} · ${c.name}`, `медианы: за час ${num(m.r60, 1, true)} %, оборот ×${num(m.turn, 1)}, уровню ${num(m.age, 0)} ч, до минимума суток ${num(m.lo24, 1)} %`, agg(rows.filter(x => x.cluster === c.id)));
        })}
      </TableBody>
    </Table>
  );
}

function sortVal(idx: any, x: Row, k: string) {
  return k === 'time' ? x.entry_ms : k === 'sym' ? x.sym : k === 'pnl' ? x.pnl : k === 'cl' ? x.cluster : k === 'kind' ? x.kind : k === 'ep' ? x.entry_px : k === 'xp' ? x.exit_px : TR_F.some(f => f[0] === k) ? x.f[idx.feats.indexOf(k)] : x.entry_ms;
}

function TradesTable({idx, rows, open, sk, sd, onSort, onOpen}: {idx: any; rows: Row[]; open: number | null; sk: string; sd: string; onSort: (k: string) => void; onOpen: (i: number) => void}) {
  const cols: [string, string][] = [['time', 'Время (GMT+4)'], ['sym', 'Монета'], ['ep', 'Вход'], ['xp', 'Выход (ср.)'], ['kind', 'Как вышли'], ['pnl', '$'], ['cl', 'Кластер'], ...TR_F.map(f => [f[0], f[1]] as [string, string])];
  const dir = sd === 'a' ? 1 : -1, ix = (k: string) => idx.feats.indexOf(k);
  const sorted = useMemo(() => rows.slice().sort((a, b) => { const x = sortVal(idx, a, sk), y = sortVal(idx, b, sk); return (x < y ? -1 : x > y ? 1 : 0) * dir || a.entry_ms - b.entry_ms; }), [rows, sk, sd, idx]);
  const left = (k: string) => k === 'time' || k === 'sym' || k === 'kind';
  return (
    <Table data-trt="1">
      <TableHeader>
        <TableRow className="hover:bg-transparent mob:flex mob:flex-wrap mob:gap-x-3.5 mob:gap-y-1 mob:pb-2">
          {cols.map(([k, t]) => (
            <TableHead key={k} data-sort={k} onClick={() => onSort(k)} className={cn('cursor-pointer select-none whitespace-normal', left(k) ? '' : 'text-right', 'mob:h-auto mob:p-0')}>{t + (sk === k ? (sd === 'a' ? ' ↑' : ' ↓') : '')}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map(x => (
          <TableRow key={x.i} data-i={x.i} data-cl={x.cluster} className={cn('cursor-pointer', open === x.i && 'sel bg-hi', mobRow)} onClick={() => onOpen(x.i)}>
            <TableCell data-f="time" data-lab="Время (GMT+4)" className={cn(cellLab, mobCell)}>{trDT(x.entry_ms)}</TableCell>
            <TableCell data-f="sym" data-lab="Монета" className={cn(cellLab, mobCell)}>{x.sym.replace(/USDT$/, '')}</TableCell>
            <TableCell data-f="ep" data-lab="Вход" className={cn('text-right', cellLab, mobCell)}>{trPx(x.entry_px)}</TableCell>
            <TableCell data-f="xp" data-lab="Выход" className={cn('text-right', cellLab, mobCell)}>{trPx(x.exit_px)}</TableCell>
            <TableCell data-f="kind" data-lab="Как вышли" className={cn(cellLab, mobCell)}>{idx.kinds[x.kind] || x.kind}</TableCell>
            <TableCell data-f="pnl" data-lab="$" className={cn('text-right', cellLab, mobCell)}>{money(x.pnl, 2)}</TableCell>
            <TableCell data-f="cl" data-lab="Кластер" className={cn('text-right', cellLab, mobCell)}>{x.cluster ? 'К' + x.cluster : '—'}</TableCell>
            {TR_F.map(([k, t, f]) => <TableCell key={k} data-f={k} data-lab={t} className="text-right mob:hidden">{x.f[ix(k)] == null ? '—' : f(x.f[ix(k)])}</TableCell>)}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// минутки сделки: один файл на сделку, подгрузка по клику; запомненные не качаются повторно
const minutesCache = new Map<number, Minutes>();
let TF = '1m';                                           // выбранный таймфрейм переживает смену сделки
async function loadMinutes(i: number, v: number): Promise<Minutes> {
  const hit = minutesCache.get(i);
  if (hit) return hit;
  const r = await fetch(`data/trades/${i}.json?v=${v}`);
  if (!r.ok) throw new Error(`${i}.json: HTTP ${r.status}`);
  const m = decode(await r.json());
  minutesCache.set(i, m);
  return m;
}

// заявки и уровни сетапа (TK-745): видимость групп переживает смену сделки; данные — в файле сделки (`ord`, `lv`)
const SHOW: Record<string, boolean> = {e1: true, e2: true, take: true, stop: true, rt: true, other: true, lv: true};

function OrdLegend({m, dark, show, onToggle}: {m: Minutes | null; dark: boolean; show: Record<string, boolean>; onToggle: (k: string) => void}) {
  if (!m) return null;
  const c = ovColors(dark), n = (g: Grp) => m.ords.filter(o => o.grp === g).length;
  const btn = (k: string, label: string, color: string, count: number, tip: string, dash?: boolean) => (
    <Tooltip key={k}>
      <TooltipTrigger asChild>
        <button data-og={k} data-n={count} aria-pressed={!!show[k]} onClick={() => onToggle(k)}
          className={cn('inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs transition-colors', show[k] ? 'bg-hi text-foreground' : 'text-muted-foreground opacity-60')}>
          <i className="inline-block w-5" style={dash ? {borderTop: `2px dashed ${color}`} : {height: 2, background: color}} />{label} <b>{count}</b>
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-[300px]">{tip}</TooltipContent>
    </Tooltip>);
  return (
    <div data-f="olegend" className="mb-1.5 flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-muted-foreground">заявки:</span>
      {GRPS.filter(g => g !== 'other' || n(g) > 0).map(g => btn(g, GRP_RU[g], c.grp[g], n(g), GRP_TIP[g]))}
      {btn('lv', 'уровни сетапа', c.pivot, m.lvs.length, 'Торгуемый уровень и версии его цены, зона входа, каждый пивот-член (своя цена, от времени пивота), следующий уровень и прочие уровни монеты на сигнале с причиной (жив / убит проколом, сделкой на уровне, запретом / уже торговался).', true)}
    </div>
  );
}

function Legend({x, dark}: {x: Row; dark: boolean}) {
  const c = lineColors(dark), sw = (color: string, dash?: boolean) => <i className="mr-1.5 inline-block w-6 align-middle" style={dash ? {borderTop: `2px dashed ${color}`} : {height: 2, background: color}} />;
  return (
    <div data-f="legend" className="mb-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <span data-lg="entry">{sw(c.entry)}вход <b className="text-foreground">{trPx(x.entry_px)}</b></span>
      <span data-lg="exit">{sw(c.exit)}выход (ср.) <b className="text-foreground">{trPx(x.exit_px)}</b></span>
      <Tooltip>
        <TooltipTrigger asChild><span data-lg="be" title={BE_TIP} tabIndex={0} className="cursor-help">{sw(c.be)}безубыток <b className="text-foreground">{trPx(x.be_px)}</b></span></TooltipTrigger>
        <TooltipContent className="max-w-[300px]">{BE_TIP}</TooltipContent>
      </Tooltip>
      <span data-lg="level">{sw('var(--mfg)', true)}уровень <b className="text-foreground">{trPx(x.lvl)}</b></span>
    </div>
  );
}

function Detail({idx, x, dark}: {idx: any; x: Row; dark: boolean}) {
  const [, bump2] = useState(0), show = SHOW;
  const [tf, setTf] = useState(TF), [reset, setReset] = useState(0), [mins, setMins] = useState<Minutes | null>(null), [err, setErr] = useState('');
  const cl = idx.clusters.find((c: any) => c.id === x.cluster), ix = (k: string) => idx.feats.indexOf(k), kind = idx.kinds[x.kind] || x.kind;
  useEffect(() => {
    let live = true;
    setMins(null); setErr('');
    loadMinutes(x.i, idx.v).then(m => { if (live) setMins(m); }).catch(e => { if (live) setErr(String(e.message || e)); });
    return () => { live = false; };
  }, [x.i]);
  return (
    <Card id="tr-detail" data-i={x.i} className="mt-5">
      <CardHeader>
        <CardTitle data-f="title">{`${x.sym} · ${trDT(x.entry_ms)} GMT+4`}</CardTitle>
        <span data-f="sub" className="text-xs text-muted-foreground">{`вход ${trPx(x.entry_px)} · выход (ср.) ${trPx(x.exit_px)} (${kind}) · ${money(x.pnl, 2)} $ · ${cl ? 'К' + cl.id + ' · ' + cl.name : 'без кластера'} · уровень ${trPx(x.lvl)}`}</span>
        <Tooltip>
          <TooltipTrigger asChild><button className="tr-x ml-auto px-1 text-[22px] leading-none text-muted-foreground hover:text-foreground" aria-label="закрыть" onClick={() => update({open: null})}>×</button></TooltipTrigger>
          <TooltipContent>закрыть</TooltipContent>
        </Tooltip>
      </CardHeader>
      <CardContent>
        <div className="mb-2 flex flex-wrap items-center gap-2" data-f="toolbar">
          <div role="group" aria-label="Таймфрейм" className="inline-flex gap-1" data-tfs="1">
            {TFS.map(([k, t]) => (
              <Button key={k} size="sm" variant={tf === k ? 'default' : 'outline'} data-tf={k} aria-pressed={tf === k} onClick={() => { TF = k; setTf(k); }}>{t}</Button>
            ))}
          </div>
          <Tooltip>
            <TooltipTrigger asChild><Button size="sm" variant="outline" data-act="center" onClick={() => setReset(r => r + 1)}>К сделке</Button></TooltipTrigger>
            <TooltipContent>вернуть масштаб и центр на сделку</TooltipContent>
          </Tooltip>
        </div>
        <Legend x={x} dark={dark} />
        <OrdLegend m={mins} dark={dark} show={show} onToggle={k => { SHOW[k] = !SHOW[k]; bump2(v => v + 1); }} />
        {mins ? <TradeChart key={x.i} x={x as any} minutes={mins} tf={tf} dark={dark} resetKey={reset} kindLabel={kind} show={{...show}} />
          : <div className="my-4 text-[13px] text-muted-foreground" data-f="chart">{err ? 'минутки не загрузились: ' + err : 'загрузка минуток…'}</div>}
        <div className="mt-1.5 text-xs text-muted-foreground">Выход — средняя цена всех частей выхода (тейки и остаток) по итоговому результату; отметка стоит на минуте последнего выхода. Свечи собираются из минут; график — TradingView Lightweight Charts™. {BE_TIP} Внизу — NATR30, %: на минуте стоит значение закрытой предыдущей минуты (как у движка), на старших таймфреймах — последней минуты свечи; точка и линия «сигнал» — значение на минуте сигнала.</div>
        <div className="mt-3 text-xs text-muted-foreground">Картина до сигнала (только прошлое, минуты до входа)</div>
        <div className="tr-feats mt-1.5 grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-x-4 gap-y-2 mob:grid-cols-2" data-f="setup">
          {TR_F.map(([k, t, f]) => <div key={k} data-f={k} className="flex flex-col"><span className="text-xs text-muted-foreground">{t}</span><b>{x.f[ix(k)] == null ? '—' : f(x.f[ix(k)])}</b></div>)}
        </div>
      </CardContent>
    </Card>
  );
}

export function App() {
  useStore();
  const S = shared.state, dark = useDark();
  const [idx, setIdx] = useState<any>(null), [err, setErr] = useState(''), [sk, setSk] = useState('time'), [sd, setSd] = useState('d');
  useEffect(() => {
    fetch('data/trades/index.json', {cache: 'no-cache'}).then(r => { if (!r.ok) throw new Error('index.json: HTTP ' + r.status); return r.json(); }).then(setIdx).catch(e => setErr(String(e.message || e)));
  }, []);
  const rows: Row[] = useMemo(() => idx ? idx.trades.map((a: any[]) => { const R: Row = {}; idx.cols.forEach((c: string, k: number) => { R[c] = a[k]; }); return R; }) : [], [idx]);
  const wasOpen = useRef<number | null>(null);
  useEffect(() => {
    if (S.open != null && S.open !== wasOpen.current) requestAnimationFrame(() => document.getElementById('tr-detail')?.scrollIntoView({block: 'start', behavior: 'auto'}));
    wasOpen.current = S.open;
  });
  if (!idx) return <div className="my-4 text-[13px] text-muted-foreground"><div className="flex flex-wrap items-baseline justify-between gap-x-2.5 gap-y-1"><h2 className="text-xl font-bold tracking-tight text-foreground">Сделки</h2><span className="text-xs">{SUB}</span></div><div className="mt-4">{err ? 'не загрузилось: ' + err : 'загрузка…'}</div></div>;
  if (S.per === 'd30' && !rows.some(r => r.per === 'D30')) S.per = 'main';      // нет прогона «30 дней» — прежний набор
  const per = rows.filter(x => (TR_PER.find(p => p[0] === S.per) as any)[2].includes(x.per)), view = S.cl ? per.filter(x => x.cluster === S.cl) : per;
  if (S.open != null && !rows.some(r => r.i === S.open)) S.open = null;
  const open = S.open != null ? rows.find(r => r.i === S.open) : null;
  return (
    <TooltipProvider delayDuration={250}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2.5 gap-y-1">
        <h2 className="text-xl font-bold tracking-tight">Сделки</h2>
        <span id="tr-sub" className="text-xs text-muted-foreground">{SUB}</span>
      </div>
      <Tabs value={S.per} onValueChange={k => update({per: k, open: null})} className="my-3">
        <TabsList id="tr-per" aria-label="Период">
          {TR_PER.map(([k, t, set]) => <TabsTrigger key={k} value={k} data-per={k}>{`${t} · ${rows.filter(x => set.includes(x.per)).length}`}</TabsTrigger>)}
        </TabsList>
      </Tabs>
      <Card className="mt-5"><CardContent><Summary idx={idx} rows={per} cl={S.cl} /></CardContent></Card>
      <div id="tr-detail-box">{open ? <Detail key={open.i} idx={idx} x={open} dark={dark} /> : null}</div>
      <Card className="mt-5">
        <CardHeader>
          <CardTitle>{S.cl ? `К${S.cl} · ${idx.clusters.find((c: any) => c.id === S.cl).name}` : 'Все сделки'}</CardTitle>
          <span id="tr-cnt" className="text-xs text-muted-foreground">{`${ints(view.length)} сделок`}</span>
        </CardHeader>
        <CardContent>
          <TradesTable idx={idx} rows={view} open={S.open} sk={sk} sd={sd}
            onSort={k => { if (sk === k) setSd(sd === 'a' ? 'd' : 'a'); else { setSk(k); setSd(k === 'sym' || k === 'kind' ? 'a' : 'd'); } }}
            onOpen={i => update({open: i})} />
        </CardContent>
      </Card>
    </TooltipProvider>
  );
}
