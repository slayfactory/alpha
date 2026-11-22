//! TK-671: токсичный поток `TOX13` — порт `tools/p42/tk600_feat.py` (`tox_features`, `minute_features`, `big_imbalance`, `sign_autocorr`, `markout`, `process_coin`)
//! без изменения правил (§13.25 п. 3): строго записи со временем `< σ`; суммы — `np_sum` (порядок numpy), накопления по минутам — последовательно (`np.bincount`).
//!
//! Вход: CSV с заголовком, столбцы `sym`, `sig_ms` (мс от `--base-date`) и необязательный `side` (`long` → +1, иначе −1; нет столбца — флаг `--side`, по умолчанию `long`;
//! знак нужен только `bigimb300` / `bigimb1800`). Выход: `sym,sig_ms,<13 TOX>` в порядке входа; NaN — пусто, число — кратчайшая круговая запись.
//! Монета читается один раз: сутки с эпизодами и предыдущие (окно ≤ 6 ч) — массивы с ценой, остальные сутки из 8 дней до первого — только часовая гистограмма `q99`.
//! Эпизоды монеты идут по времени; окно сделок суток — хвост предыдущих суток (последние 6 ч + одна запись до них, на ней держится «цена до окна»)
//! плюс текущие сутки до последнего σ: результат тот же, что у склейки двух целых суток.

use super::flow::{q99_edge, Ep, HistSink, DAY_MS, HOUR_MS, MIN_MS, NB};
use super::npcompat::{np_mean, np_sum, ss_left_i64};
use super::pdcompat::{parse_date, parse_i64};
use super::trades::{DaySrc, DayTradesP, HistDay};
use anyhow::{bail, Context, Result};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};
use std::time::Instant;

pub const TOX: [&str; 13] = [
    "vpin1h",
    "vpin6h",
    "kyle30",
    "kyle240",
    "amih30",
    "amih240",
    "bigimb300",
    "bigimb1800",
    "sac300",
    "sac1800",
    "mk10_300",
    "mk60_300",
    "mk60_1800",
];
const SAC_MIN_N: usize = 30;
pub(super) const MK_MIN_N: usize = 10;
const NAN: f64 = f64::NAN;
/// Допуск границы корзины VPIN (`tk594_feat.VPIN_TOL`).
const VPIN_TOL: f64 = 1e-12;
/// Глубина хвоста предыдущих суток: самое длинное окно вперёд-назад от начала суток — vpin 6 ч (kyle 240 мин и окна 1800 с короче).
const TAIL_MS: i64 = 6 * HOUR_MS;

/// Записи монеты в порядке времени (склейка предыдущих и текущих суток).
#[derive(Default)]
pub struct Win {
    pub t: Vec<i64>,
    pub p: Vec<f64>,
    pub v: Vec<f64>,
    pub b: Vec<bool>,
}

impl Win {
    fn clear(&mut self) {
        self.t.clear();
        self.p.clear();
        self.v.clear();
        self.b.clear();
    }

    fn push_range(&mut self, d: &DayTradesP, a: usize, z: usize) {
        self.t.extend_from_slice(&d.t[a..z]);
        self.p.extend_from_slice(&d.p[a..z]);
        self.v.extend_from_slice(&d.v[a..z]);
        self.b.extend_from_slice(&d.buy[a..z]);
    }

    /// Окно суток `d`: хвост `prev` (записи с `t ≥ d·сутки − 6 ч` и одна запись до них) и записи `cur` со временем `< max_sig`.
    fn build(&mut self, prev: Option<&DayTradesP>, cur: &DayTradesP, d: i64, max_sig: i64) {
        self.clear();
        if let Some(p) = prev {
            let cut = d * DAY_MS - TAIL_MS;
            let a = p.t.partition_point(|&x| x < cut).saturating_sub(1);
            self.push_range(p, a, p.t.len());
        }
        let n = cur.t.partition_point(|&x| x < max_sig);
        self.push_range(cur, 0, n);
    }
}

/// `x > 0` (NaN — ложь): Python `not x > 0`.
#[inline]
pub(super) fn gt0(x: f64) -> bool {
    x > 0.0
}

/// Записи со временем `< σ`.
pub(super) struct View<'a> {
    pub(super) t: &'a [i64],
    pub(super) p: &'a [f64],
    pub(super) v: &'a [f64],
    pub(super) b: &'a [bool],
}

/// Слагаемые markout по записям окна: `x[i] = v_i · mk_i` для `i ∈ [lo, hi)`, NaN — цена записи или цена через `h` не положительна.
/// Слагаемое записи не зависит от σ (`t_i + h < σ`: все записи до `t_i + h` уже до σ) — строки одних суток считают каждую запись один раз.
#[derive(Default)]
struct MkCache {
    lo: usize,
    hi: usize,
    x: Vec<f64>,
}

impl MkCache {
    /// Слагаемые записей `[lo, hi)` окна `w` для горизонта `h_ms` готовы (достраиваются слева и справа; не примыкающий отрезок заменяет прежний).
    fn ensure(&mut self, w: &View, h_ms: i64, lo: usize, hi: usize) {
        if self.x.len() < w.t.len() {
            self.x.resize(w.t.len(), 0.0);
        }
        if self.hi <= self.lo || hi < self.lo || lo > self.hi {
            self.lo = lo;
            self.hi = lo;
        }
        if lo < self.lo {
            self.fill(w, h_ms, lo, self.lo);
            self.lo = lo;
        }
        if hi > self.hi {
            self.fill(w, h_ms, self.hi, hi);
            self.hi = hi;
        }
    }

    fn fill(&mut self, w: &View, h_ms: i64, a: usize, z: usize) {
        let n = w.t.len();
        let mut j = a;
        for i in a..z {
            // последняя запись с t ≤ t_i + h (searchsorted справа − 1; t_i растёт — указатель не возвращается)
            let lim = w.t[i] + h_ms;
            while j < n && w.t[j] <= lim {
                j += 1;
            }
            let (pf, p0) = (w.p[j - 1], w.p[i]);
            self.x[i] = if gt0(pf) && gt0(p0) {
                let sgn = if w.b[i] { 1e4 } else { -1e4 };
                w.v[i] * (sgn * (pf / p0).ln())
            } else {
                NAN
            };
        }
    }
}

/// Рабочие буферы строки (переиспользуются, без выделений на строку после прогрева). `new_window` — при смене окна сделок (`Win`).
#[derive(Default)]
pub struct Scratch {
    edges: Vec<usize>,
    r: Vec<f64>,
    vk: Vec<f64>,
    qk: Vec<f64>,
    pub(super) a: Vec<f64>,
    pub(super) b: Vec<f64>,
    mk: [MkCache; 2],
}

impl Scratch {
    /// Окно сделок сменилось: слагаемые markout прежнего окна недействительны.
    pub fn new_window(&mut self) {
        for c in &mut self.mk {
            c.lo = 0;
            c.hi = 0;
        }
    }
}

/// `flow::vpin_value` без промежуточных массивов: проход 1 — общий оборот (`cumsum[-1]`), проход 2 — накопления на границах корзин; те же операции в том же порядке.
pub(super) fn vpin_fused(v: &[f64], b: &[bool], lo: usize, hi: usize) -> f64 {
    let (vv, bb) = (&v[lo..hi], &b[lo..hi]);
    let Some((&first, rest)) = vv.split_first() else {
        return NAN;
    };
    let total = rest.iter().fold(first, |acc, &x| acc + x);
    if !gt0(total) {
        return NAN;
    }
    let mut tg = [0.0f64; 50];
    for (k, t) in tg.iter_mut().enumerate().skip(1) {
        *t = k as f64 * total / 50.0 * (1.0 - VPIN_TOL);
    }
    // pv[k], pb[k] — накопленный оборот / оборот покупок на записи, закрывшей границу k (первая запись с накопленным ≥ цели k)
    let (mut pv, mut pb) = ([0.0f64; 51], [0.0f64; 51]);
    let (mut cum, mut cb, mut k) = (0.0f64, 0.0f64, 1usize);
    for (i, (&x, &m)) in vv.iter().zip(bb).enumerate() {
        let xb = if m { x } else { 0.0 };
        (cum, cb) = if i == 0 { (x, xb) } else { (cum + x, cb + xb) };
        while k < 50 && cum >= tg[k] {
            pv[k] = cum;
            pb[k] = cb;
            k += 1;
        }
    }
    (pv[50], pb[50]) = (cum, cb);
    let mut r = [0.0f64; 50];
    let mut n = 0;
    for k in 0..50 {
        let (tot, bu) = (pv[k + 1] - pv[k], pb[k + 1] - pb[k]);
        if tot > 0.0 {
            r[n] = (2.0 * bu - tot).abs() / tot;
            n += 1;
        }
    }
    if n < 10 {
        return NAN;
    }
    np_mean(&r[..n])
}

/// `(kyle_W, amih_W)` по `W` полным минутам перед σ (§13.25 п. 3.2).
fn minute_features(w: &View, sig: i64, wm: usize, avail_from: i64, sc: &mut Scratch) -> (f64, f64) {
    let m = sig.div_euclid(MIN_MS);
    bar_features(w, (m - wm as i64) * MIN_MS, MIN_MS, wm, avail_from, sc)
}

/// `(kyle, amih)` по `wm` барам длины `bar_ms` от `eb0` (бар `j` — записи `[eb0 + j·bar, eb0 + (j + 1)·bar)`); формулы §13.25 п. 3.2.
/// Минутные признаки TOX13 — `bar_ms` = минута, `eb0` — на границе минуты; короткие окна TK-681 — бары в секундах, последний кончается на σ.
pub(super) fn bar_features(
    w: &View,
    eb0: i64,
    bar_ms: i64,
    wm: usize,
    avail_from: i64,
    sc: &mut Scratch,
) -> (f64, f64) {
    if eb0 < avail_from {
        return (NAN, NAN);
    }
    // edges[j] — первая запись с t ≥ границы j (границы возрастают: поиск с прежней позиции)
    sc.edges.clear();
    let mut from = 0usize;
    for j in 0..=wm {
        let x = eb0 + j as i64 * bar_ms;
        from += w.t[from..].partition_point(|&q| q < x);
        sc.edges.push(from);
    }
    if sc.edges[0] == 0 {
        return (NAN, NAN); // нет цены до окна
    }
    sc.r.clear();
    let mut p_prev = w.p[sc.edges[0] - 1];
    if !gt0(p_prev) {
        return (NAN, NAN);
    }
    for j in 1..=wm {
        let pj = w.p[sc.edges[j] - 1]; // цена конца бара j−1
        if !gt0(pj) {
            return (NAN, NAN);
        }
        sc.r.push(1e4 * (pj / p_prev).ln());
        p_prev = pj;
    }
    // np.bincount: по каждому бару последовательная сумма в порядке записей
    sc.vk.clear();
    sc.qk.clear();
    let mut n_active = 0usize;
    for j in 0..wm {
        let (mut sv, mut sq) = (0.0f64, 0.0f64);
        for i in sc.edges[j]..sc.edges[j + 1] {
            let x = w.v[i];
            sv += x;
            sq += if w.b[i] { x } else { -x };
        }
        n_active += usize::from(sv > 0.0);
        sc.vk.push(sv);
        sc.qk.push(sq);
    }
    if n_active < wm.div_ceil(3) {
        return (NAN, NAN);
    }
    let qm = np_mean(&sc.qk);
    sc.a.clear();
    sc.a.extend(sc.qk.iter().map(|&q| q - qm)); // qd
    sc.b.clear();
    sc.b.extend(sc.a.iter().map(|&x| x * x));
    let den = np_sum(&sc.b);
    let mut kyle = NAN;
    if den > 0.0 {
        let rm = np_mean(&sc.r);
        sc.b.clear();
        sc.b.extend(sc.a.iter().zip(&sc.r).map(|(&q, &r)| q * (r - rm)));
        let lam = np_sum(&sc.b) / den;
        kyle = lam * np_mean(&sc.vk);
    }
    sc.b.clear();
    for (&r, &vk) in sc.r.iter().zip(&sc.vk) {
        if vk > 0.0 {
            sc.b.push(r.abs() / (vk / 1e6));
        }
    }
    (kyle, np_mean(&sc.b))
}

pub(super) fn big_imbalance(
    w: &View,
    sig: i64,
    w_ms: i64,
    q99: Option<f64>,
    s: i64,
    sc: &mut Scratch,
) -> f64 {
    let Some(q) = q99 else {
        return NAN;
    };
    let lo = w.t.partition_point(|&x| x < sig - w_ms);
    sc.a.clear();
    sc.b.clear();
    for (&x, &buy) in w.v[lo..].iter().zip(&w.b[lo..]) {
        if x >= q {
            sc.a.push(x);
            if buy {
                sc.b.push(x);
            }
        }
    }
    let vt = np_sum(&sc.a);
    if !gt0(vt) {
        return NAN;
    }
    s as f64 * (2.0 * np_sum(&sc.b) - vt) / vt
}

pub(super) fn sign_autocorr(w: &View, sig: i64, w_ms: i64, sc: &mut Scratch) -> f64 {
    let lo = w.t.partition_point(|&x| x < sig - w_ms);
    let n = w.b.len() - lo;
    if n < SAC_MIN_N {
        return NAN;
    }
    sc.a.clear();
    sc.a.extend(w.b[lo..].iter().map(|&b| if b { 1.0 } else { -1.0 }));
    let mean = np_mean(&sc.a);
    for x in sc.a.iter_mut() {
        *x -= mean; // dc
    }
    sc.b.clear();
    sc.b.extend(sc.a.iter().map(|&x| x * x));
    let den = np_sum(&sc.b);
    if !gt0(den) {
        return NAN;
    }
    sc.b.clear();
    sc.b.extend(sc.a.windows(2).map(|p| p[0] * p[1]));
    np_sum(&sc.b) / den
}

fn markout(w: &View, sig: i64, h_ms: i64, w_ms: i64, sc: &mut Scratch) -> f64 {
    let lo = w.t.partition_point(|&x| x < sig - w_ms);
    let hi = w.t.partition_point(|&x| x < sig - h_ms); // записи с t + h < σ
    if hi < lo + MK_MIN_N {
        return NAN;
    }
    let c = &mut sc.mk[usize::from(h_ms != 10_000)];
    c.ensure(w, h_ms, lo, hi);
    // NaN слагаемого (цена ≤ 0) даёт NaN суммы — как явный возврат NaN у Python
    np_sum(&c.x[lo..hi]) / np_sum(&w.v[lo..hi])
}

/// TOX13 одной строки. `t, p, v, b` — записи монеты в порядке времени (могут содержать `t ≥ σ` — отрезаются); `avail_from` — начало загруженных данных (мс).
pub fn tox_features(
    w: &Win,
    sig: i64,
    s: i64,
    q99: Option<f64>,
    coin_first: Option<i64>,
    avail_from: i64,
    sc: &mut Scratch,
) -> [f64; 13] {
    let mut f = [NAN; 13];
    let i_hi = ss_left_i64(&w.t, sig);
    if i_hi == 0 {
        return f;
    }
    let vw = View {
        t: &w.t[..i_hi],
        p: &w.p[..i_hi],
        v: &w.v[..i_hi],
        b: &w.b[..i_hi],
    };
    for (k, w_h) in [1i64, 6].into_iter().enumerate() {
        let wm = w_h * HOUR_MS;
        if coin_first.is_some_and(|c| c <= sig - wm) && sig - wm >= avail_from {
            let lo = vw.t.partition_point(|&x| x < sig - wm);
            f[k] = vpin_fused(vw.v, vw.b, lo, i_hi);
        }
    }
    for (wm, kk, ka) in [(30usize, 2usize, 4usize), (240, 3, 5)] {
        let (kyle, amih) = minute_features(&vw, sig, wm, avail_from, sc);
        f[kk] = kyle;
        f[ka] = amih;
    }
    for (w_s, k) in [(300i64, 6usize), (1800, 7)] {
        if sig - w_s * 1000 >= avail_from {
            f[k] = big_imbalance(&vw, sig, w_s * 1000, q99, s, sc);
        }
    }
    for (w_s, k) in [(300i64, 8usize), (1800, 9)] {
        if sig - w_s * 1000 >= avail_from {
            f[k] = sign_autocorr(&vw, sig, w_s * 1000, sc);
        }
    }
    for (h_s, w_s, k) in [(10i64, 300i64, 10usize), (60, 300, 11), (60, 1800, 12)] {
        if sig - w_s * 1000 >= avail_from {
            f[k] = markout(&vw, sig, h_s * 1000, w_s * 1000, sc);
        }
    }
    f
}

/// Часовая гистограмма `log10 v` суток `d` (записи вне суток прижимаются к краям — как `hist_add_day`).
fn hist_add_day_p(h: &mut [i32], d: i64, c: &DayTradesP) {
    let mut sink = HistSink::new(h);
    let base = d * DAY_MS;
    for (&t, &v) in c.t.iter().zip(&c.v) {
        sink.add((t - base).clamp(0, DAY_MS - 1) as u32, v);
    }
}

/// Признаки одной строки: `(окно, σ, сторона, q99, первая запись монеты, начало загруженных данных, буферы)` → `N` столбцов.
pub(super) type RowFn<const N: usize> =
    dyn Fn(&Win, i64, i64, Option<f64>, Option<i64>, i64, &mut Scratch) -> [f64; N] + Sync;

/// Результат одной монеты (`N` столбцов; TOX13 — 13).
pub struct CoinTox<const N: usize = 13> {
    pub rows: Vec<(usize, [f64; N])>,
    pub missing_trade_days: u64,
    /// Строки на сутки без файла (признаки пусты).
    pub rows_on_missing_day: u64,
    pub unsorted_days: u64,
}

/// `process_coin` `tk600_feat`: сутки от `dmin − 8` (гистограмма `q99`) до `dmax`; эпизоды `eps` — любого порядка.
pub fn tox_coin(src: &dyn DaySrc, sym: &str, eps: &[Ep]) -> Result<CoinTox> {
    coin_rows(src, sym, eps, &tox_features)
}

/// Проход монеты `tox_coin` с признаками строки `f` (TOX13 или короткие окна TK-681): чтение суток, окно сделок, `q99` — общие.
pub(super) fn coin_rows<const N: usize>(
    src: &dyn DaySrc,
    sym: &str,
    eps: &[Ep],
    f: &RowFn<N>,
) -> Result<CoinTox<N>> {
    let days: Vec<i64> = eps.iter().map(|e| e.sig.div_euclid(DAY_MS)).collect();
    let dmin = *days.iter().min().unwrap_or(&0);
    let dmax = *days.iter().max().unwrap_or(&0);
    let h0 = (dmin - 8) * 24;
    let nrows = ((dmax - (dmin - 8) + 1) * 24) as usize;
    let mut hist = vec![0i32; nrows * NB];
    let mut by_day: BTreeMap<i64, Vec<usize>> = BTreeMap::new();
    for (i, &d) in days.iter().enumerate() {
        by_day.entry(d).or_default().push(i);
    }
    for idx in by_day.values_mut() {
        idx.sort_by_key(|&i| eps[i].sig);
    }
    let mut out = CoinTox {
        rows: Vec::with_capacity(eps.len()),
        missing_trade_days: 0,
        rows_on_missing_day: 0,
        unsorted_days: 0,
    };
    let mut prev: Option<DayTradesP> = None;
    let mut first_seen = false;
    let mut coin_first: Option<i64> = None;
    let mut win = Win::default();
    let mut sc = Scratch::default();
    let mut hh = vec![0i64; NB];
    let mut q_cache: Option<(i64, Option<f64>)> = None;
    for d in (dmin - 8)..=dmax {
        let hrow = ((d * 24 - h0) as usize) * NB;
        // массивы суток нужны, если в них или в следующих сутках есть эпизод (хвост предыдущих суток); иначе суткам нужна одна гистограмма
        let arrays = by_day.contains_key(&d) || by_day.contains_key(&(d + 1));
        let (cur, seen): (Option<DayTradesP>, Option<HistDay>) = if arrays {
            let cur = src.day_p(sym, d)?;
            if let Some(c) = &cur {
                hist_add_day_p(&mut hist[hrow..hrow + 24 * NB], d, c);
            }
            let seen = cur.as_ref().map(|c| HistDay {
                unsorted: c.unsorted,
                first_t: c.t.first().copied(),
            });
            (cur, seen)
        } else {
            (None, src.day_hist(sym, d, &mut hist[hrow..hrow + 24 * NB])?)
        };
        match &seen {
            Some(c) => {
                if !first_seen {
                    first_seen = true;
                    coin_first = c.first_t;
                }
                out.unsorted_days += u64::from(c.unsorted);
            }
            None => {
                if first_seen && d >= dmin - 1 {
                    out.missing_trade_days += 1;
                }
            }
        }
        if let Some(idx) = by_day.get(&d) {
            match &cur {
                None => {
                    // сутки строки без файла — признаки пусты (дыра данных)
                    out.rows_on_missing_day += idx.len() as u64;
                    out.rows.extend(idx.iter().map(|&e| (eps[e].row, [NAN; N])));
                }
                Some(c) => {
                    let max_sig = idx.iter().map(|&e| eps[e].sig).max().unwrap_or(0);
                    win.build(prev.as_ref(), c, d, max_sig);
                    sc.new_window();
                    let avail_from = if prev.is_none() {
                        d * DAY_MS
                    } else {
                        (d - 1) * DAY_MS
                    };
                    for &e in idx {
                        let ep = &eps[e];
                        let h_end = ep.sig.div_euclid(HOUR_MS);
                        let q99 = match q_cache {
                            Some((h, q)) if h == h_end => q,
                            _ => {
                                let lo = (h_end - 168 - h0).max(0) as usize;
                                let hi = ((h_end - h0).max(0) as usize).min(nrows);
                                hh.fill(0);
                                let mut n_hours = 0;
                                for r in lo..hi.max(lo) {
                                    let row = &hist[r * NB..(r + 1) * NB];
                                    let mut s = 0i64;
                                    for (a, &cnt) in hh.iter_mut().zip(row) {
                                        *a += i64::from(cnt);
                                        s += i64::from(cnt);
                                    }
                                    if s > 0 {
                                        n_hours += 1;
                                    }
                                }
                                let q = if n_hours >= 24 { q99_edge(&hh) } else { None };
                                q_cache = Some((h_end, q));
                                q
                            }
                        };
                        out.rows.push((
                            ep.row,
                            f(&win, ep.sig, ep.side, q99, coin_first, avail_from, &mut sc),
                        ));
                    }
                }
            }
        }
        prev = cur;
    }
    Ok(out)
}

pub struct ToxCfg {
    pub eps: PathBuf,
    pub out: PathBuf,
    pub base_date: String,
    pub threads: usize,
    pub coins: Option<Vec<String>>,
    /// Сторона, если во входе нет столбца `side`: +1 лонг, −1 шорт.
    pub side: i64,
}

/// Строка входа.
#[derive(Debug, Clone)]
pub struct Input {
    pub sym: String,
    pub sig_ms: i64,
    pub side: i64,
}

pub fn read_input(cfg: &ToxCfg) -> Result<Vec<Input>> {
    let path = &cfg.eps;
    let mut rd = csv::ReaderBuilder::new()
        .has_headers(true)
        .flexible(true)
        .from_path(path)
        .with_context(|| path.display().to_string())?;
    let head = rd.headers()?.clone();
    let find = |n: &str| head.iter().position(|h| h == n);
    let is = find("sym").with_context(|| format!("{}: нет столбца sym", path.display()))?;
    let it = find("sig_ms").with_context(|| format!("{}: нет столбца sig_ms", path.display()))?;
    let isd = find("side");
    let mut out = Vec::new();
    for (ln, rec) in rd.records().enumerate() {
        let rec = rec?;
        let sym = rec.get(is).unwrap_or("").trim();
        if cfg
            .coins
            .as_ref()
            .is_some_and(|c| !c.iter().any(|x| x == sym))
        {
            continue;
        }
        let sig_ms = parse_i64(rec.get(it).unwrap_or("").trim().as_bytes())
            .with_context(|| format!("{}: строка {}", path.display(), ln + 2))?;
        let side = match isd {
            Some(i) => {
                if rec.get(i).unwrap_or("").trim() == "long" {
                    1
                } else {
                    -1
                }
            }
            None => cfg.side,
        };
        out.push(Input {
            sym: sym.to_string(),
            sig_ms,
            side,
        });
    }
    Ok(out)
}

/// Первые сутки, которые запросит счёт (`dmin − 8` по монетам) — на них ставится сетка склада.
pub fn first_day(cfg: &ToxCfg, rows: &[Input]) -> Result<i64> {
    let base = parse_date(&cfg.base_date)? * DAY_MS;
    rows.iter()
        .map(|r| (base + r.sig_ms).div_euclid(DAY_MS) - 8)
        .min()
        .context("пустой вход")
}

#[derive(Debug, Clone)]
pub struct ToxSummary<const N: usize = 13> {
    pub rows: usize,
    pub coins: usize,
    pub seconds: f64,
    pub missing_trade_days: u64,
    pub rows_on_missing_day: u64,
    pub unsorted_days: u64,
    /// Доля непустых значений по столбцам `names`.
    pub coverage: [f64; N],
    pub names: [&'static str; N],
}

impl<const N: usize> ToxSummary<N> {
    pub fn json(&self) -> String {
        let cov: Vec<String> = self
            .names
            .iter()
            .zip(&self.coverage)
            .map(|(k, c)| format!("\"{k}\":{c:.4}"))
            .collect();
        format!(
            "{{\"rows\":{},\"coins\":{},\"seconds\":{:.1},\"missing_trade_days\":{},\"rows_on_missing_day\":{},\"unsorted_days\":{},\"coverage\":{{{}}}}}",
            self.rows,
            self.coins,
            self.seconds,
            self.missing_trade_days,
            self.rows_on_missing_day,
            self.unsorted_days,
            cov.join(",")
        )
    }
}

pub fn run(cfg: &ToxCfg, rows: &[Input], src: &dyn DaySrc) -> Result<ToxSummary> {
    run_cols(cfg, rows, src, &TOX, &tox_features)
}

/// `run` для таблицы из `N` столбцов `names` с признаками строки `f`: выход `sym,sig_ms,<names>` в порядке входа.
pub(super) fn run_cols<const N: usize>(
    cfg: &ToxCfg,
    rows: &[Input],
    src: &dyn DaySrc,
    names: &[&'static str; N],
    f: &RowFn<N>,
) -> Result<ToxSummary<N>> {
    let t0 = Instant::now();
    if rows.is_empty() {
        bail!("пустой вход: {}", cfg.eps.display());
    }
    let base_ms = parse_date(&cfg.base_date)? * DAY_MS;
    let mut by: BTreeMap<&str, Vec<Ep>> = BTreeMap::new();
    for (i, r) in rows.iter().enumerate() {
        by.entry(r.sym.as_str()).or_default().push(Ep {
            row: i,
            sig: base_ms + r.sig_ms,
            side: r.side,
            turn24h: NAN,
        });
    }
    let mut jobs: Vec<(&str, Vec<Ep>)> = by.into_iter().collect();
    jobs.sort_by_key(|(_, e)| std::cmp::Reverse(e.len()));
    eprintln!("строк {}; монет {}", rows.len(), jobs.len());
    let next = AtomicUsize::new(0);
    let finished = AtomicUsize::new(0);
    let results: Mutex<Vec<CoinTox<N>>> = Mutex::new(Vec::with_capacity(jobs.len()));
    let err: Mutex<Option<anyhow::Error>> = Mutex::new(None);
    std::thread::scope(|sc| {
        for _ in 0..cfg.threads.max(1) {
            sc.spawn(|| loop {
                let j = next.fetch_add(1, Ordering::SeqCst);
                let Some((sym, eps)) = jobs.get(j) else {
                    break;
                };
                match coin_rows(src, sym, eps, f).with_context(|| format!("монета {sym}")) {
                    Ok(c) => results
                        .lock()
                        .unwrap_or_else(PoisonError::into_inner)
                        .push(c),
                    Err(e) => {
                        *err.lock().unwrap_or_else(PoisonError::into_inner) = Some(e);
                        break;
                    }
                }
                let n = finished.fetch_add(1, Ordering::SeqCst) + 1;
                if n.is_multiple_of(20) {
                    eprintln!(
                        "{n}/{} монет, {:.0} с",
                        jobs.len(),
                        t0.elapsed().as_secs_f64()
                    );
                }
            });
        }
    });
    if let Some(e) = err.into_inner().unwrap_or_else(PoisonError::into_inner) {
        return Err(e);
    }
    let mut table = vec![[NAN; N]; rows.len()];
    let (mut missing, mut on_missing, mut unsorted) = (0u64, 0u64, 0u64);
    for c in results.into_inner().unwrap_or_else(PoisonError::into_inner) {
        missing += c.missing_trade_days;
        on_missing += c.rows_on_missing_day;
        unsorted += c.unsorted_days;
        for (row, vals) in c.rows {
            table[row] = vals;
        }
    }
    let mut text = format!("sym,sig_ms,{}\n", names.join(","));
    let mut cov = [0usize; N];
    for (r, vals) in rows.iter().zip(&table) {
        let _ = write!(text, "{},{}", r.sym, r.sig_ms);
        for (k, &x) in vals.iter().enumerate() {
            text.push(',');
            if !x.is_nan() {
                cov[k] += 1;
                let _ = write!(text, "{x:?}");
            }
        }
        text.push('\n');
    }
    std::fs::write(&cfg.out, text).with_context(|| cfg.out.display().to_string())?;
    let mut coverage = [0.0; N];
    for (c, n) in coverage.iter_mut().zip(cov) {
        *c = n as f64 / rows.len() as f64;
    }
    Ok(ToxSummary {
        rows: rows.len(),
        coins: jobs.len(),
        seconds: t0.elapsed().as_secs_f64(),
        missing_trade_days: missing,
        rows_on_missing_day: on_missing,
        unsorted_days: unsorted,
        coverage,
        names: *names,
    })
}
