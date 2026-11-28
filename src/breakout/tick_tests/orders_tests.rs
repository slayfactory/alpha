//! Тесты TK-743: журнал заявок сделки (`ExitCfg::keep_orders`, ключ `--keep-orders`). Один ход с лестницей тейков, стопом и вторым входом `e2` даёт ожидаемую последовательность
//! заявок; ключ не меняет ни одной цифры сделки.

use super::*;
use crate::breakout::orders::{
    OrdEv, EV_CANCEL, EV_FILL, EV_PLACE, EV_REPRICE, OID_E1, OID_E2, OID_STOP,
};

/// Ход мира `e2_world`: сигнал на 10 000 мс, вход лота 1 на 10 004 мс по 99,7, второй вход на 30 004 по 99,95, обвал на 40 000 — оба лота выходят стопом.
const E2_TICKS: [(i64, f64, bool); 5] = [
    (0, 99.6, false),
    (10_000, 99.7, false),
    (20_000, 99.85, false),
    (30_000, 99.95, false),
    (40_000, 85.0, true),
];

fn ord_of(r: &super::super::tick::TickResult) -> &Vec<OrdEv> {
    &r.trades[0].ord
}

/// (лот, номер заявки, событие, мс) — для проверки порядка.
fn seq(v: &[OrdEv], w: &E2World) -> Vec<(u8, u8, u8, i64)> {
    let mut o: Vec<OrdEv> = v.to_vec();
    o.sort_by_key(|e| e.ms);
    o.iter()
        .map(|e| (e.lot, e.oid, e.ev, e.ms - w.abs(0)))
        .collect()
}

#[test]
fn order_log_is_empty_and_the_trade_is_identical_with_the_flag_off() {
    let mut w = e2_world();
    let off = w.run(&E2_TICKS);
    w.ex.keep_orders = true;
    let on = w.run(&E2_TICKS);
    assert!(
        off.trades[0].ord.is_empty()
            && off.trades[0]
                .lot2
                .as_ref()
                .is_some_and(|l| l.ord.is_empty())
    );
    assert!(!ord_of(&on).is_empty());
    let (a, b) = (&off.trades[0], &on.trades[0]);
    assert_eq!(
        (a.entry_ms, a.exit_ms, a.entry_px, a.kind, a.takes, &a.legs),
        (b.entry_ms, b.exit_ms, b.entry_px, b.kind, b.takes, &b.legs)
    );
    assert_eq!(
        (
            a.gross.to_bits(),
            a.fees.to_bits(),
            a.funding.to_bits(),
            a.net.to_bits()
        ),
        (
            b.gross.to_bits(),
            b.fees.to_bits(),
            b.funding.to_bits(),
            b.net.to_bits()
        )
    );
    assert_eq!(off.daily.map(f64::to_bits), on.daily.map(f64::to_bits));
    assert_eq!(
        a.lot2.as_ref().map(|l| (l.entry_ms, l.exit_ms, &l.legs)),
        b.lot2.as_ref().map(|l| (l.entry_ms, l.exit_ms, &l.legs))
    );
}

#[test]
fn one_trade_with_ladder_stop_and_e2_shows_the_expected_order_sequence() {
    let mut w = e2_world();
    w.ex.keep_orders = true;
    let r = w.run(&E2_TICKS);
    let t = &r.trades[0];
    let v = ord_of(&r);
    let s = seq(v, &w);
    // вход лота 1: заявка в момент сигнала, исполнение в момент входа
    assert_eq!(s[0], (0, OID_E1, EV_PLACE, 10_000));
    let fill1 = v
        .iter()
        .find(|e| e.oid == OID_E1 && e.ev == EV_FILL)
        .expect("исполнение входа");
    assert_eq!(
        (fill1.ms, fill1.px, fill1.u),
        (t.entry_ms, t.entry_px, NOTIONAL_U / 2)
    );
    // лестница 2 тейков и стоп лота 1 — на входе; тейки лимитные, стоп — стоп-заявка
    for oid in [0u8, 1] {
        let p = v
            .iter()
            .find(|e| e.lot == 0 && e.oid == oid && e.ev == EV_PLACE)
            .expect("тейк");
        assert_eq!(
            (p.ms, p.px > t.entry_px),
            (t.entry_ms, true),
            "тейк лонга выше входа"
        );
    }
    let stop_place = v
        .iter()
        .find(|e| e.lot == 0 && e.oid == OID_STOP && e.ev == EV_PLACE)
        .expect("стоп");
    assert_eq!(
        (stop_place.ms, stop_place.px < t.entry_px),
        (t.entry_ms, true)
    );
    // второй вход: условная заявка в момент входа лота 1, исполнена позже (30 004), цена и время — как у лота 2
    let l2 = t.lot2.as_ref().expect("второй лот");
    let e2p = v
        .iter()
        .find(|e| e.oid == OID_E2 && e.ev == EV_PLACE)
        .expect("e2");
    assert_eq!(
        (e2p.lot, e2p.ms, e2p.px),
        (1, t.entry_ms, px(100.0) - px(100.0) / 1000)
    );
    let e2f = v
        .iter()
        .find(|e| e.oid == OID_E2 && e.ev == EV_FILL)
        .expect("e2 исполнен");
    assert_eq!(
        (e2f.ms, e2f.px, e2f.aux),
        (l2.entry_ms, l2.entry_px, w.abs(30_000))
    );
    // стоп обоих лотов исполнен на обвале; непройденные тейки сняты в тот же момент
    for lot in [0u8, 1] {
        let f = v
            .iter()
            .find(|e| e.lot == lot && e.oid == OID_STOP && e.ev == EV_FILL)
            .expect("стоп исполнен");
        assert_eq!(f.ms, w.abs(40_000));
        let nc = v
            .iter()
            .filter(|e| e.lot == lot && e.ev == EV_CANCEL && e.oid < 2 && e.ms == w.abs(40_000))
            .count();
        assert_eq!(nc, 2, "оба тейка лота {lot} сняты");
        // событий заявки после её исполнения / снятия нет
        for oid in [0u8, 1, OID_STOP] {
            let mut last = None;
            for e in v.iter().filter(|e| e.lot == lot && e.oid == oid) {
                assert!(
                    last.is_none_or(|l| l == EV_PLACE || l == EV_REPRICE),
                    "лот {lot} заявка {oid}: событие после конца"
                );
                last = Some(e.ev);
            }
        }
    }
}

#[test]
fn a_filled_take_is_logged_with_its_price_and_moves_the_stop_to_break_even() {
    let mut w = e2_world();
    w.ex.keep_orders = true;
    w.ex.e2_pm = 0; // один лот
    let probe = w.run(&[
        (0, 99.6, false),
        (10_000, 99.7, false),
        (20_000, 99.8, false),
    ]);
    let take1 = probe.trades[0]
        .ord
        .iter()
        .find(|e| e.oid == 0 && e.ev == EV_PLACE)
        .expect("тейк 1")
        .px;
    let above = take1 as f64 / 1e8 + 0.01;
    let r = w.run(&[
        (0, 99.6, false),
        (10_000, 99.7, false),
        (20_000, above, false), // сквозь первый тейк
        (30_000, 85.0, true),   // стоп в безубытке
    ]);
    let t = &r.trades[0];
    let v = ord_of(&r);
    let f = v
        .iter()
        .find(|e| e.oid == 0 && e.ev == EV_FILL)
        .expect("тейк исполнен");
    assert_eq!((f.ms, f.px, f.u), (w.abs(20_000), take1, t.legs[0].u));
    assert_eq!(f.px, t.legs[0].px, "цена заявки = цена ноги движка");
    // стоп перенесён в цену входа и сжат до остатка; исполнен как безубыток
    let mv = v
        .iter()
        .find(|e| e.oid == OID_STOP && e.ev == EV_REPRICE)
        .expect("сдвиг стопа");
    assert_eq!((mv.ms, mv.px, mv.note), (w.abs(20_000), t.entry_px, "be"));
    assert_eq!(mv.u, NOTIONAL_U - f.u);
    let sf = v
        .iter()
        .find(|e| e.oid == OID_STOP && e.ev == EV_FILL)
        .expect("стоп");
    assert_eq!(
        (sf.ms, sf.note, sf.u),
        (w.abs(30_000), "be", NOTIONAL_U - f.u)
    );
}
