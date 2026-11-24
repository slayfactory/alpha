//! Кодек: точность значений, флаги групп, таблица событий, varint.

use super::*;

// -----------------------------------------------------------------
// Требование 4: точное сохранение цены/размера на значениях, которые
// f64 не может держать точно.
// -----------------------------------------------------------------

#[test]
fn deltas_survive_values_f64_cannot_hold_exactly() {
    // 2^53 + 1 — первое целое, которое f64 не представляет точно.
    const BEYOND_F64: i64 = 9_007_199_254_740_993;
    let frame = vec![
        rec(ev_snapshot_bid(), 0, 0, i64::MAX, i64::MAX),
        // Соседняя запись на противоположном краю диапазона: обычное
        // `i64::MIN - i64::MAX` переполняет i64 и в debug-сборке
        // запаниковало бы — здесь дельта берётся `wrapping_sub`.
        rec(ev_delta_ask(), 1, 1, i64::MIN, i64::MIN),
        rec(ev_delta_ask(), 2, 2, BEYOND_F64, -BEYOND_F64),
        rec(ev_delta_ask(), 3, 3, 0, 0),
    ];
    let bytes = write_all(header(), std::slice::from_ref(&frame));
    let (_, frames) = read_all(&bytes);
    assert_eq!(frames[0], frame, "цена и размер обязаны совпасть побитово");

    // f64 не смог бы: явная демонстрация того, зачем эта проверка нужна.
    assert_ne!(
        BEYOND_F64 as f64 as i64, BEYOND_F64,
        "иначе тест не проверяет то, что заявлено"
    );
}

/// Тест выше гоняет в края `i64` только `price_ticks`/`qty_lots` —
/// `exch_ts_ns`/`local_ts_ns` там остаются маленькими. У времени своя
/// дельта (от эпохи кадра, а не от предыдущей записи, см. доку модуля),
/// и `local_ts_ns` никак не связан с `exch_ts_ns` (может разъехаться с
/// ним произвольно), так что оба поля отдельно должны пережить край
/// `i64` — именно там, где `exch_ts_ns.wrapping_sub(st.epoch_ns)` /
/// `local_ts_ns.wrapping_sub(st.epoch_ns)` обязаны остаться честной
/// биекцией, а не обычным `-`, который в debug-сборке запаниковал бы.
#[test]
fn timestamp_deltas_survive_epoch_at_i64_extremes() {
    let frame = vec![
        // Эпоха кадра = exch_ts_ns первой записи (`Writer::write_frame`).
        rec(ev_snapshot_bid(), i64::MIN, i64::MAX, 1, 1),
        rec(ev_delta_ask(), i64::MAX, i64::MIN, 2, 2),
        rec(ev_trade_buy(), 0, 0, 3, 3),
    ];
    let bytes = write_all(header(), std::slice::from_ref(&frame));
    let (_, frames) = read_all(&bytes);
    assert_eq!(
        frames[0], frame,
        "exch_ts_ns и local_ts_ns обязаны совпасть побитово даже на краях i64"
    );
}

// -----------------------------------------------------------------
// Требование 4б: кодек обязан правильно трогать не только цену, размер и
// время. В v3 у записи остались `ev`, обе метки, цена, размер и `block`
// (бит `attrs` группы), а группа обязана нести флаги и метки **своего**
// сообщения. `zigzag(0)` и `uvarint(0)` — один и тот же байт 0x00, так что
// перепутанные местами `write_zigzag`/`write_uvarint` на одних нулях дали бы
// зелёный round trip: поэтому здесь есть ненулевые `ev`, обе метки, `block`
// и границы групп внутри одного кадра.
// -----------------------------------------------------------------

/// Та же запись, но блочная — единственное поле v3, которого нет у `rec()`.
pub(super) fn rec_block(
    ev: u64,
    exch_ts_ns: i64,
    local_ts_ns: i64,
    price_ticks: i64,
    qty_lots: i64,
) -> Record {
    Record {
        block: true,
        ..rec(ev, exch_ts_ns, local_ts_ns, price_ticks, qty_lots)
    }
}

#[test]
fn groups_keep_their_own_flags_stamps_and_block_bit() {
    // Три группы в одном кадре: уровни одного сообщения книги (три записи с
    // общими метками), однозаписная группа-сделка с `block = true`, и уровень
    // другого сообщения — с другой меткой приёма.
    let frame = vec![
        rec(ev_snapshot_bid(), 2_000, 2_050, 100, 5),
        rec(ev_snapshot_bid(), 2_000, 2_050, 99, 3),
        rec(ev_snapshot_bid(), 2_000, 2_050, 98, 1),
        rec_block(ev_trade_buy(), 2_100, 2_150, 101, 7),
        rec(ev_delta_ask(), 2_100, 2_150, 102, 4),
    ];
    let bytes = write_all(header(), std::slice::from_ref(&frame));
    let (_, frames) = read_all(&bytes);
    assert_eq!(
        frames[0], frame,
        "флаги, обе метки, `block` и дельты цены/размера обязаны совпасть"
    );
}

#[test]
fn records_of_one_message_share_the_group_stamps() {
    // Одно сообщение биржи — один `local_ts` на все его уровни: в этом и
    // смысл переноса метки приёма из записи в заголовок группы, и метка
    // обязана восстанавливаться у каждой записи группы, а не только у первой.
    let frame = vec![
        rec(ev_snapshot_bid(), 10, 20, 5, 1),
        rec(ev_snapshot_bid(), 10, 20, 6, 2),
    ];
    let bytes = write_all(header(), std::slice::from_ref(&frame));
    let (_, frames) = read_all(&bytes);
    for r in &frames[0] {
        assert_eq!((r.exch_ts_ns, r.local_ts_ns), (10, 20));
    }
}

/// Собирает тело кадра v3 из готовой группы: эпоха плюс байты группы как есть.
pub(super) fn v3_payload_of(group: &[u8]) -> Vec<u8> {
    let mut payload = Vec::new();
    payload.extend_from_slice(&0i64.to_le_bytes());
    payload.extend_from_slice(group);
    payload
}

#[test]
fn unknown_attrs_bit_is_corruption_not_a_silent_ignore() {
    // ev=1, exch=0, local=0, attrs=0x04 (бит 2 — неизвестный: бит 0 занят
    // блочностью, бит 1 — RPI), count=1, цена, размер.
    let payload = v3_payload_of(&[1, 0, 0, 0x04, 1, 1, 1]);
    let err = decode_frame_payload_v3(&payload)
        .expect_err("неизвестный бит `attrs` обязан быть ошибкой, а не тишиной");
    assert!(matches!(err, BinlogError::Corrupt(_)), "{err:?}");
}

/// Флаг RPI лежит в `attrs` **группы**, но обязан остаться поштучным: смена
/// флага рвёт группу (иначе две сделки одного сообщения с разными `RPI`
/// слились бы в одну и флаг потерялся бы), а round-trip возвращает значения,
/// записанные на диск.
#[test]
fn rpi_flag_survives_round_trip_and_splits_the_group() {
    let ts = 1_700_000_000_000_000_000;
    let plain = rec(ev_trade_buy(), ts, ts + 1, 100, 5);
    let mut rpi = rec(ev_trade_buy(), ts, ts + 1, 101, 7);
    rpi.rpi = true;

    assert!(
        !same_message(&plain, &rpi),
        "смена RPI обязана начинать новую группу: иначе флаг не поштучный"
    );
    assert!(
        same_message(&plain, &plain),
        "иначе тест не проверяет границу: одинаковые записи — одна группа"
    );

    let bytes = write_all(header(), &[vec![plain, rpi]]);
    let (_, frames) = read_all(&bytes);
    assert_eq!(frames.len(), 1, "один кадр — одна группа на каждую запись");
    assert_eq!(
        frames[0],
        vec![plain, rpi],
        "значения и порядок — как на диске"
    );
    assert!(!frames[0][0].rpi);
    assert!(frames[0][1].rpi);
}

/// Файл, записанный до появления флага (бит 1 `attrs` нулевой), читается как
/// «не размечено», а не как ошибка раскладки: обратная совместимость по
/// чтению — часть контракта, а не побочный эффект.
#[test]
fn file_written_before_the_rpi_bit_reads_as_not_flagged() {
    let ts = 1_700_000_000_000_000_000;
    let plain = rec(ev_trade_buy(), ts, ts + 1, 100, 5);
    let bytes = write_all(header(), &[vec![plain, plain]]);
    let (_, frames) = read_all(&bytes);
    assert_eq!(frames[0].len(), 2);
    assert!(
        frames[0].iter().all(|r| !r.rpi),
        "записи без бита RPI обязаны читаться как `false`"
    );
}

#[test]
fn group_count_larger_than_the_frame_is_corruption_not_a_loop() {
    // `count` = 127 при двух оставшихся байтах: отказ обязан случиться **до**
    // чтения записей, иначе счётчик с диска крутил бы цикл и аллоцировал
    // записи, которых в кадре нет.
    let payload = v3_payload_of(&[1, 0, 0, 0, 0x7f, 0, 0]);
    let err =
        decode_frame_payload_v3(&payload).expect_err("группа длиннее кадра обязана быть ошибкой");
    assert!(matches!(err, BinlogError::Corrupt(_)), "{err:?}");
}

#[test]
fn frame_shorter_than_the_epoch_is_corruption() {
    let err = decode_frame_payload_v3(&[0u8; FRAME_EPOCH_LEN - 1])
        .expect_err("кадр короче эпохи обязан быть ошибкой");
    assert!(matches!(err, BinlogError::Corrupt(_)), "{err:?}");
}

/// Вариант «`ev` таблицей на кадр» (замер M1e тикета 44, в формат не входит):
/// round trip обязан быть побитовым, а значения сверх потолка таблицы — уходить
/// экранированными, а не теряться.
#[test]
fn ev_table_variant_round_trips_and_escapes_beyond_the_table() {
    let mut frame = vec![
        rec(0x5000_0001, 10, 10, 100, 5),
        rec(0x5000_0001, 10, 10, 99, 3),
        rec(0x6000_0001, 11, 11, 101, 2),
        rec_block(0x5000_0002, 12, 12, 102, 1),
    ];
    // Больше `EV_TABLE_MAX` различных значений: хвост обязан уехать
    // экранированным, иначе такой поток молча потерял бы флаги.
    for i in 0..(EV_TABLE_MAX as u64 + 5) {
        frame.push(rec(0x7000_0000 + i, 100 + i as i64, 100 + i as i64, 100, 1));
    }
    let mut raw = Vec::new();
    encode_frame_payload_v3_ev_table(&frame, &mut raw);
    let back = decode_frame_payload_v3_ev_table(&raw)
        .expect("вариант «ev таблицей» обязан читаться обратно");
    assert_eq!(back, frame, "round-trip варианта — побитовый");
}

// -----------------------------------------------------------------
// Varint/зигзаг — сами примитивы, границы диапазона.
// -----------------------------------------------------------------

#[test]
fn uvarint_round_trips_boundary_values() {
    for v in [0u64, 1, 127, 128, 300, u32::MAX as u64, u64::MAX] {
        let mut buf = Vec::new();
        write_uvarint(&mut buf, v);
        let mut pos = 0;
        assert_eq!(read_uvarint(&buf, &mut pos).unwrap(), v);
        assert_eq!(pos, buf.len());
    }
}

#[test]
fn zigzag_round_trips_boundary_values() {
    for v in [0i64, 1, -1, 63, -64, i64::MAX, i64::MIN] {
        let mut buf = Vec::new();
        write_zigzag(&mut buf, v);
        let mut pos = 0;
        assert_eq!(read_zigzag(&buf, &mut pos).unwrap(), v);
    }
}

#[test]
fn truncated_varint_is_an_error_not_a_panic() {
    let buf = [0x80u8]; // продолжение обещано, следующего байта нет
    let mut pos = 0;
    assert!(read_uvarint(&buf, &mut pos).is_err());
}
