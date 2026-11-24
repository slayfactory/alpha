//! Настройка трекера уровней и исходы: режим порога H3, `LevelsConfig`, наблюдения, смерть и исход уровня.

use super::*;

/// Как задан порог `H3` — план D-H3 (таск 02): два режима, какой войдёт в
/// предрегистрацию, решает двухчасовой пилот, не этот код. Явный выбор без
/// умолчания — вызывающий (`commands/lob`) обязан подставить один из двух.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum H3Mode {
    /// Пол в лотах — из `instruments.csv` (пишет отдельный шаг сборки пула),
    /// **без прогрева**: уровень эмитится с первого рождения. Рабочий режим
    /// отладки — держит `levels > 0` на прогонах короче часа.
    Floor { h3_lots: i64 },
    /// Порог — заранее измеренный 99-й перцентиль `size_max` по
    /// времени-взвешенной выборке за скользящий час (измерение — вне этого
    /// модуля, число приходит параметром, как раньше). Прогрев `warmup_ms`
    /// действует как в прежнем определении: рождения раньше него
    /// отслеживаются, но не эмитируются.
    Percentile { h3_lots: i64 },
    /// Абсолютный пол «от N» в деньгах (владелец 2026-09-18, В-61): уровень —
    /// плотность, если его номинал `цена × размер ≥ min_usd`. В лотах порог
    /// зависит от цены: `ceil(min_usd_e9 × 1e9 / (tick × tick_e9 × step_e9))`.
    /// Без прогрева, как `Floor`.
    Notional {
        min_usd_e9: i64,
        tick_e9: i64,
        step_e9: i64,
    },
    /// Относительная сила «×соседи» (roadmap 2026-09-17 §3, знаменатель B;
    /// владелец 2026-09-18, В-61): уровень — плотность, если
    /// `100 × size / mean(size соседей той же стороны в ±window_bps)` не
    /// меньше `pct`. Соседи — остальные уровни кадра в окне; без соседей
    /// сила не определена и уровень не рождается. Числа в сотых: `pct_e2`
    /// — проценты × 100, `window_bps_e2` — bps × 100.
    Strength { pct_e2: i64, window_bps_e2: i64 },
    /// И то и другое (владелец: «сила + от денежного объёма»).
    Both {
        min_usd_e9: i64,
        tick_e9: i64,
        step_e9: i64,
        pct_e2: i64,
        window_bps_e2: i64,
    },
}

impl H3Mode {
    /// Порог рождения в лотах — общий для обоих режимов кусок конфигурации.
    /// Единый порог в лотах — только у режимов с одним числом (`Floor`,
    /// `Percentile`); у `Notional`/`Strength`/`Both` его нет (оси «×H3» в
    /// командах уровней и касаний для них не определены).
    pub fn single_h3_lots(self) -> Option<i64> {
        match self {
            H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => Some(h3_lots),
            H3Mode::Notional { .. } | H3Mode::Strength { .. } | H3Mode::Both { .. } => None,
        }
    }

    /// Порог рождения в лотах на цене `tick`: у денежного пола — с округлением
    /// вверх, чтобы `size ≥ порог` означало ровно `номинал ≥ N`. Горячий путь
    /// зовёт не её, а `notional_passes` (то же сравнение без деления); здесь —
    /// определение, по которому та проверяется тестом.
    #[cfg(test)]
    pub(super) fn notional_lots_at(min_usd_e9: i64, tick_e9: i64, step_e9: i64, tick: i64) -> i64 {
        if tick <= 0 {
            return i64::MAX;
        }
        let num = (min_usd_e9 as i128) * 1_000_000_000i128;
        let den = (tick as i128) * (tick_e9 as i128) * (step_e9 as i128);
        let lots = (num + den - 1) / den;
        lots.clamp(1, i64::MAX as i128) as i64
    }

    /// `size_lots ≥ notional_lots_at(…)` без деления i128 (Р9, T-17: деление шло
    /// на каждом неживом уровне каждого кадра). Для `den > 0`:
    /// `size ≥ ⌈num/den⌉ ⇔ size·den ≥ num`; края `clamp(1, i64::MAX)` — явно:
    /// порог не ниже 1 (`size ≥ 1`) и не выше `i64::MAX` (размер `i64::MAX`
    /// проходит всегда). Эквивалентность — тест `notional_passes_matches_lots_at`.
    pub(super) fn notional_passes(
        min_usd_e9: i64,
        tick_e9: i64,
        step_e9: i64,
        tick: i64,
        size_lots: i64,
    ) -> bool {
        if tick <= 0 {
            return size_lots == i64::MAX;
        }
        let num = (min_usd_e9 as i128) * 1_000_000_000i128;
        let den = (tick as i128) * (tick_e9 as i128) * (step_e9 as i128);
        debug_assert!(den > 0, "шаг цены и лота обязаны быть положительны");
        // Переполнение `size·den` (оба > 0) — произведение больше любого `num`.
        size_lots >= 1
            && ((size_lots as i128)
                .checked_mul(den)
                .is_none_or(|p| p >= num)
                || size_lots == i64::MAX)
    }

    /// Проходит ли наблюдение абсолютный порог **при рождении**: у пола в
    /// лотах — строго выше (как было), у денежного — не ниже номинала; у
    /// чистой силы абсолютного порога нет (размер положителен).
    pub(super) fn passes_birth(self, tick: i64, size_lots: i64) -> bool {
        match self {
            H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => size_lots > h3_lots,
            H3Mode::Notional {
                min_usd_e9,
                tick_e9,
                step_e9,
            }
            | H3Mode::Both {
                min_usd_e9,
                tick_e9,
                step_e9,
                ..
            } => Self::notional_passes(min_usd_e9, tick_e9, step_e9, tick, size_lots),
            H3Mode::Strength { .. } => size_lots > 0,
        }
    }

    /// То же для «завала» касания (`stack`): у пола в лотах — не ниже (как
    /// было), у денежного — не ниже номинала.
    pub(super) fn passes_stack(self, tick: i64, size_lots: i64) -> bool {
        match self {
            H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => size_lots >= h3_lots,
            other => other.passes_birth(tick, size_lots),
        }
    }

    /// Порог силы `(pct_e2, window_bps_e2)`, если режим его требует.
    pub(super) fn strength_gate(self) -> Option<(i64, i64)> {
        match self {
            H3Mode::Strength {
                pct_e2,
                window_bps_e2,
            }
            | H3Mode::Both {
                pct_e2,
                window_bps_e2,
                ..
            } => Some((pct_e2, window_bps_e2)),
            H3Mode::Floor { .. } | H3Mode::Percentile { .. } | H3Mode::Notional { .. } => None,
        }
    }

    /// Держится ли порог **в момент касания** (В-66, база E1: «крупная при
    /// подходе цены»): уровень рождается сильным, но к касанию соседи могли
    /// вырасти, а его самого — частично съесть. Номинал — по `size_at_touch`,
    /// сила — по `strength_e2` окна, равного окну порога. `None` — окно
    /// порога не из `STRENGTH_WINDOWS_BPS`, проверить нечем (вызывающий
    /// отказывает, не пропускает молча).
    pub fn holds_at_touch(self, touch: &TouchRecord) -> Option<bool> {
        self.holds_at_size(touch.price_tick, touch.size_at_touch, &touch.strength_e2)
    }

    /// То же правило порога В-66, но по кадру, а не по записи касания: размер
    /// на этом кадре и сила «×соседи» того же кадра — общий порог касания и
    /// сигнала подхода (F1). `None` — окно силы не из `STRENGTH_WINDOWS_BPS`,
    /// проверить порог нечем.
    pub fn holds_at_size(
        self,
        tick: i64,
        size_lots: i64,
        strength_e2: &[i64; STRENGTH_WINDOWS_BPS.len()],
    ) -> Option<bool> {
        if !self.passes_birth(tick, size_lots) {
            return Some(false);
        }
        match self.strength_gate() {
            None => Some(true),
            Some((pct_e2, window_bps_e2)) => {
                let k = STRENGTH_WINDOWS_BPS
                    .iter()
                    .position(|&w| w.saturating_mul(100) == window_bps_e2)?;
                Some(strength_e2[k] >= pct_e2)
            }
        }
    }

    /// Порог обязан быть положителен: нулевой рождал бы уровень из пустого места.
    pub(super) fn validate(self) {
        match self {
            H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => {
                assert!(h3_lots > 0, "порог H3 обязан быть положителен")
            }
            H3Mode::Notional {
                min_usd_e9,
                tick_e9,
                step_e9,
            } => assert!(
                min_usd_e9 > 0 && tick_e9 > 0 && step_e9 > 0,
                "денежный порог, тик и шаг лота обязаны быть положительны"
            ),
            H3Mode::Strength {
                pct_e2,
                window_bps_e2,
            } => assert!(
                pct_e2 > 0 && window_bps_e2 > 0,
                "сила и окно соседей обязаны быть положительны"
            ),
            H3Mode::Both {
                min_usd_e9,
                tick_e9,
                step_e9,
                pct_e2,
                window_bps_e2,
            } => assert!(
                min_usd_e9 > 0 && tick_e9 > 0 && step_e9 > 0 && pct_e2 > 0 && window_bps_e2 > 0,
                "денежный порог, тик, шаг, сила и окно обязаны быть положительны"
            ),
        }
    }
}

/// Конфигурация трекера. Порог `H3` — режим (см. `H3Mode`), окно часа —
/// параметр, а не хардкод: предрегистрированные значения подставляет
/// вызывающий шагом позже.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelsConfig {
    /// Режим порога `H3`: `floor` или `percentile`, без умолчания.
    pub mode: H3Mode,
    /// Прогрев в миллисекундах: в режиме `percentile` рождения раньше него
    /// не попадают в выборку. В режиме `floor` не читается — там прогрева
    /// нет по определению режима.
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в миллисекундах — общее для обоих
    /// режимов, план §3: «скользящий час».
    pub repeat_window_ms: i64,
    /// Полоса **сигнала подхода** в bps (F1 этапа F): взвод записи
    /// `ApproachRecord`, когда лучшая цена другой стороны впервые подошла к
    /// живому уровню на `approach_bps`. `None` — записей подхода нет: ни
    /// состояния, ни вычислений, байты касаний прежние (гейт F1).
    pub approach_bps: Option<i64>,
    /// Минимальный возраст уровня к моменту взвода, мс (флор В-71: 15 мин —
    /// как `--min-age-secs` сетки). Только для сигнала подхода.
    pub approach_min_age_ms: i64,
}

/// Одно наблюдение уровня в кадре одной стороны.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelObs {
    /// Цена в тиках.
    pub tick: i64,
    /// Размер в лотах.
    pub size_lots: i64,
    /// Входит ли цена в топ-50 этой стороны в этом кадре.
    pub in_top50: bool,
}

/// Каким правилом уровень умер. Нужно шагу 1.1, чтобы различать два правила
/// смерти из плана, а не смешивать их в одну кучу.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeathKind {
    /// Размер упал строго ниже 20% максимума (включая исчезновение из кадра).
    BelowFraction,
    /// Цена ушла за топ-50 при живом размере.
    LeftTop,
}

/// Исход уровня по правилу 70/20 из плана (§1.2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    /// Объём против уровня ≥ 70% максимума: ликвидность съели.
    Eaten,
    /// Объём против уровня ≤ 20% максимума: уровень сняли.
    Pulled,
    /// Между 20% и 70%: смешанный исход.
    Mixed,
}

/// Правило 70/20 строго целочисленно, без деления: `10 * traded >= 7 * max`
/// есть eaten, `5 * traded <= max` есть pulled, иначе mixed. Границы
/// включительные: ровно 70% — eaten, ровно 20% — pulled.
pub fn classify_outcome(traded_lots: i64, size_max: i64) -> Outcome {
    let t = traded_lots as i128;
    let m = size_max as i128;
    if t * 10 >= m * 7 {
        Outcome::Eaten
    } else if t * 5 <= m {
        Outcome::Pulled
    } else {
        Outcome::Mixed
    }
}

/// Один трейд ленты, уже переведённый вызывающим в тики и лоты.
/// Сторона — это сторона агрессора: она выбирает, какой уровень трейд ест.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradeHit {
    /// Цена сделки в тиках: обязана совпасть с тиком уровня точь-в-точь.
    pub tick: i64,
    /// Размер сделки в лотах.
    pub lots: i64,
    /// `true` — агрессор-покупатель (ест аск), `false` — продавец (ест бид).
    pub aggressor_is_buy: bool,
    /// Блочная сделка: видимую ликвидность не потребляет, в объём не идёт.
    pub block: bool,
    /// Сделка об RPI-заявку: исполнена об **невидимую** ликвидность
    /// маркет-мейкера, поэтому видимый уровень не потребляет — как и блочная,
    /// но в отличие от неё видна в ленте с 2026-09-15 (бит 1 `attrs`, В-53).
    /// Идёт в отдельный счётчик, а не в `traded_lots`.
    pub rpi: bool,
    /// Время исполнения на матчинге. Сравнимо с меткой кадра; подачи
    /// раньше рождения уровня не считаются.
    pub exch_ms: i64,
}

/// Запись умершего уровня: ключи, шесть признаков истории и место под 1.2.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelRecord {
    /// Сторона книги (нужна шагу 2.1 для полярности по стороне).
    pub side: Side,
    /// Цена в тиках.
    pub price_tick: i64,
    /// Кадр рождения, мс.
    pub birth_ms: i64,
    /// Кадр смерти, мс.
    pub death_ms: i64,
    /// Признак 1: время жизни, мс.
    pub lifetime_ms: i64,
    /// Признак 2: максимум размера в лотах (читает шаг 1.2 для правила 70/20).
    pub size_max: i64,
    /// Признак 3: время до первого достижения максимума, мс.
    pub time_to_max_ms: i64,
    /// Признак 4: размер рос монотонно вплоть до максимума.
    pub size_monotonic: bool,
    /// Признак 5: сколько уровней уже рождалось на этой цене за скользящий час
    /// до рождения этого.
    pub repeat_count: u32,
    /// Признак 6: эвристика переставления (печатается, в гейты не входит).
    pub repriced: bool,
    /// Какое из двух правил смерти сработало.
    pub death: DeathKind,
    /// Объём трейдов против уровня в лотах за его жизнь (шаг 1.2). Только
    /// сделки по **видимой** книге: блочные и RPI-сделки сюда не входят.
    pub traded_lots: i64,
    /// Объём RPI-сделок на этой цене за жизнь уровня, лоты. В метку исхода не
    /// входит (В-55): считается отдельно, чтобы было видно, сколько на уровне
    /// прошло мимо видимой книги.
    pub rpi_lots: i64,
}

impl LevelRecord {
    /// Исход по правилу 70/20 от накопленного объёма против максимума.
    pub fn outcome(&self) -> Outcome {
        classify_outcome(self.traded_lots, self.size_max)
    }
}

/// TK-115 (журнал стен e65): уровень стал стеной по критерию подхода B1 (`wall`) или перестал ею быть
/// (вырос/съеден ниже порога, умирает, ушёл из топ-50 или умер).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WallEvent {
    pub ts_ms: i64,
    pub side: Side,
    pub price_tick: i64,
    pub wall: bool,
}

/// TK-115 (e65): цена (в тиках) **второй** стены противоположной стороны за входом на момент `fill_ms` —
/// по журналу стен (`WallEvent`, порядок времени): живые на `fill_ms` стены стороны `wall_side` дальше входа
/// (лонг: аск-стены выше `entry_tick`; шорт: бид-стены ниже), по удалению от входа — вторая. Меньше двух
/// живых — `None` (сделка = B1). `live` — переиспользуемый буфер вызывающего.
pub fn second_wall_tick(
    events: &[WallEvent],
    wall_side: Side,
    fill_ms: i64,
    entry_tick: i64,
    long: bool,
    live: &mut Vec<i64>,
) -> Option<i64> {
    live.clear();
    for e in events.iter().take_while(|e| e.ts_ms <= fill_ms) {
        if e.side != wall_side {
            continue;
        }
        let at = live.iter().position(|&t| t == e.price_tick);
        match (e.wall, at) {
            (true, None) => live.push(e.price_tick),
            (false, Some(i)) => {
                live.swap_remove(i);
            }
            _ => {}
        }
    }
    live.retain(|&t| if long { t > entry_tick } else { t < entry_tick });
    live.sort_unstable();
    if long {
        live.get(1).copied()
    } else {
        live.len().checked_sub(2).map(|i| live[i])
    }
}

/// Снимок живого (ещё не умершего) уровня — то, что «стоит в стакане
/// сейчас» на момент последнего кадра (таск 33, дашборд по плотностям).
/// Только чтение состояния трекера; в горячий путь не входит.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LiveLevel {
    pub side: Side,
    pub price_tick: i64,
    pub birth_ms: i64,
    /// Размер в лотах на последнем кадре, где уровень виден.
    pub size_lots: i64,
    /// Максимум размера за жизнь (тот же `size_max`, что у записи).
    pub size_max: i64,
    /// Сколько уровней уже рождалось на этой цене за скользящее окно до
    /// рождения этого.
    pub repeat_count: u32,
    /// Объём сделок против уровня в лотах на этот момент (без блочных и RPI).
    pub traded_lots: i64,
    /// Объём RPI-сделок на этой цене на этот момент, лоты.
    pub rpi_lots: i64,
}
