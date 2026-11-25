use super::*;

/// Возраст: границы В-44 — `[0, 10 мин)`, `[10 мин, 1 ч)`, `[1 ч, ∞)`,
/// нижняя включительно, верхняя исключительно.
#[test]
fn age_buckets_follow_the_ten_minute_and_one_hour_bounds() {
    assert_eq!(age_bucket(0), Some("[0,10m)"));
    assert_eq!(age_bucket(599_999), Some("[0,10m)"));
    assert_eq!(
        age_bucket(600_000),
        Some("[10m,1h)"),
        "10 мин — включительно"
    );
    assert_eq!(age_bucket(3_599_999), Some("[10m,1h)"));
    assert_eq!(
        age_bucket(3_600_000),
        Some("[1h,inf)"),
        "1 ч — включительно"
    );
    assert_eq!(age_bucket(i64::MAX - 1), Some("[1h,inf)"));
    assert_eq!(age_bucket(-1), None, "касание раньше рождения — никуда");
    assert_eq!(AGE_LABELS.len(), AGE_BOUNDS_MS.len());
}

/// Фронтран: ровно ноль — своя корзина; половина размера — граница
/// включительно вверх (D 03:57: лям и 500 тысяч фронтрана).
#[test]
fn frontrun_buckets_split_zero_and_half_of_the_size() {
    assert_eq!(frontrun_share(0, 10), Some(0.0));
    assert_eq!(frontrun_share(5, 10), Some(0.5));
    assert_eq!(frontrun_share(3, 0), None, "размера нет — доли нет");
    assert_eq!(frontrun_share(0, 0), None);

    assert_eq!(frontrun_bucket(0.0), Some("0"));
    assert_eq!(frontrun_bucket(f64::EPSILON), Some("(0,0.5)"));
    assert_eq!(frontrun_bucket(0.499_999), Some("(0,0.5)"));
    assert_eq!(
        frontrun_bucket(0.5),
        Some("[0.5,inf)"),
        "половина — включительно"
    );
    assert_eq!(frontrun_bucket(7.0), Some("[0.5,inf)"));
    assert_eq!(frontrun_bucket(-0.1), None);
    assert_eq!(frontrun_bucket(f64::NAN), None);
    assert_eq!(
        frontrun_bucket(frontrun_share(4, 10).unwrap()),
        Some("(0,0.5)")
    );
    assert_eq!(
        frontrun_bucket(frontrun_share(10, 10).unwrap()),
        Some("[0.5,inf)")
    );
}

/// Круглость: 0 / 1 / 2+ — тотально, включая потолок `ROUND_ZEROS_CAP`.
#[test]
fn round_buckets_are_zero_one_and_two_or_more() {
    assert_eq!(round_bucket(0), "0");
    assert_eq!(round_bucket(1), "1");
    assert_eq!(round_bucket(2), ">=2");
    assert_eq!(round_bucket(crate::lob::levels::ROUND_ZEROS_CAP), ">=2");
    assert_eq!(round_bucket(u8::MAX), ">=2");
}

/// Номер касания: `touch_index` 0 → «1-е», 1–2 → «2–3-е», ≥ 3 → «4-е+»
/// (S 12:50: два-три раза максимум, четвёртый — риск).
#[test]
fn touch_index_buckets_are_first_second_to_third_and_fourth_plus() {
    assert_eq!(touch_index_bucket(0), "1");
    assert_eq!(touch_index_bucket(1), "2-3");
    assert_eq!(touch_index_bucket(2), "2-3");
    assert_eq!(touch_index_bucket(3), ">=4", "четвёртое касание — индекс 3");
    assert_eq!(touch_index_bucket(u32::MAX), ">=4");
}
