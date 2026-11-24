//! Совместный кластерный бутстрап двух рядов (план §5.2, R08/R50).
//!
//! Гейт G2 (ячейки исхода-и-истории, предикаты принадлежности к ним,
//! `run_cells`, `decide_g2`) снят таском 17: он был построен под ячеечную
//! модель §5.2, которую спека отменила (`interfaces.md`, «Что построено под
//! отменённый дизайн»), и ни один вызывающий не читал его результат — ни
//! `lob backtest` (вердикт G4, `backtest::G4Verdict`/`decide_g4`), ни
//! `lob shortlist`. Из него сохранён
//! ровно один шов, который спека называет переиспользуемым, а не заново
//! пишет: общий кластерный бутстрап двух рядов на одних и тех же весах
//! Уэбба за одну реплику — «совместный ресэмплинг», не два независимых.
//!
//! Расширяется под разные статистики через `combine`:
//! `joint_product_interval` (таск 03, R08/R50) — произведение двух средних
//! (умножение); единственный вызывающий — `costs::net_fill_interval`
//! (совместный интервал `net_fill`). Разностный `disjoint_contrast`
//! (вычитание) без вызывающих удалён в TK-702.
//!
//! `day_sums` — по суткам `(числитель ряда A, знаменатель ряда A,
//! числитель ряда B, знаменатель ряда B)` в фиксированном порядке: одна и
//! та же реплика одним и тем же весом Уэбба взвешивает обе группы за те
//! же сутки. Знаменатели не переразвешиваются репликой — тот же приём,
//! что у `cluster_robust_t_from_summary` в `stats`.
//!
//! Источник весов — общий `crate::stats::SplitMix64`, а не своя копия
//! (`webb_points_are_the_six_point_webb_distribution_bit_for_bit` держит
//! таблицу `WEBB_POINTS` побитово).

use crate::stats::{count_f64_u64, quantile_sorted, SplitMix64};

// ---------------------------------------------------------------------------
// Константы. Seed заморожен, как всё предрегистрированное (A2): один и тот
// же вход даёт один и тот же отчёт.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Детерминированный источник весов Уэбба для интервала контраста.
// ---------------------------------------------------------------------------

/// Шесть точек распределения Уэбба (Decision 9): `-√1.5, -1, -√0.5, √0.5, 1,
/// √1.5`. Источник реплик — общий `crate::stats::SplitMix64` (вторая копия
/// удалена: детектор копипасты её не видел, потому что обёртки назывались
/// по-разному). Таблица точек своя, `WEBB_POINTS`; побитовое совпадение с
/// выражениями `±(1.5).sqrt()`, `±(0.5).sqrt()` держит тест
/// `webb_points_are_the_six_point_webb_distribution_bit_for_bit`.
const WEBB_POINTS: [f64; 6] = [
    -1.224_744_871_391_589,
    -1.0,
    -std::f64::consts::FRAC_1_SQRT_2,
    std::f64::consts::FRAC_1_SQRT_2,
    1.0,
    1.224_744_871_391_589,
];

/// Очередной вес Уэбба из общего генератора: индекс `next_u64 % 6`, при том же
/// seed поток совпадает пореплико (A2). Индекс доказуемо < 6 по построению
/// остатка на любом указателе.
#[allow(clippy::indexing_slicing, clippy::cast_possible_truncation)]
fn next_webb(rng: &mut SplitMix64) -> f64 {
    WEBB_POINTS[(rng.next_u64() % WEBB_POINTS.len() as u64) as usize]
}

// ---------------------------------------------------------------------------
// Дизъюнктный контраст: разность двух взвешенных средних.
// ---------------------------------------------------------------------------

/// Общий шов совместного кластерного бутстрапа двух рядов, расширяемый под
/// разные статистики: дизъюнктный контраст соединяет два взвешенных
/// средних вычитанием, совместный интервал `net_fill` (`joint_product_interval`,
/// таск 03, R08/R50) — умножением. Реплика одна на обе серии: один и тот же
/// вес Уэбба взвешивает суммы обоих рядов за те же сутки — «совместный
/// ресэмплинг», а не два независимых. `day_sums` — по суткам `(числитель
/// ряда A, знаменатель ряда A, числитель ряда B, знаменатель ряда B)` в
/// фиксированном порядке. Знаменатели не переразвешиваются репликой — тот
/// же приём, что у `cluster_robust_t_from_summary` в `stats`: реплика
/// перетряхивает числители, а не сами счётчики наблюдений.
/// `None` — любой из рядов пуст (нулевой знаменатель).
/// Возвращает `(среднее A, знаменатель A, среднее B, знаменатель B, точка
/// = combine(среднее A, среднее B), реплики combine, отсортированные по
/// возрастанию)`.
/// Деление на счётчики точное: числа наблюдений в памяти, далеко от 2^53.
/// Ёмкость из `u32`: `u32` вкладывается в `usize` на всех целях со std.
#[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
fn joint_two_series_bootstrap(
    day_sums: &[(f64, u64, f64, u64)],
    replications: u32,
    seed: u64,
    combine: impl Fn(f64, f64) -> f64,
) -> Option<(f64, u64, f64, u64, f64, Vec<f64>)> {
    let (tot_a, n_a, tot_b, n_b) =
        day_sums
            .iter()
            .fold((0.0, 0u64, 0.0, 0u64), |(sa, ca, sb, cb), d| {
                (
                    sa + d.0,
                    ca.saturating_add(d.1),
                    sb + d.2,
                    cb.saturating_add(d.3),
                )
            });
    if n_a == 0 || n_b == 0 {
        return None;
    }
    let mean_a = tot_a / count_f64_u64(n_a);
    let mean_b = tot_b / count_f64_u64(n_b);
    let point = combine(mean_a, mean_b);
    let mut reps = Vec::with_capacity(replications as usize);
    if replications == 0 {
        reps.push(point);
    } else {
        let mut rng = SplitMix64::new(seed);
        for _ in 0..replications {
            let mut boot_a = 0.0;
            let mut boot_b = 0.0;
            for d in day_sums {
                let w = next_webb(&mut rng);
                boot_a += w * d.0;
                boot_b += w * d.2;
            }
            let r = combine(boot_a / count_f64_u64(n_a), boot_b / count_f64_u64(n_b));
            // Веса и суммы конечны по построению входа: вырожденная реплика
            // здесь невозможна арифметически, проверка — страховка A2.
            debug_assert!(r.is_finite(), "совместная реплика обязана быть конечной");
            reps.push(r);
        }
    }
    reps.sort_by(|a, b| a.total_cmp(b));
    Some((mean_a, n_a, mean_b, n_b, point, reps))
}

/// Итог совместного интервала произведения двух средних (таск 03, R08/R50):
/// расширение `joint_two_series_bootstrap` с `combine = умножение` вместо
/// вычитания дизъюнктного контраста. Публична только сигнатура, нужная
/// `costs::net_fill_interval` — сама статистика (среднее A × среднее B)
/// нейтральна к тому, что именно A и B значат для вызывающего.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct JointProduct {
    /// Знаменатель ряда A (например, число исполнившихся входов).
    pub n_a: u64,
    /// Знаменатель ряда B (например, общее число попыток).
    pub n_b: u64,
    /// Среднее ряда A по наблюдённым (невзвешенным) суммам.
    pub mean_a: f64,
    /// Среднее ряда B по наблюдённым (невзвешенным) суммам.
    pub mean_b: f64,
    /// Точка: `mean_a * mean_b` на наблюдённых данных.
    pub point: f64,
    /// Нижний перцентиль совместных реплик `combine` на уровне `alpha`
    /// (односторонний: то, что сравнивается с нулём в вызывающем гейте).
    pub lower: f64,
    /// Число реплик.
    pub replications: u32,
    /// Seed реплик.
    pub seed: u64,
}

/// Совместный интервал произведения `mean_a * mean_b` на общих кластерных
/// репликах Уэбба — расширение `joint_two_series_bootstrap` с
/// `combine = умножение`. `alpha` — односторонний
/// уровень нижнего перцентиля, обязательный параметр: спека (R08/R50) не
/// называет число для этого гейта — вызывающий обязан передать своё.
/// `None` — любой из рядов пуст.
#[allow(clippy::cast_precision_loss)]
pub(crate) fn joint_product_interval(
    day_sums: &[(f64, u64, f64, u64)],
    alpha: f64,
    replications: u32,
    seed: u64,
) -> Option<JointProduct> {
    debug_assert!((0.0..=1.0).contains(&alpha), "альфа интервала вне [0,1]");
    let (mean_a, n_a, mean_b, n_b, point, reps) =
        joint_two_series_bootstrap(day_sums, replications, seed, |a, b| a * b)?;
    Some(JointProduct {
        n_a,
        n_b,
        mean_a,
        mean_b,
        point,
        lower: quantile_sorted(&reps, alpha),
        replications,
        seed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Реплик в тестах: сетка от их числа не зависит по существу
    /// (свойство перебора весовых векторов, см. `stats`), а прогон в
    /// десять раз быстрее.
    const TEST_REPLICATIONS: u32 = 999;

    /// Seed заморожен, как всё предрегистрированное (A2): один и тот же вход
    /// даёт один и тот же отчёт.
    const CELLS_SEED: u64 = 2026_0502;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    /// Таск 03 (R08/R50): `joint_product_interval` — два кластера (сутки) с
    /// известными числами проверяют формулу буквально:
    /// `mean_a = (10+20)/(4+6) = 3.0`, `mean_b = (5+15)/(4+6) = 2.0`,
    /// точка `= 6.0`. Разные seed обязаны дать разные (но конечные) нижние
    /// границы — иначе seed декоративен.
    #[test]
    fn joint_product_interval_matches_hand_computed_point_and_reacts_to_seed() {
        let day_sums = [(10.0, 4u64, 5.0, 4u64), (20.0, 6u64, 15.0, 6u64)];
        let jp = joint_product_interval(&day_sums, 0.05, TEST_REPLICATIONS, CELLS_SEED)
            .expect("обе серии непусты");
        assert_eq!((jp.n_a, jp.n_b), (10, 10));
        assert!(close(jp.mean_a, 3.0), "mean_a = 30/10");
        assert!(close(jp.mean_b, 2.0), "mean_b = 20/10");
        assert!(close(jp.point, 6.0), "точка = mean_a*mean_b = 3*2");
        assert!(jp.lower.is_finite(), "нижняя граница обязана быть конечной");
        assert!(
            jp.lower <= jp.point,
            "нижний перцентиль не может быть выше точки на положительном сигнале"
        );

        let other_seed = joint_product_interval(&day_sums, 0.05, TEST_REPLICATIONS, CELLS_SEED + 1)
            .expect("обе серии непусты");
        assert_ne!(
            jp.lower, other_seed.lower,
            "другой seed обязан дать другую реплику нижней границы"
        );

        assert_eq!(
            joint_product_interval(&[], 0.05, TEST_REPLICATIONS, CELLS_SEED),
            None
        );
    }

    /// Шесть точек Уэбба записаны в `WEBB_POINTS` литералами и
    /// `FRAC_1_SQRT_2`, здесь — выражениями `±(1.5).sqrt()`, `±(0.5).sqrt()`:
    /// сравнение по `to_bits`, а не по `==` — равенство f64 здесь и есть
    /// предмет проверки (правка одного литерала сдвинула бы поток весов
    /// молча). Прежний двойник `stats::webb_weight` удалён вместе с
    /// мёртвым wild-бутстрапом t.
    #[test]
    fn webb_points_are_the_six_point_webb_distribution_bit_for_bit() {
        let expected = [
            -(1.5_f64).sqrt(),
            -1.0,
            -(0.5_f64).sqrt(),
            (0.5_f64).sqrt(),
            1.0,
            (1.5_f64).sqrt(),
        ];
        assert_eq!(WEBB_POINTS.len(), expected.len());
        for (i, (got, want)) in WEBB_POINTS.iter().zip(expected).enumerate() {
            assert_eq!(got.to_bits(), want.to_bits(), "вес {i} разошёлся побитово");
        }
    }

    /// Граница модулей в духе шагов 1.1/4.1/5.1: анализ не знает про транспорт
    /// и часы. Вещественное число здесь разрешено формулой (bps, интервал),
    /// поэтому его нет среди запрещённых — в отличие от списка уровней.
    ///
    /// Запрещённые фрагменты собраны из частей: литерал целиком триггерил бы
    /// эту же проверку сам на себя.
    #[test]
    fn module_stays_detached_from_transport_and_clocks() {
        const SRC: &str = include_str!("cells.rs");
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
}
