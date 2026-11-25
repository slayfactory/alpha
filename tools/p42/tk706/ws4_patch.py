#!/usr/bin/env python3
# TK-706 (пучок, H2 критика): опыт «пучок против отдельных ходов» поверх мерного дерева ws3 и рефакторинга `Walk` (src-walk): `sim_bundle` — один обход ленты на группу, члены — `Walk`, выбывшие снимаются.
# Что делает опыт (в `b3_report`, после фазы 2, ЦП потока, порядок хода чередуется по номеру группы, разогрев суток — один неучтённый проход):
#   отдельно — отдельные ходы запрошенных ключей группы (как `sim_fill_mem::compute`: свой курсор, `sim_fill_lc_s`);
#   пучок    — один обход на кандидатов ∪ спрос (в бою: промах хода K считает все ещё не посчитанные ключи группы K);
#   равенство — `TickOut` каждого члена пучка = отдельный ход того же ключа (спрос — из замеренных, остальные — неучтённым отдельным ходом); расхождений должно быть 0.
# Итог — "bundle" в JSON `b3` (`P42_B3_OUT`). Запуск на srv: cp -a src-walk src-ws4; WS_DIR=/root/perf706/src-ws4 python3 ws3_patch.py; WS_DIR=/root/perf706/src-ws4 python3 ws4_patch.py
import os

D = os.environ.get('WS_DIR', '/root/perf706/src-ws4') + '/src/breakout/'


def rep(s, old, new, cnt=1):
    assert s.count(old) == cnt, (old[:60], s.count(old))
    return s.replace(old, new)


t = open(D + 'tick.rs', 'rb').read().decode('utf-8').replace('\r\n', '\n')

BLOCK = r'''// ---- TK-706 пучок (опыт H2): `sim_bundle` и сравнение с отдельными ходами ----
/// Несколько ходов одного входа (одинаковые все аргументы, кроме `ExitCfg`) за один обход ленты: тик берётся и фильтруется один раз, затем каждому
/// не выбывшему члену — `Walk::step`; вернувшие `true` снимаются; итог — по порядку `exs`.
#[allow(clippy::too_many_arguments)]
fn sim_bundle(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    exs: &[&ExitCfg],
    lc: Option<LevelCtx>,
    notional: i64,
    entry_slip_bps: i64,
    stop_px: Option<i64>,
) -> Vec<TickOut> {
    let mut ws: Vec<Walk> = exs
        .iter()
        .map(|ex| {
            Walk::new(
                entry_ms,
                p0,
                side,
                natr_e8,
                ex,
                lc,
                notional,
                entry_slip_bps,
                stop_px,
            )
        })
        .collect();
    let mut live: Vec<usize> = (0..ws.len()).collect();
    let mut after = || -> Option<(i64, i64, bool)> {
        loop {
            let t = next()?;
            if t.0 > entry_ms {
                return Some(t);
            }
        }
    };
    while !live.is_empty() {
        let Some((ms, px, _)) = after() else {
            break;
        };
        let mut i = 0;
        while i < live.len() {
            if ws[live[i]].step(ms, px) {
                live.swap_remove(i);
            } else {
                i += 1;
            }
        }
    }
    ws.into_iter().map(Walk::finish).collect()
}

fn b4_cursor<'a>(src: &'a dyn TradeSrc, g: &'a B3Group) -> Cursor<'a> {
    let (coin, entry_ms, ..) = g;
    let day = ((*entry_ms).max(0) / DAY_MS) as usize;
    let base = day as i64 * DAY_MS;
    let idx = src.day(coin, day).map_or(0, |v| {
        v.partition_point(|x| base + i64::from(x.off()) <= *entry_ms)
    });
    Cursor::new(src, coin, day, idx)
}

/// Отдельный ход ключа группы — как `sim_fill_mem::compute`.
fn b4_single(src: &dyn TradeSrc, g: &B3Group, ex: &ExitCfg) -> TickOut {
    let (_, entry_ms, p0, side_s, natr, lc, notional, slip, stop_px) = g;
    let side = if *side_s > 0 { Side::Long } else { Side::Short };
    let mut cur = b4_cursor(src, g);
    sim_fill_lc_s(
        &mut || cur.next_tick(),
        *entry_ms,
        *p0,
        side,
        *natr,
        ex,
        *lc,
        *notional,
        *slip,
        *stop_px,
    )
}

fn b4_bundle(src: &dyn TradeSrc, g: &B3Group, exs: &[&ExitCfg]) -> Vec<TickOut> {
    let (_, entry_ms, p0, side_s, natr, lc, notional, slip, stop_px) = g;
    let side = if *side_s > 0 { Side::Long } else { Side::Short };
    let mut cur = b4_cursor(src, g);
    sim_bundle(
        &mut || cur.next_tick(),
        *entry_ms,
        *p0,
        side,
        *natr,
        exs,
        *lc,
        *notional,
        *slip,
        *stop_px,
    )
}

/// Опыт: на каждой группе со спросом — отдельные ходы спроса против одного пучка (кандидаты ∪ спрос), ЦП потока, равенство всех членов.
fn b4_experiment(
    src: &dyn TradeSrc,
    groups: &[(&B3Group, &B3Info)],
    exs: &[ExitCfg],
    threads: usize,
) -> serde_json::Value {
    use std::sync::atomic::AtomicUsize;
    let sample: usize = std::env::var("P42_B4_SAMPLE")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1)
        .max(1);
    let picked: Vec<usize> = (0..groups.len())
        .filter(|&i| !groups[i].1.dem.is_empty())
        .step_by(sample)
        .collect();
    let next = AtomicUsize::new(0);
    // по бакетам размера спроса группы: 1, 2, 3–4, 5–8, 9+ ; [групп, членов пучка, членов спроса, ЦП отдельно (нс), ЦП пучка (нс)]
    let bk = |n: usize| match n {
        0..=1 => 0,
        2 => 1,
        3..=4 => 2,
        5..=8 => 3,
        _ => 4,
    };
    let acc: Vec<[AtomicU64; 5]> = (0..5).map(|_| Default::default()).collect();
    let (mism, checked, done) = (AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0));
    let (sep_first, bun_first) = (AtomicU64::new(0), AtomicU64::new(0));
    std::thread::scope(|s| {
        for _ in 0..threads.max(1) {
            s.spawn(|| loop {
                let k = next.fetch_add(1, Ordering::Relaxed);
                if k >= picked.len() {
                    break;
                }
                let (g, info) = groups[picked[k]];
                let dem: Vec<u32> = info.dem.iter().map(|d| d.0).collect();
                let mut all: Vec<u32> = info.cand.clone();
                for &d in &dem {
                    if !all.contains(&d) {
                        all.push(d);
                    }
                }
                let dem_refs: Vec<&ExitCfg> = dem.iter().map(|&i| &exs[i as usize]).collect();
                let all_refs: Vec<&ExitCfg> = all.iter().map(|&i| &exs[i as usize]).collect();
                // разогрев (суток и кода) — неучтённый проход пучка
                let _ = b4_bundle(src, g, &all_refs);
                let timed = |f: &dyn Fn() -> Vec<TickOut>| -> (u64, Vec<TickOut>) {
                    let (c0, l0) = (b3_cpu_ns(), B3_LOAD.with(|c| c.get()));
                    let o = f();
                    let net = (b3_cpu_ns() - c0).saturating_sub(B3_LOAD.with(|c| c.get()) - l0);
                    (net, o)
                };
                let sep = || -> Vec<TickOut> { dem_refs.iter().map(|ex| b4_single(src, g, ex)).collect() };
                let bun = || -> Vec<TickOut> { b4_bundle(src, g, &all_refs) };
                let ((t_sep, o_sep), (t_bun, o_bun)) = if k % 2 == 0 {
                    sep_first.fetch_add(1, Ordering::Relaxed);
                    let a = timed(&sep);
                    let b = timed(&bun);
                    (a, b)
                } else {
                    bun_first.fetch_add(1, Ordering::Relaxed);
                    let b = timed(&bun);
                    let a = timed(&sep);
                    (a, b)
                };
                // равенство: спрос — против замеренных отдельных ходов, остальные члены — против неучтённого отдельного хода
                let mut bad = 0u64;
                for (mi, id) in all.iter().enumerate() {
                    let single = match dem.iter().position(|d| d == id) {
                        Some(di) => o_sep[di].clone(),
                        None => b4_single(src, g, &exs[*id as usize]),
                    };
                    if single != o_bun[mi] {
                        bad += 1;
                    }
                }
                mism.fetch_add(bad, Ordering::Relaxed);
                checked.fetch_add(all.len() as u64, Ordering::Relaxed);
                done.fetch_add(1, Ordering::Relaxed);
                let a = &acc[bk(dem.len())];
                a[0].fetch_add(1, Ordering::Relaxed);
                a[1].fetch_add(all.len() as u64, Ordering::Relaxed);
                a[2].fetch_add(dem.len() as u64, Ordering::Relaxed);
                a[3].fetch_add(t_sep, Ordering::Relaxed);
                a[4].fetch_add(t_bun, Ordering::Relaxed);
            });
        }
    });
    let g = |a: &AtomicU64| a.load(Ordering::Relaxed);
    let (mut sep_ns, mut bun_ns) = (0u64, 0u64);
    for a in &acc {
        sep_ns += g(&a[3]);
        bun_ns += g(&a[4]);
    }
    let s = sample as f64;
    serde_json::json!({
        "sample": sample, "groups_picked": picked.len(), "groups_done": g(&done), "members_checked": g(&checked), "mismatches": g(&mism),
        "sep_first_groups": g(&sep_first), "bun_first_groups": g(&bun_first),
        "sep_cpu_s": sep_ns as f64 / 1e9, "bun_cpu_s": bun_ns as f64 / 1e9, "gain_cpu_s": (sep_ns as f64 - bun_ns as f64) / 1e9,
        "gain_cpu_s_scaled": (sep_ns as f64 - bun_ns as f64) / 1e9 * s,
        "by_dem_size_[1,2,3-4,5-8,9+]": acc.iter().map(|a| serde_json::json!({
            "groups": g(&a[0]), "members_bundle": g(&a[1]), "members_dem": g(&a[2]), "sep_cpu_s": g(&a[3]) as f64 / 1e9, "bun_cpu_s": g(&a[4]) as f64 / 1e9})).collect::<Vec<_>>(),
    })
}

'''

anchor = '/// Итог после фазы 2: статистика групп'
t = rep(t, anchor, BLOCK + anchor)

old = '    let s = |a: &AtomicU64| a.load(Ordering::Relaxed);\n    let ns_s = |n: u64| n as f64 / 1e9;\n'
new = '    let b4 = b4_experiment(src, &groups, &st.exs, threads);\n' + old
t = rep(t, old, new)

old = '        "hist_cand_per_demgroup": chist,\n'
new = '        "hist_cand_per_demgroup": chist, "bundle": b4,\n'
t = rep(t, old, new)

open(D + 'tick.rs', 'wb').write(t.encode('utf-8'))
print('patched ws4')
