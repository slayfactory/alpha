//! Тишина канала: полное молчание, только pong, эпизоды, рыночные события.

use super::*;

/// Транспорт «сервер отвечает только pong»: каждый `recv()` после короткой
/// паузы отдаёт кадр, который `ws::parse_message_into` разбирает как ровно
/// один `Event::Other` (нет поля `topic` — ни `fast_topic`, ни `probe_topic`
/// не находят его, откуда и `Other`), и никогда — `Event::Book`/`Event::
/// Trade`. Нужен для доработки V5 (2026-09-24): такой кадр доходит до
/// пересылки в `handle_raw` и ставит `session.productive`, хотя рынок молчит,
/// а сам факт, что `recv()` регулярно что-то отдаёт, раньше держал внешний
/// `select!` в постоянном движении.
/// Ритм кадров подставного транспорта, **устойчивый к отмене** `recv()`:
/// срок следующего кадра хранится снаружи будущего и сдвигается только после
/// выдачи кадра. `run_with_backoff` пересоздаёт `recv()` на каждом витке
/// `select!` (тик пинга отменяет незавершённое чтение) — настоящий сокет при
/// этом кадров не теряет, а подставной с `sleep(every)` внутри будущего
/// начинал бы ожидание заново. На Windows таймер шагает по ~15.6 мс: пинг 10
/// мс и кадр 5 мс оба округляются до одного шага, пинг выигрывал каждый виток,
/// кадр не приходил никогда — и тесты ловили «мёртвый транспорт», которого
/// нет (на Linux шаг 1 мс, там это было незаметно).
#[derive(Clone)]
pub(super) struct Cadence {
    pub(super) every: Duration,
    pub(super) next_at: Arc<std::sync::Mutex<tokio::time::Instant>>,
}

impl Cadence {
    pub(super) fn new(every: Duration) -> Self {
        Self {
            every,
            next_at: Arc::new(std::sync::Mutex::new(tokio::time::Instant::now() + every)),
        }
    }

    /// Дождаться срока текущего кадра (срок переживает отмену) и назначить
    /// следующий.
    async fn wait(self) {
        let due = *self.next_at.lock().unwrap();
        tokio::time::sleep_until(due).await;
        *self.next_at.lock().unwrap() = tokio::time::Instant::now() + self.every;
    }
}

pub(super) struct PongOnlyTransport {
    pub(super) cadence: Cadence,
}

impl Transport for PongOnlyTransport {
    async fn send_text(&mut self, _msg: String) -> Result<(), TransportError> {
        Ok(())
    }

    fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
        let cadence = self.cadence.clone();
        async move {
            cadence.wait().await;
            Ok(Frame::Text(r#"{"op":"pong"}"#.to_string().into()))
        }
    }
}

/// Выдаёт `PongOnlyTransport` на каждое подключение и считает сами вызовы
/// `connect()` — тест обязан увидеть не только `Disconnected`, но и то, что
/// `run_with_backoff` действительно подключается заново, а не просто эмитит
/// событие и останавливается.
pub(super) struct PongOnlyConnector {
    pub(super) pong_every: Duration,
    pub(super) connects: Arc<std::sync::atomic::AtomicUsize>,
}

impl TransportConnector for PongOnlyConnector {
    type Transport = PongOnlyTransport;

    fn connect(
        &mut self,
    ) -> impl Future<Output = Result<PongOnlyTransport, TransportError>> + Send {
        self.connects
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let pong_every = self.pong_every;
        async move {
            Ok(PongOnlyTransport {
                cadence: Cadence::new(pong_every),
            })
        }
    }
}

/// Транспорт вообще без единого кадра: `recv()` не разрешается никогда (не
/// ошибкой — зависает, как настоящий полуоткрытый TCP: SYN/ACK был, а после
/// него ни байта, ни RST/FIN). Нужен для проверки настоящего детектора
/// мёртвого транспорта — независимая проверка нашла, что `frame = timeout(
/// recv_timeout, transport.recv())` в `run_with_backoff` почти никогда не
/// успевает истечь сама при боевом соотношении `recv_timeout = 2 ×
/// ping_interval`: тик пинга (виток внешнего `loop`, пересоздающий этот
/// таймаут) всегда опережает её. Настоящий детектор — `last_any_frame_ns` в
/// ветке пинга.
pub(super) struct DeadTransport;

impl Transport for DeadTransport {
    async fn send_text(&mut self, _msg: String) -> Result<(), TransportError> {
        Ok(())
    }

    fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
        std::future::pending()
    }
}

pub(super) struct DeadConnector {
    pub(super) connects: Arc<std::sync::atomic::AtomicUsize>,
}

impl TransportConnector for DeadConnector {
    type Transport = DeadTransport;

    fn connect(&mut self) -> impl Future<Output = Result<DeadTransport, TransportError>> + Send {
        self.connects
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        async move { Ok(DeadTransport) }
    }
}

/// BLOCKER независимой проверки 7af2c0b: при боевом соотношении пинг:таймаут
/// (1:2, здесь впрямую 10/20 мс) транспорт, не отвечающий вовсе — ни рынком,
/// ни `pong` — обязан дать `Disconnected` и переподключение по часу
/// `last_any_frame_ns`, а не повиснуть до ретрансмиссий ОС (15–30+ мин без
/// `SO_KEEPALIVE`).
#[tokio::test]
async fn totally_silent_transport_disconnects_even_at_the_production_ping_ratio() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.ping_interval = Duration::from_millis(10);
    cfg.recv_timeout = Duration::from_millis(20);
    let connects = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let connector = DeadConnector {
        connects: connects.clone(),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

    let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("совсем мёртвый транспорт обязан дать Disconnected, а не висеть")
        .expect("канал жив");
    assert_eq!(
        ev,
        ConnEvent::Disconnected {
            first_of_socket: true
        },
        "ни кадра вовсе (даже pong) — транспорт мёртв, это не тишина рынка"
    );

    // Переподключение: цикл обязан вызвать `connect()` заново.
    tokio::time::sleep(Duration::from_millis(100)).await;
    handle.abort();
    assert!(
        connects.load(std::sync::atomic::Ordering::SeqCst) >= 2,
        "после мёртвого транспорта соединение обязано переподключиться"
    );
}

/// Доработка V5 (2026-09-24): `ping_interval` короче `recv_timeout` —
/// боевое соотношение `recv_timeout = 2 × ping_interval`, здесь напрямую
/// 10 мс / 40 мс. Транспорт отвечает на пинг (`pong`, который двигает
/// `last_any_frame_ns` — см. `totally_silent_transport_...` выше про случай,
/// когда не отвечает и он), но рынка (Book/Trade) нет — решение владельца
/// 24.09: соединение по тишине неликвидной монеты **не рвётся**, только
/// предупреждение (`stderr`, не чаще одного на эпизод) и `ConnEvent::
/// MarketSilence` на каждом тике эпизода (счёт — только по `first_of_
/// episode`, максимум — по каждому событию, MAJOR независимой проверки).
#[tokio::test]
async fn pong_only_silence_does_not_disconnect_but_warns_once_per_episode() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.ping_interval = Duration::from_millis(10);
    cfg.recv_timeout = Duration::from_millis(40);
    let connects = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let connector = PongOnlyConnector {
        pong_every: Duration::from_millis(4),
        connects: connects.clone(),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

    // Окно с большим запасом сверх первого превышения порога (~50 мс:
    // интервалы пинга по 10 мс, порог 40 мс) — молчание длится весь прогон
    // (pong не рынок, эпизод не кончается), поэтому за всё окно обязано быть
    // ровно одно `first_of_episode: true` (предупреждение в stderr), а
    // дальше — только тихие обновления максимума (`first_of_episode: false`).
    let deadline = tokio::time::Instant::now() + Duration::from_millis(300);
    let mut episode_starts = 0u32;
    let mut updates = 0u32;
    let mut last_silence_ns = 0i64;
    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, rx.recv()).await {
            Ok(Some(ConnEvent::Disconnected { .. })) => {
                panic!("тишина рынка при живых pong-ах не должна рвать соединение (владелец 24.09)")
            }
            Ok(Some(ConnEvent::MarketSilence {
                silence_ns,
                first_of_episode,
                ..
            })) => {
                if first_of_episode {
                    episode_starts += 1;
                } else {
                    updates += 1;
                }
                assert!(
                    silence_ns >= last_silence_ns,
                    "тишина внутри одного эпизода не имеет права убывать"
                );
                last_silence_ns = silence_ns;
            }
            Ok(Some(_)) => {}
            Ok(None) => break,
            Err(_) => break,
        }
    }
    handle.abort();

    assert_eq!(
        episode_starts, 1,
        "один непрерывный эпизод молчания — ровно одно предупреждение, не поток"
    );
    assert!(
        updates > 0,
        "эпизод длиннее нескольких тиков пинга обязан обновлять максимум тишины, \
         не только первое срабатывание (иначе session.json.silence_max_ns застынет на ~recv_timeout)"
    );
    assert_eq!(
        connects.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "соединение не переподключается по тишине рынка (владелец 24.09)"
    );
}

/// Транспорт «pong, потом одно рыночное сообщение, потом снова pong»: нужен,
/// чтобы проверить границу эпизода — доработка V5 (2026-09-24) считает эпизод
/// молчания законченным на первом `Book`/`Trade`, и следующее превышение
/// порога обязано дать **новое** предупреждение, а не быть проглоченным
/// прежним `silence_warned`.
pub(super) struct TwoEpisodesTransport {
    pub(super) cadence: Cadence,
    pub(super) calls: Arc<std::sync::atomic::AtomicUsize>,
    /// Номер вызова `recv()` (считая с 1), на котором вместо `pong`
    /// отдаётся рыночный снапшот — заканчивает первый эпизод.
    pub(super) market_at_call: usize,
}

impl Transport for TwoEpisodesTransport {
    async fn send_text(&mut self, _msg: String) -> Result<(), TransportError> {
        Ok(())
    }

    fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
        let cadence = self.cadence.clone();
        let calls = self.calls.clone();
        let market_at_call = self.market_at_call;
        async move {
            cadence.wait().await;
            let n = calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
            if n == market_at_call {
                Ok(Frame::Text(orderbook_msg(
                    "snapshot",
                    1,
                    1,
                    &[(1.0, 5.0)],
                    &[],
                )))
            } else {
                Ok(Frame::Text(r#"{"op":"pong"}"#.to_string().into()))
            }
        }
    }
}

pub(super) struct TwoEpisodesConnector {
    pub(super) tick: Duration,
    pub(super) market_at_call: usize,
    pub(super) connects: Arc<std::sync::atomic::AtomicUsize>,
}

impl TransportConnector for TwoEpisodesConnector {
    type Transport = TwoEpisodesTransport;

    fn connect(
        &mut self,
    ) -> impl Future<Output = Result<TwoEpisodesTransport, TransportError>> + Send {
        self.connects
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let tick = self.tick;
        let market_at_call = self.market_at_call;
        async move {
            Ok(TwoEpisodesTransport {
                cadence: Cadence::new(tick),
                calls: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                market_at_call,
            })
        }
    }
}

/// Доработка V5 (2026-09-24): эпизод молчания кончается на первом
/// `Book`/`Trade` (`silence_warned` сбрасывается там же, где двигается
/// `last_market_ns`) — следующее превышение порога после этого обязано дать
/// **второе**, отдельное предупреждение (`first_of_episode: true`), а не
/// молчать до конца прогона и не быть учтено вторым эпизодом при первом же
/// срабатывании.
#[tokio::test]
async fn market_event_ends_the_episode_and_the_next_silence_warns_again() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.ping_interval = Duration::from_millis(10);
    cfg.recv_timeout = Duration::from_millis(40);
    // Тик 4 мс: снапшот на 20-м вызове recv() приходит ориентировочно около
    // t≈80 мс — после первого предупреждения (~t≈50 мс, первый тик пинга с
    // превышением 40-мс порога). Точный момент гуляет (тик пинга иногда
    // опережает recv() и откладывает очередной вызов), поэтому окно ниже —
    // с большим запасом, а не впритык к арифметике.
    let connects = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let connector = TwoEpisodesConnector {
        tick: Duration::from_millis(4),
        market_at_call: 20,
        connects: connects.clone(),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

    let deadline = tokio::time::Instant::now() + Duration::from_millis(800);
    let mut episode_starts = 0u32;
    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, rx.recv()).await {
            Ok(Some(ConnEvent::Disconnected { .. })) => {
                panic!("тишина рынка не должна рвать соединение (владелец 24.09)")
            }
            Ok(Some(ConnEvent::MarketSilence {
                first_of_episode: true,
                ..
            })) => episode_starts += 1,
            Ok(Some(_)) => {}
            Ok(None) => break,
            Err(_) => break,
        }
    }
    handle.abort();

    assert_eq!(
        episode_starts, 2,
        "рыночное событие кончает эпизод — следующая тишина обязана дать новое предупреждение, \
         не продолжение прежнего"
    );
    assert_eq!(
        connects.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "соединение не переподключается по тишине рынка (владелец 24.09)"
    );
}

/// Транспорт «рынок не молчит»: каждый `recv()` после короткой паузы отдаёт
/// снапшот книги (`Event::Book`, тип `snapshot` — принимается при любом `u`,
/// см. `book::Book::apply`) с новым `u`. Обратная сторона предыдущего теста:
/// рыночные события чаще `recv_timeout` обязаны держать соединение живым
/// сколь угодно долго, даже когда `ping_interval` короче `recv_timeout`.
pub(super) struct MarketEveryTransport {
    pub(super) cadence: Cadence,
    pub(super) seq: Arc<std::sync::atomic::AtomicU64>,
}

impl Transport for MarketEveryTransport {
    async fn send_text(&mut self, _msg: String) -> Result<(), TransportError> {
        Ok(())
    }

    fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
        let cadence = self.cadence.clone();
        let seq = self.seq.clone();
        async move {
            cadence.wait().await;
            let u = seq.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                u,
                i64::try_from(u).unwrap_or(i64::MAX),
                &[(1.0, 5.0)],
                &[],
            )))
        }
    }
}

pub(super) struct MarketEveryConnector {
    pub(super) market_every: Duration,
}

impl TransportConnector for MarketEveryConnector {
    type Transport = MarketEveryTransport;

    fn connect(
        &mut self,
    ) -> impl Future<Output = Result<MarketEveryTransport, TransportError>> + Send {
        let market_every = self.market_every;
        async move {
            Ok(MarketEveryTransport {
                cadence: Cadence::new(market_every),
                seq: Arc::new(std::sync::atomic::AtomicU64::new(0)),
            })
        }
    }
}

#[tokio::test]
async fn market_events_faster_than_recv_timeout_never_disconnect() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.ping_interval = Duration::from_millis(10);
    cfg.recv_timeout = Duration::from_millis(40);
    let connector = MarketEveryConnector {
        market_every: Duration::from_millis(5),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

    // 30 событий при рынке каждые 5 мс — это ~150 мс живого соединения,
    // несколько `recv_timeout` (40 мс) и добрый десяток тиков пинга (10 мс)
    // подряд: ни одно из них не имеет права оказаться `Disconnected`.
    for _ in 0..30 {
        let ev = tokio::time::timeout(Duration::from_secs(2), rx.recv())
            .await
            .expect("рыночные события каждые 5 мс не должны прекращаться")
            .expect("канал жив");
        assert!(
            matches!(ev, ConnEvent::Message { .. }),
            "рынок каждые 5 мс не должен давать Disconnected при таймауте 40 мс: {ev:?}"
        );
    }
    handle.abort();
}
