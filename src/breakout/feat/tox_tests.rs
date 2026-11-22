//! Тесты `tox` (TK-671): ручные значения `sac` / `bigimb`, kyle / amih / markout против прямых формул, пустые окна и условия NaN, хвост предыдущих суток
//! против склейки целых суток (как у Python), строки на сутки без файла, прогон по CSV. Равенство с Python — ворота на srv (`tools/p42/tk671_tox_gate.py`).

use super::flow::{edges, q99_edge, vpin_value, Ep, DAY_MS, HOUR_MS, MIN_MS, NB};
use super::npcompat::ss_right_f64;
use super::tox::{read_input, run, tox_coin, tox_features, vpin_fused, Scratch, ToxCfg, Win, TOX};
use super::trades::{DaySrc, DayTrades, DayTradesP};
use anyhow::Result;
use std::collections::BTreeMap;

/// 2026-09-01, сутки от 1970.
const D0: i64 = 20_697;
const NAN: f64 = f64::NAN;

pub(super) struct Rng(pub(super) u64);

impl Rng {
    pub(super) fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }

    pub(super) fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}

/// Сутки `d`: `n` записей в `[from_ms, to_ms)` суток (мс от полуночи), цена — блуждание с шагом 0,01, количество — тяжёлый хвост.
pub(super) fn gen_day(
    rng: &mut Rng,
    d: i64,
    n: usize,
    from_ms: i64,
    to_ms: i64,
    p0: f64,
) -> DayTradesP {
    let mut offs: Vec<i64> = (0..n)
        .map(|_| from_ms + (rng.unit() * (to_ms - from_ms) as f64) as i64)
        .collect();
    offs.sort_unstable();
    let mut px = p0;
    let mut day = DayTradesP::default();
    for off in offs {
        let step = (rng.unit() * 5.0).floor() - 2.0;
        px = (px + step * 0.01).max(0.5);
        let p = (px * 100.0).round() / 100.0;
        let q = 0.001 + rng.unit().powi(4) * 50.0;
        day.t.push(d * DAY_MS + off);
        day.p.push(p);
        day.v.push(p * q);
        day.buy.push(rng.next() & 1 == 1);
    }
    day
}

pub(super) fn win_of(parts: &[&DayTradesP]) -> Win {
    let mut w = Win::default();
    for d in parts {
        w.t.extend_from_slice(&d.t);
        w.p.extend_from_slice(&d.p);
        w.v.extend_from_slice(&d.v);
        w.b.extend_from_slice(&d.buy);
    }
    w
}

pub(super) fn win_from(t: &[i64], p: &[f64], v: &[f64], b: &[bool]) -> Win {
    Win {
        t: t.to_vec(),
        p: p.to_vec(),
        v: v.to_vec(),
        b: b.to_vec(),
    }
}

fn feats(w: &Win, sig: i64, s: i64, q99: Option<f64>, avail_from: i64) -> [f64; 13] {
    tox_features(w, sig, s, q99, Some(0), avail_from, &mut Scratch::default())
}

pub(super) fn same(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len()
        && a.iter()
            .zip(b)
            .all(|(x, y)| x.to_bits() == y.to_bits() || (x.is_nan() && y.is_nan()))
}

pub(super) fn close(a: f64, b: f64, rel: f64) -> bool {
    (a - b).abs() <= rel * a.abs().max(b.abs()).max(1e-300)
}

fn idx(k: &str) -> usize {
    TOX.iter().position(|x| *x == k).unwrap()
}

#[test]
fn no_record_before_sigma_gives_all_nan() {
    let sig = D0 * DAY_MS + 10 * HOUR_MS;
    let w = win_from(&[sig, sig + 1], &[1.0, 1.0], &[1.0, 1.0], &[true, true]);
    assert!(feats(&w, sig, 1, Some(0.0), 0).iter().all(|x| x.is_nan()));
    let empty = Win::default();
    assert!(feats(&empty, sig, 1, Some(0.0), 0)
        .iter()
        .all(|x| x.is_nan()));
}

#[test]
fn sign_autocorr_hand_values_and_nan_conditions() {
    let sig = D0 * DAY_MS + 12 * HOUR_MS;
    // 40 записей за последние 40 с, знак чередуется: dc = d, den = 40, Σ dc_i·dc_{i+1} = −39
    let alt = |n: i64| -> Win {
        let t: Vec<i64> = (0..n).map(|k| sig - (n - k) * 1000).collect();
        let b: Vec<bool> = (0..n).map(|k| k % 2 == 0).collect();
        win_from(&t, &vec![10.0; n as usize], &vec![1.0; n as usize], &b)
    };
    let f = feats(&alt(40), sig, 1, None, 0);
    assert_eq!(f[idx("sac300")], -39.0 / 40.0);
    assert_eq!(f[idx("sac1800")], -39.0 / 40.0);
    // 29 записей — меньше порога 30
    assert!(feats(&alt(29), sig, 1, None, 0)[idx("sac300")].is_nan());
    // одна сторона — разброс нулевой
    let mut w = alt(40);
    w.b.fill(true);
    assert!(feats(&w, sig, 1, None, 0)[idx("sac300")].is_nan());
    // окно раньше загруженных данных: avail_from > σ − 300 с → NaN, окно 1800 с тоже
    let w = alt(40);
    assert!(feats(&w, sig, 1, None, sig - 100_000)[idx("sac300")].is_nan());
    assert!(!feats(&w, sig, 1, None, sig - 300_000)[idx("sac300")].is_nan());
    assert!(feats(&w, sig, 1, None, sig - 300_000)[idx("sac1800")].is_nan());
}

#[test]
fn big_imbalance_hand_values() {
    let sig = D0 * DAY_MS + 12 * HOUR_MS;
    let t = [
        sig - 400_000,
        sig - 200_000,
        sig - 100_000,
        sig - 50_000,
        sig - 10_000,
    ];
    let v = [100.0, 20.0, 30.0, 40.0, 5.0];
    let b = [true, true, false, true, false];
    let w = win_from(&t, &[1.0; 5], &v, &b);
    // окно 300 с: записи 2…5, крупные (≥ 10): 20 (покупка), 30, 40 (покупка) → vt = 90, покупки 60 → (120 − 90) / 90
    let f = feats(&w, sig, 1, Some(10.0), 0);
    assert_eq!(f[idx("bigimb300")], (2.0 * 60.0 - 90.0) / 90.0);
    assert_eq!(
        feats(&w, sig, -1, Some(10.0), 0)[idx("bigimb300")],
        -((2.0 * 60.0 - 90.0) / 90.0)
    );
    // окно 1800 с: ещё запись 1 (100, покупка): vt = 190, покупки 160 → (320 − 190) / 190
    assert_eq!(f[idx("bigimb1800")], (2.0 * 160.0 - 190.0) / 190.0);
    // нет порога / порог выше всех записей — NaN
    assert!(feats(&w, sig, 1, None, 0)[idx("bigimb300")].is_nan());
    assert!(feats(&w, sig, 1, Some(f64::INFINITY), 0)[idx("bigimb300")].is_nan());
}

/// Прямые формулы §13.25 п. 3.2 по записям `t < σ` (циклы, без массивов): `(kyle, amih)` по `wm` минутам.
fn naive_kyle_amih(w: &Win, sig: i64, wm: i64) -> (f64, f64) {
    let m = sig.div_euclid(MIN_MS);
    let n = w.t.partition_point(|&x| x < sig);
    let price_before = |ms: i64| w.t[..n].iter().rposition(|&x| x < ms).map(|i| w.p[i]);
    let mut pend = vec![];
    for j in 0..=wm {
        pend.push(price_before((m - wm + j) * MIN_MS).unwrap());
    }
    let r: Vec<f64> = (0..wm as usize)
        .map(|j| 1e4 * (pend[j + 1] / pend[j]).ln())
        .collect();
    let (mut vk, mut qk) = (vec![0.0; wm as usize], vec![0.0; wm as usize]);
    for i in 0..n {
        let bar = (w.t[i] - (m - wm) * MIN_MS).div_euclid(MIN_MS);
        if (0..wm).contains(&bar) {
            vk[bar as usize] += w.v[i];
            qk[bar as usize] += if w.b[i] { w.v[i] } else { -w.v[i] };
        }
    }
    let nn = wm as f64;
    let (qm, rm) = (qk.iter().sum::<f64>() / nn, r.iter().sum::<f64>() / nn);
    let den: f64 = qk.iter().map(|q| (q - qm) * (q - qm)).sum();
    let num: f64 = qk.iter().zip(&r).map(|(q, x)| (q - qm) * (x - rm)).sum();
    let kyle = num / den * (vk.iter().sum::<f64>() / nn);
    let act: Vec<usize> = (0..wm as usize).filter(|&j| vk[j] > 0.0).collect();
    let amih = act.iter().map(|&j| r[j].abs() / (vk[j] / 1e6)).sum::<f64>() / act.len() as f64;
    (kyle, amih)
}

pub(super) fn naive_markout(w: &Win, sig: i64, h: i64, win: i64) -> f64 {
    let n = w.t.partition_point(|&x| x < sig);
    let (mut num, mut den) = (0.0, 0.0);
    for i in 0..n {
        if w.t[i] >= sig - win && w.t[i] + h < sig {
            let j = w.t[..n].iter().rposition(|&x| x <= w.t[i] + h).unwrap();
            let sgn = if w.b[i] { 1.0 } else { -1.0 };
            num += w.v[i] * (sgn * 1e4 * (w.p[j] / w.p[i]).ln());
            den += w.v[i];
        }
    }
    num / den
}

#[test]
fn kyle_amih_markout_equal_direct_formulas() {
    let mut rng = Rng(0x9e37_79b9_7f4a_7c15);
    let d = gen_day(&mut rng, D0, 30_000, 0, DAY_MS, 100.0);
    let w = win_of(&[&d]);
    let sig = D0 * DAY_MS + 15 * HOUR_MS + 31 * MIN_MS + 17_000;
    let f = feats(&w, sig, 1, None, D0 * DAY_MS);
    for (wm, kk, ka) in [(30, "kyle30", "amih30"), (240, "kyle240", "amih240")] {
        let (k, a) = naive_kyle_amih(&w, sig, wm);
        assert!(close(f[idx(kk)], k, 1e-9), "{kk}: {} vs {k}", f[idx(kk)]);
        assert!(close(f[idx(ka)], a, 1e-9), "{ka}: {} vs {a}", f[idx(ka)]);
    }
    for (h, win, k) in [
        (10, 300, "mk10_300"),
        (60, 300, "mk60_300"),
        (60, 1800, "mk60_1800"),
    ] {
        let x = naive_markout(&w, sig, h * 1000, win * 1000);
        assert!(close(f[idx(k)], x, 1e-9), "{k}: {} vs {x}", f[idx(k)]);
    }
    // vpin — те же 50 корзин равного оборота, что у `flow::vpin_value`
    let hi = w.t.partition_point(|&x| x < sig);
    let lo = w.t.partition_point(|&x| x < sig - HOUR_MS);
    assert_eq!(f[idx("vpin1h")], vpin_value(&w.v, &w.b, lo, hi));
    assert!(f[idx("vpin1h")].is_finite() && f[idx("vpin6h")].is_finite());
}

#[test]
fn start_of_day_gates_follow_available_data() {
    let mut rng = Rng(7);
    let d = gen_day(&mut rng, D0, 20_000, 0, DAY_MS, 50.0);
    let w = win_of(&[&d]);
    let at = |off: i64| D0 * DAY_MS + off;
    let q = Some(1.0);
    // 20 мин от полуночи (`avail_from` = полночь): окна 300 с есть, окна ≥ 30 мин — нет
    let f = feats(&w, at(20 * MIN_MS + 5_000), 1, q, at(0));
    assert!(
        f[idx("sac300")].is_finite()
            && f[idx("mk10_300")].is_finite()
            && f[idx("bigimb300")].is_finite()
    );
    for k in [
        "kyle30",
        "amih30",
        "sac1800",
        "mk60_1800",
        "bigimb1800",
        "vpin1h",
        "vpin6h",
        "kyle240",
    ] {
        assert!(f[idx(k)].is_nan(), "{k}");
    }
    // 35 мин: окна 30 мин есть, 60 мин и 240 мин — нет
    let f = feats(&w, at(35 * MIN_MS + 5_000), 1, q, at(0));
    for k in ["kyle30", "amih30", "sac1800", "mk60_1800", "bigimb1800"] {
        assert!(f[idx(k)].is_finite(), "{k}");
    }
    for k in ["vpin1h", "vpin6h", "kyle240", "amih240"] {
        assert!(f[idx(k)].is_nan(), "{k}");
    }
    // 2 ч: vpin1h есть, vpin6h и окно 240 мин — нет
    let f = feats(&w, at(2 * HOUR_MS + 5_000), 1, q, at(0));
    assert!(
        f[idx("vpin1h")].is_finite() && f[idx("vpin6h")].is_nan() && f[idx("kyle240")].is_nan()
    );
    // 7 ч: всё есть; `coin_first` позже начала окна 6 ч → vpin6h пуст, vpin1h остаётся
    let f = feats(&w, at(7 * HOUR_MS), 1, q, at(0));
    assert!(f[idx("vpin6h")].is_finite() && f[idx("kyle240")].is_finite());
    let g = tox_features(
        &w,
        at(7 * HOUR_MS),
        1,
        q,
        Some(at(HOUR_MS) + 1),
        at(0),
        &mut Scratch::default(),
    );
    assert!(g[idx("vpin6h")].is_nan() && g[idx("vpin1h")].is_finite());
}

pub(super) struct MemSrc {
    pub(super) days: BTreeMap<(String, i64), DayTradesP>,
}

impl DaySrc for MemSrc {
    fn day(&self, sym: &str, abs_day: i64) -> Result<Option<DayTrades>> {
        Ok(self
            .days
            .get(&(sym.to_string(), abs_day))
            .map(|d| d.clone().into_plain()))
    }

    fn day_p(&self, sym: &str, abs_day: i64) -> Result<Option<DayTradesP>> {
        Ok(self.days.get(&(sym.to_string(), abs_day)).cloned())
    }
}

/// `q99` по записям часов `[h_end − 168, h_end)` — гистограммой напрямую (бинарный поиск по `edges`), без часовых блоков `tox_coin`.
fn q99_direct(days: &[&DayTradesP], h_end: i64) -> Option<f64> {
    let ed = edges();
    let mut hh = vec![0i64; NB];
    let mut hours = std::collections::BTreeSet::new();
    for d in days {
        for (&t, &v) in d.t.iter().zip(&d.v) {
            let h = t.div_euclid(HOUR_MS);
            if h >= h_end - 168 && h < h_end {
                hours.insert(h);
                hh[(ss_right_f64(ed, v) as i64 - 1).clamp(0, NB as i64 - 1) as usize] += 1;
            }
        }
    }
    if hours.len() >= 24 {
        q99_edge(&hh)
    } else {
        None
    }
}

fn ep(row: usize, sig: i64, side: i64) -> Ep {
    Ep {
        row,
        sig,
        side,
        turn24h: NAN,
    }
}

#[test]
fn coin_rows_equal_whole_day_concat() {
    let mut rng = Rng(0x1234_5678_9abc_def1);
    let mut days = BTreeMap::new();
    let mut all: Vec<DayTradesP> = vec![];
    for k in 0..9 {
        // сутки D0 + 4 (k = 6) — разреженные: 60 записей только в первые 3 часа, дальше пусто
        let d = if k == 6 {
            gen_day(&mut rng, D0 - 2 + k, 60, 0, 3 * HOUR_MS, 100.0)
        } else {
            gen_day(&mut rng, D0 - 2 + k, 6_000, 0, DAY_MS, 100.0)
        };
        days.insert(("AAAUSDT".to_string(), D0 - 2 + k), d.clone());
        all.push(d);
    }
    // дни D0 − 2 … D0 + 6; строки на D0 + 3 (плотные), D0 + 4 (сами разреженные), D0 + 5 (предыдущие разреженные: хвост — последняя запись до среза)
    let day = |dd: i64| &all[(dd - (D0 - 2)) as usize];
    let sigs = [
        (D0 + 3) * DAY_MS + 5_000,
        (D0 + 3) * DAY_MS + 2 * HOUR_MS + 111,
        (D0 + 3) * DAY_MS + 17 * HOUR_MS + 61_000,
        (D0 + 4) * DAY_MS + 7_000,
        (D0 + 4) * DAY_MS + 3 * HOUR_MS + 40 * MIN_MS,
        (D0 + 4) * DAY_MS + 23 * HOUR_MS + 59 * MIN_MS + 59_999,
        (D0 + 5) * DAY_MS,
        (D0 + 5) * DAY_MS + 1,
        (D0 + 5) * DAY_MS + 9 * HOUR_MS + 5 * MIN_MS,
    ];
    // порядок на входе — не по времени
    let order = [4usize, 0, 7, 2, 8, 1, 5, 3, 6];
    let eps: Vec<Ep> = order
        .iter()
        .enumerate()
        .map(|(row, &i)| ep(row, sigs[i], if i % 2 == 0 { 1 } else { -1 }))
        .collect();
    let src = MemSrc { days };
    let got = tox_coin(&src, "AAAUSDT", &eps).unwrap();
    assert_eq!(got.rows.len(), 9);
    assert_eq!((got.missing_trade_days, got.rows_on_missing_day), (0, 0));
    let coin_first = Some(all[0].t[0]);
    let mut n_finite = 0;
    for (row, vals) in &got.rows {
        let e = &eps[*row];
        let d = e.sig.div_euclid(DAY_MS);
        let w = win_of(&[day(d - 1), day(d)]);
        let q99 = q99_direct(&all.iter().collect::<Vec<_>>(), e.sig.div_euclid(HOUR_MS));
        let want = tox_features(
            &w,
            e.sig,
            e.side,
            q99,
            coin_first,
            (d - 1) * DAY_MS,
            &mut Scratch::default(),
        );
        assert!(same(vals, &want), "строка {row}: {vals:?} против {want:?}");
        n_finite += vals.iter().filter(|x| x.is_finite()).count();
    }
    assert!(n_finite > 40, "почти всё NaN: {n_finite}");
}

#[test]
fn missing_files_follow_python_rules() {
    let mut rng = Rng(99);
    let mut days = BTreeMap::new();
    for k in [-3, -2, -1, 1] {
        days.insert(
            ("BBBUSDT".to_string(), D0 + k),
            gen_day(&mut rng, D0 + k, 5_000, 0, DAY_MS, 20.0),
        );
    }
    // сутки D0 — файла нет: строка пуста и посчитана как «на сутки без файла»; сутки D0 + 1: предыдущих нет → окно с полуночи, avail_from = полночь
    let eps = vec![
        ep(0, D0 * DAY_MS + 8 * HOUR_MS, 1),
        ep(1, (D0 + 1) * DAY_MS + 8 * HOUR_MS, 1),
    ];
    let got = tox_coin(&MemSrc { days }, "BBBUSDT", &eps).unwrap();
    assert_eq!(got.rows_on_missing_day, 1);
    let r0 = got.rows.iter().find(|r| r.0 == 0).unwrap().1;
    assert!(r0.iter().all(|x| x.is_nan()));
    let r1 = got.rows.iter().find(|r| r.0 == 1).unwrap().1;
    // 8 ч от полуночи: окна 240 мин и 1800 с в пределах суток считаются; vpin6h (σ − 6 ч ≥ полночь) тоже
    assert!(
        r1[idx("kyle240")].is_finite()
            && r1[idx("sac1800")].is_finite()
            && r1[idx("vpin6h")].is_finite()
    );
    // отсутствующие сутки после первого найденного (D0 в окне dmin − 1 … dmax) считаются
    assert_eq!(got.missing_trade_days, 1);
}

#[test]
fn run_writes_rows_in_input_order() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let (eps_path, out_path) = (dir.path().join("eps.csv"), dir.path().join("out.csv"));
    let mut rng = Rng(5);
    let mut days = BTreeMap::new();
    for k in -4..=2 {
        for sym in ["AAA", "BBB"] {
            days.insert(
                (sym.to_string(), D0 + k),
                gen_day(&mut rng, D0 + k, 12_000, 0, DAY_MS, 30.0),
            );
        }
    }
    // sig_ms — от base-date = D0; BBB раньше AAA на входе; сторона из столбца
    let s1 = DAY_MS + 9 * HOUR_MS + 1_234;
    let s2 = 2 * DAY_MS + 3 * HOUR_MS;
    std::fs::write(
        &eps_path,
        format!("sym,sig_ms,side,x\nBBB,{s2},short,1\nAAA,{s1},long,2\nAAA,{s2},short,3\n"),
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
    assert_eq!(rows.len(), 3);
    assert_eq!(rows[0].side, -1);
    let sum = run(&cfg, &rows, &MemSrc { days })?;
    assert_eq!((sum.rows, sum.coins), (3, 2));
    let text = std::fs::read_to_string(&out_path)?;
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines[0], format!("sym,sig_ms,{}", TOX.join(",")));
    assert_eq!(lines.len(), 4);
    for (l, (sym, sig)) in lines[1..]
        .iter()
        .zip([("BBB", s2), ("AAA", s1), ("AAA", s2)])
    {
        let cols: Vec<&str> = l.split(',').collect();
        assert_eq!((cols[0], cols[1]), (sym, sig.to_string().as_str()));
        assert_eq!(cols.len(), 2 + 13);
        // vpin / sac / kyle на плотных сутках есть
        assert!(
            !cols[2].is_empty()
                && !cols[idx("sac1800") + 2].is_empty()
                && !cols[idx("kyle30") + 2].is_empty(),
            "{l}"
        );
    }
    // нет столбца `side` — сторона из флага
    let eps2 = dir.path().join("eps2.csv");
    std::fs::write(&eps2, format!("sym,sig_ms\nAAA,{s1}\n"))?;
    for side in [1, -1] {
        let c = ToxCfg {
            eps: eps2.clone(),
            out: out_path.clone(),
            base_date: "2026-09-01".to_string(),
            threads: 1,
            coins: Some(vec!["AAA".to_string()]),
            side,
        };
        assert_eq!(read_input(&c)?[0].side, side);
    }
    Ok(())
}

#[test]
fn vpin_fused_equals_flow_vpin_bitwise() {
    let mut rng = Rng(0xdead_beef_1234_5678);
    for n in [0usize, 1, 2, 9, 10, 49, 50, 51, 120, 777, 5_000, 40_000] {
        let d = gen_day(&mut rng, D0, n, 0, DAY_MS, 100.0);
        let (mut v, mut b) = (d.v.clone(), d.buy.clone());
        for (lo, hi) in [(0, n), (n / 3, n), (n / 4, n - n / 5), (n, n)] {
            assert_eq!(
                vpin_fused(&v, &b, lo, hi).to_bits(),
                vpin_value(&v, &b, lo, hi).to_bits(),
                "n = {n}, [{lo}, {hi})"
            );
        }
        // нулевые обороты и одна сторона
        for x in v.iter_mut().step_by(3) {
            *x = 0.0;
        }
        b.fill(true);
        assert_eq!(
            vpin_fused(&v, &b, 0, n).to_bits(),
            vpin_value(&v, &b, 0, n).to_bits()
        );
        v.fill(0.0);
        assert!(vpin_fused(&v, &b, 0, n).is_nan() && vpin_value(&v, &b, 0, n).is_nan());
    }
}

#[test]
fn scratch_reuse_across_rows_equals_fresh_scratch() {
    let mut rng = Rng(0x0fed_cba9_8765_4321);
    let d = gen_day(&mut rng, D0, 40_000, 0, DAY_MS, 80.0);
    let w = win_of(&[&d]);
    // порядок строк: возрастание, убывание, скачки (левое / правое достраивание и замена не примыкающего отрезка кэша markout)
    let offs = [
        7 * HOUR_MS,
        7 * HOUR_MS + 20_000,
        7 * HOUR_MS + 400_000,
        7 * HOUR_MS + 2_000_000,
        7 * HOUR_MS + 2_000_000,
        6 * HOUR_MS + 1_000,
        6 * HOUR_MS + 3_100_000,
        20 * HOUR_MS,
        9 * HOUR_MS + 5,
        9 * HOUR_MS + 4,
        9 * HOUR_MS + 1_790_000,
        9 * HOUR_MS + 31_000,
        14 * HOUR_MS,
        14 * HOUR_MS + 61_000,
    ];
    let mut shared = Scratch::default();
    shared.new_window();
    for off in offs {
        let sig = D0 * DAY_MS + off;
        let a = tox_features(&w, sig, 1, Some(2.0), Some(0), D0 * DAY_MS, &mut shared);
        let b = feats(&w, sig, 1, Some(2.0), D0 * DAY_MS);
        assert!(same(&a, &b), "off = {off}: {a:?} против {b:?}");
        assert!(a[idx("mk60_1800")].is_finite());
    }
}
