//! Имена файлов суток: суффиксы, разбор даты, порядок и дедупликация.

/// Суффикс обычного суточного файла: `<SYMBOL>-<день>[-pN].binlog`.
pub const BINLOG_SUFFIX: &str = ".binlog";

/// Суффикс контейнера архива (T46): `<SYMBOL>-<день>[-pN].binlog.zst`.
/// Отрезается **раньше** обычного суффикса: `strip_binlog_suffix` снял бы
/// `.binlog` первым и оставил `.zst` в хвосте имени, то есть сутки перестали
/// бы разбираться.
pub const BINLOG_ARCHIVE_SUFFIX: &str = ".binlog.zst";

/// Отрезает суффикс суточного файла (архивный или обычный) — единственное
/// место, где это правило записано. Живёт в `binlog`, а не в `commands`, чтобы
/// им могли пользоваться оба слоя: `commands::lob` (резолверы) и
/// `bybit::verify` (своя копия резолвера — `bybit` не зависит от `commands`,
/// граница слоёв). `None` — не имя суточного файла.
pub fn strip_binlog_suffix(name: &str) -> Option<&str> {
    name.strip_suffix(BINLOG_ARCHIVE_SUFFIX)
        .or_else(|| name.strip_suffix(BINLOG_SUFFIX))
}

/// Имя суточного файла — обычного или контейнера архива. Оба читаются одним
/// `Reader`, поэтому и резолверы обязаны видеть оба (T46).
pub fn is_binlog_file_name(name: &str) -> bool {
    strip_binlog_suffix(name).is_some()
}

/// Разбор календарных суток `YYYY-MM-DD` — общая точка для всех мест, где день приходит строкой
/// с CLI или из CSV: `commands::record::paths::day_index_of_day_str`,
/// `commands::lob::import_archive::run_import_archive`, `commands::lob::replay::is_next_day`,
/// `lob::shortlist::parse_ymd` (W9 ревью 23.09). Живёт в `binlog`, а не в `commands` или `lob`,
/// чтобы обоим слоям было можно — `lob` не имеет пути до `commands` (граница модулей,
/// `ARCHITECTURE.md`). `None` — не разобралось как `YYYY-MM-DD` целиком (включая календарно
/// невозможные дни вроде 30 февраля); вызывающий сам решает, какой ошибкой это обернуть.
/// `bounce_grid/carry.rs` — намеренно отдельная копия (другая дорожка правок).
pub fn parse_calendar_day(day: &str) -> Option<chrono::NaiveDate> {
    chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d").ok()
}

/// Хронологический ключ суточного файла: сутки UTC, затем часть суток
/// (`-p2` после смены шагов). Голая лексикография врёт: `-` (0x2D) меньше
/// `.` (0x2E) в ASCII, и `SOLUSDT-2026-09-08-p2.binlog` как строка встал бы
/// **раньше** `SOLUSDT-2026-09-08.binlog`, то есть хвост суток — раньше их
/// начала, а данные читателям нужны по времени. Имя после префикса символа —
/// либо `день`, либо `день-pN`; всё нераспознанное считается частью 1 того же
/// имени.
///
/// Живёт здесь, а не в `commands`, по той же причине, что и снятие суффикса:
/// правило нужно двум слоям (`commands::lob` — резолверы, `bybit::verify` —
/// своя копия резолвера; `bybit` не зависит от `commands`, граница слоёв). До
/// T46 оно было второй копией в каждом из них; с двумя суффиксами копий
/// стало бы столько же — второй способ придумать то же правило перестал быть
/// дешевле общего.
pub fn binlog_file_order_key(prefix: &str, name: &str) -> (String, u32) {
    let rest = name.strip_prefix(prefix).unwrap_or(name);
    let rest = strip_binlog_suffix(rest).unwrap_or(rest);
    if let Some(tail) = rest.get(10..) {
        if let Some(num) = tail.strip_prefix("-p") {
            if let Ok(part) = num.parse::<u32>() {
                return (rest[..10].to_string(), part);
            }
        }
    }
    (rest.to_string(), 1)
}

/// Приводит список файлов одного символа к «одна часть суток — один файл»:
/// сортирует хронологически ([`binlog_file_order_key`]) и выбрасывает
/// дубликаты по `(сутки, часть)`.
///
/// Дубликат — ровно случай «оригинал и его архив лежат рядом» (T46): сутки
/// те же (архив собран из этого файла и сверен round-trip), и вернуть оба
/// значило бы проиграть сутки дважды, причём в порядке `read_dir`, то есть
/// недетерминированно. Побеждает **обычный** файл: он не требует кодека и
/// читается всеми командами ровно как до T46, а архив читается, когда
/// оригинала уже нет, — то есть в том порядке, который описывает ранбук
/// (архивировать → сверить → удалить оригинал).
pub fn dedupe_same_day_part(prefix: &str, files: &mut Vec<std::path::PathBuf>) {
    let key_of = |p: &std::path::PathBuf| {
        let name = p
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        binlog_file_order_key(prefix, &name)
    };
    let is_archive = |p: &std::path::PathBuf| p.to_string_lossy().ends_with(BINLOG_ARCHIVE_SUFFIX);
    // Порядок по ключу; при равном ключе обычный файл раньше архива.
    files.sort_by(|a, b| {
        key_of(a)
            .cmp(&key_of(b))
            .then_with(|| is_archive(a).cmp(&is_archive(b)))
    });
    files.dedup_by(|a, b| key_of(a) == key_of(b));
}
