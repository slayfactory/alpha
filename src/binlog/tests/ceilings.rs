//! Потолок разжатого кадра, старые версии файла, переполнение чтения.

use super::*;

// -----------------------------------------------------------------
// Требование 7 (ревизия 10, Decision 23): потолок разжатого кадра,
// вычисленный из `max_records_per_frame` заголовка.
// -----------------------------------------------------------------

/// Кодирует `n` заведомо одинаковых нулевых записей напрямую через кодировщик
/// v3 — не через `Writer::write_frame`, который сам отказался бы писать кадр
/// длиннее заголовочного потолка (см. тест ниже про эту самую проверку).
/// Записи одинаковы, поэтому ложатся одной группой: эпоха, заголовок группы и
/// по цене-размеру на запись. Такая нагрузка сжимается в исчезающе малый кадр
/// на диске, разжимаясь в огромный, — ровно форма, которую и обязан отвергать
/// потолок заголовка.
pub(super) fn raw_frame_payload_all_zero(n: usize) -> Vec<u8> {
    let records: Vec<Record> = (0..n).map(|_| rec(0, 0, 0, 0, 0)).collect();
    let mut scratch = Vec::new();
    encode_frame_payload_v3(&records, &mut scratch);
    scratch
}

/// Заворачивает уже готовую (разжатую) полезную нагрузку в кадр
/// формата этого файла: `u32` длина сжатого блока LE, затем сам блок —
/// то же самое, что пишет `Writer::write_frame` после кодирования, но
/// здесь собрано вручную в обход его проверки потолка (см. доку
/// `raw_frame_payload_all_zero`).
pub(super) fn frame_bytes_from_payload(payload: &[u8]) -> Vec<u8> {
    let compressed = zstd::bulk::compress(payload, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    let len = u32::try_from(compressed.len()).unwrap();
    let mut out = Vec::with_capacity(LEN_PREFIX + compressed.len());
    out.extend_from_slice(&len.to_le_bytes());
    out.extend_from_slice(&compressed);
    out
}

#[test]
#[should_panic(expected = "превышает потолок заголовка max_records_per_frame")]
fn writer_refuses_frame_exceeding_max_records_per_frame() {
    let max = 2u32;
    let hdr = Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: max,
    };
    let mut w = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    let over_limit: Vec<Record> = (0..(max as i64 + 1))
        .map(|i| rec(ev_delta_ask(), i, i, i, i))
        .collect();
    // Программная ошибка вызывающего (см. доку `Writer::write_frame`) —
    // тест проверяет, что это паника, а не `Result::Err`.
    let _ = w.write_frame(&over_limit);
}

/// Кадр ровно на потолке (`records.len() == max_records_per_frame`) —
/// граничное значение, а не «за» ним: `Writer` обязан согласиться его
/// написать (`<=`, не `<`), и `Reader` обязан прочитать его штатно.
#[test]
fn frame_at_exactly_the_maximum_still_reads() {
    let max = 3u32;
    let hdr = Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: max,
    };
    // Малые значения (все поля кодируются одним байтом: `ev`/`order_id`
    // < 128, дельты в [-64, 63], `fval = 0.0`) — записи занимают ровно
    // `MIN_RECORD_LEN` = 8 байт каждая, и разжатое тело кадра совпадает
    // с потолком (`8 + 3*8 = 32`) байт в байт, не с запасом. Тест
    // проверяет именно эту границу: `<=`, а не `<`, у сравнения внутри
    // bounded zstd API. Кадр с тем же числом записей, но с типичными
    // для потока полями (большие флаги `ev`, дельты времени в наносекундах
    // между записями одного кадра) занял бы больше 8 байт на запись —
    // это не противоречие: `max_records_per_frame` в заголовке обязан
    // выбираться вызывающим (рекордером) с запасом над реальным
    // байтовым размером его собственных кадров, а не равняться
    // количеству записей, которое он фактически туда кладёт.
    let frame: Vec<Record> = (0..max as i64).map(|i| rec(i as u64, i, i, i, i)).collect();
    let bytes = write_all(hdr, std::slice::from_ref(&frame));
    let (read_hdr, frames) = read_all(&bytes);
    assert_eq!(read_hdr, hdr);
    assert_eq!(
        frames,
        vec![frame],
        "кадр ровно на потолке обязан читаться штатно"
    );
}

/// Файл версии 1 (до ревизии 10 Decision 23): целый и полный заголовок
/// этой версии — magic(4) + version(1) + tick_e9(8) + step_e9(8) = 21
/// байт, **без** `max_records_per_frame` и без единого лишнего байта
/// сверх. Собран напрямую, потому что ни один писатель этого кода версию 1
/// не пишет.
///
/// Длина нарочно ровно 21, не 25 (`HEADER_LEN` версий 2 и 3): если бы
/// `Reader::open` по-прежнему читал единым куском фиксированные
/// `HEADER_LEN` байт (одним чтением на весь заголовок, как до этой
/// правки), этих 21 не хватило бы на затребованные 25, и код вернул бы
/// `TruncatedHeader` — правдоподобную, но **вводящую в заблуждение**
/// ошибку: файл не обрезан, он просто другой, более старой версии.
/// Два раздельных чтения (`MAGIC_VERSION_LEN`, затем `HEADER_TAIL_LEN`)
/// обязаны поймать несовпадение версии на первом шаге, пятью байтами,
/// раньше, чем код вообще спросит про хвост — вот что здесь проверяется.
#[test]
fn version_one_file_is_rejected_not_misread() {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    bytes.push(1);
    bytes.extend_from_slice(&TICK_E9.to_le_bytes());
    bytes.extend_from_slice(&STEP_E9.to_le_bytes());
    assert_eq!(
        bytes.len(),
        21,
        "ровно заголовок версии 1, ни байтом больше"
    );

    let err = Reader::open(&bytes[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::UnsupportedVersion { got: 1 },
        "старая версия обязана называться прямо, а не маскироваться под усечение"
    );
}

/// Тело кадра v2 с явными мёртвыми полями: восемь полей на запись, как их
/// писала живая запись (`ival` — из блочности, `order_id`/`fval` — нули).
/// Ненулевые значения подставляет тест: без них счётчики
/// `Reader::legacy_dead_fields` — то самое воспроизводимое доказательство
/// «0 % ненулевых» на старых файлах — нечем проверить.
pub(super) fn v2_payload_with_dead_fields(records: &[Record], dead: &[(u64, i64, u64)]) -> Vec<u8> {
    let mut out = Vec::new();
    let epoch_ns = records.first().map_or(0, |r| r.exch_ts_ns);
    out.extend_from_slice(&epoch_ns.to_le_bytes());
    let mut prev_price = 0i64;
    let mut prev_qty = 0i64;
    for (r, (order_id, ival, fval_bits)) in records.iter().zip(dead) {
        write_uvarint(&mut out, r.ev);
        write_zigzag(&mut out, r.exch_ts_ns.wrapping_sub(epoch_ns));
        write_zigzag(&mut out, r.local_ts_ns.wrapping_sub(epoch_ns));
        write_zigzag(&mut out, r.price_ticks.wrapping_sub(prev_price));
        write_zigzag(&mut out, r.qty_lots.wrapping_sub(prev_qty));
        write_uvarint(&mut out, *order_id);
        write_zigzag(&mut out, *ival);
        write_uvarint(&mut out, *fval_bits);
        prev_price = r.price_ticks;
        prev_qty = r.qty_lots;
    }
    out
}

/// Собирает файл версии 1/2/3 вручную: заголовок с заданной версией и тела
/// кадров как есть. Нужен там, где пишется не текущая версия (`Writer` умеет
/// только её одну).
pub(super) fn file_with_version(version: u8, hdr: Header, payloads: &[Vec<u8>]) -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    bytes.push(version);
    bytes.extend_from_slice(&hdr.tick_e9.to_le_bytes());
    bytes.extend_from_slice(&hdr.step_e9.to_le_bytes());
    bytes.extend_from_slice(&hdr.max_records_per_frame.to_le_bytes());
    for payload in payloads {
        bytes.extend_from_slice(&frame_bytes_from_payload(payload));
    }
    bytes
}

/// Файл версии 2 — та форма, которую пишет живой коллектор до перезапуска на
/// новый бинарник (В-41): заголовок тот же, тело — восемь полей на запись.
/// Обязан читаться новым читателем, иначе вся идущая запись перестала бы
/// читаться; `order_id`/`ival`/`fval` из потока читаются, `ival` становится
/// `block`, а счётчики мёртвых полей остаются воспроизводимыми.
#[test]
fn version_two_file_is_read_as_legacy_with_dead_field_counters() {
    let frame = vec![
        rec(ev_snapshot_bid(), 10, 20, 100, 5),
        rec(ev_trade_buy(), 30, 40, 101, 2),
    ];
    let payload = v2_payload_with_dead_fields(&frame, &[(0, 0, 0), (u64::MAX, 1, 7)]);
    let bytes = file_with_version(VERSION_V2, header(), std::slice::from_ref(&payload));

    let mut r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.version(), VERSION_V2);
    let got = r
        .read_frame()
        .unwrap()
        .expect("кадр версии 2 обязан читаться");
    assert!(r.read_frame().unwrap().is_none());
    assert_eq!(got.len(), 2);
    assert_eq!(got[0], frame[0]);
    assert_eq!(got[1].ev, frame[1].ev);
    assert_eq!(got[1].price_ticks, frame[1].price_ticks);
    assert!(got[1].block, "`ival != 0` — это `block`");
    let dead = r.legacy_dead_fields();
    assert_eq!(dead.nonzero_order_id, 1);
    assert_eq!(dead.nonzero_ival, 1);
    assert_eq!(dead.nonzero_fval, 1);
}

/// У файла версии 3 счётчиков мёртвых полей нет по построению: таких полей в
/// формате не существует, и нули здесь — не «повезло с данными», а свойство
/// раскладки.
#[test]
fn version_three_file_reports_no_dead_fields() {
    let bytes = write_all(header(), &[vec![rec(ev_trade_buy(), 10, 20, 100, 5)]]);
    let mut r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.version(), VERSION);
    assert!(r.read_frame().unwrap().is_some());
    assert_eq!(r.legacy_dead_fields(), LegacyDeadFields::default());
}

/// Кадр, чья заявленная (сжатая) длина на диске правдоподобна, но
/// распаковка которого превышает потолок заголовка: отвергается как
/// `Err`, не паника, и — проверено через `alloc_count` — не после того,
/// как память под весь разжатый объём уже выделена. Потолок читателя
/// (`max_frame_payload_bytes`) сам по себе не бесполезен только если
/// он ограничивает именно **аллокацию**, а не служит числом, которое
/// код печатает уже после того, как разжал кадр целиком, — это и есть
/// разница между bounded API и «разжать, потом проверить».
#[test]
fn frame_exceeding_the_header_ceiling_is_rejected_without_full_allocation() {
    let max = 10u32;
    let hdr = Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: max,
    };
    let expected_ceiling = max_frame_payload_bytes(max);
    // 8 (эпоха) + 10 записей * 8 байт (минимум) = 88 — записано числом
    // здесь исключительно для читаемости остальных чисел теста, само
    // значение проверено равенством `max_frame_payload_bytes(max)` выше.
    assert_eq!(expected_ceiling, 88);

    let mut file = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL)
        .unwrap()
        .into_inner();

    // Сильно за потолком, не впритык: два миллиона одинаковых нулевых записей
    // дают разжатый объём 8 (эпоха) + 5 (заголовок группы) + 2 на запись —
    // около четырёх мегабайт, то есть в десятки тысяч раз больше 88-байтового
    // потолка этого теста, — и при этом сжимаются в исчезающе малый кадр на
    // диске (проверено ниже).
    const N: usize = 2_000_000;
    let payload = raw_frame_payload_all_zero(N);
    assert!(
        payload.len() > expected_ceiling,
        "проверка теста на себе: нагрузка обязана быть за потолком заголовка \
         ({expected_ceiling} байт), иначе отвергать было бы нечего"
    );
    let frame_on_disk = frame_bytes_from_payload(&payload);
    // Проверка теста на себе: «заявленная длина правдоподобна» значит
    // сжатый кадр на диске обязан быть на порядки меньше того, во что
    // он разжимается (не впритык к потолку — потолок сам по себе
    // маленький, 88 байт, и сжатый кадр здесь его не меньше), иначе
    // это тест на что-то другое, не на bounded decompression.
    assert!(
        frame_on_disk.len() * 100 < payload.len(),
        "проверка теста на себе: сжатый кадр ({} байт) обязан быть на порядки \
         меньше разжатого объёма ({} байт), иначе это не «легко сжимаемая \
         полезная нагрузка», о которой говорит тест",
        frame_on_disk.len(),
        payload.len()
    );
    file.extend_from_slice(&frame_on_disk);

    let mut r = Reader::open(&file[..]).unwrap();
    let (result, counts) = alloc_count::measure(|| r.read_frame());
    let err = result
        .expect_err("кадр, чья распаковка превышает потолок заголовка, обязан быть ошибкой, не Ok");
    assert_eq!(
        err,
        BinlogError::FrameExceedsHeaderCeiling {
            max_records_per_frame: max,
            ceiling_bytes: expected_ceiling,
        }
    );
    assert!(
        counts.bytes < 50_000,
        "аллокация обязана остаться в пределах потолка заголовка ({expected_ceiling} байт \
         в этом тесте), а не расти пропорционально разжатому объёму (16 000 008 байт) — \
         реально выделено {} байт",
        counts.bytes
    );
}

/// Ремонт W3 (ревью 23.09): потолок заголовка теперь сравнивается с `len`
/// сразу после чтения префикса длины, **до** единого байта тела — раньше
/// `corrupt_length_prefix_does_not_pre_allocate_ahead_of_the_stream` (выше)
/// доказывал только вторую защиту (чтение кусками, не аллокация `len` байт
/// заранее); этот тест — первую и более раннюю: `len`, для которой ни один
/// настоящий кадр этого заголовка не может существовать
/// (`> max_frame_bytes_on_disk`), отвергается до попытки прочитать тело
/// вовсе — на диске после длины нет ни байта, и без этой правки читатель
/// получил бы `ShortRead` вместо честного «кадр за потолком заголовка».
#[test]
fn length_exceeding_the_on_disk_ceiling_is_rejected_before_reading_the_body() {
    let max = 10u32;
    let hdr = Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: max,
    };
    let ceiling = max_frame_bytes_on_disk(max as usize);
    let mut bytes = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL)
        .unwrap()
        .into_inner();
    assert_eq!(bytes.len(), HEADER_LEN, "только заголовок, кадров ещё нет");
    // Заявленная длина — на один байт за потолком, тела на "диске" для неё
    // нет вовсе (файл кончается сразу после этих четырёх байт).
    let over = u32::try_from(ceiling + 1).unwrap();
    bytes.extend_from_slice(&over.to_le_bytes());

    let mut r = Reader::open(&bytes[..]).unwrap();
    let (result, counts) = alloc_count::measure(|| r.read_body());
    assert_eq!(
        result.unwrap_err(),
        BinlogError::FrameExceedsHeaderCeiling {
            max_records_per_frame: max,
            ceiling_bytes: ceiling,
        },
        "длина за потолком обязана отвергаться им явно, не выглядеть усечением"
    );
    assert!(
        counts.bytes < 4096,
        "потолок обязан сработать раньше первого байта тела — аллокация осталась малой: \
         {} байт",
        counts.bytes
    );
}

/// Ремонт W3 (ревью 23.09): заголовок с испорченным `max_records_per_frame`
/// (здесь — `u32::MAX`, то же значение, которым `a_corrupt_header_cannot_
/// raise_the_ceiling_past_the_day_budget` выше насыщает потолок разжатого
/// тела об `HARD_PAYLOAD_CEILING`, ~1.5 ГБ) не имеет права требовать под
/// разжатие настоящего кадра, чьё сжатое тело на диске — считаные байты,
/// приближения к этой границе: второй, более тесный потолок от реального
/// размера кадра на диске (`MAX_DECOMPRESSION_RATIO`) обязан включиться
/// раньше, чем `bulk::Decompressor::decompress` попросит у аллокатора
/// заметную долю гигабайта. Кадр при этом настоящий и **честно записан** —
/// испорчен только заголовок постфактум (один перевёрнутый бит на диске,
/// не программная ошибка), поэтому чтение обязано не просто не разорваться
/// в аллокации, а вернуть его штатно.
#[test]
fn a_corrupt_max_records_per_frame_does_not_balloon_allocation_for_a_tiny_real_frame() {
    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)]]);
    // Затираем max_records_per_frame заголовка испорченным значением
    // напрямую в байтах (смещение 21..25, см. `Writer::create`) — тело
    // единственного кадра при этом остаётся настоящим, крошечным.
    bytes[21..25].copy_from_slice(&u32::MAX.to_le_bytes());

    let mut r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.header().max_records_per_frame, u32::MAX);
    let (result, counts) = alloc_count::measure(|| r.read_frame());
    let frame = result
        .expect(
            "настоящий крошечный кадр обязан читаться штатно, несмотря на испорченный заголовок",
        )
        .expect("это первый кадр — None здесь значило бы, что в файле их не осталось");
    assert_eq!(frame.len(), 1);
    assert!(
        counts.bytes < 1024 * 1024,
        "испорченный max_records_per_frame не имеет права требовать гигабайты под кадр \
         в считаные байты на диске — реально выделено {} байт",
        counts.bytes
    );
}

/// Ремонт W3, часть (в) (ревью 23.09): `read_upto` — общий примитив чтения
/// и обычного файла, и разжимающегося на лету потока архива (T46) — не
/// имеет права довериться `Read::read`, сообщившему больше байт, чем ему
/// давали читать: `filled += n` без `min` увело бы `filled` за `buf.len()`,
/// и следующая итерация `&mut buf[filled..]` запаниковала бы на срезе с
/// началом за концом (Decision 7: усечение обязано быть видно как ошибка,
/// не как паника). Ни `File`, ни `zstd::stream::read::Decoder` контракт не
/// нарушают — оборону проверяет подставной источник, который врёт нарочно.
pub(super) struct OverreadingSource;

impl std::io::Read for OverreadingSource {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        Ok(buf.len() + 7)
    }
}

#[test]
fn read_upto_survives_a_read_impl_that_overreports_bytes_written() {
    let mut buf = [0u8; 16];
    let status = super::super::read_upto(&mut OverreadingSource, &mut buf).unwrap();
    assert!(
        matches!(status, ReadStatus::Full),
        "буфер обязан считаться полностью заполненным, не переполненным"
    );
}
