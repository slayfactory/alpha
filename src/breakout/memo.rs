//! TK-706: память исходов хода на один запуск движка (`breakout tick`).
//!
//! Ход лота (`sim_fill_lc_s`: тейки, стоп, трейл по сделкам монеты после входа) — чистая функция своих аргументов и ленты сделок монеты; лента неизменна.
//! Варианты одного вызова с одинаковым входом и одинаковыми параметрами хода считают один и тот же ход, поэтому первый вариант считает, остальные берут исход
//! из памяти. Ключ — **весь набор аргументов функции хода по значению** (монета, `entry_ms`, `p0`, сторона, NATR, весь `ExitCfg`, контекст уровня, размер лота,
//! проскальзывание входа, цена общего стопа): новое поле `ExitCfg` / `LevelCtx` меняет ключ само, списка исключений нет. Ни один тик не пропускается:
//! равное вычисление берётся готовым (В-184).
//!
//! Проверка (B3): каждое `verify_every`-е попадание пересчитывается заново и сверяется с памятью; любое расхождение — `panic!` (вызов падает, партия не
//! помечается готовой). Включение: `P42_MEMO=0` выключает; `P42_MEMO_VERIFY=<N>` — каждое N-е попадание (по умолчанию 50, 0 — без проверки, 1 — все:
//! гейт); `P42_MEMO_MAX=<записей>` — предел памяти (по умолчанию 3 млн; выше предела исходы считаются, но не записываются).

use super::params::ExitCfg;
use super::tick::{LevelCtx, TickOut};
use super::Side;
use std::collections::hash_map::RandomState;
use std::collections::HashMap;
use std::hash::BuildHasher;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex, MutexGuard};

const SHARDS: usize = 64;
/// Каждое N-е попадание сверяется пересчётом (если не задано `P42_MEMO_VERIFY`).
pub const VERIFY_DEFAULT: u64 = 50;
/// Предел записей (≈ 1 КБ каждая; 3 млн ≈ 3 ГБ — в 4 раза выше замеренного на полном вызове).
pub const MAX_DEFAULT: u64 = 3_000_000;

/// Все аргументы хода по значению + монета (лента — единственный другой вход функции хода).
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct MoveKey {
    pub coin: Box<str>,
    pub entry_ms: i64,
    pub p0: i64,
    pub side: Side,
    pub natr_e8: i64,
    pub ex: ExitCfg,
    pub lc: Option<LevelCtx>,
    pub notional: i64,
    pub entry_slip_bps: i64,
    pub stop_px: Option<i64>,
}

pub struct MoveMemo {
    hasher: RandomState,
    shards: Vec<Mutex<HashMap<MoveKey, Arc<TickOut>>>>,
    verify_every: u64,
    max_entries: u64,
    moves: AtomicU64,
    hits: AtomicU64,
    distinct: AtomicU64,
    hold_all_ms: AtomicU64,
    hold_distinct_ms: AtomicU64,
    checked: AtomicU64,
    capped: AtomicU64,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn hold_ms(o: &TickOut) -> u64 {
    (o.exit_ms - o.entry_ms).max(0) as u64
}

impl MoveMemo {
    pub fn new(verify_every: u64, max_entries: u64) -> MoveMemo {
        MoveMemo {
            hasher: RandomState::new(),
            shards: (0..SHARDS).map(|_| Mutex::new(HashMap::new())).collect(),
            verify_every,
            max_entries,
            moves: AtomicU64::new(0),
            hits: AtomicU64::new(0),
            distinct: AtomicU64::new(0),
            hold_all_ms: AtomicU64::new(0),
            hold_distinct_ms: AtomicU64::new(0),
            checked: AtomicU64::new(0),
            capped: AtomicU64::new(0),
        }
    }

    /// По окружению (`P42_MEMO`, `P42_MEMO_VERIFY`, `P42_MEMO_MAX`); `None` — память выключена (`P42_MEMO=0`).
    pub fn from_env() -> Option<MoveMemo> {
        let num = |k: &str, d: u64| {
            std::env::var(k)
                .ok()
                .and_then(|v| v.parse::<u64>().ok())
                .unwrap_or(d)
        };
        if std::env::var("P42_MEMO").is_ok_and(|v| v == "0") {
            return None;
        }
        Some(MoveMemo::new(
            num("P42_MEMO_VERIFY", VERIFY_DEFAULT),
            num("P42_MEMO_MAX", MAX_DEFAULT),
        ))
    }

    /// Исход хода по ключу: из памяти или `compute()` (считает ход по свежему потоку сделок монеты). Каждое `verify_every`-е попадание
    /// пересчитывается и сверяется — расхождение даёт `panic!`.
    #[allow(clippy::panic)] // расхождение памяти с пересчётом = порча результатов, счёт обязан упасть
    pub fn get_or_compute(&self, key: MoveKey, compute: &mut dyn FnMut() -> TickOut) -> TickOut {
        let shard = &self.shards[(self.hasher.hash_one(&key) % SHARDS as u64) as usize];
        let found = lock(shard).get(&key).cloned();
        self.moves.fetch_add(1, Relaxed);
        if let Some(a) = found {
            let n = self.hits.fetch_add(1, Relaxed);
            self.hold_all_ms.fetch_add(hold_ms(&a), Relaxed);
            if self.verify_every > 0 && n.is_multiple_of(self.verify_every) {
                let fresh = compute();
                self.checked.fetch_add(1, Relaxed);
                if fresh != *a {
                    panic!(
                        "память исходов: расхождение при сверке: монета {} вход {} мс p0 {}: память {:?}, пересчёт {:?}",
                        key.coin, key.entry_ms, key.p0, a, fresh
                    );
                }
            }
            return (*a).clone();
        }
        let out = compute();
        self.hold_all_ms.fetch_add(hold_ms(&out), Relaxed);
        if self.distinct.load(Relaxed) >= self.max_entries {
            self.capped.fetch_add(1, Relaxed);
            return out;
        }
        // два потока могли посчитать один ключ одновременно: записывается первый, второй — повтор (в различные не идёт)
        if lock(shard).insert(key, Arc::new(out.clone())).is_none() {
            self.distinct.fetch_add(1, Relaxed);
            self.hold_distinct_ms.fetch_add(hold_ms(&out), Relaxed);
        }
        out
    }

    /// Строка итога запуска: ходов, различных ключей, G (по ходам и по времени удержания), сверка.
    pub fn summary(&self) -> String {
        let (moves, hits) = (self.moves.load(Relaxed), self.hits.load(Relaxed));
        let distinct = self.distinct.load(Relaxed);
        let g = |a: u64, b: u64| if b > 0 { a as f64 / b as f64 } else { 0.0 };
        format!(
            "память исходов: ходов {moves}, из памяти {hits}, различных {distinct}, G по ходам {:.3}, G по удержанию {:.3}, сверено {} (каждое {}-е попадание), расхождений 0{}",
            g(moves, distinct),
            g(
                self.hold_all_ms.load(Relaxed),
                self.hold_distinct_ms.load(Relaxed)
            ),
            self.checked.load(Relaxed),
            self.verify_every,
            match self.capped.load(Relaxed) {
                0 => String::new(),
                n => format!("; предел записей {} достигнут, не записано {n}", self.max_entries),
            }
        )
    }

    /// (ходов, из памяти, различных, сверено) — для тестов.
    pub fn counts(&self) -> (u64, u64, u64, u64) {
        (
            self.moves.load(Relaxed),
            self.hits.load(Relaxed),
            self.distinct.load(Relaxed),
            self.checked.load(Relaxed),
        )
    }

    /// Подменить все записанные исходы испорченными (тест проверки сверкой).
    #[cfg(test)]
    pub fn corrupt_all(&self) {
        for sh in &self.shards {
            for v in lock(sh).values_mut() {
                let mut o = (**v).clone();
                o.exit_ms += 1;
                *v = Arc::new(o);
            }
        }
    }
}
