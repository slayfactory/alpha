//! `Reader`: чтение файла журнала, в том числе из архива.

use super::*;

// ---------------------------------------------------------------------
// Reader
// ---------------------------------------------------------------------

/// Читает один суточный файл кадр за кадром. `frames_read` различает
/// «файл кончился между кадрами» (нормально) и «файл кончился на первом
/// же кадре» (в сутках нет синтетического снапшота — ошибка, см.
/// `BinlogError::MissingSnapshot`).
///
/// `version` — версия из заголовка: `VERSION` (текущая) или `VERSION_V2`
/// (то, что пишет живой коллектор до перезапуска). Обе версии читаются одним
/// и тем же `read_frame`; различие — в декодере тела кадра, и другого
/// различия нет: заголовок у версий 2 и 3 общий. Версия хранится в читателе,
/// потому что без неё нельзя решить, каким декодером разбирать кадр, а
/// угадывать по содержимому — это ровно то тихое неверное чтение, от которого
/// версия в заголовке и защищает.
///
/// `decompressor` переиспользуется между кадрами по той же причине, что и
/// `compressor` у `Writer` (см. его доку): держит один `DCtx` вместо того,
/// чтобы заводить новый на каждый вызов. Не заявлено как часть бюджета GC
/// «ноль на событие» — тот бюджет про путь разбор-и-запись рекордера, а
/// чтение обслуживает `verify`/`export`/`markout`, но переиспользование
/// не стоит ничего и держит `Reader` симметричным `Writer`.
pub struct Reader<R: Read> {
    pub(super) inner: Body<R>,
    pub(super) header: Header,
    pub(super) version: u8,
    /// Расписание шагов v4; пусто у v2/v3.
    pub(super) schedule: Vec<StepAt>,
    pub(super) frames_read: u64,
    /// Счётчики мёртвых полей версии 2 (у v3 таких полей нет вовсе). Копятся
    /// по ходу чтения, потому что иначе их неоткуда взять: в `Record` этих
    /// полей нет, а доказательство «0 % ненулевых» должно оставаться
    /// проверяемым на любом старом файле.
    pub(super) legacy_dead: LegacyDeadFields,
    /// Полная длина последнего прочитанного кадра **на диске** (префикс +
    /// тело): замеру M1 тикета 43 нужна базовая линия «сколько файл занимает
    /// сейчас», и взять её из самого читателя честнее, чем считать позицию
    /// файла снаружи. У контейнера архива покадровых байт на диске нет
    /// (сжатие общее на весь файл) — там это длина **тела** кадра в
    /// распакованном потоке, и её же кладут на диск при архивации.
    pub(super) last_frame_bytes: usize,
    /// Уровень zstd контейнера архива, `None` у обычного файла (T46). Нужен
    /// печати (`binlog-stats` именует источник архива) и тестам.
    pub(super) archive_level: Option<u8>,
    pub(super) decompressor: zstd::bulk::Decompressor<'static>,
    pub(super) scratch_stored: Vec<u8>,
    pub(super) scratch_payload: Vec<u8>,
    /// Был ли остановлен на обрезанном **хвостовом** кадре (`read_frame_soft`,
    /// A4, 2026-09-17): файл живой записи читатель может застать между
    /// `write` и полным кадром, и это не порча — см. `ShortRead` выше.
    pub(super) truncated_tail: bool,
}

/// Откуда `Reader` берёт байты после опознания формата: обычный суточный файл
/// или контейнер архива. Заголовок и кадры читаются одинаково, различие — в
/// теле кадра (T46).
pub(super) enum Body<R: Read> {
    /// Обычный файл (v2 или v3): кадры сжаты покадрово. Первые
    /// `MAGIC_VERSION_LEN` байт сюда **не** входят — они уже прочитаны и
    /// разобраны при опознании формата (`Reader::open`), и второй раз их
    /// отдавать значило бы сдвинуть весь хвост заголовка на пять байт.
    Plain(R),
    /// Контейнер архива: **один** zstd-поток на весь файл. Распаковывается
    /// по мере чтения (`Decoder`), а не целиком в память: суточный файл
    /// разжимается в единицы-десятки раз больше, чем занимает на диске, и
    /// держать его целиком в памяти у команды, которая может идти по сотне
    /// символов, нельзя. Внутри — тот же заголовок v3 и те же тела кадров,
    /// только без покадрового сжатия.
    ///
    /// `Chain` — потому что первые байты (магия zstd) already прочитаны при
    /// опознании формата, а декодер обязан увидеть поток с самого начала:
    /// zstd читает свой заголовок именно там.
    Archive(ArchiveDecoder<R>),
    /// Колоночный контейнер (версия 2), заголовок прочитан, колонки ещё нет:
    /// сборка тел кадров идёт при первом чтении кадра, а не при `open` —
    /// счёт открывает файл ради одного заголовка (шаг сетки) несколько раз.
    ColumnarLazy(ArchiveDecoder<R>),
    /// Колоночный контейнер (версия 2): тела кадров уже собраны в память как
    /// поток `[u32 длина | тело]*` — для `read_body` это тот же контейнер.
    Mem(io::Cursor<Vec<u8>>),
}

pub(super) type ArchiveDecoder<R> = zstd::stream::read::Decoder<
    'static,
    io::BufReader<io::Chain<io::Cursor<[u8; MAGIC_VERSION_LEN]>, R>>,
>;

impl<R: Read> Body<R> {
    /// То же `read_upto`, что и у обычного файла, но по телу: у контейнера
    /// это разжимающийся на лету поток.
    pub(super) fn read_upto(&mut self, buf: &mut [u8]) -> io::Result<ReadStatus> {
        match self {
            Self::Plain(inner) => read_upto(inner, buf),
            Self::Archive(decoder) => read_upto(decoder, buf),
            Self::Mem(cursor) => read_upto(cursor, buf),
            Self::ColumnarLazy(_) => {
                self.materialize()?;
                self.read_upto(buf)
            }
        }
    }

    pub(super) fn materialize(&mut self) -> io::Result<()> {
        let Self::ColumnarLazy(decoder) = std::mem::replace(self, Self::Mem(io::Cursor::default()))
        else {
            unreachable!("собирается только отложенный колоночный контейнер")
        };
        let invalid = |m: String| io::Error::new(io::ErrorKind::InvalidData, m);
        let mut blob = Vec::new();
        let limit = HARD_PAYLOAD_CEILING as u64 + 1;
        io::Read::read_to_end(&mut io::Read::take(decoder, limit), &mut blob)?;
        if blob.len() as u64 >= limit {
            return Err(invalid("колоночный контейнер больше потолка суток".into()));
        }
        let stream = archive::columnar_to_stream(&blob).map_err(|e| invalid(format!("{e:?}")))?;
        *self = Self::Mem(io::Cursor::new(stream));
        Ok(())
    }
}

impl<R: Read> fmt::Debug for Reader<R> {
    /// Ручная реализация, не `#[derive]`: `zstd::bulk::Decompressor` не
    /// реализует `Debug` (см. ту же причину у `Writer` выше).
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Reader")
            .field("header", &self.header)
            .field("version", &self.version)
            .field("schedule_len", &self.schedule.len())
            .field("frames_read", &self.frames_read)
            .field("archive_level", &self.archive_level)
            .finish()
    }
}

impl<R: Read> Reader<R> {
    /// Читает и проверяет заголовок. Ничего не помнит о других файлах —
    /// это и есть проверяемое свойство «сутки читаются с первого байта»:
    /// вся нужная для декодирования информация приходит из этого же потока.
    ///
    /// Заголовок читается в **два** шага, не один: сначала `magic` и
    /// `version` (`MAGIC_VERSION_LEN`), и версия проверяется **раньше**,
    /// чем читается хвост заголовка (`HEADER_TAIL_LEN`). У версии 1 не было
    /// поля `max_records_per_frame`, и её хвост на 4 байта короче — прочитать
    /// те же 20 байт из файла версии 1 значило бы принять первые 4 байта
    /// следующего поля (длину первого кадра) за часть заголовка версии 2:
    /// тихое неверное чтение вместо понятной ошибки версии (см. тест
    /// `old_version_file_is_rejected_not_misread`).
    ///
    /// Версии 2 и 3 различаются **только телом кадра**, поэтому хвост
    /// заголовка у них общий и читается одинаково; различие уходит в
    /// `read_frame` через сохранённую `version`.
    ///
    /// **Контейнер архива (T46) опознаётся по магии zstd в первых четырёх
    /// байтах** (`archive::ZSTD_MAGIC`): суточный файл начинается с `MAGIC`
    /// (`ABLG`), и перепутать их нельзя — ни один из них не начинается с
    /// четырёх байт другого. Дальше контейнер читается тем же кодом: внутри
    /// него лежат тот же заголовок v3 и те же тела кадров, отличается только
    /// способ их получения (разжимается весь поток, а не каждый кадр).
    pub fn open(mut inner: R) -> Result<Self, BinlogError> {
        let mut prefix = [0u8; MAGIC_VERSION_LEN];
        match read_upto(&mut inner, &mut prefix)? {
            ReadStatus::Full => {}
            ReadStatus::Partial(got) => {
                return Err(BinlogError::TruncatedHeader {
                    got,
                    want: HEADER_LEN,
                })
            }
            ReadStatus::Eof => {
                return Err(BinlogError::TruncatedHeader {
                    got: 0,
                    want: HEADER_LEN,
                })
            }
        }
        // Прочитанные байты возвращаются в поток только контейнеру: zstd
        // обязан увидеть свой поток с первого байта. Обычному пути они уже
        // не нужны — magic и версия из них прочитаны ниже, а хвост заголовка
        // идёт следом за ними.
        if prefix[0..4] == archive::ZSTD_MAGIC {
            let decoder = zstd::stream::read::Decoder::new(io::Cursor::new(prefix).chain(inner))?;
            return Self::open_container(Body::Archive(decoder));
        }
        if prefix[0..4] != MAGIC {
            return Err(BinlogError::BadMagic {
                got: [prefix[0], prefix[1], prefix[2], prefix[3]],
            });
        }
        let version = prefix[4];
        if version != VERSION && version != VERSION_V2 && version != VERSION_V4 {
            return Err(BinlogError::UnsupportedVersion { got: version });
        }
        let mut body = Body::Plain(inner);
        let header = read_header_tail(&mut body, 0)?;
        let schedule = if version == VERSION_V4 {
            read_schedule_tail(&mut body, 0, &header)?
        } else {
            Vec::new()
        };
        Ok(Self {
            inner: body,
            header,
            version,
            schedule,
            frames_read: 0,
            legacy_dead: LegacyDeadFields::default(),
            last_frame_bytes: 0,
            archive_level: None,
            decompressor: zstd::bulk::Decompressor::new()?,
            scratch_stored: Vec::new(),
            scratch_payload: Vec::new(),
            truncated_tail: false,
        })
    }

    /// Дочитывает контейнер архива: свой маркер, а за ним — обычный заголовок
    /// v3 или v4. Только версии 3 и 4: контейнер собирается из них (v2 в него не
    /// кладут — `archive::write_container` отказывается), и версия внутри
    /// контейнера проверяется ровно так же строго, как в файле. Ошибки
    /// `TruncatedHeader` считают `got`/`want` в байтах **распакованного**
    /// контейнера: снаружи у него нет ни «начала файла», ни «конца суток»,
    /// по которым можно было бы назвать смещение.
    pub(super) fn open_container(mut body: Body<R>) -> Result<Self, BinlogError> {
        let mut container = [0u8; archive::HEADER_LEN];
        match body.read_upto(&mut container)? {
            ReadStatus::Full => {}
            ReadStatus::Partial(got) => {
                return Err(BinlogError::TruncatedHeader {
                    got,
                    want: archive::HEADER_LEN,
                })
            }
            ReadStatus::Eof => {
                return Err(BinlogError::TruncatedHeader {
                    got: 0,
                    want: archive::HEADER_LEN,
                })
            }
        }
        let container_version = archive::validate_container_header(&container)?;
        let level = container[archive::HEADER_LEVEL_AT];
        let mut magic_version = [0u8; MAGIC_VERSION_LEN];
        match body.read_upto(&mut magic_version)? {
            ReadStatus::Full => {}
            ReadStatus::Partial(got) => {
                return Err(BinlogError::TruncatedHeader {
                    got: archive::HEADER_LEN + got,
                    want: archive::HEADER_LEN + HEADER_LEN,
                })
            }
            ReadStatus::Eof => {
                return Err(BinlogError::TruncatedHeader {
                    got: archive::HEADER_LEN,
                    want: archive::HEADER_LEN + HEADER_LEN,
                })
            }
        }
        if magic_version[0..4] != MAGIC {
            return Err(BinlogError::BadMagic {
                got: [
                    magic_version[0],
                    magic_version[1],
                    magic_version[2],
                    magic_version[3],
                ],
            });
        }
        let version = magic_version[4];
        if version != VERSION && version != VERSION_V4 {
            return Err(BinlogError::UnsupportedVersion { got: version });
        }
        let header = read_header_tail(&mut body, archive::HEADER_LEN)?;
        let schedule = if version == VERSION_V4 {
            read_schedule_tail(&mut body, archive::HEADER_LEN, &header)?
        } else {
            Vec::new()
        };
        let body = if container_version == archive::ARCHIVE_VERSION_COLUMNAR {
            let Body::Archive(decoder) = body else {
                unreachable!("контейнер открывается только через Body::Archive")
            };
            Body::ColumnarLazy(decoder)
        } else {
            body
        };
        Ok(Self {
            inner: body,
            header,
            version,
            schedule,
            frames_read: 0,
            legacy_dead: LegacyDeadFields::default(),
            last_frame_bytes: 0,
            archive_level: Some(level),
            decompressor: zstd::bulk::Decompressor::new()?,
            scratch_stored: Vec::new(),
            scratch_payload: Vec::new(),
            truncated_tail: false,
        })
    }

    pub fn header(&self) -> Header {
        self.header
    }

    /// Версия формата из заголовка: `VERSION` (текущая, её пишет `Writer`),
    /// `VERSION_V4` (с расписанием шагов) или `VERSION_V2` (живой коллектор до
    /// перезапуска).
    pub fn version(&self) -> u8 {
        self.version
    }

    /// Расписание шагов суток из заголовка v4; пусто у v2/v3 (шаг один — из
    /// `header()`), в том числе внутри контейнера архива с v3.
    pub fn step_schedule(&self) -> &[StepAt] {
        &self.schedule
    }

    /// Уровень zstd контейнера архива (T46), `None` у обычного суточного
    /// файла. Не влияет на разбор: уровень — запись о том, чем сутки сжаты,
    /// и нужен печати (`binlog-stats`) и тестам.
    pub fn archive_level(&self) -> Option<u8> {
        self.archive_level
    }

    /// Счётчики мёртвых полей, накопленные на прочитанных кадрах версии 2.
    /// На файле версии 3 они нулевые по построению: таких полей в формате нет.
    pub fn legacy_dead_fields(&self) -> LegacyDeadFields {
        self.legacy_dead
    }

    /// Полная длина последнего прочитанного кадра на диске (префикс + тело).
    pub fn last_frame_bytes(&self) -> usize {
        self.last_frame_bytes
    }

    /// Следующее **тело** кадра — как оно записано в источнике: у обычного
    /// файла это тело, разжатое покадрово zstd, у контейнера архива — байты,
    /// которые лежат в нём как есть (T46). Отдельно от `read_frame`, потому
    /// что архивация обязана сравнивать тела кадров **побайтово**: из
    /// `Vec<Record>` байтовое равенство кадра не следует — раскладка того же
    /// набора записей может отличаться группами.
    ///
    /// `None` на чистом конце потока **после** хотя бы одного прочитанного
    /// кадра. Любая нехватка байт внутри объявленной длины — `ShortRead`,
    /// никогда не `Ok(None)` и никогда не паника (Decision 7: усечение
    /// обязано быть видно как короткое чтение, а не как молчаливый пустой
    /// хвост). `frames_read` растёт здесь, а не в `read_frame`: кадр
    /// прочитан из потока в тот момент, когда прочитано его тело.
    ///
    /// Срезы чанка ниже доказаны: `want ≤ READ_CHUNK = chunk.len()` через
    /// `min`, `n` из `Partial(n)` не превышает запрошенного по контракту
    /// `read_upto`; проверка через `get` в цикле ввода-вывода — мёртвый код.
    pub fn read_body(&mut self) -> Result<Option<Vec<u8>>, BinlogError> {
        let mut stored = Vec::new();
        let mut payload = Vec::new();
        Ok(self
            .read_body_into(&mut stored, &mut payload)?
            .then_some(payload))
    }

    /// `read_body` в переиспользуемые буферы вызывающего: `stored` — сжатые
    /// байты с диска, `payload` — тело кадра. Те же проверки и ошибки; `false`
    /// — чистый конец потока.
    pub(super) fn read_body_into(
        &mut self,
        stored: &mut Vec<u8>,
        payload: &mut Vec<u8>,
    ) -> Result<bool, BinlogError> {
        let mut len_buf = [0u8; LEN_PREFIX];
        match self.inner.read_upto(&mut len_buf)? {
            ReadStatus::Eof => {
                return if self.frames_read == 0 {
                    Err(BinlogError::MissingSnapshot)
                } else {
                    Ok(false)
                };
            }
            ReadStatus::Partial(got) => {
                return Err(BinlogError::ShortRead {
                    context: "длина кадра",
                    want: LEN_PREFIX,
                    got,
                })
            }
            ReadStatus::Full => {}
        }
        let len = u32::from_le_bytes(len_buf) as usize;
        self.last_frame_bytes = LEN_PREFIX + len;

        // Ремонт W3 (ревью 23.09): раньше потолок заголовка проверялся только
        // ПОСЛЕ того, как эти `len` байт уже прочитаны — у обычного файла это
        // ещё сжатые байты, но у контейнера архива (`Body::Archive`) тело
        // лежит несжатым и читается через разжимающийся на лету поток
        // (`zstd::stream::read::Decoder`), то есть «прочитаны» там уже значит
        // «разжаты». Испорченный префикс длины (один перевёрнутый бит, до
        // `u32::MAX`) тянул в память гигабайты ДО единственной проверки —
        // ровно то «декомпрессия сначала, проверка после», от которого
        // Decision 23 (ревизия 10) требует уйти, только на шаг раньше в этой
        // же функции. Потолок — тот же самый, что раньше стоял после чтения
        // (`max_frame_record_bytes` для уже разжатого архива,
        // `max_frame_bytes_on_disk` для ещё сжатого обычного файла — то же
        // выражение, которым сам `Writer` резервирует буфер под кадр, поэтому
        // настоящий кадр этого писателя в него гарантированно укладывается),
        // сравнивается с длиной сразу, до единого байта чтения тела.
        let len_ceiling = match self.inner {
            Body::Archive(_) | Body::ColumnarLazy(_) | Body::Mem(_) => {
                max_frame_record_bytes(self.header.max_records_per_frame)
            }
            Body::Plain(_) => max_frame_bytes_on_disk(self.header.max_records_per_frame as usize),
        };
        if len > len_ceiling {
            return Err(BinlogError::FrameExceedsHeaderCeiling {
                max_records_per_frame: self.header.max_records_per_frame,
                ceiling_bytes: len_ceiling,
            });
        }

        // `len` прошла потолок заголовка, но заголовок — те же данные с
        // диска, что и поле длины (см. проверку выше и её обоснование):
        // остаётся читать кусками, а не аллоцировать `len` байт заранее —
        // буфер растёт только на то, что реально пришло, и испорченная
        // длина (в границах потолка, но всё ещё больше настоящего файла)
        // обрывается на `ShortRead` первого недостающего куска, а не на
        // попытке выделить впрок то, чего на диске нет.
        const READ_CHUNK: usize = 64 * 1024;
        stored.clear();
        let mut got = 0usize;
        while got < len {
            let want = (len - got).min(READ_CHUNK);
            stored.resize(got + want, 0);
            match self.inner.read_upto(&mut stored[got..got + want])? {
                ReadStatus::Full => {
                    got += want;
                }
                ReadStatus::Partial(n) => {
                    got += n;
                    return Err(BinlogError::ShortRead {
                        context: "тело кадра",
                        want: len,
                        got,
                    });
                }
                ReadStatus::Eof => {
                    // `len > got`, иначе цикл уже завершился бы выше.
                    // Значит объявленная длина требует байт, которых на
                    // диске больше нет.
                    return Err(BinlogError::ShortRead {
                        context: "тело кадра",
                        want: len,
                        got,
                    });
                }
            }
        }
        self.frames_read += 1;

        if matches!(
            self.inner,
            Body::Archive(_) | Body::ColumnarLazy(_) | Body::Mem(_)
        ) {
            // Тело контейнера лежит несжатым: это и есть тело кадра v3.
            // Потолок уже проверен выше, до чтения (`len_ceiling`) — `stored.
            // len() == len` (цикл выше не выходит иначе) не может превысить
            // его повторно; `debug_assert!` — граница инварианта, а не
            // рабочая проверка (она и не имеет права сработать в релизе, раз
            // выше `len > len_ceiling` уже вернула ошибку).
            debug_assert!(
                stored.len() <= max_frame_record_bytes(self.header.max_records_per_frame),
                "len_ceiling выше обязан был отвергнуть этот кадр раньше"
            );
            std::mem::swap(stored, payload);
            return Ok(true);
        }

        // `bulk::Decompressor::decompress` с явной ёмкостью, не
        // `stream::decode_all`: `decode_all` растит буфер по мере разжатия
        // и не отказывается сам ни при каком размере — ровно то
        // «декомпрессия сначала, проверка после» (уже после аллокации),
        // от которого Decision 23 (ревизия 10) требует уйти. Ёмкость здесь
        // — потолок из заголовка (`max_frame_payload_bytes`), и если
        // распаковка требует больше, zstd возвращает ошибку **до** того,
        // как эти байты выделены (см. `Decompressor::decompress`/`WriteBuf::
        // write_from` в крейте `zstd-safe`: буфер получает ровно
        // запрошенную ёмкость один раз и не растёт).
        //
        // Второй, более тесный потолок — от содержимого, не только от
        // заголовка (ремонт W3, ревью 23.09), и только когда первый уже
        // насыщен об `HARD_PAYLOAD_CEILING`: насыщение — признак вероятно
        // испорченного `max_records_per_frame` (ни один вызывающий в этом
        // дереве, включая тесты с намеренно завышенным потолком, не просит
        // больше нескольких миллионов записей на кадр, а `HARD_PAYLOAD_
        // CEILING` — щедрый бюджет **суток**, не одного кадра), и тогда 4
        // испорченных байта заголовка всё ещё требовали бы до 1.5 ГБ
        // аллокации под кадр, чьё сжатое тело на диске — считаные байты.
        // `stored.len() * MAX_DECOMPRESSION_RATIO` — тот же коэффициент
        // распаковки, которым уже обоснован сам `HARD_PAYLOAD_CEILING`,
        // только от РЕАЛЬНОГО размера этого кадра на диске. Условие на
        // насыщении, а не безусловный `min()`, — намеренно: честный
        // (ненасыщенный) потолок заголовка, каким бы большим он ни был
        // назначен вызывающим (`DEFAULT_TEST_MAX_RECORDS_PER_FRAME` тестов
        // этого файла в том числе), этим ремонтом не тронут вовсе — тесно
        // сравнивать с реальным сжатием годится только тогда, когда сам
        // заголовок уже не выглядит правдоподобным.
        let header_ceiling = max_frame_payload_bytes(self.header.max_records_per_frame);
        let ceiling_bytes = if header_ceiling == HARD_PAYLOAD_CEILING {
            header_ceiling.min(stored.len().saturating_mul(MAX_DECOMPRESSION_RATIO))
        } else {
            header_ceiling
        };
        // Ёмкость буфера = потолок, как у `decompress(.., ceiling)`; при
        // насыщенном потолке (от размера кадра) — свежий буфер на кадр.
        if header_ceiling == HARD_PAYLOAD_CEILING || payload.capacity() < ceiling_bytes {
            *payload = Vec::with_capacity(ceiling_bytes);
        }
        payload.clear();
        self.decompressor
            .decompress_to_buffer(&stored[..], payload)
            .map_err(|_| BinlogError::FrameExceedsHeaderCeiling {
                max_records_per_frame: self.header.max_records_per_frame,
                ceiling_bytes,
            })?;
        Ok(true)
    }

    /// Возвращает следующий кадр как список записей — `read_body` плюс
    /// декодирование по версии источника.
    pub fn read_frame(&mut self) -> Result<Option<Vec<Record>>, BinlogError> {
        let Some(payload) = self.read_body()? else {
            return Ok(None);
        };
        // Версию выбирает заголовок, а не содержимое кадра: угадывание по
        // байтам тела — ровно то тихое неверное чтение, от которого версия
        // в заголовке и защищает (`Reader::open`).
        let records = if self.version == VERSION_V2 {
            let mut dead = self.legacy_dead;
            let records = decode_frame_payload_v2(&payload, &mut dead)?;
            self.legacy_dead = dead;
            records
        } else {
            decode_frame_payload_v3(&payload)?
        };
        Ok(Some(records))
    }

    /// `read_frame` в буферы вызывающего: `records` очищается и заполняется,
    /// сжатые и разжатые байты — в буферах читателя (без аллокации на кадр).
    /// `false` — чистый конец потока. Записи те же, что у `read_frame`.
    pub fn read_frame_into(&mut self, records: &mut Vec<Record>) -> Result<bool, BinlogError> {
        let mut stored = std::mem::take(&mut self.scratch_stored);
        let mut payload = std::mem::take(&mut self.scratch_payload);
        let res = self.read_body_into(&mut stored, &mut payload);
        let out = match res {
            Ok(true) => {
                records.clear();
                if self.version == VERSION_V2 {
                    let mut dead = self.legacy_dead;
                    let r = decode_frame_payload_v2(&payload, &mut dead);
                    self.legacy_dead = dead;
                    r.map(|v| {
                        *records = v;
                        true
                    })
                } else {
                    decode_frame_payload_v3_into(&payload, records).map(|()| true)
                }
            }
            Ok(false) => Ok(false),
            Err(e) => Err(e),
        };
        self.scratch_stored = stored;
        self.scratch_payload = payload;
        out
    }

    /// Кадр живого файла (A4, 2026-09-17): обрезанный **хвостовой** кадр —
    /// это не порча, а «файл ещё пишется»: читатель может застать момент
    /// между записью префикса и дописыванием тела. Отличие от `read_frame`
    /// ровно одно: `ShortRead` здесь означает конец прочитанного (`Ok(None)`)
    /// и поднимает флаг `truncated_tail()`, который вызывающий печатает
    /// предупреждением; всё остальное (`Corrupt`, `MissingSnapshot`,
    /// `TruncatedHeader`) остаётся ошибкой — эти состояния об обрыве хвоста не
    /// говорят.
    pub fn read_frame_soft(&mut self) -> Result<Option<Vec<Record>>, BinlogError> {
        match self.read_frame() {
            Ok(frame) => Ok(frame),
            Err(BinlogError::ShortRead { .. }) => {
                self.truncated_tail = true;
                Ok(None)
            }
            Err(e) => Err(e),
        }
    }

    /// Остановился ли последний `read_frame_soft` на обрезанном хвосте.
    pub fn truncated_tail(&self) -> bool {
        self.truncated_tail
    }
}
