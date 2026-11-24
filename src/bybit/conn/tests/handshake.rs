//! Рукопожатие, повторные отказы, тишина сокета.

use super::*;

/// K1 (2026-09-17): отказ рукопожатия больше не молчит. Коннектор, который
/// всегда отвечает `HTTP 403` (гео-блок), обязан дать событие `ConnectFailed`
/// с **самим статусом** и номером попытки: до правки ошибка проглатывалась в
/// `Err(_)`, и устойчивый 403/429 выглядел как вечно тихий ретрай — ни строки
/// `gaps.csv`, ни счётчика.
#[tokio::test]
async fn failed_handshake_is_reported_with_its_http_status() {
    struct Http403Connector;
    impl TransportConnector for Http403Connector {
        type Transport = ScriptedTransport;
        async fn connect(&mut self) -> Result<ScriptedTransport, TransportError> {
            Err(TransportError::Http {
                status: 403,
                msg: "Forbidden".to_string(),
            })
        }
    }

    let (tx, mut rx) = mpsc::channel(16);
    let handle =
        tokio::spawn(Connection::new(Http403Connector, test_cfg("SOLUSDT")).run(SystemClock, tx));
    let events = collect_n(&mut rx, 1).await;
    handle.abort();

    match &events[0] {
        ConnEvent::ConnectFailed {
            attempt,
            http_status,
            err,
            ..
        } => {
            assert_eq!(*http_status, Some(403), "{err}");
            assert_eq!(*attempt, 1, "нумерация попыток с единицы");
            assert!(err.contains("Forbidden"), "{err}");
        }
        other => panic!("ожидался ConnectFailed, получено {other:?}"),
    }
    // Обрыв (таймаут/TLS) статуса не имеет — и это не `Some(0)`.
    assert_eq!(
        TransportError::Io("обрыв".to_string()).http_status(),
        None,
        "у обрыва нет HTTP-статуса"
    );
}

/// A8.5 (2026-09-18): сеть пропала на часы — серия отказов подряд. Каждый отказ
/// виден (`ConnectFailed` со своим номером попытки), а причины **отличимы**:
/// HTTP-отказ несёт статус (403/429), DNS-отказ статуса не имеет — только текст
/// (`TransportError::Io`). Восстановление — не «продолжаем как ни в чём не
/// бывало»: новый сокет подписывается заново, и данные возвращаются обычным
/// `Message` (снапшот).
#[tokio::test]
async fn repeated_connect_failures_are_each_reported_and_a_recovered_socket_resubscribes() {
    /// Коннектор с очередью отказов: первые `connect()` падают, следующий
    /// отдаёт сценарий кадров — «сеть вернулась».
    struct FlakyConnectConnector {
        failures: VecDeque<TransportError>,
        frames: Option<VecDeque<Result<Frame, TransportError>>>,
        sent: Arc<Mutex<Vec<String>>>,
    }
    impl TransportConnector for FlakyConnectConnector {
        type Transport = ScriptedTransport;
        fn connect(
            &mut self,
        ) -> impl Future<Output = Result<ScriptedTransport, TransportError>> + Send {
            let failure = self.failures.pop_front();
            // Кадры забираются только удачной попытке: иначе первый же отказ
            // съел бы сценарий, и «восстановление» осталось бы без данных.
            let frames = if failure.is_some() {
                None
            } else {
                self.frames.take()
            };
            let sent = self.sent.clone();
            async move {
                if let Some(err) = failure {
                    return Err(err);
                }
                Ok(ScriptedTransport {
                    inbox: frames.unwrap_or_default(),
                    sent,
                })
            }
        }
    }

    let sent = Arc::new(Mutex::new(Vec::new()));
    let connector = FlakyConnectConnector {
        failures: VecDeque::from(vec![
            TransportError::Http {
                status: 429,
                msg: "Too Many Requests".to_string(),
            },
            // DNS-отказ: адрес не разрешился, HTTP-статуса нет.
            TransportError::Io("dns: failed to lookup address information".to_string()),
        ]),
        frames: Some(VecDeque::from(vec![Ok(Frame::Text(orderbook_msg(
            "snapshot",
            1,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        )))])),
        sent: sent.clone(),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 3).await;
    handle.abort();

    match &events[0] {
        ConnEvent::ConnectFailed {
            attempt,
            http_status,
            err,
            ..
        } => {
            assert_eq!(*attempt, 1, "первая попытка");
            assert_eq!(*http_status, Some(429), "429 отличим от 403 и от DNS");
            assert!(err.contains("Too Many"), "{err}");
        }
        other => panic!("ждали ConnectFailed, получили {other:?}"),
    }
    match &events[1] {
        ConnEvent::ConnectFailed {
            attempt,
            http_status,
            err,
            ..
        } => {
            assert_eq!(
                *attempt, 2,
                "номер попытки растёт, пока сессия не была полезной"
            );
            assert_eq!(
                *http_status, None,
                "DNS-отказ статуса не имеет — отличается от 403/429"
            );
            assert!(err.contains("lookup"), "{err}");
        }
        other => panic!("ждали ConnectFailed, получили {other:?}"),
    }
    match &events[2] {
        ConnEvent::Message { event, .. } => {
            assert!(
                matches!(event, Event::Book(u) if u.is_snapshot),
                "после восстановления приходит снапшот: {event:?}"
            );
        }
        other => panic!("ждали снапшот после восстановления, получили {other:?}"),
    }
    // Восстановленный сокет подписался заново — «вернулись» не молча.
    let sent = sent.lock().unwrap().clone();
    assert!(
        sent.iter().any(|m| m.contains("orderbook.50.SOLUSDT")),
        "подписка после восстановления обязана уйти: {sent:?}"
    );
}

/// V5 (2026-09-17): полуживое соединение — TCP жив, данных нет — раньше висело
/// бесконечно. Порог приёма — 2 × интервал пинга; здесь транспорт молчит
/// (`pending()` на пустом ящике), поэтому событие обязано прийти, а не висеть.
#[tokio::test]
async fn silent_socket_times_out_and_reports_disconnected() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.ping_interval = Duration::from_secs(3600); // в фокусе — приём, не пинг
    cfg.recv_timeout = Duration::from_millis(40);
    let (connector, _sent) = ScriptedConnector::new(vec![Vec::new()]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

    let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("молчание длиннее 2 × пинга обязано дать событие, а не висеть")
        .expect("канал жив");
    handle.abort();

    assert!(
        matches!(
            ev,
            ConnEvent::Disconnected {
                first_of_socket: true
            }
        ),
        "молчащий сокет уходит в переподключение тем же путём, что обрыв: {ev:?}"
    );
}

/// Доработка V5 (2026-09-24), пункт (б): неудавшаяся ПЕРВАЯ подписка
/// (`ws::sub_pool`, отправка №1 — см. комментарий у `resync_resubscribe_
/// send_failure_still_emits_disconnected`) раньше отступала молча: бэкофф и
/// повтор без единого события наружу — в отличие от двух других отправок
/// этого файла (пинг, ресинк), которые уже шлют `Disconnected` на той же
/// неудаче. `SendFailsOnCall`/`SingleTransportConnector` — та же пара
/// двойников, что и у FIX 1, `fail_on_call: 1` бьёт именно по этой отправке.
#[tokio::test]
async fn failed_initial_subscription_emits_disconnected_before_backoff() {
    let connector = SingleTransportConnector {
        transport: Some(SendFailsOnCall {
            inbox: VecDeque::new(),
            send_calls: 0,
            fail_on_call: 1,
        }),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("неудавшаяся подписка обязана дать Disconnected, а не тихий бэкофф")
        .expect("канал жив");
    handle.abort();

    assert_eq!(
        ev,
        ConnEvent::Disconnected {
            first_of_socket: true
        },
        "неудавшаяся начальная подписка — тоже смерть сокета, как ресинк и пинг"
    );
}
