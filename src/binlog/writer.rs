//! `Writer`: запись файла журнала.

use super::*;

// ---------------------------------------------------------------------
// Writer
// ---------------------------------------------------------------------

/// Пишет один суточный файл: заголовок один раз при создании, дальше кадры
/// по вызову. `scratch`/`compressed` — переиспользуемые буферы, и `compressor`
/// — переиспользуемый контекст zstd: после прогрева (первый кадр вырастил
/// все три до нужной ёмкости) `write_frame` не аллоцирует ни на запись, ни
/// на кадр — это и есть путь «разбор и запись» гейта GC (бюджет «ноль»,
/// SETTLED.md B2; не тот же бюджет, что «≤ 1 на кадр» транспорта).
pub struct Writer<W: Write> {
    pub(super) inner: W,
    pub(super) header: Header,
    pub(super) scratch: Vec<u8>,
    pub(super) compressed: Vec<u8>,
    /// `zstd::bulk::Compressor`, не `zstd::stream::copy_encode`: последний
    /// создаёт новый контекст сжатия на каждый вызов (streaming `Encoder`
    /// поверх свежего `CCtx`), а `Compressor` держит один `CCtx` и переживает
    /// вызовы `write_frame` — ровно то переиспользование, которого требует
    /// бюджет «ноль после прогрева», а не только «мало и не растёт».
    pub(super) compressor: zstd::bulk::Compressor<'static>,
}

impl<W: Write> fmt::Debug for Writer<W> {
    /// Ручная реализация, не `#[derive]`: `zstd::bulk::Compressor` не
    /// реализует `Debug`, а печатать `inner`/`scratch`/`compressed` целиком
    /// ради отладочного вывода не нужно — размеры буферов дают тот же сигнал.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Writer")
            .field("header", &self.header)
            .field("scratch_cap", &self.scratch.capacity())
            .field("compressed_cap", &self.compressed.capacity())
            .finish()
    }
}

impl<W: Write> Writer<W> {
    /// `level` — параметр, не константа модуля: число, которым Decision 23
    /// торгует размер против CPU, назначается пилотом 3.1 по измеренному
    /// байту на запись, а не изобретается здесь.
    pub fn create(inner: W, header: Header, level: i32) -> Result<Self, BinlogError> {
        Self::create_checked(inner, header, &[], level)
    }

    /// Файл v4: заголовок суток плюс расписание шагов (`StepAt`). `header.tick_e9`
    /// и `header.step_e9` — мельчайший шаг суток; расписание проверяется
    /// (`validate_schedule`: от двух записей, время строго растёт, шаги
    /// положительны и кратны шагам заголовка) до единого записанного байта.
    pub fn create_with_schedule(
        inner: W,
        header: Header,
        schedule: &[StepAt],
        level: i32,
    ) -> Result<Self, BinlogError> {
        validate_header(header)?;
        validate_schedule(&header, schedule)?;
        Self::create_checked(inner, header, schedule, level)
    }

    /// Пустое `schedule` — v3, непустое (уже проверенное) — v4.
    pub(super) fn create_checked(
        mut inner: W,
        header: Header,
        schedule: &[StepAt],
        level: i32,
    ) -> Result<Self, BinlogError> {
        validate_header(header)?;
        inner.write_all(&header_bytes(&header, schedule))?;
        let compressor = zstd::bulk::Compressor::new(level)?;
        Ok(Self {
            inner,
            header,
            scratch: Vec::new(),
            compressed: Vec::new(),
            compressor,
        })
    }

    pub fn header(&self) -> Header {
        self.header
    }

    /// Пишет один кадр. Пустой срез — намеренный no-op: нулевая запись не
    /// несёт события и не порождает кадр, который на чтении пришлось бы
    /// отличать от настоящего (кадр с нулём записей был бы неотличим по
    /// смыслу от `MissingSnapshot`, если бы это оказался единственный кадр
    /// файла).
    pub fn write_frame(&mut self, records: &[Record]) -> Result<(), BinlogError> {
        if records.is_empty() {
            return Ok(());
        }

        // Программная ошибка вызывающего, не порча данных — `assert!`, не
        // `Result` (см. доку модуля и `validate_header`, тот же выбор по
        // тому же принципу: аргумент приходит из кода, а не с диска). Число
        // записей в `records` и `max_records_per_frame` заголовка выбирает
        // один и тот же вызывающий (рекордер, батчинг шага 0.3) — расхождение
        // между ними может быть только его багом. `debug_assert!` был бы не
        // тем выбором: рекордер собирается и работает неделями в `--release`,
        // где `debug_assert!` вырезается, и тогда именно тот кадр, который
        // должен был упасть здесь немедленно и громко, вместо этого ушёл бы
        // на диск — а отвергнет его уже `Reader` (`FrameExceedsHeaderCeiling`),
        // но только при следующем чтении, недели спустя, когда чинить нечего.
        assert!(
            records.len() <= self.header.max_records_per_frame as usize,
            "кадр из {} записей превышает потолок заголовка max_records_per_frame={}: \
             это баг батчинга вызывающего, а не повреждение данных",
            records.len(),
            self.header.max_records_per_frame
        );

        self.scratch.clear();
        // Тело кадра целиком — один вызов кодека v3: эпоха, группы сообщений
        // (флаги и обе метки один раз на сообщение), дельты цены и размера.
        // Полевой бюджет кодировщик считает всегда, но здесь он не нужен: его
        // читает только замер (`binlog-stats --reencode`), и отбрасывается он
        // бесплатно — это `Copy`-структура из `usize`, не аллокация.
        encode_frame_payload_v3(records, &mut self.scratch);

        self.compressed.clear();
        // Резервируем по границе zstd (`compress_bound`), не по факту: если
        // бы `compressed` не хватило места, `compress_to_buffer` вернул бы
        // ошибку вместо того, чтобы сам вырасти (в отличие от `Vec::push`),
        // а недостаточная ёмкость на холодном или необычно сжимаемом кадре —
        // это не то же самое, что «Corrupt» на чтении.
        let bound = zstd::zstd_safe::compress_bound(self.scratch.len());
        if self.compressed.capacity() < bound {
            self.compressed.reserve(bound - self.compressed.len());
        }
        self.compressor
            .compress_to_buffer(&self.scratch[..], &mut self.compressed)?;

        let len = u32::try_from(self.compressed.len()).map_err(|_| BinlogError::FrameTooLarge {
            len: self.compressed.len(),
        })?;
        self.inner.write_all(&len.to_le_bytes())?;
        self.inner.write_all(&self.compressed)?;
        Ok(())
    }

    pub fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }

    /// Приёмник как есть — вызывающему нужны его счётчики (байты на диске,
    /// таск 25), формат не задет.
    pub fn get_ref(&self) -> &W {
        &self.inner
    }

    /// Тот же приёмник, изменяемо: им закрывают часть на ходу (A8.1 —
    /// инструмент убран из пула: `FrameSink::close` отпускает дескриптор,
    /// счётчики приёмника остаются). Кадров через `Writer` после этого не
    /// пишут; сам формат не задет.
    pub fn get_mut(&mut self) -> &mut W {
        &mut self.inner
    }

    pub fn into_inner(self) -> W {
        self.inner
    }
}
