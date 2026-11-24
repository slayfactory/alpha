use super::*;

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

/// В-63 (условия счёта владельца 2026-09-18): мейкер 1.4 / тейкер 3.5 bps,
/// возврат 10 %; круг мейкер-тейкер после возврата — одно число 4.41 bps для
/// markout-метрик (Decision 19), ноги — `leg_fee_bps`.
#[test]
fn roundtrip_fees_are_single_number_for_g0_and_g3() {
    assert!(close(MAKER_FEE_BPS, 1.4));
    assert!(close(TAKER_FEE_BPS, 3.5));
    assert!(close(FEE_REBATE_SHARE, 0.10));
    assert!(close(ROUNDTRIP_FEES_BPS, 4.41));
    assert!(close(leg_fee_bps(false), 1.26));
    assert!(close(leg_fee_bps(true), 3.15));
    assert!(close(
        leg_fee_bps(false) + leg_fee_bps(true),
        ROUNDTRIP_FEES_BPS
    ));
}

/// H6 — зелёный порог — число само по себе, 3 bps: вердикт по нему делает
/// вызывающий (`bounce_verdict`), не этот модуль.
#[test]
fn green_threshold_is_three_bps() {
    assert!(close(GREEN_NET_BPS, 3.0));
}

fn fobs(day: i64, net: f64, filled: bool) -> FillObservation {
    FillObservation {
        day_cluster: day,
        net_bps: net,
        filled,
    }
}

/// `NetFillInterval.point_bps` обязан совпасть с прямой формулой
/// `Σ netᵢ·fillᵢ / N` на тех же данных (тождество разложения
/// `mean(net|fill=1)·fill_rate`, см. докстринг `NetFillInterval`), а нижняя граница — быть конечной и не
/// выше точки при явно положительном сигнале на многих сутках.
#[test]
fn net_fill_interval_point_matches_direct_formula() {
    let mut data = Vec::new();
    for day in 0..8i64 {
        data.push(fobs(day, 10.0, true));
        data.push(fobs(day, 8.0, true));
        data.push(fobs(day, -1.0, false));
    }
    // Σ netᵢ·fillᵢ / N на сутки: (10 + 8) · 8 дней / 24 наблюдения = 6.0.
    let direct = 6.0;
    let interval = net_fill_interval(&data, 0.05, 999, 7).expect("обязано посчитаться");
    assert!(
        close(interval.point_bps, direct),
        "точка интервала {} обязана совпасть с прямой формулой {direct}",
        interval.point_bps
    );
    assert_eq!(interval.n_filled, 16);
    assert_eq!(interval.n_total, 24);
    assert!(interval.lower_bps.is_finite());
    assert!(interval.lower_bps <= interval.point_bps);
}

/// Отказы интервала: пустой срез, неконечный `net_bps`, и (отдельно)
/// нет ни одного исполнения — числителю `mean(net|fill=1)` не из чего
/// считаться, `joint_product_interval` обязан вернуть `None` по своему
/// собственному правилу «ряд пуст».
#[test]
fn net_fill_interval_refuses_on_empty_nonfinite_or_no_fills() {
    assert_eq!(net_fill_interval(&[], 0.05, 999, 1), None);
    assert_eq!(
        net_fill_interval(&[fobs(0, f64::NAN, true)], 0.05, 999, 1),
        None
    );
    let no_fills = [fobs(0, 5.0, false), fobs(0, 6.0, false)];
    assert_eq!(net_fill_interval(&no_fills, 0.05, 999, 1), None);
}

/// Шесть точек Уэбба `-√1.5, -1, -√0.5, √0.5, 1, √1.5` — независимая от
/// `cells::WEBB_POINTS` запись выражениями (прежний `stats::webb_weight`
/// удалён вместе с мёртвым wild-бутстрапом t).
fn webb_point(index: u32) -> f64 {
    match index {
        0 => -(1.5_f64).sqrt(),
        1 => -1.0,
        2 => -(0.5_f64).sqrt(),
        3 => (0.5_f64).sqrt(),
        4 => 1.0,
        _ => (1.5_f64).sqrt(),
    }
}

/// Наивный процентильный нижний перцентиль ОДНОГО ряда при кластерном
/// wild-бутстрапе — контрпример, от которого предостерегает критерий
/// приёмки: если вызвать это отдельно для «net при исполнении» и
/// отдельно для «доли исполнений» с независимыми seed, а потом
/// перемножить нижние границы, кросс-суточная связь между рядами
/// пропадает без следа (в отличие от `net_fill_interval`, где один и
/// тот же вес Уэбба взвешивает оба ряда в одной реплике). Веса — тот же
/// `stats::webb_weight` по тому же индексу `next_u64() % 6`, каким
/// пользуется и `cells::next_webb`: тест не заводит второй генератор,
/// а собирает его из уже открытых внутри крейта частей `stats` — так
/// же, как это делает сам `cells.rs` (см. докстринг `next_webb`).
#[allow(clippy::cast_possible_truncation)]
fn naive_series_lower(
    day_sums: &[(f64, u64)],
    denom: u64,
    alpha: f64,
    replications: u32,
    seed: u64,
) -> f64 {
    let mut rng = crate::stats::SplitMix64::new(seed);
    let mut reps: Vec<f64> = Vec::with_capacity(replications as usize);
    for _ in 0..replications {
        let mut boot = 0.0;
        for &(num, _n) in day_sums {
            let idx = (rng.next_u64() % 6) as u32;
            boot += webb_point(idx) * num;
        }
        reps.push(boot / count_f64_u64(denom));
    }
    reps.sort_by(|a, b| a.total_cmp(b));
    let n = reps.len();
    let pos = alpha * (n - 1) as f64;
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    if lo == hi {
        reps[lo]
    } else {
        reps[lo] + (reps[hi] - reps[lo]) * (pos - lo as f64)
    }
}

/// Наблюдения для `DAYS` суток, где сутки чередуют два известных режима:
/// «сильный» (высокий `net`, высокая доля исполнений) и «слабый»
/// (отрицательный `net`, низкая доля исполнений) — известная по
/// построению положительная ковариация между `net` и `fill` день ко дню
/// (бриф: «`fill` коррелирует с исходом»). `regime_plus(day)` решает,
/// какой режим у суток `day`.
fn regime_observations(
    days: i64,
    n_total: u64,
    net_plus: f64,
    n_filled_plus: u64,
    net_minus: f64,
    n_filled_minus: u64,
    regime_plus: impl Fn(i64) -> bool,
) -> Vec<FillObservation> {
    let mut out = Vec::with_capacity(days as usize * n_total as usize);
    for day in 0..days {
        let (net_d, n_filled_d) = if regime_plus(day) {
            (net_plus, n_filled_plus)
        } else {
            (net_minus, n_filled_minus)
        };
        for i in 0..n_total {
            out.push(fobs(day, net_d, i < n_filled_d));
        }
    }
    out
}

/// То же самое разложением на два ряда по суткам — то, что кормит
/// «наивный» контрпример напрямую (числитель/знаменатель, без прохода
/// по `n_total` наблюдениям на сутки).
fn regime_day_sums(
    days: i64,
    n_total: u64,
    net_plus: f64,
    n_filled_plus: u64,
    net_minus: f64,
    n_filled_minus: u64,
    regime_plus: impl Fn(i64) -> bool,
) -> Vec<(f64, u64, f64, u64)> {
    (0..days)
        .map(|day| {
            let (net_d, n_filled_d) = if regime_plus(day) {
                (net_plus, n_filled_plus)
            } else {
                (net_minus, n_filled_minus)
            };
            (
                net_d * count_f64_u64(n_filled_d),
                n_filled_d,
                count_f64_u64(n_filled_d),
                n_total,
            )
        })
        .collect()
}

/// R08/R50, критерий 1: синтетика с известной ковариацией между `net`
/// (при исполнении) и долей исполнений — сутки чередуют «сильный» и
/// «слабый» режим поровну, так что истинный `net_fill` в популяции
/// известен по замкнутой форме: `τ = 0.5·(netₚ·nₚ + netₘ·nₘ)/n_total`
/// (доказательство в докстринге функции: при равновероятном режиме
/// `E[Σ netᵢfillᵢ]/N` телескопируется ровно в эту сумму). Через много
/// независимых выборок (разные сутки-режимы по монете на каждый прогон)
/// наивное произведение отдельно пробутстрапленных границ и совместный
/// интервал обязаны разойтись, а совместная нижняя граница — накрывать
/// `τ` не реже заявленной частоты `1 − alpha` (валидная односторонняя
/// граница может быть консервативнее, но не чаще недокрывать).
#[test]
fn joint_interval_diverges_from_naive_product_and_covers_truth_under_known_covariance() {
    const N_TOTAL: u64 = 100;
    const N_FILLED_PLUS: u64 = 80;
    const N_FILLED_MINUS: u64 = 20;
    const NET_PLUS: f64 = 8.0;
    const NET_MINUS: f64 = -6.0;
    const DAYS: i64 = 10;
    const ALPHA: f64 = 0.05;
    const REPLICATIONS: u32 = 300;
    const OUTER: u32 = 300;

    let true_net_fill = 0.5
        * (NET_PLUS * count_f64_u64(N_FILLED_PLUS) + NET_MINUS * count_f64_u64(N_FILLED_MINUS))
        / count_f64_u64(N_TOTAL);
    assert!(
        close(true_net_fill, 2.6),
        "замкнутая форма — свидетельство теста"
    );

    let mut joint_covers = 0u32;
    let mut sum_abs_gap = 0.0;
    let mut reversed_verdict = 0u32; // naive says "окупается", joint — нет.

    for trial in 0..OUTER {
        let mut coin = crate::stats::SplitMix64::new(9_000_000 + u64::from(trial));
        // Однократный бросок монеты на сутки, тот же порядок для
        // наблюдений и для их суточного разложения.
        let flips: Vec<bool> = (0..DAYS)
            .map(|_| coin.next_u64().is_multiple_of(2))
            .collect();
        let regime = |day: i64| flips[day as usize];

        let observations = regime_observations(
            DAYS,
            N_TOTAL,
            NET_PLUS,
            N_FILLED_PLUS,
            NET_MINUS,
            N_FILLED_MINUS,
            regime,
        );
        let interval = net_fill_interval(
            &observations,
            ALPHA,
            REPLICATIONS,
            1_000_000 + u64::from(trial),
        )
        .expect("оба режима дают исполнения");

        let day_sums = regime_day_sums(
            DAYS,
            N_TOTAL,
            NET_PLUS,
            N_FILLED_PLUS,
            NET_MINUS,
            N_FILLED_MINUS,
            regime,
        );
        let total_n_filled: u64 = day_sums.iter().map(|d| d.1).sum();
        let a_series: Vec<(f64, u64)> = day_sums.iter().map(|d| (d.0, d.1)).collect();
        let b_series: Vec<(f64, u64)> = day_sums.iter().map(|d| (d.2, d.3)).collect();
        let naive_a = naive_series_lower(
            &a_series,
            total_n_filled,
            ALPHA,
            REPLICATIONS,
            2_000_000 + u64::from(trial),
        );
        let naive_b = naive_series_lower(
            &b_series,
            u64::try_from(DAYS).unwrap_or(0) * N_TOTAL,
            ALPHA,
            REPLICATIONS,
            3_000_000 + u64::from(trial),
        );
        let naive_lower = naive_a * naive_b;

        sum_abs_gap += (interval.lower_bps - naive_lower).abs();
        if interval.lower_bps <= true_net_fill {
            joint_covers += 1;
        }
        if naive_lower > 0.0 && interval.lower_bps <= 0.0 {
            reversed_verdict += 1;
        }
    }

    let mean_abs_gap = sum_abs_gap / f64::from(OUTER);
    assert!(
        mean_abs_gap > 1.0,
        "наивное произведение и совместный интервал обязаны расходиться заметно: {mean_abs_gap}"
    );

    let joint_coverage = f64::from(joint_covers) / f64::from(OUTER);
    assert!(
        joint_coverage >= 1.0 - ALPHA - 0.05,
        "совместный интервал обязан накрывать истину не реже заявленной частоты {}: получено {joint_coverage}",
        1.0 - ALPHA
    );

    let reversal_rate = f64::from(reversed_verdict) / f64::from(OUTER);
    assert!(
        reversal_rate >= 0.8,
        "в большинстве прогонов наивный метод обязан ошибочно объявлять «окупается», \
         пока совместный корректно воздерживается: доля {reversal_rate}"
    );
}

/// R08/R50, критерий 2: при отрицательной нижней границе `net`
/// (составляющей `net_fill`) наивное произведение отдельно
/// пробутстрапленных границ даёт бессмыслицу — умножение двух
/// «не уверены, что положительно» границ (`net`-компонента и доля
/// исполнений обе дают отрицательную наивную нижнюю границу) молча
/// переворачивает знак и выдаёт уверенно положительное число, — тогда
/// как совместный интервал остаётся отрицательным, то есть корректно
/// отказывается признать профиль окупившимся. Сутки чередуются 5/5 —
/// детерминированный синтетический ряд, без монеты, seed только у
/// самих реплик (A2).
#[test]
fn naive_product_flips_sign_when_net_component_lower_bound_is_negative() {
    const N_TOTAL: u64 = 100;
    const N_FILLED_PLUS: u64 = 80;
    const N_FILLED_MINUS: u64 = 20;
    const NET_PLUS: f64 = 8.0;
    const NET_MINUS: f64 = -6.0;
    const DAYS: i64 = 10;
    const ALPHA: f64 = 0.05;
    const REPLICATIONS: u32 = 999;
    let regime = |day: i64| day % 2 == 0;

    let observations = regime_observations(
        DAYS,
        N_TOTAL,
        NET_PLUS,
        N_FILLED_PLUS,
        NET_MINUS,
        N_FILLED_MINUS,
        regime,
    );
    let interval = net_fill_interval(&observations, ALPHA, REPLICATIONS, 7)
        .expect("оба режима дают исполнения");
    assert!(
        close(interval.point_bps, 2.6),
        "точка — та же замкнутая форма"
    );

    let day_sums = regime_day_sums(
        DAYS,
        N_TOTAL,
        NET_PLUS,
        N_FILLED_PLUS,
        NET_MINUS,
        N_FILLED_MINUS,
        regime,
    );
    let total_n_filled: u64 = day_sums.iter().map(|d| d.1).sum();
    let a_series: Vec<(f64, u64)> = day_sums.iter().map(|d| (d.0, d.1)).collect();
    let b_series: Vec<(f64, u64)> = day_sums.iter().map(|d| (d.2, d.3)).collect();
    let naive_a = naive_series_lower(&a_series, total_n_filled, ALPHA, REPLICATIONS, 8);
    let naive_b = naive_series_lower(
        &b_series,
        u64::try_from(DAYS).unwrap_or(0) * N_TOTAL,
        ALPHA,
        REPLICATIONS,
        9,
    );
    let naive_lower = naive_a * naive_b;

    assert!(
        naive_a < 0.0,
        "триггер критерия: нижняя граница net-компоненты отрицательна, получено {naive_a}"
    );
    assert!(
        naive_lower > 0.0,
        "бессмыслица наивного метода: произведение двух неуверенных границ \
         {naive_a} и {naive_b} обязано перевернуть знак в плюс, получено {naive_lower}"
    );
    assert!(
        interval.lower_bps <= 0.0,
        "совместный метод обязан остаться корректным (не признавать окупаемость): {}",
        interval.lower_bps
    );
    assert!(
        interval.lower_bps < naive_lower,
        "совместная граница {} обязана быть заметно ниже бессмысленной наивной {naive_lower}",
        interval.lower_bps
    );
}

/// Граница модулей в духе шагов 1.1/5.2: чистая логика не знает про
/// транспорт и часы. Проверка — грепом по собственному исходнику.
#[test]
fn module_stays_detached_from_transport_and_clocks() {
    const SRC: &str = include_str!("../costs.rs");
    let banned = [
        concat!("by", "bit"),
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("std::", "time"),
    ];
    for b in banned {
        assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
    }
}
