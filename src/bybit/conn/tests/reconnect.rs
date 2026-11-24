//! Пинг, повторное подключение, отметки времени и проверки книги.

use super::*;

/// Не входит в обязательный список задачи, но "пинг на интервале, который
/// требует Bybit" — отдельное требование шага 0.1, и без этого теста его
/// никто бы не проверял вовсе.
#[tokio::test]
async fn ping_is_sent_repeatedly_on_the_configured_interval() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.ping_interval = Duration::from_millis(5);
    let frames = vec![Ok(Frame::Text(orderbook_msg(
        "snapshot",
        10,
        1,
        &[(1.0, 5.0)],
        &[(1.0001, 4.0)],
    )))];
    let (connector, sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

    let _ = collect_n(&mut rx, 1).await; // снапшот получен, дальше сокет молчит
    tokio::time::sleep(Duration::from_millis(100)).await;
    handle.abort();

    let sent = sent.lock().unwrap();
    let pings = sent.iter().filter(|m| m.contains(r#""op":"ping""#)).count();
    assert!(
        pings >= 1,
        "за 100 мс при интервале 5 мс обязан пройти хотя бы один пинг, отправлено {pings}"
    );
}

/// FIX 1. Без `SendFailsOnCall` этот путь непроверяем: `ScriptedTransport`
/// не умеет проваливать конкретную отправку, поэтому раньше `handle_raw`
/// мог молча вернуть `false` на неудавшемся ресинке, и ни один тест этого
/// не замечал. Счёт вызовов `send_text`: 1 — `sub_orderbook` при входе в
/// сессию, 2 — повторная `sub_orderbook` на ресинке после
/// разрыва `u`; проваливаем именно третий.
#[tokio::test]
async fn resync_resubscribe_send_failure_still_emits_disconnected() {
    let frames = vec![
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        Ok(Frame::Text(orderbook_msg(
            "delta",
            12, // разрыв: ждали 11 — запускает ресинк-подписку
            2,
            &[(1.0, 6.0)],
            &[],
        ))),
    ];
    let connector = SingleTransportConnector {
        transport: Some(SendFailsOnCall {
            inbox: VecDeque::from(frames),
            send_calls: 0,
            // Отправка №1 — подписка на весь набор сокета одним
            // сообщением (`ws::sub_pool`, таск 28; до него подписок было
            // две — стакан и лента — и ресинк был третьей отправкой),
            // №2 — ресинк-подписка, которая и обязана упасть.
            fail_on_call: 2,
        }),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    // Message(u=10) → SequenceGap → Disconnected. Без FIX 1 третье событие
    // никогда не приходит, и `collect_n` падает по таймауту 5с, а не по
    // неверному значению — баг вешает тест, а не просто заваливает assert.
    let events = collect_n(&mut rx, 3).await;
    handle.abort();

    match &events[0] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!(u.u, 10),
        other => panic!("{other:?}"),
    }
    assert_eq!(
        events[1],
        ConnEvent::SequenceGap {
            depth: 50,
            expected: 11,
            got: 12
        }
    );
    assert_eq!(
        events[2],
        ConnEvent::Disconnected {
            first_of_socket: true
        },
        "неудавшаяся отправка ресинка — тоже смерть сокета и обязана дать Disconnected, \
         как и два других пути в select!"
    );
}

/// Бэкофф, который не ждёт по-настоящему: записывает запрошенную
/// задержку и сразу возвращает готовое будущее — ровно то, что нужно
/// FIX 2, доказанному ниже без единой миллисекунды настоящего времени.
/// После `halt_after` записей возвращает будущее, которое не завершается
/// никогда (`std::future::pending`) — цикл `Connection::run_with_backoff`
/// сам останавливается на достаточном числе раундов вместо того, чтобы
/// тест гадал, сколько реальных миллисекунд ждать, пока раунды накопятся.
pub(super) struct RecordingBackoff {
    pub(super) waited: Arc<Mutex<Vec<Duration>>>,
    pub(super) halt_after: usize,
}

impl Backoff for RecordingBackoff {
    fn wait(&self, dur: Duration) -> impl Future<Output = ()> + Send {
        let stop = {
            let mut guard = self.waited.lock().unwrap();
            guard.push(dur);
            guard.len() >= self.halt_after
        };
        async move {
            if stop {
                std::future::pending::<()>().await;
            }
        }
    }
}

/// FIX 2. Сервер, который принимает рукопожатие и тут же рвёт соединение
/// (рейт-лимит, сброс нагрузки, дефектный прокси), не должен держать цикл
/// на полу бэкоффа вечно. Ни одна из сессий `AlwaysCloseImmediatelyConnector`
/// не пересылает ни одного сообщения (только мгновенный `Frame::Closed`),
/// поэтому счётчик попыток обязан расти от раунда к раунду, а не
/// сбрасываться по одному лишь факту успешной подписки.
///
/// Раньше тест мерил настоящие интервалы `std::time::Instant` между
/// вызовами `connect()` — реальные часы под нагрузкой CI время от времени
/// дают дребезг ровно там, где порог не рассчитан на него (наблюдалось:
/// первая задержка 64мс при пороге «< 60мс у пола ~3мс»). `RecordingBackoff`
/// убирает настоящее время из проверки целиком: `run_with_backoff` зовёт
/// `backoff.wait(delay_for_attempt(attempt))`, и это ровно то значение,
/// что здесь читается — без побочного шума планировщика ОС. `tokio::time::
/// timeout` ниже — не источник данных для проверки, а только защита от
/// зависания на сломанном коде (тот же приём, что уже даёт `collect_n`
/// в FIX 1).
#[tokio::test]
async fn connect_then_close_without_forwarding_a_message_does_not_reset_backoff() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.backoff = BackoffConfig {
        initial: Duration::from_millis(3),
        max: Duration::from_millis(150),
        multiplier: 5,
    };
    const SAMPLES: usize = 8;
    let waited: Arc<Mutex<Vec<Duration>>> = Arc::new(Mutex::new(Vec::new()));
    let backoff = RecordingBackoff {
        waited: waited.clone(),
        halt_after: SAMPLES,
    };
    let (tx, _rx) = mpsc::channel(16);
    let handle = tokio::spawn(
        Connection::new(AlwaysCloseImmediatelyConnector, cfg).run_with_backoff(
            SystemClock,
            tx,
            backoff,
        ),
    );

    // Планировщик здесь не спит по-настоящему (`RecordingBackoff`), так
    // что `SAMPLES` задержек собираются за считаные переключения задачи —
    // `yield_now` отдаёт управление рантайму ровно затем, чтобы дать
    // спавнутой задаче продвинуться, не полагаясь на реальное время;
    // `timeout` — только потолок на случай, если код сломан и цикл
    // никогда не доходит до `SAMPLES` записей.
    let collected = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if waited.lock().unwrap().len() >= SAMPLES {
                return;
            }
            tokio::task::yield_now().await;
        }
    })
    .await;
    handle.abort();
    collected.expect("сбор образцов бэкоффа завис — цикл не дошёл до SAMPLES раундов");

    let gaps = waited.lock().unwrap().clone();
    assert!(
        gaps.len() >= 5,
        "ожидались хотя бы 5 задержек, получено {}",
        gaps.len()
    );
    assert!(
        gaps[0] < Duration::from_millis(60),
        "первая задержка обязана быть у пола (~3мс): {gaps:?}"
    );
    assert!(
        gaps[gaps.len() - 3..]
            .iter()
            .all(|g| *g > Duration::from_millis(100)),
        "после нескольких непродуктивных раундов задержка обязана вырасти к потолку \
         (150мс) и остаться там, а не проваливаться обратно к полу (3мс) — это и значило \
         бы, что счётчик попыток сбрасывается без всякой причины: {gaps:?}"
    );
}

/// FIX 3. Раньше `SystemClock::now_ns` паниковал через `.expect(...)` на
/// `SystemTime::now() < UNIX_EPOCH`; настоящие часы хоста в этот момент не
/// вставить в тест, поэтому фолбэк проверяется на чистой функции
/// `ns_since_epoch` с `SystemTime`, сконструированным раньше эпохи напрямую.
#[test]
fn system_clock_falls_back_instead_of_panicking_before_unix_epoch() {
    let before_epoch = UNIX_EPOCH - Duration::from_secs(1);
    let ns = SystemClock::ns_since_epoch(before_epoch);
    assert_eq!(
        ns, -1_000_000_000,
        "секунда до эпохи обязана дать -1e9 нс, а не панику"
    );

    // Обычный путь не сломан рефакторингом: секунда ПОСЛЕ эпохи по-прежнему
    // даёт положительные наносекунды.
    let after_epoch = UNIX_EPOCH + Duration::from_secs(1);
    assert_eq!(SystemClock::ns_since_epoch(after_epoch), 1_000_000_000);
}

/// FIX 4. Старые H12-тесты используют `SystemClock` и проверяют `<=` —
/// свойство, которое не отличает "часы читаются на каждый кадр" от "часы
/// прочитаны один раз на сессию и разошлись по всем событиям": константа
/// тоже монотонна нестрого. `CountingClock` закрывает именно это: каждое
/// показание уникально по построению, поэтому строгий рост доказывает, что
/// `now_ns()` вызывается заново на каждый кадр, а не переиспользуется.
///
/// Таск 04 добавил вторую метку, `parsed_ts_ns`, сразу после
/// `ws::parse_message` (суббюджет «разбор», `PLAN.md` 3.1) — на каждый
/// успешно распарсенный кадр (все четыре здесь — валидные `orderbook`)
/// часы теперь читаются ровно дважды, не один раз; сверка счётчика с
/// `2 * n_frames` доказывает, что ни одного лишнего чтения сверх этих
/// двух не просочилось.
#[tokio::test]
async fn local_ts_advances_exactly_twice_per_completed_recv_and_is_strictly_increasing() {
    let frames = vec![
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        Ok(Frame::Text(orderbook_msg(
            "delta",
            11,
            2,
            &[(1.0, 6.0)],
            &[],
        ))),
        Ok(Frame::Text(orderbook_msg(
            "delta",
            12,
            3,
            &[(1.0, 7.0)],
            &[],
        ))),
        Ok(Frame::Text(orderbook_msg(
            "delta",
            13,
            4,
            &[(1.0, 8.0)],
            &[],
        ))),
    ];
    let n_frames = frames.len();
    let (connector, _sent) = ScriptedConnector::new(vec![frames]);
    let (clock, calls) = CountingClock::new();
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(clock, tx));

    let events = collect_n(&mut rx, n_frames).await;
    handle.abort();

    let timestamps: Vec<i64> = events
        .iter()
        .map(|ev| match ev {
            ConnEvent::Message { local_ts_ns, .. } => *local_ts_ns,
            other => panic!("ожидался Message: {other:?}"),
        })
        .collect();
    assert!(
        timestamps.windows(2).all(|w| w[0] < w[1]),
        "метки обязаны СТРОГО расти кадр за кадром: {timestamps:?}"
    );
    assert_eq!(
        calls.load(std::sync::atomic::Ordering::SeqCst) as usize,
        n_frames * 2 + 1,
        "часы обязаны читаться ровно дважды на каждый успешно распарсенный recv \
         (`local_ts_ns` до разбора, `parsed_ts_ns` сразу после) — не реже (одно \
         чтение на сессию не прошло бы строгий рост) и не чаще, плюс ровно одно \
         чтение на подключение до цикла приёма (доработка V5, 2026-09-24: старт \
         часа `last_market_ns`, см. комментарий в `run_with_backoff`)"
    );
}

/// FIX 5. `PriceNotOnTick` — то, чем `PLAN.md` (шаг 0.3) детектирует смену
/// тик-сайза биржи посреди записи; раньше эта ошибка `apply` глушилась в
/// ресинке молча, без единого события наружу.
#[tokio::test]
async fn price_not_on_tick_is_observable_and_triggers_resync() {
    let frames = vec![
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        // tick_e9 = 100_000 (0.0001) в test_cfg; "1.00005" даёт price_e9,
        // не кратный ему — ровно случай смены шага биржи посреди потока.
        Ok(Frame::Text(orderbook_msg(
            "delta",
            11,
            2,
            &[(1.00005, 6.0)],
            &[],
        ))),
    ];
    let (connector, sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 2).await; // Message(u=10) → BookInvariantViolated
    handle.abort();

    match &events[1] {
        ConnEvent::BookInvariantViolated { err, .. } => {
            assert!(
                matches!(err, ApplyError::PriceNotOnTick { .. }),
                "ожидался PriceNotOnTick, получено {err:?}"
            );
        }
        other => panic!("ожидался BookInvariantViolated, получено {other:?}"),
    }
    let resubscribes = sent
        .lock()
        .unwrap()
        .iter()
        .filter(|m| m.contains("orderbook.50.SOLUSDT"))
        .count();
    assert_eq!(
        resubscribes, 2,
        "PriceNotOnTick обязан запускать тот же ресинк, что и SequenceGap"
    );
}

/// FIX 5. `QtyNotOnStep` — тот же контракт `ApplyError`, что и
/// `PriceNotOnTick` (см. `book/mod.rs`), только по размеру, а не по цене.
#[tokio::test]
async fn qty_not_on_step_is_observable_and_triggers_resync() {
    let frames = vec![
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        // step_e9 = 1_000_000 (0.001) в test_cfg; "6.0001" не кратно ему.
        Ok(Frame::Text(orderbook_msg(
            "delta",
            11,
            2,
            &[(1.0, 6.0001)],
            &[],
        ))),
    ];
    let (connector, _sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 2).await;
    handle.abort();

    match &events[1] {
        ConnEvent::BookInvariantViolated { err, .. } => {
            assert!(
                matches!(err, ApplyError::QtyNotOnStep { .. }),
                "ожидался QtyNotOnStep, получено {err:?}"
            );
        }
        other => panic!("ожидался BookInvariantViolated, получено {other:?}"),
    }
}

/// FIX 5. `Crossed` — операционно самый интересный случай: книга уже
/// точно испорчена, а не просто временно рассинхронизирована разрывом `u`.
/// Раньше был невидим наружу этого файла точно так же, как и два других.
#[tokio::test]
async fn crossed_book_is_observable_and_triggers_resync() {
    let frames = vec![
        // Спред в один тик: бид 1.0, аск 1.0001.
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        // Новый бид 1.0002 выше старого аска 1.0001 — книга пересекается
        // сразу после применения бидов, аски в этой дельте не трогаем.
        Ok(Frame::Text(orderbook_msg(
            "delta",
            11,
            2,
            &[(1.0002, 6.0)],
            &[],
        ))),
    ];
    let (connector, _sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 2).await;
    handle.abort();

    match &events[1] {
        ConnEvent::BookInvariantViolated { err, .. } => {
            assert!(
                matches!(err, ApplyError::Crossed { .. }),
                "ожидался Crossed, получено {err:?}"
            );
        }
        other => panic!("ожидался BookInvariantViolated, получено {other:?}"),
    }
}
