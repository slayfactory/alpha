#!/usr/bin/env python3
# TK-706 (пучок, §8 / критик 25.11): мерные счётчики в копии дерева m2 (`/root/perf706/src-ws3`), поведение не меняют, включаются `P42_B3=1`.
# Предпроход «кандидаты» (решение о входе по всем сигналам задания, без фильтров портфеля) → ключи-кандидаты лота 1 по группам (ключ без `ExitCfg`);
# спрос — ключи, которые реально запросил `sim_fill_mem` (лот 1 и лот 2 отдельно) с длиной хода в тиках; после фазы 2 `b3_report`: непринятые кандидаты групп
# со спросом проходятся по ленте (стенка выборки `P42_B3_SAMPLE`, по умолчанию 1 = все), итог — JSON (`P42_B3_OUT`) и строка `B3JSON:` в stderr.
# Запуск на srv: python3 ws3_patch.py  (после `cp -a src-memo src-ws3`).
import sys
D = '/root/perf706/src-ws3/src/breakout/'


def load(n):
    return open(D + n, 'rb').read().decode('utf-8').replace('\r\n', '\n')


def rep(s, old, new, cnt=1):
    assert s.count(old) == cnt, (old[:60], s.count(old))
    return s.replace(old, new)


t = load('tick.rs')

BLOCK = r'''// ---- TK-706 пучок: мерные счётчики (поведение не меняют; включает P42_B3=1) ----
type B3Group = (Box<str>, i64, i64, i64, i64, Option<LevelCtx>, i64, i64, Option<i64>);

#[derive(Default)]
struct B3Info {
    cand: Vec<u32>,
    dem: Vec<(u32, u64)>,
}

#[derive(Default)]
struct B3State {
    exs: Vec<ExitCfg>,
    ex_ids: HashMap<ExitCfg, u32>,
    g1: HashMap<B3Group, B3Info>,
    g2: HashMap<B3Group, B3Info>,
    cand_total: u64,
    overflow: bool,
}

static B3_ST: OnceLock<Mutex<B3State>> = OnceLock::new();
static B3_ON: OnceLock<bool> = OnceLock::new();
pub static B3_SCOUT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static B3_TASKS: AtomicU64 = AtomicU64::new(0);
static B3_SKIP: AtomicU64 = AtomicU64::new(0);
static B3_SIGS: AtomicU64 = AtomicU64::new(0);
static B3_PAIRS: AtomicU64 = AtomicU64::new(0);
static B3_PRE_NS: AtomicU64 = AtomicU64::new(0);
static B3_PRE_SCOUT_NS: AtomicU64 = AtomicU64::new(0);
static B3_PRE_RAW_NS: AtomicU64 = AtomicU64::new(0);
const B3_MAXC: u64 = 40_000_000;
thread_local! {
    static B3_TICKS: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
    static B3_LOAD: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
    static B3_LOT: std::cell::Cell<u8> = const { std::cell::Cell::new(1) };
}

fn b3_on() -> bool {
    *B3_ON.get_or_init(|| std::env::var_os("P42_B3").is_some())
}

fn b3_cpu_ns() -> u64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

fn b3_state() -> std::sync::MutexGuard<'static, B3State> {
    B3_ST
        .get_or_init(|| Mutex::new(B3State::default()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

fn b3_ex_id(st: &mut B3State, ex: &ExitCfg) -> u32 {
    if let Some(&i) = st.ex_ids.get(ex) {
        return i;
    }
    let i = st.exs.len() as u32;
    st.exs.push(ex.clone());
    st.ex_ids.insert(ex.clone(), i);
    i
}

fn b3_lot(l: u8) {
    B3_LOT.with(|c| c.set(l));
}

/// Предпроход: ключи лота 1 по всем сигналам задания (решение о входе — чистая функция ленты; фильтры портфеля не применяются).
#[allow(clippy::too_many_arguments)]
fn b3_prepass(
    syms: &[Sym],
    sigs: &[TSig],
    side: Side,
    ex: &ExitCfg,
    pc: &PortCfg,
    src: &dyn TradeSrc,
    mk: u8,
    ek: i64,
    xb: i64,
) {
    if !b3_on() {
        return;
    }
    B3_TASKS.fetch_add(1, Ordering::Relaxed);
    if mk > 0 || ek > 0 || xb > 0 || ex.e1t != 0 || pc.pw_ms > 0 || pc.pr > 0 {
        B3_SKIP.fetch_add(1, Ordering::Relaxed);
        return;
    }
    let (c0, l0) = (b3_cpu_ns(), B3_LOAD.with(|c| c.get()));
    let (lot_n, _) = ex.lots(NOTIONAL_U);
    let mut set: HashSet<B3Group> = HashSet::new();
    let mut pairs = 0u64;
    for sg in sigs {
        let sym = &syms[sg.sym as usize];
        let natr = exit_natr(sym, ex, sg.t as usize);
        if natr <= 0 {
            continue;
        }
        if let EntryDec::Fill { ms, p0 } = entry_decision(src, sym, side, sg, ex) {
            let lc = level_ctx(sym, side, sg, ex);
            pairs += 1;
            set.insert((
                sym.name.as_str().into(),
                ms,
                p0,
                side.s(),
                natr,
                Some(lc),
                lot_n,
                ex.slip_bps,
                None,
            ));
        }
    }
    let raw = b3_cpu_ns() - c0;
    let net = raw.saturating_sub(B3_LOAD.with(|c| c.get()) - l0);
    B3_SIGS.fetch_add(sigs.len() as u64, Ordering::Relaxed);
    B3_PAIRS.fetch_add(pairs, Ordering::Relaxed);
    B3_PRE_RAW_NS.fetch_add(raw, Ordering::Relaxed);
    if B3_SCOUT.load(Ordering::Relaxed) {
        B3_PRE_SCOUT_NS.fetch_add(net, Ordering::Relaxed);
    } else {
        B3_PRE_NS.fetch_add(net, Ordering::Relaxed);
    }
    let mut st = b3_state();
    let stp = &mut *st;
    let id = b3_ex_id(stp, ex);
    for g in set {
        if stp.cand_total >= B3_MAXC {
            stp.overflow = true;
            break;
        }
        let info = stp.g1.entry(g).or_default();
        if !info.cand.contains(&id) {
            info.cand.push(id);
            stp.cand_total += 1;
        }
    }
}

/// Спрос: ключ, запрошенный `sim_fill_mem` (лот — по `B3_LOT`), и длина хода в тиках (0 — взят из памяти; запись держит максимум).
#[allow(clippy::too_many_arguments)]
fn b3_demand(
    coin: &str,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
    slip: i64,
    stop_px: Option<i64>,
    ticks: u64,
) {
    if !b3_on() {
        return;
    }
    let g: B3Group = (
        coin.into(),
        entry_ms,
        p0,
        side.s(),
        natr,
        lc,
        notional,
        slip,
        stop_px,
    );
    let lot2 = B3_LOT.with(|c| c.get()) == 2;
    let mut st = b3_state();
    let stp = &mut *st;
    let id = b3_ex_id(stp, ex);
    let map = if lot2 { &mut stp.g2 } else { &mut stp.g1 };
    let info = map.entry(g).or_default();
    match info.dem.iter_mut().find(|e| e.0 == id) {
        Some(e) => e.1 = e.1.max(ticks),
        None => info.dem.push((id, ticks)),
    }
}

/// Ход по ленте для ключа группы, длина в тиках.
fn b3_walk(src: &dyn TradeSrc, g: &B3Group, ex: &ExitCfg) -> u64 {
    let (coin, entry_ms, p0, side_s, natr, lc, notional, slip, stop_px) = g;
    let side = if *side_s > 0 { Side::Long } else { Side::Short };
    let day = ((*entry_ms).max(0) / DAY_MS) as usize;
    let base = day as i64 * DAY_MS;
    let idx = src.day(coin, day).map_or(0, |v| {
        v.partition_point(|x| base + i64::from(x.off()) <= *entry_ms)
    });
    let mut cur = Cursor::new(src, coin, day, idx);
    let mut n = 0u64;
    let _ = sim_fill_lc_s(
        &mut || {
            let t = cur.next_tick();
            if t.is_some() {
                n += 1;
            }
            t
        },
        *entry_ms,
        *p0,
        side,
        *natr,
        ex,
        *lc,
        *notional,
        *slip,
        *stop_px,
    );
    n
}

/// Только обвязка хода на `limit` тиков (длина настоящего хода): те же слои вызовов (`next` → `after` → цикл), без `Pos::on_trade` / `fill_takes` — верхняя оценка общей части.
fn b3_iter(src: &dyn TradeSrc, g: &B3Group, limit: u64) -> u64 {
    let (coin, entry_ms, _p0, side_s, _natr, _lc, _notional, _slip, _stop_px) = g;
    let day = ((*entry_ms).max(0) / DAY_MS) as usize;
    let base = day as i64 * DAY_MS;
    let idx = src.day(coin, day).map_or(0, |v| {
        v.partition_point(|x| base + i64::from(x.off()) <= *entry_ms)
    });
    let mut cur = Cursor::new(src, coin, day, idx);
    let mut next = || cur.next_tick();
    let nx: &mut dyn FnMut() -> Option<(i64, i64, bool)> = &mut next;
    let mut after = || -> Option<(i64, i64, bool)> {
        loop {
            let t = nx()?;
            if t.0 > *entry_ms {
                return Some(t);
            }
        }
    };
    let aft: &mut dyn FnMut() -> Option<(i64, i64, bool)> = &mut after;
    let (mut n, mut cur_day, mut last) = (0u64, (*entry_ms / DAY_MS) as usize, (*entry_ms, 0i64));
    while let Some((ms, px, _)) = aft() {
        let d = (ms / DAY_MS) as usize;
        while cur_day < d {
            cur_day += 1;
        }
        last = (ms, side_s * px);
        n += 1;
        if n >= limit {
            break;
        }
    }
    std::hint::black_box(last);
    n
}

/// Итог после фазы 2: статистика групп, прогулка непринятых кандидатов групп со спросом, JSON.
pub fn b3_report(src: &dyn TradeSrc, threads: usize) {
    use std::sync::atomic::AtomicUsize;
    if !b3_on() {
        return;
    }
    let st = std::mem::take(&mut *b3_state());
    let sample: usize = std::env::var("P42_B3_SAMPLE")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1)
        .max(1);
    let groups: Vec<(&B3Group, &B3Info)> = st.g1.iter().collect();
    let mut items: Vec<(usize, u32)> = Vec::new();
    let (mut cand_total, mut dem1, mut dem_notcand, mut groups_dem, mut cand_nodem) =
        (0u64, 0u64, 0u64, 0u64, 0u64);
    let (mut cand_in_dem, mut groups_cand) = (0u64, 0u64);
    for (gi, (_, info)) in groups.iter().enumerate() {
        cand_total += info.cand.len() as u64;
        groups_cand += u64::from(!info.cand.is_empty());
        dem1 += info.dem.len() as u64;
        dem_notcand += info
            .dem
            .iter()
            .filter(|d| !info.cand.contains(&d.0))
            .count() as u64;
        if info.dem.is_empty() {
            cand_nodem += info.cand.len() as u64;
            continue;
        }
        groups_dem += 1;
        cand_in_dem += info.cand.len() as u64;
        for &id in &info.cand {
            if !info.dem.iter().any(|d| d.0 == id) {
                items.push((gi, id));
            }
        }
    }
    let picked: Vec<usize> = (0..items.len()).step_by(sample).collect();
    let ticks: Vec<AtomicU64> = (0..picked.len()).map(|_| AtomicU64::new(0)).collect();
    let (walk_ns, walk_ticks) = (AtomicU64::new(0), AtomicU64::new(0));
    let (t_full, t_iter, t_ticks) = (AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0));
    let next = AtomicUsize::new(0);
    let w0 = std::time::Instant::now();
    std::thread::scope(|s| {
        for _ in 0..threads.max(1) {
            s.spawn(|| {
                let (c0, l0) = (b3_cpu_ns(), B3_LOAD.with(|c| c.get()));
                let mut tk = 0u64;
                loop {
                    let k = next.fetch_add(1, Ordering::Relaxed);
                    if k >= picked.len() {
                        break;
                    }
                    let (gi, id) = items[picked[k]];
                    let (g, ex) = (groups[gi].0, &st.exs[id as usize]);
                    let n = if k % 4 == 0 {
                        // замер доли общей части: разогрев суток, ход целиком, только обвязка (ЦП потока)
                        let _ = b3_walk(src, g, ex);
                        let a = b3_cpu_ns();
                        let n = b3_walk(src, g, ex);
                        let b = b3_cpu_ns();
                        let n2 = b3_iter(src, g, n);
                        let c = b3_cpu_ns();
                        assert_eq!(n, n2, "b3: тики хода и обвязки не равны");
                        t_full.fetch_add(b - a, Ordering::Relaxed);
                        t_iter.fetch_add(c - b, Ordering::Relaxed);
                        t_ticks.fetch_add(n, Ordering::Relaxed);
                        n
                    } else {
                        b3_walk(src, g, ex)
                    };
                    ticks[k].store(n, Ordering::Relaxed);
                    tk += n;
                }
                let net = (b3_cpu_ns() - c0).saturating_sub(B3_LOAD.with(|c| c.get()) - l0);
                walk_ns.fetch_add(net, Ordering::Relaxed);
                walk_ticks.fetch_add(tk, Ordering::Relaxed);
            });
        }
    });
    let walk_wall = w0.elapsed().as_secs_f64();
    let mut gu_sum = vec![0u64; groups.len()];
    let mut gu_max = vec![0u64; groups.len()];
    for (k, &p) in picked.iter().enumerate() {
        let (gi, _) = items[p];
        let n = ticks[k].load(Ordering::Relaxed);
        gu_sum[gi] += n;
        gu_max[gi] = gu_max[gi].max(n);
    }
    // бакеты размера группы по спросу: 1, 2, 3–4, 5–8, 9–16, 17–32, 33+
    let bucket = |n: usize| match n {
        0..=1 => 0,
        2 => 1,
        3..=4 => 2,
        5..=8 => 3,
        9..=16 => 4,
        17..=32 => 5,
        _ => 6,
    };
    let mut hist = [[0u64; 3]; 7]; // групп, ключей, тиков спроса
    let mut chist = [0u64; 7]; // групп со спросом по числу кандидатов
    let (mut t1, mut a_dem, mut w_ticks, mut b_ticks, mut m_ticks) =
        (0u64, 0u64, 0u64, 0u64, 0u64);
    for (gi, (_, info)) in groups.iter().enumerate() {
        if info.dem.is_empty() {
            continue;
        }
        let dsum: u64 = info.dem.iter().map(|d| d.1).sum();
        let dmax: u64 = info.dem.iter().map(|d| d.1).max().unwrap_or(0);
        let h = &mut hist[bucket(info.dem.len())];
        h[0] += 1;
        h[1] += info.dem.len() as u64;
        h[2] += dsum;
        chist[bucket(info.cand.len())] += 1;
        t1 += dsum;
        a_dem += dmax;
        let usum = gu_sum[gi] * sample as u64;
        w_ticks += usum;
        b_ticks += dmax.max(gu_max[gi]);
        m_ticks += dsum + usum;
    }
    let (mut dem2, mut t2, mut a2, mut groups2) = (0u64, 0u64, 0u64, 0u64);
    for info in st.g2.values() {
        groups2 += 1;
        dem2 += info.dem.len() as u64;
        t2 += info.dem.iter().map(|d| d.1).sum::<u64>();
        a2 += info.dem.iter().map(|d| d.1).max().unwrap_or(0);
    }
    let s = |a: &AtomicU64| a.load(Ordering::Relaxed);
    let ns_s = |n: u64| n as f64 / 1e9;
    let j = serde_json::json!({
        "tasks": s(&B3_TASKS), "tasks_skipped": s(&B3_SKIP), "sigs_scanned": s(&B3_SIGS), "pairs_fill": s(&B3_PAIRS),
        "prepass_cpu_s": ns_s(s(&B3_PRE_NS)), "prepass_scout_cpu_s": ns_s(s(&B3_PRE_SCOUT_NS)), "prepass_raw_cpu_s": ns_s(s(&B3_PRE_RAW_NS)),
        "cand_keys": cand_total, "cand_groups": groups_cand, "overflow": st.overflow,
        "dem1_keys": dem1, "dem1_not_in_cand": dem_notcand, "groups_dem": groups_dem,
        "cand_in_dem_groups": cand_in_dem, "cand_in_nodem_groups": cand_nodem, "unaccepted_in_dem_groups": items.len(),
        "sample": sample, "walked": picked.len(), "walk_cpu_s": ns_s(walk_ns.load(Ordering::Relaxed)), "walk_wall_s": walk_wall,
        "walk_ticks": walk_ticks.load(Ordering::Relaxed),
        "timed_full_cpu_s": ns_s(t_full.load(Ordering::Relaxed)), "timed_iter_cpu_s": ns_s(t_iter.load(Ordering::Relaxed)), "timed_ticks": t_ticks.load(Ordering::Relaxed),
        "ticks_dem1": t1, "ticks_dem1_groupmax": a_dem, "ticks_unacc_est": w_ticks, "ticks_bundle_wrap": b_ticks, "ticks_bundle_member": m_ticks,
        "lot2": {"groups": groups2, "keys": dem2, "ticks": t2, "ticks_groupmax": a2},
        "hist_dem_keys_per_group_[1,2,3-4,5-8,9-16,17-32,33+]": {"groups": hist.iter().map(|h| h[0]).collect::<Vec<_>>(), "keys": hist.iter().map(|h| h[1]).collect::<Vec<_>>(), "ticks": hist.iter().map(|h| h[2]).collect::<Vec<_>>()},
        "hist_cand_per_demgroup": chist,
    });
    eprintln!("B3JSON: {j}");
    if let Some(p) = std::env::var_os("P42_B3_OUT") {
        let _ = fs::write(p, serde_json::to_string_pretty(&j).unwrap_or_default());
    }
}

'''

anchor = '/// TK-706: ход лота по ленте монеты `coin`'
t = rep(t, anchor, BLOCK + anchor)

# счёт тиков хода в sim_fill_mem
old = '''        let mut cur = Cursor::new(src, coin, day, idx);
        sim_fill_lc_s(
            &mut || cur.next_tick(),
            entry_ms,
            p0,
            side,
            natr_e8,
            ex,
            Some(lc),
            notional,
            entry_slip_bps,
            stop_px,
        )
    };
'''
new = '''        let mut cur = Cursor::new(src, coin, day, idx);
        let mut b3n = 0u64;
        let out = sim_fill_lc_s(
            &mut || {
                let t = cur.next_tick();
                if t.is_some() {
                    b3n += 1;
                }
                t
            },
            entry_ms,
            p0,
            side,
            natr_e8,
            ex,
            Some(lc),
            notional,
            entry_slip_bps,
            stop_px,
        );
        B3_TICKS.with(|c| c.set(c.get() + b3n));
        out
    };
'''
t = rep(t, old, new)

old = '''    memo.get_or_compute(key, &mut compute)
}
'''
new = '''    B3_TICKS.with(|c| c.set(0));
    let out = memo.get_or_compute(key, &mut compute);
    b3_demand(
        coin,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        Some(lc),
        notional,
        entry_slip_bps,
        stop_px,
        B3_TICKS.with(|c| c.get()),
    );
    out
}
'''
t = rep(t, old, new)

# предпроход в начале run_tick_xb
old = '''    let ns = syms.len();
    let mut res = TickResult {'''
new = '''    b3_prepass(syms, sigs, side, ex, pc, src, mk, ek, xb);
    let ns = syms.len();
    let mut res = TickResult {'''
t = rep(t, old, new)

# лот 2
old = '''                                tr2 = Some(sim_fill_mem(
                                    src, &sym.name, m2, q0, side, natr, ex, lc, lot2_n, entry_slip,
                                    stop2,
                                ));'''
new = '''                                b3_lot(2);
                                tr2 = Some(sim_fill_mem(
                                    src, &sym.name, m2, q0, side, natr, ex, lc, lot2_n, entry_slip,
                                    stop2,
                                ));
                                b3_lot(1);'''
t = rep(t, old, new)

# загрузка суток: ЦП потока в разборе (вычитается из ЦП предпрохода / прогулок)
old = '    fn load_day(&self, name: &str, day: usize, full: bool) -> Result<Option<DayTrades>> {\n'
new = '''    fn load_day(&self, name: &str, day: usize, full: bool) -> Result<Option<DayTrades>> {
        let c0 = b3_cpu_ns();
        let r = self.load_day_inner(name, day, full);
        B3_LOAD.with(|c| c.set(c.get() + b3_cpu_ns() - c0));
        r
    }

    fn load_day_inner(&self, name: &str, day: usize, full: bool) -> Result<Option<DayTrades>> {
'''
t = rep(t, old, new)
open(D + 'tick.rs', 'wb').write(t.encode('utf-8'))

c = load('tick_cli.rs')
old = '        par_map(&items, threads, |&(vi, side, v)| {'
new = '''        super::tick::B3_SCOUT.store(true, std::sync::atomic::Ordering::Relaxed);
        par_map(&items, threads, |&(vi, side, v)| {'''
c = rep(c, old, new)
old = '''        });
        eprintln!(
            "разогрев суток сделок:'''
new = '''        });
        super::tick::B3_SCOUT.store(false, std::sync::atomic::Ordering::Relaxed);
        eprintln!(
            "разогрев суток сделок:'''
c = rep(c, old, new)
old = '''        None => eprintln!("память исходов: выключена (P42_MEMO=0)"),
    }
'''
new = '''        None => eprintln!("память исходов: выключена (P42_MEMO=0)"),
    }
    super::tick::b3_report(&src, threads);
'''
c = rep(c, old, new)
open(D + 'tick_cli.rs', 'wb').write(c.encode('utf-8'))
print('patched')
