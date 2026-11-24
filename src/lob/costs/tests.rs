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

fn fobs(day: i64, net: f64, filled: bool) -> FillObservation {
    FillObservation {
        day_cluster: day,
        net_bps: net,
        filled,
    }
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
