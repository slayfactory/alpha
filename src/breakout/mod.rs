//! П-42: пробой часового уровня на минутках Binance USD-M, лонг и шорт отдельно (агрегатный рантайм).
//!
//! Конвейер: `data` (CSV → кэш `.b42`, цены — `i64` ×1e8 точно как у биржи) → `levels` (часовые бары → пивоты
//! → уровни; зависит только от `LevelCfg`) → `signals` (минутный проход: ложный пробой, зона входа, цвет, пул;
//! зависит от `SigCfg`) → `portfolio` (слияние сигналов монет по минутам: потолок P, порядок, паузы) с `trade`
//! (выход одной сделки по минуткам, худший порядок внутри минутки). Выход сделки зависит только от траектории
//! цены и параметров выхода, поэтому исход считается в момент принятия сигнала.
//!
//! Правила и оси — `docs/research/P-42-level-breakout.md`; уточнения реализации — его §12.
//! Деньги — `f64` (доллары), цены и размеры позиций — целые.
#![allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss
)]

pub mod cli;
pub mod data;
pub mod feat;
pub mod ftab;
pub mod levels;
pub mod params;
pub mod portfolio;
pub mod sigcache;
pub mod signals;
pub mod store;
pub mod store_fill;
pub mod tick;
pub mod tick_cli;
pub mod trade;
pub mod volstop;

#[cfg(test)]
mod lx_tests;
#[cfg(test)]
mod sigcache_tests;
#[cfg(test)]
mod store_tests;
#[cfg(test)]
mod tests;
#[cfg(test)]
mod tick_tests;
#[cfg(test)]
mod volstop_tests;

use std::sync::Mutex;

/// Сторона: лонг ловит пробой потолка (пивоты-максимумы), шорт — пробой пола (пивоты-минимумы).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Side {
    Long,
    Short,
}

impl Side {
    /// Знак: в «длинной раме» цены шорта берутся с обратным знаком.
    pub fn s(self) -> i64 {
        match self {
            Side::Long => 1,
            Side::Short => -1,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Side::Long => "long",
            Side::Short => "short",
        }
    }
}

/// Параллельное отображение: задания раздаются потокам по атомарному счётчику, порядок результатов — как у входа.
pub fn par_map<T: Sync, R: Send>(
    items: &[T],
    threads: usize,
    f: impl Fn(&T) -> R + Sync,
) -> Vec<R> {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let slots: Vec<Mutex<Option<R>>> = items.iter().map(|_| Mutex::new(None)).collect();
    let next = AtomicUsize::new(0);
    std::thread::scope(|sc| {
        for _ in 0..threads.max(1) {
            sc.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(it) = items.get(i) else { break };
                let r = f(it);
                if let Ok(mut g) = slots[i].lock() {
                    *g = Some(r);
                }
            });
        }
    });
    slots
        .into_iter()
        .filter_map(|m| m.into_inner().ok().flatten())
        .collect()
}

/// TK-683: разрешения на счёт. Рабочих фазы 2 больше, чем потоков счёта (`P42_WIDE`): одни сутки сделок разбираются один раз на всех, кто
/// их ждёт, а считают одновременно не больше `threads` рабочих. Ожидающий суток разрешение отдаёт (`pause`).
pub struct Permits {
    free: Mutex<usize>,
    cv: std::sync::Condvar,
}

thread_local! {
    /// Разрешения рабочего потока `par_map_wide`; у прочих потоков — `None`.
    static PERMITS: std::cell::RefCell<Option<std::sync::Arc<Permits>>> = const { std::cell::RefCell::new(None) };
}

impl Permits {
    fn take(&self) {
        let mut g = self.free.lock().unwrap_or_else(|e| e.into_inner());
        while *g == 0 {
            g = self.cv.wait(g).unwrap_or_else(|e| e.into_inner());
        }
        *g -= 1;
    }

    fn give(&self) {
        *self.free.lock().unwrap_or_else(|e| e.into_inner()) += 1;
        self.cv.notify_one();
    }
}

/// Разрешение потока отдано на время ожидания; возвращается при уничтожении. У потока без разрешений — пустышка.
pub struct Paused(Option<std::sync::Arc<Permits>>);

/// Отдать разрешение потока перед ожиданием чужого результата (сутки грузит другой поток).
pub fn pause() -> Paused {
    let p = PERMITS.with(|c| c.borrow().clone());
    if let Some(p) = &p {
        p.give();
    }
    Paused(p)
}

impl Paused {
    /// Счёт внутри ожидания (поток сам оказался загрузчиком): на его время разрешение берётся снова.
    pub fn work<R>(&self, f: impl FnOnce() -> R) -> R {
        if let Some(p) = &self.0 {
            p.take();
        }
        let r = f();
        if let Some(p) = &self.0 {
            p.give();
        }
        r
    }
}

impl Drop for Paused {
    fn drop(&mut self) {
        if let Some(p) = &self.0 {
            p.take();
        }
    }
}

/// `par_map` с `wide` рабочими, из которых одновременно считают не больше `threads`; `wide <= threads` — обычный `par_map`.
pub fn par_map_wide<T: Sync, R: Send>(
    items: &[T],
    wide: usize,
    threads: usize,
    f: impl Fn(&T) -> R + Sync,
) -> Vec<R> {
    if wide <= threads {
        return par_map(items, threads, f);
    }
    let permits = std::sync::Arc::new(Permits {
        free: Mutex::new(threads.max(1)),
        cv: std::sync::Condvar::new(),
    });
    par_map(items, wide, |it| {
        PERMITS.with(|c| *c.borrow_mut() = Some(permits.clone()));
        permits.take();
        let r = f(it);
        permits.give();
        r
    })
}
