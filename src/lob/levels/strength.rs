//! Сила соседних уровней: окна, флаги и `neighbour_strength_e2`.

use super::*;

/// Трекер уровней. Состояние между кадрами — карты `live`/`births`/`carry`
/// (`SortedVec` выше) и переиспользуемые буферы новорождённых, свипа и ключей
/// с событием касания: установившийся кадр без рождений, смертей и касаний
/// не трогает кучу вообще (требование гейта GC).
/// Окна соседей для **оси** силы касания (исследование порога В-61,
/// 2026-09-18): на кадре старта касания считается сила «×соседи» для каждого
/// окна и пишется в `TouchRecord::strength_e2` — чтобы порог `S`/`W` выбирался
/// по распределению касаний и доле отскока, а не назначался. Это ось
/// разведки, как `HORIZONS_MS`, а не параметр решения.
pub const STRENGTH_WINDOWS_BPS: [i64; 3] = [10, 20, 50];

/// История силы (владелец 2026-09-18: «в текущую мс на реальном стакане — не
/// вся правда»): у живого уровня хранится выборка силы «×соседи» в ±20 bps не
/// чаще раза в `STRENGTH_SAMPLE_MS` (секунда — `HORIZONS_MS[1]`, как шаг
/// фронтрана В-45), `STRENGTH_HIST_SLOTS` слотов кольцом — покрытие не меньше
/// минуты. На старте касания — **минимум** силы за последние `T` секунд из
/// `STRENGTH_HELD_WINDOWS_S` («сила держалась всё окно»); уровень моложе `T`
/// — `-1`. Ось исследования, как `STRENGTH_WINDOWS_BPS`.
pub const STRENGTH_HELD_WINDOWS_S: [i64; 4] = [1, 5, 15, 60];

/// Окна реакции после касания, секунды (E4 базы отскока: «1–3 с» из
/// транскрипций [K 44:53; N 1:27:16; A 17:06]) — `TouchRecord::traded_first_s`.
pub const REACTION_WINDOWS_S: [i64; 3] = [1, 2, 3];

/// Окно оборота инструмента для силы «×поток», минут: час — как у
/// сторонних сканеров плотностей и в транскрипциях («за последний час»).
pub const FLOW_WINDOW_MIN: usize = 60;

pub const STRENGTH_SAMPLE_MS: i64 = HORIZONS_MS[1];

pub const STRENGTH_HIST_SLOTS: usize = 64;

/// Окно соседей истории силы — среднее из `STRENGTH_WINDOWS_BPS`.
pub(super) const STRENGTH_HIST_WINDOW_BPS_E2: i64 = STRENGTH_WINDOWS_BPS[1] * 100;

/// Сила «×соседи» наблюдения `i` кадра в процентах × 100: `10_000 × size ×
/// cnt / sum(соседи)`; соседи — остальные наблюдения той же стороны с тиком в
/// `±tick × window_bps_e2 / 1e6`; `-1` — соседей в окне нет (сила не
/// определена). `prefix` — префиксные суммы размеров кадра (`prefix[0] = 0`).
pub(crate) fn neighbour_strength_e2(
    levels: &[LevelObs],
    prefix: &[i64],
    i: usize,
    window_bps_e2: i64,
) -> i64 {
    let o = &levels[i];
    let asc = levels.len() < 2 || levels[0].tick <= levels[levels.len() - 1].tick;
    let w = ((o.tick as i128) * (window_bps_e2 as i128) / 1_000_000i128) as i64;
    let (lo_t, hi_t) = (o.tick.saturating_sub(w), o.tick.saturating_add(w));
    let (a, b) = if asc {
        (
            levels.partition_point(|x| x.tick < lo_t),
            levels.partition_point(|x| x.tick <= hi_t),
        )
    } else {
        (
            levels.partition_point(|x| x.tick > hi_t),
            levels.partition_point(|x| x.tick >= lo_t),
        )
    };
    debug_assert!(a <= i && i < b, "уровень обязан быть в своём же окне");
    let sum = (prefix[b] - prefix[a] - o.size_lots) as i128;
    let cnt = (b - a).saturating_sub(1) as i128;
    if cnt == 0 || sum <= 0 {
        return -1;
    }
    ((o.size_lots as i128) * 10_000i128 * cnt / sum).min(i64::MAX as i128) as i64
}

/// Флаг силы «×соседи» на каждое наблюдение кадра (roadmap §3, B): сила =
/// `100 × size / mean(size соседей той же стороны в ±window)`; окно в тиках
/// — `tick × window_bps / 1e4`. Кадр идёт от лучшей цены вглубь, тики
/// монотонны, поэтому соседи — непрерывный отрезок индексов (бинарный
/// поиск), суммы — префиксные. Без соседей сила не определена — `false`.
/// Векторы — рабочие буферы трекера: после прогрева аллокаций нет.
pub(super) fn strength_flags(
    mode: H3Mode,
    levels: &[LevelObs],
    ok: &mut Vec<bool>,
    prefix: &mut Vec<i64>,
) {
    ok.clear();
    // Префиксные суммы нужны всегда: осью силы касания (`STRENGTH_WINDOWS_BPS`)
    // пользуется и режим без порога силы.
    prefix.clear();
    prefix.push(0);
    for o in levels {
        let last = *prefix.last().unwrap_or(&0);
        prefix.push(last.saturating_add(o.size_lots));
    }
    let Some((pct_e2, window_bps_e2)) = mode.strength_gate() else {
        ok.resize(levels.len(), true);
        return;
    };
    for i in 0..levels.len() {
        let e2 = neighbour_strength_e2(levels, prefix, i, window_bps_e2);
        ok.push(e2 >= pct_e2);
    }
}
