//! Память кругов сетки: `RoundMemo` и память сигналов `SigMemo`.

use super::*;

/// Память кругов сетки (G10, владелец 2026-09-23: «ускорить бэктест без потерь»). В прогоне по
/// сетапам круг — свежий движок над окном `t0`: его итог задают окно, сторона, план и настройка
/// драйвера, а не прежние круги формы и не набор (`--set` только выбирает сигналы). Поэтому круг
/// формы, уже посчитанный для одного набора, другой набор берёт готовым. Ключ — `t0`, сторона и
/// план целиком; вместе с шагом хранятся число израсходованных номеров заявок и сироты круга —
/// нумерация следующих кругов идёт так же, как без памяти (`OrphanCarry::rebased`). Только для
/// `drive_bounce_windowed`: у сплошного прогона круг зависит от состояния движка.
#[derive(Default)]
pub struct RoundMemo {
    // BTreeMap — на границе круга (раз на сигнал, не на событие стакана): путь события его не трогает (А-запрет горячего пути).
    pub(super) by_t0: std::collections::BTreeMap<i64, Vec<MemoEntry>>,
    pub(super) hits: u64,
    pub(super) misses: u64,
}

pub(super) struct MemoEntry {
    pub(super) sigma: i8,
    pub(super) plan: TradePlan,
    /// R2: размер круга — часть ключа, как план: тот же сигнал с другим лотом
    /// — другой круг (очередь, частичное исполнение).
    pub(super) qty: Option<f64>,
    /// Сироты, с которыми круг **начинался** (номера — от базы прогона, `rebased(id_base, 0)`): часть ключа.
    /// Круг наследует сирот прошлого сигнала (`inherit_orphans`), а прошлый сигнал у наборов с разным
    /// допуском разный — круг с другими сиротами на входе из памяти брать нельзя (ревью бага memo 27.09).
    pub(super) carry_in: OrphanCarry,
    pub(super) step: Option<SignalStep>,
    /// База номеров заявок прогона, посчитавшего круг, и сколько номеров круг израсходовал;
    /// `0` — движок сигнала не запускался (сироты входа не трогались).
    pub(super) id_base: u64,
    pub(super) ids_used: u64,
    pub(super) carry_out: OrphanCarry,
}

impl RoundMemo {
    /// Круг из памяти для прогона с базой номеров `id_base`: шаг, число номеров и сироты
    /// (в нумерации этого прогона; `None` — движок не запускался, сироты прежние).
    pub(super) fn recall(
        &mut self,
        sig: &BounceSignal,
        id_base: u64,
        carry_in: OrphanCarry,
    ) -> Option<(Option<SignalStep>, u64, Option<OrphanCarry>)> {
        let carry_key = carry_in.rebased(id_base, 0);
        let hit = self
            .by_t0
            .get(&sig.t0_ns)
            .and_then(|v| {
                v.iter().find(|e| {
                    e.sigma == sig.sigma
                        && e.plan == sig.plan
                        && e.qty == sig.qty
                        && e.carry_in == carry_key
                })
            })
            .map(|e| {
                let carry = (e.ids_used > 0).then(|| e.carry_out.rebased(e.id_base, id_base));
                (e.step.clone(), e.ids_used, carry)
            });
        if hit.is_some() {
            self.hits += 1;
        } else {
            self.misses += 1;
        }
        hit
    }

    // Поля одной записи памяти — по смыслу, структура ради счётчика аргументов — лишний слой.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn store(
        &mut self,
        sig: &BounceSignal,
        step: Option<SignalStep>,
        id_base: u64,
        ids_used: u64,
        carry_in: OrphanCarry,
        carry_out: OrphanCarry,
    ) {
        self.by_t0.entry(sig.t0_ns).or_default().push(MemoEntry {
            sigma: sig.sigma,
            plan: sig.plan,
            qty: sig.qty,
            carry_in: carry_in.rebased(id_base, 0),
            step,
            id_base,
            ids_used,
            carry_out,
        });
    }

    /// Есть ли в памяти круг для сигнала с этими сиротами на входе (Э-08: групповой предсчёт не считает
    /// уже известное); счётчики попаданий не трогает. Сироты — в нумерации от базы 0.
    pub(super) fn contains(&self, sig: &BounceSignal, carry_key: OrphanCarry) -> bool {
        self.by_t0.get(&sig.t0_ns).is_some_and(|v| {
            v.iter().any(|e| {
                e.sigma == sig.sigma
                    && e.plan == sig.plan
                    && e.qty == sig.qty
                    && e.carry_in == carry_key
            })
        })
    }

    /// Сколько кругов взято из памяти и сколько посчитано движком.
    pub fn stats(&self) -> (u64, u64) {
        (self.hits, self.misses)
    }
}

impl std::fmt::Debug for RoundMemo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RoundMemo")
            .field("t0", &self.by_t0.len())
            .field("hits", &self.hits)
            .field("misses", &self.misses)
            .finish()
    }
}

/// Подпись входов последнего вызова круга и число её повторов: пропуск — со второго повтора (первый повтор
/// доводит накопители состояния до неподвижной точки).
#[derive(Clone, Copy, Default)]
pub(super) struct SigMemo {
    pub(super) sig: Option<[u64; 5]>,
    pub(super) reps: u8,
    /// Подпись прошлого шага удержания для счёта `SIG_STATS` (только под `ALPHA_DIAG`).
    pub(super) stat_sig: Option<[u64; 5]>,
}

impl SigMemo {
    /// Можно ли пропустить вызов на этих входах.
    pub(super) fn skip(&mut self, now: Option<[u64; 5]>) -> bool {
        match now {
            Some(s) if self.sig == Some(s) => {
                let skip = self.reps >= 1;
                self.reps = self.reps.saturating_add(1);
                if skip {
                    SIG_SKIPS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                }
                skip
            }
            other => {
                self.sig = other;
                self.reps = 0;
                false
            }
        }
    }

    pub(super) fn reset(&mut self) {
        self.sig = None;
    }

    /// Счёт шага удержания в `SIG_STATS` (`fast` — быстрый путь); подпись считается, только если счёт включён.
    pub(super) fn stat_step(&mut self, fast: bool, sig: impl FnOnce() -> Option<[u64; 5]>) {
        use std::sync::atomic::Ordering::Relaxed;
        if !fast_depth::BAND_STATS_ON.load(Relaxed) {
            return;
        }
        let now = sig();
        let b = if fast { 0 } else { 4 };
        SIG_STATS[b].fetch_add(1, Relaxed);
        let k = match now {
            None => 3,
            Some(s) if self.stat_sig == Some(s) => 1,
            Some(_) => 2,
        };
        SIG_STATS[b + k].fetch_add(1, Relaxed);
        self.stat_sig = now;
    }
}
