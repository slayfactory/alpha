//! Тесты TK-706: память исходов хода (`memo.rs`) — равный ключ берёт готовый исход, результат прогона не меняется, сверка ловит порчу, предел записей.

use super::*;
use crate::breakout::memo::{MoveKey, MoveMemo};
use crate::breakout::tick::{
    level_ctx, sim_fill_lc_s, sim_fill_mem, Cursor as TickCursor, LevelCtx, TickResult,
};

struct MemoSrc<'a> {
    inner: &'a MemSrc,
    memo: MoveMemo,
}

impl TradeSrc for MemoSrc<'_> {
    fn day(&self, sym: &str, day: usize) -> Option<Arc<Vec<Tick>>> {
        self.inner.day(sym, day)
    }

    fn move_memo(&self) -> Option<&MoveMemo> {
        Some(&self.memo)
    }
}

/// Мир одного сигнала (как в `end_to_end_trade_entered_before_the_break…`): вход после сигнала, выход по стопу в следующей минутке. Вход по ленте
/// (`ew > 0`, как у всех боевых вариантов): только он идёт через `sim_fill_mem`; при `ew = 0` вход «следующая сделка» (`simulate_tick`) память не трогает.
fn memo_world() -> (Vec<Sym>, Vec<TSig>, MemSrc, Params) {
    let mut p = lv_params();
    p.exit.ent_w_ms = 1000;
    p.exit.ent_x_bp = 100;
    let t1 = BORN + 100;
    let mut c = level_world();
    for m in t1 - 5..t1 {
        set(&mut c, m, 99.5, 99.6, 99.4, 99.5);
    }
    set(&mut c, t1, 99.0, 100.10, 98.9, 100.00);
    let s = Sym::from_cols("TEST", c, Vec::new());
    let ticks = vec![
        tk(t1, 0, 99.0, false),
        tk(t1, 10_000, 99.5, false),
        tk(t1, 20_000, 99.8, false),
        tk(t1, 30_000, 100.10, false),
        tk(t1, 40_000, 99.9, true),
        tk(t1 + 1, 0, 90.0, true),
    ];
    let mut m = HashMap::new();
    m.insert(("TEST".to_string(), t1 / 1440), Arc::new(ticks));
    let src = MemSrc(m);
    let jobs = vec![(p.sig.clone(), Side::Long)];
    let mut outs = vec![Vec::new()];
    let mut ctrs = vec![TickCtr::default()];
    signals_for_coin(&s, 0, &jobs, &src, &mut outs, &mut ctrs);
    assert_eq!(outs[0].len(), 1);
    (vec![s], outs.remove(0), src, p)
}

fn trades(r: &TickResult) -> String {
    format!("{:?} {:?}", r.trades, r.daily.iter().sum::<f64>())
}

#[test]
fn equal_exit_cfgs_take_the_outcome_from_memory_and_results_equal_the_plain_run() {
    let (syms, sigs, src, p) = memo_world();
    let mut other = p.exit.clone();
    other.slip_bps += 5;
    let cfgs = [p.exit.clone(), p.exit.clone(), other];
    let plain: Vec<String> = cfgs
        .iter()
        .map(|e| trades(&run_tick(&syms, &sigs, Side::Long, e, &p.port, &src)))
        .collect();
    assert_ne!(plain[0], plain[2], "другой ExitCfg — другой исход");
    let ms = MemoSrc {
        inner: &src,
        memo: MoveMemo::new(1, 1000),
    };
    let with: Vec<String> = cfgs
        .iter()
        .map(|e| trades(&run_tick(&syms, &sigs, Side::Long, e, &p.port, &ms)))
        .collect();
    assert_eq!(plain, with);
    // ходов 3, из памяти 1 (второй равный вариант), различных 2, сверено 1 (шаг 1: каждое попадание)
    assert_eq!(ms.memo.counts(), (3, 1, 2, 1));
    assert!(
        ms.memo.summary().contains("G по ходам 1.500"),
        "{}",
        ms.memo.summary()
    );
}

#[test]
#[should_panic(expected = "память исходов: расхождение при сверке")]
fn check_catches_a_corrupted_cached_outcome() {
    let (syms, sigs, src, p) = memo_world();
    let ms = MemoSrc {
        inner: &src,
        memo: MoveMemo::new(1, 1000),
    };
    run_tick(&syms, &sigs, Side::Long, &p.exit, &p.port, &ms);
    ms.memo.corrupt_all();
    run_tick(&syms, &sigs, Side::Long, &p.exit, &p.port, &ms);
}

#[test]
fn without_check_a_hit_is_not_recomputed_and_the_cap_stops_recording() {
    let (syms, sigs, src, p) = memo_world();
    let ms = MemoSrc {
        inner: &src,
        memo: MoveMemo::new(0, 1),
    };
    let mut other = p.exit.clone();
    other.slip_bps += 5;
    run_tick(&syms, &sigs, Side::Long, &p.exit, &p.port, &ms);
    run_tick(&syms, &sigs, Side::Long, &other, &p.port, &ms); // предел 1 запись: второй исход считается, но не записывается
    run_tick(&syms, &sigs, Side::Long, &p.exit, &p.port, &ms); // первый — из памяти, без сверки
    run_tick(&syms, &sigs, Side::Long, &other, &p.port, &ms); // второго в памяти нет — снова считается
    assert_eq!(ms.memo.counts(), (4, 1, 1, 0));
    assert!(
        ms.memo
            .summary()
            .contains("предел записей 1 достигнут, не записано 2"),
        "{}",
        ms.memo.summary()
    );
}

#[test]
fn get_or_compute_computes_each_key_once_and_the_key_includes_every_argument() {
    let p = lv_params();
    let out = TickOut {
        entry_ms: 1,
        entry_px: px(100.0),
        legs: Vec::new(),
        exit_ms: 5,
        kind: ExitKind::HardStop,
        takes: 0,
        marks: Vec::new(),
        tl: None,
        floor: [false, false],
        ord: Vec::new(),
    };
    let key = |entry_ms: i64, stop_px: Option<i64>| MoveKey {
        coin: "TEST".into(),
        entry_ms,
        p0: px(100.0),
        side: Side::Long,
        natr_e8: 1_000_000,
        ex: p.exit.clone(),
        lc: None,
        notional: NOTIONAL_U,
        entry_slip_bps: 0,
        stop_px,
    };
    let memo = MoveMemo::new(0, 100);
    let mut calls = 0;
    let mut compute = || {
        calls += 1;
        out.clone()
    };
    for k in [
        key(10, None),
        key(10, None),
        key(11, None),
        key(10, Some(5)),
        key(10, None),
    ] {
        memo.get_or_compute(k, &mut compute);
    }
    // различных ключей 3: `entry_ms` и `stop_px` входят в ключ
    assert_eq!(calls, 3);
    assert_eq!(memo.counts(), (5, 2, 3, 0));
}

/// Ход по ленте от места `(day, idx)` — как до TK-706 (внешний старт читал сделки с индекса сигнала + 1).
fn reference(
    src: &MemSrc,
    day: usize,
    idx: usize,
    entry_ms: i64,
    p0: i64,
    p: &Params,
    lc: LevelCtx,
) -> TickOut {
    let mut cur = TickCursor::new(src, "TEST", day, idx);
    sim_fill_lc_s(
        &mut || cur.next_tick(),
        entry_ms,
        p0,
        Side::Long,
        1_000_000,
        &p.exit,
        Some(lc),
        NOTIONAL_U,
        0,
        None,
    )
}

#[test]
fn derived_stream_start_equals_every_valid_cursor_start() {
    // C1: место старта чтения выводится из `entry_ms`; результат равен счёту от любого корректного места (до первой сделки со временем > `entry_ms`)
    let (syms, sigs, src, p) = memo_world();
    let lc = level_ctx(&syms[0], Side::Long, &sigs[0], &p.exit);
    let t1 = BORN + 100;
    let day = t1 / 1440;
    let entry_ms = day as i64 * DAY_MS + (t1 % 1440) as i64 * MIN_MS + 20_000; // время третьей сделки (индекс 2)
    let p0 = px(99.8);
    let derived = sim_fill_mem(
        &src,
        "TEST",
        entry_ms,
        p0,
        Side::Long,
        1_000_000,
        &p.exit,
        lc,
        NOTIONAL_U,
        0,
        None,
    );
    assert!(derived.exit_ms > entry_ms);
    for idx in [0, 1, 2, 3] {
        assert_eq!(
            derived,
            reference(&src, day, idx, entry_ms, p0, &p, lc),
            "старт с индекса {idx}"
        );
    }
}

#[test]
fn derived_stream_start_crosses_midnight_and_skips_a_day_without_file() {
    let (syms, sigs, _, p) = memo_world();
    let lc = level_ctx(&syms[0], Side::Long, &sigs[0], &p.exit);
    let day = (BORN + 100) / 1440;
    let last = DAY_MS as u32 - 1000;
    let mut m = HashMap::new();
    m.insert(
        ("TEST".to_string(), day),
        Arc::new(vec![
            Tick::new(px(99.0), 1000, false),
            Tick::new(px(99.5), last, false),
        ]),
    );
    // следующие сутки без файла, через одни — сделка ниже стопа
    m.insert(
        ("TEST".to_string(), day + 2),
        Arc::new(vec![Tick::new(px(80.0), 1000, true)]),
    );
    let src = MemSrc(m);
    let entry_ms = day as i64 * DAY_MS + i64::from(last);
    let p0 = px(99.5);
    let derived = sim_fill_mem(
        &src,
        "TEST",
        entry_ms,
        p0,
        Side::Long,
        1_000_000,
        &p.exit,
        lc,
        NOTIONAL_U,
        0,
        None,
    );
    assert_eq!(derived.exit_ms, (day as i64 + 2) * DAY_MS + 1000);
    assert_eq!(derived, reference(&src, day, 0, entry_ms, p0, &p, lc));
    // вход раньше всех сделок суток и вход в сутки без файла
    let early = sim_fill_mem(
        &src,
        "TEST",
        day as i64 * DAY_MS + 500,
        p0,
        Side::Long,
        1_000_000,
        &p.exit,
        lc,
        NOTIONAL_U,
        0,
        None,
    );
    assert_eq!(
        early,
        reference(&src, day, 0, day as i64 * DAY_MS + 500, p0, &p, lc)
    );
    let no_file = sim_fill_mem(
        &src,
        "TEST",
        (day as i64 + 1) * DAY_MS + 5,
        p0,
        Side::Long,
        1_000_000,
        &p.exit,
        lc,
        NOTIONAL_U,
        0,
        None,
    );
    assert_eq!(
        no_file,
        reference(&src, day + 1, 0, (day as i64 + 1) * DAY_MS + 5, p0, &p, lc)
    );
}
