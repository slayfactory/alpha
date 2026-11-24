//! Отсортированный вектор с «пальцем» и кольцо съеденного.

use super::*;

// К1 (T-23, Судья b86eed6): поиск «с пальца» в `SortedVec` — тот же ответ, что двоичный поиск.

/// Детерминированный xorshift — случайные кадры без внешних крейтов.
pub(super) fn xorshift(state: &mut u64) -> u64 {
    *state ^= *state << 13;
    *state ^= *state >> 7;
    *state ^= *state << 17;
    *state
}

pub(super) fn sorted_keys(keys: &[(u8, i64)]) -> SortedVec<(u8, i64), u32> {
    let mut sv = SortedVec::with_capacity(keys.len());
    for (n, k) in keys.iter().enumerate() {
        sv.insert(*k, n as u32);
    }
    sv
}

#[test]
fn find_from_equals_find_for_every_hint_and_key() {
    // Обе стороны, пустой `live`, края (до первого, после последнего, другой стороны),
    // палец за концом — на каждой длине до 12.
    for n in 0..=12i64 {
        let keys: Vec<(u8, i64)> = (0..n).map(|t| ((t % 2) as u8, 10 * t)).collect();
        let sv = sorted_keys(&keys);
        for hint in 0..=(n as usize + 3) {
            for side in 0..=1u8 {
                for tick in -5..=(10 * n + 5) {
                    let key = (side, tick);
                    assert_eq!(
                        sv.find_from(hint, &key),
                        sv.find(&key),
                        "n={n} hint={hint} key={key:?}"
                    );
                }
            }
        }
    }
}

#[test]
fn finger_walk_with_inserts_matches_binary_search_on_both_sides() {
    // Обход как в `scan_levels`: биды — тики вниз от лучшего, аски — вверх; отсутствующий
    // ключ иногда вставляется в точку ответа (вставка и до, и после прошлого пальца —
    // по стороне обхода); ответ и палец — как у двоичного поиска.
    let mut rng = 0x9E37_79B9_7F4A_7C15u64;
    for round in 0..400 {
        let mut live: SortedVec<(u8, i64), u32> = SortedVec::with_capacity(128);
        for _ in 0..(xorshift(&mut rng) % 60) {
            let side = (xorshift(&mut rng) % 2) as u8;
            let tick = (xorshift(&mut rng) % 200) as i64;
            live.insert((side, tick), 0);
        }
        for side in 0..=1u8 {
            let best = 60 + (xorshift(&mut rng) % 80) as i64;
            let depth = 1 + (xorshift(&mut rng) % 50) as i64;
            let mut finger = 0usize;
            for d in 0..depth {
                let tick = if side == 0 { best - d } else { best + d };
                let key = (side, tick);
                let expect = live.find(&key);
                let got = live.get_mut_from(&mut finger, &key).is_some();
                assert_eq!(got, expect.is_ok(), "round={round} key={key:?}");
                let at = match expect {
                    Ok(i) | Err(i) => i,
                };
                assert_eq!(finger, at, "палец не на ответе: round={round} key={key:?}");
                if expect.is_err() && xorshift(&mut rng).is_multiple_of(3) {
                    live.insert(key, round);
                    assert_eq!(live.find(&key), Ok(finger), "вставка не в точку пальца");
                }
            }
        }
    }
}

#[test]
fn finger_is_exact_on_non_monotone_and_repeated_keys() {
    // Запасной путь не нужен: галоп от любой подсказки сходится к тому же ответу —
    // немонотонный и повторный ключ только дороже, не иначе.
    let mut rng = 0xD1B5_4A32_D192_ED03u64;
    let mut live: SortedVec<(u8, i64), u32> = SortedVec::with_capacity(128);
    for t in (0..300).step_by(3) {
        live.insert(((t % 2) as u8, t), 0);
    }
    let mut finger = 0usize;
    for step in 0..5000 {
        let key = (
            (xorshift(&mut rng) % 2) as u8,
            (xorshift(&mut rng) % 320) as i64 - 10,
        );
        for _ in 0..(1 + step % 2) {
            let expect = live.find(&key);
            assert_eq!(
                live.get_mut_from(&mut finger, &key).is_some(),
                expect.is_ok()
            );
            let at = match expect {
                Ok(i) | Err(i) => i,
            };
            assert_eq!(finger, at, "step={step} key={key:?}");
        }
    }
}

/// TK-012 (Г-55): окно кольца — секунды `[S − 59, S]`: сделка в секунду `S − 60`
/// (ровно за 60 с до взвода) вне окна, в `S − 59` — внутри; размер — наибольший
/// видимый в тех же секундах.
#[test]
fn eat_ring_window_is_sixty_whole_seconds() {
    let mut r = EatRing::new(0, 10);
    r.observe_size(40, 30, 10); // размер 30 с секунды 40
    r.add_trade(40, 5, 30); // S − 60 при S = 100 — вне окна
    r.observe_size(41, 12, 30); // в секунде 41 видно 30 (стояло с 40), затем 12
    r.add_trade(41, 7, 12); // S − 59 — внутри
    r.observe_size(100, 12, 12);
    let (lots, max) = r.window(100);
    assert_eq!(lots, 7, "сделка ровно за 60 с — вне окна, за 59 с — внутри");
    assert_eq!(max, 30, "в секунде 41 ещё виден размер 30 до наблюдения 12");
    // Та же секунда 40 при S = 99 — внутри.
    let mut r = EatRing::new(0, 10);
    r.add_trade(40, 5, 10);
    r.observe_size(99, 10, 10);
    assert_eq!(r.window(99).0, 5);
}

/// TK-012 (Г-55): пропуск времени обнуляет слоты — сделки старше окна не
/// всплывают в слоте той же секунды `mod 60`, размер пропущенных секунд —
/// последний видимый; пропуск больше 60 с сбрасывает всё кольцо.
#[test]
fn eat_ring_resets_skipped_seconds() {
    let mut r = EatRing::new(0, 50);
    r.add_trade(5, 9, 50);
    r.observe_size(6, 8, 50);
    // Пропуск 55 с: секунда 65 делит слот с секундой 5.
    r.add_trade(65, 1, 8);
    assert_eq!(
        r.window(65),
        (1, 50),
        "сделки 5-й секунды вне окна; 50 видно в секунде 6"
    );
    // Пропуск больше окна: всё кольцо — последний видимый размер, ноль сделок.
    r.observe_size(1000, 4, 8);
    assert_eq!(r.window(1000), (0, 8));
    // Метка старше окна не пишется.
    r.add_trade(900, 100, 4);
    assert_eq!(r.window(1000), (0, 8));
}
