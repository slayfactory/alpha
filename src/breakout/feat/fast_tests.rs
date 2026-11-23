//! Тесты `fast` (TK-681): края окон ускорения ленты и коротких окон потока (ручные значения), короткий markout и бары Кайла / Амихуда против прямых формул,
//! связь с TOX13 (те же формулы на окнах TOX13 — побитово), отсутствие заглядывания за σ, хвост предыдущих суток, прогон по CSV.

use super::fast::{
    accel, fast_features, markout_short, run, sacd_features, ACC_P, ACC_S, FAST, N_FAST, N_SACD,
    SACD,
};
use super::flow::{Ep, DAY_MS, HOUR_MS, MIN_MS};
use super::tox::{
    bar_features, coin_rows, read_input, tox_features, vpin_fused, Scratch, ToxCfg, View, Win, TOX,
};
use super::tox_tests::{close, gen_day, naive_markout, same, win_from, win_of, MemSrc, Rng};
use anyhow::Result;
use std::collections::BTreeMap;

/// 2026-09-01, сутки от 1970.
const D0: i64 = 20_697;
const NAN: f64 = f64::NAN;

fn fi(k: &str) -> usize {
    FAST.iter().position(|x| *x == k).unwrap()
}

fn ff(w: &Win, sig: i64, s: i64, q99: Option<f64>, avail_from: i64) -> [f64; N_FAST] {
    fast_features(w, sig, s, q99, Some(0), avail_from, &mut Scratch::default())
}

fn view(w: &Win, sig: i64) -> View<'_> {
    let n = w.t.partition_point(|&x| x < sig);
    View {
        t: &w.t[..n],
        p: &w.p[..n],
        v: &w.v[..n],
        b: &w.b[..n],
    }
}

/// Плотные сутки: 40 000 записей в 09:00–11:00 (≈ 5,5 сделки/с).
fn dense(seed: u64) -> Win {
    let mut rng = Rng(seed);
    win_of(&[&gen_day(
        &mut rng,
        D0,
        40_000,
        9 * HOUR_MS,
        11 * HOUR_MS,
        100.0,
    )])
}

#[test]
fn column_names_are_unique_and_acceleration_order_follows_windows() {
    let set: std::collections::HashSet<&&str> = FAST.iter().collect();
    assert_eq!(set.len(), N_FAST);
    assert!(
        FAST.iter().all(|k| !TOX.contains(k)),
        "имена не пересекаются с TOX13"
    );
    let mut k = fi("accn1_60");
    for s in ACC_S {
        for p in ACC_P {
            for pre in ["accn", "accv", "accdn", "accdv"] {
                assert_eq!(FAST[k], format!("{pre}{s}_{p}"));
                k += 1;
            }
        }
    }
    assert_eq!(k, N_FAST);
}

#[test]
fn no_record_before_sigma_gives_all_nan() {
    let sig = D0 * DAY_MS + 10 * HOUR_MS;
    let w = win_from(&[sig, sig + 1], &[1.0, 1.0], &[1.0, 1.0], &[true, true]);
    assert!(ff(&w, sig, 1, Some(0.0), 0).iter().all(|x| x.is_nan()));
    assert!(ff(&Win::default(), sig, 1, Some(0.0), 0)
        .iter()
        .all(|x| x.is_nan()));
}

/// Шесть записей вокруг краёв окон `s = 5 с`, `P = 60 с`.
fn acc_win(sig: i64) -> Win {
    win_from(
        &[
            sig - 65_001, // раньше окна P = 60 с, внутри P = 300 с
            sig - 65_000, // ровно начало окна P (входит)
            sig - 5_001,  // последняя мс окна P
            sig - 5_000,  // ровно начало последних 5 с (входит)
            sig - 1,      // последняя мс перед σ
            sig,          // σ — вне всех окон
        ],
        &[1.0; 6],
        &[100.0, 3.0, 5.0, 4.0, 2.0, 1000.0],
        &[true, true, false, true, false, true],
    )
}

#[test]
fn acceleration_hand_values_window_edges_and_sign() {
    let sig = D0 * DAY_MS + 10 * HOUR_MS;
    let w = acc_win(sig);
    let f = ff(&w, sig, 1, None, 0);
    // 5 с против 60 с: P — 2 сделки, оборот 8; последние 5 с — 2 сделки, оборот 6, покупок 1 (оборот 4); P / s = 12
    assert_eq!(f[fi("accn5_60")], 12.0);
    assert_eq!(f[fi("accv5_60")], 9.0);
    assert_eq!(f[fi("accdn5_60")], 0.0);
    assert_eq!(f[fi("accdv5_60")], 3.0);
    // 5 с против 300 с: в P ещё запись −65 001 (3 сделки, оборот 108); P / s = 60
    assert_eq!(f[fi("accn5_300")], 40.0);
    assert!(close(f[fi("accv5_300")], 6.0 / 108.0 * 60.0, 1e-12));
    // 1 с против 60 с: последняя секунда — одна продажа (оборот 2); P = [σ − 61 с, σ − 1 с) — 2 сделки, оборот 9
    assert_eq!(f[fi("accn1_60")], 30.0);
    assert_eq!(f[fi("accdn1_60")], -30.0);
    assert!(close(f[fi("accv1_60")], 2.0 / 9.0 * 60.0, 1e-12));
    assert!(close(f[fi("accdv1_60")], -2.0 / 9.0 * 60.0, 1e-12));
    // шорт: направленные меняют знак, ненаправленные — нет
    let g = ff(&w, sig, -1, None, 0);
    assert_eq!(g[fi("accdv5_60")], -3.0);
    assert_eq!(g[fi("accdn1_60")], 30.0);
    assert_eq!((g[fi("accn5_60")], g[fi("accv5_60")]), (12.0, 9.0));
    // запись ровно на σ в счёт не входит; на σ + 1 мс — уже входит
    let h = ff(&w, sig + 1, 1, None, 0);
    assert!(h[fi("accv5_60")] > 1000.0);
}

#[test]
fn acceleration_nan_rules() {
    let sig = D0 * DAY_MS + 10 * HOUR_MS;
    let w = acc_win(sig);
    let vw = view(&w, sig);
    // начало окна раньше загруженных данных — пусто
    assert!(accel(&vw, sig, 5_000, 60_000, 1, sig - 65_000)[0].is_finite());
    assert!(accel(&vw, sig, 5_000, 60_000, 1, sig - 64_999)
        .iter()
        .all(|x| x.is_nan()));
    let f = ff(&w, sig, 1, None, sig - 65_000);
    assert!(f[fi("accn5_60")].is_finite() && f[fi("accn5_300")].is_nan());
    // в предыдущем окне нет сделок — пусто (деления на ноль нет)
    let only_last = win_from(&[sig - 100], &[1.0], &[7.0], &[true]);
    assert!(accel(&view(&only_last, sig), sig, 5_000, 60_000, 1, 0)
        .iter()
        .all(|x| x.is_nan()));
    // лента замолчала в последние секунды — ноль, а не пусто
    let only_prev = win_from(&[sig - 30_000], &[1.0], &[7.0], &[true]);
    assert_eq!(
        accel(&view(&only_prev, sig), sig, 5_000, 60_000, 1, 0),
        [0.0; 4]
    );
    // нулевой оборот предыдущего окна: счёт сделок есть, оборот пуст
    let zero_v = win_from(
        &[sig - 30_000, sig - 100],
        &[1.0; 2],
        &[0.0, 7.0],
        &[true; 2],
    );
    let a = accel(&view(&zero_v, sig), sig, 5_000, 60_000, 1, 0);
    assert!(a[0] == 12.0 && a[2] == 12.0 && a[1].is_nan() && a[3].is_nan());
}

#[test]
fn one_sidedness_30s_hand_values_and_edges() {
    let sig = D0 * DAY_MS + 10 * HOUR_MS;
    let w = win_from(
        &[sig - 30_001, sig - 30_000, sig - 1, sig],
        &[1.0; 4],
        &[500.0, 1.0, 3.0, 900.0],
        &[true, false, true, true],
    );
    let f = ff(&w, sig, 1, None, 0);
    assert_eq!((f[fi("tfv30")], f[fi("tfn30")]), (0.5, 0.0));
    let g = ff(&w, sig, -1, None, 0);
    assert_eq!((g[fi("tfv30")], g[fi("tfn30")]), (-0.5, 0.0));
    // окно раньше загруженных данных — пусто
    assert!(ff(&w, sig, 1, None, sig - 29_999)[fi("tfv30")].is_nan());
}

#[test]
fn short_big_imbalance_and_sign_autocorr_hand_values() {
    let sig = D0 * DAY_MS + 10 * HOUR_MS;
    // крупная продажа за 20 с до σ и 30 сделок с чередующимся знаком в последние 9 с (первая — крупная покупка 20, вторая — крупная продажа 10)
    let (mut t, mut v, mut b) = (vec![sig - 20_000], vec![30.0], vec![false]);
    for i in 0..30i64 {
        t.push(sig - 9_000 + i * 100);
        v.push(match i {
            0 => 20.0,
            1 => 10.0,
            _ => 1.0,
        });
        b.push(i % 2 == 0);
    }
    let w = win_from(&t, &vec![1.0; t.len()], &v, &b);
    let f = ff(&w, sig, 1, Some(10.0), 0);
    assert!(
        close(f[fi("bigimb10")], 1.0 / 3.0, 1e-12),
        "(2·20 − 30) / 30"
    );
    assert!(
        close(f[fi("bigimb30")], -1.0 / 3.0, 1e-12),
        "(2·20 − 60) / 60"
    );
    assert_eq!(f[fi("bigimb30")].to_bits(), f[fi("bigimb60")].to_bits());
    assert!(close(f[fi("sac10")], -29.0 / 30.0, 1e-12));
    assert!(f[fi("sac30")].is_finite() && f[fi("sac30")] != f[fi("sac10")]);
    let g = ff(&w, sig, -1, Some(10.0), 0);
    assert!(close(g[fi("bigimb10")], -1.0 / 3.0, 1e-12));
    assert_eq!(g[fi("sac10")].to_bits(), f[fi("sac10")].to_bits());
    // нет порога недели или нет крупных сделок в окне — пусто
    assert!(ff(&w, sig, 1, None, 0)[fi("bigimb10")].is_nan());
    assert!(ff(&w, sig, 1, Some(1e6), 0)[fi("bigimb10")].is_nan());
    // 29 сделок в окне 10 с — автокорреляция пуста (минимум 30)
    let w29 = win_from(&t[..30], &vec![1.0; 30], &v[..30], &b[..30]);
    assert!(ff(&w29, sig, 1, Some(10.0), 0)[fi("sac10")].is_nan());
}

/// Сторона записи по её номеру в окне.
type Side = fn(i64) -> bool;

/// TK-690: `sac10/30/60` пусты ровно когда окно не покрыто данными, записей окна < 30 или все записи одной стороны; `sacd` считает n и покупки независимо.
#[test]
fn sac_is_empty_exactly_for_uncovered_few_trades_or_one_side() {
    let sig = 10 * HOUR_MS;
    let ix = |k: &str| SACD.iter().position(|x| *x == k).unwrap();
    // записи за 70 с до σ шагом 100 мс; сторона — по номеру записи: чередование / все покупки / все продажи / редкие покупки
    let cases: [(i64, Side); 8] = [
        (29, |i| i % 2 == 0),
        (30, |i| i % 2 == 0),
        (30, |_| true),
        (30, |_| false),
        (700, |i| i % 2 == 0),
        (700, |_| true),
        (700, |i| i % 97 == 0),
        (0, |_| true),
    ];
    let mut seen = [0usize; 2]; // пустых / непустых по всем окнам
    for (n, side) in cases {
        for step in [100i64, 2_000] {
            let t: Vec<i64> = (0..n).map(|i| sig - 1 - i * step).rev().collect();
            let b: Vec<bool> = (0..n).map(side).collect();
            let w = win_from(&t, &vec![1.0; t.len()], &vec![1.0; t.len()], &b);
            for gap in [3_000i64, 20_000, 40_000, 100_000] {
                let avail_from = sig - gap;
                let f = fast_features(
                    &w,
                    sig,
                    1,
                    Some(10.0),
                    Some(0),
                    avail_from,
                    &mut Scratch::default(),
                );
                let d = sacd_features(
                    &w,
                    sig,
                    1,
                    Some(10.0),
                    Some(0),
                    avail_from,
                    &mut Scratch::default(),
                );
                assert_eq!(d.len(), N_SACD);
                assert_eq!(d[ix("gap")], gap as f64);
                for (w_s, sac, nk, bk) in [
                    (10i64, "sac10", "n10", "nb10"),
                    (30, "sac30", "n30", "nb30"),
                    (60, "sac60", "n60", "nb60"),
                ] {
                    let (cnt, buys) = (d[ix(nk)], d[ix(bk)]);
                    let want = t.iter().filter(|&&x| x >= sig - w_s * 1000).count() as f64;
                    assert_eq!(cnt, want, "n окна {w_s} с");
                    let empty = gap < w_s * 1000 || cnt < 30.0 || buys == 0.0 || buys == cnt;
                    assert_eq!(
                        f[fi(sac)].is_nan(),
                        empty,
                        "{sac}: n={n} шаг={step} gap={gap} n_окна={cnt} покупок={buys}"
                    );
                    seen[usize::from(!empty)] += 1;
                }
            }
        }
    }
    assert!(seen[0] > 20 && seen[1] > 5, "покрыты оба исхода: {seen:?}");
}

/// Прямые формулы Кайла / Амихуда по `nb` барам длины `bar` перед σ (циклы по записям).
fn naive_bars(w: &Win, sig: i64, bar: i64, nb: usize) -> (f64, f64) {
    let n = w.t.partition_point(|&x| x < sig);
    let e0 = sig - nb as i64 * bar;
    let price_before = |ms: i64| w.t[..n].iter().rposition(|&x| x < ms).map(|i| w.p[i]);
    let pend: Vec<f64> = (0..=nb as i64)
        .map(|j| price_before(e0 + j * bar).unwrap())
        .collect();
    let r: Vec<f64> = (0..nb)
        .map(|j| 1e4 * (pend[j + 1] / pend[j]).ln())
        .collect();
    let (mut vk, mut qk) = (vec![0.0; nb], vec![0.0; nb]);
    for i in 0..n {
        let j = (w.t[i] - e0).div_euclid(bar);
        if (0..nb as i64).contains(&j) {
            vk[j as usize] += w.v[i];
            qk[j as usize] += if w.b[i] { w.v[i] } else { -w.v[i] };
        }
    }
    let nn = nb as f64;
    let (qm, rm) = (qk.iter().sum::<f64>() / nn, r.iter().sum::<f64>() / nn);
    let den: f64 = qk.iter().map(|q| (q - qm) * (q - qm)).sum();
    let num: f64 = qk.iter().zip(&r).map(|(q, x)| (q - qm) * (x - rm)).sum();
    let act: Vec<usize> = (0..nb).filter(|&j| vk[j] > 0.0).collect();
    (
        num / den * (vk.iter().sum::<f64>() / nn),
        act.iter().map(|&j| r[j].abs() / (vk[j] / 1e6)).sum::<f64>() / act.len() as f64,
    )
}

#[test]
fn short_markout_and_bars_equal_direct_formulas() {
    let w = dense(0x9e37_79b9_7f4a_7c15);
    let sig = D0 * DAY_MS + 10 * HOUR_MS + 31 * MIN_MS + 17_321;
    let f = ff(&w, sig, 1, None, D0 * DAY_MS);
    for (h, win, k) in [
        (1, 10, "mk1_10"),
        (1, 30, "mk1_30"),
        (5, 30, "mk5_30"),
        (5, 60, "mk5_60"),
        (10, 60, "mk10_60"),
    ] {
        let x = naive_markout(&w, sig, h * 1000, win * 1000);
        assert!(
            x.is_finite() && close(f[fi(k)], x, 1e-9),
            "{k}: {} vs {x}",
            f[fi(k)]
        );
    }
    for (bar, kk, ka) in [(2_000, "kyle1m", "amih1m"), (10_000, "kyle5m", "amih5m")] {
        let (k, a) = naive_bars(&w, sig, bar, 30);
        assert!(
            k.is_finite() && close(f[fi(kk)], k, 1e-9),
            "{kk}: {} vs {k}",
            f[fi(kk)]
        );
        assert!(
            a.is_finite() && close(f[fi(ka)], a, 1e-9),
            "{ka}: {} vs {a}",
            f[fi(ka)]
        );
    }
    let hi = w.t.partition_point(|&x| x < sig);
    let lo = w.t.partition_point(|&x| x < sig - 300_000);
    assert!(f[fi("vpin300")].is_finite());
    assert_eq!(f[fi("vpin300")], vpin_fused(&w.v, &w.b, lo, hi));
}

#[test]
fn short_formulas_on_tox_windows_equal_tox_bitwise() {
    // те же функции на окнах TOX13 дают TOX13: короткие окна — те же определения, другие длины
    let w = dense(77);
    let sig = D0 * DAY_MS + 10 * HOUR_MS + 40 * MIN_MS; // на границе минуты: минутные бары TOX13 кончаются на σ
    let tox = tox_features(
        &w,
        sig,
        1,
        None,
        Some(0),
        D0 * DAY_MS,
        &mut Scratch::default(),
    );
    let ti = |k: &str| TOX.iter().position(|x| *x == k).unwrap();
    let vw = view(&w, sig);
    let mut sc = Scratch::default();
    let mk = markout_short(&vw, sig, 10_000, 300_000, &mut sc);
    assert!(same(&[mk], &[tox[ti("mk10_300")]]));
    let (k, a) = bar_features(&vw, sig - 30 * MIN_MS, MIN_MS, 30, D0 * DAY_MS, &mut sc);
    assert!(k.is_finite() && a.is_finite());
    assert!(same(&[k, a], &[tox[ti("kyle30")], tox[ti("amih30")]]));
}

#[test]
fn no_lookahead_past_sigma_and_availability_gates() {
    let w = dense(5);
    let sig = D0 * DAY_MS + 10 * HOUR_MS + 5 * MIN_MS + 777;
    let n = w.t.partition_point(|&x| x < sig);
    assert!(n < w.t.len());
    let cut = win_from(&w.t[..n], &w.p[..n], &w.v[..n], &w.b[..n]);
    let q = Some(50.0);
    let full = ff(&w, sig, 1, q, D0 * DAY_MS);
    assert!(
        same(&full, &ff(&cut, sig, 1, q, D0 * DAY_MS)),
        "записи с t ≥ σ не читаются"
    );
    assert!(
        full.iter().filter(|x| x.is_finite()).count() >= N_FAST - 3,
        "на плотной ленте столбцы заполнены: {full:?}"
    );
    // данные загружены только с σ − 20 с: окна длиннее 20 с пусты, окна 10 с — нет
    let g = ff(&w, sig, 1, q, sig - 20_000);
    for k in [
        "tfv30", "vpin300", "sac30", "sac60", "mk1_30", "mk5_60", "kyle1m", "amih5m",
    ] {
        assert!(g[fi(k)].is_nan(), "{k}");
    }
    assert!(g.iter().skip(fi("accn1_60")).all(|x| x.is_nan()));
    assert!(g[fi("sac10")].is_finite() && g[fi("mk1_10")].is_finite());
    // монета появилась меньше 5 минут назад — vpin300 пуст
    let young = fast_features(
        &w,
        sig,
        1,
        q,
        Some(sig - 299_999),
        D0 * DAY_MS,
        &mut Scratch::default(),
    );
    assert!(young[fi("vpin300")].is_nan() && young[fi("tfv30")].is_finite());
}

#[test]
fn windows_cross_midnight_through_previous_day_tail() {
    let mut rng = Rng(11);
    let prev = gen_day(&mut rng, D0 - 1, 60_000, 20 * HOUR_MS, DAY_MS, 50.0);
    let cur = gen_day(&mut rng, D0, 2_000, 0, 10 * MIN_MS, 50.0);
    let sig = D0 * DAY_MS + 10_000; // 00:00:10 — окна 30 с, 60 с и 300 с начинаются в предыдущих сутках
    let want = ff(&win_of(&[&prev, &cur]), sig, -1, None, (D0 - 1) * DAY_MS);
    let mut days = BTreeMap::new();
    days.insert(("AAA".to_string(), D0 - 1), prev);
    days.insert(("AAA".to_string(), D0), cur);
    let eps = [Ep {
        row: 0,
        sig,
        side: -1,
        turn24h: NAN,
    }];
    let got = coin_rows(&MemSrc { days }, "AAA", &eps, &fast_features).unwrap();
    assert_eq!(got.rows.len(), 1);
    // q99 у прохода монеты свой (истории недели нет → нет порога) — как у `want` с `None`
    assert!(same(&got.rows[0].1, &want));
    assert!(want[fi("accn30_300")].is_finite() && want[fi("kyle5m")].is_finite());
}

#[test]
fn run_writes_fast_table_in_input_order() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let (eps_path, out_path) = (dir.path().join("eps.csv"), dir.path().join("out.csv"));
    let mut rng = Rng(5);
    let mut days = BTreeMap::new();
    for k in 0..=2 {
        for sym in ["AAA", "BBB"] {
            days.insert(
                (sym.to_string(), D0 + k),
                gen_day(&mut rng, D0 + k, 40_000, 8 * HOUR_MS, 10 * HOUR_MS, 30.0),
            );
        }
    }
    let s1 = DAY_MS + 9 * HOUR_MS + 1_234;
    let s2 = 2 * DAY_MS + 9 * HOUR_MS + 30 * MIN_MS;
    std::fs::write(
        &eps_path,
        format!("sym,sig_ms,side\nBBB,{s2},short\nAAA,{s1},long\nAAA,{s2},short\n"),
    )?;
    let cfg = ToxCfg {
        eps: eps_path,
        out: out_path.clone(),
        base_date: "2026-09-01".to_string(),
        threads: 2,
        coins: None,
        side: 1,
    };
    let rows = read_input(&cfg)?;
    let sum = run(&cfg, &rows, &MemSrc { days })?;
    assert_eq!((sum.rows, sum.coins), (3, 2));
    assert!(sum.json().contains("\"accn1_60\":1.0000"));
    let text = std::fs::read_to_string(&out_path)?;
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines[0], format!("sym,sig_ms,{}", FAST.join(",")));
    assert_eq!(lines.len(), 4);
    for (l, (sym, sig)) in lines[1..]
        .iter()
        .zip([("BBB", s2), ("AAA", s1), ("AAA", s2)])
    {
        let cols: Vec<&str> = l.split(',').collect();
        assert_eq!((cols[0], cols[1]), (sym, sig.to_string().as_str()));
        assert_eq!(cols.len(), 2 + N_FAST);
        for k in [
            "tfv30",
            "sac30",
            "mk5_60",
            "kyle5m",
            "accv10_300",
            "accdn30_60",
        ] {
            assert!(!cols[fi(k) + 2].is_empty(), "{k}: {l}");
        }
    }
    Ok(())
}
