//! Часовые бары → пивоты → уровни (П-42 §5.1). Только закрытые бары прошлого, без заглядывания.
//!
//! Работаем в «длинной раме»: для шорта цены берутся с обратным знаком (`s = -1`), тогда потолок и пол —
//! один код. Пивот-максимум ширины `k`: максимум бара `h` строго больше максимумов `k` баров слева и справа;
//! он известен только после закрытия бара `h + k`. Глубина `d`: после пивота какой-то закрытый бар ушёл от него
//! на ≥ `d` %, прежде чем закрытие бара оказалось за пивотом (иначе пивот отброшен). Отскок «засчитан» на
//! закрытии бара `max(h + k, бар глубины)`.
//!
//! Уровень: ≥ `N` засчитанных пивотов одного типа в окне `L` суток, все в пределах `τ` % от своего среднего;
//! цена уровня — среднее. Группа строится от нового пивота: ближайшие по цене добавляются, пока условие `τ` держится.
//! Новый пивот присоединяется к живому уровню, если условие держится с ним; члены старше `L` выпадают, при
//! `< N` членов уровень кончается. Уровень жив, пока ни один закрытый бар (после первого пивота) не закрылся за
//! ним; такой бар — истинный пробой, уровень израсходован.

use super::data::{Bars, Sym, N_MIN, SCALE};
use super::params::LevelCfg;
use super::Side;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

/// Счётчики опции `pk` (§13.11, диагностика; считаются только в обычной сборке уровней, не в журнале членов): группы,
/// не родившиеся из-за прокола/закрытия за ценой с первого пивота, и уровни, убитые перепроверкой при смене версии.
pub static PK_BIRTH_BLOCKED: AtomicU64 = AtomicU64::new(0);
pub static PK_RETRO_KILLED: AtomicU64 = AtomicU64::new(0);
/// TK-747 (`lz`): сколько пар отменено заколом (диагностика, как `PK_*`).
pub static LZ_CANCELLED: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone)]
pub struct Level {
    /// Первая минута, когда уровень известен (после закрытия бара, давшего N-й отскок).
    pub born: u32,
    /// Первая минута, когда уровня уже нет (`u32::MAX` — до конца данных).
    pub end: u32,
    /// Версии цены: (с какой минуты, цена уровня); цена меняется, когда присоединяется пивот или выпадает член.
    pub segs: Vec<(u32, i64)>,
    pub first_bar: u32,
    /// Только при `build_levels_tracked(.., true)`: для каждой версии цены — члены уровня (бар, цена ×1e8); иначе пусто.
    pub mem: Vec<Vec<(u32, i64)>>,
    /// Источник уровня (TK-668): 0 — пивоты; иначе значение `LevelCfg::lx` (1 `D`, 2 `W`, 3 `P`). Уровень источника «свежий» по построению: `fresh_ok` его пропускает.
    pub src: u8,
    /// TK-743: чем кончился уровень пивотов (`end`): 0 — не кончился или источник / линия; 1 — членов стало меньше `n` (старше `l` суток); 2 — прокол `pk`; 3 — закрытие бара за уровнем.
    pub why: u8,
    /// TK-747 (`fw`): для каждой версии цены (параллельно `segs`) — минута постановки уровня: подтверждение последнего присоединившегося пивота версии (новый пивот, «заметивший» старые уровни).
    /// Пусто у уровней источника и линий — для них не определено.
    pub fresh: Vec<u32>,
}

#[derive(Clone, Copy)]
struct Pv {
    bar: u32,
    /// Значение в длинной раме (шорт — с обратным знаком).
    p: i64,
    active: u32,
}

struct Frame<'a> {
    b: &'a Bars,
    s: i64,
}

impl Frame<'_> {
    fn hi(&self, i: usize) -> i64 {
        if self.s > 0 {
            self.b.h[i]
        } else {
            -self.b.l[i]
        }
    }
    fn lo(&self, i: usize) -> i64 {
        if self.s > 0 {
            self.b.l[i]
        } else {
            -self.b.h[i]
        }
    }
    fn cl(&self, i: usize) -> i64 {
        self.s * self.b.c[i]
    }
}

/// (все члены в пределах τ от цены уровня, цена уровня в длинной раме). `lm = 0` — цена уровня среднее членов, τ от среднего; TK-747 `lm = 1` (В-197) — среднего нет:
/// цена уровня — БЛИЖНИЙ к цене пивот сетапа (рама: наименьшее значение — у шорта самый высокий из минимумов, у лонга самый низкий из максимумов), τ от его цены (прочие члены дальше него по ходу пробоя).
fn group_ok(ps: &[i64], tau_pm: i64, lm: u8) -> (bool, i64) {
    let px = if lm == 0 {
        let n = ps.len() as i128;
        let sum: i128 = ps.iter().map(|&x| i128::from(x)).sum();
        (sum / n) as i64
    } else {
        ps.iter().copied().min().unwrap_or(0)
    };
    let ok = ps.iter().all(|&x| {
        i128::from((x - px).abs()) * 100_000 <= i128::from(tau_pm) * i128::from(px.abs())
    });
    (ok, px)
}

/// `pk` (§13.11): среди закрытых баров `from..=to` есть бар с high за ценой `mean` (длинная рама) больше чем на τ или —
/// после бара `from` — с закрытием за ценой `mean`. Часовой high — максимум минутных, поэтому равно поминутной проверке.
fn pk_violated(f: &Frame, bars: &Bars, from: usize, to: usize, mean: i64, tau_pm: i64) -> bool {
    let lim = mean + (i128::from(mean.abs()) * i128::from(tau_pm) / 100_000) as i64;
    (from..=to).any(|bb| bars.ok[bb] && (f.hi(bb) > lim || (bb > from && f.cl(bb) > mean)))
}

fn find_pivots(f: &Frame, cfg: &LevelCfg) -> Vec<Pv> {
    let nb = f.b.ok.len();
    let k = cfg.k;
    let mut out = Vec::new();
    if nb < 2 * k + 1 {
        return out;
    }
    'outer: for h in k..nb - k {
        for j in h - k..=h + k {
            if !f.b.ok[j] {
                continue 'outer;
            }
        }
        let ph = f.hi(h);
        for j in 1..=k {
            if f.hi(h - j) >= ph || f.hi(h + j) >= ph {
                continue 'outer;
            }
        }
        let thr = ph - (i128::from(ph.abs()) * i128::from(cfg.d_pm) / 100_000) as i64;
        let mut j = h + 1;
        while j < nb {
            if f.b.ok[j] {
                if f.cl(j) > ph {
                    break;
                }
                if f.lo(j) <= thr {
                    out.push(Pv {
                        bar: h as u32,
                        p: ph,
                        active: j.max(h + k) as u32,
                    });
                    break;
                }
            }
            j += 1;
        }
    }
    out.sort_by_key(|p| (p.active, p.bar));
    out
}

struct Live {
    id: usize,
    members: Vec<Pv>,
    /// Цена основного уровня версии в длинной раме (`lm ≠ 0`): ближний непроколотый пивот; при `lm = 0` — среднее группы.
    main: i64,
}

/// TK-747 (`lm`, В-197): статусы пивота — (минута первого прокола, минута выхода из каскада). Прокол: минута после подтверждения пивота (закрытие бара `active`) с максимумом рамы дальше
/// цены пивота + допуск (`pierce_tol`: 1 шаг цены ≤ 0,05 %, иначе 0). Выход: закрытие часового бара после подтверждения за ценой пивота (+ допуск при `pp`) — минута закрытия этого бара.
/// `u32::MAX` — не наступило. Бары самих пивотов (до подтверждения) не считаются — члены каскада друг друга «проколами» не убивают.
struct Pstat<'a> {
    f: &'a Frame<'a>,
    sym: &'a Sym,
    bars: &'a Bars,
    tfm: usize,
    tick: i64,
    pp: bool,
    memo: HashMap<(u32, i64), (u32, u32)>,
}

impl Pstat<'_> {
    fn minute_hi(&self, m: usize) -> i64 {
        if self.f.s > 0 {
            self.sym.h[m]
        } else {
            -self.sym.l[m]
        }
    }

    fn get(&mut self, p: &Pv) -> (u32, u32) {
        if let Some(&v) = self.memo.get(&(p.bar, p.p)) {
            return v;
        }
        let tol = pierce_tol(self.tick, p.p);
        let hi_thr = p.p + tol;
        let cl_thr = p.p + if self.pp { tol } else { 0 };
        let (mut pierce, mut left) = (u32::MAX, u32::MAX);
        for bb in p.active as usize + 1..self.bars.ok.len() {
            if !self.bars.ok[bb] {
                continue;
            }
            if pierce == u32::MAX && self.f.hi(bb) > hi_thr {
                let from = bb * self.tfm;
                pierce = (from..from + self.tfm)
                    .find(|&m| self.sym.o[m] != 0 && self.minute_hi(m) > hi_thr)
                    .unwrap_or(from) as u32;
            }
            if left == u32::MAX && self.f.cl(bb) > cl_thr {
                left = ((bb + 1) * self.tfm) as u32;
            }
            if pierce != u32::MAX && left != u32::MAX {
                break;
            }
        }
        self.memo.insert((p.bar, p.p), (pierce, left));
        (pierce, left)
    }

    /// (все члены в пределах τ от цены основного уровня, цена основного уровня в раме): основной — ближний (наименьшее значение рамы) непроколотый к минуте `now` член; все прокололи — группы нет.
    fn grp(&mut self, tau_pm: i64, ms: &[Pv], now: u32) -> (bool, i64) {
        let mut px: Option<i64> = None;
        for p in ms {
            if self.get(p).0 >= now {
                px = Some(px.map_or(p.p, |x| x.min(p.p)));
            }
        }
        let Some(px) = px else {
            return (false, 0);
        };
        let ok = ms.iter().all(|x| {
            i128::from((x.p - px).abs()) * 100_000 <= i128::from(tau_pm) * i128::from(px.abs())
        });
        (ok, px)
    }
}

fn prices(m: &[Pv]) -> Vec<i64> {
    m.iter().map(|p| p.p).collect()
}

/// TK-747 (`lz`, закол, В-194, В-196, В-197): сетап ровно из двух пивотов, где второй — допускающий (новый) пивот `pv`, позже первого по бару и дальше него по ходу пробоя больше чем на допуск
/// прокола (`pierce_tol(tick, ..)`: 1 шаг цены ≤ 0,05 %, иначе 0), — «второй перебивает первый», уровень не рождается. Каскад (≥ 3 пивота) не отменяется — перебития в нём нормальны.
fn is_zakol(grp: &[Pv], pv: Pv, tick: i64) -> bool {
    let [a, b] = grp else {
        return false;
    };
    let first = if a.bar == pv.bar && a.p == pv.p { b } else { a };
    first.bar < pv.bar && pv.p > first.p + pierce_tol(tick, first.p)
}

/// TK-747 (`fw`): минута постановки версии цены с составом `m` и баром `tfm` минут — подтверждение пивота с наибольшим `active` (последний присоединившийся).
fn fresh_of(m: &[Pv], tfm: i64) -> u32 {
    m.iter()
        .max_by_key(|p| (p.active, p.bar))
        .map_or(0, |last| ((i64::from(last.active) + 1) * tfm) as u32)
}

/// TK-747 (`kill_from`, В-197): бар, с которого действуют убийцы уровня «закрытие за ценой» / перепроверка: при `lm = 0` — первый пивот группы; при `lm = 1` — пивот, давший цену уровня
/// (ближний; при равных ценах — более ранний): до него этой цены ещё не было, закрытия прежних баров её не убивают.
fn kill_from(m: &[Pv], lm: u8) -> usize {
    if lm == 0 {
        return m.iter().map(|p| p.bar as usize).min().unwrap_or(0);
    }
    m.iter()
        .min_by_key(|p| (p.p, p.bar))
        .map_or(0, |p| p.bar as usize)
}

/// TK-747 (`pp`, В-197): допуск на прокол — 1 шаг цены, если он не больше 0,05 % цены `px` (в тысячных процента — `PP_TOL_MAX_PM`), иначе 0. `tick` — шаг цены монеты ×1e8 (`Sym::tick`).
pub fn pierce_tol(tick: i64, px: i64) -> i64 {
    if tick > 0 && i128::from(tick) * 100_000 <= i128::from(PP_TOL_MAX_PM) * i128::from(px.abs()) {
        tick
    } else {
        0
    }
}

/// Допуск прокола `pp`: 0,05 % цены в тысячных процента.
pub const PP_TOL_MAX_PM: i64 = 50;

pub fn build_levels(sym: &Sym, cfg: &LevelCfg, side: Side) -> Vec<Level> {
    build_levels_tracked(sym, cfg, side, false)
}

/// Как `build_levels`, но с журналом членов (`Level::mem`) и со счётчиками `pk` (§13.19: дальний край зоны `P_far` для стопа от уровня).
pub fn build_levels_far(sym: &Sym, cfg: &LevelCfg, side: Side) -> Vec<Level> {
    build_levels_impl(sym, cfg, side, true, true)
}

impl Level {
    /// Уровень известен и жив на минуте `t`: `born ≤ t < end` (как цикл `tick::candidates_fg`).
    pub fn alive_at(&self, t: usize) -> bool {
        self.born as usize <= t && t < self.end as usize
    }

    /// Цена версии на минуте `t` (настоящая, не рама): последняя версия с началом ≤ `t`, до первой — первая (как переход версий в `tick::candidates_fg`).
    pub fn price_at(&self, t: usize) -> i64 {
        let k = self.segs.partition_point(|&(from, _)| from as usize <= t);
        self.segs[k.saturating_sub(1)].1
    }

    /// TK-747 (`fw`): минута постановки уровня версии цены `seg`; у уровней источника и линий — минута рождения.
    pub fn fresh_at(&self, seg: usize) -> usize {
        self.fresh
            .get(seg)
            .map_or(self.born as usize, |&s| s as usize)
    }

    /// Дальний край зоны версии цены `seg` в длинной раме: минимум цен пивотов (лонг — самый низкий, шорт — минус самый высокий).
    /// Без журнала членов — `None`.
    pub fn far_edge(&self, seg: usize, s: i64) -> Option<i64> {
        let m = self.mem.get(seg)?;
        if s > 0 {
            m.iter().map(|x| x.1).min()
        } else {
            m.iter().map(|x| -x.1).min()
        }
    }
}

/// TK-697 (`tn`): цена ближайшего уровня набора `levels` строго за ценой `lp` (длинная рама, `s` — сторона) по ходу сделки на минуте `t`; 0 — такого уровня нет.
/// Берутся только уровни, известные и живые на минуте `t` (`born ≤ t < end`), цена — версия минуты `t`.
pub fn next_px(levels: &[Level], s: i64, lp: i64, t: usize) -> i64 {
    levels
        .iter()
        .filter(|lv| lv.alive_at(t))
        .map(|lv| s * lv.price_at(t))
        .filter(|&p| p > lp)
        .min()
        .unwrap_or(0)
}

fn members(m: &[Pv]) -> Vec<(u32, i64)> {
    m.iter().map(|p| (p.bar, p.p.abs())).collect()
}

/// `track` — вести журнал членов уровня (`Level::mem`); цены, рождение и конец уровней от него не зависят.
pub fn build_levels_tracked(sym: &Sym, cfg: &LevelCfg, side: Side, track: bool) -> Vec<Level> {
    build_levels_impl(sym, cfg, side, track, !track)
}

/// `count` — считать счётчики `pk` (диагностика): обычная сборка — да, журнал для графиков — нет (не удваиваем).
fn build_levels_impl(
    sym: &Sym,
    cfg: &LevelCfg,
    side: Side,
    track: bool,
    count: bool,
) -> Vec<Level> {
    if cfg.lx == 4 {
        return build_line_levels(sym, cfg, side);
    }
    if cfg.lx != 0 {
        return build_source_levels(sym, cfg, side);
    }
    let Some(bars) = sym.bars_for(cfg.tf_min) else {
        return Vec::new();
    };
    let f = Frame {
        b: bars,
        s: side.s(),
    };
    let pivs = find_pivots(&f, cfg);
    // TK-747 (`lm`, В-197): модель «основной / внутренние уровни каскада» — статусы пивотов (прокол, выход по часовому закрытию) по цене и минутам монеты
    let owner = cfg.lm != 0;
    let tick = if cfg.pp || cfg.lz || owner {
        sym.tick()
    } else {
        0
    };
    // TK-747 (`pp`): закрытие бара за ценой уровня убивает его, если зашло дальше допуска (1 шаг цены ≤ 0,05 %); без `pp` — любое закрытие за ценой
    let kill_px = |px: i64| {
        if cfg.pp {
            px + pierce_tol(tick, px)
        } else {
            px
        }
    };
    let nb = bars.ok.len();
    let tfm = cfg.tf_min as i64;
    let lwin = cfg.l_days as i64 * 1440;
    let mut st = Pstat {
        f: &f,
        sym,
        bars,
        tfm: cfg.tf_min,
        tick,
        pp: cfg.pp,
        memo: HashMap::new(),
    };
    let mut levels: Vec<Level> = Vec::new();
    let mut live: Vec<Live> = Vec::new();
    let mut free: Vec<Pv> = Vec::new();
    let mut pi = 0;
    for b in 0..nb {
        let now = (b as i64 + 1) * tfm;
        let now_u = now as u32;
        // `li`: свободные пивоты стареют от подтверждения (7 суток до самого позднего члена группы); без `li` — от бара экстремума, как раньше
        if cfg.li {
            free.retain(|p| (i64::from(p.active) + 1) * tfm >= now - lwin);
        } else {
            free.retain(|p| i64::from(p.bar) * tfm >= now - lwin);
        }
        let mut li = 0;
        while li < live.len() {
            let id = live[li].id;
            if owner {
                // TK-747 (`lm`, В-197): основной / внутренние уровни каскада. `li` (целостность сетапа): пивоты не стареют по неделе от старшего — сетап отменяется на минуте
                // «подтверждение последнего присоединившегося пивота + `l` суток» (дальше только присоединение пивота продлевает срок); без `li` — прежнее старение каждого члена по `l` суток от его бара
                let set = i64::from(levels[id].fresh.last().copied().unwrap_or(0));
                if cfg.li && now >= set + lwin {
                    levels[id].end = (set + lwin) as u32;
                    levels[id].why = 1;
                    live.remove(li);
                    continue;
                }
                let main = live[li].main;
                let (mut pierced, mut left) = (true, true);
                for p in live[li].members.iter().filter(|p| p.p == main) {
                    let (pm, lm_) = st.get(p);
                    pierced &= pm < now_u;
                    left &= lm_ < now_u;
                }
                let why = if pierced {
                    2
                } else if left {
                    3
                } else {
                    0
                };
                if why != 0 {
                    levels[id].end = now_u;
                    levels[id].why = why;
                    live.remove(li);
                    continue;
                }
                let before = live[li].members.len();
                if !cfg.li {
                    live[li]
                        .members
                        .retain(|p| i64::from(p.bar) * tfm >= now - lwin);
                    if live[li].members.len() < cfg.n {
                        levels[id].end = now_u;
                        levels[id].why = 1;
                        live.remove(li);
                        continue;
                    }
                }
                live[li].members.retain(|p| st.get(p).1 >= now_u);
                if live[li].members.len() != before {
                    if live[li].members.len() < cfg.n {
                        levels[id].end = now_u;
                        levels[id].why = 4;
                        live.remove(li);
                        continue;
                    }
                    let (ok, mean) = st.grp(cfg.tau_pm, &live[li].members, now_u);
                    if !ok {
                        levels[id].end = now_u;
                        levels[id].why = 4;
                        live.remove(li);
                        continue;
                    }
                    live[li].main = mean;
                    levels[id].segs.push((now_u, mean.abs()));
                    let prev = levels[id].fresh.last().copied().unwrap_or(0);
                    levels[id].fresh.push(prev);
                    if track {
                        levels[id].mem.push(members(&live[li].members));
                    }
                }
                li += 1;
                continue;
            }
            let before = live[li].members.len();
            live[li]
                .members
                .retain(|p| i64::from(p.bar) * tfm >= now - lwin);
            if live[li].members.len() < cfg.n {
                levels[id].end = now as u32;
                levels[id].why = 1;
                live.remove(li);
                continue;
            }
            let (_, mean) = group_ok(&prices(&live[li].members), cfg.tau_pm, cfg.lm);
            if live[li].members.len() != before {
                levels[id].segs.push((now as u32, mean.abs()));
                let prev = levels[id].fresh.last().copied().unwrap_or(0);
                levels[id].fresh.push(prev);
                if track {
                    levels[id].mem.push(members(&live[li].members));
                }
                if cfg.pk {
                    let from = kill_from(&live[li].members, cfg.lm);
                    if pk_violated(&f, bars, from, b, mean, cfg.tau_pm) {
                        if count {
                            PK_RETRO_KILLED.fetch_add(1, Ordering::Relaxed);
                        }
                        levels[id].end = now as u32;
                        levels[id].why = 2;
                        live.remove(li);
                        continue;
                    }
                }
            }
            if bars.ok[b] && f.cl(b) > kill_px(mean) {
                levels[id].end = now as u32;
                levels[id].why = 3;
                live.remove(li);
                continue;
            }
            li += 1;
        }
        while pi < pivs.len() && pivs[pi].active as usize == b {
            let pv = pivs[pi];
            pi += 1;
            let mut joined = false;
            for li in 0..live.len() {
                let (ok, mean) = if owner {
                    let mut cand = live[li].members.clone();
                    cand.push(pv);
                    st.grp(cfg.tau_pm, &cand, now_u)
                } else {
                    let mut cand = prices(&live[li].members);
                    cand.push(pv.p);
                    group_ok(&cand, cfg.tau_pm, cfg.lm)
                };
                if ok {
                    let id = live[li].id;
                    live[li].members.push(pv);
                    live[li].main = mean;
                    levels[id].segs.push((now as u32, mean.abs()));
                    levels[id].fresh.push(fresh_of(&live[li].members, tfm));
                    if track {
                        levels[id].mem.push(members(&live[li].members));
                    }
                    joined = true;
                    if cfg.pk {
                        let from = kill_from(&live[li].members, cfg.lm);
                        if pk_violated(&f, bars, from, b, mean, cfg.tau_pm) {
                            if count {
                                PK_RETRO_KILLED.fetch_add(1, Ordering::Relaxed);
                            }
                            levels[id].end = now as u32;
                            levels[id].why = 2;
                            live.remove(li);
                        }
                    }
                    break;
                }
            }
            if joined {
                continue;
            }
            free.push(pv);
            let mut order: Vec<Pv> = free
                .iter()
                .copied()
                .filter(|q| !(q.bar == pv.bar && q.p == pv.p))
                .collect();
            if owner {
                order.retain(|q| st.get(q).1 >= now_u);
            }
            order.sort_by_key(|q| ((q.p - pv.p).abs(), std::cmp::Reverse(q.bar)));
            let mut grp = vec![pv];
            for q in order {
                let okq = if owner {
                    let mut cand = grp.clone();
                    cand.push(q);
                    st.grp(cfg.tau_pm, &cand, now_u).0
                } else {
                    let mut cand = prices(&grp);
                    cand.push(q.p);
                    group_ok(&cand, cfg.tau_pm, cfg.lm).0
                };
                if okq {
                    grp.push(q);
                } else {
                    break;
                }
            }
            if grp.len() < cfg.n {
                continue;
            }
            // TK-747 (`lz`): закол — пара, где допускающий (новый) пивот перебил ранний больше чем на допуск, уровень не рождается; пивоты остаются свободными (с третьим дадут каскад)
            if cfg.lz && is_zakol(&grp, pv, tick) {
                if count {
                    LZ_CANCELLED.fetch_add(1, Ordering::Relaxed);
                }
                continue;
            }
            let mean = if owner {
                st.grp(cfg.tau_pm, &grp, now_u).1
            } else {
                group_ok(&prices(&grp), cfg.tau_pm, cfg.lm).1
            };
            let first_bar = grp.iter().map(|p| p.bar).min().unwrap_or(pv.bar);
            let from_bar = kill_from(&grp, cfg.lm);
            free.retain(|p| !grp.iter().any(|q| q.bar == p.bar && q.p == p.p));
            let dead = if owner {
                false
            } else if cfg.pk {
                let d = pk_violated(&f, bars, from_bar, b, mean, cfg.tau_pm);
                if d && count && !(from_bar + 1..=b).any(|bb| bars.ok[bb] && f.cl(bb) > mean) {
                    PK_BIRTH_BLOCKED.fetch_add(1, Ordering::Relaxed);
                }
                d
            } else {
                (from_bar + 1..=b).any(|bb| bars.ok[bb] && f.cl(bb) > kill_px(mean))
            };
            if !dead {
                levels.push(Level {
                    born: now as u32,
                    end: u32::MAX,
                    segs: vec![(now as u32, mean.abs())],
                    first_bar,
                    mem: if track {
                        vec![members(&grp)]
                    } else {
                        Vec::new()
                    },
                    src: 0,
                    why: 0,
                    fresh: vec![fresh_of(&grp, tfm)],
                });
                live.push(Live {
                    id: levels.len() - 1,
                    members: grp,
                    main: mean,
                });
            }
        }
    }
    levels
}

// ───────────── TK-668 (`lx`): уровни из источника вместо пивотов ─────────────

/// Источник `lx` читает только закрытые часовые бары (`bars_for(60)`); минутки — лишь профиль `lx = 3`.
const SRC_TF_MIN: usize = 60;
const DAY_BARS: usize = 24;
const WEEK_BARS: usize = 168;
/// `lx = 3`: минимум минут суток с данными, при меньшем числе профиля нет.
const PROFILE_MIN_MINUTES: usize = 1200;
/// `lx = 3`: ширина корзины — `средняя tp суток / PROFILE_BIN_DIV` (0,10 %).
const PROFILE_BIN_DIV: i64 = 1000;

/// Источник уровней `lx ≠ 0` (§13.68): периоды подряд — сутки (`lx = 1, 3`) или календарные недели UTC (`lx = 2`, первый понедельник — на `lw` суток
/// от начала сетки). Уровень периода рождается готовым на первой минуте следующего периода и живёт один период, но не дольше закрытия первого
/// закрытого часового бара за ценой (в раме стороны, как у пивотных уровней: `end` — конец этого бара). Период без данных (бар `ok = false`; для
/// профиля — меньше `PROFILE_MIN_MINUTES` минут с данными) уровня не даёт. Журнала членов нет (`mem` пуст): дальний край зоны — `None`.
fn build_source_levels(sym: &Sym, cfg: &LevelCfg, side: Side) -> Vec<Level> {
    let Some(bars) = sym.bars_for(SRC_TF_MIN) else {
        return Vec::new();
    };
    let f = Frame {
        b: bars,
        s: side.s(),
    };
    let nb = bars.ok.len();
    let (step, first) = if cfg.lx == 2 {
        (WEEK_BARS, cfg.lw * DAY_BARS)
    } else {
        (DAY_BARS, 0)
    };
    let mut out = Vec::new();
    let mut a = first;
    while a + step < nb {
        let born_bar = a + step;
        let found = if cfg.lx == 3 {
            profile_poc(sym, &f, a, born_bar)
        } else {
            range_extreme(&f, a, born_bar)
        };
        if let Some((price, first_bar)) = found {
            let life_end = (born_bar + step).min(nb);
            let end_bar = (born_bar..life_end).find(|&b| bars.ok[b] && f.cl(b) > price);
            let born = (born_bar * SRC_TF_MIN) as u32;
            out.push(Level {
                born,
                end: end_bar.map_or(((born_bar + step) * SRC_TF_MIN) as u32, |b| {
                    ((b + 1) * SRC_TF_MIN) as u32
                }),
                segs: vec![(born, price.abs())],
                first_bar: first_bar as u32,
                mem: Vec::new(),
                src: cfg.lx,
                why: 0,
                fresh: Vec::new(),
            });
        }
        a += step;
    }
    out
}

/// `lx = 1, 2`: цена уровня периода — максимум `h` (лонг) / минимум `l` (шорт) баров `a..z` (в раме стороны — максимум `hi`) и первый бар с ним;
/// любой бар периода без данных — `None`.
fn range_extreme(f: &Frame, a: usize, z: usize) -> Option<(i64, usize)> {
    if !(a..z).all(|i| f.b.ok[i]) {
        return None;
    }
    let price = (a..z).map(|i| f.hi(i)).max()?;
    let first = (a..z).find(|&i| f.hi(i) == price)?;
    Some((price, first))
}

/// `lx = 3`: профиль объёма минуток баров `a..z` (одни сутки): `tp = (h + l + c) / 3`, корзины шириной 0,10 % средней `tp` суток (сетка корзин от нуля
/// цены: корзина — `tp / ширина`, целочисленно), вес — оборот `qv`. Цена — центр корзины с наибольшим оборотом (ничья — нижняя), в раме стороны;
/// бар — час минуты с наибольшим оборотом в корзине (ничья — ранняя минута). Меньше `PROFILE_MIN_MINUTES` минут с данными или нулевой оборот — `None`.
#[allow(clippy::needless_range_loop)] // m индексирует сразу несколько рядов монеты
fn profile_poc(sym: &Sym, f: &Frame, a: usize, z: usize) -> Option<(i64, usize)> {
    let (m0, m1) = (a * SRC_TF_MIN, z * SRC_TF_MIN);
    let mut mins: Vec<(i64, i64, usize)> = Vec::with_capacity(m1 - m0);
    let mut sum_tp: i128 = 0;
    for m in m0..m1 {
        if sym.o[m] == 0 {
            continue;
        }
        let tp = (sym.h[m] + sym.l[m] + sym.c[m]) / 3;
        sum_tp += i128::from(tp);
        mins.push((tp, sym.qv[m], m));
    }
    if mins.len() < PROFILE_MIN_MINUTES {
        return None;
    }
    let mean = (sum_tp / mins.len() as i128) as i64;
    let w = (mean / PROFILE_BIN_DIV).max(1);
    let mut cells: Vec<(i64, i64, usize)> = mins
        .iter()
        .map(|&(tp, qv, m)| (tp.div_euclid(w), qv, m))
        .collect();
    cells.sort_unstable_by_key(|&(bin, _, m)| (bin, m));
    let mut best: Option<(i128, i64, usize)> = None;
    for grp in cells.chunk_by(|x, y| x.0 == y.0) {
        let total: i128 = grp.iter().map(|x| i128::from(x.1)).sum();
        if best.is_none_or(|(t, _, _)| total > t) {
            let mut peak = grp[0];
            for &x in grp {
                if x.1 > peak.1 {
                    peak = x;
                }
            }
            best = Some((total, grp[0].0, peak.2));
        }
    }
    let (total, bin, peak_min) = best?;
    if total <= 0 {
        return None;
    }
    let poc = bin * w + w / 2;
    Some((f.s * poc, peak_min / SRC_TF_MIN))
}

// ───────────── TK-697 D № 1 (`lx = 4`): наклонная линия по последним разворотам ─────────────

/// Период NATR для предела наклона `lsl` (расстояния в NATR60 — вердикт критика TK-697).
const LINE_NATR_P: usize = 60;

/// МНК-линия по точкам `(x, y)`, `x` — минуты сетки (строго возрастают), `y` — цена ×1e8 в раме стороны. Целая арифметика (`i128`), коэффициенты
/// рациональные: float в расчёте нет. `x`, `y` берутся от первой точки (`x0`, `y0`); наклон = `nn / d` цены за минуту.
struct Line {
    x0: i64,
    y0: i64,
    n: i128,
    /// `n·Σxy − Σx·Σy`: наклон `nn / d` (строго падающие точки дают `nn < 0`).
    nn: i128,
    /// `n·Σx² − (Σx)²` > 0.
    d: i128,
    /// `Σy·d − nn·Σx`: числитель свободного члена над `n·d`.
    c0: i128,
}

impl Line {
    /// `None` — точек нет.
    fn fit(pts: &[(i64, i64)]) -> Option<Line> {
        let &(x0, y0) = pts.first()?;
        let n = pts.len() as i128;
        let (mut sx, mut sy, mut sxx, mut sxy) = (0i128, 0i128, 0i128, 0i128);
        for &(x, y) in pts {
            let (dx, dy) = (i128::from(x - x0), i128::from(y - y0));
            sx += dx;
            sy += dy;
            sxx += dx * dx;
            sxy += dx * dy;
        }
        let d = n * sxx - sx * sx;
        let nn = n * sxy - sx * sy;
        Some(Line {
            x0,
            y0,
            n,
            nn,
            d,
            c0: sy * d - nn * sx,
        })
    }

    /// Значение линии в раме стороны в середине минуты `m` (`m·60 с + 30 с`): `y0 + (c0 + n·nn·(x − x0)) / (n·d)` при `x = m + ½`; округление
    /// к ближайшему целому 1e-8, половина — вверх.
    fn at_mid(&self, m: usize) -> i64 {
        let rel2 = 2 * (m as i128 - i128::from(self.x0)) + 1;
        let num = 2 * self.c0 + self.n * self.nn * rel2;
        let q = 2 * self.n * self.d;
        let off = (2 * num + q).div_euclid(2 * q);
        self.y0
            .saturating_add(i64::try_from(off).unwrap_or(i64::MAX))
    }
}

/// `lx = 4` (TK-697 D № 1): наклонная линия через `lk` последних подтверждённых разворотов стороны (тот же `find_pivots`, свечи `tf`, ключи `k` / `d`).
/// Линия рождается на каждое подтверждение пивота стороны (закрытие бара `active`; пивоты одного бара — одна линия), если
/// - набралось `lk` точек (последние по номеру бара среди подтверждённых) и их цены в раме стороны строго падают (лонг — падающие максимумы, шорт — растущие
///   минимумы: наклон против пробоя);
/// - возраст старшей точки на минуте рождения (от начала её бара) ≤ `ld` суток;
/// - при `lsl > 0`: |наклон| ≤ `lsl`·NATR60[рождение − 1]·цена линии в час (мерится один раз, при рождении; нет значения NATR — линии нет);
/// - цена линии в момент рождения положительна, и ни один закрытый бар `tf` после старшей точки не закрылся выше линии (закрытие против версии
///   последней минуты бара; рама стороны).
///
/// Конец — первое из: следующее подтверждение пивота той же стороны (набор «последние `lk`» сменился: старая линия кончилась, новая — другой `Level`),
/// возраст старшей точки > `ld` суток на закрытии бара `tf` (как окно `l_days` у пивотных уровней), закрытие бара выше линии, цена ≤ 0. Версия цены — на каждую минуту
/// жизни: значение линии в середине минуты (`Line::at_mid`); внутри минуты уровень неподвижен, как любой уровень движка. Журнала членов нет (`mem` пуст),
/// `first_bar` — бар старшей точки в барах `tf`, `src = 4`.
fn build_line_levels(sym: &Sym, cfg: &LevelCfg, side: Side) -> Vec<Level> {
    let Some(bars) = sym.bars_for(cfg.tf_min) else {
        return Vec::new();
    };
    let f = Frame {
        b: bars,
        s: side.s(),
    };
    let pivs = find_pivots(&f, cfg);
    let natr = if cfg.lsl_m > 0 {
        sym.natr_for(LINE_NATR_P)
    } else {
        None
    };
    let tfm = cfg.tf_min;
    let mut out = Vec::new();
    // последние `lk` подтверждённых пивотов по возрастанию бара: поздно подтверждённый пивот с более ранним баром встаёт на своё место
    let mut last: Vec<Pv> = Vec::with_capacity(cfg.lk + 1);
    // пивоты одного бара подтверждения — одна группа (`pivs` отсортирован по `active`)
    let groups: Vec<&[Pv]> = pivs.chunk_by(|a, b| a.active == b.active).collect();
    for (gi, grp) in groups.iter().enumerate() {
        let Some(first) = grp.first() else {
            continue;
        };
        for &pv in *grp {
            let at = last.partition_point(|q| q.bar < pv.bar);
            last.insert(at, pv);
            if last.len() > cfg.lk {
                last.remove(0);
            }
        }
        if last.len() < cfg.lk {
            continue;
        }
        // следующее подтверждение пивота стороны — конец этой линии (даже если новой линии не будет)
        let next = groups
            .get(gi + 1)
            .and_then(|g| g.first())
            .map_or(usize::MAX, |p| (p.active as usize + 1) * tfm);
        if let Some(lv) = line_level(&f, cfg, natr, &last, first.active as usize, next) {
            out.push(lv);
        }
    }
    out
}

/// Линия по точкам `pts` (по возрастанию бара), подтверждённым к закрытию бара `b`; `next` — минута следующего подтверждения пивота стороны. `None` — линии нет.
fn line_level(
    f: &Frame,
    cfg: &LevelCfg,
    natr: Option<&[i32]>,
    pts: &[Pv],
    b: usize,
    next: usize,
) -> Option<Level> {
    let tfm = cfg.tf_min;
    let now = (b + 1) * tfm;
    if !pts.windows(2).all(|w| matches!(w, [p, q] if q.p < p.p)) {
        return None;
    }
    let first_bar = pts.first()?.bar;
    let oldest = first_bar as usize;
    let lwin = cfg.ld * 1440;
    if now - oldest * tfm > lwin {
        return None;
    }
    let xy: Vec<(i64, i64)> = pts
        .iter()
        .map(|p| ((p.bar as usize * tfm + tfm / 2) as i64, p.p))
        .collect();
    let line = Line::fit(&xy)?;
    if line.nn >= 0 {
        return None;
    }
    let v_born = line.at_mid(now);
    if f.s * v_born <= 0 {
        return None;
    }
    if cfg.lsl_m > 0 {
        // |наклон|·60 / цена ≤ lsl·NATR60 (доли ×SCALE): nn·60·1000·SCALE ≤ d·lsl_m·natr·цена
        let nv = i64::from(*natr?.get(now - 1)?);
        if nv < 0 {
            return None;
        }
        let lhs = line.nn.abs().saturating_mul(60_000 * i128::from(SCALE));
        let rhs = line
            .d
            .saturating_mul(i128::from(cfg.lsl_m))
            .saturating_mul(i128::from(nv))
            .saturating_mul(i128::from(v_born.abs()));
        if lhs > rhs {
            return None;
        }
    }
    // конец давности: закрытие первого бара, на котором возраст старшей точки больше `ld` суток
    let cutoff = next.min((oldest + lwin / tfm + 1) * tfm);
    // закрытие бара выше линии (в раме стороны): до рождения — линии нет, после — её конец; бар сверяется с версией своей последней минуты
    let mut kill = cutoff;
    for (bb, &ok) in f.b.ok.iter().enumerate().skip(oldest + 1) {
        if (bb + 1) * tfm > cutoff {
            break;
        }
        if ok && f.cl(bb) > line.at_mid((bb + 1) * tfm - 1) {
            if bb <= b {
                return None;
            }
            kill = (bb + 1) * tfm;
            break;
        }
    }
    let mut end = kill;
    let mut segs = Vec::with_capacity(kill.min(N_MIN).saturating_sub(now));
    for m in now..kill.min(N_MIN) {
        let v = line.at_mid(m);
        if f.s * v <= 0 {
            end = m;
            break;
        }
        segs.push((u32::try_from(m).ok()?, v.abs()));
    }
    if segs.is_empty() {
        return None;
    }
    Some(Level {
        born: u32::try_from(now).ok()?,
        end: u32::try_from(end).unwrap_or(u32::MAX),
        segs,
        first_bar,
        mem: Vec::new(),
        src: 4,
        why: 0,
        fresh: Vec::new(),
    })
}
