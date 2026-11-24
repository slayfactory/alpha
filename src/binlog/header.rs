//! Формат файла: заголовок, расписание, запись и ошибки журнала.

use super::*;

/// Магия формата. Проверяется при открытии, чтобы чужой или пустой файл
/// не читался молча как валидный лог с нулевым содержимым.
pub const MAGIC: [u8; 4] = *b"ABLG";

/// Версия формата, которую **пишет** этот код, — байт в заголовке (раздел
/// «Contracts touched» `PLAN.md`: «формат бинарного лога версионируется
/// байтом в заголовке»). Меняется при любой несовместимой правке раскладки,
/// а не при добавлении новых значений существующих полей.
///
/// `3`, не `2`: тело кадра стало списком групп (сообщений биржи), у записи
/// больше нет `order_id`/`ival`/`fval`, а `local_ts` переехал из записи в
/// заголовок группы. Старый читатель такого тела не ждёт — несовместимая
/// правка раскладки обязана поднять версию, иначе файл версии 3 читался бы
/// байт в байт как версия 2 и первая же группа ушла бы не туда (см. тесты
/// `old_version_file_is_rejected_not_misread` и
/// `version_two_file_is_read_as_legacy_not_misread`).
pub const VERSION: u8 = 3;

/// Версия 2 — та, которую пишет **живой коллектор** до перезапуска на новый
/// бинарник (В-41: процесс живёт с копии `data/always-on/alpha-collector.exe`
/// и в этой правке не трогается). Читатель обязан знать обе версии, иначе
/// вся идущая запись перестала бы читаться; писателя версии 2 в коде нет —
/// её форму держит только legacy-декодер `decode_frame_payload_v2`.
pub const VERSION_V2: u8 = 2;

/// Версия 4: заголовок v3 плюс расписание шагов суток (`StepAt`). Пишется
/// только `Writer::create_with_schedule`; тело кадра то же, что у v3.
pub const VERSION_V4: u8 = 4;

/// magic(4) + version(1) — этого достаточно, чтобы решить, версия ли это,
/// которую понимает остальной код. Читается отдельно от хвоста заголовка
/// (`HEADER_TAIL_LEN`) и до него: у более старой версии хвост другой длины
/// и другого смысла, и разбирать его тем же способом значило бы читать
/// чужие байты как свои вместо понятной ошибки версии.
pub(super) const MAGIC_VERSION_LEN: usize = 4 + 1;

/// tick_e9(8) + step_e9(8) + max_records_per_frame(4) — хвост заголовка
/// ровно текущей версии.
pub(super) const HEADER_TAIL_LEN: usize = 8 + 8 + 4;

/// Полная длина заголовка текущей версии.
pub(super) const HEADER_LEN: usize = MAGIC_VERSION_LEN + HEADER_TAIL_LEN;

/// Число записей расписания v4 — `u16 LE` сразу за общей частью заголовка.
pub(super) const SCHEDULE_COUNT_LEN: usize = 2;

/// Запись расписания v4: `ts_ns`, `tick_e9`, `step_e9` — по восемь байт.
pub(super) const STEP_AT_LEN: usize = 3 * 8;

/// Длина префикса кадра — `u32`, как назначено Decision 7. Публичная, потому
/// что замер формата (`lob binlog-stats --reencode`) собирает кадры в памяти и
/// обязан считать их длину той же формулой, а не второй копией числа 4.
pub const LEN_PREFIX: usize = 4;

/// Заголовок суточного файла: шаг цены и шаг количества, в единицах 1e-9
/// (тот же масштаб, что `book::Book` и разбор `bybit::ws` уже используют) —
/// значения одни и те же по всему конвейеру, а не два независимых масштаба,
/// которые могли бы разойтись при переносе константы.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Header {
    pub tick_e9: i64,
    pub step_e9: i64,
    /// Максимум записей в одном кадре (Decision 23, ревизия 10). Число
    /// выбирает и хранит в заголовке вызывающий (рекордер, шаг 0.3) —
    /// этот модуль не назначает ему значение по умолчанию: «потолок
    /// берётся из файла, а не назначается», иначе это было бы изобретённым
    /// числом. `Writer::write_frame` отказывается писать кадр длиннее
    /// этого значения; `Reader` использует его, чтобы вычислить потолок
    /// разжатого тела кадра (`max_frame_payload_bytes`) и не дать
    /// bounded zstd API выделить память сверх него.
    pub max_records_per_frame: u32,
}

pub(super) fn validate_header(h: Header) -> Result<(), BinlogError> {
    // Не `assert!`, как в `Book::new`: там аргументы приходят из кода,
    // здесь заголовок может прийти прямо с диска (`Reader::open`), и файл —
    // недоверенный ввод. Панике здесь взяться неоткуда ни при записи, ни
    // при чтении: обе стороны проверяются одной и той же функцией.
    if h.tick_e9 <= 0 || h.step_e9 <= 0 || h.max_records_per_frame == 0 {
        return Err(BinlogError::InvalidHeader {
            tick_e9: h.tick_e9,
            step_e9: h.step_e9,
            max_records_per_frame: h.max_records_per_frame,
        });
    }
    Ok(())
}

/// Шаг цены и количества, действующий с момента `ts_ns` (наносекунды Unix) до
/// следующей записи расписания, в тех же единицах 1e-9, что и `Header`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StepAt {
    pub ts_ns: i64,
    pub tick_e9: i64,
    pub step_e9: i64,
}

/// Расписание v4: от двух записей, метки строго по возрастанию, каждый шаг
/// положителен и кратен шагу заголовка (мельчайшему шагу суток). `checked_rem`,
/// не `%`: заголовок и расписание приходят с диска, и деление на ноль или
/// `i64::MIN % -1` не вправе паниковать.
pub(super) fn validate_schedule(h: &Header, schedule: &[StepAt]) -> Result<(), BinlogError> {
    let bad = |index: usize, reason: &'static str| BinlogError::InvalidSchedule { index, reason };
    if schedule.len() < 2 {
        return Err(bad(0, "меньше двух записей"));
    }
    if schedule.len() > usize::from(u16::MAX) {
        return Err(bad(0, "больше 65535 записей"));
    }
    let mut prev_ts: Option<i64> = None;
    for (i, a) in schedule.iter().enumerate() {
        if a.tick_e9 <= 0 || a.step_e9 <= 0 {
            return Err(bad(i, "шаг не положителен"));
        }
        if a.tick_e9.checked_rem(h.tick_e9) != Some(0)
            || a.step_e9.checked_rem(h.step_e9) != Some(0)
        {
            return Err(bad(i, "шаг не кратен шагу заголовка"));
        }
        if prev_ts.is_some_and(|p| p >= a.ts_ns) {
            return Err(bad(i, "метки времени не по возрастанию"));
        }
        prev_ts = Some(a.ts_ns);
    }
    Ok(())
}

/// Заголовок суток как байты: v3 при пустом расписании (раскладка и байты те же,
/// что до v4), иначе v4. Расписание обязано быть уже проверено
/// (`validate_schedule`); общая точка для `Writer` и контейнера архива.
pub(super) fn header_bytes(header: &Header, schedule: &[StepAt]) -> Vec<u8> {
    let mut buf = Vec::with_capacity(
        HEADER_LEN
            + if schedule.is_empty() {
                0
            } else {
                SCHEDULE_COUNT_LEN + schedule.len() * STEP_AT_LEN
            },
    );
    buf.extend_from_slice(&MAGIC);
    buf.push(if schedule.is_empty() {
        VERSION
    } else {
        VERSION_V4
    });
    buf.extend_from_slice(&header.tick_e9.to_le_bytes());
    buf.extend_from_slice(&header.step_e9.to_le_bytes());
    buf.extend_from_slice(&header.max_records_per_frame.to_le_bytes());
    if !schedule.is_empty() {
        buf.extend_from_slice(
            &u16::try_from(schedule.len())
                .unwrap_or(u16::MAX)
                .to_le_bytes(),
        );
        for s in schedule {
            buf.extend_from_slice(&s.ts_ns.to_le_bytes());
            buf.extend_from_slice(&s.tick_e9.to_le_bytes());
            buf.extend_from_slice(&s.step_e9.to_le_bytes());
        }
    }
    buf
}

/// Одна запись — сырое событие внутри группы (сообщения биржи). Поля, которые
/// есть у `Event` крейта `hftbacktest`, но информации в живых данных не несут
/// (`order_id`, `fval` — 0 % ненулевых на 17 млн записей, замер
/// `docs/findings/binlog-format-2026-09-13.md`), у `Record` нет вовсе: экспортёр
/// ставит на их место `0`/`0.0` (поток L2 Bybit числового id заявки не
/// сообщает). `ival` заменён на `block` — семантику блочной сделки, ради
/// которой он и отличался от нуля (`lob/levels.rs` такие сделки пропускает).
///
/// `Eq`, не только `PartialEq` (в v2 здесь был `f64` `fval`, и `NaN != NaN`
/// делал `Eq` невозможным): после переноса мёртвых полей вон в записи не
/// осталось ни одного нецелого поля, и сравнение записей стало полным.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Record {
    /// Флаги события — те же биты, что `hftbacktest::types::{DEPTH_EVENT,
    /// TRADE_EVENT, BUY_EVENT, ...}`. Модуль их не толкует, только хранит:
    /// смысл бит — дело пишущего (рекордер, шаг 0.3), не формата хранения.
    pub ev: u64,
    /// Наносекунды от эпохи Unix — тот же масштаб, что `local_ts_ns`
    /// в `bybit::conn::ConnEvent` (H12): `exch_ts` биржи приходит в мс
    /// (`cts`/`T`) и переводится в нс на той же границе, что и сравнение
    /// `exch_ts < local_ts`, а не заново здесь другим способом.
    ///
    /// В формате v3 обе метки хранятся **один раз на группу**: внутри
    /// сообщения биржи они одни и те же у всех его записей (sink передаёт
    /// одну метку приёма на сообщение), поэтому перенос не теряет ничего, а
    /// экономит вторую метку в каждой записи.
    pub exch_ts_ns: i64,
    pub local_ts_ns: i64,
    /// Цена как целое число тиков (A1). Тик восстанавливается из `tick_e9`
    /// заголовка: `price_e9 = price_ticks * tick_e9`.
    pub price_ticks: i64,
    /// Размер как целое число шагов количества (A1), аналогично `step_e9`.
    pub qty_lots: i64,
    /// Блочная сделка (`BT` у Bybit, в v2 — `ival != 0`): такие сделки не
    /// потребляют видимую ликвидность (Decision 5), разметка их пропускает
    /// (`lob/levels.rs`). В формате v3 — бит 0 `attrs` группы.
    pub block: bool,
    /// Сделка об RPI-заявку (`RPI` у Bybit). В формате v3 — бит 1 `attrs`
    /// группы; файлы до 2026-09-16 читаются как `false` («не размечено»).
    pub rpi: bool,
}

/// Ошибка формата. `Io`/`Corrupt` несут текст, а не исходный `io::Error`:
/// он не реализует `PartialEq`, а структурные варианты ниже сравниваются
/// в тестах через `assert_eq!`, как `ApplyError` в `book/mod.rs`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BinlogError {
    /// Файла не хватило даже на заголовок. Пустой файл — частный случай
    /// с `got: 0`.
    TruncatedHeader {
        got: usize,
        want: usize,
    },
    /// Первые четыре байта — не `MAGIC`: не наш формат, а не сутки с нулём
    /// кадров.
    BadMagic {
        got: [u8; 4],
    },
    UnsupportedVersion {
        got: u8,
    },
    /// `tick_e9`/`step_e9` не положительны, либо `max_records_per_frame`
    /// нулевой — заголовок сфабрикован или повреждён; без этой проверки
    /// дельты в тиках молча приобрели бы нулевой или отрицательный
    /// масштаб, а нулевой потолок сделал бы каждый непустой кадр
    /// отвергнутым независимо от содержимого.
    InvalidHeader {
        tick_e9: i64,
        step_e9: i64,
        max_records_per_frame: u32,
    },
    /// Расписание шагов v4 не прошло проверку (`validate_schedule`): `index` —
    /// запись, на которой оно нарушено (0 — расписание целиком).
    InvalidSchedule {
        index: usize,
        reason: &'static str,
    },
    /// Кадр объявил длину, для которой на диске не хватило байт: это
    /// усечённый хвост, а не повреждённое содержимое, и Decision 7 требует
    /// различать эти два случая — усечение диагностируется без разбора
    /// самого кадра.
    ShortRead {
        context: &'static str,
        want: usize,
        got: usize,
    },
    /// EOF на самом первом кадре: в файле нет даже синтетического снапшота,
    /// который Decision 7 требует от каждых суток. Заголовок-без-кадров —
    /// это отдельная, более узкая поломка, чем «файл кончился между
    /// кадрами», и молчаливый пустой результат здесь замаскировал бы
    /// потерю целых суток.
    MissingSnapshot,
    /// Кадр целиком прочитан, но не разобрался: битый zstd-поток или
    /// оборванный varint внутри уже разжатого содержимого.
    Corrupt(String),
    /// Сжатый кадр не влезает в `u32`-префикс. На практике недостижимо при
    /// разумном размере кадра, но `as u32` тихо обрезал бы длину, а это и
    /// есть silent corruption, которую этот модуль обязан не производить.
    FrameTooLarge {
        len: usize,
    },
    /// Разжатое тело кадра не поместилось бы в потолок, вычисленный из
    /// `max_records_per_frame` заголовка (Decision 23, ревизия 10):
    /// заявленная длина сжатого кадра на диске правдоподобна, но
    /// распаковка потребовала бы больше байт, чем потолок разрешает — либо
    /// кадр честно превышает потолок, либо испорчен так, что разжимаемый
    /// объём не сходится. Оба случая читаются здесь одинаково: bounded
    /// zstd API (`Reader::read_frame`) отказывается выделять память сверх
    /// `ceiling_bytes`, поэтому различить их без превышения самого потолка
    /// нечем, а превышать его ради диагностики — обходить весь смысл
    /// проверки.
    FrameExceedsHeaderCeiling {
        max_records_per_frame: u32,
        ceiling_bytes: usize,
    },
    Io(String),
}

impl fmt::Display for BinlogError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BinlogError::TruncatedHeader { got, want } => {
                write!(f, "заголовок обрезан: {got} байт из {want}")
            }
            BinlogError::BadMagic { got } => write!(f, "неверная магия формата: {got:02x?}"),
            BinlogError::UnsupportedVersion { got } => {
                write!(f, "неподдерживаемая версия формата: {got}")
            }
            BinlogError::InvalidHeader {
                tick_e9,
                step_e9,
                max_records_per_frame,
            } => write!(
                f,
                "заголовок неисправен: tick_e9={tick_e9}, step_e9={step_e9}, \
                 max_records_per_frame={max_records_per_frame}"
            ),
            BinlogError::InvalidSchedule { index, reason } => {
                write!(f, "расписание шагов неисправно (запись {index}): {reason}")
            }
            BinlogError::ShortRead { context, want, got } => {
                write!(f, "короткое чтение ({context}): {got} байт из {want}")
            }
            BinlogError::MissingSnapshot => {
                write!(
                    f,
                    "в файле нет ни одного кадра — нет синтетического снапшота"
                )
            }
            BinlogError::Corrupt(msg) => write!(f, "повреждён кадр: {msg}"),
            BinlogError::FrameTooLarge { len } => {
                write!(f, "сжатый кадр {len} байт не влезает в u32-префикс")
            }
            BinlogError::FrameExceedsHeaderCeiling {
                max_records_per_frame,
                ceiling_bytes,
            } => write!(
                f,
                "разжатый кадр превысил бы потолок заголовка \
                 (max_records_per_frame={max_records_per_frame}, {ceiling_bytes} байт) \
                 либо испорчен так, что разжимаемый объём не сходится"
            ),
            BinlogError::Io(msg) => write!(f, "ошибка ввода-вывода: {msg}"),
        }
    }
}

impl std::error::Error for BinlogError {}

impl From<io::Error> for BinlogError {
    fn from(e: io::Error) -> Self {
        BinlogError::Io(e.to_string())
    }
}
