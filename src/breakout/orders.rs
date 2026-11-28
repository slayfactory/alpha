//! TK-743: журнал заявок сделки движка пробоев (`breakout tick --keep-orders`) и уровни сетапа сделки — данные для графиков :8090.
//!
//! Журнал ведёт сам движок из состояния заявок (`Pos`, `sim_tail_e`, `run_tick_xb`), а не пересчитывает их по параметрам: событие пишется там, где
//! движок ставит заявку, двигает её цену, исполняет или снимает. Ключ выключен — вектор событий пуст (не выделяет память), счёт и `trades.csv`
//! те же байт в байт. Включён — событие дописывается в вектор сделки (один на лот), файл пишется при закрытии прогона.
//!
//! Файл `<side>-orders.csv`: одна строка — одно событие заявки; заявка = `(sym, level, sig_ms, lot, oid)`.

use super::data::{Sym, MIN_MS, SCALE};
use super::levels::{build_levels_tracked, Level};
use super::params::{Params, SigCfg};
use super::tick::{candidates_group_tr, mul_div, zone_at, TSig, TickTrade};
use super::trade::NOTIONAL_U;
use super::Side;
use std::collections::HashMap;
use std::fmt::Write as _;

/// Событие заявки: поставлена / сдвинута (новая цена) / исполнена (полностью) / снята.
pub const EV_PLACE: u8 = 0;
pub const EV_REPRICE: u8 = 1;
pub const EV_FILL: u8 = 2;
pub const EV_CANCEL: u8 = 3;

/// Вид заявки: лимитная; рыночная; стоп (рынок по срабатыванию цены); условный выход по правилу (рынок по условию).
pub const TY_LIMIT: u8 = 0;
pub const TY_MARKET: u8 = 1;
pub const TY_STOP: u8 = 2;
pub const TY_COND: u8 = 3;

/// Номера заявок внутри лота: тейки лестницы — 0…n−1; остальные — ниже.
pub const OID_STOP: u8 = 100;
pub const OID_NX: u8 = 101;
pub const OID_TU: u8 = 102;
pub const OID_RT: u8 = 103;
pub const OID_TX: u8 = 104;
pub const OID_TRL: u8 = 105;
pub const OID_EXIT: u8 = 110;
pub const OID_E1: u8 = 120;
pub const OID_E2: u8 = 121;

/// Одно событие заявки; `px` — настоящая цена ×1e8 (0 — цены нет), `u` — размер, мкУЕ, `aux` — второе время, мс (срок / срабатывание; 0 — нет).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OrdEv {
    pub ms: i64,
    pub px: i64,
    pub u: i64,
    pub aux: i64,
    pub oid: u8,
    pub ev: u8,
    pub typ: u8,
    pub lot: u8,
    pub note: &'static str,
}

impl OrdEv {
    pub fn new(ms: i64, ev: u8, oid: u8, typ: u8, px: i64, u: i64, note: &'static str) -> OrdEv {
        OrdEv {
            ms,
            px,
            u,
            aux: 0,
            oid,
            ev,
            typ,
            lot: 0,
            note,
        }
    }
}

/// Событие переписывается, если предыдущее событие той же заявки того же вида случилось в ту же мс (трейл двигается на каждой сделке — в журнал идёт последнее значение мс).
pub fn push_reprice(v: &mut Vec<OrdEv>, e: OrdEv) {
    if let Some(l) = v.last_mut() {
        if l.oid == e.oid && l.ev == EV_REPRICE && l.ms == e.ms {
            *l = e;
            return;
        }
    }
    v.push(e);
}

/// Снимает все заявки, которые к концу списка ещё стоят (последнее событие — постановка или сдвиг), в момент `ms`.
pub fn cancel_open(v: &mut Vec<OrdEv>, ms: i64, note: &'static str) {
    let mut last: [Option<OrdEv>; 256] = [None; 256];
    for e in v.iter() {
        last[usize::from(e.oid)] = Some(*e);
    }
    for e in last.into_iter().flatten() {
        if e.ev == EV_PLACE || e.ev == EV_REPRICE {
            v.push(OrdEv {
                ms,
                ev: EV_CANCEL,
                note,
                aux: 0,
                ..e
            });
        }
    }
}

fn role_name(oid: u8) -> String {
    match oid {
        OID_STOP => "stop".into(),
        OID_NX => "nx".into(),
        OID_TU => "tu".into(),
        OID_RT => "rt".into(),
        OID_TX => "tx".into(),
        OID_TRL => "trail_limit".into(),
        OID_EXIT => "exit".into(),
        OID_E1 => "e1".into(),
        OID_E2 => "e2".into(),
        i => format!("take{}", u32::from(i) + 1),
    }
}

fn type_name(t: u8) -> &'static str {
    match t {
        TY_LIMIT => "limit",
        TY_MARKET => "market",
        TY_STOP => "stop",
        _ => "cond",
    }
}

fn ev_name(e: u8) -> &'static str {
    match e {
        EV_PLACE => "place",
        EV_REPRICE => "reprice",
        EV_FILL => "fill",
        _ => "cancel",
    }
}

pub const ORDERS_HEADER: &str =
    "sym,level,sig_ms,lot,oid,role,type,event,ms,px_e8,size_u,share_pm,aux_ms,note\n";

/// Строки журнала заявок сделки `x` (события отсортированы по времени; при равном — как записаны).
pub fn write_orders_rows(out: &mut String, syms: &[Sym], x: &TickTrade) {
    let mut ev = x.ord.clone();
    ev.sort_by_key(|e| e.ms);
    for e in &ev {
        let _ = writeln!(
            out,
            "{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
            syms[x.sym as usize].name,
            x.level,
            x.sig_ms,
            e.lot,
            e.oid,
            role_name(e.oid),
            type_name(e.typ),
            ev_name(e.ev),
            e.ms,
            e.px,
            e.u,
            e.u * 1000 / NOTIONAL_U,
            e.aux,
            e.note
        );
    }
}

// ───────────────────────────── уровни сетапа сделки ─────────────────────────────

pub const SETUP_HEADER: &str = "sym,level,sig_ms,role,px_e8,from_ms,to_ms,reason\n";

/// Минута → мс от 01.09 00:00 UTC; `u32::MAX` — «до конца данных» (пустая ячейка).
fn min_ms(m: u32) -> String {
    if m == u32::MAX {
        String::new()
    } else {
        (i64::from(m) * MIN_MS).to_string()
    }
}

fn why_name(w: u8) -> &'static str {
    match w {
        1 => "aged",
        2 => "pk",
        3 => "close_above",
        4 => "inner_left",
        _ => "alive",
    }
}

/// TK-747 (В-197): проколы цены `px` (длинная рама, `s` — сторона) минутами `from..to`: (число минут с максимумом рамы строго за `px`, наибольший заход ×1e8). Минуты без данных пропускаются.
fn wicks(sym: &Sym, s: i64, px: i64, from: usize, to: usize) -> (usize, i64) {
    let mut n = 0usize;
    let mut wick = 0i64;
    for m in from..to.min(sym.o.len()) {
        if sym.o[m] == 0 {
            continue;
        }
        let hi = if s > 0 { sym.h[m] } else { -sym.l[m] };
        if hi > px {
            n += 1;
            wick = wick.max(hi - px);
        }
    }
    (n, wick)
}

/// Уровни сетапа сделки `x`: торгуемый уровень с версиями цены, зона (ближний край — цена версии, нижний край зоны входа, дальний край `P_far`),
/// члены версии (пивоты-касания) и выпавшие члены, следующий уровень (`tn`), прочие живые уровни монеты на минуте сигнала с их состоянием
/// (живой / убит `pk`, `pb`, `ban` с минутой / уже торговался). Данные — из `Level` движка (журнал членов, версии цены, причина конца) и из прохода
/// состояний уровней `candidates_group_tr`; цены — настоящие ×1e8.
pub struct SetupCtx<'a> {
    syms: &'a [Sym],
    side: Side,
    p: &'a Params,
    levels: HashMap<u16, Vec<Level>>,
    kills: HashMap<u16, Vec<(u32, u32, &'static str)>>,
    sigs: HashMap<(u16, u32, i64), &'a TSig>,
}

impl<'a> SetupCtx<'a> {
    pub fn new(syms: &'a [Sym], side: Side, p: &'a Params, sigs: &'a [TSig]) -> SetupCtx<'a> {
        SetupCtx {
            syms,
            side,
            p,
            levels: HashMap::new(),
            kills: HashMap::new(),
            sigs: sigs.iter().map(|s| ((s.sym, s.level, s.ms), s)).collect(),
        }
    }

    /// Строки уровней сделки `i` из `trades` (в порядке обработки; ранние сделки той же монеты — «уже торговался»).
    pub fn rows(&mut self, out: &mut String, trades: &[TickTrade], i: usize) {
        let x = &trades[i];
        let Some(&sg) = self.sigs.get(&(x.sym, x.level, x.sig_ms)) else {
            return;
        };
        let sym = &self.syms[x.sym as usize];
        let (side, p) = (self.side, self.p);
        let s = side.s();
        let levels = self
            .levels
            .entry(x.sym)
            .or_insert_with(|| build_levels_tracked(sym, &p.sig.lv, side, true));
        let kills = self.kills.entry(x.sym).or_insert_with(|| {
            let mut tr = Vec::new();
            let cfg: &SigCfg = &p.sig;
            let mut outs = [Vec::new()];
            candidates_group_tr(
                sym,
                levels,
                &[cfg],
                side,
                &mut outs,
                &mut [[0; 4]],
                Some(&mut tr),
            );
            tr
        });
        let key = |r: &str, px: i64, from: String, to: String, why: String| {
            format!(
                "{},{},{},{r},{px},{from},{to},{why}\n",
                sym.name, x.level, x.sig_ms
            )
        };
        let lv = &levels[x.level as usize];
        let t = sg.t as usize;
        let si = lv.segs.iter().rposition(|(m, _)| *m <= sg.t).unwrap_or(0);
        let ver_to = |k: usize| -> u32 { lv.segs.get(k + 1).map_or(lv.end, |v| v.0) };
        // торгуемый уровень и версии цены
        let _ = out.write_str(&key(
            "level",
            lv.segs[si].1,
            min_ms(lv.born),
            min_ms(lv.end),
            format!(
                "traded;first_bar={};end={}",
                lv.first_bar,
                if lv.end == u32::MAX {
                    "alive"
                } else {
                    why_name(lv.why)
                }
            ),
        ));
        for (k, (m, px)) in lv.segs.iter().enumerate() {
            let _ = out.write_str(&key(
                "ver",
                *px,
                min_ms(*m),
                min_ms(ver_to(k)),
                format!("v{}{}", k + 1, if k == si { ";active" } else { "" }),
            ));
        }
        // зона: ближний край — цена версии, нижний край зоны входа, дальний край P_far
        let (v_from, v_to) = (min_ms(lv.segs[si].0), min_ms(ver_to(si)));
        let _ = out.write_str(&key(
            "zone_near",
            s * sg.lp,
            v_from.clone(),
            v_to.clone(),
            "level price of the active version".into(),
        ));
        if let Some(dmax) = zone_at(sym, &p.sig, t) {
            let edge = sg.lp - mul_div(sg.lp.abs(), dmax, SCALE) as i64;
            let _ = out.write_str(&key(
                "zone_entry",
                s * edge,
                v_from.clone(),
                v_to.clone(),
                format!(
                    "entry zone lower edge; dmax_e8={dmax}; signal dist_e8={}",
                    sg.dist
                ),
            ));
        }
        let _ = out.write_str(&key(
            "zone_far",
            s * sg.far,
            v_from,
            v_to,
            "P_far: far edge of the zone (min of member prices)".into(),
        ));
        // члены активной версии — касания, давшие уровень; выпавшие члены прежних версий
        let tf_ms = p.sig.lv.tf_min as i64 * MIN_MS;
        let cur: &[(u32, i64)] = lv.mem.get(si).map_or(&[], Vec::as_slice);
        for (bar, px) in cur {
            let a = i64::from(*bar) * tf_ms;
            let _ = out.write_str(&key(
                "touch",
                *px,
                a.to_string(),
                (a + tf_ms).to_string(),
                format!(
                    "pivot bar {bar} of tf {}m; member of the active version",
                    p.sig.lv.tf_min
                ),
            ));
        }
        // TK-747 (`lm`, `pp`, В-197): основной уровень — любой прокол убивает (здесь — заходы в пределах допуска с версии цены до сигнала); внутренние уровни каскада (прочие члены версии, дальше основного
        // по ходу пробоя) колоться могут — число минут с заходом и наибольший заход от конца бара пивота до сигнала; из каскада внутренний уровень выходит только закрытием часового бара за ним (`close_above`)
        if p.sig.lv.pp {
            let (n, wick) = wicks(sym, s, s * lv.segs[si].1, lv.segs[si].0 as usize, t);
            let _ = out.write_str(&key(
                "main_pierce",
                lv.segs[si].1,
                min_ms(lv.segs[si].0),
                min_ms(sg.t),
                format!("pierce_minutes={n};max_wick_e8={wick}"),
            ));
        }
        if p.sig.lv.lm != 0 {
            for (bar, px) in cur.iter().filter(|(_, px)| *px != lv.segs[si].1) {
                let end_min = (*bar as usize + 1) * p.sig.lv.tf_min;
                let (n, wick) = wicks(sym, s, s * px, end_min, t);
                let a = i64::from(*bar) * tf_ms;
                let _ = out.write_str(&key(
                    "inner",
                    *px,
                    a.to_string(),
                    (a + tf_ms).to_string(),
                    format!("inner level of the cascade; pierce_minutes={n};max_wick_e8={wick}"),
                ));
            }
        }
        let mut gone: Vec<(u32, i64, usize)> = Vec::new();
        for (k, m) in lv.mem.iter().enumerate().take(si + 1) {
            for e in m {
                if !cur.contains(e) {
                    if let Some(g) = gone.iter_mut().find(|g| (g.0, g.1) == *e) {
                        g.2 = k;
                    } else {
                        gone.push((e.0, e.1, k));
                    }
                }
            }
        }
        for (bar, px, last) in gone {
            let a = i64::from(bar) * tf_ms;
            let _ = out.write_str(&key(
                "touch_gone",
                px,
                a.to_string(),
                min_ms(ver_to(last)),
                format!("pivot bar {bar}; dropped from the level (older than l days)"),
            ));
        }
        if sg.nxt != 0 {
            let _ = out.write_str(&key(
                "next",
                s * sg.nxt,
                x.sig_ms.to_string(),
                String::new(),
                "next level beyond (tn take target)".into(),
            ));
        }
        // прочие живые уровни монеты на минуте сигнала
        let traded: HashMap<u32, i64> = trades[..i]
            .iter()
            .filter(|y| y.sym == x.sym)
            .map(|y| (y.level, y.entry_ms))
            .collect();
        for (j, o) in levels.iter().enumerate() {
            if j == x.level as usize || !o.alive_at(t) {
                continue;
            }
            let state = if let Some(ms) = traded.get(&(j as u32)) {
                format!("consumed@{ms}")
            } else if let Some((_, m, why)) = kills
                .iter()
                .find(|(l, m, _)| *l == j as u32 && (*m as usize) < t)
            {
                format!("{why}@{}", i64::from(*m) * MIN_MS)
            } else {
                "live".into()
            };
            let _ = out.write_str(&key(
                "other",
                o.price_at(t),
                min_ms(o.born),
                min_ms(o.end),
                format!(
                    "{state};level={j};end={}",
                    if o.end == u32::MAX {
                        "alive"
                    } else {
                        why_name(o.why)
                    }
                ),
            ));
        }
    }
}
