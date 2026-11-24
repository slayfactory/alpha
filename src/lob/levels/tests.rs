use super::*;

mod touches;
use touches::*;
mod approach;
mod thresholds;
use approach::*;
mod approach_features;
mod sorted_vec;

const H3: i64 = 100;
const HOUR_MS: i64 = 3_600_000;

/// `percentile` с прогревом 0 — прежнее поведение до режима `floor`:
/// используется во всех тестах жизненного цикла, которым режим не важен.
fn cfg() -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: H3 },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    }
}

/// `floor` с тем же порогом — прогрев в конфиге стоит намеренно большим:
/// тест на разницу режимов обязан доказывать, что `floor` его не читает,
/// а не просто подставлять 0 и совпасть с `percentile` по умолчанию.
fn cfg_floor() -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Floor { h3_lots: H3 },
        warmup_ms: HOUR_MS,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    }
}

fn ob(tick: i64, size: i64) -> LevelObs {
    LevelObs {
        tick,
        size_lots: size,
        in_top50: true,
    }
}

fn ob_out(tick: i64, size: i64) -> LevelObs {
    LevelObs {
        tick,
        size_lots: size,
        in_top50: false,
    }
}

/// Свёртка аргументов в пары, чтобы помощник не спорил с лимитом числа
/// параметров: `span` — (рождение, смерть), `peak` — (максимум, время до него).
fn rec(
    tick: i64,
    span: (i64, i64),
    peak: (i64, i64),
    mono: bool,
    repeat: u32,
    repriced: bool,
    kind: DeathKind,
) -> LevelRecord {
    LevelRecord {
        side: Side::Bid,
        price_tick: tick,
        birth_ms: span.0,
        death_ms: span.1,
        lifetime_ms: span.1 - span.0,
        size_max: peak.0,
        time_to_max_ms: peak.1,
        size_monotonic: mono,
        repeat_count: repeat,
        repriced,
        death: kind,
        traded_lots: 0,
        rpi_lots: 0,
    }
}

/// Синтетика §1.1: четыре известных уровня плюс один вспомогательный.
///
/// L1 (тик 1000) — съеден: затухание 120→150→180→30, смерть по доле.
/// L2 (тик 2000) — снят: 110→90→200 и уход за топ-50 при живом размере;
///   провал 110→90 до максимума гасит монотонность.
/// L3 (тик 3000) — частичный: 130→130→140→28 (ровно 20% — жив) →27,
///   смерть только на 27. Проверяет строгость границы.
/// L4 (тик 1000) — пересоздан: новое рождение после смерти L1, поэтому
///   `repeat_count == 1`; умирает в кадре, где на соседнем тике 1001
///   рождается сопоставимый размер, поэтому `repriced == true`.
/// L5 (тик 1001) — вспомогательный триггер `repriced` для L4: рождение
///   150 против максимума 170 укладывается в удвоение; `time_to_max == 0`,
///   потому что максимум взят уже кадром рождения.
#[test]
fn four_known_levels_give_known_classes_and_features() {
    let mut tr = LevelTracker::new(cfg());
    let mut out = Vec::with_capacity(16);

    tr.observe_frame(
        1000,
        Side::Bid,
        &[ob(1000, 120), ob(2000, 110), ob(3000, 130)],
        &mut out,
    );
    tr.observe_frame(
        2000,
        Side::Bid,
        &[ob(1000, 150), ob(2000, 90), ob(3000, 130)],
        &mut out,
    );
    tr.observe_frame(
        3000,
        Side::Bid,
        &[ob(1000, 180), ob(2000, 200), ob(3000, 140)],
        &mut out,
    );
    tr.observe_frame(
        4000,
        Side::Bid,
        &[ob(1000, 30), ob_out(2000, 60), ob(3000, 140)],
        &mut out,
    );
    assert_eq!(
        out,
        vec![
            rec(
                1000,
                (1000, 4000),
                (180, 2000),
                true,
                0,
                false,
                DeathKind::BelowFraction
            ),
            rec(
                2000,
                (1000, 4000),
                (200, 2000),
                false,
                0,
                false,
                DeathKind::LeftTop
            ),
        ],
        "кадр 4000: L1 съеден по доле, L2 снят уходом за топ-50"
    );

    tr.observe_frame(5000, Side::Bid, &[ob(1000, 160), ob(3000, 28)], &mut out);
    assert_eq!(
        out.len(),
        2,
        "кадр 5000: смертей нет, L3 держится ровно на 20%"
    );

    tr.observe_frame(6000, Side::Bid, &[ob(1000, 170), ob(3000, 27)], &mut out);
    assert_eq!(
        out[2],
        rec(
            3000,
            (1000, 6000),
            (140, 2000),
            true,
            0,
            false,
            DeathKind::BelowFraction
        ),
        "L3 умер только ниже строгой границы, спад после максимума монотонность не гасит"
    );

    tr.observe_frame(7000, Side::Bid, &[ob(1000, 10), ob(1001, 150)], &mut out);
    assert_eq!(
        out[3],
        rec(
            1000,
            (5000, 7000),
            (170, 1000),
            true,
            1,
            true,
            DeathKind::BelowFraction
        ),
        "L4 — новый уровень на той же цене: повтор учтён, переставление замечено"
    );

    tr.observe_frame(8000, Side::Bid, &[], &mut out);
    assert_eq!(
        out[4],
        rec(
            1001,
            (7000, 8000),
            (150, 0),
            true,
            0,
            false,
            DeathKind::BelowFraction
        ),
        "вспомогательный L5: максимум кадром рождения, смерть исчезновением"
    );

    assert_eq!(out.len(), 5);
    assert_eq!(
        tr.live_count(),
        0,
        "фикстура обязана никого не оставлять висеть"
    );
    assert!(
        out.iter().all(|r| r.traded_lots == 0),
        "объём против уровня заполняет шаг 1.2, здесь всегда ноль"
    );
}

/// Реплей тех же восьми кадров, что и синтетика с четырьмя известными
/// уровнями выше, — фиксирует то, что различает `H3Mode` по плану D-H3:
/// `warmup_ms` в конфиге стоит намеренно больше всего диапазона фикстуры.
fn replay_four_known_levels(cfg: LevelsConfig) -> Vec<LevelRecord> {
    let mut tr = LevelTracker::new(cfg);
    let mut out = Vec::with_capacity(16);
    tr.observe_frame(
        1000,
        Side::Bid,
        &[ob(1000, 120), ob(2000, 110), ob(3000, 130)],
        &mut out,
    );
    tr.observe_frame(
        2000,
        Side::Bid,
        &[ob(1000, 150), ob(2000, 90), ob(3000, 130)],
        &mut out,
    );
    tr.observe_frame(
        3000,
        Side::Bid,
        &[ob(1000, 180), ob(2000, 200), ob(3000, 140)],
        &mut out,
    );
    tr.observe_frame(
        4000,
        Side::Bid,
        &[ob(1000, 30), ob_out(2000, 60), ob(3000, 140)],
        &mut out,
    );
    tr.observe_frame(5000, Side::Bid, &[ob(1000, 160), ob(3000, 28)], &mut out);
    tr.observe_frame(6000, Side::Bid, &[ob(1000, 170), ob(3000, 27)], &mut out);
    tr.observe_frame(7000, Side::Bid, &[ob(1000, 10), ob(1001, 150)], &mut out);
    tr.observe_frame(8000, Side::Bid, &[], &mut out);
    out
}

/// Синтетика с четырьмя известными уровнями (план D-H3, таск 02) — для
/// обоих режимов `H3`, с прогревом в конфиге больше всего диапазона
/// фикстуры (8000 мс против часового `warmup_ms`): `floor` обязан
/// эмитировать все пять записей (прогрева у него нет по определению
/// режима), `percentile` с тем же конфигом — ни одной (прогрев не истёк).
/// Это и есть разница режимов, которую нельзя было увидеть на `warmup_ms
/// == 0` — там оба режима совпадают тривиально.
#[test]
fn floor_mode_ignores_warmup_percentile_mode_respects_it() {
    let floor_out = replay_four_known_levels(cfg_floor());
    assert_eq!(
        floor_out.len(),
        5,
        "floor: без прогрева фикстура эмитит все пять уровней, как при warmup_ms=0"
    );

    let percentile_cfg = LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: H3 },
        warmup_ms: HOUR_MS,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let percentile_out = replay_four_known_levels(percentile_cfg);
    assert!(
        percentile_out.is_empty(),
        "percentile: часовой прогрев не истёк за 8 секунд фикстуры — эмиссии нет"
    );
}

/// Прогрев 60 минут (H3): уровень, рождённый до конца прогрева, в выборке
/// отсутствует — но его рождение считается для `repeat_count` позднего
/// уровня на той же цене: рождался он буквально.
#[test]
fn warmup_births_are_tracked_but_not_emitted() {
    let cfg = LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: H3 },
        warmup_ms: HOUR_MS,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut tr = LevelTracker::new(cfg);
    let mut out = Vec::with_capacity(8);

    tr.observe_frame(0, Side::Bid, &[ob(5000, 50)], &mut out);
    tr.observe_frame(3_500_000, Side::Bid, &[ob(5000, 120)], &mut out);
    tr.observe_frame(3_510_000, Side::Bid, &[ob(5000, 10)], &mut out);
    assert!(
        out.is_empty(),
        "прогревный уровень обязан отсутствовать в выборке"
    );

    tr.observe_frame(3_700_000, Side::Bid, &[ob(5000, 130)], &mut out);
    tr.observe_frame(3_800_000, Side::Bid, &[ob(5000, 20)], &mut out);
    assert_eq!(
        out,
        vec![LevelRecord {
            side: Side::Bid,
            price_tick: 5000,
            birth_ms: 3_700_000,
            death_ms: 3_800_000,
            lifetime_ms: 100_000,
            size_max: 130,
            time_to_max_ms: 0,
            size_monotonic: true,
            repeat_count: 1,
            repriced: false,
            death: DeathKind::BelowFraction,
            traded_lots: 0,
            rpi_lots: 0,
        }],
        "выживает только послепрогревный уровень, повтор прогревного учтён"
    );
    assert_eq!(tr.live_count(), 0);
}

/// Детерминизм реплея (требование архитектуры к шагу 1.1): одна и та же
/// последовательность кадров даёт побайтово одинаковый вывод.
#[test]
fn same_frames_twice_give_byte_identical_output() {
    fn replay() -> Vec<LevelRecord> {
        let mut tr = LevelTracker::new(cfg());
        let mut out = Vec::with_capacity(16);
        tr.observe_frame(1000, Side::Bid, &[ob(1000, 120), ob(2000, 110)], &mut out);
        tr.observe_frame(2000, Side::Bid, &[ob(1000, 150), ob(2000, 90)], &mut out);
        tr.observe_frame(3000, Side::Bid, &[ob(1000, 180), ob(2000, 200)], &mut out);
        tr.observe_frame(4000, Side::Bid, &[ob(1000, 30), ob_out(2000, 60)], &mut out);
        tr.observe_frame(5000, Side::Ask, &[ob(1000, 160)], &mut out);
        tr.observe_frame(6000, Side::Ask, &[ob(1000, 10)], &mut out);
        out
    }
    assert_eq!(replay(), replay());
}

/// Гейт GC для шага 1.1: установившийся кадр без рождений и смертей
/// не аллоцирует. Замер идёт через счётчик на глобальном аллокаторе,
/// цены те же, что в прогреве, — новых ключей в картах не возникает.
#[test]
fn steady_frames_allocate_nothing() {
    let mut tr = LevelTracker::new(cfg());
    let mut out = Vec::with_capacity(16);
    // Прогрев: рождения и смерти растягивают все буферы до рабочего размера.
    tr.observe_frame(1000, Side::Bid, &[ob(1000, 120), ob(1001, 130)], &mut out);
    tr.observe_frame(2000, Side::Bid, &[ob(1000, 150), ob(1001, 160)], &mut out);
    tr.observe_frame(3000, Side::Bid, &[ob(1000, 10), ob(1001, 10)], &mut out);
    out.clear();
    tr.observe_frame(4000, Side::Bid, &[ob(1000, 140), ob(1001, 150)], &mut out);
    tr.observe_frame(5000, Side::Bid, &[ob(1000, 145), ob(1001, 155)], &mut out);
    out.clear();

    let (_, counts) = crate::alloc_count::measure(|| {
        for i in 0..1000 {
            // Обновления живых уровней плюс промах по цене, которой нет:
            // ни вставка, ни поиск отсутствующего ключа кучу не трогают.
            tr.observe_frame(
                6000 + i,
                Side::Bid,
                &[ob(1000, 145), ob(1001, 155), ob(9999, 10)],
                &mut out,
            );
            out.clear();
        }
    });
    assert_eq!(counts.allocations, 0, "горячий путь обязан не аллоцировать");
    assert_eq!(tr.live_count(), 2);
}

/// Граница модулей (требование архитектуры к шагу 1.1): чистая логика не
/// знает про транспорт и системные часы, целые не размениваются на
/// приближённые числа, а живые карты (`live`/`births`/`carry`) не возвращаются
/// к дереву-словарю, которое аллоцирует узел на каждое рождение (W4б — было
/// исправлено на отсортированный `Vec` с двоичным поиском, `SortedVec` по
/// образцу `Book::HalfBook`, A4). Проверка — грепом по собственному исходнику.
///
/// Запрещённые фрагменты собраны из частей: литерал целиком триггерил бы
/// эту же проверку сам на себя.
#[test]
fn module_stays_detached_from_transport_clocks_and_approx_numbers() {
    const SRCS: [&str; 7] = [
        include_str!("../levels.rs"),
        include_str!("config.rs"),
        include_str!("records.rs"),
        include_str!("state.rs"),
        include_str!("strength.rs"),
        include_str!("tracker.rs"),
        include_str!("scan.rs"),
    ];
    let banned = [
        concat!("by", "bit"),
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("f", "64"),
        concat!("std::", "time"),
        concat!("BTree", "Map"),
        concat!("Hash", "Map"),
    ];
    for b in banned {
        for src in SRCS {
            assert!(!src.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}

/// Шаг 1.2: объём трейдов с агрессором против уровня делит исходы ровно
/// по правилу 70/20; чужая сторона, блочные сделки и время вне жизни
/// уровня в объём не попадают. Склейка — по времени матчинга `T`
/// (поле `exch_ms`) против жизни уровня, а не по порядку подачи.
#[test]
fn aggressor_volume_splits_eaten_pulled_and_mixed() {
    use Outcome::{Eaten, Mixed, Pulled};
    let mut tr = LevelTracker::new(cfg());
    let mut out = Vec::with_capacity(16);

    tr.observe_frame(
        1000,
        Side::Bid,
        &[ob(1000, 200), ob(2000, 200), ob(3000, 200)],
        &mut out,
    );
    tr.observe_frame(1000, Side::Ask, &[ob(4000, 200)], &mut out);

    // Съеден (ровно 70%): 100 + 40 своих, чужое и блочное мимо.
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 100,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2000,
    });
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 500,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
        exch_ms: 2100,
    });
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 500,
        aggressor_is_buy: false,
        block: true,
        rpi: false,
        exch_ms: 2200,
    });
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 50,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 500,
    });
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 40,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 3000,
    });
    // Снят (ровно 20%).
    tr.observe_trade(TradeHit {
        tick: 2000,
        lots: 40,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2500,
    });
    tr.observe_trade(TradeHit {
        tick: 2000,
        lots: 100,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
        exch_ms: 2600,
    });
    // Середина (50%).
    tr.observe_trade(TradeHit {
        tick: 3000,
        lots: 100,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2500,
    });
    // Аск ест только покупатель-агрессор.
    tr.observe_trade(TradeHit {
        tick: 4000,
        lots: 150,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
        exch_ms: 2500,
    });
    tr.observe_trade(TradeHit {
        tick: 4000,
        lots: 200,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2600,
    });

    tr.observe_frame(
        4000,
        Side::Bid,
        &[ob(1000, 10), ob(2000, 10), ob(3000, 10)],
        &mut out,
    );
    tr.observe_frame(4000, Side::Ask, &[ob(4000, 10)], &mut out);

    assert_eq!(out.len(), 4);
    let by_tick = |t: i64| out.iter().find(|r| r.price_tick == t).copied().unwrap();
    let eaten = by_tick(1000);
    let pulled = by_tick(2000);
    let mixed = by_tick(3000);
    let ask_eaten = by_tick(4000);
    assert_eq!(eaten.traded_lots, 140);
    assert_eq!(pulled.traded_lots, 40);
    assert_eq!(mixed.traded_lots, 100);
    assert_eq!(ask_eaten.traded_lots, 150);
    assert_eq!(eaten.outcome(), Eaten);
    assert_eq!(pulled.outcome(), Pulled);
    assert_eq!(mixed.outcome(), Mixed);
    assert_eq!(ask_eaten.outcome(), Eaten);
    assert_eq!(classify_outcome(140, 200), Eaten);
    assert_eq!(classify_outcome(40, 200), Pulled);
    assert_eq!(classify_outcome(100, 200), Mixed);

    // Трейд по мёртвому уровню после смерти ни на что не влияет.
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 1000,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 5000,
    });
    assert_eq!(tr.live_count(), 0);
    assert_eq!(out.len(), 4);
}

/// RPI-сделка исполнилась об **невидимую** заявку маркет-мейкера, а не об этот
/// уровень (В-55): в объём против видимой плотности она не идёт — иначе уровень
/// помечался бы «съели» за счёт чужого объёма. Считается она отдельно, чтобы
/// было видно, сколько на этой цене прошло мимо книги.
#[test]
fn rpi_trade_does_not_count_as_visible_consumption_but_is_counted_separately() {
    let mut tr = LevelTracker::new(cfg());
    let mut out = Vec::with_capacity(4);

    tr.observe_frame(1000, Side::Bid, &[ob(1000, 200)], &mut out);
    tr.observe_frame(1000, Side::Ask, &[ob(4000, 200)], &mut out);

    // 150 лотов об RPI: этого хватило бы на «съели» (это 75 % от 200), но
    // видимый уровень такие сделки не потребляют.
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 150,
        aggressor_is_buy: false,
        block: false,
        rpi: true,
        exch_ms: 2000,
    });
    // Обычная сделка на 20 лотов: ровно 20 % от 200 — «снят».
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 20,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2100,
    });

    tr.observe_frame(3000, Side::Bid, &[ob(1000, 10)], &mut out);
    tr.observe_frame(3000, Side::Ask, &[ob(4000, 10)], &mut out);

    let bid = out.iter().find(|r| r.price_tick == 1000).copied().unwrap();
    assert_eq!(
        bid.traded_lots, 20,
        "RPI-объём не входит в объём против видимого уровня"
    );
    assert_eq!(bid.rpi_lots, 150, "RPI-объём обязан считаться отдельно");
    assert_eq!(
        bid.outcome(),
        Outcome::Pulled,
        "без RPI-объёма исход — «снят»; с ним ошибочно вышло бы «съели»"
    );
}

/// Гейт GC для шага 1.2: трейды на живом уровне не аллоцируют —
/// только поиск в карте и сложение целых.
#[test]
fn trades_on_a_live_level_allocate_nothing() {
    let mut tr = LevelTracker::new(cfg());
    let mut out = Vec::with_capacity(16);
    tr.observe_frame(1000, Side::Bid, &[ob(1000, 200)], &mut out);
    let (_, counts) = crate::alloc_count::measure(|| {
        for i in 0..1000 {
            tr.observe_trade(TradeHit {
                tick: 1000,
                lots: 1,
                aggressor_is_buy: false,
                block: false,
                rpi: false,
                exch_ms: 1000 + i,
            });
        }
    });
    assert_eq!(counts.allocations, 0, "трейд обязан не аллоцировать");
    assert_eq!(tr.live_count(), 1);
}
