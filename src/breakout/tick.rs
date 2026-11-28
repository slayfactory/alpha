//! П-42 шаг B (протокол §13.2): сигнал, вход и выходы по сделкам Binance aggTrades вместо минутного приближения.
//!
//! Что не меняется: уровни (часовые бары из минуток), пул и NATR (по закрытым минуткам), запрет ложного пробоя
//! (по закрытиям минуток), портфель, издержки, фандинг. Что меняется: (1) сигнал — первая сделка минуты `t`,
//! подошедшая к уровню на ≤ E % снизу, пока ни одна сделка минуты не дошла до уровня; (2) вход — следующая
//! агрессивная сделка стороны входа (± проскальзывание); (3) тейки/стоп/трейл — по ценам сделок: тейк — только
//! строго через цену, стоп — по цене сделки, пробившей его.
//!
//! Фаза 1 (`signals_for_coin`): кандидаты (уровень, минута) по минуткам → проверка по сделкам суток монеты.
//! Фаза 2 (`run_tick`): портфель (как `portfolio::run`, но в мс) и исход сделки по сделкам (`simulate_tick`).

use super::data::{
    day_string, parse_dec, Sym, COUNT_DAYS, DAYS_TOTAL, HALF_DAYS, MIN_MS, N_MIN, SCALE, START_DAY,
    START_MIN,
};
use super::levels::{build_levels_far, pierce_tol, Level};
use super::memo::{MoveKey, MoveMemo};
use super::orders::{
    cancel_open, push_reprice, OrdEv, EV_CANCEL, EV_FILL, EV_PLACE, EV_REPRICE, OID_E1, OID_E2,
    OID_EXIT, OID_NX, OID_RT, OID_STOP, OID_TRL, OID_TU, OID_TX, TY_COND, TY_LIMIT, TY_MARKET,
    TY_STOP,
};
use super::params::{ExitCfg, LevelCfg, PortCfg, SigCfg};
use super::portfolio::Settled;
use super::store::{DayHead, DayTrades, QtyRaw, SrcMeta, TickStore};
use super::trade::{frac, ExitKind, NOTIONAL_U};
use super::volstop;
use super::Side;
use crate::lob::costs::leg_fee_bps;
use anyhow::{bail, Context, Result};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

/// Счётчик опции `pk` (§13.11, диагностика): уровни, убитые минутным проколом > τ, которых запрет ложного пробоя к этой минуте не убил.
pub static PK_MINUTE_KILLED: AtomicU64 = AtomicU64::new(0);
/// TK-747 (`fw`): отсев кандидат-минут по шагам свежести — [ожидание `fw` после постановки, нет касания, проторговка]; диагностика (печать в `breakout tick`).
pub static FW_REJECT: [AtomicU64; 3] = [AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0)];

/// Счётчик правила §13.14 (диагностика): кандидаты (уровень, минута), прошедшие прежние условия и отвергнутые правилом «свежий уровень».
pub static FR_REJECTED: AtomicU64 = AtomicU64::new(0);

/// Счётчик опции `pb` (§13.13, диагностика): уровни, убитые сделкой на уровне или за ним, которых запрет ложного пробоя к этой минуте не убил.
pub static PB_KILLED: AtomicU64 = AtomicU64::new(0);

/// Опция `ap` (§13.13): сколько суток назад искать решающую сделку.
pub const AP_LOOKBACK_DAYS: usize = 3;

pub const DAY_MS: i64 = 86_400_000;

/// `a·b/d` с усечением к нулю — как прежнее `i128`-выражение; быстрый путь в `i64`, при переполнении (и делителе 0, `MIN/−1`) — `i128`.
#[inline]
pub fn mul_div(a: i64, b: i64, d: i64) -> i128 {
    if let Some(q) = a.checked_mul(b).and_then(|x| x.checked_div(d)) {
        return i128::from(q);
    }
    i128::from(a) * i128::from(b) / i128::from(d)
}

/// Одна агрегированная сделка: цена ×1e8, время — мс от начала суток файла, `bm` = `is_buyer_maker`
/// (агрессор — продавец).
///
/// TK-683: 12 байт вместо 16 — `bm` лежит в старшем бите `off` (мс суток < 2^27), выравнивание 4: сутки сделок занимают на четверть
/// меньше памяти (`--cap-gb` вмещает на треть больше суток). На поле `px` нельзя брать ссылку (не выровнено) — только копия.
#[derive(Clone, Copy, PartialEq, Eq)]
#[repr(C, packed(4))]
pub struct Tick {
    pub px: i64,
    ob: u32,
}

impl Tick {
    const BM: u32 = 1 << 31;

    #[inline(always)]
    pub fn new(px: i64, off: u32, bm: bool) -> Tick {
        debug_assert!(off < Self::BM);
        Tick {
            px,
            ob: off | (u32::from(bm) << 31),
        }
    }

    #[inline(always)]
    pub fn off(&self) -> u32 {
        self.ob & !Self::BM
    }

    #[inline(always)]
    pub fn bm(&self) -> bool {
        self.ob & Self::BM != 0
    }
}

impl std::fmt::Debug for Tick {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let px = self.px;
        f.debug_struct("Tick")
            .field("px", &px)
            .field("off", &self.off())
            .field("bm", &self.bm())
            .finish()
    }
}

/// Источник сделок: сутки монеты (0 = 01.09) по времени; `None` — файла нет.
pub trait TradeSrc: Sync {
    fn day(&self, sym: &str, day: usize) -> Option<Arc<Vec<Tick>>>;

    /// TK-683: ступени НОД цен суток (для `tick_at`); источник с памятью считает их один раз на сутки за запуск.
    fn gcd_steps(&self, sym: &str, day: usize) -> Option<Arc<GcdSteps>> {
        self.day(sym, day).map(|v| Arc::new(GcdSteps::of(&v)))
    }

    /// TK-706: память исходов хода на запуск (`memo.rs`); `None` — исходы считаются каждый раз (источники тестов, `P42_MEMO=0`).
    fn move_memo(&self) -> Option<&MoveMemo> {
        None
    }
}

#[derive(Debug, Default, Clone)]
pub struct LoadStat {
    pub files: u64,
    pub rows: u64,
    pub unsorted_files: u64,
    pub out_of_day: u64,
    pub bad_rows: u64,
    pub errors: u64,
}

impl LoadStat {
    fn add(&mut self, o: &LoadStat) {
        self.files += o.files;
        self.rows += o.rows;
        self.unsorted_files += o.unsorted_files;
        self.out_of_day += o.out_of_day;
        self.bad_rows += o.bad_rows;
        self.errors += o.errors;
    }
}

/// Цена aggTrades ×1e8: обычная десятичная строка или запись Java `9.16E-4` (так Binance пишет цены < 0,001).
pub fn parse_px(b: &[u8]) -> Option<i64> {
    let Some(k) = b.iter().position(|&c| c == b'E' || c == b'e') else {
        return parse_dec(b, 8);
    };
    let (m, e) = (&b[..k], &b[k + 1..]);
    let (eneg, e) = match e.first()? {
        b'-' => (true, &e[1..]),
        b'+' => (false, &e[1..]),
        _ => (false, e),
    };
    if e.is_empty() || e.len() > 3 {
        return None;
    }
    let mut exp = 0i32;
    for &c in e {
        let d = c.wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        exp = exp * 10 + i32::from(d);
    }
    if eneg {
        exp = -exp;
    }
    let (mut mant, mut fd, mut dot) = (0i64, 0i32, false);
    for &c in m {
        if c == b'.' && !dot {
            dot = true;
            continue;
        }
        let d = c.wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        mant = mant.checked_mul(10)?.checked_add(i64::from(d))?;
        fd += i32::from(dot);
    }
    let shift = exp - fd + 8;
    if shift >= 0 {
        mant.checked_mul(10i64.checked_pow(shift as u32)?)
    } else {
        Some(10i64.checked_pow((-shift) as u32).map_or(0, |p| mant / p))
    }
}

/// Неотрицательная десятичная строка (в том числе `1.5E-4`) → `(мантисса, знаков после точки)`: значение = мантисса / 10^знаков,
/// хвостовые нули дробной части убраны; `None` — не число или не помещается в `u64`/`u8`.
pub fn dec_parts(b: &[u8]) -> Option<(u64, u8)> {
    let (m, e) = match b.iter().position(|&c| c == b'E' || c == b'e') {
        Some(k) => (&b[..k], Some(&b[k + 1..])),
        None => (b, None),
    };
    let mut exp = 0i32;
    if let Some(e) = e {
        let (neg, e) = match e.first()? {
            b'-' => (true, &e[1..]),
            b'+' => (false, &e[1..]),
            _ => (false, e),
        };
        if e.is_empty() || e.len() > 3 {
            return None;
        }
        for &c in e {
            let d = c.wrapping_sub(b'0');
            if d > 9 {
                return None;
            }
            exp = exp * 10 + i32::from(d);
        }
        if neg {
            exp = -exp;
        }
    }
    let (mut mant, mut dec, mut dot, mut any) = (0u64, 0i32, false, false);
    for &c in m {
        if c == b'.' && !dot {
            dot = true;
            continue;
        }
        let d = c.wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        mant = mant.checked_mul(10)?.checked_add(u64::from(d))?;
        dec += i32::from(dot);
        any = true;
    }
    if !any {
        return None;
    }
    dec -= exp;
    while dec > 0 && mant % 10 == 0 {
        mant /= 10;
        dec -= 1;
    }
    if dec < 0 {
        mant = mant.checked_mul(10u64.checked_pow(dec.unsigned_abs())?)?;
        dec = 0;
    }
    u8::try_from(dec).ok().map(|d| (mant, d))
}

/// CSV суток (`agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker`) → сделки.
pub fn parse_trades(rd: impl BufRead, day: usize, st: &mut LoadStat) -> Result<Vec<Tick>> {
    parse_rows(rd, day, st, None)
}

/// То же и количество (TK-609): сделки те же, что у `parse_trades`, плюс количество по строкам и флаги точности.
pub fn parse_trades_full(rd: impl BufRead, day: usize, mut st: LoadStat) -> Result<DayTrades> {
    let mut raw = QtyRaw::default();
    let ticks = parse_rows(rd, day, &mut st, Some(&mut raw))?;
    Ok(DayTrades::new(ticks, raw, st))
}

fn parse_rows(
    mut rd: impl BufRead,
    day: usize,
    st: &mut LoadStat,
    mut col: Option<&mut QtyRaw>,
) -> Result<Vec<Tick>> {
    let base = super::data::t0_ms() + day as i64 * DAY_MS;
    let mut v: Vec<Tick> = Vec::new();
    let mut line: Vec<u8> = Vec::with_capacity(128);
    let mut prev = 0u32;
    let mut unsorted = false;
    loop {
        line.clear();
        if rd.read_until(b'\n', &mut line)? == 0 {
            break;
        }
        if !line.first().is_some_and(u8::is_ascii_digit) {
            continue;
        }
        let mut f = line.split(|&c| c == b',');
        let (_, Some(p), q, _, _, Some(t), Some(b)) = (
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
        ) else {
            st.bad_rows += 1;
            if st.bad_rows == 1 {
                eprintln!(
                    "плохая строка (день {day}): {}",
                    String::from_utf8_lossy(&line).trim_end()
                );
            }
            continue;
        };
        let (Some(px), Some(ts)) = (parse_px(p), parse_dec(t, 0)) else {
            st.bad_rows += 1;
            if st.bad_rows == 1 {
                eprintln!(
                    "плохая строка (день {day}): {}",
                    String::from_utf8_lossy(&line).trim_end()
                );
            }
            continue;
        };
        let off = ts - base;
        if !(0..DAY_MS).contains(&off) {
            st.out_of_day += 1;
            continue;
        }
        let off = off as u32;
        if off < prev {
            unsorted = true;
        }
        prev = off;
        v.push(Tick::new(px, off, b.first() == Some(&b't')));
        if let Some(c) = col.as_deref_mut() {
            c.push(p, px, q);
        }
        st.rows += 1;
    }
    if unsorted {
        st.unsorted_files += 1;
        match col {
            Some(c) => c.sort_with(&mut v),
            None => v.sort_by_key(|x| x.off()),
        }
    }
    Ok(v)
}

/// TK-683: ячейка суток — грузит один поток, остальные ждут его результат (потоки вариантов идут по одним и тем же сигналам
/// и раньше разбирали одни и те же сутки каждый сам).
type DaySlot = Arc<OnceLock<Option<Arc<Vec<Tick>>>>>;
type GcdMap = HashMap<(String, usize), Arc<OnceLock<Option<Arc<GcdSteps>>>>>;

struct Lru {
    /// Было ли вытеснение суток (память под сутки исчерпана).
    evicted: bool,
    map: HashMap<(String, usize), DaySlot>,
    order: VecDeque<(String, usize)>,
    bytes: usize,
}

impl Lru {
    /// Вытеснение сверх `cap`: самые старые сутки уходят из `map`.
    ///
    /// TK-683: сутки, которые сейчас держит вариант или которые ещё грузятся, не вытесняются (память они не вернут, а в счёте
    /// `bytes` перестанут быть) — уходят в конец очереди; за один вызов очередь обходится не больше одного раза.
    fn shrink(&mut self, cap: usize) {
        let mut left = self.order.len();
        while self.bytes > cap && self.order.len() > 1 && left > 0 {
            left -= 1;
            self.evicted = true;
            let Some(k) = self.order.pop_front() else {
                break;
            };
            let busy = self.map.get(&k).is_some_and(|s| match s.get() {
                Some(Some(v)) => Arc::strong_count(v) > 1,
                Some(None) => false,
                None => true,
            });
            if busy {
                self.order.push_back(k);
                continue;
            }
            if let Some(old) = self.map.remove(&k) {
                if let Some(Some(v)) = old.get() {
                    self.bytes = self.bytes.saturating_sub(day_bytes(v));
                }
            }
        }
    }
}

fn day_bytes(v: &Arc<Vec<Tick>>) -> usize {
    v.len() * std::mem::size_of::<Tick>()
}

/// Итог `ZipSrc::fold_day`: нет файла zip / склад не дал сутки (промах, отступления, повреждение) / сутки свёрнуты.
pub enum Fold {
    NoZip,
    Miss,
    Done(DayHead),
}

/// Сутки из `<dir>/<МОНЕТА>/<МОНЕТА>-aggTrades-<день>.zip` (распаковка внешним `unzip -p`), с ограничением памяти.
pub struct ZipSrc {
    dir: PathBuf,
    cap_bytes: usize,
    lru: Mutex<Lru>,
    /// TK-683: ступени НОД суток — на весь запуск (десятки байт на сутки), вытеснение суток из `lru` их не трогает.
    gcds: Mutex<GcdMap>,
    pub stat: Mutex<LoadStat>,
    /// TK-607 (L1): склад разобранных суток; без него — прежний путь (`unzip` + CSV).
    store: Option<Arc<TickStore>>,
    /// TK-683: каталог файлов ступеней НОД суток (`<каталог>/<МОНЕТА>/<день>-<размер>-<mtime zip>.gcd`): тик монеты (`tick_at`) берёт НОД
    /// прежних суток, не разбирая их сделки. Имя файла несёт отпечаток zip — другой zip даёт другой файл.
    gcd_dir: Option<PathBuf>,
    /// TK-683: время потоков в `day`, нс: разбор суток (загрузчик) и ожидание чужого разбора (вместе с ожиданием разрешения на счёт).
    pub load_ns: AtomicU64,
    pub wait_ns: AtomicU64,
    /// TK-706: память исходов хода на весь запуск (общая для рабочих всех вариантов).
    memo: Option<MoveMemo>,
}

impl ZipSrc {
    pub fn new(dir: PathBuf, cap_bytes: usize) -> ZipSrc {
        ZipSrc {
            dir,
            cap_bytes,
            lru: Mutex::new(Lru {
                evicted: false,
                map: HashMap::new(),
                order: VecDeque::new(),
                bytes: 0,
            }),
            gcds: Mutex::new(HashMap::new()),
            stat: Mutex::new(LoadStat::default()),
            store: None,
            gcd_dir: None,
            load_ns: AtomicU64::new(0),
            wait_ns: AtomicU64::new(0),
            memo: None,
        }
    }

    /// TK-706: включить память исходов хода (`MoveMemo::from_env()`; `None` — выключена).
    pub fn with_memo(mut self, memo: Option<MoveMemo>) -> ZipSrc {
        self.memo = memo;
        self
    }

    pub fn with_store(mut self, store: Arc<TickStore>) -> ZipSrc {
        self.store = Some(store);
        self
    }

    pub fn with_gcd_dir(mut self, dir: PathBuf) -> ZipSrc {
        self.gcd_dir = Some(dir);
        self
    }

    /// Файл ступеней НОД суток; `None` — каталог не задан или zip суток нет.
    fn gcd_file(&self, name: &str, day: usize) -> Option<PathBuf> {
        let dir = self.gcd_dir.as_ref()?;
        let date = day_string(day);
        let zip = self
            .dir
            .join(name)
            .join(format!("{name}-aggTrades-{date}.zip"));
        let m = SrcMeta::of(&fs::metadata(zip).ok()?)?;
        Some(dir.join(name).join(format!(
            "{date}-{}-{}-{}.gcd",
            m.size, m.mtime_s, m.mtime_ns
        )))
    }

    /// TK-683: память под сутки (`cap_bytes`) уже исчерпана — загрузка впрок (разогрев) бесполезна.
    pub fn evicted(&self) -> bool {
        self.lru.lock().is_ok_and(|g| g.evicted)
    }

    /// Сутки, уже лежащие в памяти (без загрузки).
    fn loaded(&self, sym: &str, day: usize) -> Option<Arc<Vec<Tick>>> {
        let g = self.lru.lock().ok()?;
        g.map.get(&(sym.to_string(), day))?.get()?.clone()
    }

    /// Файл ступеней НОД не от этих суток (другой zip с тем же размером и временем): запуск помечается ошибкой загрузки и завершится отказом,
    /// файл переписывается по сделкам.
    fn gcd_foreign(&self, sym: &str, day: usize) {
        eprintln!(
            "ОШИБКА: файл ступеней НОД {sym} день {day} не от этих суток — пересчитан по сделкам"
        );
        if let Ok(mut g) = self.stat.lock() {
            g.errors += 1;
        }
    }

    /// Запись — через временный файл и переименование: соседний процесс видит файл целиком или не видит вовсе.
    fn gcd_write(&self, p: &std::path::Path, s: &GcdSteps) {
        let tmp = p.with_extension(format!("{}.tmp", std::process::id()));
        let ok = p.parent().is_some_and(|d| fs::create_dir_all(d).is_ok())
            && fs::write(&tmp, s.to_bytes()).is_ok()
            && fs::rename(&tmp, p).is_ok();
        if !ok {
            let _ = fs::remove_file(&tmp);
        }
    }

    /// Сутки монеты со склада или из zip. `full` — вместе с количеством (TK-609, `read_day`); без него — путь движка: со склада
    /// читается только блок сделок, а количество при первой загрузке разбирается лишь ради записи в склад и отбрасывается.
    fn load_day(&self, name: &str, day: usize, full: bool) -> Result<Option<DayTrades>> {
        let date = day_string(day);
        let p = self
            .dir
            .join(name)
            .join(format!("{name}-aggTrades-{date}.zip"));
        let Ok(md) = fs::metadata(&p) else {
            return Ok(None);
        };
        let meta = SrcMeta::of(&md);
        if let (Some(store), Some(meta)) = (&self.store, &meta) {
            let hit = if full {
                store.read_day(name, &date, meta)
            } else {
                store
                    .read(name, &date, meta)
                    .map(|(ticks, stat)| DayTrades {
                        ticks,
                        stat,
                        ..DayTrades::default()
                    })
            };
            if let Some(d) = hit {
                if let Ok(mut g) = self.stat.lock() {
                    g.add(&d.stat);
                }
                return Ok(Some(d));
            }
        }
        let mut child = Command::new("unzip")
            .arg("-p")
            .arg(&p)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .with_context(|| format!("unzip {}", p.display()))?;
        let out = child.stdout.take().context("unzip: нет stdout")?;
        let rd = BufReader::with_capacity(1 << 20, out);
        let parsed = if full || self.store.is_some() {
            parse_trades_full(rd, day, LoadStat::default())
        } else {
            let mut st = LoadStat::default();
            parse_trades(rd, day, &mut st).map(|ticks| DayTrades {
                ticks,
                stat: st,
                ..DayTrades::default()
            })
        };
        let status = child.wait()?;
        let mut d = parsed?;
        if !status.success() {
            bail!("unzip {}: {status}", p.display());
        }
        d.stat.files = 1;
        if let (Some(store), Some(meta)) = (&self.store, &meta) {
            store.write(name, &date, meta, &d);
        }
        if let Ok(mut g) = self.stat.lock() {
            g.add(&d.stat);
        }
        if !full {
            d.qty = Vec::new();
        }
        Ok(Some(d))
    }

    /// Сутки с количеством, без кэша (потребитель решает сам, что держать в памяти): со склада или из zip с записью в склад.
    pub fn read_day(&self, name: &str, day: usize) -> Result<Option<DayTrades>> {
        self.load_day(name, day, true)
    }

    /// TK-609 (v6): сутки со склада свёрткой (`TickStore::fold_day`) — без массивов и без записи в склад.
    pub fn fold_day<F: FnMut(u32, i64, bool, u64)>(
        &self,
        name: &str,
        day: usize,
        start: impl FnOnce(&DayHead) -> Option<F>,
    ) -> Fold {
        let date = day_string(day);
        let p = self
            .dir
            .join(name)
            .join(format!("{name}-aggTrades-{date}.zip"));
        let Ok(md) = fs::metadata(&p) else {
            return Fold::NoZip;
        };
        match (&self.store, SrcMeta::of(&md)) {
            (Some(store), Some(meta)) => store
                .fold_day(name, &date, &meta, start)
                .map_or(Fold::Miss, Fold::Done),
            _ => Fold::Miss,
        }
    }
}

impl TradeSrc for ZipSrc {
    fn move_memo(&self) -> Option<&MoveMemo> {
        self.memo.as_ref()
    }

    fn day(&self, sym: &str, day: usize) -> Option<Arc<Vec<Tick>>> {
        let key = (sym.to_string(), day);
        let slot: DaySlot = match self.lru.lock() {
            Ok(mut g) => match g.map.get(&key) {
                Some(slot) => slot.clone(),
                None => {
                    let slot = DaySlot::default();
                    g.map.insert(key.clone(), slot.clone());
                    g.order.push_back(key.clone());
                    slot
                }
            },
            Err(_) => DaySlot::default(),
        };
        let mut fresh = false;
        let loaded = match slot.get() {
            Some(v) => v.clone(),
            None => {
                // ожидающий чужой загрузки разрешение на счёт не держит (`P42_WIDE`); загрузчик берёт его на время разбора
                let t0 = std::time::Instant::now();
                let paused = super::pause(day);
                let v = slot
                    .get_or_init(|| {
                        paused.work(|| {
                            fresh = true;
                            match self.load_day(sym, day, false) {
                                Ok(v) => v.map(|d| Arc::new(d.ticks)),
                                Err(e) => {
                                    eprintln!("ОШИБКА загрузки {sym} день {day}: {e:#}");
                                    if let Ok(mut g) = self.stat.lock() {
                                        g.errors += 1;
                                    }
                                    None
                                }
                            }
                        })
                    })
                    .clone();
                drop(paused);
                let ns = t0.elapsed().as_nanos() as u64;
                if fresh { &self.load_ns } else { &self.wait_ns }.fetch_add(ns, Ordering::Relaxed);
                v
            }
        };
        if !fresh {
            return loaded;
        }
        // TK-683: ступени НОД этих суток, взятые из файла до загрузки сделок, сверяются с ними (число сделок, первая цена)
        if let Some(v) = &loaded {
            let known = self
                .gcds
                .lock()
                .ok()
                .and_then(|g| g.get(&key).cloned())
                .and_then(|s| s.get().cloned().flatten());
            if known.is_some_and(|s| !s.fits(v)) {
                self.gcd_foreign(sym, day);
                let fixed = Arc::new(GcdSteps::of(v));
                if let Some(p) = self.gcd_file(sym, day) {
                    self.gcd_write(&p, &fixed);
                }
                if let Ok(mut g) = self.gcds.lock() {
                    g.insert(key.clone(), Arc::new(OnceLock::from(Some(fixed))));
                }
            }
        }
        if let Ok(mut g) = self.lru.lock() {
            // ячейку могли вытеснить, пока сутки грузились: тогда её объём не в счёте
            if g.map.get(&key).is_some_and(|s| Arc::ptr_eq(s, &slot)) {
                g.bytes += loaded.as_ref().map_or(0, day_bytes);
            }
            g.shrink(self.cap_bytes);
        }
        loaded
    }

    fn gcd_steps(&self, sym: &str, day: usize) -> Option<Arc<GcdSteps>> {
        let slot = match self.gcds.lock() {
            Ok(mut g) => g.entry((sym.to_string(), day)).or_default().clone(),
            Err(_) => Default::default(),
        };
        if let Some(v) = slot.get() {
            return v.clone();
        }
        let paused = super::pause(day);
        slot.get_or_init(|| {
            paused.work(|| {
                let file = self.gcd_file(sym, day);
                let saved = file
                    .as_ref()
                    .and_then(|p| fs::read(p).ok())
                    .and_then(|b| GcdSteps::from_bytes(&b));
                if let Some(s) = saved {
                    // сутки уже в памяти — файл сверяется с ними; сутки, загруженные позже, сверяет `day`
                    match self.loaded(sym, day) {
                        Some(v) if !s.fits(&v) => self.gcd_foreign(sym, day),
                        _ => return Some(Arc::new(s)),
                    }
                }
                let steps = self.day(sym, day).map(|v| Arc::new(GcdSteps::of(&v)));
                if let (Some(p), Some(s)) = (&file, &steps) {
                    self.gcd_write(p, s);
                }
                steps
            })
        })
        .clone()
    }
}

// ───────────────────────────── фаза 1: сигналы ─────────────────────────────

/// Кандидат: уровень `level` жив в минуте `t`, цена уровня `lp` в длинной раме, минутка по диапазону может дать сигнал.
#[derive(Debug, Clone, Copy)]
pub struct Cand {
    pub t: u32,
    pub level: u32,
    pub lp: i64,
    /// §13.19: дальний край зоны версии цены (длинная рама); без журнала членов — равен `lp`.
    pub far: i64,
}

/// Сигнал по сделке: `ms` — время сделки от 01.09 00:00 UTC, `day`/`idx` — её место в файле суток.
#[derive(Debug, Clone, Copy)]
pub struct TSig {
    pub ms: i64,
    pub sym: u16,
    pub level: u32,
    pub dist: u32,
    pub t: u32,
    pub day: u16,
    pub idx: u32,
    /// §13.16: цена версии уровня в минуте сигнала, длинная рама (`s·цена`, ×1e8).
    pub lp: i64,
    /// §13.19: дальний край зоны версии уровня (минимум цен пивотов в длинной раме), для стопа от уровня (`ls`, `bm = 3`).
    pub far: i64,
    /// §13.13 (`ap = 2`): цена пришла в зону сверху (сигнал помечен, не отвергнут).
    pub ws: bool,
    /// TK-697 (`tn`): цена следующего уровня за пробиваемым (длинная рама) — ближайший по цене уровень того же набора строго за `lp` по ходу сделки, известный и живой
    /// на минуте сигнала `t` (`levels::next_px`); 0 — такого уровня нет. Считается всегда (фаза 1 не знает ключей выхода, кэш сигналов хранит поле).
    pub nxt: i64,
}

#[derive(Debug, Default, Clone)]
pub struct TickCtr {
    pub cands: u64,
    pub signals: [u64; 2],
    pub missing_days: u64,
    /// §13.13: сигналов, пришедших в зону сверху (при `ap = 1` отвергнуты, при `ap = 2` помечены).
    pub ap_wrong: u64,
    /// TK-669 (`fg > 0`): приостановки уровней по источнику — `pk`, `pb`, `ban` (первый сработавший по порядку в коде) и снятые приостановки.
    pub fg: [u64; 4],
}

impl TickCtr {
    pub fn add(&mut self, o: &TickCtr) {
        self.cands += o.cands;
        self.missing_days += o.missing_days;
        self.ap_wrong += o.ap_wrong;
        for h in 0..2 {
            self.signals[h] += o.signals[h];
        }
        for k in 0..4 {
            self.fg[k] += o.fg[k];
        }
    }
}

fn half_of_day(day: usize) -> usize {
    usize::from(day >= START_DAY + HALF_DAYS)
}

/// §13.14 «свежий уровень»: допуск кандидата (уровень `lv`, минута `t`), цена версии `lp` в длинной раме (`s` — сторона). По закрытым
/// минутам ≤ `t−1`. (а) последнее касание (high/low не ниже `lp − |lp|·τ`, минутки без данных не в счёт) не старше H минут,
/// окно — от бара первого пивота; (б) в минутах `t−K … t−1` не меньше `K·share/100` закрытий строго ниже `lp` и не дальше `b` %
/// от него, и ни одного закрытия на уровне или за ним. Допуск — (а) или (б); правило выключено — всегда `true`.
/// TK-668: уровень источника (`lv.src ≠ 0`, ключ `lx`) всегда допущен — у вчерашнего экстремума, недельного экстремума и POC отскоков нет, давность
/// касания не определена; ключи `fh/fk/fm/fb` в строке варианта остаются, но для таких уровней игнорируются (защита от повторных подходов — `pb`).
pub fn fresh_ok(sym: &Sym, cfg: &SigCfg, lv: &Level, s: i64, lp: i64, t: usize) -> bool {
    if lv.src != 0 || !cfg.fresh_on() {
        return true;
    }
    if cfg.fr_h_min > 0 {
        let tau = mul_div(lp.abs(), cfg.lv.tau_pm, 100_000) as i64;
        let band = lp - tau;
        let lo = (lv.first_bar as usize * cfg.lv.tf_min).max(t.saturating_sub(cfg.fr_h_min));
        let touch = |m: usize| sym.o[m] != 0 && (if s > 0 { sym.h[m] } else { -sym.l[m] }) >= band;
        if (lo..t).any(touch) {
            return true;
        }
    }
    if cfg.fr_share > 0 {
        let lim = mul_div(lp.abs(), cfg.fr_b_pm, 100_000) as i64;
        let need = cfg.fr_k * cfg.fr_share / 100;
        let mut n_in = 0usize;
        let mut beyond = false;
        for m in t.saturating_sub(cfg.fr_k)..t {
            if sym.o[m] == 0 {
                continue;
            }
            let c = s * sym.c[m];
            if c >= lp {
                beyond = true;
                break;
            }
            if lp - c <= lim {
                n_in += 1;
            }
        }
        if !beyond && n_in >= need {
            return true;
        }
    }
    false
}

/// TK-747 (`fw`, В-195, В-196, В-197) свежесть по владельцу: допуск кандидата пивотного уровня (`set` — минута постановки версии цены, `Level::fresh_at`; минута `t`), цена версии `lp` в длинной раме (`s` — сторона), `touched` —
/// после постановки уровня была минута с high не ниже `lp − допуск прокола` (по минутам ≤ `t−1`, ведёт `candidates_group_tr`). Постановка — подтверждение последнего присоединившегося (нового) пивота:
/// без нового пивота, «заметившего» старые (в окне `l` суток) пивоты, уровня и постановки нет. Допуск — все условия сразу: (1) `t − постановка ≥ fw`; (2) было касание; (3) проторговка в окне `[max(постановка, t − fk), t)`: не меньше `доля fm` закрытий строго ниже `lp` и не дальше `fb` от него, ни одного закрытия
/// на уровне или за ним (минуты без данных пропускаются, как в `fresh_ok`). Уровни источника (`src ≠ 0`) допущены всегда — вызывающий их не проверяет.
pub fn fresh_fw_ok(
    sym: &Sym,
    cfg: &SigCfg,
    set: usize,
    s: i64,
    lp: i64,
    touched: bool,
    t: usize,
) -> bool {
    if t < set + cfg.fw_min {
        FW_REJECT[0].fetch_add(1, Ordering::Relaxed);
        return false;
    }
    if !touched {
        FW_REJECT[1].fetch_add(1, Ordering::Relaxed);
        return false;
    }
    let lim = mul_div(lp.abs(), cfg.fr_b_pm, 100_000) as i64;
    // окно `[t − fk, t)` (критик 15, В-197): допуск пересчитывается на каждой минуте — повторный подход через сутки заново набирает проторговку и касание
    let from = t.saturating_sub(cfg.fr_k);
    let (mut n_in, mut n_data) = (0usize, 0usize);
    for m in from..t {
        if sym.o[m] == 0 {
            continue;
        }
        n_data += 1;
        let c = s * sym.c[m];
        if c >= lp {
            FW_REJECT[2].fetch_add(1, Ordering::Relaxed);
            return false;
        }
        if lp - c <= lim {
            n_in += 1;
        }
    }
    // доля — от минут с данными (пустые минуты долю не занижают)
    let ok = n_data > 0 && n_in * 100 >= n_data * cfg.fr_share;
    if !ok {
        FW_REJECT[2].fetch_add(1, Ordering::Relaxed);
    }
    ok
}

/// Кандидаты (§13.2 п. 1): состояние уровня (запрет, ложный пробой) — по закрытиям минут ≤ `t−1`; пул — по
/// закрытым минутам ≤ `t−1`; минутка по диапазону может дать сделку в зоне входа под уровнем. Затем состояние
/// обновляется минуткой `t` (как в `signals::scan`).
pub fn candidates(sym: &Sym, levels: &[Level], cfg: &SigCfg, side: Side, out: &mut Vec<Cand>) {
    candidates_fg(sym, levels, cfg, side, out, &mut [0; 4]);
}

/// `candidates` со счётчиками `fg` (TK-669): `fgc` = `[приостановок от pk, от pb, от ban, снятых]`; при `fg = 0` не меняется.
///
/// `fg > 0` («закол прощаем после отката X NATR»): убийцы уровня `ban`, `pk`, `pb` приостанавливают уровень (минута `ts`, действует со следующей),
/// а не убивают его. Приостановка снимается на первой минуте `t > ts`, чей low (в раме стороны) ≤ `lp − lp·NATR[t−1]·X` (NATR нет — не снимается):
/// со следующей минуты уровень снова даёт кандидатов, память касания (`pend`) и метка `pk` сброшены. Минута, снявшая приостановку, сама проверяется
/// убийцами заново (широкая минута с откатом и новым проколом — новая приостановка). Повторное срабатывание убийцы на приостановленном уровне
/// ни время приостановки, ни счётчик не меняет.
pub fn candidates_fg(
    sym: &Sym,
    levels: &[Level],
    cfg: &SigCfg,
    side: Side,
    out: &mut Vec<Cand>,
    fgc: &mut [u64; 4],
) {
    let mut outs = [std::mem::take(out)];
    let mut fg = [[0u64; 4]];
    candidates_group(sym, levels, &[cfg], side, &mut outs, &mut fg);
    let [o] = outs;
    *out = o;
    for (a, b) in fgc.iter_mut().zip(fg[0]) {
        *a += b;
    }
}

/// TK-683: ключ состояния уровня в `candidates_group` — `SigCfg` без полей, от которых зависит только выдача кандидата
/// (живость, обороты, зона входа, «свежий уровень», цвет, `ap`). Задания с равным ключом и стороной делят один проход по минутам.
pub fn level_state_key(cfg: &SigCfg) -> SigCfg {
    SigCfg {
        natr_min_pm: 0,
        t1h_u: 0,
        t24_u: 0,
        track_pm: 0,
        e_pm: 0,
        color: false,
        ap: 0,
        fr_h_min: 0,
        fr_k: 0,
        fr_share: 0,
        fr_b_pm: 0,
        fw_min: 0,
        nf: 0,
        ev: 0,
        ea_m: 0,
        el_pm: 0,
        eh_pm: 0,
        vx_pm: 0,
        hr_min: 0,
        hw_min: 0,
        ..cfg.clone()
    }
}

/// Условие выдачи кандидата одного задания группы (`candidates_group`).
struct Emit<'a> {
    cfg: &'a SigCfg,
    /// Номер задания в группе.
    k: usize,
    /// TK-681 (`nf`): ряд фильтра живости — NATR `natr_p` или источник волатильности.
    live: &'a [i32],
    /// TK-681 (`ev`): ряд зоны входа.
    evol: Option<&'a [i32]>,
    natr_min: i64,
    /// TK-738 (`vx`): ряд RV60 (`vol_for(8)`) и потолок ×1e8; `None` — потолка нет.
    vx: Option<(&'a [i32], i64)>,
    /// TK-738 (`hr`, `hw`): начало и ширина окна часа UTC, минуты суток; `None` — окна нет (`hw = 0` или на все сутки).
    hour: Option<(usize, usize)>,
    /// Зона входа ×1e8: постоянная, при `ev ≠ 0` — её верхняя граница (зажим `eh`, `track`); `None` — границы нет.
    dmax0: Option<i64>,
    /// Нижняя граница зоны входа при `dmax0` для текущей версии цены уровня.
    zone_lo0: i64,
}

/// `candidates_fg` для группы заданий `cfgs` с одним состоянием уровня (`level_state_key` и сторона равны): состояние (запрет, ложный пробой,
/// приостановка) ведётся один раз, кандидаты выдаются каждому заданию по его условию — в `outs[k]`, счётчики `fg` — в `fgc[k]`.
/// Результат каждого задания тот же, что у отдельного прохода; счётчики `pk` / `pb` растут на число заданий группы.
#[allow(clippy::needless_range_loop)] // t индексирует сразу несколько рядов монеты
pub fn candidates_group(
    sym: &Sym,
    levels: &[Level],
    cfgs: &[&SigCfg],
    side: Side,
    outs: &mut [Vec<Cand>],
    fgc: &mut [[u64; 4]],
) {
    candidates_group_tr(sym, levels, cfgs, side, outs, fgc, None);
}

/// `candidates_group` с журналом запретов уровней (TK-743): `trace` получает `(номер уровня, минута, причина)` — первую причину запрета (`ban`, `pk`, `pb`) каждого
/// перехода уровня в «запрещён»; на кандидатов и счётчики не влияет.
#[allow(clippy::needless_range_loop)] // t индексирует сразу несколько рядов монеты
pub fn candidates_group_tr(
    sym: &Sym,
    levels: &[Level],
    cfgs: &[&SigCfg],
    side: Side,
    outs: &mut [Vec<Cand>],
    fgc: &mut [[u64; 4]],
    mut trace: Option<&mut Vec<(u32, u32, &'static str)>>,
) {
    let s = side.s();
    let Some(&cfg) = cfgs.first() else {
        return;
    };
    let Some(natr) = sym.natr_for(cfg.natr_p) else {
        return;
    };
    // задание без ряда источника (`nf`, `ev`) кандидатов и счётчиков не даёт
    let mut em: Vec<Emit> = cfgs
        .iter()
        .enumerate()
        .filter_map(|(k, &c)| {
            let live = if c.nf == 0 { natr } else { sym.vol_for(c.nf)? };
            let track = c.track_pm * 1000;
            let (evol, dmax0) = if c.ev == 0 {
                (None, Some((c.e_pm * 1000).min(track)))
            } else {
                let ub = (c.eh_pm >= 0 && track >= 0).then(|| (c.eh_pm * 1000).min(track));
                (Some(sym.vol_for(c.ev)?), ub)
            };
            // TK-738: потолок RV60 — ряд источника 8, нет ряда — задания нет (как у `nf`, `ev`)
            let vx = if c.vx_pm != 0 {
                Some((sym.vol_for(8)?, c.vx_pm * 1000))
            } else {
                None
            };
            let hour =
                (c.hw_min > 0 && c.hw_min < 1440).then_some((c.hr_min as usize, c.hw_min as usize));
            Some(Emit {
                cfg: c,
                k,
                live,
                evol,
                natr_min: c.natr_min_pm * 1000,
                vx,
                hour,
                dmax0,
                zone_lo0: i64::MIN,
            })
        })
        .collect();
    if em.is_empty() {
        return;
    }
    let n_jobs = em.len() as u64;
    let mut fg = [0u64; 4];
    let zone_lo_of = |lp: i64, dmax: i64| lp - mul_div(lp.abs(), dmax + 1, SCALE) as i64 - 1;
    // TK-747 (`pp`): шаг цены монеты для допуска прокола (1 шаг ≤ 0,05 % цены)
    let tick = if cfg.lv.pp { sym.tick() } else { 0 };
    // пороги, зависящие только от цены версии уровня, считаются при смене версии, а не на каждой минуте:
    // нижние границы зон входа заданий (возвращается наименьшая), порог возврата `ban`, порог прокола `pk`
    let marks = |lp: i64, em: &mut [Emit]| {
        let mut zone_min = i64::MAX;
        for e in em.iter_mut() {
            e.zone_lo0 = e.dmax0.map_or(i64::MIN, |d| zone_lo_of(lp, d));
            zone_min = zone_min.min(e.zone_lo0);
        }
        (
            zone_min,
            lp - mul_div(lp.abs(), cfg.f_pm, 100_000) as i64,
            lp + if cfg.lv.pp {
                pierce_tol(tick, lp)
            } else {
                mul_div(lp.abs(), cfg.lv.tau_pm, 100_000) as i64
            },
        )
    };
    for (li, lv) in levels.iter().enumerate() {
        let end = (lv.end as usize).min(N_MIN);
        let mut seg = 0;
        let mut price = lv.segs[0].1;
        let mut far = lv.far_edge(0, s);
        let (mut zone_min, mut back, mut pk_thr) = marks(s * price, &mut em);
        let mut pend: Option<usize> = None;
        let mut forbidden = false;
        let mut pk_dead = false;
        // TK-747 (`fw`): после постановки версии цены была минута с high не ниже `lp − τ` (по минутам < t); при смене версии пересчитывается по окну `[постановка, t)`
        let fw_on = em.iter().any(|e| e.cfg.fw_min > 0);
        // TK-747 (В-197, критик б): касание — подход к самой цене уровня: high не ниже `lp − допуск прокола` (1 шаг цены, если он ≤ 0,05 % цены; иначе нужен high ≥ lp)
        let ftick = if fw_on { sym.tick() } else { 0 };
        let touch_band = |lp: i64| lp - pierce_tol(ftick, lp);
        let touch_hi = |m: usize| {
            if s > 0 {
                sym.h[m]
            } else {
                -sym.l[m]
            }
        };
        // TK-747 (`fw`): минута последнего касания (по минутам < t); допуск требует касания в последних `fk` минутах
        let fk_max = em.iter().map(|e| e.cfg.fr_k).max().unwrap_or(0);
        let last_before = |t: usize, band: i64| -> Option<usize> {
            (t.saturating_sub(fk_max)..t)
                .rev()
                .find(|&m| sym.o[m] != 0 && touch_hi(m) >= band)
        };
        let mut last_touch: Option<usize> = None;
        if fw_on {
            last_touch = last_before(lv.born as usize, touch_band(s * price));
        }
        // TK-669 (`fg`): минута приостановки уровня
        let mut susp: Option<usize> = None;
        for t in lv.born as usize..end {
            let seg0 = seg;
            while seg + 1 < lv.segs.len() && lv.segs[seg + 1].0 as usize <= t {
                seg += 1;
                price = lv.segs[seg].1;
                far = lv.far_edge(seg, s);
                (zone_min, back, pk_thr) = marks(s * price, &mut em);
            }
            if fw_on && seg != seg0 {
                last_touch = last_before(t, touch_band(s * price));
            }
            if sym.o[t] == 0 {
                continue;
            }
            let lp = s * price;
            let (hi, lo) = if s > 0 {
                (sym.h[t], sym.l[t])
            } else {
                (-sym.l[t], -sym.h[t])
            };
            // минута ниже самой широкой зоны группы кандидата не даёт ни одному заданию
            if t >= START_MIN && !forbidden && lo < lp && hi >= zone_min {
                for e in &em {
                    // зона входа минуты: постоянная или от волатильности закрытой минутки `t−1` (`ev`); нет значения — кандидата нет
                    let zone_lo = match e.evol {
                        None => e.zone_lo0,
                        Some(v) => match e.cfg.zone_e8(i64::from(v[t - 1])) {
                            Some(dmax) => zone_lo_of(lp, dmax),
                            None => continue,
                        },
                    };
                    let nv = i64::from(e.live[t - 1]);
                    if hi >= zone_lo
                        && nv >= 0
                        && nv >= e.natr_min
                        && e.vx.is_none_or(|(v, cap)| {
                            let x = i64::from(v[t - 1]);
                            x >= 0 && x <= cap
                        })
                        && e.hour
                            .is_none_or(|(hr, hw)| (t % 1440 + 1440 - hr) % 1440 < hw)
                        && sym.pq[t] - sym.pq[t - 60] >= e.cfg.t1h_u
                        && (e.cfg.t24_u == 0 || sym.pq[t] - sym.pq[t - 1440] >= e.cfg.t24_u)
                    {
                        let fresh = if e.cfg.fw_min > 0 {
                            lv.src != 0
                                || fresh_fw_ok(
                                    sym,
                                    e.cfg,
                                    lv.fresh_at(seg),
                                    s,
                                    lp,
                                    last_touch.is_some_and(|m| m + e.cfg.fr_k >= t),
                                    t,
                                )
                        } else {
                            fresh_ok(sym, e.cfg, lv, s, lp, t)
                        };
                        if fresh {
                            outs[e.k].push(Cand {
                                t: t as u32,
                                level: li as u32,
                                lp,
                                far: far.unwrap_or(lp),
                            });
                        } else {
                            FR_REJECTED.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
            }
            if fw_on && hi >= touch_band(lp) {
                last_touch = Some(t);
            }
            // TK-669 (`fg`): откат на X NATR за уровень на минуте после приостановки — уровень возвращается к сигналам со следующей минуты
            if let Some(ts) = susp {
                let nv = i64::from(natr[t - 1]);
                if t > ts
                    && nv >= 0
                    && lo <= lp - mul_div(lp.abs(), nv * cfg.fg_m, SCALE * 1000) as i64
                {
                    susp = None;
                    forbidden = false;
                    pk_dead = false;
                    pend = None;
                    fg[3] += 1;
                }
            }
            if let Some(tc) = pend {
                if t - tc > cfg.r_min {
                    pend = None;
                }
            }
            if hi > lp && pend.is_none() {
                pend = Some(t);
            }
            if cfg.ban && pend.is_some() && s * sym.c[t] < back {
                if cfg.fg_m > 0 && susp.is_none() {
                    susp = Some(t);
                    fg[2] += 1;
                }
                if !forbidden {
                    if let Some(tr) = trace.as_mut() {
                        tr.push((li as u32, t as u32, "ban"));
                    }
                }
                forbidden = true;
            }
            // §13.11 (`pk`): прокол цены версии больше чем на τ убивает уровень навсегда, со следующей минуты
            // TK-747 (`pp`, В-197): любой минутный прокол дальше допуска (1 шаг ≤ 0,05 %) — то же убийство, порог `lp + допуск`
            if (cfg.lv.pk || cfg.lv.pp) && !pk_dead && hi > pk_thr {
                pk_dead = true;
                if !forbidden {
                    PK_MINUTE_KILLED.fetch_add(n_jobs, Ordering::Relaxed);
                    if let Some(tr) = trace.as_mut() {
                        tr.push((li as u32, t as u32, if cfg.lv.pp { "pp" } else { "pk" }));
                    }
                }
                if cfg.fg_m > 0 && susp.is_none() {
                    susp = Some(t);
                    fg[0] += 1;
                }
                forbidden = true;
            }
            // §13.13 (`pb`): сделка минуты на уровне или за ним (high ≥ цены версии) убивает уровень со следующей минуты
            if cfg.pb && hi >= lp {
                if !forbidden {
                    PB_KILLED.fetch_add(n_jobs, Ordering::Relaxed);
                    if let Some(tr) = trace.as_mut() {
                        tr.push((li as u32, t as u32, "pb"));
                    }
                }
                if cfg.fg_m > 0 && susp.is_none() {
                    susp = Some(t);
                    fg[1] += 1;
                }
                forbidden = true;
            }
        }
    }
    for e in &em {
        for (a, b) in fgc[e.k].iter_mut().zip(fg) {
            *a += b;
        }
    }
}

/// Первая сделка среди `ticks[from..to]` (сделки одной минуты), дающая сигнал: ни одна сделка минуты до неё
/// включительно не дошла до уровня (длинная рама: цена < `lp`), расстояние `(lp − цена)/lp ≤ dmax`, цвет (если
/// включён) — цена выше цены первой сделки минуты. Возвращает (индекс в `ticks`, расстояние ×1e8).
pub fn first_signal(
    ticks: &[Tick],
    from: usize,
    to: usize,
    s: i64,
    lp: i64,
    dmax: i64,
    color: bool,
) -> Option<(usize, u32)> {
    let mut hi = i64::MIN;
    let mut op = 0i64;
    for (k, tk) in ticks.iter().enumerate().take(to).skip(from) {
        let p = s * tk.px;
        if k == from {
            op = p;
        }
        hi = hi.max(p);
        if hi >= lp {
            return None;
        }
        let dist = mul_div(lp - p, SCALE, lp.abs()) as i64;
        if dist > dmax || (color && p <= op) {
            continue;
        }
        return Some((k, dist as u32));
    }
    None
}

/// Границы сделок минуты `t` в `ticks` суток (по времени).
pub fn minute_range(ticks: &[Tick], t: usize) -> (usize, usize) {
    let start = ((t % 1440) as i64 * MIN_MS) as u32;
    let end = start + MIN_MS as u32;
    (
        ticks.partition_point(|x| x.off() < start),
        ticks.partition_point(|x| x.off() < end),
    )
}

/// TK-681: зона входа (доля цены ×1e8) кандидата минуты `t` — `SigCfg::zone_e8` по закрытой минутке `t−1`.
pub fn zone_at(sym: &Sym, cfg: &SigCfg, t: usize) -> Option<i64> {
    let v = if cfg.ev == 0 || t == 0 {
        -1
    } else {
        sym.vol_for(cfg.ev).map_or(-1, |v| i64::from(v[t - 1]))
    };
    cfg.zone_e8(v)
}

/// Сигналы одной монеты для всех заданий `(SigCfg, сторона)`: сделки каждых суток читаются один раз.
pub fn signals_for_coin(
    sym: &Sym,
    si: u16,
    jobs: &[(SigCfg, Side)],
    src: &dyn TradeSrc,
    outs: &mut [Vec<TSig>],
    ctrs: &mut [TickCtr],
) {
    let mut lev: HashMap<(LevelCfg, Side), Vec<Level>> = HashMap::new();
    let mut by_day: BTreeMap<usize, Vec<(usize, Cand)>> = BTreeMap::new();
    // TK-683: задания с одним состоянием уровня (`level_state_key`, сторона) идут по минутам одним проходом
    let mut groups: Vec<Vec<usize>> = Vec::new();
    let mut group_of: HashMap<(SigCfg, Side), usize> = HashMap::new();
    for (j, (cfg, side)) in jobs.iter().enumerate() {
        let g = *group_of
            .entry((level_state_key(cfg), *side))
            .or_insert_with(|| {
                groups.push(Vec::new());
                groups.len() - 1
            });
        groups[g].push(j);
    }
    let mut cands: Vec<Vec<Cand>> = jobs.iter().map(|_| Vec::new()).collect();
    for g in &groups {
        let (cfg, side) = &jobs[g[0]];
        let levels = lev
            .entry((cfg.lv.clone(), *side))
            .or_insert_with(|| build_levels_far(sym, &cfg.lv, *side));
        let cfgs: Vec<&SigCfg> = g.iter().map(|&j| &jobs[j].0).collect();
        let mut outs_g: Vec<Vec<Cand>> = g.iter().map(|_| Vec::new()).collect();
        let mut fg = vec![[0u64; 4]; g.len()];
        candidates_group(sym, levels, &cfgs, *side, &mut outs_g, &mut fg);
        for ((&j, cs), f) in g.iter().zip(outs_g).zip(fg) {
            cands[j] = cs;
            for (a, b) in ctrs[j].fg.iter_mut().zip(f) {
                *a += b;
            }
        }
    }
    // порядок кандидатов суток — по номеру задания, как при отдельных проходах
    for (j, cs) in cands.into_iter().enumerate() {
        ctrs[j].cands += cs.len() as u64;
        for c in cs {
            by_day.entry(c.t as usize / 1440).or_default().push((j, c));
        }
    }
    for (day, list) in by_day {
        let Some(ticks) = src.day(&sym.name, day) else {
            for (j, _) in &list {
                ctrs[*j].missing_days += 1;
            }
            continue;
        };
        for (j, c) in list {
            let (cfg, side) = &jobs[j];
            let (from, to) = minute_range(&ticks, c.t as usize);
            // TK-681 (`ev`): зона — та же, что дала кандидата (волатильность закрытой минутки `t−1`)
            let dmax = zone_at(sym, cfg, c.t as usize).unwrap_or(0);
            if let Some((k, dist)) = first_signal(&ticks, from, to, side.s(), c.lp, dmax, cfg.color)
            {
                let mut ws = false;
                if cfg.ap != 0
                    && came_from_above(src, &sym.name, day, &ticks, k, side.s(), (c.lp, dmax))
                {
                    ctrs[j].ap_wrong += 1;
                    if cfg.ap == 1 {
                        continue;
                    }
                    ws = true;
                }
                outs[j].push(TSig {
                    ms: day as i64 * DAY_MS + i64::from(ticks[k].off()),
                    sym: si,
                    level: c.level,
                    dist,
                    t: c.t,
                    day: day as u16,
                    idx: k as u32,
                    lp: c.lp,
                    far: c.far,
                    ws,
                    nxt: super::levels::next_px(
                        &lev[&(cfg.lv.clone(), *side)],
                        side.s(),
                        c.lp,
                        c.t as usize,
                    ),
                });
                ctrs[j].signals[half_of_day(day)] += 1;
            }
        }
    }
}

/// §13.13 (`ap`): сигнал по сделке `k` суток `day` пришёл в зону сверху? Назад по сделкам монеты (не дальше
/// `AP_LOOKBACK_DAYS` суток): решает первая сделка, которая дошла до уровня (`p ≥ lp` — цена пришла сверху → `true`)
/// или стоит ниже зоны (`dist > dmax` — подход снизу → `false`); сделки внутри зоны не решают; решающей нет → `false`.
/// `zone` = (цена уровня в длинной раме, `dmax` ×1e8).
pub fn came_from_above(
    src: &dyn TradeSrc,
    name: &str,
    day: usize,
    ticks: &[Tick],
    k: usize,
    s: i64,
    zone: (i64, i64),
) -> bool {
    let (lp, dmax) = zone;
    let decide = |tk: &Tick| -> Option<bool> {
        let p = s * tk.px;
        if p >= lp {
            return Some(true);
        }
        let dist = mul_div(lp - p, SCALE, lp.abs()) as i64;
        (dist > dmax).then_some(false)
    };
    if let Some(r) = ticks[..k].iter().rev().find_map(decide) {
        return r;
    }
    for back in 1..=AP_LOOKBACK_DAYS {
        let Some(d) = day.checked_sub(back) else {
            break;
        };
        if let Some(v) = src.day(name, d) {
            if let Some(r) = v.iter().rev().find_map(decide) {
                return r;
            }
        }
    }
    false
}

/// Порядок обработки (§13.2): время сделки, расстояние до уровня, монета по алфавиту, уровень.
pub fn sort_tsigs(v: &mut [TSig]) {
    v.sort_unstable_by_key(|s| (s.ms, s.dist, s.sym, s.level));
}

// ───────────────────────────── фаза 2: исход сделки ─────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TLeg {
    pub ms: i64,
    /// Настоящая цена исполнения (не рама).
    pub px: i64,
    pub u: i64,
    pub maker: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TickOut {
    pub entry_ms: i64,
    pub entry_px: i64,
    pub legs: Vec<TLeg>,
    pub exit_ms: i64,
    pub kind: ExitKind,
    pub takes: u32,
    /// Цена последней сделки до 00:00 UTC конца суток `g` (настоящая цена), для суток входа … суток перед выходом.
    pub marks: Vec<(usize, i64)>,
    /// v22 (`tl`): выставленный лимитный приказ трейла — (мс срабатывания, настоящая цена ×1e8); `None` — приказа не было (`tl = 0` или трейл не срабатывал).
    pub tl: Option<(i64, i64)>,
    /// TK-681 (`sm`): стоп от уровня (`sl`, `cw`) или порог `nx` на входе — [сдвинут нижним зажимом `sm`, прежний зажим `вход − 1e-8` сработал бы].
    pub floor: [bool; 2],
    /// TK-743 (`--keep-orders`): журнал заявок лота — поставлена / сдвинута / исполнена / снята; без ключа пуст (память не выделяется).
    pub ord: Vec<OrdEv>,
}

/// Множитель ступени `i` лестницы тейков в тысячных NATR (§13.12): `t_low + (t_high − t_low)·(i/(n−1))^γ`; γ = 1 — прежняя
/// целая арифметика, концы лестницы не двигаются при любом γ.
pub fn tp_mult_m(ex: &ExitCfg, i: i64, n: i64) -> i64 {
    if ex.tp_gamma_m == 1000 || i == 0 || i == n - 1 {
        return ex.t_low_m + (ex.t_high_m - ex.t_low_m) * i / (n - 1);
    }
    let x = (i as f64 / (n - 1) as f64).powf(ex.tp_gamma_m as f64 / 1000.0);
    ex.t_low_m + ((ex.t_high_m - ex.t_low_m) as f64 * x).round() as i64
}

/// Доли ступеней в мкУЕ (§13.12): вес ∝ `1 + κ·i/(n−1)`, сумма — `q` от позиции, остаток целого деления — последней ступени;
/// κ = 0 — прежний равный лот.
pub fn tp_lots(ex: &ExitCfg) -> Vec<i64> {
    tp_lots_n(ex, NOTIONAL_U)
}

/// То же для лота размера `notional` (мкУЕ): §13.19 — два входа по 50 %.
pub fn tp_lots_n(ex: &ExitCfg, notional: i64) -> Vec<i64> {
    let n = ex.n_tp as i64;
    if ex.tp_kappa_m == 0 {
        return vec![notional * ex.q_pct / (100 * n); ex.n_tp];
    }
    let total = notional * ex.q_pct / 100;
    let w: Vec<i128> = (0..n)
        .map(|i| i128::from(1000 * (n - 1) + ex.tp_kappa_m * i))
        .collect();
    let sw: i128 = w.iter().sum();
    let mut lots: Vec<i64> = w
        .iter()
        .map(|&x| (i128::from(total) * x / sw) as i64)
        .collect();
    let done: i64 = lots.iter().sum();
    if let Some(last) = lots.last_mut() {
        *last += total - done;
    }
    lots
}

/// TK-697 (`tn`): план тейков позиции с первым тейком у следующего уровня (определение — у `ExitCfg::tn_pct`): цены (рама), лоты (мкУЕ) и исходные номера
/// ступеней лестницы (для пересчёта `tj`; у тейка у уровня — 0).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NextTake {
    pub tps: Vec<i64>,
    pub lots: Vec<i64>,
    pub steps: Vec<usize>,
}

/// TK-697 (`tn`): план тейков лота `notional` (мкУЕ) с входом по `e` (рама) — `nxt` цена следующего уровня (рама; 0 — нет), `off` отступ внутрь (цена), `ladder` — цены ступеней
/// обычной лестницы (рама, по возрастанию). `None` — откат на обычную лестницу: ключ выключен, следующего уровня нет, тейк `nxt − off` не выше цены входа или дальше
/// внешней ступени лестницы. Иначе: тейк у уровня — лот `tn` % и ступени лестницы №2…n с ценой строго выше него, в тейки уходит `q` % остатка по весам `tpk` (нормированы по оставшимся
/// ступеням, остаток целого деления — последней); `tn = 100` — ступеней нет. Полное определение — у `ExitCfg::tn_pct`.
pub fn next_take_plan(
    ex: &ExitCfg,
    nxt: i64,
    e: i64,
    off: i64,
    ladder: &[i64],
    notional: i64,
) -> Option<NextTake> {
    if ex.tn_pct <= 0 || nxt == 0 {
        return None;
    }
    let outer = *ladder.last()?;
    let tp = nxt - off;
    if tp <= e || tp > outer {
        return None;
    }
    let first = notional * ex.tn_pct / 100;
    let rest = notional - first;
    let kept: Vec<usize> = if rest > 0 {
        (1..ladder.len()).filter(|&i| ladder[i] > tp).collect()
    } else {
        Vec::new()
    };
    // веса ступеней — как в `tp_lots_n`: `1000·(n−1) + κ·i` по исходному номеру ступени
    let n = ladder.len() as i64;
    let w = |i: usize| i128::from(1000 * (n - 1) + ex.tp_kappa_m * i as i64);
    let sw: i128 = kept.iter().map(|&i| w(i)).sum();
    let budget = rest * ex.q_pct / 100;
    let mut lots = vec![first];
    lots.extend(
        kept.iter()
            .map(|&i| (i128::from(budget) * w(i) / sw) as i64),
    );
    if !kept.is_empty() {
        let done: i64 = lots[1..].iter().sum();
        if let Some(last) = lots.last_mut() {
            *last += budget - done;
        }
    }
    let mut tps = vec![tp];
    tps.extend(kept.iter().map(|&i| ladder[i]));
    let mut steps = vec![0usize];
    steps.extend(kept);
    Some(NextTake { tps, lots, steps })
}

/// NATR выхода (e8) для сигнала на минуте `t` (§13.2 п. 3; `ns`, §13.12): закрытая минутка `t−1` или, при `ns = 1`, `t`.
pub fn exit_natr(sym: &Sym, ex: &ExitCfg, t: usize) -> i64 {
    sym.natr_for(ex.natr_p)
        .map_or(-1, |v| i64::from(v[t - 1 + usize::from(ex.natr_src)]))
}

/// Цена версии уровня на сигнале и дальний край зоны (длинная рама) — для выходов от уровня (§13.19).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct LevelCtx {
    pub lp: i64,
    pub far: i64,
    /// §13.31 (`cw`): диапазон (минимум low, максимум high; длинная рама) закрытых минут `t−W … t−1` до сигнальной; `None` — не включено или данных меньше половины окна.
    pub rng: Option<(i64, i64)>,
    /// TK-676 (`vf`): волатильность стопа `V` и режимная `V_z` на закрытой минутке `t−1` (доля цены ×1e8, −1 — нет значения или `vf = 0`).
    pub vol: i64,
    pub volz: i64,
    /// TK-697 (`tn`): цена следующего уровня (длинная рама), `TSig::nxt`; 0 — нет.
    pub nxt: i64,
}

/// TK-676: значение источника волатильности `src` (коды `vs`) на закрытой минутке `t−1` сигнала в минуту `t`; −1 — нет значения.
fn vol_at(sym: &Sym, src: u8, t: usize) -> i64 {
    sym.vol_for(src).map_or(-1, |v| i64::from(v[t - 1]))
}

/// §13.31: диапазон проторговки — минимум low и максимум high (длинная рама, `s` — сторона) закрытых минут `t−w … t−1` с данными; минуты без данных пропускаются;
/// диапазона нет (`None`), если минут с данными меньше `⌈w/2⌉` или `w = 0`. Причинно: сигнальная минута `t` и позже не читаются.
pub fn cons_range(sym: &Sym, s: i64, t: usize, w: usize) -> Option<(i64, i64)> {
    if w == 0 {
        return None;
    }
    let (mut lo, mut hi, mut n) = (i64::MAX, i64::MIN, 0usize);
    for m in t.saturating_sub(w)..t {
        if sym.o[m] == 0 {
            continue;
        }
        let (h, l) = if s > 0 {
            (sym.h[m], sym.l[m])
        } else {
            (-sym.l[m], -sym.h[m])
        };
        hi = hi.max(h);
        lo = lo.min(l);
        n += 1;
    }
    (n > 0 && 2 * n >= w).then_some((lo, hi))
}

/// Контекст уровня сигнала для выходов от уровня (§13.19, §13.31).
pub fn level_ctx(sym: &Sym, side: Side, sg: &TSig, ex: &ExitCfg) -> LevelCtx {
    LevelCtx {
        lp: sg.lp,
        far: sg.far,
        rng: cons_range(sym, side.s(), sg.t as usize, ex.cs_w as usize),
        vol: if ex.vf != 0 {
            vol_at(sym, ex.vs, sg.t as usize)
        } else {
            -1
        },
        volz: if ex.vz != 0 {
            vol_at(sym, ex.vz, sg.t as usize)
        } else {
            -1
        },
        nxt: sg.nxt,
    }
}

const NONE_MS: i64 = i64::MIN;

/// §13.21 (`sn`): NATR-стоп лота. Расстояние `d = clamp(k0·NATR·g, 0,5 %, S_жёсткий)` от цены входа; NATR — закрытая минутка `t−1`
/// (`exit_natr` при `ns = 0`), заморожен на входе. Уровень стопа пересчитывается на каждой сделке (`g` зависит от времени и числа тейков `j`).
struct NatrStop {
    k0_m: i64,
    sg: u8,
    sb_m: i64,
    sr_m: i64,
    sd_m: i64,
    /// `|e|·NATR` (e8): расстояние в NATR → цена = `unit·x/(SCALE·1000)` при `x` в тысячных NATR.
    unit: i128,
    /// Зажим дистанции: 0,5 % и жёсткий стоп S от цены входа (цена ×1e8).
    lo: i64,
    hi: i64,
    entry_ms: i64,
}

/// Доля NATR-стопа в 0,5 % цены входа (нижний зажим), тысячные процента.
const NATR_STOP_MIN_PM: i64 = 500;
const HOUR_MS: i128 = 3_600_000;

impl NatrStop {
    /// Расстояние стопа от цены входа (цена ×1e8) на сделке в `ms`, когда исполнено `j` тейков.
    fn dist(&self, ms: i64, j: usize) -> i64 {
        let nat = self.unit * i128::from(self.k0_m) / (i128::from(SCALE) * 1000);
        let d = match self.sg {
            1 => {
                let dt = i128::from((ms - self.entry_ms).max(0));
                nat * (1000 * HOUR_MS + i128::from(self.sb_m) * dt) / (1000 * HOUR_MS)
            }
            2 => {
                let g = (self.sr_m as f64 / 1000.0).powi(j.min(i32::MAX as usize) as i32);
                (nat as f64 * g) as i128
            }
            3 => nat * (1000 + i128::from(self.sd_m) * j as i128) / 1000,
            _ => nat,
        };
        // верхняя граница главнее нижней: при S < 0,5 % жёсткий стоп и так ближе
        d.max(i128::from(self.lo)).min(i128::from(self.hi)) as i64
    }
}

/// §13.21 (`tj`): пересчёт оставшихся ступеней лестницы тейков после исполнения тейков.
struct StepLadder {
    delta_m: i64,
    /// Исходные множители ступеней, тысячные NATR (`tp_mult_m`).
    mults: Vec<i64>,
    unit: i128,
}

/// Состояние позиции в длинной раме; сделки подаются по одной.
struct Pos {
    e: i64,
    tps: Vec<i64>,
    lots: Vec<i64>,
    a_dist: i64,
    r_dist: i64,
    be_cfg: bool,
    rem: i64,
    stop_base: i64,
    be_on: bool,
    armed: bool,
    trail: i64,
    maxp: i64,
    next_tp: usize,
    legs: Vec<TLeg>,
    s: i64,
    /// §13.19: безубыток (режим `bm`) и его стоп «за уровнем».
    be_mode: u8,
    be_lvl: Option<i64>,
    /// §13.19: правила от уровня — цена уровня (рама), выход по времени (мс; 0 — нет) и глубина «пробоя», возврат (глубина, мс; 0 — нет).
    rules: bool,
    lp: i64,
    tx_ms: i64,
    tx_x: i64,
    rt_d: i64,
    rt_ms: i64,
    touched_ms: i64,
    broke: bool,
    below_since: i64,
    /// §13.31 (`nx`): до касания цена ≤ этого порога (рама) — выход «сценарий отменён»; `i64::MIN` — выкл.
    nx_lvl: i64,
    /// TK-649 (`tu`): срок касания — `вход + T` (мс); `i64::MAX` — выкл.
    tu_due: i64,
    /// §13.21: NATR-стоп (`sn`) и шаг лестницы тейков (`tj`); `None` — выкл.
    natr_stop: Option<NatrStop>,
    step: Option<StepLadder>,
    /// v22 (`tl`): лимитный приказ трейла стоит — тейки сняты, трейл заморожен, остаются рыночные стопы и выходы от уровня.
    lim: bool,
    /// TK-669 (`trp`): трейл включается первым исполненным тейком (откат `r_dist` — уже в цене), а не ходом `+a` NATR.
    trp: bool,
    /// TK-681 (`sm`): см. `TickOut::floor`.
    floor: [bool; 2],
    /// TK-743: журнал заявок лота (`ExitCfg::keep_orders`); выключен — пуст, память не выделяется.
    ord: Vec<OrdEv>,
    ord_on: bool,
    /// Последняя записанная цена действующего стопа (рама) и остаток, под который он стоит.
    ord_stop: i64,
    ord_rem: i64,
}

impl Pos {
    #[allow(clippy::too_many_arguments)]
    fn new(
        s: i64,
        e: i64,
        p0: i64,
        entry_ms: i64,
        natr_e8: i64,
        ex: &ExitCfg,
        lc: Option<LevelCtx>,
        notional: i64,
        stop_px: Option<i64>,
    ) -> Pos {
        let ea = e.abs();
        let nat = |x_m: i64| -> i64 {
            (i128::from(ea) * i128::from(natr_e8) * i128::from(x_m) / (i128::from(SCALE) * 1000))
                as i64
        };
        let n = ex.n_tp as i64;
        // TK-681 (`sm`): цена от уровня не ближе `sm` % от входа (расстояние округлено от входа); `sm = 0` — прежний зажим `вход − 1e-8`
        let near = if ex.sm_pm > 0 {
            e - ((i128::from(ea) * i128::from(ex.sm_pm) + 99_999) / 100_000).max(1) as i64
        } else {
            e - 1
        };
        let mut floor = [false; 2];
        let mut lvl_px = |raw: i64| -> i64 {
            floor[0] |= ex.sm_pm > 0 && raw > near;
            floor[1] |= raw > e - 1;
            raw.min(near)
        };
        let mut stop_base = e - frac(ea, ex.s_pm, 100_000);
        let mut be_lvl = None;
        if let Some(l) = lc {
            if ex.ls_m > 0 {
                stop_base = stop_base.max(e.min(l.far) - nat(ex.ls_m));
            }
            // §13.31: стоп за проторговкой (`cw`) — если ширина диапазона в допуске `[cl, ch]` NATR; иначе запасной резиновый стоп от уровня (`sl`)
            let mut cons_ok = false;
            if ex.cs_w > 0 {
                if let Some((rlo, rhi)) = l.rng {
                    let w = rhi - rlo;
                    if w >= nat(ex.cs_lo_m) && w <= nat(ex.cs_hi_m) {
                        stop_base = stop_base.max(lvl_px(rlo - nat(ex.cs_m_m)));
                        cons_ok = true;
                    }
                }
            }
            if !cons_ok && ex.sl_m > 0 {
                stop_base = stop_base.max(lvl_px(l.lp - nat(ex.sl_m)));
            }
            if ex.be_mode == 3 {
                let kb = if ex.ls_m > 0 { ex.ls_m } else { 500 };
                be_lvl = Some(e.min(l.far) - nat(kb));
            }
            // TK-676 (`vf`): стоп от волатильности — расстояние заморожено на входе; «самый плотный» вместе с `s`, `ls`, `sl`, `cw`
            if let Some(d_pm) = volstop::stop_pm(ex, l.vol, l.volz) {
                stop_base = stop_base.max(e - frac(ea, d_pm, 100_000));
            }
        }
        // TK-677 (`s2`): общий стоп позиции — жёсткий стоп лота стоит на заданной цене (стоп лота 1), а не от своей цены входа
        if let Some(sp) = stop_px {
            stop_base = sp.min(e - 1);
        }
        let lp = lc.map_or(0, |l| l.lp);
        let nx_lvl = if ex.nx_m > 0 {
            lvl_px(lp - nat(ex.nx_m))
        } else {
            i64::MIN
        };
        let rules =
            lc.is_some() && (ex.tx_min > 0 || ex.rt_f_pm > 0 || ex.nx_m > 0 || ex.tu_min > 0);
        let dist = |x_pm: i64| (i128::from(lp.abs()) * i128::from(x_pm) / 100_000) as i64;
        let unit = i128::from(ea) * i128::from(natr_e8);
        let natr_stop = (ex.sn_m > 0).then(|| NatrStop {
            k0_m: ex.sn_m,
            sg: ex.sg,
            sb_m: ex.sb_m,
            sr_m: ex.sr_m,
            sd_m: ex.sd_m,
            unit,
            lo: frac(ea, NATR_STOP_MIN_PM, 100_000),
            hi: frac(ea, ex.s_pm, 100_000),
            entry_ms,
        });
        // TK-669 (`tpp`, `trp`): тейки и откат трейла — в процентах цены входа, а не в NATR
        let tp_at = |i: i64| {
            if ex.tpp {
                e + frac(ea, tp_mult_m(ex, i, n), 100_000)
            } else {
                e + nat(tp_mult_m(ex, i, n))
            }
        };
        let ladder: Vec<i64> = (0..n).map(tp_at).collect();
        // TK-697 (`tn`): первый тейк у следующего уровня; `None` — обычная лестница (ключ выключен или тейк не годится)
        let nt = if ex.tn_pct > 0 {
            lc.and_then(|l| next_take_plan(ex, l.nxt, e, nat(ex.tno_m), &ladder, notional))
        } else {
            None
        };
        // пересчёт `tj` идёт по множителям исходных ступеней (у тейка у уровня множитель не используется: после его исполнения он не пересчитывается)
        let step = (ex.tj_m > 0).then(|| StepLadder {
            delta_m: ex.tj_m,
            mults: match &nt {
                Some(pl) => pl
                    .steps
                    .iter()
                    .map(|&i| tp_mult_m(ex, i as i64, n))
                    .collect(),
                None => (0..n).map(|i| tp_mult_m(ex, i, n)).collect(),
            },
            unit,
        });
        let (tps, lots) = match nt {
            Some(pl) => (pl.tps, pl.lots),
            None => (ladder, tp_lots_n(ex, notional)),
        };
        let mut pos = Pos {
            e,
            tps,
            lots,
            a_dist: nat(ex.a_m),
            r_dist: if ex.trp {
                frac(ea, ex.r_m, 100_000)
            } else {
                nat(ex.r_m)
            },
            trp: ex.trp,
            be_cfg: ex.be,
            rem: notional,
            stop_base,
            be_on: false,
            armed: false,
            trail: i64::MIN,
            maxp: p0,
            next_tp: 0,
            legs: Vec::new(),
            s,
            be_mode: ex.be_mode,
            be_lvl,
            rules,
            lp,
            tx_ms: ex.tx_min * 60_000,
            tx_x: dist(ex.tx_x_pm),
            rt_d: dist(ex.rt_f_pm),
            rt_ms: ex.rt_s * 1000,
            touched_ms: NONE_MS,
            broke: false,
            below_since: NONE_MS,
            nx_lvl,
            tu_due: if ex.tu_min > 0 {
                entry_ms + ex.tu_min * 60_000
            } else {
                i64::MAX
            },
            natr_stop,
            step,
            lim: false,
            floor,
            ord: Vec::new(),
            ord_on: false,
            ord_stop: i64::MIN,
            ord_rem: 0,
        };
        if ex.keep_orders {
            pos.ord_init(entry_ms);
        }
        pos
    }

    /// TK-743: действующий стоп лота (рама) на сделке в `ms` — «самый плотный» из жёсткого, безубытка, трейла и NATR-стопа (та же формула, что в `on_trade`).
    fn eff_stop(&self, ms: i64) -> i64 {
        let trail_lvl = if self.armed && !self.lim {
            self.trail
        } else {
            i64::MIN
        };
        let natr_lvl = self
            .natr_stop
            .as_ref()
            .map_or(i64::MIN, |n| self.e - n.dist(ms, self.next_tp));
        self.stop_base.max(trail_lvl).max(natr_lvl)
    }

    /// TK-743: заявки лота на входе — лестница тейков (лимитки), стоп и условные выходы до касания (`nx`, `tu`).
    fn ord_init(&mut self, entry_ms: i64) {
        self.ord_on = true;
        self.ord = Vec::with_capacity(64);
        let s = self.s;
        for (i, (&tp, &lot)) in self.tps.iter().zip(&self.lots).enumerate() {
            self.ord.push(OrdEv::new(
                entry_ms,
                EV_PLACE,
                i as u8,
                TY_LIMIT,
                s * tp,
                lot,
                "ladder",
            ));
        }
        let sp = self.eff_stop(entry_ms);
        self.ord_stop = sp;
        self.ord_rem = self.rem;
        self.ord.push(OrdEv::new(
            entry_ms,
            EV_PLACE,
            OID_STOP,
            TY_STOP,
            s * sp,
            self.rem,
            "entry",
        ));
        if self.nx_lvl != i64::MIN {
            self.ord.push(OrdEv::new(
                entry_ms,
                EV_PLACE,
                OID_NX,
                TY_COND,
                s * self.nx_lvl,
                self.rem,
                "until touch",
            ));
        }
        if self.tu_due != i64::MAX {
            let mut e = OrdEv::new(
                entry_ms,
                EV_PLACE,
                OID_TU,
                TY_COND,
                0,
                self.rem,
                "until touch",
            );
            e.aux = self.tu_due;
            self.ord.push(e);
        }
    }

    /// TK-743: сдвиг действующего стопа после сделки в `ms` (безубыток, трейл, NATR-стоп, остаток после тейков) — одно событие на мс.
    fn log_stop(&mut self, ms: i64) {
        let sp = self.eff_stop(ms);
        if sp != self.ord_stop || self.rem != self.ord_rem {
            let note = if self.armed && !self.lim && self.trail >= sp {
                "trail"
            } else if self.be_on {
                "be"
            } else {
                "rest"
            };
            self.ord_stop = sp;
            self.ord_rem = self.rem;
            push_reprice(
                &mut self.ord,
                OrdEv::new(
                    ms,
                    EV_REPRICE,
                    OID_STOP,
                    TY_STOP,
                    self.s * sp,
                    self.rem,
                    note,
                ),
            );
        }
    }

    /// TK-743: первое касание уровня в `ms` — снимаются условия «до касания», ставятся условия «после касания» (возврат, выход по времени).
    fn ord_touch(&mut self, ms: i64) {
        let s = self.s;
        if self.nx_lvl != i64::MIN {
            self.ord.push(OrdEv::new(
                ms,
                EV_CANCEL,
                OID_NX,
                TY_COND,
                s * self.nx_lvl,
                self.rem,
                "touched",
            ));
        }
        if self.tu_due != i64::MAX {
            let mut e = OrdEv::new(ms, EV_CANCEL, OID_TU, TY_COND, 0, self.rem, "touched");
            e.aux = self.tu_due;
            self.ord.push(e);
        }
        if self.rt_d > 0 {
            let mut e = OrdEv::new(
                ms,
                EV_PLACE,
                OID_RT,
                TY_COND,
                s * (self.lp - self.rt_d),
                self.rem,
                "touched",
            );
            e.aux = self.rt_ms;
            self.ord.push(e);
        }
        if self.tx_ms > 0 {
            let mut e = OrdEv::new(ms, EV_PLACE, OID_TX, TY_COND, 0, self.rem, "touched");
            e.aux = ms + self.tx_ms;
            self.ord.push(e);
        }
    }

    /// TK-743: лимитный трейл встал на биржу (`tl`) — оставшиеся тейки сняты в `ms`.
    fn ord_cancel_takes(&mut self, ms: i64) {
        for i in self.next_tp..self.tps.len() {
            self.ord.push(OrdEv::new(
                ms,
                EV_CANCEL,
                i as u8,
                TY_LIMIT,
                self.s * self.tps[i],
                self.lots[i],
                "trail limit set",
            ));
        }
    }

    /// TK-743: конец лота — исполнение выходной заявки по виду выхода и снятие всех остальных заявок. `xm`, `px` — время и цена последней ноги, `trig` — время срабатывания
    /// (отличается от `xm` при задержке выхода), `limit` — выход лимитным приказом трейла, `trl` — выход трейлом-лимиткой.
    fn ord_close(&mut self, xm: i64, px: i64, kind: ExitKind, trig: i64, limit: bool, trl: bool) {
        let rem = self.rem;
        let (oid, typ, note) = match kind {
            ExitKind::Takes => {
                cancel_open(&mut self.ord, xm, "all takes filled");
                return;
            }
            ExitKind::Trail if trl => (
                OID_TRL,
                if limit { TY_LIMIT } else { TY_MARKET },
                if limit {
                    "trail limit"
                } else {
                    "trail limit, marketable at arrival"
                },
            ),
            ExitKind::HardStop => (OID_STOP, TY_STOP, "stop"),
            ExitKind::BreakEven => (OID_STOP, TY_STOP, "be"),
            ExitKind::Trail => (OID_STOP, TY_STOP, "trail"),
            ExitKind::Return => (OID_RT, TY_COND, "rt"),
            ExitKind::TimeExit => (OID_TX, TY_COND, "tx"),
            ExitKind::NoTouch => (OID_NX, TY_COND, "nx"),
            ExitKind::TimeNoTouch => (OID_TU, TY_COND, "tu"),
            ExitKind::Forced => (OID_EXIT, TY_MARKET, "forced: end of data"),
        };
        if !self.ord.iter().any(|e| e.oid == oid) {
            self.ord
                .push(OrdEv::new(trig, EV_PLACE, oid, typ, 0, rem, note));
        }
        let mut f = OrdEv::new(xm, EV_FILL, oid, typ, px, rem, note);
        if trig != xm {
            f.aux = trig;
        }
        self.ord.push(f);
        cancel_open(&mut self.ord, xm, "position closed");
    }

    /// Тейки-лимитки, пройденные сделкой `p` строго насквозь.
    fn fill_takes(&mut self, ms: i64, p: i64) {
        let filled0 = self.next_tp;
        while self.next_tp < self.tps.len() && p > self.tps[self.next_tp] {
            let lot = self.lots[self.next_tp];
            self.legs.push(TLeg {
                ms,
                px: self.s * self.tps[self.next_tp],
                u: lot,
                maker: true,
            });
            if self.ord_on {
                self.ord.push(OrdEv::new(
                    ms,
                    EV_FILL,
                    self.next_tp as u8,
                    TY_LIMIT,
                    self.s * self.tps[self.next_tp],
                    lot,
                    "",
                ));
            }
            self.rem -= lot;
            if self.be_cfg {
                // §13.19 `bm`: 0 — стоп в цену входа после 1-го тейка; 1 — выкл; 2 — после 2-го; 3 — за уровнем
                let to = match (self.be_mode, self.next_tp) {
                    (0 | 3, 0) | (2, 1) => Some(self.be_lvl.unwrap_or(self.e)),
                    _ => None,
                };
                if let Some(lvl) = to {
                    self.be_on = true;
                    self.stop_base = self.stop_base.max(lvl);
                }
            }
            self.next_tp += 1;
        }
        // §13.21 (`tj`): лестница пересчитана после этой сделки — действует со следующей (внутри сделки ступени идут по прежним ценам)
        if self.next_tp > filled0 {
            if let Some(st) = &self.step {
                let f = 1000 + i128::from(st.delta_m) * self.next_tp as i128;
                let nt = self.next_tp;
                let rest = self.tps[nt..].iter_mut().zip(&st.mults[nt..]).enumerate();
                for (k, (tp, &m)) in rest {
                    let old = *tp;
                    *tp = self.e
                        + (st.unit * i128::from(m) * f / (i128::from(SCALE) * 1_000_000)) as i64;
                    if self.ord_on && *tp != old {
                        self.ord.push(OrdEv::new(
                            ms,
                            EV_REPRICE,
                            (nt + k) as u8,
                            TY_LIMIT,
                            self.s * *tp,
                            self.lots[nt + k],
                            "tj",
                        ));
                    }
                }
            }
        }
    }

    /// Сделка с ценой `p` (рама) в момент `ms`. `Some(вид)` — стоп сработал на этой сделке (исполнение по `p`).
    fn on_trade(&mut self, ms: i64, p: i64) -> Option<ExitKind> {
        let r = self.on_trade_inner(ms, p);
        if self.ord_on && r.is_none() {
            self.log_stop(ms);
        }
        r
    }

    fn on_trade_inner(&mut self, ms: i64, p: i64) -> Option<ExitKind> {
        let trail_lvl = if self.armed && !self.lim {
            self.trail
        } else {
            i64::MIN
        };
        // §13.21: NATR-стоп пересчитывается на каждой сделке; «самый плотный из активных» — уровень, ближайший к цене (в рамe — наибольший)
        let natr_lvl = self
            .natr_stop
            .as_ref()
            .map_or(i64::MIN, |n| self.e - n.dist(ms, self.next_tp));
        let sp = self.stop_base.max(trail_lvl).max(natr_lvl);
        if p <= sp {
            return Some(if trail_lvl > self.stop_base.max(natr_lvl) {
                ExitKind::Trail
            } else if natr_lvl > self.stop_base {
                ExitKind::HardStop
            } else if self.be_on {
                ExitKind::BreakEven
            } else {
                ExitKind::HardStop
            });
        }
        if self.lim {
            return if self.rules {
                self.level_exit(ms, p)
            } else {
                None
            };
        }
        self.fill_takes(ms, p);
        // TK-669 (`q = 100`): лестница закрыла позицию целиком — выход на сделке последнего тейка
        if self.rem <= 0 {
            return Some(ExitKind::Takes);
        }
        self.maxp = self.maxp.max(p);
        let arm = if self.trp {
            self.next_tp > 0
        } else {
            self.maxp - self.e >= self.a_dist
        };
        if !self.armed && arm {
            self.armed = true;
        }
        if self.armed {
            self.trail = self.trail.max(self.maxp - self.r_dist);
        }
        if self.rules {
            return self.level_exit(ms, p);
        }
        None
    }

    /// §13.19: выход по времени (`tt/tb`) и при возврате за уровень (`rf/rs`); «пересекли» — сделка `p ≥ L`.
    fn level_exit(&mut self, ms: i64, p: i64) -> Option<ExitKind> {
        if self.touched_ms == NONE_MS && p >= self.lp {
            self.touched_ms = ms;
            if self.ord_on {
                self.ord_touch(ms);
            }
        }
        if self.touched_ms == NONE_MS {
            // §13.31 (`nx`): до касания цена ушла от уровня против позиции на X·NATR — сценарий отменён
            if p <= self.nx_lvl {
                return Some(ExitKind::NoTouch);
            }
            // TK-649 (`tu`): уровень не тронут за T минут от входа
            return (ms >= self.tu_due).then_some(ExitKind::TimeNoTouch);
        }
        if !self.broke && p >= self.lp + self.tx_x {
            self.broke = true;
            if self.ord_on && self.tx_ms > 0 {
                self.ord.push(OrdEv::new(
                    ms, EV_CANCEL, OID_TX, TY_COND, 0, self.rem, "broke",
                ));
            }
        }
        if self.tx_ms > 0 && !self.broke && ms >= self.touched_ms + self.tx_ms {
            return Some(ExitKind::TimeExit);
        }
        if self.rt_d > 0 {
            if p <= self.lp - self.rt_d {
                if self.below_since == NONE_MS {
                    self.below_since = ms;
                }
                if ms - self.below_since >= self.rt_ms {
                    return Some(ExitKind::Return);
                }
            } else {
                self.below_since = NONE_MS;
            }
        }
        None
    }
}

/// Курсор по сделкам монеты от места `(day, idx)`; сутки без файла пропускаются.
pub struct Cursor<'a> {
    src: &'a dyn TradeSrc,
    name: &'a str,
    day: usize,
    idx: usize,
    cur: Option<Arc<Vec<Tick>>>,
}

impl<'a> Cursor<'a> {
    pub fn new(src: &'a dyn TradeSrc, name: &'a str, day: usize, idx: usize) -> Cursor<'a> {
        Cursor {
            src,
            name,
            day,
            idx,
            cur: None,
        }
    }

    /// (время от 01.09 в мс, цена, `is_buyer_maker`).
    pub fn next_tick(&mut self) -> Option<(i64, i64, bool)> {
        loop {
            if self.day >= DAYS_TOTAL {
                return None;
            }
            if self.cur.is_none() {
                self.cur = Some(
                    self.src
                        .day(self.name, self.day)
                        .unwrap_or_else(|| Arc::new(Vec::new())),
                );
            }
            if let Some(v) = &self.cur {
                if let Some(tk) = v.get(self.idx) {
                    self.idx += 1;
                    return Some((
                        self.day as i64 * DAY_MS + i64::from(tk.off()),
                        tk.px,
                        tk.bm(),
                    ));
                }
            }
            self.cur = None;
            self.day += 1;
            self.idx = 0;
        }
    }
}

/// Вход и выходы по потоку сделок `next` (после сигнала в момент `sig_ms`): §13.2 п. 2–3.
pub fn sim_stream(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    sig_ms: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
) -> Option<TickOut> {
    let s = side.s();
    let taker_bm = side == Side::Short;
    let (entry_ms, p0) = loop {
        let (ms, px, bm) = next()?;
        if ms >= sig_ms + ex.lat_ms && bm == taker_bm {
            break (ms, s * px);
        }
    };
    Some(sim_tail(
        next, entry_ms, p0, side, natr_e8, ex, None, NOTIONAL_U,
    ))
}

/// §13.16: вход заполнен в момент `entry_ms` по цене `p0` (рама); выходы — по сделкам со временем строго после `entry_ms`.
pub fn sim_fill(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
) -> TickOut {
    sim_fill_lc(next, entry_ms, p0, side, natr_e8, ex, None, NOTIONAL_U)
}

/// `sim_fill` с контекстом уровня (§13.19: стоп/выходы от уровня) и размером лота `notional` (мкУЕ).
#[allow(clippy::too_many_arguments)]
pub fn sim_fill_lc(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
) -> TickOut {
    sim_fill_lc_e(
        next,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        lc,
        notional,
        ex.slip_bps,
    )
}

/// `sim_fill_lc` с проскальзыванием входа `entry_slip_bps` (§13.28: лимитный вход — 0).
#[allow(clippy::too_many_arguments)]
pub fn sim_fill_lc_e(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
    entry_slip_bps: i64,
) -> TickOut {
    sim_fill_lc_s(
        next,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        lc,
        notional,
        entry_slip_bps,
        None,
    )
}

/// TK-677: цена жёсткого стопа лота (рама) на входе по `p0` с проскальзыванием `entry_slip_bps` — та, с которой стартует `sim_fill_lc_e`
/// («самый плотный» из `s`, `ls`, `sl`, `cw`, `vf`); для общего стопа позиции (`s2`).
pub fn entry_stop_px(
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    entry_slip_bps: i64,
) -> i64 {
    let e = p0 + frac(p0.abs(), entry_slip_bps, 10_000);
    Pos::new(side.s(), e, p0, 0, natr_e8, ex, lc, NOTIONAL_U, None).stop_base
}

/// `sim_fill_lc_e` с заданной ценой жёсткого стопа `stop_px` (рама; TK-677 `s2` — стоп лота 1); `None` — стоп от своей цены входа.
#[allow(clippy::too_many_arguments)]
pub fn sim_fill_lc_s(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
    entry_slip_bps: i64,
    stop_px: Option<i64>,
) -> TickOut {
    let mut after = || -> Option<(i64, i64, bool)> {
        loop {
            let t = next()?;
            if t.0 > entry_ms {
                return Some(t);
            }
        }
    };
    sim_tail_e(
        &mut after,
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
}

/// TK-706: ход лота по ленте монеты `coin` (сделки строго после `entry_ms`) с памятью исходов запуска: ход — чистая функция аргументов и ленты, поэтому равный
/// ключ (`MoveKey`: все аргументы по значению + монета) берёт готовый исход; без памяти (`move_memo` = `None`) — прежний счёт по свежему курсору. Место старта
/// чтения **выводится из `entry_ms`** (сутки входа, первая сделка со временем > `entry_ms` — двоичный поиск), а не берётся извне: ключ определяет ход целиком
/// (условие C1 критика). Ход лота 2 — тот же вызов с `m2` / `q0` / `lot2_n` / `stop2`.
#[allow(clippy::too_many_arguments)]
pub(super) fn sim_fill_mem(
    src: &dyn TradeSrc,
    coin: &str,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: LevelCtx,
    notional: i64,
    entry_slip_bps: i64,
    stop_px: Option<i64>,
) -> TickOut {
    let mut compute = || {
        let day = (entry_ms.max(0) / DAY_MS) as usize;
        let base = day as i64 * DAY_MS;
        let idx = src.day(coin, day).map_or(0, |v| {
            v.partition_point(|x| base + i64::from(x.off()) <= entry_ms)
        });
        let mut cur = Cursor::new(src, coin, day, idx);
        sim_fill_lc_s(
            &mut || cur.next_tick(),
            entry_ms,
            p0,
            side,
            natr_e8,
            ex,
            Some(lc),
            notional,
            entry_slip_bps,
            stop_px,
        )
    };
    let Some(memo) = src.move_memo() else {
        return compute();
    };
    let key = MoveKey {
        coin: coin.into(),
        entry_ms,
        p0,
        side,
        natr_e8,
        ex: ex.clone(),
        lc: Some(lc),
        notional,
        entry_slip_bps,
        stop_px,
    };
    memo.get_or_compute(key, &mut compute)
}

/// Позиция после входа: цена входа `p0` (рама) ± проскальзывание, тейки, стоп, трейл по потоку `next` (сделки после входа).
#[allow(clippy::too_many_arguments)]
fn sim_tail(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
) -> TickOut {
    sim_tail_e(
        next,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        lc,
        notional,
        ex.slip_bps,
        None,
    )
}

/// `sim_tail` с проскальзыванием входа `entry_slip_bps` (§13.28: у лимитного входа 0; проскальзывание выходов — `ex.slip_bps`).
#[allow(clippy::too_many_arguments)]
fn sim_tail_e(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
    entry_slip_bps: i64,
    stop_px: Option<i64>,
) -> TickOut {
    let s = side.s();
    let e = p0 + frac(p0.abs(), entry_slip_bps, 10_000);
    let mut pos = Pos::new(s, e, p0, entry_ms, natr_e8, ex, lc, notional, stop_px);
    let mut cur_day = (entry_ms / DAY_MS) as usize;
    let mut marks: Vec<(usize, i64)> = Vec::new();
    let mut last = (entry_ms, p0);
    let mut exit: Option<(i64, i64, ExitKind)> = None;
    let mut pending: Option<(i64, ExitKind)> = None;
    // v22 (`tl`): стоящий приказ трейла — (цена в рамe, момент, с которого он живёт, цена последней сделки до его прихода); `tl_info` — (мс срабатывания,
    // настоящая цена) для вывода
    let mut lim: Option<(i64, i64, i64)> = None;
    let mut tl_info: Option<(i64, i64)> = None;
    let mut maker_exit = false;
    while let Some((ms, px, _)) = next() {
        let d = (ms / DAY_MS) as usize;
        while cur_day < d {
            marks.push((cur_day, s * last.1));
            cur_day += 1;
        }
        let p = s * px;
        last = (ms, p);
        if let Some((lpx, live, arrival)) = lim {
            if ms < live {
                // приказ в пути (задержка выхода): как окно задержки рыночного выхода — сделки только исполняют тейки
                pos.fill_takes(ms, p);
                lim = Some((lpx, live, p));
                continue;
            }
            // `tl = 2`: к приходу приказа цена уже строго за ним — приказ маркетабелен и исполняется как рыночный выход v21 (тейкер по сделке, с проскальзыванием)
            if ex.tl == 2 && arrival > lpx {
                exit = Some((ms, p, ExitKind::Trail));
                break;
            }
            lim = Some((lpx, live, i64::MIN));
            if pos.ord_on && !pos.lim {
                pos.ord_cancel_takes(ms);
            }
            pos.lim = true;
            if let Some((due, kind)) = pending {
                if ms >= due {
                    exit = Some((ms, p, kind));
                    break;
                }
            }
            if p >= lpx {
                exit = Some((ms, lpx, ExitKind::Trail));
                maker_exit = true;
                break;
            }
            if pending.is_none() {
                if let Some(kind) = pos.on_trade(ms, p) {
                    if ex.lat_exit_ms > 0 {
                        pending = Some((ms + ex.lat_exit_ms, kind));
                    } else {
                        exit = Some((ms, p, kind));
                        break;
                    }
                }
            }
            continue;
        }
        if let Some((due, kind)) = pending {
            if ms >= due {
                exit = Some((ms, p, kind));
                break;
            }
            pos.fill_takes(ms, p);
            continue;
        }
        if let Some(kind) = pos.on_trade(ms, p) {
            if ex.tl > 0 && kind == ExitKind::Trail {
                lim = Some((pos.trail, ms + ex.lat_exit_ms, p));
                tl_info = Some((ms, s * pos.trail));
                if pos.ord_on {
                    let mut e = OrdEv::new(
                        ms,
                        EV_PLACE,
                        OID_TRL,
                        TY_LIMIT,
                        s * pos.trail,
                        pos.rem,
                        "trail",
                    );
                    e.aux = ms + ex.lat_exit_ms;
                    pos.ord.push(e);
                }
                continue;
            }
            // TK-669: «всё взято» — лимитки уже исполнены, выхода (и его задержки) нет
            if ex.lat_exit_ms > 0 && kind != ExitKind::Takes {
                pending = Some((ms + ex.lat_exit_ms, kind));
                continue;
            }
            exit = Some((ms, p, kind));
            break;
        }
    }
    let (xm, xp, kind) = exit.unwrap_or((last.0, last.1, ExitKind::Forced));
    // лимитный выход трейла — по цене приказа, мейкер, без проскальзывания; остальные выходы — тейкер с проскальзыванием
    let px_frame = if maker_exit {
        xp
    } else {
        xp - frac(xp.abs(), ex.slip_bps, 10_000)
    };
    if pos.ord_on {
        // TK-743: срабатывание выхода — момент ухода сделки в задержку (`pending`), для трейла-лимитки — момент постановки приказа
        let trl = tl_info.is_some() && kind == ExitKind::Trail;
        let trig = match (pending, tl_info) {
            (Some((due, _)), _) => due - ex.lat_exit_ms,
            (None, Some((m, _))) if trl => m,
            _ => xm,
        };
        let px = s * px_frame;
        pos.ord_close(xm, px, kind, trig, maker_exit, trl);
    }
    let ord = std::mem::take(&mut pos.ord);
    let mut legs = pos.legs;
    if kind != ExitKind::Takes {
        legs.push(TLeg {
            ms: xm,
            px: s * px_frame,
            u: pos.rem,
            maker: maker_exit,
        });
    }
    TickOut {
        entry_ms,
        entry_px: s * e,
        legs,
        exit_ms: xm,
        kind,
        takes: pos.next_tp as u32,
        marks,
        tl: tl_info,
        floor: pos.floor,
        ord,
    }
}

pub fn simulate_tick(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    natr_e8: i64,
    ex: &ExitCfg,
) -> Option<TickOut> {
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    let taker_bm = side == Side::Short;
    let mut next = || cur.next_tick();
    let (entry_ms, p0) = loop {
        let (ms, px, bm) = next()?;
        if ms >= sig.ms + ex.lat_ms && bm == taker_bm {
            break (ms, side.s() * px);
        }
    };
    let lc = level_ctx(sym, side, sig, ex);
    Some(sim_tail(
        &mut next,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        Some(lc),
        NOTIONAL_U,
    ))
}

/// Причина отказа входа по ленте (§13.16).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryRej {
    /// Цена-заменитель уже за уровнем больше чем на X бп.
    Beyond,
    /// Нет сделки стороны входа со временем ≤ `сигнал + lat` не старше W мс.
    Stale,
    /// §13.28 (`mk`): заявка мейкера не заполнилась до конца минуты.
    Unfilled,
    /// §13.28 (`mk = 1`): до заполнения сделка дошла до уровня — заявка снята.
    Touched,
    /// §13.28 (`mk`): тик монеты нигде не определён (нет окна из ≥ 100 сделок).
    NoTick,
    /// TK-660 (`ek`): порог E(k) на уровне или за ним уже при постановке — приказ не ставится.
    NoRoom,
}

/// Решение о входе для сигнала (§13.16).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryDec {
    /// `ew = 0`: прежний вход — следующая сделка стороны (§13.2 п. 2).
    Next,
    /// Вход в момент `ms` по цене `p0` (рама).
    Fill {
        ms: i64,
        p0: i64,
    },
    Reject(EntryRej),
}

/// Последняя сделка стороны входа (лонг — покупатель-тейкер, `bm = false`; шорт — `bm = true`) со временем ≤ `t_abs` (мс от
/// 01.09) не старше `cap_ms`: (цена ×1e8, возраст мс). `ticks` — сутки `day`; при исчерпании суток в пределах возраста — предыдущие.
pub fn tape_last_side(
    src: &dyn TradeSrc,
    name: &str,
    day: usize,
    ticks: &[Tick],
    t_abs: i64,
    taker_bm: bool,
    cap_ms: i64,
) -> Option<(i64, i64)> {
    let t_off = t_abs - day as i64 * DAY_MS;
    let hi = ticks.partition_point(|x| i64::from(x.off()) <= t_off);
    for tk in ticks[..hi].iter().rev() {
        let age = t_off - i64::from(tk.off());
        if age > cap_ms {
            return None;
        }
        if tk.bm() == taker_bm {
            return Some((tk.px, age));
        }
    }
    let v = src.day(name, day.checked_sub(1)?)?;
    for tk in v.iter().rev() {
        let age = t_off + DAY_MS - i64::from(tk.off());
        if age > cap_ms {
            return None;
        }
        if tk.bm() == taker_bm {
            return Some((tk.px, age));
        }
    }
    None
}

/// §13.16: вход по ленте «как на момент сигнала + `lat`»: цена-заменитель — последняя сделка стороны ≤ `T` не старше W; за уровнем
/// больше чем на X бп — отказ.
pub fn entry_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
) -> EntryDec {
    if !ex.tape_entry() {
        return EntryDec::Next;
    }
    entry_at(
        src,
        sym,
        side,
        usize::from(sig.day),
        sig.ms + ex.lat_ms,
        sig.lp,
        ex,
    )
}

/// Вход по ленте на момент `t` (мс от 01.09; сутки файла — `day`) при цене уровня `lp` (рама): цена-заменитель — последняя сделка
/// стороны ≤ `t` не старше W; за уровнем больше чем на X бп — отказ.
pub fn entry_at(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    day: usize,
    t: i64,
    lp: i64,
    ex: &ExitCfg,
) -> EntryDec {
    entry_at_ex(src, sym, side, day, t, lp, ex, true)
}

/// `entry_at` с выключаемой проверкой «за уровнем — отказ» (`check_beyond = false` — вход за уровнем, TK-669 `xb`).
#[allow(clippy::too_many_arguments)]
pub fn entry_at_ex(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    day: usize,
    t: i64,
    lp: i64,
    ex: &ExitCfg,
    check_beyond: bool,
) -> EntryDec {
    let Some(ticks) = src.day(&sym.name, day) else {
        return EntryDec::Reject(EntryRej::Stale);
    };
    let Some((px, _)) = tape_last_side(
        src,
        &sym.name,
        day,
        &ticks,
        t,
        side == Side::Short,
        ex.ent_w_ms,
    ) else {
        return EntryDec::Reject(EntryRej::Stale);
    };
    let p = side.s() * px;
    if check_beyond && i128::from(p - lp) * 10_000 > i128::from(ex.ent_x_bp) * i128::from(lp.abs())
    {
        return EntryDec::Reject(EntryRej::Beyond);
    }
    EntryDec::Fill { ms: t, p0: p }
}

/// Решение о втором лоте (§13.19, `e2`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lot2Dec {
    /// Лот 2 входит в `ms` по цене `p0` (рама); `immediate` — цена лота 1 уже в пределах d, вход в тот же момент по той же цене.
    Fill { ms: i64, p0: i64, immediate: bool },
    /// Лот 1 закрыт к моменту входа / цена пересекла уровень раньше, чем подошла на d.
    Cancel,
    /// Цена-заменитель лота 2 за уровнем или ленты нет — отказ (как у лота 1).
    Reject,
}

/// Второй лот (§13.19): рыночный вход, когда цена подошла к уровню ближе d (`p ≥ L − d·|L|`) и ещё не пересекла (`p < L`), пока лот 1 открыт.
/// `lot1` — (время входа, цена без проскальзывания) лота 1, `lot1_exit_ms` — время его выхода.
pub fn lot2_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    lot1: (i64, i64),
    lot1_exit_ms: i64,
) -> Lot2Dec {
    let d = lot2_dist(sig.lp, ex.e2_pm);
    lot2_decision_d(src, sym, side, sig, ex, lot1, lot1_exit_ms, d)
}

/// Расстояние второго входа `e2` от уровня `lp` в единицах цены.
pub fn lot2_dist(lp: i64, e2_pm: i64) -> i64 {
    (i128::from(lp.abs()) * i128::from(e2_pm) / 100_000) as i64
}

/// `lot2_decision` с расстоянием `d` в единицах цены (TK-677 `e2t`: `d = N·тик`).
#[allow(clippy::too_many_arguments)]
pub fn lot2_decision_d(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    lot1: (i64, i64),
    lot1_exit_ms: i64,
    d: i64,
) -> Lot2Dec {
    lot2_trace(src, sym, side, sig, ex, lot1, lot1_exit_ms, d).0
}

/// `lot2_decision_d` с пометкой для журнала заявок (TK-743): момент события и его причина — срабатывание условия входа (исполнение / отказ), снятие заявки
/// (лот 1 закрыт / цена дошла до уровня раньше подхода на `d`).
#[allow(clippy::too_many_arguments)]
pub fn lot2_trace(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    lot1: (i64, i64),
    lot1_exit_ms: i64,
    d: i64,
) -> (Lot2Dec, i64, &'static str) {
    let (ms1, p1) = lot1;
    if p1 >= sig.lp - d {
        return (
            Lot2Dec::Fill {
                ms: ms1,
                p0: p1,
                immediate: true,
            },
            ms1,
            "immediate: lot 1 entry already inside the e2 distance",
        );
    }
    let s = side.s();
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms <= ms1 {
            continue;
        }
        if ms >= lot1_exit_ms {
            return (Lot2Dec::Cancel, lot1_exit_ms, "lot 1 closed first");
        }
        let p = s * px;
        if p >= sig.lp {
            return (
                Lot2Dec::Cancel,
                ms,
                "price reached the level before the e2 distance",
            );
        }
        if p >= sig.lp - d {
            let t2 = ms + ex.lat_ms;
            if t2 >= lot1_exit_ms {
                return (
                    Lot2Dec::Cancel,
                    lot1_exit_ms,
                    "lot 1 closed before the entry",
                );
            }
            return match entry_at(src, sym, side, (t2 / DAY_MS) as usize, t2, sig.lp, ex) {
                EntryDec::Fill { ms: m2, p0 } => (
                    Lot2Dec::Fill {
                        ms: m2,
                        p0,
                        immediate: false,
                    },
                    ms,
                    "later: price came within the e2 distance",
                ),
                _ => (Lot2Dec::Reject, ms, "rejected: no tape or beyond the level"),
            };
        }
    }
    (Lot2Dec::Cancel, lot1_exit_ms, "lot 1 closed first")
}

// ───────────────────────────── TK-660: вход по подтверждению E(k), опция `ek` ─────────────────────────────

/// TK-660 (`ek`): приказ E(k) живёт столько от `сигнал + lat`, мс (60 мин, как повтор TK-617 / §13.55).
pub const EK_EXPIRY_MS: i64 = 3_600_000;

/// TK-660 (`ek`): вход по подтверждению E(k), обе стороны в раме. Опора `p_ref` — вход по ленте §13.16 на `сигнал + lat`
/// (`entry_decision`; её отказ — отказ сигнала, приказа нет, второй элемент `None`). Порог рамы `p_ref + k·|p_ref|/10⁴` (лонг — рост
/// на k бп, шорт — падение) не ниже уровня ни при одной цене `p < L` — приказ не ставится (`NoRoom`, без занятости). Иначе приказ
/// живёт с `сигнал + lat`: первая сделка ленты (любой стороны) на уровне или за ним (`p ≥ L`) — снят (`Touched`); первая сделка с
/// ценой у порога или дальше — срабатывание; не сработал за `EK_EXPIRY_MS` — снят (`Unfilled`);
/// сработал в `ms` — рыночный вход по ленте §13.16 на `ms + lat` (`entry_at`; отказ — `Stale` / `Beyond`). Второй элемент —
/// момент снятия / исполнения приказа (занятость монеты и места в портфеле до него, `run_tick_ek`).
pub fn ek_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    k_bp: i64,
) -> (EntryDec, Option<i64>) {
    let (t0, pref) = match entry_decision(src, sym, side, sig, ex) {
        EntryDec::Fill { ms, p0 } => (ms, p0),
        other => return (other, None),
    };
    let need = i128::from(pref.abs()) * i128::from(k_bp);
    if i128::from(sig.lp - 1 - pref) * 10_000 < need {
        return (EntryDec::Reject(EntryRej::NoRoom), None);
    }
    let until = t0 + EK_EXPIRY_MS;
    let s = side.s();
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms <= t0 {
            continue;
        }
        if ms > until {
            break;
        }
        let p = s * px;
        if p >= sig.lp {
            return (EntryDec::Reject(EntryRej::Touched), Some(ms));
        }
        if i128::from(p - pref) * 10_000 < need {
            continue;
        }
        let t1 = ms + ex.lat_ms;
        let dec = entry_at(src, sym, side, (t1 / DAY_MS) as usize, t1, sig.lp, ex);
        return (dec, Some(t1));
    }
    (EntryDec::Reject(EntryRej::Unfilled), Some(until))
}

/// TK-669 (`xb`): вход за уровнем, обе стороны в раме. Опора и срок — как у `ek`: вход по ленте §13.16 на `сигнал + lat` (`entry_decision`; её
/// отказ — отказ сигнала, приказа нет, второй элемент `None`), приказ живёт `EK_EXPIRY_MS` с этого момента. Порог рамы
/// `T = lp + |lp|·NATR·xb/10³` (`natr_e8` — NATR выхода на сигнале, `xb_m` — X в тысячных NATR): первая сделка ленты (любой стороны) позже
/// момента опоры с ценой `p ≥ T` — срабатывание в `ms`; вход — рыночный по ленте §13.16 на `ms + lat` (`entry_at_ex` без проверки «за уровнем»;
/// отказ — `Stale`). Не сработал за срок — `Unfilled`. Отмены «коснулась уровня» нет: цена пересекает уровень до порога по построению.
/// Второй элемент — момент снятия / исполнения приказа (занятость монеты и места в портфеле до него, `run_tick_xb`).
pub fn xb_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    natr_e8: i64,
    xb_m: i64,
) -> (EntryDec, Option<i64>) {
    let t0 = match entry_decision(src, sym, side, sig, ex) {
        EntryDec::Fill { ms, .. } => ms,
        other => return (other, None),
    };
    let thr = sig.lp + mul_div(sig.lp.abs(), natr_e8 * xb_m, SCALE * 1000) as i64;
    let until = t0 + EK_EXPIRY_MS;
    let s = side.s();
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms <= t0 {
            continue;
        }
        if ms > until {
            break;
        }
        if s * px < thr {
            continue;
        }
        let t1 = ms + ex.lat_ms;
        let dec = entry_at_ex(
            src,
            sym,
            side,
            (t1 / DAY_MS) as usize,
            t1,
            sig.lp,
            ex,
            false,
        );
        return (dec, Some(t1));
    }
    (EntryDec::Reject(EntryRej::Unfilled), Some(until))
}

// ───────────────────────────── §13.28: вход лимиткой (мейкер), опция `mk` ─────────────────────────────

/// §13.28: минимум сделок окна для определения тика.
pub const MK_MIN_TRADES: usize = 100;

/// §13.28: окно тика — столько календарных суток до суток `T` плюс сутки `T` до `T`.
pub const MK_TICK_DAYS: usize = 7;

fn gcd(mut a: i64, mut b: i64) -> i64 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

/// §13.28: цены набора сделок для тика — НОД разностей к первой цене, первая цена, число сделок (`n = 0` — набор пуст).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PxGcd {
    pub g: i64,
    pub p0: i64,
    pub n: usize,
}

impl PxGcd {
    pub fn of(ticks: &[Tick]) -> PxGcd {
        let Some(first) = ticks.first() else {
            return PxGcd::default();
        };
        let mut g = 0;
        for t in ticks {
            if g != 1 {
                g = gcd(g, (t.px - first.px).abs());
            }
        }
        PxGcd {
            g,
            p0: first.px,
            n: ticks.len(),
        }
    }

    /// Объединение двух наборов: НОД всех разностей цен обоих наборов.
    pub fn merge(self, o: PxGcd) -> PxGcd {
        if self.n == 0 {
            return o;
        }
        if o.n == 0 {
            return self;
        }
        PxGcd {
            g: gcd(gcd(self.g, o.g), (o.p0 - self.p0).abs()),
            p0: self.p0,
            n: self.n + o.n,
        }
    }

    fn tick(self) -> Option<i64> {
        (self.n >= MK_MIN_TRADES && self.g > 0).then_some(self.g)
    }
}

/// TK-683: НОД каждого начала суток без повторного прохода по сделкам. НОД разностей к первой цене при добавлении сделок только
/// делится, поэтому меняется считаные разы: хранятся ступени (число сделок, после которого НОД стал таким; НОД).
const GCD_MAGIC: [u8; 4] = *b"GC42";
const GCD_HEAD_LEN: usize = 4 + 8 + 8 + 4;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct GcdSteps {
    p0: i64,
    n: usize,
    steps: Vec<(usize, i64)>,
}

impl GcdSteps {
    pub fn of(ticks: &[Tick]) -> GcdSteps {
        let Some(first) = ticks.first() else {
            return GcdSteps::default();
        };
        let mut g = 0;
        let mut steps = Vec::new();
        for (i, t) in ticks.iter().enumerate() {
            if g != 1 {
                let d = (t.px - first.px).abs();
                // d кратно g — НОД прежний (частый случай: одно деление вместо цикла)
                if g == 0 || d % g != 0 {
                    g = gcd(g, d);
                    if g != 0 {
                        steps.push((i + 1, g));
                    }
                }
            }
        }
        GcdSteps {
            p0: first.px,
            n: ticks.len(),
            steps,
        }
    }

    /// Ступени посчитаны по этим суткам? Сверка файла ступеней с загруженными сделками: число сделок и первая цена.
    pub fn fits(&self, ticks: &[Tick]) -> bool {
        self.n == ticks.len() && ticks.first().is_none_or(|t| t.px == self.p0)
    }

    /// Запись в файл ступеней (`ZipSrc::gcd_file`): метка, первая цена, число сделок, ступени; числа — little-endian.
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut b = Vec::with_capacity(GCD_HEAD_LEN + 16 * self.steps.len());
        b.extend_from_slice(&GCD_MAGIC);
        b.extend_from_slice(&self.p0.to_le_bytes());
        b.extend_from_slice(&(self.n as u64).to_le_bytes());
        b.extend_from_slice(&(self.steps.len() as u32).to_le_bytes());
        for &(k, g) in &self.steps {
            b.extend_from_slice(&(k as u64).to_le_bytes());
            b.extend_from_slice(&g.to_le_bytes());
        }
        b
    }

    /// Разбор `to_bytes`; запись другой длины или с чужой меткой — `None` (сутки будут разобраны заново).
    pub fn from_bytes(b: &[u8]) -> Option<GcdSteps> {
        let num = |i: usize| -> Option<[u8; 8]> { b.get(i..i + 8)?.try_into().ok() };
        if b.get(..4)? != GCD_MAGIC {
            return None;
        }
        let k = u32::from_le_bytes(b.get(20..24)?.try_into().ok()?) as usize;
        if b.len() != GCD_HEAD_LEN + 16 * k {
            return None;
        }
        let mut steps = Vec::with_capacity(k);
        for i in 0..k {
            let at = GCD_HEAD_LEN + 16 * i;
            steps.push((
                usize::try_from(u64::from_le_bytes(num(at)?)).ok()?,
                i64::from_le_bytes(num(at + 8)?),
            ));
        }
        Some(GcdSteps {
            p0: i64::from_le_bytes(num(4)?),
            n: usize::try_from(u64::from_le_bytes(num(12)?)).ok()?,
            steps,
        })
    }

    /// То же, что `PxGcd::of(&ticks[..k])`.
    pub fn prefix(&self, k: usize) -> PxGcd {
        let k = k.min(self.n);
        if k == 0 {
            return PxGcd::default();
        }
        let i = self.steps.partition_point(|s| s.0 <= k);
        PxGcd {
            g: if i == 0 { 0 } else { self.steps[i - 1].1 },
            p0: self.p0,
            n: k,
        }
    }

    /// То же, что `PxGcd::of(ticks)`.
    pub fn full(&self) -> PxGcd {
        self.prefix(self.n)
    }
}

/// §13.28: тик монеты на момент `t` — НОД разностей цен сделок строго до `t` за предыдущие `MK_TICK_DAYS` суток и сутки `t` до `t`;
/// окно меньше `MK_MIN_TRADES` сделок — последнее более раннее окно (то же правило, закончившееся сутками раньше), в котором тик определён.
/// Сделок позже `t` не читает. `cache` — НОД полных суток монеты (ключ — номер монеты и сутки).
pub fn tick_at(
    src: &dyn TradeSrc,
    name: &str,
    sym_i: u16,
    t: i64,
    cache: &mut HashMap<(u16, usize), PxGcd>,
) -> Option<i64> {
    let day = t.div_euclid(DAY_MS) as usize;
    let mut full = |d: usize| -> PxGcd {
        *cache.entry((sym_i, d)).or_insert_with(|| {
            src.gcd_steps(name, d)
                .map_or(PxGcd::default(), |s| s.full())
        })
    };
    let part = src.day(name, day).map_or(PxGcd::default(), |v| {
        let off = t - day as i64 * DAY_MS;
        let k = v.partition_point(|x| i64::from(x.off()) < off);
        src.gcd_steps(name, day)
            .map_or(PxGcd::default(), |s| s.prefix(k))
    });
    let mut w = part;
    for d in day.saturating_sub(MK_TICK_DAYS)..day {
        w = full(d).merge(w);
    }
    if let Some(g) = w.tick() {
        return Some(g);
    }
    for e in (0..day).rev() {
        let mut w = PxGcd::default();
        for d in e.saturating_sub(MK_TICK_DAYS)..=e {
            w = w.merge(full(d));
        }
        if let Some(g) = w.tick() {
            return Some(g);
        }
    }
    None
}

/// Конец минуты, содержащей `t` (мс).
fn minute_end(t: i64) -> i64 {
    (t.div_euclid(MIN_MS) + 1) * MIN_MS
}

/// §13.28: заявка мейкера на вход, выставляемая в момент `t` (мс от 01.09) при цене уровня `lp` (рама); живёт до `until` (мс, не включая).
/// Допуск по ленте — как у тейкера (`entry_at`: `Stale` / `Beyond`). Цена-заменитель `p` — последняя сделка стороны ≤ `t`; тик — `tick_of(t)`.
/// `mode = 1` (mk1): лимит `p − тик` (рама); снимается сделкой `≥ lp` (`Touched`); заполняет первая сделка после `t` строго ниже лимита.
/// `mode = 2` (mk2): лимит — цена уровня, округлённая вниз на сетку заменителя (`p + ⌊(lp − p)/тик⌋·тик`); заявка рабочая после пробоя —
/// первой сделки после `t` с ценой `≥ lp` плюс `lat`; заполняет первая сделка строго после этого момента строго ниже лимита.
/// Заполнение — по цене лимита, время — время сквозной сделки. `from` — (сутки, индекс) первой сделки курсора после сигнальной.
#[allow(clippy::too_many_arguments)]
pub fn mk_order(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    from: (usize, usize),
    day: usize,
    t: i64,
    lp: i64,
    ex: &ExitCfg,
    mode: u8,
    until: i64,
    tick_of: &mut dyn FnMut(i64) -> Option<i64>,
) -> EntryDec {
    let p_sur = match entry_at(src, sym, side, day, t, lp, ex) {
        EntryDec::Fill { p0, .. } => p0,
        other => return other,
    };
    let Some(tick) = tick_of(t) else {
        return EntryDec::Reject(EntryRej::NoTick);
    };
    let s = side.s();
    let limit = if mode == 1 {
        p_sur - tick
    } else {
        p_sur + (lp - p_sur).div_euclid(tick) * tick
    };
    let mut live_from = if mode == 1 { t } else { NONE_MS };
    let mut cur = Cursor::new(src, &sym.name, from.0, from.1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms <= t {
            continue;
        }
        if ms >= until {
            break;
        }
        let p = s * px;
        if mode == 1 {
            if p >= lp {
                return EntryDec::Reject(EntryRej::Touched);
            }
        } else if live_from == NONE_MS {
            if p >= lp {
                live_from = ms + ex.lat_ms;
            }
            continue;
        }
        if ms > live_from && p < limit {
            return EntryDec::Fill { ms, p0: limit };
        }
    }
    EntryDec::Reject(EntryRej::Unfilled)
}

/// §13.28: решение о входе лимиткой для сигнала (лот 1): заявка в `сигнал + lat`, жизнь — до конца минуты этого момента.
pub fn mk_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    mode: u8,
    tick_of: &mut dyn FnMut(i64) -> Option<i64>,
) -> EntryDec {
    let t = sig.ms + ex.lat_ms;
    mk_order(
        src,
        sym,
        side,
        (usize::from(sig.day), sig.idx as usize + 1),
        usize::from(sig.day),
        t,
        sig.lp,
        ex,
        mode,
        minute_end(t),
        tick_of,
    )
}

/// Второй лот лимиткой (§13.28): как `lot2_decision`, но заявка подхода (`t2 = ms + lat`) — мейкер mk1, живёт до конца минуты `t2` и не дольше
/// выхода лота 1; не заполнилась / уровень сорван — `Cancel`; у mk2 лот 2 заполняется тем же событием и по той же цене, что лот 1.
#[allow(clippy::too_many_arguments)]
pub fn lot2_decision_mk(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    lot1: (i64, i64),
    lot1_exit_ms: i64,
    mode: u8,
    tick_of: &mut dyn FnMut(i64) -> Option<i64>,
) -> Lot2Dec {
    let d = lot2_dist(sig.lp, ex.e2_pm);
    lot2_decision_mk_d(
        src,
        sym,
        side,
        sig,
        ex,
        lot1,
        lot1_exit_ms,
        mode,
        tick_of,
        d,
    )
}

/// `lot2_decision_mk` с расстоянием `d` в единицах цены (TK-677 `e2t`).
#[allow(clippy::too_many_arguments)]
pub fn lot2_decision_mk_d(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    lot1: (i64, i64),
    lot1_exit_ms: i64,
    mode: u8,
    tick_of: &mut dyn FnMut(i64) -> Option<i64>,
    d: i64,
) -> Lot2Dec {
    let (ms1, p1) = lot1;
    if mode == 2 || p1 >= sig.lp - d {
        return Lot2Dec::Fill {
            ms: ms1,
            p0: p1,
            immediate: true,
        };
    }
    let s = side.s();
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms <= ms1 {
            continue;
        }
        if ms >= lot1_exit_ms {
            return Lot2Dec::Cancel;
        }
        let p = s * px;
        if p >= sig.lp {
            return Lot2Dec::Cancel;
        }
        if p >= sig.lp - d {
            let t2 = ms + ex.lat_ms;
            if t2 >= lot1_exit_ms {
                return Lot2Dec::Cancel;
            }
            let until = minute_end(t2).min(lot1_exit_ms);
            let dec = mk_order(
                src,
                sym,
                side,
                (usize::from(sig.day), sig.idx as usize + 1),
                (t2 / DAY_MS) as usize,
                t2,
                sig.lp,
                ex,
                1,
                until,
                tick_of,
            );
            return match dec {
                EntryDec::Fill { ms, p0 } => Lot2Dec::Fill {
                    ms,
                    p0,
                    immediate: false,
                },
                EntryDec::Reject(EntryRej::Unfilled | EntryRej::Touched) => Lot2Dec::Cancel,
                _ => Lot2Dec::Reject,
            };
        }
    }
    Lot2Dec::Cancel
}

/// TK-688 (`e1t`): вся ставка одной лимитной заявкой в `|ticks|` тиков от уровня; `ticks` со знаком: > 0 — со стороны подхода (лимит `L − ticks·тик`
/// в раме), < 0 — за уровнем (лимит `L + |ticks|·тик`). Заявка встаёт в `сигнал + lat` и живёт до конца минуты этого момента (как `mk`); допуск по
/// ленте и тик — как у `mk_order` (`Stale` / `Beyond` / `NoTick`). Цена — уровень, округлённый вниз на сетку заменителя (как mk2), минус `ticks·тик`.
/// В книге заявка стоит, только пока рынок не ниже лимита (зона как у `e2t`: `p ≥ L − N·тик`; лимитная покупка выше рынка исполнилась бы сразу
/// как тейкер — мейкером она не была бы): последняя сделка стороны ≥ лимита — со времени постановки, иначе — с первой сделки `p ≥ лимит` плюс `lat`.
/// Заполняет первая сделка строго позже этого момента строго ниже лимита (сделка на цене лимита — очередь впереди — не заполняет), по цене лимита.
/// Со стороны подхода сделка `p ≥ L` до заполнения снимает заявку (`Touched`; последняя сделка стороны ≥ L при постановке — тоже); за уровнем снятия
/// касанием нет: заявка встаёт после пробоя на `|ticks|` тиков и заполняется возвратом цены ниже лимита (ретест), как mk2 со сдвигом.
pub fn e1_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    ticks: i64,
    tick_of: &mut dyn FnMut(i64) -> Option<i64>,
) -> EntryDec {
    let t = sig.ms + ex.lat_ms;
    let day = usize::from(sig.day);
    let p_sur = match entry_at(src, sym, side, day, t, sig.lp, ex) {
        EntryDec::Fill { p0, .. } => p0,
        other => return other,
    };
    let Some(tick) = tick_of(t) else {
        return EntryDec::Reject(EntryRej::NoTick);
    };
    let approach = ticks > 0;
    if approach && p_sur >= sig.lp {
        return EntryDec::Reject(EntryRej::Touched);
    }
    let limit = p_sur + (sig.lp - p_sur).div_euclid(tick) * tick - ticks * tick;
    let until = minute_end(t);
    let mut live_from = if p_sur >= limit { t } else { NONE_MS };
    let s = side.s();
    let mut cur = Cursor::new(src, &sym.name, day, sig.idx as usize + 1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms <= t {
            continue;
        }
        if ms >= until {
            break;
        }
        let p = s * px;
        if approach && p >= sig.lp {
            return EntryDec::Reject(EntryRej::Touched);
        }
        if live_from == NONE_MS {
            if p >= limit {
                live_from = ms + ex.lat_ms;
            }
            continue;
        }
        if ms > live_from && p < limit {
            return EntryDec::Fill { ms, p0: limit };
        }
    }
    EntryDec::Reject(EntryRej::Unfilled)
}

/// Вход прежней модели для сигнала (§13.2 п. 2): (время, цена ×1e8 без проскальзывания) первой сделки стороны со временем
/// ≥ `сигнал + lat` после сигнальной; справочно для сравнения с §13.16.
pub fn old_entry(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
) -> Option<(i64, i64)> {
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    let taker_bm = side == Side::Short;
    while let Some((ms, px, bm)) = cur.next_tick() {
        if ms >= sig.ms + ex.lat_ms && bm == taker_bm {
            return Some((ms, px));
        }
    }
    None
}

/// Цена входа прежней модели с проскальзыванием (`px` — настоящая цена сделки, ×1e8).
fn with_slip(side: Side, px: i64, slip_bps: i64) -> i64 {
    let p0 = side.s() * px;
    side.s() * (p0 + frac(p0.abs(), slip_bps, 10_000))
}

/// Деньги сделки (как `portfolio::settle`, но по времени сделок): валовая, комиссии, фандинг, суточные приращения.
pub fn settle_tick(sym: &Sym, side: Side, tr: &TickOut) -> Settled {
    settle_tick_n(sym, side, tr, NOTIONAL_U)
}

/// `settle_tick` для лота размера `notional` (мкУЕ): §13.19 — два входа по 50 %.
pub fn settle_tick_n(sym: &Sym, side: Side, tr: &TickOut, notional: i64) -> Settled {
    settle_tick_mk(sym, side, tr, notional, true)
}

/// `settle_tick_n` с видом комиссии входа: `entry_taker = false` — лимитный вход (§13.28, мейкер).
pub fn settle_tick_mk(
    sym: &Sym,
    side: Side,
    tr: &TickOut,
    notional: i64,
    entry_taker: bool,
) -> Settled {
    let s = side.s() as f64;
    let e = tr.entry_px as f64;
    let n0 = notional as f64 / 1e6;
    let mut flows: Vec<(i64, f64)> = Vec::new();
    let entry_fee = n0 * leg_fee_bps(entry_taker) / 1e4;
    flows.push((tr.entry_ms, -entry_fee));
    let mut gross = 0.0;
    let mut fees = entry_fee;
    for leg in &tr.legs {
        let u = leg.u as f64 / 1e6;
        let g = u * s * (leg.px - tr.entry_px) as f64 / e;
        let fee = u * (leg.px as f64 / e) * leg_fee_bps(!leg.maker) / 1e4;
        gross += g;
        fees += fee;
        flows.push((leg.ms, g - fee));
    }
    // фандинг: вход строго раньше момента выплаты, выход строго позже
    let mut funding = 0.0;
    for f in &sym.fund {
        let fms = i64::from(f.min) * MIN_MS;
        if !(fms > tr.entry_ms && fms < tr.exit_ms) {
            continue;
        }
        let closed: i64 = tr
            .legs
            .iter()
            .filter(|l| l.maker && l.ms < fms)
            .map(|l| l.u)
            .sum();
        let rem = (notional - closed) as f64 / 1e6;
        let pay = -s * (f.rate_e8 as f64 / 1e8) * rem * (f.mark as f64 / e);
        funding += pay;
        flows.push((fms, pay));
    }
    let g0 = (tr.entry_ms / DAY_MS) as usize;
    let g1 = (tr.exit_ms / DAY_MS) as usize;
    let d_entry = g0 - START_DAY;
    let mut prev = 0.0;
    let mut daily = Vec::new();
    for g in g0..=g1 {
        let b = (g as i64 + 1) * DAY_MS;
        let realized: f64 = flows.iter().filter(|(m, _)| *m < b).map(|(_, v)| v).sum();
        let unreal = if tr.exit_ms < b {
            0.0
        } else {
            let closed: i64 = tr
                .legs
                .iter()
                .filter(|l| l.maker && l.ms < b)
                .map(|l| l.u)
                .sum();
            let rem = (notional - closed) as f64 / 1e6;
            let mk = tr
                .marks
                .iter()
                .find(|(d, _)| *d == g)
                .map_or(tr.entry_px, |(_, p)| *p);
            rem * s * (mk - tr.entry_px) as f64 / e
        };
        let cum = realized + unreal;
        let d = g - START_DAY;
        let attr = if d_entry < HALF_DAYS {
            d.min(HALF_DAYS - 1)
        } else {
            d
        };
        daily.push((attr, cum - prev));
        prev = cum;
    }
    Settled {
        gross,
        fees,
        funding,
        daily,
    }
}

#[derive(Debug, Clone)]
pub struct TickTrade {
    pub sym: u16,
    pub level: u32,
    pub sig_ms: i64,
    pub sig_min: u32,
    pub entry_ms: i64,
    pub exit_ms: i64,
    pub entry_px: i64,
    pub kind: ExitKind,
    pub takes: u32,
    pub gross: f64,
    pub fees: f64,
    pub funding: f64,
    pub net: f64,
    /// Ноги сделки (тейки и остаток), NATR выхода (e8) и расстояние сигнала до уровня (×1e8) — для диагностики.
    pub legs: Vec<TLeg>,
    pub natr_e8: i64,
    pub dist: u32,
    /// §13.13: сигнал помечен `ap = 2` (цена пришла в зону сверху).
    pub ws: bool,
    /// §13.16 (только при `ew > 0`): вход прежней модели для этого сигнала — (время мс от 01.09, цена ×1e8 с проскальзыванием).
    pub old_entry: Option<(i64, i64)>,
    /// §13.19 (`e2`): второй лот (свой вход, ноги, выход); `entry_ms`, `entry_px`, `legs` выше — лот 1, `exit_ms` — последний выход.
    pub lot2: Option<TickOut>,
    /// §13.19: вид выхода лота 1 (при `lot2` поле `kind` — вид сделки целиком).
    pub kind1: ExitKind,
    /// v22 (`tl`): приказ трейла лота 1 — (мс срабатывания, настоящая цена ×1e8); `None` — приказа не было.
    pub tl: Option<(i64, i64)>,
    /// TK-676 (`vf`): расстояние стопа от волатильности, тысячные процента цены входа; −1 — стопа нет или нет значения источника (остался жёсткий `s`).
    pub vd_pm: i64,
    /// TK-677: чистый P&L лота 1 при включённом втором входе (лот 2 — `net − net1`); `None` — позиция одним лотом.
    pub net1: Option<f64>,
    /// TK-743 (`--keep-orders`): все заявки сделки — входы (`e1`, `e2`) и выходы лотов (тейки, стоп, правила); без ключа пуст.
    pub ord: Vec<OrdEv>,
}

#[derive(Debug, Clone)]
pub struct TickResult {
    pub trades: Vec<TickTrade>,
    /// §13.16: отвергнутые входом сигналы (монета, уровень, `sig_ms`, причина).
    pub entry_rej: Vec<(u16, u32, i64, EntryRej)>,
    pub daily: [f64; COUNT_DAYS],
    pub coinday: BTreeMap<(u16, usize), f64>,
    /// Отказы: уровень израсходован, монета занята, пауза монеты, общая пауза, потолок P, нет исхода.
    pub rej: [u64; 6],
    /// §13.19 (`e2`): лот 2 — вошёл сразу (цена лота 1 уже в пределах d), вошёл позже, отменён (лот 1 закрыт / цена пересекла уровень), отказ (за уровнем / нет ленты).
    pub lot2: [u64; 4],
    /// §13.28 (`mk > 0`): заявки лота 1 — заполнено, не заполнено, снято уровнем (число отказов `notick` — в `entry_rej`).
    pub mk: [u64; 3],
    /// TK-660 (`ek > 0`): приказы E(k) — исполнено, снято за 60 мин, снято уровнем, отказ входа после срабатывания (`stale` / `beyond`),
    /// не поставлено (порог за уровнем, `noroom`).
    pub ek: [u64; 5],
    /// TK-669 (`xb > 0`): приказы на пробой — исполнено, снято за 60 мин, отказ входа после срабатывания (нет ленты стороны).
    pub xb: [u64; 3],
    /// TK-681 (`sm`): сделки (по лоту 1) — стоп от уровня или `nx` сдвинут нижним зажимом; прежний зажим `вход − 1e-8` сработал бы.
    pub floor: [u64; 2],
    /// TK-679 (`pr`, `pw`): окон сбора; окон с двумя и более кандидатами; окон, где первым по `pr` стал не первый по прежнему порядку;
    /// входов после окна; отказов входа после окна (уровень достигнут / нет ленты / за уровнем); сигналов, переставленных `pr` внутри одной мс.
    pub prio: [u64; 6],
}

impl TickResult {
    /// Пустой результат (режим `P42_SIGSX_ONLY`, §13.18: только сигналы и дампы, без портфеля и без сделок).
    pub fn empty() -> TickResult {
        TickResult {
            trades: Vec::new(),
            entry_rej: Vec::new(),
            daily: [0.0; COUNT_DAYS],
            coinday: BTreeMap::new(),
            rej: [0; 6],
            lot2: [0; 4],
            mk: [0; 3],
            ek: [0; 5],
            xb: [0; 3],
            floor: [0; 2],
            prio: [0; 6],
        }
    }
}

/// TK-679 (`pr`): значение приоритета кандидата по закрытым минутам до минуты сигнала `t` (больше — раньше): 1 — NATR(30) минуты `t−1`,
/// 2 — оборот за 15 минут `t−15 … t−1`, 3 — за 60; 0 — приоритета нет (прежний порядок).
pub fn prio_val(sym: &Sym, t: usize, pr: u8) -> i64 {
    match pr {
        1 => sym.natr_for(30).map_or(-1, |v| i64::from(v[t - 1])),
        2 => sym.pq[t] - sym.pq[t - 15],
        3 => sym.pq[t] - sym.pq[t - 60],
        _ => 0,
    }
}

/// TK-679 (`pw`): вход кандидата после окна сбора — решение в `t_dec` (конец окна). Сделка в `(сигнал, t_dec]` на уровне или за ним — кандидат снят
/// (`Touched`); иначе вход по ленте на `t_dec + lat` с прежними проверками (`stale`, `beyond`).
pub fn late_entry(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    t_dec: i64,
) -> EntryDec {
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms > t_dec {
            break;
        }
        if side.s() * px >= sig.lp {
            return EntryDec::Reject(EntryRej::Touched);
        }
    }
    let t = t_dec + ex.lat_ms;
    entry_at(src, sym, side, (t / DAY_MS) as usize, t, sig.lp, ex)
}

/// TK-743: заявки сделки — входы лотов и выходы лотов из `TickOut::ord`. Вход лота 1 — рыночная заявка (лимитная у мейкера), поставлена в момент сигнала, исполнена в момент входа;
/// второй вход (`e2`) — условная заявка на цене `уровень − d`, поставлена в момент входа лота 1, исполнена / снята по решению `lot2_trace`.
#[allow(clippy::too_many_arguments)]
fn entry_orders(
    sg: &TSig,
    ex: &ExitCfg,
    side: Side,
    tr: &TickOut,
    tr2: Option<&TickOut>,
    lots: (i64, i64),
    plan: (bool, i64),
    l2: Option<(i64, Lot2Dec, i64, &'static str)>,
) -> Vec<OrdEv> {
    let s = side.s();
    let (maker, plan_px) = plan;
    let typ = if maker { TY_LIMIT } else { TY_MARKET };
    let note = if maker {
        "maker entry"
    } else if ex.tape_entry() {
        "ew proxy price"
    } else {
        "next side trade"
    };
    let mut v = Vec::with_capacity(tr.ord.len() + tr2.map_or(0, |t| t.ord.len()) + 8);
    let px0 = if plan_px != 0 { plan_px } else { tr.entry_px };
    v.push(OrdEv::new(sg.ms, EV_PLACE, OID_E1, typ, px0, lots.0, note));
    v.push(OrdEv::new(
        tr.entry_ms,
        EV_FILL,
        OID_E1,
        typ,
        tr.entry_px,
        lots.0,
        "",
    ));
    if let Some((trig, dec, ms, why)) = l2 {
        let mut p = OrdEv::new(
            tr.entry_ms,
            EV_PLACE,
            OID_E2,
            TY_COND,
            s * trig,
            lots.1,
            "e2 at level - d",
        );
        p.lot = 1;
        v.push(p);
        let mut e = match (dec, tr2) {
            (Lot2Dec::Fill { .. }, Some(t2)) => {
                let mut f = OrdEv::new(
                    t2.entry_ms,
                    EV_FILL,
                    OID_E2,
                    TY_COND,
                    t2.entry_px,
                    lots.1,
                    why,
                );
                f.aux = ms;
                f
            }
            _ => OrdEv::new(ms, EV_CANCEL, OID_E2, TY_COND, s * trig, lots.1, why),
        };
        e.lot = 1;
        v.push(e);
    }
    v.extend(tr.ord.iter().copied());
    if let Some(t2) = tr2 {
        v.extend(t2.ord.iter().map(|e| OrdEv { lot: 1, ..*e }));
    }
    v
}

struct Open {
    exit_ms: i64,
    sym: usize,
    key: u64,
    kind: ExitKind,
    /// TK-660 (`ek`): не позиция, а живой приказ E(k), снятый без входа: держит монету и место до `exit_ms`, на паузы и серии стопов не влияет.
    hold: bool,
}

/// Портфель стороны по сигналам-сделкам (как `portfolio::run`; паузы — в мс).
pub fn run_tick(
    syms: &[Sym],
    sigs: &[TSig],
    side: Side,
    ex: &ExitCfg,
    pc: &PortCfg,
    src: &dyn TradeSrc,
) -> TickResult {
    run_tick_mk(syms, sigs, side, ex, pc, src, 0)
}

/// `run_tick` с входом лимиткой (§13.28): `mk = 0` — прежний путь тейкера; 1 — mk1; 2 — mk2 (требует `ew > 0`).
pub fn run_tick_mk(
    syms: &[Sym],
    sigs: &[TSig],
    side: Side,
    ex: &ExitCfg,
    pc: &PortCfg,
    src: &dyn TradeSrc,
    mk: u8,
) -> TickResult {
    run_tick_ek(syms, sigs, side, ex, pc, src, mk, 0)
}

/// `run_tick_mk` со входом по подтверждению E(k) (TK-660): `ek = 0` — прежний путь; `ek > 0` — k, бп (требует `ew > 0`, без `mk`).
#[allow(clippy::too_many_arguments)]
pub fn run_tick_ek(
    syms: &[Sym],
    sigs: &[TSig],
    side: Side,
    ex: &ExitCfg,
    pc: &PortCfg,
    src: &dyn TradeSrc,
    mk: u8,
    ek: i64,
) -> TickResult {
    run_tick_xb(syms, sigs, side, ex, pc, src, mk, ek, 0)
}

/// `run_tick_ek` со входом за уровнем (TK-669, `xb`): `xb = 0` — прежний путь; `xb > 0` — X, тысячные NATR (требует `ew > 0`, без `mk`, `ek`, `e2`).
#[allow(clippy::too_many_arguments)]
pub fn run_tick_xb(
    syms: &[Sym],
    sigs: &[TSig],
    side: Side,
    ex: &ExitCfg,
    pc: &PortCfg,
    src: &dyn TradeSrc,
    mk: u8,
    ek: i64,
    xb: i64,
) -> TickResult {
    let ns = syms.len();
    let mut res = TickResult {
        trades: Vec::new(),
        entry_rej: Vec::new(),
        daily: [0.0; COUNT_DAYS],
        coinday: BTreeMap::new(),
        rej: [0; 6],
        lot2: [0; 4],
        mk: [0; 3],
        ek: [0; 5],
        xb: [0; 3],
        floor: [0; 2],
        prio: [0; 6],
    };
    // TK-688 (`e1t`): вход одной лимитной заявкой — мейкер (комиссия, проскальзывание 0, счётчики заявок), как у `mk`
    let maker = mk > 0 || ex.e1t != 0;
    let entry_slip = if maker { 0 } else { ex.slip_bps };
    let mut tick_cache: HashMap<(u16, usize), PxGcd> = HashMap::new();
    let mut busy = vec![false; ns];
    let mut cool_until = vec![0i64; ns];
    let mut cpause_until = vec![0i64; ns];
    let mut streak = vec![0usize; ns];
    let mut gstops: VecDeque<i64> = VecDeque::new();
    let mut gpause_until = 0i64;
    let mut consumed: HashSet<u64> = HashSet::new();
    let mut open: Vec<Open> = Vec::new();
    let min = |m: usize| m as i64 * MIN_MS;
    // TK-679 (`pr`): сигналы с одним и тем же временем — по приоритету, затем прежний порядок
    let by_prio;
    let sigs: &[TSig] = if pc.pr > 0 {
        let mut v = sigs.to_vec();
        v.sort_unstable_by_key(|s| {
            let pv = prio_val(&syms[s.sym as usize], s.t as usize, pc.pr);
            (s.ms, std::cmp::Reverse(pv), s.dist, s.sym, s.level)
        });
        res.prio[5] = v
            .iter()
            .zip(sigs)
            .filter(|(a, b)| (a.sym, a.level) != (b.sym, b.level))
            .count() as u64;
        by_prio = v;
        &by_prio
    } else {
        sigs
    };
    // TK-679 (`pw`): окно сбора — номера сигналов, конец открытого окна, момент решения разбираемого окна
    let mut batch: Vec<u32> = Vec::new();
    let mut batch_end: Option<i64> = None;
    let mut flush: Option<i64> = None;
    let (mut next, mut bi) = (0usize, 0usize);
    loop {
        let (sg, t, late) = if let Some(tf) = flush {
            if bi == batch.len() {
                flush = None;
                batch.clear();
                continue;
            }
            bi += 1;
            (&sigs[batch[bi - 1] as usize], tf, true)
        } else if let Some(e) = batch_end.filter(|&e| next == sigs.len() || sigs[next].ms > e) {
            let old = |k: &u32| {
                let s = &sigs[*k as usize];
                (s.dist, s.sym, s.level)
            };
            let first_old = batch.iter().min_by_key(|&k| old(k)).copied();
            batch.sort_unstable_by_key(|k| {
                let s = &sigs[*k as usize];
                let pv = prio_val(&syms[s.sym as usize], s.t as usize, pc.pr);
                (std::cmp::Reverse(pv), old(k))
            });
            res.prio[1] += u64::from(batch.len() > 1);
            res.prio[2] += u64::from(first_old != batch.first().copied());
            batch_end = None;
            flush = Some(e);
            bi = 0;
            continue;
        } else if next < sigs.len() {
            next += 1;
            (&sigs[next - 1], sigs[next - 1].ms, false)
        } else {
            break;
        };
        while !open.is_empty() && open[0].exit_ms <= t {
            let o = open.remove(0);
            busy[o.sym] = false;
            if o.hold {
                continue;
            }
            cool_until[o.sym] = o.exit_ms + min(pc.cool_min);
            if o.kind == ExitKind::HardStop {
                if pc.reentry == 1 {
                    consumed.insert(o.key);
                }
                streak[o.sym] += 1;
                if streak[o.sym] >= pc.m_stops {
                    cpause_until[o.sym] = o.exit_ms + min(pc.mm_min);
                    streak[o.sym] = 0;
                }
                gstops.push_back(o.exit_ms);
                while let Some(&f) = gstops.front() {
                    if o.exit_ms >= f + min(pc.w_min) {
                        gstops.pop_front();
                    } else {
                        break;
                    }
                }
                if gstops.len() >= pc.s_glob {
                    if pc.ww_min > 0 {
                        gpause_until = o.exit_ms + min(pc.ww_min);
                    }
                    gstops.clear();
                }
            } else {
                streak[o.sym] = 0;
            }
        }
        let si = sg.sym as usize;
        let key = (u64::from(sg.sym) << 32) | u64::from(sg.level);
        if consumed.contains(&key) {
            res.rej[0] += 1;
            continue;
        }
        // §13.21 (`busy = 0`): монета с открытой позицией принимает сигналы других уровней
        if pc.busy && busy[si] {
            res.rej[1] += 1;
            continue;
        }
        if t < cool_until[si] || t < cpause_until[si] {
            res.rej[2] += 1;
            continue;
        }
        if t < gpause_until {
            res.rej[3] += 1;
            continue;
        }
        if open.len() >= pc.p {
            res.rej[4] += 1;
            continue;
        }
        if pc.pw_ms > 0 && !late {
            // идёт окно — кандидат в него; последнее свободное место — кандидат открывает окно
            if batch_end.is_some() {
                batch.push((next - 1) as u32);
                continue;
            }
            if open.len() + 1 >= pc.p {
                batch_end = Some(t + pc.pw_ms);
                batch.push((next - 1) as u32);
                res.prio[0] += 1;
                continue;
            }
        }
        let sym = &syms[si];
        let natr = exit_natr(sym, ex, sg.t as usize);
        let mut dec = EntryDec::Next;
        let mut tr2: Option<TickOut> = None;
        let lc = level_ctx(sym, side, sg, ex);
        let split = ex.split();
        let (lot_n, lot2_n) = ex.lots(NOTIONAL_U);
        let mut ek_end: Option<i64> = None;
        let mut xb_end: Option<i64> = None;
        // TK-743: решение о втором входе для журнала заявок — (цена условия, вердикт, момент события, причина)
        let mut l2_ord: Option<(i64, Lot2Dec, i64, &'static str)> = None;
        let mut plan_px = 0i64;
        let tr = if natr > 0 {
            dec = if ex.e1t != 0 {
                let mut tick_of = |t: i64| tick_at(src, &sym.name, sg.sym, t, &mut tick_cache);
                e1_decision(src, sym, side, sg, ex, ex.e1t, &mut tick_of)
            } else if maker {
                let mut tick_of = |t: i64| tick_at(src, &sym.name, sg.sym, t, &mut tick_cache);
                mk_decision(src, sym, side, sg, ex, mk, &mut tick_of)
            } else if ek > 0 {
                let (d, end) = ek_decision(src, sym, side, sg, ex, ek);
                ek_end = end;
                d
            } else if xb > 0 {
                let (d, end) = xb_decision(src, sym, side, sg, ex, natr, xb);
                xb_end = end;
                d
            } else if late {
                let d = late_entry(src, sym, side, sg, ex, t);
                res.prio[if matches!(d, EntryDec::Fill { .. }) {
                    3
                } else {
                    4
                }] += 1;
                d
            } else {
                entry_decision(src, sym, side, sg, ex)
            };
            match dec {
                EntryDec::Next => simulate_tick(src, sym, side, sg, natr, ex),
                EntryDec::Fill { ms, p0 } => {
                    plan_px = side.s() * p0;
                    let t1 = sim_fill_mem(
                        src, &sym.name, ms, p0, side, natr, ex, lc, lot_n, entry_slip, None,
                    );
                    if maker {
                        res.mk[0] += 1;
                    }
                    if ek_end.is_some() {
                        res.ek[0] += 1;
                    }
                    if xb_end.is_some() {
                        res.xb[0] += 1;
                    }
                    if split {
                        // TK-677 (`e2t`): расстояние второго входа — N тиков; тик — по сделкам строго до входа лота 1 (нет тика — отказ лота 2)
                        let d2 = if ex.e2t > 0 {
                            tick_at(src, &sym.name, sg.sym, ms, &mut tick_cache)
                                .map(|tk| ex.e2t * tk)
                        } else {
                            Some(lot2_dist(sg.lp, ex.e2_pm))
                        };
                        let l2 = match d2 {
                            None => Lot2Dec::Reject,
                            Some(d2) if maker => {
                                let mut tick_of =
                                    |t: i64| tick_at(src, &sym.name, sg.sym, t, &mut tick_cache);
                                lot2_decision_mk_d(
                                    src,
                                    sym,
                                    side,
                                    sg,
                                    ex,
                                    (ms, p0),
                                    t1.exit_ms,
                                    mk,
                                    &mut tick_of,
                                    d2,
                                )
                            }
                            Some(d2) => {
                                let (l, m, why) =
                                    lot2_trace(src, sym, side, sg, ex, (ms, p0), t1.exit_ms, d2);
                                l2_ord = Some((sg.lp - d2, l, m, why));
                                l
                            }
                        };
                        // TK-677 (`s2`): общий стоп позиции — лот 2 наследует цену жёсткого стопа лота 1
                        let stop2 = ex
                            .s2
                            .then(|| entry_stop_px(p0, side, natr, ex, Some(lc), entry_slip));
                        match l2 {
                            Lot2Dec::Fill {
                                ms: m2,
                                p0: q0,
                                immediate,
                            } => {
                                res.lot2[usize::from(!immediate)] += 1;
                                tr2 = Some(sim_fill_mem(
                                    src, &sym.name, m2, q0, side, natr, ex, lc, lot2_n, entry_slip,
                                    stop2,
                                ));
                            }
                            Lot2Dec::Cancel => res.lot2[2] += 1,
                            Lot2Dec::Reject => res.lot2[3] += 1,
                        }
                        if maker && ex.keep_orders {
                            // заявки мейкера (`mk`, `e1t`) в журнале — без подробностей: условие и причина не восстанавливаются
                            l2_ord = Some((
                                sg.lp - d2.unwrap_or(0),
                                l2,
                                t1.exit_ms,
                                "maker lot 2: untraced",
                            ));
                        }
                    }
                    Some(t1)
                }
                EntryDec::Reject(why) => {
                    if maker {
                        match why {
                            EntryRej::Unfilled => res.mk[1] += 1,
                            EntryRej::Touched => res.mk[2] += 1,
                            _ => {}
                        }
                    }
                    if why == EntryRej::NoRoom {
                        res.ek[4] += 1;
                    }
                    if xb_end.is_some() {
                        res.xb[usize::from(why != EntryRej::Unfilled) + 1] += 1;
                    }
                    if let Some(end) = ek_end.or(xb_end) {
                        // TK-660 / TK-669: живой приказ E(k) / на пробой занимал монету и место в портфеле до снятия
                        if ek_end.is_some() {
                            res.ek[match why {
                                EntryRej::Unfilled => 1,
                                EntryRej::Touched => 2,
                                _ => 3,
                            }] += 1;
                        }
                        busy[si] = true;
                        open.push(Open {
                            exit_ms: end,
                            sym: si,
                            key,
                            kind: ExitKind::Forced,
                            hold: true,
                        });
                        open.sort_by_key(|o| o.exit_ms);
                    }
                    res.entry_rej.push((sg.sym, sg.level, sg.ms, why));
                    None
                }
            }
        } else {
            None
        };
        let Some(tr) = tr else {
            res.rej[5] += 1;
            continue;
        };
        res.floor[0] += u64::from(tr.floor[0]);
        res.floor[1] += u64::from(tr.floor[1]);
        if pc.reentry == 0 {
            consumed.insert(key);
        }
        busy[si] = true;
        // §13.19: позиция из двух лотов — выход последнего лота; «стоп», если хоть один лот закрыт стопом
        let (exit_all, kind_all) = match &tr2 {
            Some(t2) => (
                tr.exit_ms.max(t2.exit_ms),
                if tr.kind == ExitKind::HardStop || t2.kind == ExitKind::HardStop {
                    ExitKind::HardStop
                } else if t2.exit_ms > tr.exit_ms {
                    t2.kind
                } else {
                    tr.kind
                },
            ),
            None => (tr.exit_ms, tr.kind),
        };
        open.push(Open {
            exit_ms: exit_all,
            sym: si,
            key,
            kind: kind_all,
            hold: false,
        });
        open.sort_by_key(|o| o.exit_ms);
        let mut st = settle_tick_mk(sym, side, &tr, lot_n, !maker);
        let net1 = split.then_some(st.gross - st.fees + st.funding);
        if let Some(t2) = &tr2 {
            let s2 = settle_tick_mk(sym, side, t2, lot2_n, !maker);
            st.gross += s2.gross;
            st.fees += s2.fees;
            st.funding += s2.funding;
            st.daily.extend(s2.daily);
        }
        for (d, v) in &st.daily {
            res.daily[*d] += v;
            *res.coinday.entry((sg.sym, *d)).or_insert(0.0) += v;
        }
        let ord = if ex.keep_orders {
            entry_orders(
                sg,
                ex,
                side,
                &tr,
                tr2.as_ref(),
                (lot_n, lot2_n),
                (maker, plan_px),
                l2_ord,
            )
        } else {
            Vec::new()
        };
        res.trades.push(TickTrade {
            sym: sg.sym,
            level: sg.level,
            sig_ms: sg.ms,
            sig_min: sg.t,
            entry_ms: tr.entry_ms,
            exit_ms: exit_all,
            entry_px: tr.entry_px,
            kind: kind_all,
            takes: tr.takes + tr2.as_ref().map_or(0, |t| t.takes),
            gross: st.gross,
            fees: st.fees,
            funding: st.funding,
            net: st.gross - st.fees + st.funding,
            legs: tr.legs.clone(),
            natr_e8: natr,
            dist: sg.dist,
            ws: sg.ws,
            old_entry: if matches!(dec, EntryDec::Fill { .. }) {
                old_entry(src, sym, side, sg, ex)
                    .map(|(ms, px)| (ms, with_slip(side, px, ex.slip_bps)))
            } else {
                None
            },
            kind1: tr.kind,
            tl: tr.tl,
            lot2: tr2,
            vd_pm: volstop::stop_pm(ex, lc.vol, lc.volz).unwrap_or(-1),
            net1,
            ord,
        });
    }
    res
}
