//! Живое состояние уровня: касание, подход, `Live`, отсортированный вектор вместо дерева-словаря.

use super::*;

/// Состояние идущего касания — целые в `Live`, кучи нет.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct Touch {
    pub(super) start_ms: i64,
    /// Номер кадра начала: `stack` заполняется после свипа того же кадра, и
    /// сравнение по номеру кадра, а не по метке (несколько кадров могут
    /// делить миллисекунду), говорит, что заполнять пора.
    pub(super) start_frame: u64,
    pub(super) size_at: i64,
    pub(super) size_max_before: i64,
    pub(super) frontrun: i64,
    /// Цена того же наблюдения фронтрана (B2): из неё строится вход «от
    /// первого фронтранера»; `None` — фронтрана не было вовсе.
    pub(super) frontrun_tick: Option<i64>,
    pub(super) swept: i64,
    /// Окно «завала» в тиках — `stack_window_ticks` цены уровня, один раз
    /// на старте.
    pub(super) window_ticks: i64,
    pub(super) traded_at_start: i64,
    pub(super) stack: u32,
    /// Вторая плотность завала (см. `TouchRecord::stack_next_tick`).
    pub(super) stack_next_tick: Option<i64>,
    /// Объём против уровня за первые окна `REACTION_WINDOWS_S` касания —
    /// копится в `observe_trade` по метке исполнения.
    pub(super) traded_first_s: [i64; REACTION_WINDOWS_S.len()],
    /// Оборот инструмента за час к старту (см. `TouchRecord::flow_1h_lots`).
    pub(super) flow_1h_lots: i64,
    /// Уровень в этом кадре перестал быть лучшей ценой: конец касания ждёт
    /// свипа — смерть в том же кадре имеет приоритет.
    pub(super) end_pending: bool,
    /// Сила «×соседи» на кадре старта (см. `TouchRecord::strength_e2`).
    pub(super) strength_e2: [i64; STRENGTH_WINDOWS_BPS.len()],
    /// Минимум силы за окна `STRENGTH_HELD_WINDOWS_S` (см. `TouchRecord`).
    pub(super) strength_held_e2: [i64; STRENGTH_HELD_WINDOWS_S.len()],
    /// Прошлых рождений уровня на этой цене в окне `repeat_window_ms`.
    pub(super) repeat_count: u32,
    /// Направленная глубина на кадре старта (см. `TouchRecord::depth_behind_lots`).
    pub(super) depth_behind_lots: i64,
}

/// Взведённый подход уровня — целые в `Live`, кучи нет (F1). Поля — состояние
/// кадра взвода; из них собирается `ApproachRecord` на снятии.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct Approach {
    pub(super) arm_ms: i64,
    pub(super) arm_dist_bps: i64,
    pub(super) size_at_arm: i64,
    pub(super) best_own_tick: i64,
    pub(super) best_opp_tick: i64,
    pub(super) flow_1h_lots: i64,
    pub(super) strength_e2: [i64; STRENGTH_WINDOWS_BPS.len()],
    /// Направленная глубина на кадре взвода (см. `TouchRecord::depth_behind_lots`,
    /// T2 П-02).
    pub(super) depth_behind_lots: i64,
    /// Лоты впереди уровня к взводу (`ApproachRecord::frontrun_lots_at_arm`).
    pub(super) frontrun_lots: i64,
    /// «Завал» на кадре взвода — ставится после свипа кадра (`compute_arm_stacks`).
    pub(super) stack_levels: u32,
    /// Расстояние завала на кадре взвода, bps (`ApproachRecord::stack_span_bps_at_arm`).
    pub(super) stack_span_bps: i64,
    /// Признаки П-08 на кадре взвода (`ApproachRecord::p08`).
    pub(super) p08: ArmP08,
}

/// Один кадр уровня для правила взвода/снятия подхода (F1): всё, что нужно
/// `observe_approach`, — чтобы правило читалось отдельно от горячего цикла.
#[derive(Debug, Clone, Copy)]
pub(super) struct ApproachFrame {
    pub(super) ts_ms: i64,
    pub(super) tick: i64,
    pub(super) size_lots: i64,
    /// Уровень — лучшая цена своей стороны в этом кадре (касание идёт либо
    /// началось).
    pub(super) best: bool,
    /// Лучшая цена своей стороны в этом кадре.
    pub(super) best_own_tick: i64,
    /// Лучшая цена другой стороны на её последнем кадре.
    pub(super) best_opp_tick: i64,
    pub(super) flow_1h_lots: i64,
    pub(super) depth_behind_lots: i64,
    /// `ArmP08::depth_behind50_lots` этого наблюдения.
    pub(super) depth_behind50_lots: i64,
    /// Лоты впереди уровня за секунду до кадра (`Live::frontrun_before`).
    pub(super) frontrun_lots: i64,
}

/// Живой уровень: всё состояние — несколько целых, кучи нет.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct Live {
    pub(super) birth_ms: i64,
    /// Возраст перенесён с прошлых суток (`with_carried_births`, аудит дизайна 22.09 Т3):
    /// рождение раньше начала трекера, но уровень наблюдался непрерывно — прогрев его не глушит.
    pub(super) carried: bool,
    pub(super) max: i64,
    pub(super) max_ms: i64,
    pub(super) prev: i64,
    pub(super) first_decrease_ms: Option<i64>,
    pub(super) repeat: u32,
    pub(super) seen_frame: u64,
    pub(super) seen_top50: bool,
    pub(super) seen_size: i64,
    /// Прошёл ли уровень порог силы на последнем наблюдении (у режимов без
    /// силы — всегда `true`).
    pub(super) seen_strong: bool,
    pub(super) traded: i64,
    /// RPI-сделки на этой цене: в `traded` не идут, но копятся отдельно —
    /// иначе метка исхода считает чужой объём (В-55).
    pub(super) rpi: i64,
    /// Сумма лотов строго лучше уровня по цене на последнем кадре, где он
    /// наблюдался, — `swept_lots` будущего касания.
    pub(super) better_lots: i64,
    /// Слоты выборки фронтрана (В-45): текущая секунда наблюдений и
    /// предыдущая; `None` — уровень моложе одного шага.
    pub(super) slot_new: FrontrunSlot,
    pub(super) slot_old: Option<FrontrunSlot>,
    /// Был ли уровень лучшей ценой на последнем наблюдении: касание — переход
    /// `false → true`, не состояние.
    pub(super) was_best: bool,
    /// Сколько касаний у уровня уже кончилось.
    pub(super) touch_index: u32,
    pub(super) touch: Option<Touch>,
    /// Взведённый подход (F1); `None` — не взведён.
    pub(super) approach: Option<Approach>,
    /// Сколько подходов у уровня уже кончилось (растёт как `touch_index`).
    pub(super) approach_index: u32,
    /// Лучшая цена другой стороны уходила за `2 × D` после последнего снятия
    /// — уровень может взводиться снова. У новорождённого `true`.
    pub(super) approach_clear: bool,
    /// TK-115 (журнал стен e65): на последнем кадре уровень был стеной по критерию подхода B1.
    pub(super) wall_on: bool,
    /// Кольцо выборок силы: метки и значения (`e2`, `-1` — не определена).
    pub(super) sh_ts: [i64; STRENGTH_HIST_SLOTS],
    pub(super) sh_e2: [i64; STRENGTH_HIST_SLOTS],
    pub(super) sh_len: u8,
    pub(super) sh_next: u8,
    /// Кольцо Г-55 (`ArmP08::eat_60s_lots`/`size_max_60s_lots`): ведётся
    /// только при включённом подходе (`approach_bps`).
    pub(super) eat: EatRing,
}

impl Live {
    /// Пора ли писать выборку силы кадра `ts_ms`: с последней прошло не меньше
    /// `STRENGTH_SAMPLE_MS` (первая — сразу). Вынесено, чтобы силу кадра
    /// считать только тогда, когда она будет записана (Р9, T-17).
    pub(super) fn strength_sample_due(&self, ts_ms: i64) -> bool {
        if self.sh_len > 0 {
            let last = (self.sh_next as usize + STRENGTH_HIST_SLOTS - 1) % STRENGTH_HIST_SLOTS;
            if ts_ms.saturating_sub(self.sh_ts[last]) < STRENGTH_SAMPLE_MS {
                return false;
            }
        }
        true
    }

    /// Выборка силы кадра `ts_ms`: пишется, если с последней прошло не меньше
    /// `STRENGTH_SAMPLE_MS` (первая — сразу).
    pub(super) fn observe_strength(&mut self, ts_ms: i64, e2: i64) {
        if !self.strength_sample_due(ts_ms) {
            return;
        }
        let i = self.sh_next as usize;
        self.sh_ts[i] = ts_ms;
        self.sh_e2[i] = e2;
        self.sh_next = ((i + 1) % STRENGTH_HIST_SLOTS) as u8;
        if (self.sh_len as usize) < STRENGTH_HIST_SLOTS {
            self.sh_len += 1;
        }
    }

    /// Минимум силы за `[ts_ms - window_ms, ts_ms]` по выборкам и текущему
    /// значению `now_e2`; уровень моложе окна или хоть одна выборка окна не
    /// определена (`-1`) — `-1`.
    pub(super) fn strength_held_e2(&self, ts_ms: i64, window_ms: i64, now_e2: i64) -> i64 {
        if ts_ms.saturating_sub(self.birth_ms) < window_ms || now_e2 < 0 {
            return -1;
        }
        let mut m = now_e2;
        for k in 0..(self.sh_len as usize) {
            if self.sh_ts[k] >= ts_ms.saturating_sub(window_ms) {
                if self.sh_e2[k] < 0 {
                    return -1;
                }
                m = m.min(self.sh_e2[k]);
            }
        }
        m
    }

    /// Наблюдение лотов впереди уровня в кадре `ts_ms`: тот же шаг слота, что
    /// в документации модуля — новый слот старше `FRONTRUN_BACK_MS` уходит в
    /// старый, иначе дописывается последним наблюдением.
    pub(super) fn observe_frontrun(&mut self, ts_ms: i64, lots: i64, tick: Option<i64>) {
        if ts_ms.saturating_sub(self.slot_new.start_ms) >= FRONTRUN_BACK_MS {
            self.slot_old = Some(self.slot_new);
            self.slot_new = FrontrunSlot::new(ts_ms, lots, tick);
        } else {
            self.slot_new.last_ms = ts_ms;
            self.slot_new.last_lots = lots;
            self.slot_new.last_tick = tick;
        }
    }

    /// Лоты впереди уровня на кадре не позже `ts_ms − FRONTRUN_BACK_MS` по
    /// двум слотам (В-45): последнее наблюдение старого слота, если оно не
    /// позже секунды до `ts_ms`, иначе его первое; старого нет — первое
    /// наблюдение нового (уровень моложе шага). Зовётся после
    /// `observe_frontrun` того же кадра: старый слот тогда начат не позже
    /// `ts_ms − FRONTRUN_BACK_MS` по построению шага.
    pub(super) fn frontrun_before(&self, ts_ms: i64) -> (i64, Option<i64>) {
        match self.slot_old {
            Some(old) if old.last_ms.saturating_add(FRONTRUN_BACK_MS) <= ts_ms => {
                (old.last_lots, old.last_tick)
            }
            Some(old) => (old.first_lots, old.first_tick),
            None => (self.slot_new.first_lots, self.slot_new.first_tick),
        }
    }
}

/// Ключ с событием касания за кадр и «завал» касания, начавшегося в нём
/// (считается после обхода кадра, до свипа; у конца касания не читается).
#[derive(Debug, Clone, Copy)]
pub(super) struct Touched {
    pub(super) key: (u8, i64),
    pub(super) stack: u32,
    pub(super) stack_next_tick: Option<i64>,
}

/// Пары (ключ, значение), отсортированные по ключу в `Vec` — тот же приём,
/// что `Book::HalfBook` (A4, `ARCHITECTURE.md`), обобщённый на карту: дерево-
/// словарь аллоцирует узел на каждую вставку и гоняет указатели на обходе
/// (запреты 1 и 7 горячего пути), здесь вставка и удаление — `memmove` в уже
/// выделенной ёмкости, поиск — двоичный по ключу. Живых уровней разом немного
/// (это уже отфильтрованные плотности, а не полная книга на 256 тиков), так
/// что сдвиг на вставку дешевле дерева — довод тот же, что у `HalfBook`.
pub(super) struct SortedVec<K, V> {
    pub(super) items: Vec<(K, V)>,
}

impl<K: Ord + Copy, V> SortedVec<K, V> {
    pub(super) fn with_capacity(cap: usize) -> Self {
        Self {
            items: Vec::with_capacity(cap),
        }
    }

    /// Уже отсортированные по ключу пары без повторов — вход `with_carried_births`
    /// (снимок конца прошлых суток; строится обходом `live` в порядке ключа).
    pub(super) fn from_sorted(items: Vec<(K, V)>) -> Self {
        debug_assert!(
            items.windows(2).all(|w| w[0].0 < w[1].0),
            "вход обязан быть отсортирован по ключу без повторов"
        );
        Self { items }
    }

    pub(super) fn find(&self, key: &K) -> Result<usize, usize> {
        self.items.binary_search_by(|(k, _)| k.cmp(key))
    }

    /// Поиск «с пальца» (К1, T-23; Судья b86eed6): `hint` — позиция прошлого
    /// поиска того же обхода. Уровни кадра идут от лучшего наружу, ключи обхода
    /// монотонны, и ответ обычно в шаге-двух от пальца: галоп 1, 2, 4, … от `hint`
    /// в сторону ключа сужает отрезок, двоичный поиск идёт внутри него (профиль
    /// касаний 26.09: ≈ 36 % сэмплов — двоичный поиск по всему `live`). Ключи
    /// уникальны и отсортированы, поэтому ответ тот же, что у `find`, при любой
    /// подсказке — немонотонный обход только дороже: `Ok` — единственный индекс
    /// ключа, `Err` — единственная точка вставки.
    #[inline(always)]
    pub(super) fn find_from(&self, hint: usize, key: &K) -> Result<usize, usize> {
        let items = &self.items;
        let n = items.len();
        let h = hint.min(n);
        let (lo, hi) = if h < n && items[h].0 <= *key {
            if items[h].0 == *key {
                return Ok(h);
            }
            // Ключ правее пальца: всё до `lo` меньше ключа, `hi` — первый больший.
            let mut lo = h + 1;
            let mut step = 1;
            loop {
                let probe = h + step;
                if probe >= n {
                    break (lo, n);
                }
                match items[probe].0.cmp(key) {
                    Ordering::Less => {
                        lo = probe + 1;
                        step *= 2;
                    }
                    Ordering::Equal => return Ok(probe),
                    Ordering::Greater => break (lo, probe),
                }
            }
        } else {
            // Ключ левее пальца (или палец за концом): от `hi` и дальше — больше ключа.
            let mut hi = h;
            let mut step = 1;
            loop {
                if step > h {
                    break (0, hi);
                }
                let probe = h - step;
                match items[probe].0.cmp(key) {
                    Ordering::Greater => {
                        hi = probe;
                        step *= 2;
                    }
                    Ordering::Equal => return Ok(probe),
                    Ordering::Less => break (probe + 1, hi),
                }
            }
        };
        match items[lo..hi].binary_search_by(|(k, _)| k.cmp(key)) {
            Ok(j) => Ok(lo + j),
            Err(j) => Err(lo + j),
        }
    }

    pub(super) fn get(&self, key: &K) -> Option<&V> {
        self.find(key).ok().map(|i| &self.items[i].1)
    }

    /// `get_mut` с пальцем (К1): ищет от `*finger` и ставит палец на ответ —
    /// индекс ключа или его точку вставки (после `insert` в неё там же и лежит
    /// новый ключ, палец остаётся верным).
    #[inline(always)]
    pub(super) fn get_mut_from(&mut self, finger: &mut usize, key: &K) -> Option<&mut V> {
        let found = self.find_from(*finger, key);
        debug_assert_eq!(found, self.find(key), "поиск с пальца разошёлся с двоичным");
        match found {
            Ok(i) => {
                *finger = i;
                Some(&mut self.items[i].1)
            }
            Err(pos) => {
                *finger = pos;
                None
            }
        }
    }

    pub(super) fn get_mut(&mut self, key: &K) -> Option<&mut V> {
        match self.find(key) {
            Ok(i) => Some(&mut self.items[i].1),
            Err(_) => None,
        }
    }

    /// Вставляет или заменяет значение на ключе. В горячем пути зовётся
    /// только для заведомо нового ключа (это доказано вызывающим через
    /// предшествующий `get_mut`, вернувший `None`), но словарная семантика
    /// upsert этого не требует.
    pub(super) fn insert(&mut self, key: K, value: V) {
        match self.find(&key) {
            Ok(i) => self.items[i].1 = value,
            Err(pos) => self.items.insert(pos, (key, value)),
        }
    }

    pub(super) fn remove(&mut self, key: &K) -> Option<V> {
        match self.find(key) {
            Ok(i) => Some(self.items.remove(i).1),
            Err(_) => None,
        }
    }

    /// Значение на ключе — вычисляет и вставляет по умолчанию, если ключа
    /// ещё не было (замена `entry(..).or_default()` дерева-словаря).
    pub(super) fn get_or_insert_with(&mut self, key: K, default: impl FnOnce() -> V) -> &mut V {
        match self.find(&key) {
            Ok(i) => &mut self.items[i].1,
            Err(pos) => {
                self.items.insert(pos, (key, default()));
                &mut self.items[pos].1
            }
        }
    }

    pub(super) fn retain(&mut self, mut f: impl FnMut(&K, &mut V) -> bool) {
        self.items.retain_mut(|(k, v)| f(k, v));
    }

    pub(super) fn iter(&self) -> impl Iterator<Item = &(K, V)> {
        self.items.iter()
    }

    #[cfg(test)]
    pub(super) fn len(&self) -> usize {
        self.items.len()
    }

    /// Подслайс с ключами в `[lo, hi]`: ключи отсортированы, диапазон —
    /// непрерывный отрезок, `partition_point` — двоичный поиск его границ
    /// (замена диапазона дерева-словаря, которого у `Vec` нет).
    pub(super) fn range(&self, lo: K, hi: K) -> &[(K, V)] {
        let a = self.items.partition_point(|(k, _)| *k < lo);
        let b = self.items.partition_point(|(k, _)| *k <= hi);
        &self.items[a..b]
    }
}
