//! Кодек кадра: разбор и запись полезной нагрузки версий v2 / v3.

use super::*;

// ---------------------------------------------------------------------
// Кодек кадра: v3 (текущий, пишется) и v2 (только чтение живых файлов).
// ---------------------------------------------------------------------

/// Длина префикса эпохи кадра — `i64`-метка времени первой записи
/// (`Writer::write_frame`), которой мерятся дельты времени внутри кадра.
pub(super) const FRAME_EPOCH_LEN: usize = 8;

/// Бит 0 `attrs` группы — блочная сделка (`BT` у Bybit, в v2 `ival != 0`).
/// Остальные биты обязаны быть нулевыми: неизвестный бит означает раскладку,
/// которой этот читатель не знает, и молча его проглотить значило бы прочитать
/// чужие байты как свои (та же дисциплина, что у версии в заголовке).
pub(super) const ATTRS_BLOCK: u64 = 1 << 0;

/// Бит 1 `attrs` группы — RPI-сделка (`RPI` у Bybit, 2026-09-16): исполнена об
/// невидимую заявку маркет-мейкера, видимую ликвидность уровня не потребляет.
/// Группа рвётся по смене флага (`same_message`), поэтому флаг остаётся
/// поштучным, хотя живёт в заголовке группы.
pub(super) const ATTRS_RPI: u64 = 1 << 1;

/// Все биты `attrs`, которые знает эта версия читателя.
pub(super) const ATTRS_KNOWN: u64 = ATTRS_BLOCK | ATTRS_RPI;

/// `attrs` группы из флагов записи.
pub(super) fn attrs_of(r: &Record) -> u64 {
    u64::from(r.block) | (u64::from(r.rpi) << 1)
}

/// Полевой бюджет кадра — сколько байт **сырого** тела пришлось на каждое
/// поле. В формат не входит: копит кодировщик, читает замер
/// (`lob binlog-stats --reencode`), чтобы вопрос «за что платятся байты» имел
/// числовой ответ, а не оценку на глаз (`docs/findings/binlog-format-2026-09-13.md`,
/// «Чего этот замер не говорит»).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct FieldBytes {
    /// Эпоха кадра — одна на кадр, в обеих версиях.
    pub epoch: usize,
    pub ev: usize,
    /// `exch_ts`/`local_ts`: в v3 — на группу (сообщение), в v2 — на запись.
    pub exch_ts: usize,
    pub local_ts: usize,
    pub price: usize,
    pub qty: usize,
    /// `attrs` и `count` — заголовок группы, не привязанный к отдельной записи
    /// (в v3).
    pub group_overhead: usize,
    /// Только v2: байты `order_id`, `ival`, `fval` — тех полей, которых в v3
    /// нет.
    pub dead_fields: usize,
}

impl FieldBytes {
    /// Складывает бюджеты: замер идёт по всем кадрам файла, а кодировщик
    /// считает кадр за кадром.
    pub fn add(&mut self, other: Self) {
        self.epoch += other.epoch;
        self.ev += other.ev;
        self.exch_ts += other.exch_ts;
        self.local_ts += other.local_ts;
        self.price += other.price;
        self.qty += other.qty;
        self.group_overhead += other.group_overhead;
        self.dead_fields += other.dead_fields;
    }
}

/// Состояние дельты, которое живёт внутри одного кадра и обнуляется на
/// каждом новом (см. доку модуля: кадры читаются независимо друг от друга).
/// Эпоха кадра здесь не хранится: её знает и кодировщик, и декодер, а метки
/// считаются от неё напрямую — держать её ещё и тут значило бы заводить
/// вторую копию одного значения без нужды.
pub(super) struct DeltaState {
    pub(super) prev_price_ticks: i64,
    pub(super) prev_qty_lots: i64,
}

impl DeltaState {
    pub(super) fn new() -> Self {
        Self {
            prev_price_ticks: 0,
            prev_qty_lots: 0,
        }
    }
}

/// Сколько байт записал один вызов кодека: `buf.len()` до и после.
pub(super) fn took(buf: &[u8], before: usize) -> usize {
    buf.len() - before
}

/// Пишет одну запись v3: цена и размер — дельты от предыдущей записи кадра.
/// `indexed` — только для симуляции M1i (`PriceMode::IndexSimulated`): вместо
/// дельты цены один байт, как у настоящего индекса уровня.
pub(super) fn encode_entry_v3(
    buf: &mut Vec<u8>,
    r: &Record,
    st: &mut DeltaState,
    fb: &mut FieldBytes,
    indexed: bool,
) {
    if indexed {
        // Индекс уровня стороны: у книги ≤ 50 уровней на сторону, значит
        // индекс < 128 и uvarint занимает ровно один байт. Значение — младшие
        // биты цены, а не ноль: у замены должна быть та же длина и
        // сопоставимая энтропия, иначе zstd сжал бы одинаковые нули в ничто и
        // завысил бы экономию варианта.
        buf.push((r.price_ticks as u64 & 0x7f) as u8);
        fb.price += 1;
    } else {
        let before = buf.len();
        write_zigzag(buf, r.price_ticks.wrapping_sub(st.prev_price_ticks));
        fb.price += took(buf, before);
    }
    let before = buf.len();
    write_zigzag(buf, r.qty_lots.wrapping_sub(st.prev_qty_lots));
    fb.qty += took(buf, before);
    st.prev_price_ticks = r.price_ticks;
    st.prev_qty_lots = r.qty_lots;
}

/// Заголовок группы: флаги события (или код таблицы — `EvMode`), метка биржи,
/// метка приёма (одна на сообщение биржи), `attrs` и число записей.
pub(super) fn encode_group_header(
    buf: &mut Vec<u8>,
    r: &Record,
    epoch_ns: i64,
    count: usize,
    ev_mode: &EvMode<'_>,
    fb: &mut FieldBytes,
) {
    let before = buf.len();
    match ev_mode {
        // Код группы: индекс в таблице кадра, а для значения, не попавшего в
        // таблицу (различных значений больше `EV_TABLE_MAX`), — `ev_count` как
        // escape и полный `ev` следом: молча терять значение нельзя.
        EvMode::Table(table) => match table.iter().position(|&e| e == r.ev) {
            Some(code) => write_uvarint(buf, code as u64),
            None => {
                write_uvarint(buf, table.len() as u64);
                write_uvarint(buf, r.ev);
            }
        },
        EvMode::Inline => write_uvarint(buf, r.ev),
    }
    fb.ev += took(buf, before);
    // `wrapping_sub`, не `-`: обоснование — в доке модуля. Метки времени —
    // дельты от эпохи кадра (Decision 23), а не от предыдущей записи: эпоха
    // одна на кадр, и метка сообщения одна на все его уровни.
    let before = buf.len();
    write_zigzag(buf, r.exch_ts_ns.wrapping_sub(epoch_ns));
    fb.exch_ts += took(buf, before);
    let before = buf.len();
    write_zigzag(buf, r.local_ts_ns.wrapping_sub(epoch_ns));
    fb.local_ts += took(buf, before);
    let before = buf.len();
    write_uvarint(buf, attrs_of(r));
    write_uvarint(buf, count as u64);
    fb.group_overhead += took(buf, before);
}

/// Одна ли это группа: у сообщения биржи флаги, обе метки и блочность одни и
/// те же на все его записи. Смена любого из пяти — начало следующего
/// сообщения. Функция публичная, потому что по этой же границе считает группы
/// замер (`lob binlog-stats`): граница групп — часть формата, и второй её
/// редакции в командах быть не должно.
pub fn same_message(a: &Record, b: &Record) -> bool {
    a.ev == b.ev
        && a.exch_ts_ns == b.exch_ts_ns
        && a.local_ts_ns == b.local_ts_ns
        && a.block == b.block
        && a.rpi == b.rpi
}

/// Как кодировать цену — параметр **замера**, в формат входит только `Delta`.
pub(super) enum PriceMode<'a> {
    /// Дельта от предыдущей записи кадра — формат v3.
    Delta,
    /// Симуляция «уровень индексом» для M1i тикета 43. В формат не входит и
    /// писателем не используется: чтобы индекс уровня стал настоящим, читателю
    /// нужна книга инструмента, а это ломает «кадр читается независимо»
    /// (Decision 7) и вдобавок требует хранить `u` — последовательность
    /// обновлений, без которой `Book::apply` не работает, а в логе её нет.
    IndexSimulated(&'a [bool]),
}

/// Как кодировать флаги `ev` — параметр **замера** (M1e тикета 44). В формат
/// входит только `Inline`.
pub(super) enum EvMode<'a> {
    /// `ev` в заголовке каждой группы — формат v3.
    Inline,
    /// Таблица `ev` на кадр, в группе — код. Различных значений в живых файлах
    /// 4–6 при пятибайтовом варинте, поэтому таблица (~21 байт на кадр) может
    /// оказаться дешевле, чем полный `ev` в каждой группе; решает замер, а не
    /// очевидность.
    Table(&'a [u64]),
}

/// Максимум значений `ev` в таблице кадра варианта M1e. Больше — значение
/// уходит в группу экранированным (код `ev_count` плюс полный `ev`), поэтому
/// вариант не ломается на незнакомом потоке, а не теряет значение молча.
pub(super) const EV_TABLE_MAX: usize = 32;

/// Тело кадра v3: эпоха, затем группы сообщений до конца среза. Число групп
/// нигде не хранится отдельно — конец среза и есть конец кадра; `count`
/// группы считается просмотром вперёд по срезу.
pub(super) fn encode_frame_payload(
    records: &[Record],
    mode: PriceMode<'_>,
    ev_mode: EvMode<'_>,
    out: &mut Vec<u8>,
) -> FieldBytes {
    let mut fb = FieldBytes::default();
    let Some(first) = records.first() else {
        return fb;
    };
    // Эпоха кадра — метка первой записи (см. доку модуля): не отдельный
    // параметр звонка, чтобы вызывающему не приходилось поддерживать вторую
    // копию того же значения.
    let epoch_ns = first.exch_ts_ns;
    out.extend_from_slice(&epoch_ns.to_le_bytes());
    fb.epoch += FRAME_EPOCH_LEN;

    if let EvMode::Table(table) = &ev_mode {
        let before = out.len();
        write_uvarint(out, table.len() as u64);
        for ev in *table {
            write_uvarint(out, *ev);
        }
        fb.ev += took(out, before);
    }

    let mut st = DeltaState::new();
    let mut i = 0;
    while let Some(head) = records.get(i) {
        let mut end = i + 1;
        while records.get(end).is_some_and(|r| same_message(head, r)) {
            end += 1;
        }
        encode_group_header(out, head, epoch_ns, end - i, &ev_mode, &mut fb);
        for (k, r) in records[i..end].iter().enumerate() {
            let indexed = matches!(mode, PriceMode::IndexSimulated(m) if m.get(i + k).copied().unwrap_or(false));
            encode_entry_v3(out, r, &mut st, &mut fb, indexed);
        }
        i = end;
    }
    fb
}

/// Тело кадра v3 — то, что пишет `Writer::write_frame`.
pub fn encode_frame_payload_v3(records: &[Record], out: &mut Vec<u8>) -> FieldBytes {
    encode_frame_payload(records, PriceMode::Delta, EvMode::Inline, out)
}

// Варианты `PriceMode::IndexSimulated`/`EvMode::Table` (M1i/M1e тикетов
// 43/44) и перекодировщик v2 живут в `experiments` (ремонт W3, ревью 23.09):
// `encode_frame_payload_v3_index_simulated`, `encode_frame_payload_v3_ev_table`,
// `encode_frame_payload_v2` — эти три и парный им `decode_frame_payload_v3_ev_table`
// ниже. Ни один формат-кадр их не использует — только замер `lob binlog-stats`
// (в т.ч. `--reencode`), и pub API этого модуля (реального писателя/читателя)
// им нести незачем; `experiments` — дочерний модуль, поэтому видит
// `encode_frame_payload`/`PriceMode`/`EvMode`/`EV_TABLE_MAX` этого файла как есть.

/// Сколько записей v2-файла несли ненулевое значение в поле, которого в v3
/// больше нет. Ненулевое `ival` — это и есть блочность (в `Record` такие
/// записи приходят с `block: true`), но считается она здесь по исходной форме:
/// счётчики отвечают на вопрос про **байты формата**, а не про смысл поля.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct LegacyDeadFields {
    pub nonzero_order_id: u64,
    pub nonzero_ival: u64,
    pub nonzero_fval: u64,
}

/// Читает эпоху кадра — общий префикс обеих версий.
pub(super) fn read_frame_epoch(payload: &[u8]) -> Result<i64, BinlogError> {
    let raw: [u8; FRAME_EPOCH_LEN] = payload
        .get(..FRAME_EPOCH_LEN)
        .and_then(|s| s.try_into().ok())
        .ok_or_else(|| {
            BinlogError::Corrupt(format!("кадр короче эпохи ({FRAME_EPOCH_LEN} байт)"))
        })?;
    Ok(i64::from_le_bytes(raw))
}

/// Разбирает уже разжатое тело кадра v3: эпоха, затем группы сообщений до
/// конца среза. Число записей группы проверяется против остатка кадра **до**
/// чтения: каждая запись — минимум два байта, поэтому группа, объявившая
/// записей больше, чем в кадре осталось байт, — порча, а не повод крутить
/// цикл по счётчику, который пришёл с диска.
pub fn decode_frame_payload_v3(payload: &[u8]) -> Result<Vec<Record>, BinlogError> {
    let mut out = Vec::new();
    decode_frame_payload_v3_into(payload, &mut out)?;
    Ok(out)
}

/// `decode_frame_payload_v3` в буфер вызывающего (дописывает в конец `out`).
pub fn decode_frame_payload_v3_into(
    payload: &[u8],
    out: &mut Vec<Record>,
) -> Result<(), BinlogError> {
    let epoch_ns = read_frame_epoch(payload)?;
    let mut st = DeltaState::new();
    let mut pos = FRAME_EPOCH_LEN;
    while pos < payload.len() {
        let ev = read_uvarint(payload, &mut pos)?;
        let exch_delta = read_zigzag(payload, &mut pos)?;
        let local_delta = read_zigzag(payload, &mut pos)?;
        let attrs = read_uvarint(payload, &mut pos)?;
        if attrs & !ATTRS_KNOWN != 0 {
            return Err(BinlogError::Corrupt(format!(
                "неизвестный бит attrs группы: {attrs:#x}"
            )));
        }
        let count = read_uvarint(payload, &mut pos)?;
        let remaining = (payload.len() - pos) as u64;
        if count > remaining {
            return Err(BinlogError::Corrupt(format!(
                "группа объявила {count} записей, а в кадре осталось {remaining} байт"
            )));
        }
        let exch_ts_ns = epoch_ns.wrapping_add(exch_delta);
        let local_ts_ns = epoch_ns.wrapping_add(local_delta);
        let block = attrs & ATTRS_BLOCK != 0;
        let rpi = attrs & ATTRS_RPI != 0;
        for _ in 0..count {
            let price_delta = read_zigzag(payload, &mut pos)?;
            let qty_delta = read_zigzag(payload, &mut pos)?;
            let price_ticks = st.prev_price_ticks.wrapping_add(price_delta);
            let qty_lots = st.prev_qty_lots.wrapping_add(qty_delta);
            st.prev_price_ticks = price_ticks;
            st.prev_qty_lots = qty_lots;
            out.push(Record {
                ev,
                exch_ts_ns,
                local_ts_ns,
                price_ticks,
                qty_lots,
                block,
                rpi,
            });
        }
    }
    Ok(())
}

// `decode_frame_payload_v3_ev_table` (парный читатель варианта замера выше)
// — тоже в `experiments`, тем же обоснованием.

/// Разбирает тело кадра v2 — форму, которую пишет живой коллектор до
/// перезапуска. `order_id`/`ival`/`fval` читаются, потому что лежат в потоке,
/// но в `Record` их больше нет: `ival` становится `block`, а `order_id`/`fval`
/// только считаются в `dead` — эти счётчики и есть воспроизводимое на любом
/// старом файле доказательство «0 % ненулевых».
pub(super) fn decode_frame_payload_v2(
    payload: &[u8],
    dead: &mut LegacyDeadFields,
) -> Result<Vec<Record>, BinlogError> {
    let epoch_ns = read_frame_epoch(payload)?;
    let mut st = DeltaState::new();
    let mut pos = FRAME_EPOCH_LEN;
    let mut out = Vec::new();
    while pos < payload.len() {
        let ev = read_uvarint(payload, &mut pos)?;
        let exch_delta = read_zigzag(payload, &mut pos)?;
        let local_delta = read_zigzag(payload, &mut pos)?;
        let price_delta = read_zigzag(payload, &mut pos)?;
        let qty_delta = read_zigzag(payload, &mut pos)?;
        let order_id = read_uvarint(payload, &mut pos)?;
        let ival = read_zigzag(payload, &mut pos)?;
        let fval_bits = read_uvarint(payload, &mut pos)?;
        if order_id != 0 {
            dead.nonzero_order_id += 1;
        }
        if ival != 0 {
            dead.nonzero_ival += 1;
        }
        if fval_bits != 0 {
            dead.nonzero_fval += 1;
        }
        let price_ticks = st.prev_price_ticks.wrapping_add(price_delta);
        let qty_lots = st.prev_qty_lots.wrapping_add(qty_delta);
        st.prev_price_ticks = price_ticks;
        st.prev_qty_lots = qty_lots;
        out.push(Record {
            ev,
            exch_ts_ns: epoch_ns.wrapping_add(exch_delta),
            local_ts_ns: epoch_ns.wrapping_add(local_delta),
            price_ticks,
            qty_lots,
            block: ival != 0,
            // v2 поля RPI не несёт: файлы той версии — «не размечено».
            rpi: false,
        });
    }
    Ok(out)
}
