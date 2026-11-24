use super::*;

fn close(a: f64, b: f64, tol: f64) -> bool {
    (a - b).abs() <= tol
}

/// Нормальное распределение сверяется с опубликованными процентилями, а
/// не с самим собой: 99-й процентиль 2.3263, медиана 0, плюс круговой
/// обход прямой и обратной функций.
#[test]
fn normal_cdf_matches_published_percentiles_and_roundtrips() {
    assert!(close(normal_cdf(0.0), 0.5, 1e-9));
    assert!(close(normal_inv_cdf(0.5), 0.0, 1e-9));
    assert!(
        close(normal_inv_cdf(0.99), 2.3263, 1e-4),
        "99-й процентиль обязан быть 2.3263, получили {}",
        normal_inv_cdf(0.99)
    );
    for x in [-2.0, -1.0, -0.25, 0.75, 1.5] {
        // Допуск 1e-5, а не 1e-6: ошибка erf (≤ 1.5e-7) усиливается
        // делением на плотность в точке (при |x| = 2 даёт ~1.4e-6), и
        // более тонкий допуск проверял бы аппроксимацию, а не свойство.
        assert!(
            close(normal_inv_cdf(normal_cdf(x)), x, 1e-5),
            "круговой обход сломан на {x}"
        );
    }
    assert_eq!(normal_inv_cdf(0.0), f64::NEG_INFINITY);
    assert_eq!(normal_inv_cdf(1.0), f64::INFINITY);
}

/// Моменты на [1,2,3,4] считаются руками: среднее 2.5, дисперсия 1.25,
/// Sharpe 2.5/√1.25, скошенность 0 (симметрия), эксцесс 1.64.
#[test]
fn moments_match_hand_computation_on_1_2_3_4() {
    let m = moments(&[1.0, 2.0, 3.0, 4.0]).unwrap();
    assert!(close(m.mean, 2.5, 1e-12));
    assert!(close(m.std, 1.25_f64.sqrt(), 1e-12));
    assert!(close(m.mean / m.std, 2.236_067_977_5, 1e-9));
    assert!(close(m.skew, 0.0, 1e-12));
    assert!(close(m.kurtosis, 1.64, 1e-12));
    assert_eq!(m.n, 4);
}

/// Без дисперсии Sharpe нет, а не ноль: константа и одиночка отказываются.
#[test]
fn sharpe_is_none_without_variance() {
    assert_eq!(sharpe_ratio(&[]), None);
    assert_eq!(sharpe_ratio(&[1.0]), None);
    assert_eq!(sharpe_ratio(&[2.0, 2.0, 2.0, 2.0]), None);
    assert_eq!(sharpe_ratio(&[1.0, f64::NAN, 3.0]), None);
}

/// PBO = 1.0, когда IS-победитель заучил IS-блоки и валится на OOS: при
/// S = 4 три сплита, и во всех трёх OOS-ранг ниже медианы.
#[test]
fn pbo_is_one_when_is_winner_fails_out_of_sample() {
    let memorized = vec![4.0, 4.0, 1.0, 1.0, -3.0, -3.0, -3.0, -3.0];
    // Приманки — плоские нули: их Sharpe 0.0 ниже любого IS-значения
    // заучившего (все три сплита) и выше любого его OOS-значения.
    // (Отрицательная константа не годится: её −inf делит худший OOS-ранг
    // с заучившим и размывает счёт ровно до медианы.)
    let flat_a = vec![0.0; 8];
    let flat_b = vec![0.0; 8];
    let got = pbo(&[memorized, flat_a, flat_b], 4).unwrap();
    assert!(
        close(got, 1.0, 1e-12),
        "заученный IS-победитель обязан дать PBO = 1.0, получили {got}"
    );
}

/// Бесструктурный шум не обязан пугать: пять детерминированных сидов, 8
/// рядов, S = 6 — средний PBO остаётся в середине, вдали и от нуля, и от
/// единицы. Среднее по сидам, а не один сид: один розыгрыш при десяти
/// коррелированных сплитах шумит сам по себе, свойство — у среднего.
#[test]
fn pbo_on_unstructured_noise_stays_near_one_half() {
    let mut mean = 0.0;
    for seed in 1..=5u64 {
        let mut state = 0x1234_5678_9ABC_DEF1u64.wrapping_add(seed.wrapping_mul(97));
        let mut next = move || {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            (state >> 33) as f64 / (1u64 << 31) as f64 * 2.0 - 1.0
        };
        let trials: Vec<Vec<f64>> = (0..8).map(|_| (0..48).map(|_| next()).collect()).collect();
        let got = pbo(&trials, 6).unwrap();
        println!("pbo on deterministic noise (seed {seed}) = {got}");
        mean += got;
    }
    mean /= 5.0;
    println!("pbo on deterministic noise: mean over 5 seeds = {mean}");
    assert!(
        (0.3..=0.7).contains(&mean),
        "шум обязан дать средний PBO около половины, получили {mean}"
    );
    // Детерминизм: тот же вход — тот же PBO.
    let mut state = 0x1234_5678_9ABC_DEF1u64.wrapping_add(1u64.wrapping_mul(97));
    let mut next = move || {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        (state >> 33) as f64 / (1u64 << 31) as f64 * 2.0 - 1.0
    };
    let trials: Vec<Vec<f64>> = (0..8).map(|_| (0..48).map(|_| next()).collect()).collect();
    assert_eq!(pbo(&trials, 6).unwrap(), pbo(&trials, 6).unwrap());
}

/// Вырожденный вход PBO — отказ: мало испытаний, рваные ряды, нечётные
/// блоки, блоков больше периодов, перебор за границей.
#[test]
fn pbo_refuses_degenerate_input() {
    assert_eq!(pbo(&[vec![1.0, -1.0, 1.0, -1.0]], 2), None);
    assert_eq!(
        pbo(&[vec![1.0, 2.0], vec![1.0]], 2),
        None,
        "рваные ряды — отказ"
    );
    assert_eq!(
        pbo(&[vec![1.0, -1.0], vec![-1.0, 1.0]], 3),
        None,
        "нечётные блоки — отказ"
    );
    assert_eq!(pbo(&[vec![1.0], vec![2.0]], 4), None);
    assert_eq!(
        pbo(&[vec![1.0, -1.0], vec![-1.0, 1.0]], 18),
        None,
        "перебор за границей — отказ"
    );
}

/// CPCV проверяет **процедуру отбора**, а не профиль (задача §6.4 п. 4,
/// `SETTLED.md` В-19): правило отбора — лучший по среднему на
/// IS-периодах, то же, которым выбирает шорт-лист. Оба ответа выведены
/// руками. Матрица, где одно испытание стабильно лучшее и стабильно
/// прибыльное, обязана дать **положительный** средний OOS Sharpe:
/// правило берёт его в обеих складках, а его OOS-сутки все
/// положительны. Матрица-качели, где каждое испытание выигрывает IS
/// ровно той половиной, на которой оно прибыльно, обязана дать
/// **отрицательный**: выбранное на IS проваливает OOS — ровно то, ради
/// чего CPCV и считают, и по одному профилю это не видно вовсе.
#[test]
fn cpcv_selection_separates_a_steady_winner_from_a_seesaw() {
    let best_mean = |trials: &[Vec<f64>], is: &[usize]| -> Option<usize> {
        let mut best: Option<(usize, f64)> = None;
        for (i, row) in trials.iter().enumerate() {
            let vals: Vec<f64> = is.iter().filter_map(|&j| row.get(j).copied()).collect();
            let Some(m) = moments(&vals) else { continue };
            if best.is_none_or(|(_, b)| m.mean > b) {
                best = Some((i, m.mean));
            }
        }
        best.map(|(i, _)| i)
    };

    let steady = vec![
        vec![2.0, 2.1, 1.9, 2.05, 2.0, 1.95, 2.1, 2.0],
        vec![-1.0, -1.2, -0.8, -1.1, -0.9, -1.3, -1.0, -1.1],
        vec![0.1, -0.2, 0.3, -0.4, 0.2, -0.1, 0.4, -0.3],
    ];
    let v = cpcv_selection_oos_sharpe(&steady, REPORT_CPCV_PARAMS, best_mean)
        .expect("восемь суток, две складки — оценка обязана состояться");
    assert!(v > 0.0, "стабильный победитель обязан пережить OOS: {v}");

    let seesaw = vec![
        vec![-3.0, -2.0, -3.5, -2.5, 2.0, 3.0, 2.5, 3.5],
        vec![2.0, 3.0, 2.5, 3.5, -3.0, -2.0, -3.5, -2.5],
    ];
    let v = cpcv_selection_oos_sharpe(&seesaw, REPORT_CPCV_PARAMS, best_mean)
        .expect("та же форма входа — оценка обязана состояться");
    assert!(
        v < 0.0,
        "победитель IS обязан провалить OOS на качелях: {v}"
    );
}

/// Нормальные числа без зависимостей: xorshift64* + Бокс — Мюллер.
struct Gauss(u64);

impl Gauss {
    fn uniform(&mut self) -> f64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        let x = self.0.wrapping_mul(0x2545_F491_4F6C_DD1D) >> 11;
        (x as f64 + 0.5) / (1u64 << 53) as f64
    }

    fn normal(&mut self) -> f64 {
        let (u1, u2) = (self.uniform(), self.uniform());
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }
}

/// DSR суточного ряда лучшей по Шарпу формы сетки, `V = max(V_изм, 1/T)` —
/// ровно так, как `lob bounce-verdict` (T-26).
fn grid_dsr(g: &mut Gauss, forms: usize, days: usize, edge: f64) -> f64 {
    let series: Vec<Vec<f64>> = (0..forms)
        .map(|_| (0..days).map(|_| edge + g.normal()).collect())
        .collect();
    let sharpes: Vec<f64> = series
        .iter()
        .map(|s| sharpe_ratio(s).unwrap_or(0.0))
        .collect();
    let v = trial_sharpe_variance(&sharpes)
        .map_or(1.0 / count_f64(days), |v| v.max(1.0 / count_f64(days)));
    let best = (0..forms)
        .max_by(|a, b| sharpes[*a].total_cmp(&sharpes[*b]))
        .unwrap();
    let m = moments(&series[best]).unwrap();
    dsr_with_variance(m.mean / m.std, m.n, m.skew, m.kurtosis, forms, v).unwrap()
}

/// Калибровка T-26 (условие 2 Судьи `11a3d1e`): сетка из 20 форм без края на
/// T = 31 сутках — DSR ≥ 0,95 не чаще 5 % повторов; форма с истинным дневным
/// Шарпом 1,0 при N = 1 проходит в большинстве повторов.
#[test]
fn daily_dsr_is_calibrated_on_synthetic_grids() {
    const REPEATS: usize = 400;
    let mut g = Gauss(0x9E37_79B9_7F4A_7C15);
    let false_pass = (0..REPEATS)
        .filter(|_| grid_dsr(&mut g, 20, 31, 0.0) >= DSR_TARGET)
        .count();
    assert!(
        false_pass * 20 <= REPEATS,
        "шум прошёл {false_pass} из {REPEATS}"
    );
    let true_pass = (0..REPEATS)
        .filter(|_| grid_dsr(&mut g, 1, 31, 1.0) >= DSR_TARGET)
        .count();
    assert!(
        true_pass * 2 > REPEATS,
        "край прошёл лишь {true_pass} из {REPEATS}"
    );
}
