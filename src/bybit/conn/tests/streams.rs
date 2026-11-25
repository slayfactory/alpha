//! Подписки, метки времени, разрывы последовательности и пересинхронизация.

use super::*;

/// A8.3 (замер 2026-09-18): ответ биржи на подписку с `success:false` обязан
/// стать `ConnEvent::SubscribeFailed` **с индексом инструмента из топика**, а
/// не остаться служебным сообщением. Сокет несёт два инструмента: отказ по
/// топику второго приходит индексом 1, а первый продолжает жить — его снапшот
/// доходит следующим событием.
#[tokio::test]
async fn refused_subscription_is_attributed_to_the_topic_instrument_and_neighbours_live() {
    let refused = r#"{"success":false,"ret_msg":"error:handler not found,topic:orderbook.50.BBBUSDT","conn_id":"c","req_id":"","op":"subscribe"}"#;
    let frames = vec![
        Ok(Frame::Text(refused.to_string().into())),
        Ok(Frame::Text(snapshot_msg("AAAUSDT", 1))),
    ];
    let (connector, _sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(
        Connection::new(connector, test_pool_cfg_two_symbols()).run(SystemClock, IndexSink(tx)),
    );

    let first = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("отказ подписки обязан дойти, а не потеряться как служебное")
        .expect("канал жив");
    match first {
        (1, ConnEvent::SubscribeFailed { topic, ret_msg, .. }) => {
            assert_eq!(
                topic.as_deref(),
                Some("orderbook.50.BBBUSDT"),
                "индекс события — инструмент топика из отказа"
            );
            assert!(ret_msg.contains("handler not found"), "{ret_msg}");
        }
        other => panic!("ждали SubscribeFailed индекса 1, получили {other:?}"),
    }
    // Сосед по сокету не задет: его снапшот идёт обычным рыночным событием.
    let second = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("сосед обязан продолжить работу")
        .expect("канал жив");
    match second {
        (0, ConnEvent::Message { event, .. }) => {
            assert!(matches!(event, Event::Book(_)), "{event:?}");
        }
        other => panic!("ждали рыночное событие индекса 0, получили {other:?}"),
    }
    handle.abort();
}

/// A8.3: отказ по топику, которого нет в пуле этого сокета, привязывается к
/// сокету (первому инструменту) — как события без символа; молчания нет.
#[tokio::test]
async fn refused_subscription_with_an_unknown_topic_falls_back_to_the_socket() {
    let refused = r#"{"success":false,"ret_msg":"error:handler not found,topic:orderbook.50.CCCUSDT","op":"subscribe"}"#;
    let (connector, _sent) =
        ScriptedConnector::new(vec![vec![Ok(Frame::Text(refused.to_string().into()))]]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(
        Connection::new(connector, test_pool_cfg_two_symbols()).run(SystemClock, IndexSink(tx)),
    );

    let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("отказ обязан дойти и с чужим топиком")
        .expect("канал жив");
    match ev {
        (0, ConnEvent::SubscribeFailed { topic, .. }) => {
            assert_eq!(topic.as_deref(), Some("orderbook.50.CCCUSDT"));
        }
        other => panic!("ждали SubscribeFailed индекса 0 (сокет), получили {other:?}"),
    }
    handle.abort();
}

#[tokio::test]
async fn local_ts_is_stamped_before_parse_and_is_monotonic_across_messages() {
    let frames = vec![
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        // Не JSON вовсе: разбор обязан упасть, метка — нет.
        Ok(Frame::Text("not json at all".to_string().into())),
        Ok(Frame::Text(orderbook_msg(
            "delta",
            11,
            2,
            &[(1.0, 6.0)],
            &[],
        ))),
    ];
    let (connector, _sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 3).await; // 2 Message + 1 ParseFailed
    handle.abort();

    let mut timestamps = Vec::new();
    for ev in &events {
        match ev {
            ConnEvent::Message { local_ts_ns, .. } => timestamps.push(*local_ts_ns),
            ConnEvent::ParseFailed { local_ts_ns, .. } => timestamps.push(*local_ts_ns),
            other => panic!("неожиданное событие: {other:?}"),
        }
    }
    assert_eq!(
        timestamps.len(),
        3,
        "метка обязана стоять и на нераспарсенном фрейме — иначе она берётся после разбора"
    );
    assert!(
        timestamps.windows(2).all(|w| w[0] <= w[1]),
        "local_ts обязана быть монотонна: {timestamps:?}"
    );
}

#[tokio::test]
async fn exch_ts_never_exceeds_local_ts_for_any_forwarded_message() {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64;
    let frames = vec![
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            now_ms - 5,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        Ok(Frame::Text(trade_msg(now_ms - 3))),
        Ok(Frame::Text(orderbook_msg(
            "delta",
            11,
            now_ms - 1,
            &[(1.0, 6.0)],
            &[],
        ))),
    ];
    let (connector, _sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 3).await;
    handle.abort();

    for ev in events {
        match ev {
            ConnEvent::Message {
                local_ts_ns, event, ..
            } => {
                let exch_ts_ns = match event {
                    Event::Book(u) => u.cts_ms * 1_000_000,
                    Event::Trade(t) => t.exch_ms * 1_000_000,
                    // Служебные сообщения времени матчинга не несут (A8.3
                    // добавил к ним отказ подписки — он приходит своим
                    // `ConnEvent::SubscribeFailed`, не через `Message`).
                    Event::Other | Event::SubscribeFailed { .. } => continue,
                };
                assert!(
                    exch_ts_ns <= local_ts_ns,
                    "exch_ts {exch_ts_ns} > local_ts {local_ts_ns}"
                );
            }
            other => panic!("ожидалось Message, получено {other:?}"),
        }
    }
}

#[tokio::test]
async fn sequence_gap_triggers_resubscribe_and_wait_for_resnapshot_not_silent_continuation() {
    let frames = vec![
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))), // u=10, ok
        Ok(Frame::Text(orderbook_msg(
            "delta",
            12,
            2,
            &[(1.0, 6.0)],
            &[],
        ))), // разрыв: ждали 11, пришло 12
        Ok(Frame::Text(orderbook_msg(
            "delta",
            13,
            3,
            &[(1.0, 7.0)],
            &[],
        ))), // всё ещё в разрыве — повторной подписки быть не должно
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            20,
            4,
            &[(2.0, 1.0)],
            &[(2.0001, 1.0)],
        ))), // ресинк — поток возобновляется
    ];
    let (connector, sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    // Ровно три события: снапшот u=10, разрыв, ресинк-снапшот u=20.
    // Обе гэпнутые дельты (u=12, u=13) не порождают ни Message, ни
    // повторного SequenceGap — это и есть "не продолжать молча".
    let events = collect_n(&mut rx, 3).await;
    handle.abort();

    match &events[0] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!(u.u, 10),
        other => panic!("ожидался снапшот u=10, получено {other:?}"),
    }
    assert_eq!(
        events[1],
        ConnEvent::SequenceGap {
            depth: 50,
            expected: 11,
            got: 12
        }
    );
    match &events[2] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!(u.u, 20),
        other => panic!("ожидался ресинк-снапшот u=20, получено {other:?}"),
    }

    let sent = sent.lock().unwrap();
    let resubscribes = sent
        .iter()
        .filter(|m| m.contains("orderbook.50.SOLUSDT"))
        .count();
    assert_eq!(
        resubscribes, 2,
        "изначальная подписка плюс ровно один ресинк — не по подписке на каждую гэпнутую дельту"
    );
}

/// T45: потоки глубины независимы. Разрыв `u` в глубоком потоке (`.200`)
/// обязан дать `SequenceGap` **своей** глубины, ресинк-подписку **своего**
/// топика и не тронуть быстрый поток: следующая дельта `.50` идёт дальше
/// как ни в чём не бывало. Обратное — то же самое, потому что состояние
/// (`books`/`resyncing`) хранится на пару (инструмент, поток), а не на
/// инструмент.
#[tokio::test]
async fn gap_in_one_depth_stream_resyncs_only_that_stream() {
    let frames = vec![
        // Быстрый поток: снапшот u=10 и следующая дельта u=11.
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEPTH,
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        // Глубокий поток: снапшот u=5, затем дельта u=7 — разрыв (ждали 6).
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEEP_DEPTH,
            "snapshot",
            5,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEEP_DEPTH,
            "delta",
            7,
            2,
            &[(1.0, 6.0)],
            &[],
        ))),
        // Быстрый поток продолжается: его `u` не терялся, ресинка быть не
        // должно, и событие обязано дойти до канала.
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEPTH,
            "delta",
            11,
            3,
            &[(1.0, 7.0)],
            &[],
        ))),
        // Ресинк глубокого потока: свежий снапшот u=100.
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEEP_DEPTH,
            "snapshot",
            100,
            4,
            &[(2.0, 1.0)],
            &[(2.0001, 1.0)],
        ))),
    ];
    let (connector, sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(
        Connection::new(connector, test_cfg_with_both_depths("SOLUSDT")).run(SystemClock, tx),
    );

    // Пять событий: .50 снапшот, .200 снапшот, разрыв .200, .50 дельта,
    // .200 ресинк-снапшот. Дельты .200 между разрывом и снапшотом наружу не
    // идут, лишних `SequenceGap` тоже нет.
    let events = collect_n(&mut rx, 5).await;
    handle.abort();

    match &events[0] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => {
            assert_eq!((u.depth, u.u), (ORDERBOOK_DEPTH, 10));
        }
        other => panic!("ожидался снапшот .50 u=10, получено {other:?}"),
    }
    match &events[1] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => {
            assert_eq!((u.depth, u.u), (ORDERBOOK_DEEP_DEPTH, 5));
        }
        other => panic!("ожидался снапшот .200 u=5, получено {other:?}"),
    }
    assert_eq!(
        events[2],
        ConnEvent::SequenceGap {
            depth: ORDERBOOK_DEEP_DEPTH,
            expected: 6,
            got: 7
        },
        "разрыв обязан быть помечен своим потоком"
    );
    match &events[3] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!(
            (u.depth, u.u),
            (ORDERBOOK_DEPTH, 11),
            "разрыв .200 не имеет права глушить .50"
        ),
        other => panic!("ожидалась дельта .50 u=11, получено {other:?}"),
    }
    match &events[4] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!((u.depth, u.u), (ORDERBOOK_DEEP_DEPTH, 100)),
        other => panic!("ожидался ресинк-снапшот .200 u=100, получено {other:?}"),
    }

    let sent = sent.lock().unwrap();
    let fast_resubscribes = sent
        .iter()
        .filter(|m| m.contains(r#""orderbook.50.SOLUSDT""#))
        .count();
    let deep_resubscribes = sent
        .iter()
        .filter(|m| m.contains(r#""orderbook.200.SOLUSDT""#))
        .count();
    assert_eq!(
        fast_resubscribes, 1,
        "быстрый поток подписан один раз (общий sub_pool) и ни разу не ресинкан"
    );
    assert_eq!(
        deep_resubscribes, 2,
        "глубокий поток: общий sub_pool плюс ровно один ресинк — не по подписке на дельту"
    );
}

/// Одно-символьные вызывающие (`commands::record`, `pick::measure`) ведут
/// одну книгу и один бинлог — их соединение подписано только на `.50`
/// (`From<ConnConfig>`). Приди такому соединению кадр `.200`, он обязан стать
/// `Unrouted`, а не лечь в ту же книгу вторым потоком: у `.200` своя
/// последовательность `u`, и смешение выглядело бы разрывом на каждом втором
/// сообщении (T45).
#[tokio::test]
async fn deep_topic_is_unrouted_on_a_single_depth_connection() {
    let frames = vec![Ok(Frame::Text(orderbook_msg_at(
        ORDERBOOK_DEEP_DEPTH,
        "snapshot",
        5,
        1,
        &[(1.0, 5.0)],
        &[(1.0001, 4.0)],
    )))];
    let (connector, sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 1).await;
    handle.abort();
    match &events[0] {
        ConnEvent::Unrouted { local_ts_ns } => {
            assert!(*local_ts_ns >= 0, "метка кадра обязана быть настоящей");
        }
        other => {
            panic!("кадр не нашего потока наружу как рынок не идёт, ожидался Unrouted: {other:?}")
        }
    }
    let sent = sent.lock().unwrap();
    assert!(
        sent.iter().all(|m| !m.contains("orderbook.200.SOLUSDT")),
        "в подписке одно-символьного соединения глубокого топика быть не должно: {sent:?}"
    );
}

#[tokio::test]
async fn u_equals_one_mid_stream_is_delegated_to_book_as_a_restart_not_a_gap() {
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
            1, // рестарт сервиса Bybit
            3,
            &[(0.5, 2.0)],
            &[(0.5001, 2.0)],
        ))),
        Ok(Frame::Text(orderbook_msg(
            "delta",
            2, // продолжение уже от u=1 — валидно, только если рестарт принят
            4,
            &[(0.5, 3.0)],
            &[],
        ))),
    ];
    let (connector, sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 4).await;
    handle.abort();

    // Все четыре — Message, ни одного SequenceGap: рестарт не ошибка.
    // Четвёртое событие (u=2) проходит только если `Book` приняла u=1 как
    // новую точку отсчёта — иначе оно само оказалось бы разрывом.
    for (i, ev) in events.iter().enumerate() {
        assert!(
            matches!(ev, ConnEvent::Message { .. }),
            "событие {i} не Message: {ev:?}"
        );
    }
    match &events[2] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!(u.u, 1),
        other => panic!("{other:?}"),
    }
    match &events[3] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!(u.u, 2),
        other => panic!("{other:?}"),
    }

    let sent = sent.lock().unwrap();
    let resubscribes = sent
        .iter()
        .filter(|m| m.contains("orderbook.50.SOLUSDT"))
        .count();
    assert_eq!(
        resubscribes, 1,
        "рестарт сервиса не должен вызывать ресинк по сети — это не разрыв последовательности"
    );
}

#[tokio::test]
async fn socket_close_triggers_reconnect_that_resumes_and_resubscribes() {
    let session1 = vec![
        Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        Ok(Frame::Closed),
    ];
    let session2 = vec![Ok(Frame::Text(orderbook_msg(
        "snapshot",
        5,
        1,
        &[(3.0, 1.0)],
        &[(3.0001, 1.0)],
    )))];
    let (connector, sent) = ScriptedConnector::new(vec![session1, session2]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    // Message(u=10) → Disconnected → Message(u=5, уже вторая сессия).
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
        ConnEvent::Disconnected {
            first_of_socket: true
        }
    );
    match &events[2] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!(u.u, 5),
        other => panic!("{other:?}"),
    }

    let sent = sent.lock().unwrap();
    let subscribes = sent
        .iter()
        .filter(|m| m.contains("orderbook.50.SOLUSDT"))
        .count();
    assert_eq!(
        subscribes, 2,
        "переподключение обязано подписаться заново, а не переиспользовать старую подписку"
    );
}
