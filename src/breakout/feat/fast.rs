//! TK-681 (В-173): короткие окна токсичного потока (10 с, 30 с, 1 мин, 5 мин) и ускорение ленты в секундах — таблица `FAST` по aggTrades строго до σ.
//!
//! Вход, флаги, чтение суток и `q99` — как у `tox` (TK-671): строки `sym, sig_ms[, side]`, выход `sym,sig_ms,<FAST>` в порядке входа, NaN — пусто.
//! Все окна — записи со временем `t < σ`; окно `W` — `[σ − W, σ)`. Семейство TK-600 в коротких окнах:
//! - `tfv30`, `tfn30` — перевес покупок по обороту / числу сделок за 30 с в сторону пробоя (10 с, 1 мин, 5 мин — столбцы `tfv*` / `tfn*` таблицы `flow`);
//! - `vpin300` — односторонность по 50 корзинам равного оборота за 5 мин (короче не определена: корзин с оборотом меньше 10 либо корзина = одна сделка);
//! - `bigimb10/30/60` — перевес крупных сделок (`v ≥ q99` недели) в сторону пробоя; пусто, если крупных сделок в окне нет;
//! - `sac10/30/60` — автокорреляция знака соседних сделок; пусто при числе сделок окна < 30;
//! - `mk{h}_{w}` — сдвиг цены через `h` с после сделки в её сторону, б.п., взвешен оборотом, сделки окна `w` с `t + h < σ` (не меньше 10); горизонт `h < w`;
//! - `kyle1m/amih1m` — 30 баров по 2 с, `kyle5m/amih5m` — 30 баров по 10 с, последний бар кончается на σ; для окон 10 с и 30 с не определены
//!   (30 баров короче секунды, регрессии не на чем стоять).
//!
//! Ускорение ленты: последние `s` секунд `[σ − s, σ)` против предыдущих `P` секунд `[σ − s − P, σ − s)`, `s` = 1 / 5 / 10 / 30, `P` = 60 / 300:
//! `accn` = (сделок/с в `s`) / (сделок/с в `P`), `accv` — то же по обороту; `accdn`, `accdv` — в числителе покупки минус продажи в сторону пробоя.
//! Пусто, если в `P` нет сделок (оборота) или окно начинается раньше загруженных данных.

use super::npcompat::{np_sum, ss_left_i64};
use super::tox::{
    bar_features, big_imbalance, gt0, run_cols, sign_autocorr, vpin_fused, Input, Scratch, ToxCfg,
    ToxSummary, View, Win, MK_MIN_N,
};
use super::trades::DaySrc;
use anyhow::Result;

pub const N_FAST: usize = 50;
pub const FAST: [&str; N_FAST] = [
    "tfv30",
    "tfn30",
    "vpin300",
    "bigimb10",
    "bigimb30",
    "bigimb60",
    "sac10",
    "sac30",
    "sac60",
    "mk1_10",
    "mk1_30",
    "mk5_30",
    "mk5_60",
    "mk10_60",
    "kyle1m",
    "amih1m",
    "kyle5m",
    "amih5m",
    "accn1_60",
    "accv1_60",
    "accdn1_60",
    "accdv1_60",
    "accn1_300",
    "accv1_300",
    "accdn1_300",
    "accdv1_300",
    "accn5_60",
    "accv5_60",
    "accdn5_60",
    "accdv5_60",
    "accn5_300",
    "accv5_300",
    "accdn5_300",
    "accdv5_300",
    "accn10_60",
    "accv10_60",
    "accdn10_60",
    "accdv10_60",
    "accn10_300",
    "accv10_300",
    "accdn10_300",
    "accdv10_300",
    "accn30_60",
    "accv30_60",
    "accdn30_60",
    "accdv30_60",
    "accn30_300",
    "accv30_300",
    "accdn30_300",
    "accdv30_300",
];
/// Окна ускорения: последние `s` секунд и предыдущие `P` секунд (порядок столбцов — `s`, затем `P`).
pub const ACC_S: [i64; 4] = [1, 5, 10, 30];
pub const ACC_P: [i64; 2] = [60, 300];
/// Первый столбец ускорения в `FAST`.
const ACC0: usize = 18;
/// Баров в регрессии Кайла / Амихуда коротких окон (как 30 минутных баров у `kyle30`).
const BARS: usize = 30;
const NAN: f64 = f64::NAN;

/// `(оборот, оборот покупок, записей, покупок)` записей `[lo, hi)` — последовательные суммы в порядке времени.
fn stats(w: &View, lo: usize, hi: usize) -> (f64, f64, usize, usize) {
    let (mut v, mut vb, mut nb) = (0.0f64, 0.0f64, 0usize);
    for (&x, &buy) in w.v[lo..hi].iter().zip(&w.b[lo..hi]) {
        v += x;
        if buy {
            vb += x;
            nb += 1;
        }
    }
    (v, vb, hi - lo, nb)
}

/// Ускорение ленты: `[accn, accv, accdn, accdv]` — последние `s_ms` против предыдущих `p_ms`; `s` — сторона (+1 лонг / −1 шорт).
pub(super) fn accel(w: &View, sig: i64, s_ms: i64, p_ms: i64, s: i64, avail_from: i64) -> [f64; 4] {
    let mut f = [NAN; 4];
    if sig - s_ms - p_ms < avail_from {
        return f;
    }
    let a = w.t.partition_point(|&x| x < sig - s_ms - p_ms);
    let m = w.t.partition_point(|&x| x < sig - s_ms);
    let (vp, _, n_p, _) = stats(w, a, m);
    let (vl, vbl, nl, nbl) = stats(w, m, w.t.len());
    // (X_s / s) / (X_P / P) = X_s / X_P · P / s
    let k = p_ms as f64 / s_ms as f64;
    let sf = s as f64;
    if n_p > 0 {
        f[0] = nl as f64 / n_p as f64 * k;
        f[2] = sf * (2.0 * nbl as f64 - nl as f64) / n_p as f64 * k;
    }
    if gt0(vp) {
        f[1] = vl / vp * k;
        f[3] = sf * (2.0 * vbl - vl) / vp * k;
    }
    f
}

/// Сдвиг цены после сделок (`markout` TOX13 без кэша слагаемых): записи `[σ − w, σ − h)`, цена через `h` — последняя запись с `t ≤ t_i + h`.
pub(super) fn markout_short(w: &View, sig: i64, h_ms: i64, w_ms: i64, sc: &mut Scratch) -> f64 {
    let lo = w.t.partition_point(|&x| x < sig - w_ms);
    let hi = w.t.partition_point(|&x| x < sig - h_ms);
    if hi < lo + MK_MIN_N {
        return NAN;
    }
    let n = w.t.len();
    sc.a.clear();
    let mut j = lo;
    for i in lo..hi {
        let lim = w.t[i] + h_ms;
        while j < n && w.t[j] <= lim {
            j += 1;
        }
        let (pf, p0) = (w.p[j - 1], w.p[i]);
        sc.a.push(if gt0(pf) && gt0(p0) {
            let sgn = if w.b[i] { 1e4 } else { -1e4 };
            w.v[i] * (sgn * (pf / p0).ln())
        } else {
            NAN
        });
    }
    np_sum(&sc.a) / np_sum(&w.v[lo..hi])
}

/// `FAST` одной строки; аргументы — как у `tox::tox_features`.
pub fn fast_features(
    w: &Win,
    sig: i64,
    s: i64,
    q99: Option<f64>,
    coin_first: Option<i64>,
    avail_from: i64,
    sc: &mut Scratch,
) -> [f64; N_FAST] {
    let mut f = [NAN; N_FAST];
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
    let ok = |w_ms: i64| sig - w_ms >= avail_from;
    let sf = s as f64;
    if ok(30_000) {
        let lo = vw.t.partition_point(|&x| x < sig - 30_000);
        let (vt, vb, n, nb) = stats(&vw, lo, i_hi);
        if n > 0 {
            if gt0(vt) {
                f[0] = sf * (2.0 * vb - vt) / vt;
            }
            f[1] = sf * (2.0 * nb as f64 - n as f64) / n as f64;
        }
    }
    if coin_first.is_some_and(|c| c <= sig - 300_000) && ok(300_000) {
        let lo = vw.t.partition_point(|&x| x < sig - 300_000);
        f[2] = vpin_fused(vw.v, vw.b, lo, i_hi);
    }
    for (k, w_s) in [10i64, 30, 60].into_iter().enumerate() {
        if ok(w_s * 1000) {
            f[3 + k] = big_imbalance(&vw, sig, w_s * 1000, q99, s, sc);
            f[6 + k] = sign_autocorr(&vw, sig, w_s * 1000, sc);
        }
    }
    for (k, (h_s, w_s)) in [(1i64, 10i64), (1, 30), (5, 30), (5, 60), (10, 60)]
        .into_iter()
        .enumerate()
    {
        if ok(w_s * 1000) {
            f[9 + k] = markout_short(&vw, sig, h_s * 1000, w_s * 1000, sc);
        }
    }
    for (k, bar_ms) in [2_000i64, 10_000].into_iter().enumerate() {
        let (kyle, amih) = bar_features(
            &vw,
            sig - BARS as i64 * bar_ms,
            bar_ms,
            BARS,
            avail_from,
            sc,
        );
        f[14 + 2 * k] = kyle;
        f[15 + 2 * k] = amih;
    }
    let mut k = ACC0;
    for s_s in ACC_S {
        for p_s in ACC_P {
            f[k..k + 4].copy_from_slice(&accel(&vw, sig, s_s * 1000, p_s * 1000, s, avail_from));
            k += 4;
        }
    }
    f
}

/// Таблица `FAST` по строкам входа (`tox::run_cols`).
pub fn run(cfg: &ToxCfg, rows: &[Input], src: &dyn DaySrc) -> Result<ToxSummary<N_FAST>> {
    run_cols(cfg, rows, src, &FAST, &fast_features)
}
