//! Признаки подхода P08, стена на ходу, вторая стена, близнецы.

use super::*;

/// TK-012 (Г-36, Г-55): признаки на кадре взвода — исполнено с рождения (без
/// RPI), максимум, монотонность по правилу `size_monotonic` (уменьшение до
/// максимума — `false`), съеденное и максимум за 60 с (после пропуска времени).
#[test]
fn approach_p08_features_at_arm() {
    let mut tr = LevelTracker::new(cfg_approach(20, 500));
    let (mut out, mut touches, mut ap) = (Vec::new(), Vec::new(), Vec::new());
    let bid = |size: i64| [ob(10020, 3), ob(10000, size)];
    let hit = |lots: i64, exch_ms: i64, rpi: bool| TradeHit {
        tick: 10000,
        lots,
        aggressor_is_buy: false,
        block: false,
        rpi,
        exch_ms,
    };
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &bid(10),
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Bid,
        &bid(8),
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &bid(20),
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        5000,
        Side::Bid,
        &bid(15),
        &mut out,
        &mut touches,
        &mut ap,
    );
    tr.observe_trade(hit(4, 1500, false));
    tr.observe_trade(hit(2, 10_000, false)); // секунда 10 = S − 60 — вне окна
    tr.observe_trade(hit(3, 11_000, false));
    tr.observe_trade(hit(100, 20_000, true)); // RPI — мимо
    frame(
        &mut tr,
        69_000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        70_000,
        Side::Bid,
        &bid(15),
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        71_000,
        Side::Bid,
        &[ob(10000, 15)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1);
    assert_eq!(ap[0].arm_ms, 70_000);
    assert_eq!(
        ap[0].p08,
        Some(ArmP08 {
            traded_lots: 9,
            size_max: 20,
            size_monotonic: false,
            eat_60s_lots: 3,
            size_max_60s_lots: 15,
            depth_behind50_lots: 0,
        })
    );
}

/// TK-012 (Г-07): `depth_behind50_lots` суммирует только первые 50 наблюдений
/// стороны; `depth_behind_lots` — весь кадр, как раньше.
#[test]
fn approach_depth_behind50_cuts_at_fifty_levels() {
    let mut tr = LevelTracker::new(cfg_approach(20, 500));
    let (mut out, mut touches, mut ap) = (Vec::new(), Vec::new(), Vec::new());
    // Лучший бид 10 020, стена 10 000 (индекс 1), позади — 58 уровней по 1 лоту
    // (индексы 2..59): в первых 50 из них 48.
    let mut book = vec![ob(10020, 3), ob(10000, 10)];
    for k in 0..58 {
        book.push(ob(9990 - k, 1));
    }
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &book,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &book,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        4000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1);
    assert_eq!(ap[0].depth_behind_lots, 58);
    assert_eq!(ap[0].p08.map(|p| p.depth_behind50_lots), Some(48));
}

/// TK-115 (журнал стен e65): стена попадает в журнал, когда возраст дошёл до пола и порог держится, и
/// выходит из него, когда размер съеден; без `enable_wall_log` журнал пуст.
#[test]
fn wall_log_records_age_gate_and_loss() {
    let run = |log: bool| {
        let mut tr = LevelTracker::new(cfg_approach(20, 1500));
        if log {
            tr.enable_wall_log();
        }
        let (mut out, mut touches, mut ap) = (Vec::new(), Vec::new(), Vec::new());
        for (ts, lots) in [(1000, 10), (2000, 10), (3000, 10), (4000, 1)] {
            let lv = [ob(10020, 3), ob(10000, lots)];
            frame(&mut tr, ts, Side::Bid, &lv, &mut out, &mut touches, &mut ap);
        }
        let mut ev = Vec::new();
        tr.take_wall_events(&mut ev);
        ev
    };
    assert!(run(false).is_empty());
    let ev = run(true);
    let wall = |ts_ms, wall| WallEvent {
        ts_ms,
        side: Side::Bid,
        price_tick: 10000,
        wall,
    };
    // Рождение в 1000: возраст 2000 ≥ 1500 только к кадру 3000 (на 2000 — 1000 < 1500); в 4000 размер съеден.
    assert_eq!(ev, vec![wall(3000, true), wall(4000, false)]);
}

/// TK-115 (e65): вторая живая стена за входом на момент исполнения — по журналу, а не по кадру взвода.
#[test]
fn second_wall_is_taken_from_walls_alive_at_fill() {
    let w = |ts_ms, side, price_tick, wall| WallEvent {
        ts_ms,
        side,
        price_tick,
        wall,
    };
    let ev = [
        w(100, Side::Ask, 10100, true),
        w(200, Side::Ask, 10200, true),
        w(300, Side::Ask, 10300, true),
        w(400, Side::Ask, 10200, false),
        w(500, Side::Bid, 9900, true),
        w(600, Side::Bid, 9800, true),
    ];
    let mut live = Vec::new();
    // Лонг, вход 10000: на 350 живы 10100/10200/10300 — вторая 10200; на 450 вторая 10300 (10200 снята).
    assert_eq!(
        second_wall_tick(&ev, Side::Ask, 350, 10000, true, &mut live),
        Some(10200)
    );
    assert_eq!(
        second_wall_tick(&ev, Side::Ask, 450, 10000, true, &mut live),
        Some(10300)
    );
    // Стена ниже входа не считается; до появления второй — None.
    assert_eq!(
        second_wall_tick(&ev, Side::Ask, 450, 10150, true, &mut live),
        None
    );
    assert_eq!(
        second_wall_tick(&ev, Side::Ask, 150, 10000, true, &mut live),
        None
    );
    // Шорт, вход 10000: бид-стены ниже входа, вторая от входа — 9800.
    assert_eq!(
        second_wall_tick(&ev, Side::Bid, 650, 10000, false, &mut live),
        Some(9800)
    );
}

/// Г-03 (TK-274): сценарий подхода на цене ≈ 100 000 тиков (1 bps ≈ 10 тиков): стена 100 000 (10 лотов), лучший бид
/// 100 040 (3 лота — не плотность), аск 100 300 → 100 050 (разрыв 50 тиков = 5 bps), взвод на 3000, касание на 4000.
pub(super) fn twin_scenario(twins: Option<&[u64]>) -> (Vec<ApproachRecord>, Vec<TwinRec>) {
    let mut tr = LevelTracker::new(cfg_approach(20, 500));
    if let Some(seeds) = twins {
        tr.enable_twins("TST", seeds);
    }
    let (mut out, mut touches, mut ap) = (Vec::new(), Vec::new(), Vec::new());
    let wall = [ob(100_040, 3), ob(100_000, 10)];
    for (ts, side, lv) in [
        (1000, Side::Bid, &wall[..]),
        (1000, Side::Ask, &[ob(100_300, 4)][..]),
        (2000, Side::Ask, &[ob(100_050, 4)][..]),
        (3000, Side::Bid, &wall[..]),
        (4000, Side::Bid, &[ob(100_000, 10)][..]),
    ] {
        frame(&mut tr, ts, side, lv, &mut out, &mut touches, &mut ap);
    }
    let mut tw = Vec::new();
    tr.take_twins(&mut tw);
    (ap, tw)
}

/// Двойник строится на кадре взвода для каждого seed из корзины того же целого bps (5 bps: тики 99 991…99 999),
/// без стены; выключенный режим — записи подходов те же, двойников нет.
#[test]
fn twins_are_built_at_arm_and_off_changes_nothing() {
    let (ap_off, tw_off) = twin_scenario(None);
    assert!(tw_off.is_empty());
    let (ap_on, tw) = twin_scenario(Some(&[1, 2]));
    assert_eq!(ap_on, ap_off, "режим двойника не меняет записи подходов");
    assert_eq!(ap_on.len(), 1);
    assert_eq!(tw.len(), 2);
    for (r, seed) in tw.iter().zip([1u64, 2]) {
        assert_eq!(
            (r.side, r.price_tick, r.arm_ms, r.seed),
            (Side::Bid, 100_000, 3000, seed)
        );
        let t = r.twin.expect("корзина не пуста");
        assert_eq!(t.candidates, 9);
        assert!((99_991..=99_999).contains(&t.tick), "{}", t.tick);
    }
}
