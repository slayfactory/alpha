//! Тесты TK-747 (В-194, В-195, В-196): ключи `lm` (уровень без усреднения, ближний пивот), `lz` (закол двух пивотов), `pp` (любой прокол), `fw` (свежесть по владельцу). Ключи выключены — уровни и кандидаты прежние.

use super::*;
use crate::breakout::levels::build_levels_tracked;
use crate::breakout::params::{parse_variant, SigCfg};
use crate::breakout::sigcache::cfg_key_text;
use crate::breakout::tests::{bars_world, HB};
use crate::breakout::tick::fresh_fw_ok;

fn with_keys(keys: &[(&str, &str)]) -> Params {
    let mut p = lv_params();
    for (k, v) in keys {
        p.set(k, v).expect("ключ");
    }
    p.validate().expect("проверка");
    p
}

fn levels_of(c: Cols, p: &Params, side: Side) -> Vec<Level> {
    build_levels_tracked(&sym(c), &p.sig.lv, side, true)
}

// ───────────────────────── lm: цена уровня без усреднения ─────────────────────────

#[test]
fn lm_level_price_is_the_extreme_pivot_in_the_breakout_direction() {
    // мир `level_world`: пивоты-максимумы 100,00 (бар 1) и 100,05 (бар 5); среднее 100,025
    let off = levels_of(level_world(), &with_keys(&[]), Side::Long);
    assert_eq!(off[0].segs[0].1, px(100.025), "lm = 0: среднее, как раньше");
    let p = with_keys(&[("lm", "1")]);
    let on = levels_of(level_world(), &p, Side::Long);
    assert_eq!(on.len(), 1);
    assert_eq!(
        (on[0].born, on[0].end, on[0].first_bar),
        (off[0].born, off[0].end, off[0].first_bar),
        "состав и рождение те же"
    );
    assert_eq!(
        on[0].segs[0].1,
        px(100.0),
        "лонг: ближний — самый низкий из максимумов"
    );
    // у каждого члена своя цена
    assert_eq!(on[0].far_edge(0, 1), Some(px(100.0)));
    let mut m = on[0].mem[0].clone();
    m.sort_unstable();
    assert_eq!(
        m,
        vec![(HB as u32 + 1, px(100.0)), (HB as u32 + 5, px(100.05))]
    );
    // шорт (зеркало вокруг 200): пивоты-минимумы 100,00 и 99,95; цена — самый низкий минимум
    let mc = mirror(&level_world(), 200.0);
    let off = levels_of(mc.clone(), &with_keys(&[]), Side::Short);
    assert_eq!(off[0].segs[0].1, px(99.975));
    let on = levels_of(mc, &p, Side::Short);
    assert_eq!(
        on[0].segs[0].1,
        px(100.0),
        "шорт: ближний — самый высокий из минимумов"
    );
    assert_eq!(on[0].far_edge(0, -1), Some(-px(100.0)));
    // допуск τ = 0,2 % мерится от ближнего пивота, не от среднего: пивоты 100,00 и 100,30 — от среднего 100,15 по 0,15 % (группа есть), от ближнего 100,00 — 0,3 % (группы нет)
    let wide = || pivots_world(100.0, 100.30, None);
    assert_eq!(levels_of(wide(), &with_keys(&[]), Side::Long).len(), 1);
    assert!(levels_of(wide(), &p, Side::Long).is_empty());
    assert!(levels_of(mirror(&wide(), 200.0), &p, Side::Short).is_empty());
    // пивоты 100,00 и 100,15: от ближнего 0,15 % — группа; второй (100,15) прокололближний (100,00) дальше допуска (шаг 0,05) — ближний непроколотый теперь второй, цена уровня — 100,15
    let near = levels_of(pivots_world(100.0, 100.15, None), &p, Side::Long);
    assert_eq!(near.len(), 1);
    assert_eq!(near[0].segs[0].1, px(100.15));
    assert_eq!(
        near[0].mem[0].len(),
        2,
        "проколотый пивот остаётся внутренним уровнем"
    );
    // каскад: 100,00 прокололи (100,10), третий (100,05) — ближний из непроколотых: цена уровня идёт 100,10 → 100,05
    let cas = levels_of(pivots_world(100.0, 100.10, Some(100.05)), &p, Side::Long);
    assert_eq!(cas.len(), 1);
    assert_eq!(
        cas[0].segs.iter().take(2).map(|x| x.1).collect::<Vec<_>>(),
        vec![px(100.10), px(100.05)]
    );
}

// ───────────────────────── lz: закол двух пивотов ─────────────────────────

/// Бары `HB + i`: (максимум, закрытие) — пивоты `a` (бар 1), `b` (бар 4), по желанию `c` (бар 6); остальные бары ниже.
fn pivots_world(a: f64, b: f64, c: Option<f64>) -> Cols {
    let low = (98.0, 97.6);
    let top = |h: f64| (h, h - 0.4);
    let mut spec = vec![(99.0, 98.6), top(a), (98.5, 98.1), low, top(b), low];
    if let Some(c) = c {
        spec.extend([top(c), low]);
    }
    spec.extend([low, low]);
    bars_world(&spec)
}

#[test]
fn lz_second_pivot_beyond_the_first_cancels_a_two_pivot_level() {
    let p = with_keys(&[("lz", "1")]);
    let off = with_keys(&[]);
    // лонг: шаг цены мира 0,05 (допуск прокола — 1 шаг): второй максимум (100,10) выше первого (100,00) больше чем на допуск — «перебил»: уровня нет; без ключа — есть
    assert_eq!(
        levels_of(pivots_world(100.0, 100.10, None), &off, Side::Long).len(),
        1
    );
    assert!(levels_of(pivots_world(100.0, 100.10, None), &p, Side::Long).is_empty());
    // выше на 1 шаг (100,05) — в допуске, уровень рождается
    assert_eq!(
        levels_of(pivots_world(100.0, 100.05, None), &p, Side::Long).len(),
        1
    );
    // второй не выше первого (99,95 и равный 100,00) — уровень рождается
    for second in [99.95, 100.0] {
        let lv = levels_of(pivots_world(100.0, second, None), &p, Side::Long);
        assert_eq!(lv.len(), 1, "второй пивот {second}");
    }
    // шорт: зеркало — второй минимум ниже первого — отмена, выше — уровень
    let mc = |a, b| mirror(&pivots_world(a, b, None), 200.0);
    assert!(levels_of(mc(100.0, 100.10), &p, Side::Short).is_empty());
    assert_eq!(levels_of(mc(100.0, 99.95), &p, Side::Short).len(), 1);
    assert_eq!(levels_of(mc(100.0, 100.10), &off, Side::Short).len(), 1);
}

#[test]
fn lz_cascade_of_three_pivots_is_not_cancelled() {
    // пивоты 100,00, 100,10 (перебил) и 100,05: пара {1, 2} отменена, третий пивот даёт каскад из трёх — уровень рождается на нём
    let c = pivots_world(100.0, 100.10, Some(100.05));
    let off = levels_of(c.clone(), &with_keys(&[]), Side::Long);
    assert_eq!(
        off[0].born as usize,
        (HB + 6) * 60,
        "без lz — на втором пивоте"
    );
    // версии цены: рождение, присоединение третьего (далее — выпадение старого пивота по окну L)
    assert_eq!(off[0].mem[1].len(), 3, "третий присоединился");
    assert_eq!(off[0].segs[1].0 as usize, (HB + 8) * 60);
    let on = levels_of(c, &with_keys(&[("lz", "1")]), Side::Long);
    assert_eq!(on.len(), 1);
    assert_eq!(on[0].born as usize, (HB + 8) * 60, "на третьем пивоте");
    assert_eq!(on[0].mem[0].len(), 3);
}

// ───────────────────────── fw: свежесть по владельцу ─────────────────────────

fn wcfg(fw: usize, k: usize, share: usize) -> SigCfg {
    let mut c = fcfg(0, k, share, 500);
    c.fw_min = fw;
    c
}

const SET: usize = 2000;

/// Закрытия `cl` в минутах `from..to`, остальные — 100,0.
fn closes(from: usize, to: usize, cl: f64) -> Sym {
    fresh_cols(move |c| {
        for m in from..to {
            set(c, m, cl, cl, cl, cl);
        }
    })
}

#[test]
fn fw_entry_waits_the_minimum_after_the_level_is_set_and_needs_a_touch() {
    let lp = px(101.0);
    let s = closes(SET - 200, SET + 200, 100.7); // 0,297 % ниже уровня — в полосе 0,5 %
    let cfg = wcfg(30, 120, 50);
    let ok = |t: usize, touched: bool| fresh_fw_ok(&s, &cfg, SET, 1, lp, touched, t);
    assert!(!ok(SET + 29, true), "29 минут после постановки — рано");
    assert!(ok(SET + 30, true), "30 минут и проторговка — можно");
    assert!(!ok(SET + 30, false), "касания нет — нельзя");
    assert!(!ok(SET, true) && !ok(SET - 1, true));
}

#[test]
fn fw_consolidation_window_is_the_last_fk_minutes() {
    let lp = px(101.0);
    let cfg = wcfg(30, 120, 50);
    let t = SET + 130; // окно [t − 120, t): доля 50 % от минут с данными — 60 минут в полосе
    let in_band = |n: usize| closes(t - n, t, 100.7);
    assert!(fresh_fw_ok(&in_band(60), &cfg, SET, 1, lp, true, t));
    assert!(!fresh_fw_ok(&in_band(59), &cfg, SET, 1, lp, true, t));
    // закрытие на уровне и за ним в окне — отказ; до окна — не мешает
    let beyond = |m: usize| {
        fresh_cols(move |c| {
            for x in t - 120..t {
                set(c, x, 100.7, 100.7, 100.7, 100.7);
            }
            set(c, m, 100.9, 101.2, 100.9, 101.0);
        })
    };
    assert!(!fresh_fw_ok(&beyond(t - 5), &cfg, SET, 1, lp, true, t));
    assert!(fresh_fw_ok(&beyond(t - 121), &cfg, SET, 1, lp, true, t));
    // минуты без данных долю не занижают: из 40 минут с данными 20 в полосе — 50 %
    let gaps = fresh_cols(|c| {
        for x in t - 120..t - 40 {
            set(c, x, 0.0, 0.0, 0.0, 0.0);
        }
        for x in t - 40..t - 20 {
            set(c, x, 100.7, 100.7, 100.7, 100.7);
        }
        for x in t - 20..t {
            set(c, x, 99.0, 99.0, 99.0, 99.0);
        }
    });
    assert!(fresh_fw_ok(&gaps, &cfg, SET, 1, lp, true, t));
    // шорт: рама с обратным знаком — уровень −99,0, закрытия 99,3 (в раме ниже уровня)
    let sh = fresh_cols(|c| {
        for x in SET - 100..SET + 100 {
            set(c, x, 99.3, 99.3, 99.3, 99.3);
        }
    });
    assert!(fresh_fw_ok(&sh, &cfg, SET, -1, px(-99.0), true, SET + 40));
}

#[test]
fn fresh_of_set_follows_the_last_joined_pivot() {
    // пивоты 100,00 (бар 1, активен на 2), 100,05 (бар 4, активен на 5): постановка — подтверждение второго (нового) пивота
    let lv = levels_of(
        pivots_world(100.0, 100.05, None),
        &with_keys(&[]),
        Side::Long,
    );
    assert_eq!(lv[0].fresh, vec![(HB as u32 + 6) * 60]);
    // третий пивот (бар 6, активен на 7): версия 2 — постановка на нём
    let lv = levels_of(
        pivots_world(100.0, 100.05, Some(100.02)),
        &with_keys(&[]),
        Side::Long,
    );
    assert_eq!(lv[0].fresh[1], (HB as u32 + 8) * 60);
    assert_eq!(lv[0].fresh.len(), lv[0].segs.len());
}

/// Мир `level_world` (уровень 100,025 известен с `BORN`, шаг цены 0,05): минуты `BORN + 10…` — закрытие 99,8, high 99,8 (в полосе подхода, не касание: полоса касания — от 99,975);
/// в минуте `touch_at` — high 100,0 (касание: не ниже уровень − шаг), закрытие 99,8.
fn approach_world(touch_at: Option<usize>) -> Cols {
    let mut c = level_world();
    for m in BORN + 10..BORN + 200 {
        set(&mut c, m, 99.8, 99.8, 99.8, 99.8);
    }
    if let Some(m) = touch_at {
        set(&mut c, m, 99.8, 100.0, 99.8, 99.8);
    }
    c
}

fn cand_minutes(c: Cols, p: &Params) -> Vec<usize> {
    let s = sym(c);
    let lv = build_levels(&s, &p.sig.lv, Side::Long);
    let mut cs = Vec::new();
    candidates(&s, &lv, &p.sig, Side::Long, &mut cs);
    cs.iter().map(|x| x.t as usize).collect()
}

#[test]
fn fw_candidates_start_after_the_wait_and_only_after_a_touch() {
    let off = cand_minutes(approach_world(Some(BORN + 12)), &with_keys(&[]));
    assert_eq!(
        off.first(),
        Some(&(BORN + 10)),
        "без fw — с первой минуты в зоне"
    );
    let fw = with_keys(&[("fw", "30"), ("fm", "50"), ("fk", "60")]);
    let on = cand_minutes(approach_world(Some(BORN + 12)), &fw);
    // постановка = рождение (BORN); окно [t − 60, t): в полосе минуты с BORN + 10, доля 50 % — 30 минут в полосе: t = BORN + 40; касание — в BORN + 12 (в последних 60 минутах)
    assert_eq!(
        on.first(),
        Some(&(BORN + 40)),
        "30 минут проторговки в окне и 30 минут после постановки"
    );
    assert!(on.iter().all(|t| off.contains(t)), "подмножество прежних");
    // касания нет — кандидатов нет совсем; касание позже — кандидаты только после него
    assert!(cand_minutes(approach_world(None), &fw).is_empty());
    // касание старше fk минут допуск не даёт: после BORN + 72 кандидатов нет
    assert!(on.iter().all(|&t| t <= BORN + 72), "{on:?}");
    let late = cand_minutes(approach_world(Some(BORN + 60)), &fw);
    assert_eq!(
        late.first(),
        Some(&(BORN + 61)),
        "касание в минуте 60 учитывается со следующей"
    );
}

#[test]
fn owner_keys_validate_parse_and_enter_the_cache_key_only_when_on() {
    let bad = |line: &str| parse_variant(line).is_err();
    assert!(bad("x lm=2") && bad("x lz=1 n_bounce=3") && bad("x lm=1 lx=1"));
    assert!(bad("x fw=30"), "fw без fm");
    assert!(bad("x fw=30 fm=50 fh=4"), "fw при включённом (а)");
    assert!(bad("x fw=200 fm=50 fk=120"), "fw > fk");
    assert!(bad("x fz=4"), "fz упразднён (В-197)");
    assert!(bad("x pp=1 pk=1"), "pp заменяет pk");
    assert!(bad("x li=1"), "li требует lm = 1");
    assert!(bad("x lm=1 pk=1"), "lm = 1 несовместим с pk");
    let (_, p) = parse_variant("x lm=1 lz=1 pp=1 fw=30 fm=50").expect("разбор");
    assert_eq!(
        (p.sig.lv.lm, p.sig.lv.lz, p.sig.lv.pp, p.sig.fw_min),
        (1, true, true, 30)
    );
    // выключены — текст ключа и отпечаток прежние
    let base = Params::base().sig;
    let t0 = cfg_key_text(&base);
    assert!(
        !t0.contains("lm=") && !t0.contains("fw_min") && !t0.contains("lz=") && !t0.contains("pp=")
    );
    let dbg = format!("{:?}", Params::base());
    assert!(
        !dbg.contains("lm:")
            && !dbg.contains("lz:")
            && !dbg.contains("fw_min")
            && !dbg.contains("pp:")
    );
    let with = |f: &dyn Fn(&mut SigCfg)| {
        let mut c = base.clone();
        f(&mut c);
        cfg_key_text(&c)
    };
    let keys = [
        t0,
        with(&|c| c.lv.lm = 1),
        with(&|c| c.lv.lz = true),
        with(&|c| (c.lv.lm, c.lv.lz) = (1, true)),
        with(&|c| c.lv.pp = true),
        with(&|c| c.fw_min = 30),
        with(&|c| c.fw_min = 31),
    ];
    let distinct: std::collections::HashSet<&String> = keys.iter().collect();
    assert_eq!(distinct.len(), keys.len(), "{keys:?}");
}

// ───────────────────────── pp: любой минутный прокол, допуск 1 шаг ≤ 0,05 % ─────────────────────────

#[test]
fn tick_is_the_gcd_of_prices_and_the_tolerance_is_one_tick_up_to_five_bp() {
    use crate::breakout::levels::pierce_tol;
    // цены мира `level_world` кратны 0,05
    assert_eq!(sym(level_world()).tick(), px(0.05));
    // допуск: шаг ≤ 0,05 % цены — один шаг, иначе 0
    assert_eq!(pierce_tol(px(0.05), px(100.0)), px(0.05));
    assert_eq!(
        pierce_tol(px(0.06), px(100.0)),
        0,
        "шаг 0,06 % — допуска нет"
    );
    assert_eq!(pierce_tol(px(0.01), px(100.0)), px(0.01));
    assert_eq!(pierce_tol(0, px(100.0)), 0, "шаг неизвестен — допуска нет");
}

#[test]
fn pp_any_minute_pierce_beyond_one_tick_kills_the_level_for_the_next_minutes() {
    let p = with_keys(&[("lm", "1"), ("pp", "1"), ("ban", "0")]);
    // уровень 100,00 (ближний пивот), шаг 0,05 (0,05 % цены — допуск 1 шаг): high 100,05 — в допуске, high 100,10 — прокол
    let spike = |hi: f64| {
        let mut c = approach_world(None);
        set(&mut c, BORN + 50, 99.8, hi, 99.8, 99.8);
        c
    };
    let within = cand_minutes(spike(100.05), &p);
    assert!(
        within.contains(&(BORN + 51)),
        "заход на 1 шаг уровень не убивает"
    );
    let pierced = cand_minutes(spike(100.10), &p);
    assert!(
        pierced.iter().all(|&t| t <= BORN + 50),
        "заход на 2 шага убивает со следующей минуты: {pierced:?}"
    );
    assert!(pierced.contains(&(BORN + 49)));
}

// ───────────────────────── li: целостность сетапа вместо старения пивотов ─────────────────────────

#[test]
fn li_chain_rule_ends_the_setup_at_the_last_confirmation_plus_l_days() {
    // l = 1 сутки: с `li` сетап отменяется на «постановка + 1440», без `li` — когда старший пивот выпадает по окну от своего бара
    let lv = |keys: &[(&str, &str)]| {
        let mut k = vec![("l_days", "1"), ("lm", "1")];
        k.extend_from_slice(keys);
        levels_of(level_world(), &with_keys(&k), Side::Long)
    };
    let li = lv(&[("li", "1")]);
    assert_eq!((li[0].end as usize, li[0].why), (BORN + 1440, 1));
    let old = lv(&[]);
    assert!((old[0].end as usize) < BORN + 1440 && old[0].why == 1);
}

#[test]
fn lm_main_level_pierced_after_the_setup_ends_it_at_the_hour_close() {
    // прокол основного (100,00; допуск 0,05) минутой BORN + 100 (high 100,20): уровня нет с закрытия часа BORN + 120
    let mut c = level_world();
    set(&mut c, BORN + 100, 90.0, 100.2, 90.0, 90.0);
    let lv = levels_of(c, &with_keys(&[("lm", "1"), ("li", "1")]), Side::Long);
    assert_eq!((lv[0].end as usize, lv[0].why), (BORN + 120, 2));
}

#[test]
fn pivot_minutes_give_extremum_and_confirmation_minutes_for_the_setup_print() {
    use crate::breakout::levels::pivot_minutes;
    let p = with_keys(&[]);
    let s = sym(level_world());
    let hb = HB as u32;
    let m = pivot_minutes(&s, &p.sig.lv, Side::Long);
    // пивоты баров 1 и 5: экстремум — первая минута бара; подтверждение — закрытие бара `max(h + k, бар глубины)` (оба — бар h + 1)
    assert_eq!(m.len(), 2);
    assert_eq!(
        m[&(hb + 1, px(100.0))],
        ((hb + 1) * 60, (hb + 3) * 60),
        "пивот бара 1"
    );
    assert_eq!(
        m[&(hb + 5, px(100.05))],
        ((hb + 5) * 60, (hb + 7) * 60),
        "подтверждение второго пивота = рождение уровня"
    );
    // шорт: зеркало — те же минуты
    let sm = sym(mirror(&level_world(), 200.0));
    let ms = pivot_minutes(&sm, &p.sig.lv, Side::Short);
    assert_eq!(ms[&(hb + 1, px(100.0))].1, (hb + 3) * 60);
}
