//! `alpha breakout tick` (П-42 шаг B, §13.2): варианты из файла → сделки по Binance aggTrades. Файлы результата — как
//! у `breakout run` (`<out>/<id>/<side>-{trades,daily,coinday}.csv`, `meta.json`, `<out>/summary.csv`); в
//! `trades.csv` в конце добавлены `sig_ms,entry_ms,exit_ms` (мс от 01.09 00:00 UTC).

use super::data::{self, Sym, HALF_DAYS, MIN_MS, SCALE, START_DAY};
use super::ftab::{filter_sigs, FeatTab};
use super::levels::build_levels_tracked;
use super::memo::MoveMemo;
use super::orders::{write_orders_rows, SetupCtx, ORDERS_HEADER, SETUP_HEADER};
use super::params::ExitCfg;
use super::params::{parse_variant, Params, SigCfg};
use super::sigcache::{SigCache, SigCacheOpts};
use super::store::{StoreOpts, TickStore};
use super::tick::{
    cons_range, exit_natr, old_entry, run_tick_xb, signals_for_coin, sort_tsigs, tape_last_side,
    Cursor, EntryRej, TSig, TickCtr, TickResult, TradeSrc, ZipSrc, DAY_MS,
};
use super::{par_map, par_map_wide, Side};
use anyhow::{bail, Context, Result};
use std::collections::HashMap;
use std::fmt::Write as _;
use std::fs;
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

/// TK-683: сколько вариантов вызова проходится по монетам для разогрева суток сделок перед фазой 2.
const WARM_VARIANTS: usize = 16;

fn mean(v: &[f64]) -> f64 {
    if v.is_empty() {
        0.0
    } else {
        v.iter().sum::<f64>() / v.len() as f64
    }
}

fn write_outputs(dir: &Path, syms: &[Sym], side: Side, r: &TickResult) -> Result<()> {
    let mut t = String::from(
        "sym,level,sig_min,entry_min,exit_min,entry_px_e8,kind,takes,gross,fees,funding,net,entry_day,sig_ms,entry_ms,exit_ms\n",
    );
    for x in &r.trades {
        let _ = writeln!(
            t,
            "{},{},{},{},{},{},{},{},{:.6},{:.6},{:.6},{:.6},{},{},{},{}",
            syms[x.sym as usize].name,
            x.level,
            x.sig_min,
            x.entry_ms / MIN_MS,
            x.exit_ms / MIN_MS,
            x.entry_px,
            x.kind.name(),
            x.takes,
            x.gross,
            x.fees,
            x.funding,
            x.net,
            x.entry_ms / DAY_MS - START_DAY as i64,
            x.sig_ms,
            x.entry_ms,
            x.exit_ms
        );
    }
    fs::write(dir.join(format!("{}-trades.csv", side.name())), t)?;
    // TK-677: лоты сделок при включённом втором входе — строки в порядке trades.csv; `lot2 = 0` — вошёл только лот 1
    if r.trades.iter().any(|x| x.net1.is_some()) {
        let mut l = String::from(
            "sym,level,sig_ms,lot2,kind1,kind2,net1,net2,entry2_ms,entry2_px_e8
",
        );
        for x in &r.trades {
            let n1 = x.net1.unwrap_or(x.net);
            let (k2, ms2, px2) = x
                .lot2
                .as_ref()
                .map_or(("", 0, 0), |t2| (t2.kind.name(), t2.entry_ms, t2.entry_px));
            let _ = writeln!(
                l,
                "{},{},{},{},{},{},{:.6},{:.6},{},{}",
                syms[x.sym as usize].name,
                x.level,
                x.sig_ms,
                u8::from(x.lot2.is_some()),
                x.kind1.name(),
                k2,
                n1,
                x.net - n1,
                ms2,
                px2
            );
        }
        fs::write(dir.join(format!("{}-lots.csv", side.name())), l)?;
    }
    let mut d = String::from("day,pnl\n");
    for (i, v) in r.daily.iter().enumerate() {
        let _ = writeln!(d, "{i},{v:.6}");
    }
    fs::write(dir.join(format!("{}-daily.csv", side.name())), d)?;
    let mut cd = String::from("sym,day,pnl\n");
    for ((si, day), v) in &r.coinday {
        let _ = writeln!(cd, "{},{day},{v:.6}", syms[*si as usize].name);
    }
    fs::write(dir.join(format!("{}-coinday.csv", side.name())), cd)?;
    Ok(())
}

/// TK-743 (`--keep-orders`): все заявки сделок — `<side>-orders.csv` (формат — `docs/COMMANDS.md`, «Журнал заявок»); строки сделок в порядке `trades.csv`.
fn write_orders(dir: &Path, syms: &[Sym], side: Side, r: &TickResult) -> Result<()> {
    let mut t = String::from(ORDERS_HEADER);
    for x in &r.trades {
        write_orders_rows(&mut t, syms, x);
    }
    fs::write(dir.join(format!("{}-orders.csv", side.name())), t)?;
    Ok(())
}

/// TK-743 (`--keep-orders`): уровни сетапа каждой сделки — `<side>-setup.csv`.
fn write_setup(
    dir: &Path,
    syms: &[Sym],
    side: Side,
    p: &Params,
    sigs: &[TSig],
    r: &TickResult,
) -> Result<()> {
    let mut ctx = SetupCtx::new(syms, side, p, sigs);
    let mut t = String::from(SETUP_HEADER);
    for i in 0..r.trades.len() {
        ctx.rows(&mut t, &r.trades, i);
    }
    fs::write(dir.join(format!("{}-setup.csv", side.name())), t)?;
    Ok(())
}

/// §13.16: отвергнутые входом по ленте сигналы — `<side>-entryrej.csv` (только при `ew > 0`).
fn write_entry_rej(dir: &Path, syms: &[Sym], side: Side, r: &TickResult) -> Result<()> {
    let mut t = String::from("sym,level,sig_ms,why\n");
    for (si, level, ms, why) in &r.entry_rej {
        let w = match why {
            EntryRej::Beyond => "beyond",
            EntryRej::Stale => "stale",
            EntryRej::Unfilled => "unfilled",
            EntryRej::Touched => "touched",
            EntryRej::NoTick => "notick",
            EntryRej::NoRoom => "noroom",
        };
        let _ = writeln!(t, "{},{level},{ms},{w}", syms[*si as usize].name);
    }
    fs::write(dir.join(format!("{}-entryrej.csv", side.name())), t)?;
    Ok(())
}

/// TK-676 (`vf > 0`): расстояние стопа от волатильности по сделкам — `<side>-vstop.csv`: `vd_pm` — тысячные процента цены входа (−1 — нет значения источника,
/// остался жёсткий `s`), `natr_e8` — NATR выхода на сигнале (для отношения стоп / тейк).
fn write_vstop(dir: &Path, syms: &[Sym], side: Side, r: &TickResult) -> Result<()> {
    let mut t = String::from("sym,level,sig_ms,vd_pm,natr_e8\n");
    for x in &r.trades {
        let _ = writeln!(
            t,
            "{},{},{},{},{}",
            syms[x.sym as usize].name, x.level, x.sig_ms, x.vd_pm, x.natr_e8
        );
    }
    fs::write(dir.join(format!("{}-vstop.csv", side.name())), t)?;
    Ok(())
}

/// §13.28 (`mk > 0`, TK-688 `e1t != 0`): счётчики заявок мейкера — `<side>-maker.json`: лот 1 (заполнено, не заполнено, снято уровнем, заявок = сумма), число отказов
/// `notick` и лот 2 (`e2`: сразу, позже, отменён, отказ — как `lot2_[…]` в `meta.json`).
fn write_maker(dir: &Path, side: Side, r: &TickResult) -> Result<()> {
    let n_of = |w: EntryRej| r.entry_rej.iter().filter(|x| x.3 == w).count();
    let [filled, unfilled, touched] = r.mk;
    let rec = serde_json::json!({
        "orders": filled + unfilled + touched,
        "filled": filled, "unfilled": unfilled, "touched": touched,
        "notick": n_of(EntryRej::NoTick),
        "lot2_[now,later,cancelled,rejected]": r.lot2,
    });
    fs::write(
        dir.join(format!("{}-maker.json", side.name())),
        serde_json::to_string_pretty(&rec)?,
    )?;
    Ok(())
}

/// Окно возраста для справочной колонки заменителя в `P42_SIGS`, мс (верхняя граница сетки калибровки §13.16).
const SIGS_CAP_MS: i64 = 5000;

/// `P42_SIGS=<сутки от>:<сутки до>` (сутки от 01.09): по каждому сигналу задания — `<side>-sigs.csv` для калибровки §13.16: монета,
/// уровень, время сигнала, цена уровня, цена и возраст последней сделки стороны ≤ `сигнал + lat` (окно 5 с), вход прежней модели.
fn write_sigs(
    dir: &Path,
    syms: &[Sym],
    side: Side,
    sigs: &[TSig],
    ex: &ExitCfg,
    src: &dyn TradeSrc,
    days: (usize, usize),
) -> Result<()> {
    let mut t = String::from(
        "sym,level,sig_ms,level_px_e8,proxy_px_e8,proxy_age_ms,old_entry_ms,old_entry_px_e8\n",
    );
    for sg in sigs {
        let d = usize::from(sg.day);
        if d < days.0 || d > days.1 {
            continue;
        }
        let sym = &syms[sg.sym as usize];
        let proxy = src.day(&sym.name, d).and_then(|ticks| {
            tape_last_side(
                src,
                &sym.name,
                d,
                &ticks,
                sg.ms + ex.lat_ms,
                side == Side::Short,
                SIGS_CAP_MS,
            )
        });
        let old = old_entry(src, sym, side, sg, ex);
        let _ = writeln!(
            t,
            "{},{},{},{},{},{},{},{}",
            sym.name,
            sg.level,
            sg.ms,
            side.s() * sg.lp,
            proxy.map_or(String::new(), |(p, _)| p.to_string()),
            proxy.map_or(String::new(), |(_, a)| a.to_string()),
            old.map_or(String::new(), |(m, _)| m.to_string()),
            old.map_or(String::new(), |(_, p)| p.to_string()),
        );
    }
    fs::write(dir.join(format!("{}-sigs.csv", side.name())), t)?;
    Ok(())
}

/// TK-671: `P42_SIDES=long|short` — считать только одну сторону (фаза 1 и фаза 2 пропускают вторую); без переменной — обе, как раньше.
fn wanted_sides() -> Vec<Side> {
    match std::env::var("P42_SIDES").as_deref() {
        Ok("long") => vec![Side::Long],
        Ok("short") => vec![Side::Short],
        _ => vec![Side::Long, Side::Short],
    }
}

/// `P42_SIGSX=<сутки от>:<сутки до>` (§13.17): по каждому сигналу задания — `<side>-sigsx.csv`: монета, уровень, время сигнала, расстояние,
/// цена уровня на сигнале, рождение/конец уровня (минуты), бар первого пивота, NATR минуты `t−1` (×1e8), члены версии уровня на сигнале (`бар:цена|…`).
pub(super) fn write_sigsx(
    dir: &Path,
    syms: &[Sym],
    side: Side,
    p: &Params,
    sigs: &[TSig],
    days: (usize, usize),
) -> Result<()> {
    let mut cache: HashMap<u16, Vec<super::levels::Level>> = HashMap::new();
    let mut t =
        String::from("sym,level,sig_ms,dist_e8,level_px_e8,born,end,first_bar,natr_e8,members\n");
    for sg in sigs {
        let d = usize::from(sg.day);
        if d < days.0 || d > days.1 {
            continue;
        }
        let sym = &syms[sg.sym as usize];
        let levels = cache
            .entry(sg.sym)
            .or_insert_with(|| build_levels_tracked(sym, &p.sig.lv, side, true));
        let lv = &levels[sg.level as usize];
        let si = lv.segs.iter().rposition(|(m, _)| *m <= sg.t).unwrap_or(0);
        // TK-668: у уровней источника (`lx ≠ 0`) журнала членов нет — столбец пуст
        let mem: Vec<String> = lv
            .mem
            .get(si)
            .map_or(&[][..], Vec::as_slice)
            .iter()
            .map(|(b, px)| format!("{b}:{px}"))
            .collect();
        let natr = sym
            .natr_for(p.sig.natr_p)
            .map_or(-1, |n| n[sg.t as usize - 1]);
        let _ = writeln!(
            t,
            "{},{},{},{},{},{},{},{},{},{}",
            sym.name,
            sg.level,
            sg.ms,
            sg.dist,
            side.s() * sg.lp,
            lv.born,
            lv.end,
            lv.first_bar,
            natr,
            mem.join("|"),
        );
    }
    fs::write(dir.join(format!("{}-sigsx.csv", side.name())), t)?;
    Ok(())
}

/// Ступеней отклонения в `P42_EXC` (шаг 0.25 NATR, 0.25 … 5.0).
const EXC_STEPS: usize = 20;

/// `P42_EXC=<горизонт, мин>:<окно проторговки W, мин>` (§13.31 шаг 0, описательно, P&L нет): по каждому сигналу задания — `<side>-exc.csv`: первые моменты
/// (мс от сигнала, −1 — не было за горизонт) касания уровня (`p ≥ L`), «продолжения» (`p ≥ L + 1·NATR`), ухода цены против позиции на `k·0.25·NATR` ниже уровня
/// (`l1…l20`) и ниже дальнего края проторговки (`r0…r20`, `r0` — на краю и ниже); сделки — строго после `сигнал + lat`, после касания и продолжения просмотр идёт
/// только пока не найдено продолжение. Единица `u = |L|·NATR` (NATR выхода на сигнале, как у `Pos`).
#[allow(clippy::too_many_arguments)]
fn write_exc(
    dir: &Path,
    syms: &[Sym],
    side: Side,
    sigs: &[TSig],
    ex: &ExitCfg,
    src: &dyn TradeSrc,
    hz_min: i64,
    w: usize,
) -> Result<()> {
    let s = side.s();
    let mut t = String::from(
        "sym,level,sig_ms,sig_min,dist_e8,lp_e8,natr_e8,nvalid_rng,rlo_e8,rhi_e8,touch,up1",
    );
    for k in 1..=EXC_STEPS {
        let _ = write!(t, ",l{k}");
    }
    for k in 0..=EXC_STEPS {
        let _ = write!(t, ",r{k}");
    }
    t.push('\n');
    for sg in sigs {
        let sym = &syms[sg.sym as usize];
        let natr = exit_natr(sym, ex, sg.t as usize);
        if natr <= 0 {
            continue;
        }
        let u = (i128::from(sg.lp.abs()) * i128::from(natr) / i128::from(SCALE)) as i64;
        if u <= 0 {
            continue;
        }
        let rng = cons_range(sym, s, sg.t as usize, w);
        let t0 = sg.ms + ex.lat_ms;
        let end = sg.ms + hz_min * 60_000;
        let mut cur = Cursor::new(src, &sym.name, usize::from(sg.day), sg.idx as usize + 1);
        let (mut touch, mut up1) = (-1i64, -1i64);
        let mut l = [-1i64; EXC_STEPS];
        let mut r = [-1i64; EXC_STEPS + 1];
        let (mut nl, mut nr) = (0usize, 0usize);
        while let Some((ms, px, _)) = cur.next_tick() {
            if ms > end {
                break;
            }
            if ms <= t0 {
                continue;
            }
            let rel = ms - sg.ms;
            let p = s * px;
            if touch < 0 {
                if p >= sg.lp {
                    touch = rel;
                } else {
                    let d = ((sg.lp - p) as i128 * 4 / i128::from(u)) as usize;
                    while nl < d.min(EXC_STEPS) {
                        l[nl] = rel;
                        nl += 1;
                    }
                    if let Some((rlo, _)) = rng {
                        if p <= rlo {
                            let d = 1 + ((rlo - p) as i128 * 4 / i128::from(u)) as usize;
                            while nr < d.min(EXC_STEPS + 1) {
                                r[nr] = rel;
                                nr += 1;
                            }
                        }
                    }
                }
            }
            if touch >= 0 && up1 < 0 && p >= sg.lp + u {
                up1 = rel;
            }
            if touch >= 0 && up1 >= 0 {
                break;
            }
        }
        let (rlo, rhi, nv) = rng.map_or((String::new(), String::new(), 0), |(a, b)| {
            ((s * a).to_string(), (s * b).to_string(), 1)
        });
        let _ = write!(
            t,
            "{},{},{},{},{},{},{},{nv},{rlo},{rhi},{touch},{up1}",
            sym.name,
            sg.level,
            sg.ms,
            sg.t,
            sg.dist,
            s * sg.lp,
            natr
        );
        for v in l {
            let _ = write!(t, ",{v}");
        }
        for v in r {
            let _ = write!(t, ",{v}");
        }
        t.push('\n');
    }
    fs::write(dir.join(format!("{}-exc.csv", side.name())), t)?;
    Ok(())
}

/// `P42_LEVELS=1` (§13.17): все уровни всех монет задания — `<side>-levels.csv`: монета, номер, рождение, конец (минуты; `u32::MAX` — до
/// конца данных), бар первого пивота, версии цены (`минута:цена|…`) и члены каждой версии (`бар:цена,…` через `|`, по версиям).
pub(super) fn write_levels(dir: &Path, syms: &[Sym], side: Side, p: &Params) -> Result<()> {
    let mut t = String::from(
        "sym,level,born,end,first_bar,segs,members
",
    );
    for sym in syms {
        for (i, lv) in build_levels_tracked(sym, &p.sig.lv, side, true)
            .iter()
            .enumerate()
        {
            let segs: Vec<String> = lv.segs.iter().map(|(m, px)| format!("{m}:{px}")).collect();
            let mem: Vec<String> = lv
                .mem
                .iter()
                .map(|v| {
                    v.iter()
                        .map(|(b, px)| format!("{b}:{px}"))
                        .collect::<Vec<_>>()
                        .join(",")
                })
                .collect();
            let _ = writeln!(
                t,
                "{},{i},{},{},{},{},{}",
                sym.name,
                lv.born,
                lv.end,
                lv.first_bar,
                segs.join("|"),
                mem.join("|"),
            );
        }
    }
    fs::write(dir.join(format!("{}-levels.csv", side.name())), t)?;
    Ok(())
}

/// `P42_DETAIL=1`: построчно (JSONL) всё, что нужно для графика сделки — уровень на сигнале с членами, ноги, NATR.
fn write_detail(dir: &Path, syms: &[Sym], side: Side, p: &Params, r: &TickResult) -> Result<()> {
    let mut cache: HashMap<u16, Vec<super::levels::Level>> = HashMap::new();
    let mut t = String::new();
    for (i, x) in r.trades.iter().enumerate() {
        let sym = &syms[x.sym as usize];
        let levels = cache
            .entry(x.sym)
            .or_insert_with(|| build_levels_tracked(sym, &p.sig.lv, side, true));
        let lv = &levels[x.level as usize];
        let si = lv
            .segs
            .iter()
            .rposition(|(m, _)| *m <= x.sig_min)
            .unwrap_or(0);
        let mut rec = serde_json::json!({
            "i": i, "sym": sym.name, "side": side.name(), "level": x.level,
            "born": lv.born, "end": lv.end, "first_bar": lv.first_bar,
            "level_px_e8": lv.segs[si].1, "members": lv.mem.get(si).map_or(&[][..], Vec::as_slice),
            "sig_ms": x.sig_ms, "sig_min": x.sig_min, "dist_e8": x.dist,
            "entry_ms": x.entry_ms, "entry_px_e8": x.entry_px, "exit_ms": x.exit_ms,
            "kind": x.kind.name(), "takes": x.takes, "natr_e8": x.natr_e8,
            "legs": x.legs.iter().map(|l| serde_json::json!([l.ms, l.px, l.u, l.maker])).collect::<Vec<_>>(),
            "gross": x.gross, "fees": x.fees, "funding": x.funding, "net": x.net,
        });
        if p.sig.ap != 0 {
            rec["ws"] = serde_json::Value::Bool(x.ws);
        }
        if let Some((ms, px)) = x.old_entry {
            rec["old_entry_ms"] = ms.into();
            rec["old_entry_px_e8"] = px.into();
        }
        if let Some((ms, px)) = x.tl {
            rec["tl"] = serde_json::json!([ms, px]);
        }
        if let Some(t2) = &x.lot2 {
            rec["kind1"] = x.kind1.name().into();
            rec["lot2"] = serde_json::json!({
                "entry_ms": t2.entry_ms, "entry_px_e8": t2.entry_px, "exit_ms": t2.exit_ms,
                "kind": t2.kind.name(), "takes": t2.takes,
                "legs": t2.legs.iter().map(|l| serde_json::json!([l.ms, l.px, l.u, l.maker])).collect::<Vec<_>>(),
            });
            if let Some((ms, px)) = t2.tl {
                rec["lot2"]["tl"] = serde_json::json!([ms, px]);
            }
        }
        t.push_str(&rec.to_string());
        t.push('\n');
    }
    fs::write(dir.join(format!("{}-detail.jsonl", side.name())), t)?;
    Ok(())
}

/// TK-683: память процесса из `/proc/self/status` (сейчас и пик) — строка журнала, на результат не влияет.
fn mem_line(stage: &str) -> String {
    let st = fs::read_to_string("/proc/self/status").unwrap_or_default();
    let gb = |key: &str| {
        st.lines()
            .find_map(|l| l.strip_prefix(key))
            .and_then(|v| v.trim().trim_end_matches("kB").trim().parse::<f64>().ok())
            .map_or(0.0, |kb| kb / 1_048_576.0)
    };
    format!(
        "память {stage}: сейчас {:.2} ГБ, пик {:.2} ГБ",
        gb("VmRSS:"),
        gb("VmHWM:")
    )
}

#[allow(clippy::too_many_arguments)]
pub fn run(
    cache: &Path,
    trades: &Path,
    variants: &Path,
    out: &Path,
    threads: usize,
    symbols: Option<String>,
    cap_gb: usize,
    ftab: Option<&Path>,
    store: Option<StoreOpts>,
    sig_cache: Option<SigCacheOpts>,
    keep_orders: bool,
) -> Result<()> {
    let t0 = Instant::now();
    let only: Option<Vec<String>> = symbols.map(|s| s.split(',').map(str::to_string).collect());
    // TK-683: склад сделок (обход каталога) и отпечаток кэша сигналов (метаданные всех суток) — пока грузятся минутки, а не после них
    let files = data::cache_files(cache, only.as_deref())?;
    let (syms, store, sigcache) = std::thread::scope(|sc| {
        let h_store = sc.spawn(|| {
            store
                .as_ref()
                .map(|o| TickStore::open(o).map(Arc::new))
                .transpose()
        });
        let h_sig = sc.spawn(|| {
            let names: Vec<&str> = files.iter().map(|f| f.0.as_str()).collect();
            sig_cache
                .as_ref()
                .map(|o| SigCache::open(o, cache, trades, &names))
                .transpose()
        });
        let syms = data::load_files(&files, threads);
        (syms, h_store.join(), h_sig.join())
    });
    let syms = syms?;
    eprintln!("{}", mem_line("после загрузки минуток"));
    eprintln!(
        "загружено символов {} за {:.1} с",
        syms.len(),
        t0.elapsed().as_secs_f64()
    );
    let text = fs::read_to_string(variants).with_context(|| variants.display().to_string())?;
    let mut vs: Vec<(String, Params)> = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        vs.push(parse_variant(line).with_context(|| line.to_string())?);
    }
    if vs.is_empty() {
        bail!("нет вариантов");
    }
    if keep_orders {
        // TK-743: журнал заявок — для всех вариантов задания; счёт и `trades.csv` от ключа не зависят
        for (_, p) in &mut vs {
            p.exit.keep_orders = true;
        }
    }
    // §13.21: таблица признаков кандидатов — один раз, только столбцы, названные в `fa`; ошибка таблицы — до тяжёлой фазы 1
    let mut want: Vec<&str> = vs
        .iter()
        .filter(|(_, p)| p.filt.on())
        .map(|(_, p)| p.filt.name.as_str())
        .collect();
    want.sort_unstable();
    want.dedup();
    let tab: Option<FeatTab> = if want.is_empty() {
        None
    } else {
        let Some(path) = ftab else {
            bail!("варианты с фильтром признаков (fa/fo/fv) требуют --ftab <csv>");
        };
        let idx: HashMap<&str, u16> = syms
            .iter()
            .enumerate()
            .map(|(i, s)| (s.name.as_str(), i as u16))
            .collect();
        let text = fs::read_to_string(path).with_context(|| path.display().to_string())?;
        let t = FeatTab::parse(&text, &idx, &want).with_context(|| path.display().to_string())?;
        eprintln!(
            "таблица признаков: строк {} (монеты задания), столбцов {} за {:.1} с",
            t.rows(),
            want.len(),
            t0.elapsed().as_secs_f64()
        );
        Some(t)
    };
    fs::create_dir_all(out)?;
    // TK-683: время этапов вызова — в журнал (на результат не влияет)
    let t_parsed = t0.elapsed().as_secs_f64();
    // задания фазы 1: различные (SigCfg, сторона)
    let mut job_idx: HashMap<(SigCfg, Side), usize> = HashMap::new();
    let mut jobs: Vec<(SigCfg, Side)> = Vec::new();
    let sides = wanted_sides();
    for (_, p) in &vs {
        for &side in &sides {
            let k = (p.sig.clone(), side);
            if !job_idx.contains_key(&k) {
                job_idx.insert(k.clone(), jobs.len());
                jobs.push(k);
            }
        }
    }
    let store = store
        .map_err(|_| anyhow::anyhow!("поток открытия склада сделок упал"))?
        .context("склад сделок")?;
    let t_store = t0.elapsed().as_secs_f64();
    // TK-706: память исходов хода — общая для рабочих всех вариантов запуска (`P42_MEMO=0` выключает, `P42_MEMO_VERIFY` — шаг сверки)
    let memo = MoveMemo::from_env();
    let mut src = ZipSrc::new(trades.to_path_buf(), cap_gb << 30).with_memo(memo);
    if let Some(st) = &store {
        src = src.with_store(Arc::clone(st));
    }
    // TK-683: ступени НОД суток — рядом с кэшем сигналов (тот же каталог, подкаталог `gcd`)
    if let Some(o) = &sig_cache {
        src = src.with_gcd_dir(o.dir.join("gcd"));
    }
    eprintln!(
        "фаза 1: монет {}, заданий сигналов {}",
        syms.len(),
        jobs.len()
    );
    // TK-607 (L2): задания с записью в кэше сигналов фазу 1 не проходят; остальные считаются как прежде
    let sigcache = sigcache
        .map_err(|_| anyhow::anyhow!("поток открытия кэша сигналов упал"))?
        .context("кэш сигналов")?;
    let t_sc_open = t0.elapsed().as_secs_f64();
    let keys: Vec<Option<String>> = jobs
        .iter()
        .map(|(cfg, side)| sigcache.as_ref().and_then(|c| c.key(cfg, *side)))
        .collect();
    let mut sigs: Vec<Vec<TSig>> = vec![Vec::new(); jobs.len()];
    let mut ctr: Vec<TickCtr> = vec![TickCtr::default(); jobs.len()];
    let mut todo: Vec<usize> = Vec::new();
    for (j, key) in keys.iter().enumerate() {
        match key
            .as_deref()
            .zip(sigcache.as_ref())
            .and_then(|(k, c)| c.get(k))
        {
            Some((s, c)) => (sigs[j], ctr[j]) = (s, c),
            None => todo.push(j),
        }
    }
    let t_sc_read = t0.elapsed().as_secs_f64();
    eprintln!(
        "этапы до фазы 1, с от старта: варианты и таблица {t_parsed:.2}, склад сделок {t_store:.2}, кэш сигналов открыт {t_sc_open:.2}, прочитан {t_sc_read:.2}"
    );
    if !todo.is_empty() {
        let tjobs: Vec<(SigCfg, Side)> = todo.iter().map(|&j| jobs[j].clone()).collect();
        let idx: Vec<usize> = (0..syms.len()).collect();
        let parts = par_map(&idx, threads, |&i| {
            let mut outs: Vec<Vec<TSig>> = vec![Vec::new(); tjobs.len()];
            let mut ctrs: Vec<TickCtr> = vec![TickCtr::default(); tjobs.len()];
            signals_for_coin(&syms[i], i as u16, &tjobs, &src, &mut outs, &mut ctrs);
            outs.into_iter().zip(ctrs).collect::<Vec<_>>()
        });
        for part in parts {
            for (k, (v, c)) in part.into_iter().enumerate() {
                sigs[todo[k]].extend(v);
                ctr[todo[k]].add(&c);
            }
        }
        for &j in &todo {
            sort_tsigs(&mut sigs[j]);
        }
    }
    if vs.iter().any(|(_, p)| p.sig.lv.pk || p.sig.lv.pp) {
        use std::sync::atomic::Ordering::Relaxed;
        eprintln!(
            "pk (§13.11): не родилось из-за предыстории {}, убито перепроверкой при смене версии {}, убито минутным проколом {}",
            super::levels::PK_BIRTH_BLOCKED.load(Relaxed),
            super::levels::PK_RETRO_KILLED.load(Relaxed),
            super::tick::PK_MINUTE_KILLED.load(Relaxed)
        );
    }
    if vs.iter().any(|(_, p)| p.sig.lv.lz || p.sig.fw_min > 0) {
        use std::sync::atomic::Ordering::Relaxed;
        let r = &super::tick::FW_REJECT;
        eprintln!(
            "TK-747: закол отменил пар {}; отсев кандидат-минут: ожидание fw {}, нет касания {}, проторговка {}; убито минутным проколом {}",
            super::levels::LZ_CANCELLED.load(Relaxed),
            r[0].load(Relaxed),
            r[1].load(Relaxed),
            r[2].load(Relaxed),
            super::tick::PK_MINUTE_KILLED.load(Relaxed)
        );
    }
    if vs.iter().any(|(_, p)| p.sig.pb) {
        eprintln!(
            "pb (§13.13): уровней убито сделкой на уровне или за ним {}",
            super::tick::PB_KILLED.load(std::sync::atomic::Ordering::Relaxed)
        );
    }
    if vs.iter().any(|(_, p)| p.sig.fresh_on()) {
        eprintln!(
            "свежий уровень (§13.14): кандидатов отвергнуто {} (по всем заданиям сигналов)",
            super::tick::FR_REJECTED.load(std::sync::atomic::Ordering::Relaxed)
        );
    }
    let ls = src.stat.lock().map(|g| g.clone()).unwrap_or_default();
    eprintln!(
        "фаза 1 готова за {:.1} с; файлов {}, строк {}, не по порядку {}, вне суток {}, плохих строк {}, ошибок {}",
        t0.elapsed().as_secs_f64(),
        ls.files,
        ls.rows,
        ls.unsorted_files,
        ls.out_of_day,
        ls.bad_rows,
        ls.errors
    );
    eprintln!("{}", mem_line("после фазы 1"));
    if let Some(st) = &store {
        eprintln!("{}", st.summary());
    }
    if let Some(sc) = &sigcache {
        // ошибка загрузки суток неотличима от «нет файла»: при ошибках запись не закрепляется
        if ls.errors == 0 {
            for &j in &todo {
                if let Some(k) = &keys[j] {
                    sc.put(k, &sigs[j], &ctr[j]);
                }
            }
        }
        eprintln!("{}", sc.summary());
    }
    // фаза 2: портфель и исходы — задания (вариант, сторона) параллельно
    let tasks: Vec<(usize, Side)> = (0..vs.len())
        .flat_map(|i| sides.iter().map(move |&sd| (i, sd)))
        .collect();
    // TK-683: разогрев суток сделок. Потоки вариантов идут по сигналам в одном порядке и ждут разбора одних и тех же суток по цепочке
    // (на партии из 400 вариантов — две трети времени фазы 2). До фазы 2 несколько вариантов, взятых равномерно по списку, проходятся
    // по монетам параллельно (исходы отбрасываются): разные потоки разбирают разные сутки. На результат вариантов разогрев не влияет;
    // мало заданий на поток — лишние проходы не окупаются.
    if threads > 1 && tasks.len() >= 4 * threads && std::env::var_os("P42_SIGSX_ONLY").is_none() {
        let t1 = std::time::Instant::now();
        let n_scout = WARM_VARIANTS.min(tasks.len());
        let mut split: HashMap<usize, Vec<Vec<TSig>>> = HashMap::new();
        let mut scouts: Vec<(usize, Side, usize)> = Vec::new();
        for k in 0..n_scout {
            let (vi, side) = tasks[k * tasks.len() / n_scout];
            if let Some(&j) = job_idx.get(&(vs[vi].1.sig.clone(), side)) {
                split.entry(j).or_insert_with(|| {
                    let mut by_sym: Vec<Vec<TSig>> = vec![Vec::new(); syms.len()];
                    for s in &sigs[j] {
                        by_sym[s.sym as usize].push(*s);
                    }
                    by_sym.retain(|v| !v.is_empty());
                    by_sym
                });
                scouts.push((vi, side, j));
            }
        }
        let items: Vec<(usize, Side, &Vec<TSig>)> = scouts
            .iter()
            .flat_map(|&(vi, side, j)| split[&j].iter().map(move |v| (vi, side, v)))
            .collect();
        par_map(&items, threads, |&(vi, side, v)| {
            // память под сутки кончилась — дальше разогрев только вытеснял бы загруженное
            if src.evicted() {
                return;
            }
            let p = &vs[vi].1;
            run_tick_xb(&syms, v, side, &p.exit, &p.port, &src, p.mk, p.ek, p.xb);
        });
        eprintln!(
            "разогрев суток сделок: {:.1} с, вариантов {}, проходов по монетам {}{}",
            t1.elapsed().as_secs_f64(),
            scouts.len(),
            items.len(),
            if src.evicted() {
                " (прерван: память под сутки исчерпана)"
            } else {
                ""
            }
        );
    }
    let done = std::sync::atomic::AtomicUsize::new(0);
    let total = tasks.len();
    let (run_ns, write_ns) = (
        std::sync::atomic::AtomicU64::new(0),
        std::sync::atomic::AtomicU64::new(0),
    );
    let t_ph2 = std::time::Instant::now();
    let warm_ns = (
        src.load_ns.load(std::sync::atomic::Ordering::Relaxed),
        src.wait_ns.load(std::sync::atomic::Ordering::Relaxed),
    );
    // TK-683: `P42_WIDE=<рабочих фазы 2>` (не меньше `threads`): сутки, не помещающиеся в `--cap-gb`, каждая волна вариантов разбирает
    // заново — чем шире волна, тем меньше разборов; считают одновременно по-прежнему `threads` рабочих. На результат не влияет.
    let wide = match std::env::var("P42_WIDE") {
        Ok(v) => v
            .parse::<usize>()
            .with_context(|| format!("P42_WIDE={v}"))?
            .max(threads),
        Err(_) => threads,
    };
    if wide > threads {
        eprintln!("фаза 2: рабочих {wide}, считают одновременно {threads} (P42_WIDE)");
    }
    let parts = par_map_wide(
        &tasks,
        wide,
        threads,
        |&(vi, side)| -> Result<(usize, Side, String, serde_json::Value)> {
            let (id, p) = &vs[vi];
            let dir = out.join(id);
            fs::create_dir_all(&dir)?;
            let Some(&j) = job_idx.get(&(p.sig.clone(), side)) else {
                bail!("{id}: нет задания сигналов");
            };
            // §13.21: фильтр признаков — кандидат отбрасывается до портфеля; дампы ниже остаются по полному потоку кандидатов
            let mut fstat = None;
            let filtered;
            let port_sigs: &[TSig] = match (&tab, p.filt.on()) {
                (Some(t), true) => {
                    let c = t.col(&p.filt.name).context("нет столбца признака")?;
                    let (v, st) = filter_sigs(
                        &sigs[j],
                        t,
                        c,
                        p.filt.hi == Some(true),
                        p.filt.v.unwrap_or(0.0),
                    );
                    fstat = Some(st);
                    filtered = v;
                    &filtered
                }
                _ => &sigs[j],
            };
            // `P42_SIGSX_ONLY` (§13.18, v20): сетка суток длиннее окна сделок (COUNT_DAYS), портфель не считается — только сигналы и дампы.
            let t_run = std::time::Instant::now();
            let r = if std::env::var_os("P42_SIGSX_ONLY").is_some() {
                TickResult::empty()
            } else {
                run_tick_xb(
                    &syms, port_sigs, side, &p.exit, &p.port, &src, p.mk, p.ek, p.xb,
                )
            };
            run_ns.fetch_add(
                t_run.elapsed().as_nanos() as u64,
                std::sync::atomic::Ordering::Relaxed,
            );
            let t_write = std::time::Instant::now();
            write_outputs(&dir, &syms, side, &r)?;
            write_ns.fetch_add(
                t_write.elapsed().as_nanos() as u64,
                std::sync::atomic::Ordering::Relaxed,
            );
            if p.exit.keep_orders {
                write_orders(&dir, &syms, side, &r)?;
                write_setup(&dir, &syms, side, p, &sigs[j], &r)?;
            }
            if p.exit.tape_entry() {
                write_entry_rej(&dir, &syms, side, &r)?;
            }
            if p.mk > 0 || p.exit.e1t != 0 {
                write_maker(&dir, side, &r)?;
            }
            if p.exit.vf > 0 {
                write_vstop(&dir, &syms, side, &r)?;
            }
            if let Some(v) = std::env::var_os("P42_SIGS") {
                let v = v.to_string_lossy().into_owned();
                let (a, b) = v.split_once(':').context("P42_SIGS=<от>:<до>")?;
                let days = (a.parse::<usize>()?, b.parse::<usize>()?);
                write_sigs(&dir, &syms, side, &sigs[j], &p.exit, &src, days)?;
            }
            if let Some(v) = std::env::var_os("P42_SIGSX") {
                let v = v.to_string_lossy().into_owned();
                let (a, b) = v.split_once(':').context("P42_SIGSX=<от>:<до>")?;
                let days = (a.parse::<usize>()?, b.parse::<usize>()?);
                write_sigsx(&dir, &syms, side, p, &sigs[j], days)?;
            }
            if let Some(v) = std::env::var_os("P42_EXC") {
                let v = v.to_string_lossy().into_owned();
                let (a, b) = v
                    .split_once(':')
                    .context("P42_EXC=<горизонт, мин>:<окно W, мин>")?;
                write_exc(
                    &dir,
                    &syms,
                    side,
                    &sigs[j],
                    &p.exit,
                    &src,
                    a.parse()?,
                    b.parse()?,
                )?;
            }
            if std::env::var_os("P42_LEVELS").is_some() {
                write_levels(&dir, &syms, side, p)?;
            }
            if std::env::var_os("P42_DETAIL").is_some() {
                write_detail(&dir, &syms, side, p, &r)?;
            }
            let n = r.trades.len();
            let stops = r
                .trades
                .iter()
                .filter(|x| x.kind == super::trade::ExitKind::HardStop)
                .count();
            let total_net: f64 = r.daily.iter().sum();
            let ma = mean(&r.daily[..HALF_DAYS]);
            let mb = mean(&r.daily[HALF_DAYS..]);
            let row = format!(
                "{id},{},{n},{:.3},{:.4},{:.4},{:.4}",
                side.name(),
                total_net,
                ma,
                mb,
                if n > 0 { stops as f64 / n as f64 } else { 0.0 }
            );
            let mut meta = serde_json::json!({
                "trades": n, "hard_stops": stops, "net_total": total_net,
                "rej_[consumed,busy,coin_pause,global_pause,cap,no_outcome]": r.rej,
                "signals_AB": ctr[j].signals, "cand_minutes": ctr[j].cands,
                "missing_days": ctr[j].missing_days, "signals_total": sigs[j].len(),
            });
            if p.sig.ap != 0 {
                meta["ap_wrong_signals"] = ctr[j].ap_wrong.into();
            }
            if let Some(st) = fstat {
                meta["filter_[cands,dropped,no_key,no_value]"] =
                    serde_json::json!([st.cands, st.dropped, st.no_key, st.no_value]);
            }
            if p.exit.split() {
                meta["lot2_[now,later,cancelled,rejected]"] = serde_json::json!(r.lot2);
            }
            if p.exit.nx_m > 0 || p.exit.sl_m > 0 || p.exit.cs_w > 0 {
                meta["lvl_stop_[floored,clamped]"] = serde_json::json!(r.floor);
            }
            if p.port.pr != 0 || p.port.pw_ms != 0 {
                meta["prio_[windows,multi,order_changed,late_fill,late_rej,same_ms_moved]"] =
                    serde_json::json!(r.prio);
            }
            if p.ek > 0 {
                meta["ek_[filled,expired,touched,rejected,not_placed]"] = serde_json::json!(r.ek);
            }
            if p.xb > 0 {
                meta["xb_[filled,expired,rejected]"] = serde_json::json!(r.xb);
            }
            if p.sig.fg_m > 0 {
                meta["fg_[susp_pk,susp_pb,susp_ban,resumed]"] = serde_json::json!(ctr[j].fg);
            }
            if p.exit.tape_entry() {
                let n_of = |w: EntryRej| r.entry_rej.iter().filter(|x| x.3 == w).count();
                meta["entry_rej_[beyond,stale]"] =
                    serde_json::json!([n_of(EntryRej::Beyond), n_of(EntryRej::Stale)]);
            }
            let k = done.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
            eprintln!(
                "[{k}/{total}] {id} {}  {:.0} с",
                side.name(),
                t0.elapsed().as_secs_f64()
            );
            Ok((vi, side, row, meta))
        },
    );
    let mut metas: Vec<serde_json::Map<String, serde_json::Value>> = vs
        .iter()
        .map(|(id, p)| {
            let mut m = serde_json::Map::new();
            m.insert("id".into(), id.clone().into());
            m.insert("params".into(), format!("{p:?}").into());
            m
        })
        .collect();
    let mut rows: Vec<String> = Vec::new();
    for part in parts {
        let (vi, side, row, meta) = part?;
        rows.push(row);
        metas[vi].insert(side.name().into(), meta);
    }
    for ((id, _), m) in vs.iter().zip(metas) {
        fs::write(
            out.join(id).join("meta.json"),
            serde_json::to_string_pretty(&m)?,
        )?;
    }
    let total = vs.len();
    let mut summary =
        String::from("variant,side,trades,net_total,mean_day_A,mean_day_B,stop_share\n");
    for line in rows {
        summary.push_str(&line);
        summary.push('\n');
    }
    fs::write(out.join("summary.csv"), summary)?;
    let ls = src.stat.lock().map(|g| g.clone()).unwrap_or_default();
    eprintln!(
        "готово: {total} вариантов, {:.1} с; файлов прочитано {}, ошибок загрузки {}",
        t0.elapsed().as_secs_f64(),
        ls.files,
        ls.errors
    );
    {
        use std::sync::atomic::Ordering::Relaxed;
        let s = |ns: u64| ns as f64 / 1e9;
        eprintln!(
            "фаза 2: стена {:.1} с; по потокам, с: варианты (портфель и исходы, с сутками) {:.1}, из них разбор суток {:.1} и ожидание суток и разрешения {:.1}; запись основных файлов {:.1}",
            t_ph2.elapsed().as_secs_f64(),
            s(run_ns.load(Relaxed)),
            s(src.load_ns.load(Relaxed) - warm_ns.0),
            s(src.wait_ns.load(Relaxed) - warm_ns.1),
            s(write_ns.load(Relaxed))
        );
    }
    match src.move_memo() {
        Some(m) => eprintln!("{}", m.summary()),
        None => eprintln!("память исходов: выключена (P42_MEMO=0)"),
    }
    if src.evicted() {
        eprintln!("сутки сверх памяти: были вытеснения");
    }
    if let Some(st) = &store {
        eprintln!("{}", st.summary());
    }
    eprintln!("{}", mem_line("в конце"));
    if ls.errors > 0 {
        bail!("ошибок загрузки: {}", ls.errors);
    }
    Ok(())
}
