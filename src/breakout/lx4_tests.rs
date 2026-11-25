//! Тесты TK-697 D № 1 (`lx = 4`): наклонная линия по `lk` последним подтверждённым разворотам стороны вместо горизонтального уровня.
//! Мир: фон 90,0 (минута: high 90,1, low 89,9 — NATR60 ≈ 0,22 %), пивот — одна минута-максимум в начале бара `tf` (бар выше соседей, закрытие остаётся 90,0).
//! Номера баров в тестах — от `B0` часов (после прогрева: сигналы идут с `START_MIN`).

use super::data::{Cols, Sym};
use super::levels::{build_levels, Level};
use super::params::{parse_variant, LevelCfg, Params, SigCfg};
use super::sigcache::cfg_key_text;
use super::tests::{mirror, px, set, sym};
use super::tick::{candidates, signals_for_coin, TSig, Tick, TickCtr, TradeSrc};
use super::Side;
use std::collections::HashMap;
use std::sync::Arc;

/// Часов прогрева до бара 0 тестов; фон занимает `B0 + HOURS` часов с минуты 0.
const B0: usize = 400;
const HOURS: usize = 200;
/// 1e8 — цена ×1e8 равна `px`.
const E8: i64 = 100_000_000;

/// Фон: каждая минута `B0 + HOURS` часов — open = close = 90,0, high 90,1, low 89,9.
fn bg() -> Cols {
    let mut c = Cols::new();
    for m in 0..(B0 + HOURS) * 60 {
        set(&mut c, m, 90.0, 90.1, 89.9, 90.0);
    }
    c
}

/// Абсолютный номер бара `tf` по относительному (от `B0` часов).
fn ab(tf: usize, rel: usize) -> usize {
    B0 * 60 / tf + rel
}

/// Первая минута бара `rel` (относительного, размер `tf`).
fn mn(tf: usize, rel: usize) -> usize {
    ab(tf, rel) * tf
}

/// Пивоты-максимумы: (бар от `B0`, цена) — high первой минуты бара `tf`; соседние бары остаются на фоне.
fn peaks(c: &mut Cols, tf: usize, ps: &[(usize, f64)]) {
    for &(b, v) in ps {
        c.h[mn(tf, b)] = px(v);
    }
}

/// Конфиг уровней `lx = 4` напрямую (минуя разбор): ширина пивота `k = 1`, глубина 0,3 %.
fn cfg(tf: usize, lk: usize, ld: usize, lsl_m: i64) -> LevelCfg {
    let mut p = Params::base();
    p.sig.lv.tf_min = tf;
    p.sig.lv.k = 1;
    p.sig.lv.d_pm = 300;
    p.sig.lv.lx = 4;
    p.sig.lv.lk = lk;
    p.sig.lv.ld = ld;
    p.sig.lv.lsl_m = lsl_m;
    p.sig.lv
}

fn long(c: &Cols, cf: &LevelCfg) -> Vec<Level> {
    build_levels(&sym(c.clone()), cf, Side::Long)
}

/// Эталон версии минуты `m` линии через две точки (бары, цены ×1e8): целая арифметика, середина минуты, половина — вверх.
fn exact2(tf: usize, p0: (usize, i64), p1: (usize, i64), m: usize) -> i64 {
    let x = |b: usize| (b * tf + tf / 2) as i128;
    let (x0, x1) = (x(p0.0), x(p1.0));
    let num = i128::from(p1.1 - p0.1) * (2 * (m as i128 - x0) + 1);
    let den = 2 * (x1 - x0);
    p0.1 + (2 * num + den).div_euclid(2 * den) as i64
}

/// Эталон МНК в `f64` (для трёх и более точек): значение линии в середине минуты `m`, цена ×1e8.
fn ref_ls(tf: usize, pts: &[(usize, f64)], m: usize) -> f64 {
    let n = pts.len() as f64;
    let xs: Vec<f64> = pts.iter().map(|&(b, _)| (b * tf + tf / 2) as f64).collect();
    let ys: Vec<f64> = pts.iter().map(|&(_, y)| y * 1e8).collect();
    let (mx, my) = (xs.iter().sum::<f64>() / n, ys.iter().sum::<f64>() / n);
    let sxx: f64 = xs.iter().map(|x| (x - mx) * (x - mx)).sum();
    let sxy: f64 = xs.iter().zip(&ys).map(|(x, y)| (x - mx) * (y - my)).sum();
    my + sxy / sxx * (m as f64 + 0.5 - mx)
}

fn assert_ls(l: &Level, tf: usize, pts: &[(usize, f64)]) {
    let abs: Vec<(usize, f64)> = pts.iter().map(|&(b, y)| (ab(tf, b), y)).collect();
    assert!(!l.segs.is_empty());
    for &(m, p) in &l.segs {
        let want = ref_ls(tf, &abs, m as usize);
        assert!(
            (p as f64 - want).abs() <= 2.0,
            "минута {m}: {p} против МНК {want}"
        );
    }
}

// ───────────────────────────── построение: точки, МНК, рождение, конец ─────────────────────────────

#[test]
fn two_points_line_has_exact_minute_versions_born_and_end() {
    let mut c = bg();
    peaks(&mut c, 60, &[(10, 110.0), (20, 108.0)]);
    let lv = long(&c, &cfg(60, 2, 3, 0));
    assert_eq!(lv.len(), 1, "один пивот не даёт линии, два — одну");
    let l = &lv[0];
    // рождение — закрытие бара подтверждения второй точки (бар 21); конец — возраст старшей точки (бар 10) > 3 суток на закрытии бара
    assert_eq!(
        (l.born as usize, l.end as usize, l.first_bar as usize, l.src),
        (mn(60, 22), mn(60, 10 + 72 + 1), ab(60, 10), 4)
    );
    assert_eq!(
        l.segs.len(),
        (l.end - l.born) as usize,
        "версия на каждую минуту жизни линии"
    );
    for (i, &(m, p)) in l.segs.iter().enumerate() {
        assert_eq!(m as usize, l.born as usize + i);
        let want = exact2(
            60,
            (ab(60, 10), px(110.0)),
            (ab(60, 20), px(108.0)),
            m as usize,
        );
        assert_eq!(p, want, "минута {m}");
        assert_eq!(l.price_at(m as usize), p);
    }
    assert!(l.mem.is_empty() && l.far_edge(0, 1).is_none());
    assert!(
        l.segs.last().map(|x| x.1) < l.segs.first().map(|x| x.1),
        "наклон против пробоя: максимумы падают, линия идёт вниз"
    );
}

#[test]
fn line_ignores_everything_after_its_birth_until_a_new_pivot_is_confirmed() {
    let mut a = bg();
    peaks(&mut a, 60, &[(10, 110.0), (20, 108.0)]);
    let la = long(&a, &cfg(60, 2, 3, 0));
    // после рождения всё заменено плоским 50,0: рождение, версии и конец те же
    let mut g = a.clone();
    for m in mn(60, 22)..(B0 + HOURS) * 60 {
        set(&mut g, m, 50.0, 50.0, 50.0, 50.0);
    }
    let lg = long(&g, &cfg(60, 2, 3, 0));
    assert_eq!(lg.len(), 1);
    assert_eq!(
        (lg[0].born, lg[0].end, lg[0].first_bar),
        (la[0].born, la[0].end, la[0].first_bar)
    );
    assert_eq!(lg[0].segs, la[0].segs);
    // третий пивот (бар 40) подтверждается на закрытии бара 41: до этой минуты версии прежние, линия кончается ею
    let mut b = a.clone();
    peaks(&mut b, 60, &[(40, 107.0)]);
    let lb = long(&b, &cfg(60, 2, 3, 0));
    assert_eq!(lb.len(), 2);
    assert_eq!(lb[0].born, la[0].born);
    assert_eq!(lb[0].end as usize, mn(60, 42));
    assert_eq!(lb[0].segs[..], la[0].segs[..lb[0].segs.len()]);
}

#[test]
fn three_and_four_points_use_the_least_squares_line() {
    let mut c = bg();
    let p3 = [(10, 110.0), (20, 108.0), (34, 106.0)];
    peaks(&mut c, 60, &p3);
    let l3 = long(&c, &cfg(60, 3, 5, 0));
    assert_eq!(l3.len(), 1, "три пивота — одна линия по трём точкам");
    assert_eq!(
        (l3[0].born as usize, l3[0].first_bar as usize),
        (mn(60, 36), ab(60, 10)),
        "рождение — закрытие бара 35 (подтверждение пивота 34)"
    );
    assert_ls(&l3[0], 60, &p3);
    // МНК — не прямая через крайние точки: средняя точка (20; 108,0) лежит ниже прямой по крайним, точки не коллинеарны
    let mid = l3[0].price_at(mn(60, 36));
    let ends = exact2(
        60,
        (ab(60, 10), px(110.0)),
        (ab(60, 34), px(106.0)),
        mn(60, 36),
    );
    assert_ne!(mid, ends, "МНК по трём точкам ≠ прямая по крайним");
    // четыре точки
    let p4 = [(10, 110.0), (18, 109.0), (26, 107.0), (34, 106.5)];
    let mut c4 = bg();
    peaks(&mut c4, 60, &p4);
    let l4 = long(&c4, &cfg(60, 4, 5, 0));
    assert_eq!(l4.len(), 1);
    assert_eq!(l4[0].born as usize, mn(60, 36));
    assert_ls(&l4[0], 60, &p4);
    // lk = 3 на тех же четырёх пивотах: линия на каждое подтверждение, начиная с третьего; соседние линии стыкуются
    let l33 = long(&c4, &cfg(60, 3, 5, 0));
    assert_eq!(l33.len(), 2);
    assert_eq!(
        (l33[0].born as usize, l33[0].first_bar as usize),
        (mn(60, 28), ab(60, 10))
    );
    assert_eq!(
        (l33[1].born as usize, l33[1].first_bar as usize),
        (mn(60, 36), ab(60, 18))
    );
    assert_eq!(l33[0].end, l33[1].born, "новая линия — на месте старой");
    assert_ls(&l33[1], 60, &p4[1..]);
    // точек меньше lk — линии нет
    let mut c2 = bg();
    peaks(&mut c2, 60, &p4[..3]);
    assert!(long(&c2, &cfg(60, 4, 5, 0)).is_empty());
}

#[test]
fn prices_must_fall_strictly_in_the_side_frame() {
    for pts in [[(10, 108.0), (20, 110.0)], [(10, 108.0), (20, 108.0)]] {
        let mut c = bg();
        peaks(&mut c, 60, &pts);
        assert!(
            long(&c, &cfg(60, 2, 3, 0)).is_empty(),
            "растущие или равные максимумы: не против пробоя, {pts:?}"
        );
    }
    // три точки: вторая выше первой — не монотонно падают
    let mut c = bg();
    peaks(&mut c, 60, &[(10, 110.0), (20, 106.0), (30, 108.0)]);
    assert!(long(&c, &cfg(60, 3, 3, 0)).is_empty());
    // при lk = 2 последние две (106 → 108) растут, а прежняя пара (110 → 106) давала линию, которая кончилась с подтверждением третьего
    let lv = long(&c, &cfg(60, 2, 3, 0));
    assert_eq!(lv.len(), 1);
    assert_eq!(lv[0].born as usize, mn(60, 22));
    assert_eq!(
        lv[0].end as usize,
        mn(60, 32),
        "новый пивот сменил набор точек — старая линия кончилась, новой нет"
    );
}

#[test]
fn look_back_age_of_the_oldest_point_cuts_birth_and_life() {
    // бары 10 и 40: подтверждение на закрытии бара 41 — возраст старшей точки 42·60 − 10·60 = 1920 мин
    let mut c = bg();
    peaks(&mut c, 60, &[(10, 110.0), (40, 106.0)]);
    assert!(
        long(&c, &cfg(60, 2, 1, 0)).is_empty(),
        "1920 > 1440 (1 сутки)"
    );
    let l2 = long(&c, &cfg(60, 2, 2, 0));
    assert_eq!(l2.len(), 1);
    assert_eq!(l2[0].born as usize, mn(60, 42));
    assert_eq!(l2[0].end as usize, mn(60, 10 + 48 + 1), "конец давности");
    // граница: возраст ровно ld суток — линия рождается (≤), на сутки и бар больше — нет
    let mut e = bg();
    peaks(&mut e, 60, &[(10, 110.0), (32, 106.0)]);
    let le = long(&e, &cfg(60, 2, 1, 0));
    assert_eq!(le.len(), 1, "возраст 1 сутки ровно: рождается");
    assert_eq!(
        (le[0].born as usize, le[0].end as usize),
        (mn(60, 34), mn(60, 35)),
        "жизнь — один час до конца давности"
    );
    let mut e = bg();
    peaks(&mut e, 60, &[(10, 110.0), (33, 106.0)]);
    assert!(long(&e, &cfg(60, 2, 1, 0)).is_empty(), "1440 + 60 > 1440");
}

#[test]
fn slope_cap_is_measured_once_at_birth_in_natr60_per_hour() {
    let mut c = bg();
    peaks(&mut c, 60, &[(10, 110.0), (20, 108.0)]);
    let off = long(&c, &cfg(60, 2, 3, 0));
    assert_eq!(off.len(), 1, "lsl = 0 — без предела");
    let l = &off[0];
    let s = sym(c.clone());
    let natr = s.natr_for(60).expect("NATR60")[l.born as usize - 1];
    assert!(natr > 0);
    let per_min = (l.segs[l.segs.len() - 1].1 - l.segs[0].1) as f64 / (l.segs.len() - 1) as f64;
    // |наклон| в NATR60 в час: (цена за час / цена линии) / NATR60 доли цены
    let ratio = per_min.abs() * 60.0 / l.segs[0].1 as f64 / (f64::from(natr) / 1e8);
    assert!((0.5..5.0).contains(&ratio), "{ratio}");
    let ok = (ratio * 1000.0).ceil() as i64 + 2;
    let bad = (ratio * 1000.0).floor() as i64 - 2;
    assert_eq!(
        long(&c, &cfg(60, 2, 3, ok)).len(),
        1,
        "предел чуть выше наклона {ratio}"
    );
    assert!(
        long(&c, &cfg(60, 2, 3, bad)).is_empty(),
        "предел чуть ниже наклона {ratio}"
    );
    // версии и конец при подходящем пределе те же, что без предела
    let capped = long(&c, &cfg(60, 2, 3, ok));
    assert_eq!(capped[0].segs, l.segs);
    assert_eq!(capped[0].end, l.end);
}

#[test]
fn bar_close_above_the_line_ends_it_strictly_and_checks_the_bar_last_minute() {
    let tf = 60;
    let pts = ((ab(tf, 10), px(110.0)), (ab(tf, 20), px(108.0)));
    // версия линии на последней минуте бара 40 (минута закрытия бара — первая минута следующего за ним бара минус 1)
    let last = mn(tf, 41) - 1;
    let v = exact2(tf, pts.0, pts.1, last);
    for (close, ends_at_close) in [(v, false), (v + 1, true)] {
        let mut c = bg();
        peaks(&mut c, tf, &[(10, 110.0), (20, 108.0)]);
        // последняя минута бара 40: закрытие `close`, high не ниже закрытия (бар 40 становится пивотом ниже линии — он подтверждается на баре 41)
        c.c[last] = close;
        c.h[last] = close;
        let lv = long(&c, &cfg(tf, 2, 3, 0));
        let l = &lv[0];
        if ends_at_close {
            assert_eq!(
                l.end as usize,
                mn(tf, 41),
                "закрытие на 1e-8 выше линии — бар закрыт за ней"
            );
        } else {
            assert_eq!(
                l.end as usize,
                mn(tf, 42),
                "закрытие ровно на линии — не конец (строго выше); линию кончает подтверждение пивота бара 40"
            );
        }
    }
}

#[test]
fn a_close_above_the_line_between_the_points_blocks_the_birth() {
    let tf = 60;
    let mid = mn(tf, 16) - 1; // последняя минута бара 15
    let line_mid = exact2(tf, (ab(tf, 10), px(110.0)), (ab(tf, 20), px(108.0)), mid);
    for (close, born) in [(line_mid - 1_000_000, true), (line_mid + 1_000_000, false)] {
        let mut c = bg();
        peaks(&mut c, tf, &[(10, 110.0), (20, 108.0)]);
        // бары 14–16 с равными максимумами выше любого из закрытий — пивота между точками нет
        for b in [14, 15, 16] {
            c.h[mn(tf, b)] = line_mid + 1_000_000;
        }
        c.c[mid] = close;
        let lv = long(&c, &cfg(tf, 2, 3, 0));
        assert_eq!(
            !lv.is_empty(),
            born,
            "закрытие {close} против линии {line_mid}"
        );
    }
}

#[test]
fn the_next_pivot_of_the_side_ends_the_line_and_starts_another() {
    let mut c = bg();
    peaks(
        &mut c,
        60,
        &[(10, 110.0), (20, 108.0), (40, 104.0), (50, 109.0)],
    );
    let lv = long(&c, &cfg(60, 2, 3, 0));
    assert_eq!(
        lv.len(),
        2,
        "пара (108; 104) — вторая линия; (104; 109) растёт — третьей нет"
    );
    assert_eq!(lv[0].end, lv[1].born, "линии стыкуются");
    assert_eq!(lv[0].born as usize, mn(60, 22));
    assert_eq!(lv[1].born as usize, mn(60, 42));
    assert_eq!(lv[1].first_bar as usize, ab(60, 20));
    assert_eq!(
        lv[1].end as usize,
        mn(60, 52),
        "пивот бара 50 выше — набор сменился, линии нет"
    );
    assert_ls(&lv[1], 60, &[(20, 108.0), (40, 104.0)]);
}

#[test]
fn thirty_minute_candles_use_their_own_bars() {
    let tf = 30;
    let mut c = bg();
    peaks(&mut c, tf, &[(20, 110.0), (40, 108.0)]);
    let lv = long(&c, &cfg(tf, 2, 1, 0));
    assert_eq!(lv.len(), 1);
    let l = &lv[0];
    // подтверждение пивота бара 40 — закрытие бара 41; давность 1 сутки = 48 баров по 30 минут
    assert_eq!(
        (l.born as usize, l.end as usize, l.first_bar as usize),
        (mn(tf, 42), mn(tf, 20 + 48 + 1), ab(tf, 20))
    );
    for &(m, p) in &l.segs {
        let want = exact2(
            tf,
            (ab(tf, 20), px(110.0)),
            (ab(tf, 40), px(108.0)),
            m as usize,
        );
        assert_eq!(p, want, "минута {m}");
    }
}

#[test]
fn short_side_is_the_mirror_rising_lows() {
    let mut c = bg();
    peaks(&mut c, 60, &[(10, 110.0), (20, 108.0), (30, 107.5)]);
    let m = mirror(&c, 200.0);
    let sh = build_levels(&sym(m.clone()), &cfg(60, 3, 3, 0), Side::Short);
    let lg = long(&c, &cfg(60, 3, 3, 0));
    assert_eq!((lg.len(), sh.len()), (1, 1));
    assert_eq!(
        (sh[0].born, sh[0].end, sh[0].first_bar, sh[0].src),
        (lg[0].born, lg[0].end, lg[0].first_bar, 4)
    );
    assert_eq!(sh[0].segs.len(), lg[0].segs.len());
    for (a, b) in sh[0].segs.iter().zip(&lg[0].segs) {
        assert_eq!(a.0, b.0);
        assert_eq!(
            a.1,
            200 * E8 - b.1,
            "цена линии шорта положительна и зеркальна: растущая поддержка"
        );
    }
    assert!(
        sh[0].segs.last().map(|x| x.1) > sh[0].segs.first().map(|x| x.1),
        "поддержка шорта растёт"
    );
    // стороны не смешиваются: у исходного мира нет растущих минимумов, у зеркального — падающих максимумов
    assert!(build_levels(&sym(c), &cfg(60, 3, 3, 0), Side::Short).is_empty());
    assert!(build_levels(&sym(m), &cfg(60, 3, 3, 0), Side::Long).is_empty());
}

#[test]
fn other_sources_ignore_the_line_fields() {
    for lx in 0..=3u8 {
        let c = bg();
        let mut a = cfg(60, 0, 0, 0);
        a.lx = lx;
        let mut b = a.clone();
        b.lk = 3;
        b.ld = 5;
        b.lsl_m = 700;
        let (x, y) = (long(&c, &a), long(&c, &b));
        assert_eq!(x.len(), y.len(), "lx = {lx}");
        for (p, q) in x.iter().zip(&y) {
            assert_eq!(
                (p.born, p.end, p.first_bar, p.src, &p.segs),
                (q.born, q.end, q.first_bar, q.src, &q.segs)
            );
        }
    }
}

// ───────────────────────────── потребитель: кандидаты и сигналы по версиям минут ─────────────────────────────

struct MemSrc(HashMap<(String, usize), Arc<Vec<Tick>>>);

impl TradeSrc for MemSrc {
    fn day(&self, sym: &str, day: usize) -> Option<Arc<Vec<Tick>>> {
        self.0.get(&(sym.to_string(), day)).cloned()
    }
}

fn tk(m: usize, ms: u32, price: f64) -> Tick {
    Tick::new(px(price), (m % 1440) as u32 * 60_000 + ms, false)
}

/// Параметры сигналов мира: линия по двум точкам, без фильтров пула и без запрета ложного пробоя.
fn sig_params(lk: usize) -> SigCfg {
    let mut p = Params::base();
    p.sig.lv = cfg(60, lk, 3, 0);
    p.sig.natr_min_pm = 0;
    p.sig.t1h_u = 0;
    p.sig.ban = false;
    p.sig
}

/// Мир подхода: линия (110,0 на баре 10; 108,0 на баре 20) рождена с минуты `born = mn(60, 22)`. Минуты подхода — `born + 100`, `+ 130`, `+ 290`: цена
/// на 0,10–0,25 под версией линии этой минуты. Первые минуты баров вокруг подхода подняты над линией на одну и ту же цену (равные максимумы соседних баров —
/// бары подхода не пивоты; минута целиком выше линии кандидата не даёт).
fn approach_world() -> (Cols, Level, [usize; 3]) {
    let mut c = bg();
    peaks(&mut c, 60, &[(10, 110.0), (20, 108.0)]);
    let l = long(&c, &cfg(60, 2, 3, 0)).remove(0);
    let born = l.born as usize;
    let ts = [born + 100, born + 130, born + 290];
    let hi = l.segs[0].1 + 20_000_000;
    for b in ts[0] / 60 - 1..=ts[2] / 60 + 1 {
        let m = b * 60;
        (c.o[m], c.h[m], c.l[m], c.c[m]) = (hi, hi, hi, hi);
    }
    for &t in &ts {
        let v = l.price_at(t);
        c.o[t] = v - 20_000_000;
        c.h[t] = v - 10_000_000;
        c.l[t] = v - 25_000_000;
        c.c[t] = v - 15_000_000;
    }
    (c, l, ts)
}

#[test]
fn candidates_follow_the_per_minute_line_version() {
    let (c, l, ts) = approach_world();
    let s = sym(c);
    let lv = build_levels(&s, &sig_params(2).lv, Side::Long);
    assert_eq!(
        lv[0].segs, l.segs,
        "подход не меняет линию (бары подхода — не пивоты)"
    );
    let mut cs = Vec::new();
    candidates(&s, &lv[..1], &sig_params(2), Side::Long, &mut cs);
    let got: Vec<(usize, i64)> = cs.iter().map(|x| (x.t as usize, x.lp)).collect();
    assert_eq!(
        got,
        ts.iter().map(|&t| (t, l.price_at(t))).collect::<Vec<_>>(),
        "кандидат — в минутах подхода, цена — версия именно этой минуты"
    );
    assert!(
        got[0].1 > got[1].1 && got[1].1 > got[2].1,
        "версии убывают: линия идёт вниз"
    );
    assert!(
        cs.iter().all(|x| x.far == x.lp),
        "журнала членов нет: far = lp"
    );
}

fn run_signals(c: Cols, p: &SigCfg, side: Side, ticks: Vec<(usize, Vec<Tick>)>) -> Vec<TSig> {
    let s = Sym::from_cols("TEST", c, Vec::new());
    let mut m = HashMap::new();
    for (d, v) in ticks {
        m.insert(("TEST".to_string(), d), Arc::new(v));
    }
    let src = MemSrc(m);
    let jobs = vec![(p.clone(), side)];
    let mut outs = vec![Vec::new()];
    let mut ctrs = vec![TickCtr::default()];
    signals_for_coin(&s, 0, &jobs, &src, &mut outs, &mut ctrs);
    outs.remove(0)
}

#[test]
fn signal_price_is_the_line_at_the_signal_minute_and_tn_sees_no_second_level() {
    for flip in [false, true] {
        let side = if flip { Side::Short } else { Side::Long };
        let (c, l, ts) = approach_world();
        let f = |p: f64| if flip { 200.0 - p } else { p };
        let mut by_day: HashMap<usize, Vec<Tick>> = HashMap::new();
        for &t in &ts {
            let v = l.price_at(t) as f64 / 1e8;
            let day = by_day.entry(t / 1440).or_default();
            day.push(tk(t, 0, f(v - 0.20)));
            day.push(tk(t, 10_000, f(v - 0.15))); // выше открытия минуты (для шорта — ниже), в зоне 0,3 %
        }
        let c = if flip { mirror(&c, 200.0) } else { c };
        let mut sg = run_signals(c, &sig_params(2), side, by_day.into_iter().collect());
        sg.sort_by_key(|s| s.t);
        assert_eq!(
            sg.iter().map(|s| s.t as usize).collect::<Vec<_>>(),
            ts.to_vec(),
            "{side:?}"
        );
        for (s, &t) in sg.iter().zip(&ts) {
            // длинная рама: лонг — цена линии, шорт — цена линии со знаком минус; цена шорта зеркальна (200 − цена лонга)
            let want = if flip {
                l.price_at(t) - 200 * E8
            } else {
                l.price_at(t)
            };
            assert_eq!(
                s.lp, want,
                "{side:?}: цена уровня сигнала — версия линии в минуте сигнала {t}"
            );
            assert_eq!(
                (s.far, s.nxt),
                (s.lp, 0),
                "{side:?}: дальнего края и следующего уровня нет"
            );
        }
        assert!(
            sg[0].lp > sg[1].lp && sg[1].lp > sg[2].lp,
            "{side:?}: версии на разных минутах разные — линия идёт против пробоя"
        );
    }
}

#[test]
fn crossing_is_a_trade_at_or_above_the_minute_version_not_the_birth_price() {
    let (c, l, ts) = approach_world();
    let t = ts[2]; // версия минуты на 1,0 ниже цены рождения (200 минут · 1/300 ≈ 0,67 + прежние 100 минут)
    let v = l.price_at(t);
    let born_v = l.segs[0].1;
    assert!(born_v - v > 50_000_000, "линия ушла вниз больше чем на 0,5");
    let day = t / 1440;
    let p = sig_params(2);
    let vf = v as f64 / 1e8;
    // сделка выше версии этой минуты (линия ушла вниз от цены рождения) — линия пересечена, сигнала нет
    let crossed = vec![
        tk(t, 0, vf - 0.20),
        tk(t, 5_000, vf + 0.02),
        tk(t, 10_000, vf - 0.15),
    ];
    let sg = run_signals(c.clone(), &p, Side::Long, vec![(day, crossed)]);
    assert!(
        sg.iter().all(|s| s.t as usize != t),
        "пересечение версии минуты до сделки — сигнала нет"
    );
    // ровно на версии — тоже пересечение (сделка ≥ уровня); на 1e-8 ниже — сигнал есть
    let on = vec![
        tk(t, 0, vf - 0.20),
        Tick::new(v, (t % 1440) as u32 * 60_000 + 5_000, false),
        tk(t, 10_000, vf - 0.15),
    ];
    let sg = run_signals(c.clone(), &p, Side::Long, vec![(day, on)]);
    assert!(
        sg.iter().all(|s| s.t as usize != t),
        "сделка ровно на линии"
    );
    let below = vec![
        tk(t, 0, vf - 0.20),
        Tick::new(v - 1, (t % 1440) as u32 * 60_000 + 5_000, false),
        tk(t, 10_000, vf - 0.15),
    ];
    let sg = run_signals(c, &p, Side::Long, vec![(day, below)]);
    assert_eq!(
        sg.iter().filter(|s| s.t as usize == t).count(),
        1,
        "на 1e-8 ниже линии пересечения нет"
    );
}

// ───────────────────────────── ключи: разбор, отпечаток, ключ кэша ─────────────────────────────

#[test]
fn parse_and_round_trip_of_the_line_keys() {
    let (_, p) = parse_variant("x lx=4 lk=3 ld=5 lsl=1.5 tf=30").expect("разбор");
    let lv = &p.sig.lv;
    assert_eq!(
        (lv.lx, lv.lk, lv.ld, lv.lsl_m, lv.tf_min),
        (4, 3, 5, 1500, 30)
    );
    let (_, p) = parse_variant("x lx=4 lk=2 ld=1 tf=60 k=1 d=0.3").expect("разбор");
    assert_eq!((p.sig.lv.lk, p.sig.lv.ld, p.sig.lv.lsl_m), (2, 1, 0));
    assert!(
        parse_variant("x lx=4 lk=4 ld=3 tf=240").is_ok(),
        "tf из списка 30 / 60 / 240"
    );
    // база: ключей линии нет
    let (_, p) = parse_variant("x").expect("база");
    assert_eq!((p.sig.lv.lk, p.sig.lv.ld, p.sig.lv.lsl_m), (0, 0, 0));
    // отпечаток в meta.json: поля линии печатаются только при lx = 4
    let (_, p) = parse_variant("x lx=4 lk=3 ld=5 lsl=1.5").expect("разбор");
    let dbg = format!("{:?}", p.sig.lv);
    assert!(
        dbg.ends_with("pk: false, lx: 4, lw: 0, lk: 3, ld: 5, lsl_m: 1500 }"),
        "{dbg}"
    );
}

#[test]
fn line_keys_are_rejected_when_inconsistent() {
    for bad in [
        "x lx=4",
        "x lx=4 lk=3",
        "x lx=4 ld=3",
        "x lx=4 lk=1 ld=3",
        "x lx=4 lk=9 ld=3",
        "x lx=4 lk=3 ld=0",
        "x lx=4 lk=3 ld=31",
        "x lx=4 lk=3 ld=3 lsl=-1",
        "x lx=4 lk=3 ld=3 lsl=101",
        "x lx=4 lk=3 ld=3 ap=1",
        "x lk=3",
        "x ld=3",
        "x lsl=1",
        "x lx=1 lk=3",
        "x lx=5",
        "x lx=1 tf=30",
        "x lx=3 tf=240",
        "x lx=2 tf=30 lw=1",
    ] {
        assert!(parse_variant(bad).is_err(), "{bad}");
    }
    for ok in [
        "x lx=4 lk=2 ld=1",
        "x lx=4 lk=8 ld=30 lsl=100",
        "x lx=4 lk=3 ld=3 ap=0 tf=30",
        "x lx=4 lk=3 ld=3 fh=4 pk=1 pb=1",
    ] {
        assert!(parse_variant(ok).is_ok(), "{ok}");
    }
}

#[test]
fn old_configurations_keep_their_fingerprint_and_cache_key() {
    // отпечаток params в meta.json и ключ кэша сигналов у lx = 0…3 — как до D № 1
    let base = Params::base();
    assert_eq!(
        format!("{:?}", base.sig.lv),
        "LevelCfg { tf_min: 60, k: 2, d_pm: 500, n: 2, tau_pm: 200, l_days: 7, pk: false }"
    );
    for lx in 1..=3u8 {
        let mut p = Params::base();
        p.sig.lv.lx = lx;
        p.sig.lv.lw = 2;
        assert_eq!(
            format!("{:?}", p.sig.lv),
            format!(
                "LevelCfg {{ tf_min: 60, k: 2, d_pm: 500, n: 2, tau_pm: 200, l_days: 7, pk: false, lx: {lx}, lw: 2 }}"
            )
        );
        assert!(
            cfg_key_text(&p.sig).starts_with(&format!(
                "lv[tf_min=60,k=2,d_pm=500,n=2,tau_pm=200,l_days=7,pk=false,lx={lx},lw=2],natr_p=30"
            )),
            "{}",
            cfg_key_text(&p.sig)
        );
    }
    // неиспользуемые поля линии у lx ≠ 4 в ключ не попадают
    let mut p = Params::base();
    let k0 = cfg_key_text(&p.sig);
    p.sig.lv.lk = 3;
    p.sig.lv.ld = 5;
    p.sig.lv.lsl_m = 700;
    assert_eq!(cfg_key_text(&p.sig), k0);
    // отпечаток всего варианта (meta.json) при выключенном ключе — тот же, что у базы
    let (_, v) = parse_variant("x lx=1 tf=60").expect("разбор");
    assert!(!format!("{v:?}").contains(" lk:"));
}

#[test]
fn line_fields_change_the_signal_cache_key_only_for_lx4() {
    let mut base = Params::base().sig;
    base.lv.tf_min = 60;
    base.lv.lx = 4;
    base.lv.lk = 3;
    base.lv.ld = 3;
    base.lv.lsl_m = 1000;
    let t0 = cfg_key_text(&base);
    assert!(t0.contains("lx=4,lw=0,lk=3,ld=3,lsl_m=1000]"), "{t0}");
    type Mutator = Box<dyn Fn(&mut SigCfg)>;
    let muts: Vec<(&str, Mutator)> = vec![
        ("lk", Box::new(|c| c.lv.lk += 1)),
        ("ld", Box::new(|c| c.lv.ld += 1)),
        ("lsl_m", Box::new(|c| c.lv.lsl_m += 1)),
        ("tf_min", Box::new(|c| c.lv.tf_min = 30)),
        ("k", Box::new(|c| c.lv.k += 1)),
        ("d_pm", Box::new(|c| c.lv.d_pm += 1)),
    ];
    let mut seen = std::collections::HashSet::new();
    seen.insert(t0.clone());
    for (name, m) in &muts {
        let mut c = base.clone();
        m(&mut c);
        let t = cfg_key_text(&c);
        assert!(seen.insert(t), "поле {name} не меняет ключ линии");
    }
}
