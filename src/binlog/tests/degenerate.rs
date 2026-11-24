//! Аллокации на запись и вырожденный ввод: ошибка, не паника.

use super::*;

// -----------------------------------------------------------------
// Требование 5: аллокации на запись не растут с числом событий.
// -----------------------------------------------------------------

/// Бюджет GC (`PLAN.md` шаг 0.2; SETTLED.md B2) — буквально ноль
/// аллокаций на событие после прогрева, не просто «не растёт кратно».
/// Раньше эта проверка допускала рост вплоть до `small * 2 + 4` —
/// свойство, которое зелёный тест давал бы и при паре аллокаций на
/// кадр (так и было: `zstd::stream::copy_encode` заводил новый
/// контекст сжатия на каждый вызов). `Writer` теперь держит
/// переиспользуемый `zstd::bulk::Compressor`, и бюджет проверяется
/// как заявлено в плане.
#[test]
fn write_path_allocates_nothing_after_warmup() {
    const FRAMES: usize = 4;

    fn synth(n: usize, seed: i64) -> Vec<Record> {
        (0..n)
            .map(|i| {
                let idx = seed + i as i64;
                rec(
                    ev_delta_ask(),
                    1_000_000 + idx,
                    1_000_100 + idx,
                    500 + (idx % 13),
                    10 + (idx % 7),
                )
            })
            .collect()
    }

    fn allocations_for(events_per_frame: usize) -> u64 {
        // `io::sink()`, не `Vec::new()`: сток теста не должен участвовать
        // в замере. Растущий `Vec<u8>`, копящий байты всех кадров подряд,
        // сам периодически перевыделяется (амортизированное удвоение
        // ёмкости) — это аллокации стока теста, а не `write_frame`, и
        // с `Vec::new()` они попадали бы в тот же счётчик, маскируя
        // настоящий бюджет под ложным «не совсем ноль».
        let mut w = Writer::create(io::sink(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();

        // Прогрев: кадр той же формы вне замера, чтобы `scratch`,
        // `compressed` и `compressor` выросли до итоговой ёмкости
        // заранее — гейт GC требует «ноль после прогрева», а не
        // «ноль с первого кадра» (тот же принцип, что в
        // `alloc_count::tests`).
        w.write_frame(&synth(events_per_frame, 0)).unwrap();

        // Кадры строятся заранее и вне замера, чтобы считать только
        // аллокации самого `write_frame`, а не тестовых данных. Сдвиг
        // `seed` — кратное 91 (= НОК(13, 7), периодов `idx % 13` и
        // `idx % 7` выше): фаза обоих циклов совпадает с прогревом
        // (`seed = 0`), так что каждый кадр кодируется в те же самые
        // байты, что и прогревочный, — иначе кадр со сдвинутой фазой
        // мог бы дать дельту на один байт длиннее и вызвать рост
        // `scratch`/`compressed`, не имеющий отношения к бюджету GC.
        let batches: Vec<Vec<Record>> = (0..FRAMES)
            .map(|k| synth(events_per_frame, (k as i64 + 1) * 91))
            .collect();

        let (_, counts) = alloc_count::measure(|| {
            for batch in &batches {
                w.write_frame(batch).unwrap();
            }
        });
        counts.allocations
    }

    for events_per_frame in [1_000usize, 10_000] {
        let allocations = allocations_for(events_per_frame);
        assert_eq!(
            allocations, 0,
            "путь разбор-и-запись обязан быть нулевым после прогрева \
             ({events_per_frame} записей/кадр, {FRAMES} кадра): было {allocations}"
        );
    }
}

/// То же самое, но на масштабе, который SETTLED.md B2 называет
/// буквально: «измеряется проигрыванием 10^6 событий из заранее
/// заполненного буфера» — не 40 тысяч, как в тесте выше.
///
/// Дельты здесь ограничены по модулю (как в тесте выше), а не растут
/// без края с `idx`, как в `round_trips_one_million_events`: та форма
/// нарочно нужна для проверки корректности на больших скачках, но
/// делает байтовую длину кадра зависимой от того, насколько далеко
/// зашёл прогон, — здесь же кадр под замером обязан кодироваться
/// ровно в те же байты, что и прогревочный, на любом из миллиона
/// событий, иначе замер ловил бы рост данных, а не аллокатор.
#[test]
fn write_path_allocates_nothing_replaying_one_million_events() {
    const TOTAL: usize = 1_000_000;
    const PER_FRAME: usize = 20_000;

    fn synth(n: usize, seed: i64) -> Vec<Record> {
        (0..n)
            .map(|i| {
                let idx = seed + i as i64;
                rec(
                    ev_delta_ask(),
                    1_000_000 + idx,
                    1_000_100 + idx,
                    500 + (idx % 13),
                    10 + (idx % 7),
                )
            })
            .collect()
    }

    // `io::sink()`, не `Vec::new()` — см. комментарий в тесте выше.
    let mut w = Writer::create(io::sink(), header(), 1).unwrap();

    // Прогрев вне замера, как и в тесте выше.
    w.write_frame(&synth(PER_FRAME, 0)).unwrap();

    // Оставшиеся 980 000 событий, кадрами заранее — под замером
    // остаётся только сам `write_frame`. Сдвиг `seed` кратен 91
    // (см. комментарий в тесте выше), чтобы фаза `idx % 13`/`idx % 7`
    // всегда совпадала с прогревом.
    let batches: Vec<Vec<Record>> = (1..TOTAL / PER_FRAME)
        .map(|k| synth(PER_FRAME, (k as i64) * 91))
        .collect();

    let (_, counts) = alloc_count::measure(|| {
        for batch in &batches {
            w.write_frame(batch).unwrap();
        }
    });
    assert_eq!(
        counts.allocations, 0,
        "путь разбор-и-запись обязан быть нулевым после прогрева на \
         {TOTAL} событиях: было {}",
        counts.allocations
    );
}

// -----------------------------------------------------------------
// Требование 6: вырожденный ввод — ошибка, никогда не паника и не
// молчаливый пустой результат.
// -----------------------------------------------------------------

#[test]
fn empty_file_is_an_error() {
    let err = Reader::open(&b""[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::TruncatedHeader {
            got: 0,
            want: HEADER_LEN
        }
    );
}

#[test]
fn header_only_file_is_an_error_not_an_empty_result() {
    // Валидный заголовок, ни одного кадра — в сутках нет даже
    // синтетического снапшота, которого требует Decision 7.
    let w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    let bytes = w.into_inner();
    assert_eq!(bytes.len(), HEADER_LEN);

    let mut r = Reader::open(&bytes[..]).unwrap();
    let err = r.read_frame().unwrap_err();
    assert_eq!(err, BinlogError::MissingSnapshot);
}

#[test]
fn frame_claiming_more_bytes_than_remain_is_an_error() {
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&[rec(ev_snapshot_bid(), 0, 1, 10, 10)])
        .unwrap();
    let mut bytes = w.into_inner();

    // Читаем реальную длину первого кадра и завышаем её, оставляя тело
    // прежним: заявленная длина теперь превышает то, что реально есть.
    let len_at = HEADER_LEN;
    let real_len = u32::from_le_bytes(bytes[len_at..len_at + 4].try_into().unwrap());
    let inflated = real_len + 10_000;
    bytes[len_at..len_at + 4].copy_from_slice(&inflated.to_le_bytes());

    let mut r = Reader::open(&bytes[..]).unwrap();
    let err = r.read_frame().unwrap_err();
    assert!(
        matches!(err, BinlogError::ShortRead { .. }),
        "ожидался ShortRead, получено {err:?}"
    );
}

/// Как тест выше, но с длиной, завышенной не на 10 000 байт, а на
/// сотни мегабайт — там, где `vec![0u8; len]` до проверки остатка
/// потока попытался бы выделить эти мегабайты впрок. Неудачная
/// аллокация такого масштаба — это `abort` процесса (не перехватываемая
/// паника), что противоречит «никогда не паника» из шапки модуля;
/// `Reader::read_frame` обязан читать кусками и остановиться на
/// `ShortRead`, не аллоцируя больше, чем реально пришло.
#[test]
fn corrupt_length_prefix_does_not_pre_allocate_ahead_of_the_stream() {
    const HUGE: u32 = 200 * 1024 * 1024; // 200 МиБ — заведомо больше, чем есть на "диске"
                                         // Свой заголовок, не общий `header()` (ремонт W3, ревью 23.09): `read_
                                         // body` теперь сравнивает `len` с потолком заголовка (`max_frame_bytes_
                                         // on_disk`) ДО чтения, и на `DEFAULT_TEST_MAX_RECORDS_PER_FRAME`
                                         // (~80 МиБ потолка) `HUGE` сама была бы отвергнута этой более ранней
                                         // проверкой — тест тогда доказывал бы её, а не то, ради чего он
                                         // написан: что чтение кусками не аллоцирует объявленный объём вперёд,
                                         // когда `len` ещё правдоподобна для потолка заголовка, но больше того,
                                         // что реально есть на диске. Потолок здесь намеренно поднят щедрым
                                         // `max_records_per_frame`, чтобы `HUGE` осталась в его границах с
                                         // большим запасом.
    let hdr = Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: 10_000_000,
    };
    let mut w = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&[rec(ev_snapshot_bid(), 0, 1, 10, 10)])
        .unwrap();
    let mut bytes = w.into_inner();

    let len_at = HEADER_LEN;
    bytes[len_at..len_at + 4].copy_from_slice(&HUGE.to_le_bytes());

    let mut r = Reader::open(&bytes[..]).unwrap();
    let (result, counts) = alloc_count::measure(|| r.read_frame());
    let err = result.unwrap_err();
    assert!(
        matches!(err, BinlogError::ShortRead { .. }),
        "ожидался ShortRead, получено {err:?}"
    );
    assert!(
        counts.bytes < 4 * 1024 * 1024,
        "испорченная длина ({HUGE} байт) не должна аллоцировать вперёд \
         объявленного объёма — реально выделено {} байт",
        counts.bytes
    );
}

#[test]
fn truncated_header_prefix_is_an_error() {
    // Короче даже magic+version (`MAGIC_VERSION_LEN`) — усечение обязано
    // ловиться на первом шаге чтения заголовка, до того как код вообще
    // пытается истолковать эти байты как magic (см. доку `Reader::open`
    // про два шага чтения).
    let bytes = [0u8; MAGIC_VERSION_LEN - 1];
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::TruncatedHeader {
            got: MAGIC_VERSION_LEN - 1,
            want: HEADER_LEN,
        }
    );
}

#[test]
fn truncated_header_tail_is_an_error() {
    // Magic и версия целы и валидны, хвост (tick_e9/step_e9/
    // max_records_per_frame) обрезан на один байт — усечение ловится на
    // втором шаге чтения, не читается как валидные, но сдвинутые байты.
    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    bytes.truncate(HEADER_LEN - 1);
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::TruncatedHeader {
            got: HEADER_LEN - 1,
            want: HEADER_LEN,
        }
    );
}

#[test]
fn bad_magic_is_rejected() {
    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    bytes[0] = b'X';
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert!(matches!(err, BinlogError::BadMagic { .. }));
}

#[test]
fn unsupported_version_is_rejected() {
    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    bytes[4] = VERSION_V4 + 1;
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::UnsupportedVersion {
            got: VERSION_V4 + 1
        }
    );
}
