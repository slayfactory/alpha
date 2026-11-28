//! Тесты TK-747 (В-194, В-195, В-196): ключи `lm` (уровень без усреднения), `lz` (закол двух пивотов), `fw` / `fz` (свежесть по владельцу). Ключи выключены — уровни и кандидаты прежние.

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
    assert_eq!(on[0].segs[0].1, px(100.05), "лонг: самый высокий максимум");
    // дальний край зоны — противоположный крайний пивот, у каждого члена своя цена
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
    assert_eq!(on[0].segs[0].1, px(99.95), "шорт: самый низкий минимум");
    assert_eq!(
        on[0].far_edge(0, -1),
        Some(-px(100.0)),
        "P_far — самый высокий минимум"
    );
    // допуск τ = 0,2 % мерится от крайнего пивота, не от среднего: пивоты 100,00 и 100,30 — от среднего 100,15 по 0,15 % (группа есть), от крайнего 100,30 — 0,299 % (группы нет)
    let wide = || pivots_world(100.0, 100.30, None);
    assert_eq!(levels_of(wide(), &with_keys(&[]), Side::Long).len(), 1);
    assert!(levels_of(wide(), &p, Side::Long).is_empty());
    assert!(levels_of(mirror(&wide(), 200.0), &p, Side::Short).is_empty());
    // пивоты 100,00 и 100,15: от крайнего 0,15 % — группа, цена уровня — 100,15
    let near = levels_of(pivots_world(100.0, 100.15, None), &p, Side::Long);
    assert_eq!(near.len(), 1);
    assert_eq!(near[0].segs[0].1, px(100.15));
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
    // лонг: второй максимум (100,05) выше первого (100,00) — «перебил»: уровня нет; без ключа — есть
    assert_eq!(
        levels_of(pivots_world(100.0, 100.05, None), &off, Side::Long).len(),
        1
    );
    assert!(levels_of(pivots_world(100.0, 100.05, None), &p, Side::Long).is_empty());
    // второй не выше первого (99,95 и равный 100,00) — уровень рождается
    for second in [99.95, 100.0] {
        let lv = levels_of(pivots_world(100.0, second, None), &p, Side::Long);
        assert_eq!(lv.len(), 1, "второй пивот {second}");
    }
    // шорт: зеркало — второй минимум ниже первого — отмена, выше — уровень
    let mc = |a, b| mirror(&pivots_world(a, b, None), 200.0);
    assert!(levels_of(mc(100.0, 100.05), &p, Side::Short).is_empty());
    assert_eq!(levels_of(mc(100.0, 99.95), &p, Side::Short).len(), 1);
    assert_eq!(levels_of(mc(100.0, 100.05), &off, Side::Short).len(), 1);
}

#[test]
fn lz_cascade_of_three_pivots_is_not_cancelled() {
    // пивоты 100,00, 100,05 (перебил) и 100,02: пара {1, 2} отменена, третий пивот даёт каскад из трёх — уровень рождается на нём
    let c = pivots_world(100.0, 100.05, Some(100.02));
    let off = levels_of(c.clone(), &with_keys(&[]), Side::Long);
    assert_eq!(
        off[0].born as usize,
        (HB + 6) * 60,
        "без lz — на втором пивоте"
    );
    assert_eq!(off[0].segs.len(), 2, "третий присоединился");
    let on = levels_of(c, &with_keys(&[("lz", "1")]), Side::Long);
    assert_eq!(on.len(), 1);
    assert_eq!(on[0].born as usize, (HB + 8) * 60, "на третьем пивоте");
    assert_eq!(on[0].mem[0].len(), 3);
}

// ───────────────────────── fw, fz: свежесть по владельцу ─────────────────────────

fn wcfg(fw: usize, k: usize, share: usize, fz: usize) -> SigCfg {
    let mut c = fcfg(0, k, share, 500);
    c.fw_min = fw;
    c.fz_min = fz;
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
    let s = closes(SET, SET + 200, 100.7); // 0,297 % ниже уровня — в полосе 0,5 %
    let cfg = wcfg(30, 120, 50, 0);
    let ok = |t: usize, touched: bool| fresh_fw_ok(&s, &cfg, (SET, 0), 1, lp, touched, t);
    assert!(!ok(SET + 29, true), "29 минут после постановки — рано");
    assert!(ok(SET + 30, true), "30 минут и проторговка — можно");
    assert!(!ok(SET + 30, false), "касания нет — нельзя");
    assert!(!ok(SET, true) && !ok(SET - 1, true));
}

#[test]
fn fw_consolidation_share_counts_from_the_set_minute_not_before() {
    let lp = px(101.0);
    let cfg = wcfg(30, 120, 50, 0);
    let t = SET + 40; // окно [SET, t): 40 минут, доля 50 % — 20 минут в полосе
    let in_band = |n: usize| closes(SET, SET + n, 100.7);
    assert!(fresh_fw_ok(&in_band(20), &cfg, (SET, 0), 1, lp, true, t));
    assert!(!fresh_fw_ok(&in_band(19), &cfg, (SET, 0), 1, lp, true, t));
    // закрытие на уровне и за ним в окне — отказ; до постановки — не мешает
    let beyond = |m: usize| {
        fresh_cols(move |c| {
            for x in SET..t {
                set(c, x, 100.7, 100.7, 100.7, 100.7);
            }
            set(c, m, 100.9, 101.2, 100.9, 101.0);
        })
    };
    assert!(!fresh_fw_ok(
        &beyond(SET + 5),
        &cfg,
        (SET, 0),
        1,
        lp,
        true,
        t
    ));
    assert!(fresh_fw_ok(
        &beyond(SET - 5),
        &cfg,
        (SET, 0),
        1,
        lp,
        true,
        t
    ));
    // старый уровень: окно — последние fk = 120 минут, закрытие за уровнем до окна не мешает
    let old = fresh_cols(|c| {
        for x in SET + 880..SET + 1000 {
            set(c, x, 100.7, 100.7, 100.7, 100.7);
        }
        set(c, SET + 100, 100.9, 101.2, 100.9, 101.0);
    });
    assert!(fresh_fw_ok(&old, &cfg, (SET, 0), 1, lp, true, SET + 1000));
    // шорт: рама с обратным знаком — уровень −99,0, закрытия 99,3 (в раме ниже уровня)
    let sh = fresh_cols(|c| {
        for x in SET..SET + 100 {
            set(c, x, 99.3, 99.3, 99.3, 99.3);
        }
    });
    assert!(fresh_fw_ok(
        &sh,
        &cfg,
        (SET, 0),
        -1,
        px(-99.0),
        true,
        SET + 40
    ));
}

#[test]
fn fz_requires_a_long_gap_between_the_setting_pivot_and_the_older_ones() {
    let lp = px(101.0);
    let s = closes(SET, SET + 200, 100.7);
    let cfg = wcfg(30, 120, 50, 240); // 4 ч
    let ok = |gap: usize| fresh_fw_ok(&s, &cfg, (SET, gap), 1, lp, true, SET + 60);
    assert!(!ok(239) && ok(240) && ok(1000));
    // fz = 0 — разрыв не проверяется
    let cfg0 = wcfg(30, 120, 50, 0);
    assert!(fresh_fw_ok(&s, &cfg0, (SET, 0), 1, lp, true, SET + 60));
}

#[test]
fn fresh_of_set_and_gap_follow_the_last_joined_pivot() {
    // пивоты 100,00 (бар 1, активен на 2), 100,05 (бар 4, активен на 5): постановка — подтверждение второго, разрыв — 3 бара
    let lv = levels_of(
        pivots_world(100.0, 100.05, None),
        &with_keys(&[]),
        Side::Long,
    );
    assert_eq!(lv[0].fresh, vec![((HB as u32 + 6) * 60, 3 * 60)]);
    // третий пивот (бар 6, активен на 7): версия 2 — постановка на нём, разрыв — до ближайшего (бар 4) — 2 бара
    let lv = levels_of(
        pivots_world(100.0, 100.05, Some(100.02)),
        &with_keys(&[]),
        Side::Long,
    );
    assert_eq!(lv[0].fresh[1], ((HB as u32 + 8) * 60, 2 * 60));
    assert_eq!(lv[0].fresh.len(), lv[0].segs.len());
}

/// Мир `level_world` (уровень 100,025 известен с `BORN`): минуты `BORN + 10…` — закрытие 99,8, high 99,8 (в полосе подхода, не касание: полоса касания — от 99,825);
/// в минуте `touch_at` — high 99,9 (касание), закрытие 99,8.
fn approach_world(touch_at: Option<usize>) -> Cols {
    let mut c = level_world();
    for m in BORN + 10..BORN + 200 {
        set(&mut c, m, 99.8, 99.8, 99.8, 99.8);
    }
    if let Some(m) = touch_at {
        set(&mut c, m, 99.8, 99.9, 99.8, 99.8);
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
    let fw = with_keys(&[("fw", "30"), ("fm", "50")]);
    let on = cand_minutes(approach_world(Some(BORN + 12)), &fw);
    // постановка = рождение (BORN); окно [BORN, t) — 30 минут: 20 в полосе из 30 — доля 66 % ≥ 50 %; касание — в BORN + 12
    assert_eq!(
        on.first(),
        Some(&(BORN + 30)),
        "через 30 минут после постановки"
    );
    assert!(on.iter().all(|t| off.contains(t)), "подмножество прежних");
    // касания нет — кандидатов нет совсем; касание позже — кандидаты только после него
    assert!(cand_minutes(approach_world(None), &fw).is_empty());
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
    assert!(bad("x fz=4"), "fz без fw");
    let (_, p) = parse_variant("x lm=1 lz=1 fw=30 fm=50 fz=4").expect("разбор");
    assert_eq!(
        (p.sig.lv.lm, p.sig.lv.lz, p.sig.fw_min, p.sig.fz_min),
        (1, true, 30, 240)
    );
    // выключены — текст ключа и отпечаток прежние
    let base = Params::base().sig;
    let t0 = cfg_key_text(&base);
    assert!(!t0.contains("lm=") && !t0.contains("fw_min") && !t0.contains("lz="));
    let dbg = format!("{:?}", Params::base());
    assert!(!dbg.contains("lm:") && !dbg.contains("lz:") && !dbg.contains("fw_min"));
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
        with(&|c| c.fw_min = 30),
        with(&|c| (c.fw_min, c.fz_min) = (30, 240)),
        with(&|c| (c.fw_min, c.fz_min) = (30, 241)),
    ];
    let distinct: std::collections::HashSet<&String> = keys.iter().collect();
    assert_eq!(distinct.len(), keys.len(), "{keys:?}");
}
