import re,sys
p='/root/perf706/src-ws2/src/breakout/tick.rs'
s=open(p,'rb').read().decode('utf-8').replace('\r\n','\n')
# 1. rename inner
assert s.count('\nfn sim_tail_e(')==1
s=s.replace('\nfn sim_tail_e(','\nfn sim_tail_e_inner(')
# 2. wrapper + counters inserted before the doc-comment line of sim_tail_e_inner
anchor='/// `sim_tail` с проскальзыванием входа `entry_slip_bps`'
assert s.count(anchor)==1
wrapper=r'''
// ---- TK-706 мерные счётчики (поведение не меняют) ----
pub static WS2_WALKS: AtomicU64 = AtomicU64::new(0);
pub static WS2_TICKS: AtomicU64 = AtomicU64::new(0);
pub static WS2_DIST: AtomicU64 = AtomicU64::new(0);
pub static WS2_DTICKS: AtomicU64 = AtomicU64::new(0);
pub static WS2_MISMATCH: AtomicU64 = AtomicU64::new(0);
pub static WS2_NS: AtomicU64 = AtomicU64::new(0);
pub static WS2_LOADS: AtomicU64 = AtomicU64::new(0);
type Ws2Key = (u64, i64, i64, i64, i64, i64, i64, Option<i64>, String);
static WS2_MAP: OnceLock<Mutex<HashMap<Ws2Key, Vec<(ExitCfg, u64, u64)>>>> = OnceLock::new();
static WS2_LOADKEYS: OnceLock<Mutex<HashSet<(String, usize)>>> = OnceLock::new();
static WS2_REG: std::sync::Once = std::sync::Once::new();
thread_local! { static WS2_COIN: std::cell::Cell<u64> = const { std::cell::Cell::new(0) }; }
fn ws2_set_coin(name: &str) {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    name.hash(&mut h);
    WS2_COIN.with(|c| c.set(h.finish()));
}
fn ws2_cpu_ns() -> u64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}
extern "C" fn ws2_report() {
    let r = |a: &AtomicU64| a.load(Ordering::Relaxed);
    let (w, t, d, dt) = (r(&WS2_WALKS), r(&WS2_TICKS), r(&WS2_DIST), r(&WS2_DTICKS));
    let groups = WS2_MAP.get().and_then(|m| m.lock().ok().map(|g| g.len())).unwrap_or(0);
    let dk = WS2_LOADKEYS.get().and_then(|m| m.lock().ok().map(|g| g.len())).unwrap_or(0);
    eprintln!(
        "WS2: ходов {w} входов-ключей без ExitCfg {groups} различных полных ключей {d} G по ходам {:.3} тиков {t} в различных {dt} G по тикам {:.3} расхождений исхода при равном ключе {} ЦП в ходах {:.2} с; загрузок суток {} различных (монета, день) {dk}",
        w as f64 / d.max(1) as f64,
        t as f64 / dt.max(1) as f64,
        r(&WS2_MISMATCH),
        r(&WS2_NS) as f64 / 1e9,
        r(&WS2_LOADS)
    );
}
#[allow(clippy::too_many_arguments)]
fn sim_tail_e(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
    entry_slip_bps: i64,
    stop_px: Option<i64>,
) -> TickOut {
    use std::hash::{Hash, Hasher};
    WS2_REG.call_once(|| unsafe {
        libc::atexit(ws2_report);
    });
    let mut n = 0u64;
    let mut counting = || {
        let t = next();
        if t.is_some() {
            n += 1;
        }
        t
    };
    let t0 = ws2_cpu_ns();
    let out = sim_tail_e_inner(
        &mut counting,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        lc,
        notional,
        entry_slip_bps,
        stop_px,
    );
    WS2_NS.fetch_add(ws2_cpu_ns() - t0, Ordering::Relaxed);
    let coin = WS2_COIN.with(|c| c.get());
    let key: Ws2Key = (coin, entry_ms, p0, side.s(), natr_e8, notional, entry_slip_bps, stop_px, format!("{lc:?}"));
    let mut h = std::collections::hash_map::DefaultHasher::new();
    format!("{out:?}").hash(&mut h);
    let oh = h.finish();
    WS2_WALKS.fetch_add(1, Ordering::Relaxed);
    WS2_TICKS.fetch_add(n, Ordering::Relaxed);
    if let Ok(mut g) = WS2_MAP.get_or_init(|| Mutex::new(HashMap::new())).lock() {
        let v = g.entry(key).or_default();
        if let Some(e) = v.iter().find(|e| e.0 == *ex) {
            if e.1 != oh || e.2 != n {
                WS2_MISMATCH.fetch_add(1, Ordering::Relaxed);
            }
        } else {
            v.push((ex.clone(), oh, n));
            WS2_DIST.fetch_add(1, Ordering::Relaxed);
            WS2_DTICKS.fetch_add(n, Ordering::Relaxed);
        }
    }
    out
}

'''
s=s.replace(anchor,wrapper+anchor)
# 3. coin tag in Cursor::new
old='''    pub fn new(src: &'a dyn TradeSrc, name: &'a str, day: usize, idx: usize) -> Cursor<'a> {
        Cursor {'''
assert s.count(old)==1
s=s.replace(old,'''    pub fn new(src: &'a dyn TradeSrc, name: &'a str, day: usize, idx: usize) -> Cursor<'a> {
        ws2_set_coin(name);
        Cursor {''')
# 4. load counter in ZipSrc::day
old='''                        paused.work(|| {
                            fresh = true;'''
assert s.count(old)==1
s=s.replace(old,'''                        paused.work(|| {
                            fresh = true;
                            WS2_LOADS.fetch_add(1, Ordering::Relaxed);
                            if let Ok(mut g) = WS2_LOADKEYS.get_or_init(|| Mutex::new(HashSet::new())).lock() {
                                g.insert((sym.to_string(), day));
                            }''')
open(p,'wb').write(s.encode('utf-8'))
print('patched')
