//! Чтение хвостов заголовка и расписания из потока.

use super::*;

// ---------------------------------------------------------------------
// Чтение точно `n` байт с различением «чисто EOF» и «оборвано на середине».
// ---------------------------------------------------------------------

pub(super) enum ReadStatus {
    Full,
    Partial(usize),
    Eof,
}

/// Хвост заголовка (`HEADER_TAIL_LEN` байт) из уже опознанного потока:
/// общий код обычного файла и контейнера архива — заголовок суток у них
/// один и тот же (T46). `before` — сколько байт заголовка уже прочитано
/// (0 у обычного файла, `archive::HEADER_LEN` у контейнера), чтобы
/// `TruncatedHeader` называл смещение в распакованном потоке, а не в
/// формате, которого у контейнера снаружи нет.
pub(super) fn read_header_tail<R: Read>(
    body: &mut Body<R>,
    before: usize,
) -> Result<Header, BinlogError> {
    let mut tail = [0u8; HEADER_TAIL_LEN];
    match body.read_upto(&mut tail)? {
        ReadStatus::Full => {}
        ReadStatus::Partial(got) => {
            return Err(BinlogError::TruncatedHeader {
                got: before + MAGIC_VERSION_LEN + got,
                want: before + HEADER_LEN,
            })
        }
        ReadStatus::Eof => {
            return Err(BinlogError::TruncatedHeader {
                got: before + MAGIC_VERSION_LEN,
                want: before + HEADER_LEN,
            })
        }
    }
    let header = Header {
        tick_e9: i64::from_le_bytes(
            tail[0..8]
                .try_into()
                .map_err(|_| BinlogError::Corrupt("tick заголовка не лёг в i64".into()))?,
        ),
        step_e9: i64::from_le_bytes(
            tail[8..16]
                .try_into()
                .map_err(|_| BinlogError::Corrupt("step заголовка не лёг в i64".into()))?,
        ),
        max_records_per_frame: u32::from_le_bytes(
            tail[16..20]
                .try_into()
                .map_err(|_| BinlogError::Corrupt("потолок кадра не лёг в u32".into()))?,
        ),
    };
    validate_header(header)?;
    Ok(header)
}

/// Расписание шагов v4 за общей частью заголовка: `n u16 LE` и `n` записей.
/// `before` — как у `read_header_tail`; `TruncatedHeader` называет смещение в
/// заголовке целиком, включая расписание.
pub(super) fn read_schedule_tail<R: Read>(
    body: &mut Body<R>,
    before: usize,
    header: &Header,
) -> Result<Vec<StepAt>, BinlogError> {
    let base = before + HEADER_LEN;
    let mut count_buf = [0u8; SCHEDULE_COUNT_LEN];
    match body.read_upto(&mut count_buf)? {
        ReadStatus::Full => {}
        ReadStatus::Partial(got) => {
            return Err(BinlogError::TruncatedHeader {
                got: base + got,
                want: base + SCHEDULE_COUNT_LEN,
            })
        }
        ReadStatus::Eof => {
            return Err(BinlogError::TruncatedHeader {
                got: base,
                want: base + SCHEDULE_COUNT_LEN,
            })
        }
    }
    let n = usize::from(u16::from_le_bytes(count_buf));
    let mut raw = vec![0u8; n * STEP_AT_LEN];
    let want = base + SCHEDULE_COUNT_LEN + raw.len();
    match body.read_upto(&mut raw)? {
        ReadStatus::Full => {}
        ReadStatus::Partial(got) => {
            return Err(BinlogError::TruncatedHeader {
                got: base + SCHEDULE_COUNT_LEN + got,
                want,
            })
        }
        ReadStatus::Eof => {
            return Err(BinlogError::TruncatedHeader {
                got: base + SCHEDULE_COUNT_LEN,
                want,
            })
        }
    }
    let field = |chunk: &[u8], at: usize| -> Result<i64, BinlogError> {
        chunk
            .get(at..at + 8)
            .and_then(|s| s.try_into().ok())
            .map(i64::from_le_bytes)
            .ok_or_else(|| BinlogError::Corrupt("запись расписания не легла в i64".into()))
    };
    let mut schedule = Vec::with_capacity(n);
    for chunk in raw.chunks_exact(STEP_AT_LEN) {
        schedule.push(StepAt {
            ts_ns: field(chunk, 0)?,
            tick_e9: field(chunk, 8)?,
            step_e9: field(chunk, 16)?,
        });
    }
    validate_schedule(header, &schedule)?;
    Ok(schedule)
}

/// `Read::read_exact` не годится: при ошибке он не сообщает, сколько байт
/// успел прочитать, а формату нужно различать «ровно ноль байт — конец
/// файла» и «часть кадра есть, но не вся — усечение» (Decision 7). Читает
/// через `read()` в цикле именно ради этого различия.
///
/// Срез `buf[filled..]` доказуемо в границах: условие цикла держит
/// `filled < buf.len()`. Развёртка в `get` невозможна без смены контракта
/// чтения (`Read::read` требует `&mut [u8]`), поэтому заглушка именная,
/// на функцию.
pub(super) fn read_upto<R: Read>(r: &mut R, buf: &mut [u8]) -> io::Result<ReadStatus> {
    if buf.is_empty() {
        return Ok(ReadStatus::Full);
    }
    let mut filled = 0;
    while filled < buf.len() {
        match r.read(&mut buf[filled..]) {
            Ok(0) => break,
            // `.min(buf.len() - filled)` (ремонт W3, ревью 23.09): `Read::
            // read` обязан вернуть `n` не больше длины среза, который ему
            // дали, но контракт — не гарантия компилятора, а обещание
            // реализации. Источники здесь — обычный файл, и (T46) поток
            // `zstd::stream::read::Decoder`, разжимающий архив на лету: у
            // обоих `n` без проверки уже был бы доверенным чужим числом,
            // и нарушивший контракт `Read` увёл бы `filled` за `buf.len()`,
            // а следующая итерация — `&mut buf[filled..]` — запаниковала бы
            // на срезе с началом за концом (Decision 7: усечение обязано
            // быть видно как ошибка, не как паника).
            Ok(n) => filled += n.min(buf.len() - filled),
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
    Ok(if filled == 0 {
        ReadStatus::Eof
    } else if filled == buf.len() {
        ReadStatus::Full
    } else {
        ReadStatus::Partial(filled)
    })
}
