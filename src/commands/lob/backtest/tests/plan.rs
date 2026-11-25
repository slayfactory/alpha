//! План сделки-отскока: геометрия, пол, зеркало для аска.

use super::*;

/// F5 (В-74): режим `entry_ttl` превращается в **потолок** срока жизни входа
/// и два условия снятия — порог В-66 в единицах крейта (`--h3-usd / цена
/// уровня`) и полосу ухода (`--band-exit-bps`). Режим `touch` (гейт «те же
/// круги») оставляет прежний срок (конец касания) и выключает условия нулями.
#[test]
fn bounce_plan_turns_the_entry_ttl_mode_into_the_ceiling_and_the_two_conditions() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005)); // P = 10.00, вход 10.05
    let form = base("pct2", "1to1");
    let shape = |entry_ttl: EntryTtl| PlanShape {
        entry_ttl,
        h3_usd: Some(50.0),
        band_exit_bps: 20.0,
        ..plain_shape()
    };
    let plan_fields = |plan: &TradePlan| match plan {
        TradePlan::Bounce {
            entry_ttl_ns,
            level_floor_qty,
            band_exit_bps,
            ..
        } => (*entry_ttl_ns, *level_floor_qty, *band_exit_bps),
        TradePlan::SpreadHold => panic!("отскок обязан быть Bounce"),
    };

    // Прежний режим: срок — конец касания (1 с), условия выключены.
    let (_, plan) = bounce_plan(&touch, tick, form, None, shape(EntryTtl::Touch)).unwrap();
    let (ttl, floor, band) = plan_fields(&plan);
    assert_eq!(
        ttl,
        1_000 * 1_000_000,
        "touch — прежний срок, конец касания"
    );
    assert_eq!((floor, band), (0.0, 0.0), "условия F5 в touch выключены");

    // Потолок 60 с из сетки В-74; порог В-66 — 50 / 10.00 = 5 единиц крейта.
    let (_, plan) = bounce_plan(&touch, tick, form, None, shape(EntryTtl::Secs(60))).unwrap();
    let (ttl, floor, band) = plan_fields(&plan);
    assert_eq!(ttl, 60 * 1_000_000_000, "потолок — секунды сетки В-74");
    assert!(
        (floor - 5.0).abs() < 1e-9,
        "порог В-66 = --h3-usd / цена уровня: {floor}"
    );
    assert!((band - 20.0).abs() < 1e-9, "полоса — число флага: {band}");

    // `wall` — только условия рынка, потолка-таймера нет.
    let (_, plan) = bounce_plan(&touch, tick, form, None, shape(EntryTtl::Wall)).unwrap();
    let (ttl, _, _) = plan_fields(&plan);
    assert_eq!(ttl, i64::MAX, "wall — без предохранительного потолка");
}

/// В-62: вход — от **первого фронтранера**; стоп — `a × σ_H` bps от входа в
/// целых тиках вверх, но не ближе тика за плотностью; тейк — `b × σ_H` bps
/// от входа, но не ниже `k × 4.41` bps круга комиссий (В-63). Числа: P = 10.00, вход 10.05
/// (тик 1005), σ = 100 bps: стоп 1×σ = 100 bps × 1005 / 10⁴ = 10.05 тика →
/// 11 тиков → 9.94 (дальше пола 9.99 — берётся он же, 9.94); тейк 2×σ =
/// 200 bps → 20.1 → 21 тиков → 10.26.
#[test]
fn bounce_plan_puts_stop_and_take_at_sigma_multiples_from_the_entry() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    let (_, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 2.0, 1.0),
        Some(100.0),
        plain_shape(),
    )
    .unwrap();
    let (entry, stop, take) = plan_prices(&plan);
    assert!((entry - 10.05).abs() < 1e-9, "вход от фронтранера: {entry}");
    assert!(
        (stop - 9.94).abs() < 1e-9,
        "стоп 1×σ = 11 тиков ниже входа: {stop}"
    );
    assert!(
        (take - 10.26).abs() < 1e-9,
        "тейк 2×σ = 21 тик выше входа: {take}"
    );
}

/// В-62, полы: при `σ` малой (или нулевой) стоп встаёт на тик **за**
/// плотностью (`P − 1`), а не на входе, тейк — на `k × 4.41` bps: форма не
/// вырождается в «стоп на цене входа» сетки В-58.
#[test]
fn bounce_plan_floors_keep_the_form_non_degenerate() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    // σ = 0: стоп 0 тиков → пол 9.99; тейк max(0, 3 × 4.41 = 13.23 bps) →
    // 13.23 bps × 1005 / 10⁴ = 1.33 тика → 2 тика → 10.07.
    let (_, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 1.0, 3.0),
        Some(0.0),
        plain_shape(),
    )
    .unwrap();
    let (_, stop, take) = plan_prices(&plan);
    assert!(
        (stop - 9.99).abs() < 1e-9,
        "стоп на тике за плотностью: {stop}"
    );
    assert!(
        (take - 10.07).abs() < 1e-9,
        "тейк на полу 3 × 4.41 bps: {take}"
    );
    // Стоп по σ ближе пола (5 bps → 5.025 тика → 6 тиков → 9.99): совпадает с
    // полом; 2 bps → 2.01 → 3 тика → 10.02 — ближе пола, берётся пол 9.99.
    let (_, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 1.0, 1.0),
        Some(2.0),
        plain_shape(),
    )
    .unwrap();
    let (_, stop, _) = plan_prices(&plan);
    assert!(
        (stop - 9.99).abs() < 1e-9,
        "пол дальше σ-стопа — берётся пол: {stop}"
    );
}

/// В-62, аск зеркально: уровень 10.00 (аск), фронтранер 9.95; стоп выше входа,
/// не ближе `P + 1`; тейк ниже входа.
#[test]
fn bounce_plan_mirrors_the_geometry_for_the_ask() {
    let tick = 0.01_f64;
    let mut touch = bounce_touch(1_000, Some(995));
    touch.side = Side::Ask;
    let (dir, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 2.0, 1.0),
        Some(100.0),
        plain_shape(),
    )
    .unwrap();
    assert_eq!(dir, SIGMA_SHORT);
    let (entry, stop, take) = plan_prices(&plan);
    assert!((entry - 9.95).abs() < 1e-9, "{entry}");
    // 100 bps × 995 / 10⁴ = 9.95 тика → 10 тиков → 10.05; пол P + 1 = 10.01 —
    // σ-стоп дальше, берётся он.
    assert!((stop - 10.05).abs() < 1e-9, "{stop}");
    // 200 bps → 19.9 → 20 тиков → 9.75.
    assert!((take - 9.75).abs() < 1e-9, "{take}");
}

/// Фронтрана впереди не было — вход прежний (`P + 1` тик), геометрия та же.
#[test]
fn bounce_plan_without_frontrun_enters_one_tick_before_the_level() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, None);
    let (_, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 1.0, 1.0),
        Some(50.0),
        plain_shape(),
    )
    .unwrap();
    let (entry, stop, take) = plan_prices(&plan);
    assert!((entry - 10.01).abs() < 1e-9, "вход P+1: {entry}");
    // 50 bps × 1001 / 10⁴ = 5.005 → 6 тиков → 9.95 (дальше пола 9.99).
    assert!((stop - 9.95).abs() < 1e-9, "{stop}");
    // max(50, 4.41) = 50 bps → 6 тиков → 10.07.
    assert!((take - 10.07).abs() < 1e-9, "{take}");
}

/// T4 (П-02, Г-86): `EntryForm::Market` ставит вход за
/// `MARKET_CROSS_MARGIN_BPS` от стены (не от фронтранера — фронтран здесь
/// есть, но форма его игнорирует, в отличие от `single@fr`), и снимает
/// `post_only` независимо от формы, пришедшей в `PlanShape` (рыночный вход
/// не может быть мейкером, GTX отклонил бы пересёкшую спред заявку как
/// Expired, В-72).
#[test]
fn bounce_plan_market_entry_crosses_the_spread_and_is_never_post_only() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005)); // P = 10.00, фронтран 10.05
    let mut shape = plain_shape();
    shape.entry_form = EntryForm::Market;
    shape.post_only = true; // рыночный вход обязан снять этот флаг сам
    let (_, plan) = bounce_plan(&touch, tick, base("behind", "1to1"), None, shape).unwrap();
    let (entry, stop, _) = plan_prices(&plan);
    let expected_tick =
        1_000 + super::super::forms::bps_to_ticks_ceil(MARKET_CROSS_MARGIN_BPS, 1_000);
    assert!(
        (entry - expected_tick as f64 * tick).abs() < 1e-9,
        "entry = {entry}, expected tick {expected_tick}"
    );
    // Стоп формы `behind` не зависит от входа — по-прежнему `P - 1`.
    assert!((stop - 9.99).abs() < 1e-9, "{stop}");
    match plan {
        TradePlan::Bounce { post_only, .. } => {
            assert!(!post_only, "рыночный вход не может быть GTX (пост-онли)")
        }
        TradePlan::SpreadHold => panic!("отскок обязан быть Bounce"),
    }
}

/// `EntryForm::Market` разбирается из `"market"` и печатается тем же именем
/// — критерий приёмки T4, как у `single@fr`/`ladder…`.
#[test]
fn entry_form_market_round_trips_through_its_label() {
    assert_eq!(EntryForm::parse("market").unwrap(), EntryForm::Market);
    assert_eq!(EntryForm::Market.label(), "market");
}

/// Г-135 (П-17): `postsweep<R>` — каноническое имя туда и обратно, `R = 0`, ведущие нули и мусор — отказ; цена входа и
/// `post_only` — как у `market`, а `R` уходит в конфиг стратегии.
#[test]
fn entry_form_postsweep_round_trips_and_enters_like_market() {
    let f = EntryForm::parse("postsweep5").unwrap();
    assert_eq!(f, EntryForm::Postsweep { rise_bps: 5 });
    assert_eq!(f.label(), "postsweep5");
    for bad in [
        "postsweep",
        "postsweep0",
        "postsweep05",
        "postsweep-5",
        "postsweep5x",
    ] {
        assert!(EntryForm::parse(bad).is_err(), "{bad}");
    }
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    let mut shape = plain_shape();
    shape.entry_form = f;
    shape.post_only = true;
    let (_, plan) = bounce_plan(&touch, tick, base("behind", "1to1"), None, shape).unwrap();
    let (entry, _, _) = plan_prices(&plan);
    let expected_tick =
        1_000 + super::super::forms::bps_to_ticks_ceil(MARKET_CROSS_MARGIN_BPS, 1_000);
    assert!(
        (entry - expected_tick as f64 * tick).abs() < 1e-9,
        "{entry}"
    );
    match plan {
        TradePlan::Bounce {
            post_only, pyramid, ..
        } => {
            assert!(!post_only);
            assert_eq!(pyramid.postsweep_bps, 5);
        }
        TradePlan::SpreadHold => panic!("отскок обязан быть Bounce"),
    }
}

/// T-35 (Г-85): `single@fr+N` / `single@fr_N` — отступ от фронтрана к рынку / к стене; имя туда и обратно,
/// нулевой, ведущие нули и `-` (разделитель полей имени формы) — отказ.
#[test]
fn entry_form_frontrun_offset_round_trips_and_rejects_non_canonical() {
    for (s, t) in [
        ("single@fr+2", 2),
        ("single@fr_3", -3),
        ("single@fr+10", 10),
    ] {
        let f = EntryForm::parse(s).unwrap();
        assert_eq!(f, EntryForm::SingleFrontrunOffset { ticks: t });
        assert_eq!(f.label(), s);
    }
    for bad in [
        "single@fr+0",
        "single@fr_0",
        "single@fr+02",
        "single@fr-2",
        "single@fr2",
        "single@fr+x",
    ] {
        assert!(EntryForm::parse(bad).is_err(), "{bad}");
    }
}

/// T-35: бид `P = 10.00`, фронтран `10.05`: `+2` — `10.07`, `_3` — `10.02`, `_9` — не ближе `P + 1` (`10.01`);
/// без фронтрана отступ от `P + 1`.
#[test]
fn bounce_plan_frontrun_offset_moves_the_single_entry_in_ticks() {
    let tick = 0.01_f64;
    let entry_of = |fr: Option<i64>, ticks: i32| {
        let touch = bounce_touch(1_000, fr);
        let mut shape = plain_shape();
        shape.entry_form = EntryForm::SingleFrontrunOffset { ticks };
        let (_, plan) = bounce_plan(&touch, tick, base("behind", "1to1"), None, shape).unwrap();
        plan_prices(&plan).0
    };
    for (fr, ticks, want) in [
        (Some(1_005), 2, 10.07),
        (Some(1_005), -3, 10.02),
        (Some(1_005), -9, 10.01),
        (None, 2, 10.03),
        (None, -1, 10.01),
    ] {
        let e = entry_of(fr, ticks);
        assert!(
            (e - want).abs() < 1e-9,
            "fr {fr:?} ticks {ticks}: {e} ≠ {want}"
        );
    }
}

/// Формы базы (В-65) на бид-уровне `P = 10.00` с фронтранером `10.05`:
/// `before` → стоп `P+1` = 10.01, тейк 1:1 от входа 10.09; `at` → 10.00 /
/// 10.10; `behind` → 9.99 / 10.11; `midfr` → середина между 10.05 и 10.00 =
/// 10.02 (два тика от уровня, целая часть) / 10.08; `pct1` → 1 % от входа =
/// 10.05 тика → 11 тиков → 9.94 / 10.16; `stack2` — нужна вторая плотность.
#[test]
fn base_stop_forms_are_positional_from_the_level_and_the_entry() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    let cases = [
        ("before", 10.01, 10.09),
        ("at", 10.00, 10.10),
        ("behind", 9.99, 10.11),
        ("midfr", 10.02, 10.08),
        ("pct1", 9.94, 10.16),
    ];
    for (stop, want_stop, want_take) in cases {
        let (_, plan) = bounce_plan(&touch, tick, base(stop, "1to1"), None, plain_shape())
            .unwrap_or_else(|| panic!("{stop}: форма обязана строиться"));
        let (entry, got_stop, got_take) = plan_prices(&plan);
        assert!(
            (entry - 10.05).abs() < 1e-9,
            "{stop}: вход от фронтрана: {entry}"
        );
        assert!(
            (got_stop - want_stop).abs() < 1e-9,
            "{stop}: стоп {got_stop} вместо {want_stop}"
        );
        assert!(
            (got_take - want_take).abs() < 1e-9,
            "{stop}: тейк {got_take} вместо {want_take}"
        );
    }
    // Вторая плотность завала на 9.95: стоп за ней — 9.94, тейк 1:1 — 10.16.
    let mut stacked = touch;
    stacked.stack_next_tick = Some(995);
    let (_, plan) =
        bounce_plan(&stacked, tick, base("stack2", "1to1"), None, plain_shape()).unwrap();
    let (_, got_stop, got_take) = plan_prices(&plan);
    assert!((got_stop - 9.94).abs() < 1e-9, "{got_stop}");
    assert!((got_take - 10.16).abs() < 1e-9, "{got_take}");
    assert!(
        bounce_plan(&touch, tick, base("stack2", "1to1"), None, plain_shape()).is_none(),
        "без второй плотности форма stack2 не строится"
    );
}

/// Без фронтрана вход `P+1`: `before` и `midfr` вырождаются (стоп совпал бы
/// со входом) — `None`, а `at`/`behind`/`pct` строятся; σ-формам без σ — `None`.
#[test]
fn base_forms_that_need_a_frontrun_skip_touches_without_one() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, None);
    assert!(bounce_plan(&touch, tick, base("before", "1to1"), None, plain_shape()).is_none());
    assert!(bounce_plan(&touch, tick, base("midfr", "1to1"), None, plain_shape()).is_none());
    let (_, plan) = bounce_plan(&touch, tick, base("at", "1to1"), None, plain_shape()).unwrap();
    let (entry, stop, take) = plan_prices(&plan);
    assert!(
        (entry - 10.01).abs() < 1e-9 && (stop - 10.00).abs() < 1e-9 && (take - 10.02).abs() < 1e-9
    );
    assert!(bounce_plan(&touch, tick, base("behind", "1to1"), None, plain_shape()).is_some());
    assert!(bounce_plan(&touch, tick, base("pct0.5", "1to1"), None, plain_shape()).is_some());
    assert!(
        bounce_plan(&touch, tick, geometry(1.0, 1.0, 1.0), None, plain_shape()).is_none(),
        "σ-форма без σ не строится"
    );
}

/// Аск зеркально для позиционных форм: уровень 10.00 (аск), фронтранер 9.95;
/// `before` → стоп 9.99, тейк 9.91; `pct1` → 9.95 × 1 % = 9.95 тика → 10 тиков
/// → стоп 10.05, тейк 9.85.
#[test]
fn base_forms_mirror_for_the_ask() {
    let tick = 0.01_f64;
    let mut touch = bounce_touch(1_000, Some(995));
    touch.side = Side::Ask;
    let (dir, plan) =
        bounce_plan(&touch, tick, base("before", "1to1"), None, plain_shape()).unwrap();
    assert_eq!(dir, SIGMA_SHORT);
    let (entry, stop, take) = plan_prices(&plan);
    assert!(
        (entry - 9.95).abs() < 1e-9 && (stop - 9.99).abs() < 1e-9 && (take - 9.91).abs() < 1e-9,
        "{entry} {stop} {take}"
    );
    let (_, plan) = bounce_plan(&touch, tick, base("pct1", "1to1"), None, plain_shape()).unwrap();
    let (_, stop, take) = plan_prices(&plan);
    assert!(
        (stop - 10.05).abs() < 1e-9 && (take - 9.85).abs() < 1e-9,
        "{stop} {take}"
    );
}
