//! Режимы порога В-61: «от N» в деньгах, сила «×соседи», оба.

use super::*;

// ---------------------------------------------------------------------------
// Режимы порога В-61 (владелец 2026-09-18): «от N» в деньгах, сила «×соседи», оба.
// ---------------------------------------------------------------------------

pub(super) fn live_count(tr: &LevelTracker) -> usize {
    let mut open = Vec::new();
    tr.live_levels(&mut open);
    open.len()
}

/// Денежный пол: тик $1, лот 1 — уровень на цене 10 с размером 9 ($90) не
/// рождается при N = $100, с размером 10 ($100) — рождается (не ниже, а не
/// строго выше). На цене 1000 хватает и одного лота.
#[test]
fn notional_mode_births_by_price_times_size() {
    let mode = H3Mode::Notional {
        min_usd_e9: 100 * 1_000_000_000,
        tick_e9: 1_000_000_000,
        step_e9: 1_000_000_000,
    };
    let cfg = LevelsConfig {
        mode,
        warmup_ms: HOUR_MS,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut out = Vec::new();
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10, 9)], &mut out);
    assert_eq!(live_count(&tr), 0, "$90 < $100");
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10, 10)], &mut out);
    assert_eq!(live_count(&tr), 1, "$100 ≥ $100");
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(1000, 1)], &mut out);
    assert_eq!(live_count(&tr), 1, "$1000 одним лотом");
    assert_eq!(out.len(), 0, "без прогрева, но и без смертей");
}

/// Сила «×соседи»: 300 % от среднего соседа в ±20 bps. У бидов кадр идёт
/// сверху вниз, у асков — снизу вверх; окно от цены 10000 и 20 bps — 20 тиков.
#[test]
fn strength_mode_compares_a_level_with_its_neighbours() {
    let mode = H3Mode::Strength {
        pct_e2: 300 * 100,
        window_bps_e2: 20 * 100,
    };
    let cfg = LevelsConfig {
        mode,
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut out = Vec::new();
    // Бид 10000×100 против соседей 9990×10 и 9985×10: 100/10 = 1000 % — рождается;
    // сами соседи (10 против среднего (100+10)/2 = 55 — 18 %) — нет.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(
        1000,
        Side::Bid,
        &[ob(10000, 100), ob(9990, 10), ob(9985, 10)],
        &mut out,
    );
    assert_eq!(live_count(&tr), 1);
    // Сосед за окном (9970 — 30 тиков) не считается: уровень один — силы нет.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10000, 100), ob(9970, 1)], &mut out);
    assert_eq!(live_count(&tr), 0, "без соседей в окне сила не определена");
    // Аски (тики растут): та же картина зеркально.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(
        1000,
        Side::Ask,
        &[ob(10000, 100), ob(10010, 10), ob(10015, 10)],
        &mut out,
    );
    assert_eq!(live_count(&tr), 1);
    // Ровно 300 % проходит (не строго): 30 против соседей 10 и 10.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(
        1000,
        Side::Ask,
        &[ob(10000, 30), ob(10010, 10), ob(10015, 10)],
        &mut out,
    );
    assert_eq!(live_count(&tr), 1, "граница включительная");
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(
        1000,
        Side::Ask,
        &[ob(10000, 29), ob(10010, 10), ob(10015, 10)],
        &mut out,
    );
    assert_eq!(live_count(&tr), 0, "290 % — мало");
}

/// «Оба» — И: денежный пол пройден, сила нет — уровня нет; и наоборот.
#[test]
fn both_mode_requires_notional_and_strength_together() {
    let both = H3Mode::Both {
        min_usd_e9: 100 * 1_000_000_000,
        tick_e9: 1_000_000_000,
        step_e9: 1_000_000_000,
        pct_e2: 300 * 100,
        window_bps_e2: 20 * 100,
    };
    let cfg = LevelsConfig {
        mode: both,
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut out = Vec::new();
    // Цена 10000, размер 100: $1 000 000 ≥ $100 и 1000 % силы — рождается.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10000, 100), ob(9990, 10)], &mut out);
    assert_eq!(live_count(&tr), 1);
    // Размер 100 против соседа 100: сила 100 % — нет, хотя номинал есть.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10000, 100), ob(9990, 100)], &mut out);
    assert_eq!(live_count(&tr), 0);
    // Цена 1, размер 50 против соседа 10: сила есть, номинала $50 нет.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(1, 50), ob(1, 10)], &mut out);
    assert_eq!(live_count(&tr), 0);
}

/// Сила «×соседи» как ось: кадр бидов 10000×100, 9990×10, 9985×10 — в ±20 bps
/// (20 тиков) у первого два соседа по 10 ⇒ 1000 %; у второго соседи 100 и 10 ⇒
/// 18.18 %; в ±10 bps (10 тиков) у третьего (9985) сосед только 9990 ⇒ 100 %;
/// одинокий уровень — `-1`.
#[test]
fn neighbour_strength_axis_is_percent_of_mean_neighbour() {
    let levels = [ob(10000, 100), ob(9990, 10), ob(9985, 10)];
    let mut prefix = vec![0i64];
    for o in &levels {
        let last = *prefix.last().unwrap();
        prefix.push(last + o.size_lots);
    }
    assert_eq!(
        neighbour_strength_e2(&levels, &prefix, 0, 20 * 100),
        100_000
    );
    assert_eq!(neighbour_strength_e2(&levels, &prefix, 1, 20 * 100), 1_818);
    assert_eq!(neighbour_strength_e2(&levels, &prefix, 2, 10 * 100), 10_000);
    let lone = [ob(10000, 100), ob(9000, 5)];
    let prefix = vec![0i64, 100, 105];
    assert_eq!(neighbour_strength_e2(&lone, &prefix, 0, 20 * 100), -1);
}

/// История силы: бид 10000 с соседом 9990 — сила 100/10 = 1000 %; на втором
/// кадре сосед вырос до 100 — сила 100/((5+100)/2) = 190.47 % (в ±20 bps от
/// 10000 сосед и 10010 с 5 лотами), дальше снова 10. Касание через 5 с после
/// рождения (уровень стал лучшим после ухода): «сейчас» 1000 %, минимум за 1 с
/// — 1000 %, за 5 с — 190.47 % (провал внутри окна), за 15 и 60 с — уровень
/// моложе окна: `-1`. `repeat_count` — 0, рождений на этой цене до этого не было.
#[test]
fn strength_history_keeps_the_minimum_over_the_window() {
    let cfg = LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 1 },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut tr = LevelTracker::new(cfg);
    let mut out = Vec::new();
    let mut touches = Vec::new();
    let quiet = [ob(10010, 5), ob(10000, 100), ob(9990, 10)];
    let dip = [ob(10010, 5), ob(10000, 100), ob(9990, 100)];
    tr.observe_frame_with_touches(1000, Side::Bid, &quiet, &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, Side::Bid, &dip, &mut out, &mut touches);
    for ts in [3000, 4000, 5000] {
        tr.observe_frame_with_touches(ts, Side::Bid, &quiet, &mut out, &mut touches);
    }
    // 6000: 10010 ушёл — уровень 10000 стал лучшим: касание в возрасте 5 с.
    tr.observe_frame_with_touches(
        6000,
        Side::Bid,
        &[ob(10000, 100), ob(9990, 10)],
        &mut out,
        &mut touches,
    );
    // 7000: цена ушла выше — касание кончилось.
    tr.observe_frame_with_touches(
        7000,
        Side::Bid,
        &[ob(10020, 5), ob(10000, 100), ob(9990, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1, "{touches:?}");
    let t = &touches[0];
    assert_eq!(t.price_tick, 10000);
    assert_eq!(t.strength_e2[1], 100_000, "сейчас 1000 % в ±20 bps");
    assert_eq!(t.strength_held_e2[0], 100_000, "за 1 с провала нет");
    assert_eq!(t.strength_held_e2[1], 19_047, "за 5 с минимум — 190.47 %");
    assert_eq!(t.strength_held_e2[2], -1, "уровень моложе 15 с");
    assert_eq!(t.strength_held_e2[3], -1, "уровень моложе 60 с");
    assert_eq!(t.repeat_count, 0);
}

/// Порог в момент касания (В-66, база E1): номинал — по размеру при касании,
/// сила — по `strength_e2` окна порога; окно не из оси — `None`.
#[test]
fn threshold_is_rechecked_at_touch_time() {
    let mut t = touch_rec(100, 0, (2000, 3000), (12, 10), 3, false, 1);
    t.strength_e2 = [-1, 15_000, 20_000]; // ±20 bps — 150 %
    let both = H3Mode::Both {
        min_usd_e9: 5_000_000_000, // $5: цена 100 тиков × 0.01 = $1, шаг лота 1 → порог 5 лотов
        tick_e9: 10_000_000,
        step_e9: 1_000_000_000,
        pct_e2: 10_000,
        window_bps_e2: 2_000,
    };
    assert_eq!(both.holds_at_touch(&t), Some(true));
    t.strength_e2 = [-1, 9_900, 20_000];
    assert_eq!(
        both.holds_at_touch(&t),
        Some(false),
        "сила ниже порога при касании"
    );
    t.strength_e2 = [-1, 15_000, 20_000];
    t.size_at_touch = 1; // $1 < $5
    assert_eq!(
        both.holds_at_touch(&t),
        Some(false),
        "номинал ниже пола при касании"
    );
    let odd_window = H3Mode::Strength {
        pct_e2: 10_000,
        window_bps_e2: 1_500,
    };
    assert_eq!(odd_window.holds_at_touch(&t), None, "окно 15 bps не из оси");
    let floor = H3Mode::Floor { h3_lots: 5 };
    t.size_at_touch = 12;
    assert_eq!(floor.holds_at_touch(&t), Some(true));
}
