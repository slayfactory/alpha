//! Диагностические счётчики прогона (не влияют на результат).

use super::*;

/// Крутит один круг: продвигает часы стороны шагами `ON_EVENT_POLL_STEP_NS`,
/// пока `on_event` не приведёт состояние к `Idle`, и достаёт из `Bot<MD>`
/// исполнение обеих ног. Общий для обоих планов (`SpreadHold` — Decision 20,
/// `Bounce` — В-44): свой цикл решений у второго плана был бы второй
/// стратегией. Заодно считает путь исполнения входа (F3): крейт отдаёт статус
/// и объём, но не говорит, пришло ли исполнение сделкой или обновлением
/// лучшей цены.
///
/// Возвращает исход круга и **свою** позицию стратегии на его конце (F4,
/// В-78): на ней стоит страховка остатка — `Bot::position` крейта частичного
/// исполнения не видит и после частичного входа врёт со знаком.
/// Пропусков пустых шагов удержания (Э-04б) — счётчик процесса для строки итога суток и тестов; на итог
/// счёта не влияет.
pub static HOLD_SKIPS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Пересчётов круга из-за короткого горизонта развёртки (Р6) — счётчик процесса; `bounce-grid` печатает
/// прирост по символу-суткам. Пересчёт не меняет результат, только время.
pub static HORIZON_RETRIES: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Прогонов движка и развёрнутых строк по номеру попытки (0, 1, 2, 3+) — замер TK-049, на итог не влияет.
pub static ATTEMPT_RUNS: [std::sync::atomic::AtomicU64; 4] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 4];

pub static ATTEMPT_ROWS: [std::sync::atomic::AtomicU64; 4] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 4];

/// Строки развёртки, до которых круг дошёл по часам (`local_ts <= now`), по попыткам (0, 1, 2, 3+) — замер TK-048
/// против `ATTEMPT_ROWS` (зря развёрнутое); считается только под `ALPHA_DIAG`, на итог не влияет.
pub static ATTEMPT_USED: [std::sync::atomic::AtomicU64; 4] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 4];

/// Шаги `run_round` по виду (0 — пошаговый без заявок, 1 — пошаговый с открытыми заявками, 2 — прыжок удержания) — замер TK-049.
pub static STEP_KINDS: [std::sync::atomic::AtomicU64; 3] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 3];

/// Пропущено вызовов `on_event` (процесс; на итог счёта не влияет).
pub static SIG_SKIPS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Шаги удержания по подписи входов решения (замер TK-048, `ALPHA_DIAG`, на итог не влияет): для быстрого
/// пути (HoldTracker) и пути движка — [шагов, подпись та же, сменилась, нет подписи (`None`)]; в групповом круге
/// шаг считается по варианту.
pub static SIG_STATS: [std::sync::atomic::AtomicU64; 8] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 8];

/// Строки, прочитанные процессорами движка, по состоянию круга (до входа / живая заявка / позиция без заявок /
/// вне шага опроса) — замер TK-048; каждая строка читается двумя процессорами (local, exch), доли те же.
pub static ROW_CLASSES: [std::sync::atomic::AtomicU64; 4] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 4];

pub(super) fn note_used(attempt: u32, rest: &[Event], now: i64) {
    if fast_depth::BAND_STATS_ON.load(std::sync::atomic::Ordering::Relaxed) {
        for (a, n) in ROW_CLASSES.iter().zip(hftbacktest::prof::take_row_counts()) {
            a.fetch_add(n, std::sync::atomic::Ordering::Relaxed);
        }
        let used = rest.partition_point(|e| e.local_ts <= now);
        ATTEMPT_USED[(attempt as usize).min(3)]
            .fetch_add(used as u64, std::sync::atomic::Ordering::Relaxed);
    }
}

pub(super) fn note_attempt(attempt: u32, rows: usize) {
    let k = (attempt as usize).min(3);
    ATTEMPT_RUNS[k].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    ATTEMPT_ROWS[k].fetch_add(rows as u64, std::sync::atomic::Ordering::Relaxed);
}

/// Групповых кругов Э-08 — счётчик процесса для тестов; на итог не влияет.
pub static EXIT_GROUP_ROUNDS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Разбор нулевых групповых кругов (TK-048, печатается под `ALPHA_DIAG`): [сигналов-участников,
/// уже в памяти, частей с равной входной частью, частей из ≥ 2, из них круг не получился (`None`)].
pub static EXIT_GROUP_DIAG: [std::sync::atomic::AtomicU64; 5] = [
    std::sync::atomic::AtomicU64::new(0),
    std::sync::atomic::AtomicU64::new(0),
    std::sync::atomic::AtomicU64::new(0),
    std::sync::atomic::AtomicU64::new(0),
    std::sync::atomic::AtomicU64::new(0),
];

pub(super) fn diag_add(i: usize, n: u64) {
    if fast_depth::BAND_STATS_ON.load(std::sync::atomic::Ordering::Relaxed) {
        EXIT_GROUP_DIAG[i].fetch_add(n, std::sync::atomic::Ordering::Relaxed);
    }
}

/// Г-65: круги `wall2*`, посчитанные сольным путём (без журнала стен → молча B1); не ноль — прогон суток отказывает.
pub static WALL2_SOLO: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
