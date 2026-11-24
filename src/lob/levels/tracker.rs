//! `LevelTracker`: приём кадров и сделок, записи касаний и подходов.

use super::*;

/// Начальная ёмкость `live`/`births`/`carry`: тот же порядок, что у буферов
/// силы «×соседи» ниже (`strength_ok`/`strength_prefix`, одна сторона книги),
/// удвоенный — карта копит уровни обеих сторон разом. Не измеренный потолок:
/// превышение просто разово растит `Vec`, как у любого другого буфера
/// трекера (`newborns`/`sweep`/`touched`), сам факт роста поведение не меняет.
pub(super) const LEVEL_MAP_CAPACITY: usize = 128;

pub struct LevelTracker {
    pub(super) cfg: LevelsConfig,
    pub(super) live: SortedVec<(u8, i64), Live>,
    pub(super) births: SortedVec<(u8, i64), VecDeque<i64>>,
    /// Метка последней чистки `births` по cutoff (пункт «а», W4): без нового
    /// рождения на том же ключе очередь никогда не проходит через
    /// `count_prior_births`, и её устаревшие записи не тримятся сами —
    /// нужен отдельный периодический обход всей карты (`cleanup_births`).
    pub(super) births_cleaned_ms: Option<i64>,
    pub(super) newborns: Vec<(u8, i64, i64)>,
    pub(super) sweep: Vec<(u8, i64)>,
    pub(super) touched: Vec<Touched>,
    /// Уровни, взведшие подход в этом кадре (T-28): «завал» на взводе — после свипа.
    pub(super) armed: Vec<(u8, i64)>,
    /// TK-115: журнал стен включён (`enable_wall_log`); события копятся в `wall_events`.
    pub(super) wall_log: bool,
    pub(super) wall_events: Vec<WallEvent>,
    /// TK-274 (Г-03): seed двойников (пусто — режим выключен, лишней работы нет), имя инструмента для ключа
    /// выбора и накопленные записи двойников.
    pub(super) twin_seeds: Vec<u64>,
    pub(super) twin_symbol: String,
    pub(super) twins: Vec<TwinRec>,
    /// Буфер касаний для `observe_frame` без выхода касаний: те же события
    /// считаются, записи отбрасываются, ёмкость переиспользуется.
    #[cfg(test)]
    pub(super) touch_scratch: Vec<TouchRecord>,
    /// Буфер подходов для `observe_frame_with_touches` (F1): записи
    /// отбрасываются, ёмкость переиспользуется.
    #[cfg(test)]
    pub(super) approach_scratch: Vec<ApproachRecord>,
    /// Лучшая цена каждой стороны из последнего её кадра (`side_key`): сигналу
    /// подхода нужна лучшая цена **другой** стороны, а кадры приходят по одной
    /// стороне. `None` — сторона ещё не наблюдалась.
    pub(super) best_tick: [Option<i64>; 2],
    /// Рабочие буферы силы «×соседи» (`strength_flags`).
    pub(super) strength_ok: Vec<bool>,
    pub(super) strength_prefix: Vec<i64>,
    pub(super) start_ms: Option<i64>,
    pub(super) frame: u64,
    /// Оборот по минутам за скользящий час (лоты) — кольцо `FLOW_WINDOW_MIN`
    /// слотов; `flow_slot_min[i]` — номер минуты слота (−1 — пусто).
    pub(super) flow_ring: [i64; FLOW_WINDOW_MIN],
    pub(super) flow_slot_min: [i64; FLOW_WINDOW_MIN],
    /// Рождения живых уровней конца прошлых суток (`with_carried_births`): уровень, который
    /// стоит на той же цене в **первом** кадре своей стороны новых суток, наследует рождение.
    pub(super) carry: SortedVec<(u8, i64), i64>,
    /// Первый кадр стороны ещё не пришёл — перенос для неё открыт.
    pub(super) carry_open: [bool; 2],
    /// Состояние колонок R1 (TK-025): `None`, пока не вызван `enable_r1` — тогда ничего не ведётся.
    pub(super) r1: Option<Box<R1State>>,
}

/// Запись касания из состояния уровня в момент конца.
pub(super) fn touch_record(
    key: (u8, i64),
    lv: &Live,
    t: Touch,
    end_ms: i64,
    stack: (u32, Option<i64>),
    ended_by_death: bool,
) -> TouchRecord {
    TouchRecord {
        side: side_of(key),
        price_tick: key.1,
        touch_index: lv.touch_index,
        start_ms: t.start_ms,
        end_ms,
        duration_ms: end_ms - t.start_ms,
        level_birth_ms: lv.birth_ms,
        size_at_touch: t.size_at,
        size_max_before: t.size_max_before,
        traded_during: lv.traded.saturating_sub(t.traded_at_start),
        frontrun_lots: t.frontrun,
        frontrun_tick: t.frontrun_tick,
        swept_lots: t.swept,
        round_zeros: round_zeros(key.1),
        ended_by_death,
        stack_levels: stack.0,
        stack_next_tick: stack.1,
        traded_first_s: t.traded_first_s,
        flow_1h_lots: t.flow_1h_lots,
        strength_e2: t.strength_e2,
        strength_held_e2: t.strength_held_e2,
        repeat_count: t.repeat_count,
        depth_behind_lots: t.depth_behind_lots,
    }
}

/// Расстояние в bps по цене уровня: `gap × 10⁴ / tick` — тот же расчёт, что
/// `dist_bps` ёмкости (`fill_capacity`); цена неположительна — ноль,
/// арифметика тотальна.
pub(crate) fn gap_bps(gap: i64, tick: i64) -> i64 {
    if tick <= 0 {
        return 0;
    }
    let v = i128::from(gap) * BPS_PER_UNIT / i128::from(tick);
    i64::try_from(v).unwrap_or(i64::MAX)
}

/// Запись подхода из состояния уровня и снятого взвода.
pub(super) fn approach_record(
    key: (u8, i64),
    lv: &Live,
    a: Approach,
    disarm_ms: i64,
    touch_start_ms: Option<i64>,
    reason: ApproachEnd,
) -> ApproachRecord {
    ApproachRecord {
        side: side_of(key),
        price_tick: key.1,
        approach_index: lv.approach_index,
        arm_ms: a.arm_ms,
        arm_dist_bps: a.arm_dist_bps,
        level_birth_ms: lv.birth_ms,
        size_at_arm: a.size_at_arm,
        best_own_tick: a.best_own_tick,
        best_opp_tick: a.best_opp_tick,
        flow_1h_lots: a.flow_1h_lots,
        strength_e2: a.strength_e2,
        depth_behind_lots: a.depth_behind_lots,
        stack_levels_at_arm: a.stack_levels,
        stack_span_bps_at_arm: a.stack_span_bps,
        repeat_count_at_arm: lv.repeat,
        frontrun_lots_at_arm: a.frontrun_lots,
        p08: Some(a.p08),
        r1: None,
        touch_start_ms,
        disarm_ms,
        disarm_reason: reason,
    }
}

/// Правило взвода и снятия подхода (F1) по одному кадру уровня. `true` — подход
/// взведён в этом кадре («завал» на взводе ставится после свипа, T-28).
///
/// - **Взвод** — уровень жив, не лучший на своей стороне (цена ещё не дошла),
///   держит порог В-66 (`holds`, тот же, что у касания), возраст не меньше
///   `min_age_ms`, лучшая цена другой стороны впервые вошла в полосу `d_bps`
///   (`tick_diff × 10⁴ ≤ d × tick`, целочисленно) и после прошлого снятия
///   цена успела уйти за `2 × d`. Числа `d` и `min_age_ms` — конфигурация
///   замера, не константа кода.
/// - **Снятие**: касание (уровень стал лучшим — это первый такой кадр после
///   взвода), уход цены за `2 × d` (гистерезис: без него цена на границе
///   полосы взводила бы подход каждый кадр), смерть уровня — в свипе
///   (`ApproachEnd::LevelDeath`), здесь не видна.
/// - Записи уровня прогрева считаются (индекс растёт), но не эмитируются
///   (`emit = false`) — как у касаний.
/// - `arm_inputs` — `(holds, strength_e2)` кадра: порог В-66 и сила «×соседи».
///   Нужны только в момент взвода, поэтому зовутся только когда все прочие
///   условия взвода выполнены (Р1, T-17: раньше считались на каждом кадре
///   каждого живого уровня — 2/3 времени `lob touches`, замер
///   `docs/findings/backtest-optimization-2026-09-26.md`). Обе — чистые функции
///   кадра, условия взвода — конъюнкция без побочных эффектов, поэтому решение и
///   записи те же.
pub(super) fn observe_approach(
    lv: &mut Live,
    cfg: (i64, i64),
    s: u8,
    arm_inputs: impl FnOnce() -> (bool, [i64; STRENGTH_WINDOWS_BPS.len()]),
    f: ApproachFrame,
    emit: bool,
    out: &mut Vec<ApproachRecord>,
) -> bool {
    let (d_bps, min_age_ms) = cfg;
    let key = (s, f.tick);
    // Знаковое расстояние от уровня до чужой лучшей цены: у бида чужая цена
    // выше (положительно), у аска — ниже.
    let gap = if s == side_key(Side::Bid) {
        f.best_opp_tick - f.tick
    } else {
        f.tick - f.best_opp_tick
    };
    let scaled = i128::from(gap) * BPS_PER_UNIT;
    let near = scaled <= i128::from(d_bps) * i128::from(f.tick);
    let far = scaled > i128::from(d_bps.saturating_mul(2)) * i128::from(f.tick);
    if let Some(a) = lv.approach {
        // Касание (уровень стал лучшим) и уход цены — снятие; смерть уровня
        // снимает подход в свипе.
        if f.best || far {
            let (reason, touch_start_ms) = if f.best {
                (ApproachEnd::Touch, Some(f.ts_ms))
            } else {
                (ApproachEnd::PriceLeft, None)
            };
            let rec = approach_record(key, lv, a, f.ts_ms, touch_start_ms, reason);
            lv.approach = None;
            lv.approach_index = lv.approach_index.saturating_add(1);
            if emit {
                out.push(rec);
            }
            // Снова взводиться можно только после ухода цены за `2 × d`:
            // снятие касанием такого ухода не даёт.
            lv.approach_clear = far;
        }
        return false;
    }
    if far {
        lv.approach_clear = true;
    }
    if !near || !lv.approach_clear || f.best {
        return false;
    }
    if f.ts_ms.saturating_sub(lv.birth_ms) < min_age_ms {
        return false;
    }
    let (holds, strength_e2) = arm_inputs();
    if !holds {
        return false;
    }
    let (eat_60s_lots, size_max_60s_lots) = lv.eat.window(f.ts_ms.div_euclid(1_000));
    let p08 = ArmP08 {
        traded_lots: lv.traded,
        size_max: lv.max,
        size_monotonic: lv.first_decrease_ms.is_none_or(|t| t > lv.max_ms),
        eat_60s_lots,
        size_max_60s_lots,
        depth_behind50_lots: f.depth_behind50_lots,
    };
    lv.approach = Some(Approach {
        arm_ms: f.ts_ms,
        arm_dist_bps: gap_bps(gap, f.tick),
        size_at_arm: f.size_lots,
        best_own_tick: f.best_own_tick,
        best_opp_tick: f.best_opp_tick,
        flow_1h_lots: f.flow_1h_lots,
        strength_e2,
        depth_behind_lots: f.depth_behind_lots,
        frontrun_lots: f.frontrun_lots,
        stack_levels: 0,
        stack_span_bps: 0,
        p08,
    });
    true
}

pub(super) fn side_key(side: Side) -> u8 {
    match side {
        Side::Bid => 0,
        Side::Ask => 1,
    }
}

pub(super) fn side_of(key: (u8, i64)) -> Side {
    if key.0 == 0 {
        Side::Bid
    } else {
        Side::Ask
    }
}

/// Строго ниже 20%: `5 * size < max` без деления и без остатка.
pub(super) fn below_fraction(size: i64, max: i64) -> bool {
    size as i128 * 5 < max as i128
}

/// Сопоставимый размер для эвристики `repriced`: внутри удвоения в обе
/// стороны, строго целочисленно.
pub(super) fn comparable_size(a: i64, b: i64) -> bool {
    let (a, b) = (a as i128, b as i128);
    a <= 2 * b && b <= 2 * a
}

impl LevelTracker {
    /// Включает состояние и запись колонок R1 (TK-025, `--r1-cols`): без вызова
    /// `ApproachRecord::r1` всегда `None`, лишней работы нет. Звать до первого события.
    pub fn enable_r1(&mut self) {
        // Колонки пишутся только в запись подхода: без полосы подхода записей нет.
        if self.cfg.approach_bps.is_some() && self.r1.is_none() {
            self.r1 = Some(R1State::new());
        }
    }

    /// TK-115 (e65): включает журнал стен — переходы предиката «стена по критерию подхода B1»
    /// (не умирает, держит порог В-66 по размеру и силе кадра, возраст ≥ `approach_min_age_ms`).
    /// Без вызова журнал пуст и лишней работы нет. Звать до первого события.
    pub fn enable_wall_log(&mut self) {
        self.wall_log = true;
    }

    /// Забирает накопленные события журнала стен (в порядке времени), буфер трекера очищается.
    pub fn take_wall_events(&mut self, out: &mut Vec<WallEvent>) {
        out.append(&mut self.wall_events);
    }

    /// TK-274 (Г-03, П-15 §5): на каждом эмитируемом взводе строит двойника стены для каждого seed
    /// (`lob::twin::pick_twin`). Без вызова записей двойников нет и лишней работы нет. Звать до первого события.
    pub fn enable_twins(&mut self, symbol: &str, seeds: &[u64]) {
        self.twin_symbol = symbol.to_owned();
        self.twin_seeds = seeds.to_vec();
    }

    /// Забирает накопленные записи двойников (в порядке взводов), буфер трекера очищается.
    pub fn take_twins(&mut self, out: &mut Vec<TwinRec>) {
        out.append(&mut self.twins);
    }

    /// Создаёт трекер. Порог должен быть положителен, окно — тоже, прогрев
    /// неотрицателен: нулевой порог рождал бы уровень из пустого места.
    pub fn new(cfg: LevelsConfig) -> Self {
        cfg.mode.validate();
        assert!(cfg.warmup_ms >= 0, "прогрев не может быть отрицателен");
        assert!(
            cfg.repeat_window_ms > 0,
            "окно repeat_count обязано быть положительно"
        );
        assert!(
            cfg.approach_bps.is_none_or(|d| d > 0),
            "полоса подхода обязана быть положительна"
        );
        assert!(
            cfg.approach_min_age_ms >= 0,
            "возраст взвода не может быть отрицателен"
        );
        Self {
            carry: SortedVec::with_capacity(LEVEL_MAP_CAPACITY),
            carry_open: [false; 2],
            cfg,
            live: SortedVec::with_capacity(LEVEL_MAP_CAPACITY),
            births: SortedVec::with_capacity(LEVEL_MAP_CAPACITY),
            births_cleaned_ms: None,
            newborns: Vec::with_capacity(8),
            sweep: Vec::with_capacity(8),
            touched: Vec::with_capacity(8),
            armed: Vec::with_capacity(8),
            wall_log: false,
            wall_events: Vec::new(),
            twin_seeds: Vec::new(),
            twin_symbol: String::new(),
            twins: Vec::new(),
            #[cfg(test)]
            touch_scratch: Vec::with_capacity(8),
            #[cfg(test)]
            approach_scratch: Vec::with_capacity(8),
            best_tick: [None; 2],
            strength_ok: Vec::with_capacity(64),
            strength_prefix: Vec::with_capacity(65),
            start_ms: None,
            frame: 0,
            flow_ring: [0; FLOW_WINDOW_MIN],
            flow_slot_min: [-1; FLOW_WINDOW_MIN],
            r1: None,
        }
    }

    /// Оборот инструмента за последние `FLOW_WINDOW_MIN` минут к `now_ms`
    /// (включая текущую минуту), лоты. Слоты старше окна не считаются, даже
    /// если ещё не перезаписаны.
    pub(super) fn flow_1h_lots(&self, now_ms: i64) -> i64 {
        let now_min = now_ms.div_euclid(60_000);
        let oldest = now_min - (FLOW_WINDOW_MIN as i64 - 1);
        let mut sum: i64 = 0;
        for (i, &m) in self.flow_slot_min.iter().enumerate() {
            if m >= oldest && m <= now_min {
                sum = sum.saturating_add(self.flow_ring[i]);
            }
        }
        sum
    }

    #[cfg(test)]
    /// Сколько уровней живо прямо сейчас. Нужно тестам, чтобы убедиться, что
    /// фикстура никого не потеряла и не оставила висеть.
    pub fn live_count(&self) -> usize {
        self.live.len()
    }

    /// Снимок всех живых уровней в порядке ключа (сторона, цена) — «что стоит
    /// в стакане сейчас» для дашборда (таск 33). Уровни, родившиеся в прогреве,
    /// включены: они живые, просто при смерти не будут эмитированы.
    pub fn live_levels(&self, out: &mut Vec<LiveLevel>) {
        out.clear();
        for (key, lv) in self.live.iter() {
            out.push(LiveLevel {
                side: side_of(*key),
                price_tick: key.1,
                birth_ms: lv.birth_ms,
                size_lots: lv.seen_size,
                size_max: lv.max,
                repeat_count: lv.repeat,
                traded_lots: lv.traded,
                rpi_lots: lv.rpi,
            });
        }
    }

    /// Один кадр одной стороны без выхода касаний: те же события, что у
    /// `observe_frame_with_touches` (состояние касаний ведётся, индексы
    /// растут), записи касаний отбрасываются. Для читателей, которым нужны
    /// только смерти (`watch`, `profiles`). Умершие за кадр дописываются в
    /// `out` в порядке возрастания цены; ёмкость `out` — забота вызывающего,
    /// трекер её не растит сам и в горячем пути не аллоцирует.
    /// Трекер новых суток, который **переносит возраст** живых уровней конца прошлых суток
    /// (аудит дизайна 22.09 Т3): без переноса трекер стартует в 00:00 UTC, стена, пережившая
    /// полночь, выглядит молодой, а возраст ≥ 45 мин до 00:45 недостижим. Уровень наследует
    /// рождение, только если стоит на той же цене в первом кадре своей стороны (снимок начала
    /// файла суток) — непрерывность наблюдения. Режимы с прогревом (`percentile`) перенос не
    /// принимают: там прогрев — про порог, а не про рождение.
    pub fn with_carried_births(cfg: LevelsConfig, births: Vec<((u8, i64), i64)>) -> Self {
        let mut t = Self::new(cfg);
        if t.effective_warmup_ms() == 0 && !births.is_empty() {
            t.carry = SortedVec::from_sorted(births);
            t.carry_open = [true, true];
        }
        t
    }

    /// Рождения живых уровней (ключ `(сторона, тик)` → `birth_ms`), отсортированные
    /// по ключу, — вход переноса (`with_carried_births`).
    pub fn live_births(&self) -> Vec<((u8, i64), i64)> {
        self.live.iter().map(|(k, lv)| (*k, lv.birth_ms)).collect()
    }

    #[cfg(test)]
    pub fn observe_frame(
        &mut self,
        ts_ms: i64,
        side: Side,
        levels: &[LevelObs],
        out: &mut Vec<LevelRecord>,
    ) {
        // `take` кладёт на место пустой вектор без выделения; ёмкость буфера
        // возвращается назад тем же ходом — куча после прогрева не трогается.
        let mut scratch = std::mem::take(&mut self.touch_scratch);
        scratch.clear();
        self.observe_frame_with_touches(ts_ms, side, levels, out, &mut scratch);
        self.touch_scratch = scratch;
    }

    #[cfg(test)]
    /// Один кадр одной стороны. Умершие за кадр дописываются в `out`
    /// в порядке возрастания цены, кончившиеся касания — в `touches`
    /// (порядок — документация модуля, «Касания»); ёмкость обоих — забота
    /// вызывающего, трекер её не растит сам и в горячем пути не аллоцирует.
    /// Кадр идёт от лучшей цены вглубь (`Book::levels`): наблюдение с
    /// индексом 0 — лучшая цена стороны. Записи подхода считаются
    /// (`cfg.approach_bps`), но отбрасываются: читатель подходов —
    /// `observe_frame_with_approaches`.
    pub fn observe_frame_with_touches(
        &mut self,
        ts_ms: i64,
        side: Side,
        levels: &[LevelObs],
        out: &mut Vec<LevelRecord>,
        touches: &mut Vec<TouchRecord>,
    ) {
        let mut scratch = std::mem::take(&mut self.approach_scratch);
        scratch.clear();
        self.observe_frame_with_approaches(ts_ms, side, levels, out, touches, &mut scratch);
        self.approach_scratch = scratch;
    }

    /// То же плюс записи подхода (F1) в `approaches`. Порядок выдачи
    /// детерминирован: снятия по касанию и уходу цены — в порядке кадра,
    /// затем снятия смертью уровня — в порядке `(сторона, тик)` вместе с его
    /// смертью (перед записью оборванного смертью касания).
    pub fn observe_frame_with_approaches(
        &mut self,
        ts_ms: i64,
        side: Side,
        levels: &[LevelObs],
        out: &mut Vec<LevelRecord>,
        touches: &mut Vec<TouchRecord>,
        approaches: &mut Vec<ApproachRecord>,
    ) {
        self.cleanup_births(ts_ms);
        let ctx = self.begin_frame(ts_ms, side, levels);
        let carry_open_s = self.carry_open[ctx.s as usize];
        let first_new = approaches.len();
        // Фазы кадра (W4в) — свободные функции над непересекающимися полями,
        // а не один вложенный метод: каждая берёт только то состояние, что
        // реально трогает, вызовы идут строго по очереди, поэтому ни одна
        // пара заимствований не пересекается во времени.
        if self.r1.is_some() {
            scan_levels::<true>(
                &ctx,
                ts_ms,
                levels,
                &self.strength_ok,
                &self.strength_prefix,
                &mut self.live,
                &mut self.births,
                &mut self.carry,
                carry_open_s,
                &mut self.newborns,
                &mut self.touched,
                &mut self.armed,
                approaches,
                &mut self.wall_events,
                (&self.twin_seeds, &self.twin_symbol, &mut self.twins),
                self.r1.as_deref_mut(),
            );
        } else {
            scan_levels::<false>(
                &ctx,
                ts_ms,
                levels,
                &self.strength_ok,
                &self.strength_prefix,
                &mut self.live,
                &mut self.births,
                &mut self.carry,
                carry_open_s,
                &mut self.newborns,
                &mut self.touched,
                &mut self.armed,
                approaches,
                &mut self.wall_events,
                (&self.twin_seeds, &self.twin_symbol, &mut self.twins),
                self.r1.as_deref_mut(),
            );
        }
        if let Some(r1) = self.r1.as_deref_mut() {
            r1.finish_frame(ctx.s, &self.live);
        }
        close_carry_after_first_frame(&mut self.carry, &mut self.carry_open, ctx.s);
        detect_sweep(&self.live, &mut self.sweep, ctx.s, ctx.frame);
        compute_touch_stacks(&self.live, &mut self.touched, ctx.s, ctx.frame, ctx.mode);
        compute_arm_stacks(&mut self.live, &self.armed, ctx.s, ctx.frame, ctx.mode);
        resolve_deaths(
            &mut self.wall_events,
            &mut self.live,
            &mut self.sweep,
            &self.newborns,
            &self.touched,
            ts_ms,
            ctx.frame,
            ctx.warm_end,
            out,
            touches,
            approaches,
            self.r1.as_deref_mut(),
        );
        if let Some(r1) = self.r1.as_deref_mut() {
            r1.attach(&mut approaches[first_new..]);
        }
        finalize_surviving_touches(
            &mut self.live,
            &mut self.touched,
            ts_ms,
            ctx.frame,
            ctx.warm_end,
            touches,
        );
    }

    /// Разбор кадра до прохода по наблюдениям (W4в, фаза 1): всё, что не
    /// зависит от конкретного наблюдения, — оборот за час, счётчик кадра,
    /// окно прогрева, флаги силы «×соседи», лучшие цены сторон.
    pub(super) fn begin_frame(&mut self, ts_ms: i64, side: Side, levels: &[LevelObs]) -> FrameCtx {
        // Оборот за час к этому кадру — один раз на кадр, для касаний, начавшихся в нём.
        let flow_1h = self.flow_1h_lots(ts_ms);
        if self.start_ms.is_none() {
            self.start_ms = Some(ts_ms);
        }
        self.frame += 1;
        let frame = self.frame;
        if let Some(r1) = self.r1.as_deref_mut() {
            r1.on_frame(ts_ms, side_key(side), levels);
        }
        let mode = self.cfg.mode;
        let window = self.cfg.repeat_window_ms;
        // Граница прогрева: рождения строго раньше не эмитируются — ни
        // смертью, ни касанием, ни подходом (индексы при этом растут).
        let warm_end = self
            .start_ms
            .unwrap_or(ts_ms)
            .saturating_add(self.effective_warmup_ms());
        strength_flags(
            mode,
            levels,
            &mut self.strength_ok,
            &mut self.strength_prefix,
        );
        let s = side_key(side);
        // Лучшая цена этой стороны в этом кадре — и последняя известная лучшая
        // цена другой стороны: полосе подхода нужна именно чужая цена (F1).
        let best_own = levels.first().map(|o| o.tick);
        let best_opp = self.best_tick[1 - s as usize];
        self.best_tick[s as usize] = best_own;
        let approach_d = self.cfg.approach_bps;
        let approach_min_age_ms = self.cfg.approach_min_age_ms;
        self.newborns.clear();
        self.touched.clear();
        self.armed.clear();
        FrameCtx {
            s,
            frame,
            mode,
            window,
            warm_end,
            best_own,
            best_opp,
            approach_d,
            approach_min_age_ms,
            flow_1h,
            wall_log: self.wall_log,
        }
    }

    /// Периодическая чистка `births` по cutoff (W4а): без нового рождения на
    /// том же ключе очередь никогда больше не проходит через
    /// `count_prior_births`, и её устаревшие записи не тримятся сами — карта
    /// иначе растёт вечно на ценах, где рождение было один раз. Не чаще раза
    /// в `repeat_window_ms`: после целого окна прошлые записи гарантированно
    /// устарели у любого ключа, чаще обходить всю карту незачем. `retain`
    /// только сдвигает и режет — не аллоцирует и не растит ёмкость.
    pub(super) fn cleanup_births(&mut self, ts_ms: i64) {
        let window = self.cfg.repeat_window_ms;
        let due = self
            .births_cleaned_ms
            .is_none_or(|last| ts_ms.saturating_sub(last) >= window);
        if !due {
            return;
        }
        self.births_cleaned_ms = Some(ts_ms);
        let cutoff = ts_ms - window;
        self.births.retain(|_, q| {
            while q.front().is_some_and(|&t| t <= cutoff) {
                q.pop_front();
            }
            !q.is_empty()
        });
    }

    /// Один трейд ленты. Находит живой уровень той стороны, которую трейд ест
    /// на этом тике, и добавляет объём — иначе молча пропускает. Блочные,
    /// нулевые и поданные раньше рождения не считаются. RPI-сделки идут в
    /// свой счётчик: они исполнились об невидимую заявку, а не об этот уровень
    /// (В-55), поэтому метку исхода не сдвигают. Поиск в карте кучу не
    /// трогает, внутри — одно целое сложение с насыщением.
    pub fn observe_trade(&mut self, tr: TradeHit) {
        if tr.block || tr.lots <= 0 {
            return;
        }
        // Оборот инструмента (сила «×поток»): все неблочные сделки, включая
        // RPI, — это то, что рынок реально проторговал.
        {
            let minute = tr.exch_ms.div_euclid(60_000);
            let slot = minute.rem_euclid(FLOW_WINDOW_MIN as i64) as usize;
            if self.flow_slot_min[slot] != minute {
                self.flow_slot_min[slot] = minute;
                self.flow_ring[slot] = 0;
            }
            self.flow_ring[slot] = self.flow_ring[slot].saturating_add(tr.lots);
        }
        if let Some(r1) = self.r1.as_deref_mut() {
            r1.on_trade(&tr);
        }
        let key = (u8::from(tr.aggressor_is_buy), tr.tick);
        if let Some(lv) = self.live.get_mut(&key) {
            if tr.exch_ms < lv.birth_ms {
                return;
            }
            if tr.rpi {
                lv.rpi = lv.rpi.saturating_add(tr.lots);
            } else {
                lv.traded = lv.traded.saturating_add(tr.lots);
                // Г-55 (TK-012): секундное кольцо — только при подходе.
                if self.cfg.approach_bps.is_some() {
                    let visible = lv.prev;
                    lv.eat
                        .add_trade(tr.exch_ms.div_euclid(1_000), tr.lots, visible);
                }
                // Окно реакции (E4): объём в первые секунды идущего касания —
                // по метке исполнения относительно старта касания.
                if let Some(t) = &mut lv.touch {
                    let since_ms = tr.exch_ms.saturating_sub(t.start_ms);
                    for (k, w) in REACTION_WINDOWS_S.iter().enumerate() {
                        if since_ms >= 0 && since_ms < w.saturating_mul(1_000) {
                            t.traded_first_s[k] = t.traded_first_s[k].saturating_add(tr.lots);
                        }
                    }
                }
            }
        }
    }

    /// Прогрев, который реально действует на эмиссию: `floor` — всегда ноль
    /// (режим отладки, без прогрева по определению), `percentile` — как
    /// сконфигурировано. `cfg.warmup_ms` в режиме `floor` не читается.
    pub(super) fn effective_warmup_ms(&self) -> i64 {
        match self.cfg.mode {
            H3Mode::Floor { .. }
            | H3Mode::Notional { .. }
            | H3Mode::Strength { .. }
            | H3Mode::Both { .. } => 0,
            H3Mode::Percentile { .. } => self.cfg.warmup_ms,
        }
    }
}
