use super::*;
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use tokio_tungstenite::tungstenite::Utf8Bytes;

mod handshake;
mod reconnect;
mod silence;
mod streams;

/// Транспорт по сценарию: очередь фреймов на приём плюс лог того, что
/// через него отправили. Единственная замена сети во всех тестах файла.
///
/// Пустая очередь — это НЕ конец сессии: `recv` зависает (`pending`),
/// как настоящий сокет, у которого просто пока нет данных. Закрытие
/// сессии — это явный `Ok(Frame::Closed)` в очереди, а не её исчерпание;
/// иначе тест на пинг никогда не дождался бы своего интервала — первый же
/// `recv` после последнего заскриптованного фрейма "закрывал" бы сессию.
struct ScriptedTransport {
    inbox: VecDeque<Result<Frame, TransportError>>,
    sent: Arc<Mutex<Vec<String>>>,
}

impl Transport for ScriptedTransport {
    fn send_text(
        &mut self,
        msg: String,
    ) -> impl Future<Output = Result<(), TransportError>> + Send {
        let sent = self.sent.clone();
        async move {
            sent.lock().unwrap().push(msg);
            Ok(())
        }
    }

    fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
        let next = self.inbox.pop_front();
        async move {
            match next {
                Some(item) => item,
                None => std::future::pending().await,
            }
        }
    }
}

/// Выдаёт по одному транспорту на подключение — так переподключение в
/// тесте наблюдаемо как новая сессия со своей очередью фреймов, а не
/// продолжение старой.
struct ScriptedConnector {
    sessions: VecDeque<VecDeque<Result<Frame, TransportError>>>,
    sent: Arc<Mutex<Vec<String>>>,
}

impl ScriptedConnector {
    fn new(sessions: Vec<Vec<Result<Frame, TransportError>>>) -> (Self, Arc<Mutex<Vec<String>>>) {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let sessions = sessions.into_iter().map(VecDeque::from).collect();
        (
            Self {
                sessions,
                sent: sent.clone(),
            },
            sent,
        )
    }
}

impl TransportConnector for ScriptedConnector {
    type Transport = ScriptedTransport;

    fn connect(
        &mut self,
    ) -> impl Future<Output = Result<ScriptedTransport, TransportError>> + Send {
        let next = self.sessions.pop_front();
        let sent = self.sent.clone();
        async move {
            match next {
                Some(inbox) => Ok(ScriptedTransport { inbox, sent }),
                None => Err(TransportError::Io(
                    "сценарий подключений исчерпан".to_string(),
                )),
            }
        }
    }
}

/// Транспорт, у которого проваливается ровно один по счёту вызов
/// `send_text` (1-based `fail_on_call`), остальные и `recv` ведут себя как
/// обычный сценарный транспорт. Нужен ровно для FIX 1: до этого двойника
/// ни один тест не мог провалить именно ресинковскую отправку — обычный
/// `ScriptedTransport::send_text` всегда возвращает `Ok`, — и путь «отправка
/// ресинка не удалась» в `handle_raw` был непроверяем в принципе.
struct SendFailsOnCall {
    inbox: VecDeque<Result<Frame, TransportError>>,
    send_calls: usize,
    fail_on_call: usize,
}

impl Transport for SendFailsOnCall {
    fn send_text(
        &mut self,
        _msg: String,
    ) -> impl Future<Output = Result<(), TransportError>> + Send {
        self.send_calls += 1;
        let should_fail = self.send_calls == self.fail_on_call;
        async move {
            if should_fail {
                Err(TransportError::Io(
                    "сценарий: эта конкретная отправка проваливается намеренно".to_string(),
                ))
            } else {
                Ok(())
            }
        }
    }

    fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
        let next = self.inbox.pop_front();
        async move {
            match next {
                Some(item) => item,
                None => std::future::pending().await,
            }
        }
    }
}

/// Отдаёт один-единственный заранее собранный транспорт, затем считает
/// сценарий исчерпанным. Смысл существования — обвязка для
/// `SendFailsOnCall`, которому (в отличие от `ScriptedConnector`) сессия
/// нужна ровно одна и с точным счётчиком отправок, а не очередь сессий.
struct SingleTransportConnector {
    transport: Option<SendFailsOnCall>,
}

impl TransportConnector for SingleTransportConnector {
    type Transport = SendFailsOnCall;

    fn connect(&mut self) -> impl Future<Output = Result<SendFailsOnCall, TransportError>> + Send {
        let next = self.transport.take();
        async move {
            match next {
                Some(t) => Ok(t),
                None => Err(TransportError::Io(
                    "единственная сессия уже выдана".to_string(),
                )),
            }
        }
    }
}

/// Коннектор, который на каждый `connect()` выдаёт новый транспорт,
/// закрывающийся немедленно (`Frame::Closed`, ни одного текстового кадра —
/// то самое "принял рукопожатие и тут же разорвал", которое проверяет
/// FIX 2). Session-по-сценарию (как у `ScriptedConnector`) здесь не
/// годится: конечная очередь рано или поздно кончается, и `connect()`
/// начинает возвращать `Err` — путь, который был корректен и ДО FIX 2
/// (растущий бэкофф на неудачном коннекте никогда не был багом), и он
/// замаскировал бы дефект, который тест обязан ловить, — бэкофф выглядел
/// бы растущим по совершенно другой, не относящейся к делу причине.
/// Поэтому сессии здесь не кончаются никогда. Момент вызова `connect()`
/// коннектор больше не запоминает — тест FIX 2 читает запрошенные
/// задержки бэкоффа через подменяемый `Backoff`, а не настоящие часы
/// (`std::time::Instant`), поэтому и считать не нужно.
struct AlwaysCloseImmediatelyConnector;

impl TransportConnector for AlwaysCloseImmediatelyConnector {
    type Transport = ScriptedTransport;

    async fn connect(&mut self) -> Result<ScriptedTransport, TransportError> {
        Ok(ScriptedTransport {
            inbox: VecDeque::from(vec![Ok(Frame::Closed)]),
            sent: Arc::new(Mutex::new(Vec::new())),
        })
    }
}

/// Часы, возвращающие строго возрастающий счётчик вместо времени. Нужны
/// FIX 4: монотонность, которую проверяли старые тесты (`<=`), проходит и
/// на константе — счётчик же гарантирует, что каждое отдельное показание
/// уникально и по порядку, а число вызовов `now_ns()` совпадает с числом
/// завершившихся `recv()`, ровно то свойство, которое обязано пережить
/// будущий рефакторинг "меньше системных вызовов".
#[derive(Clone)]
struct CountingClock {
    calls: Arc<std::sync::atomic::AtomicI64>,
}

impl CountingClock {
    fn new() -> (Self, Arc<std::sync::atomic::AtomicI64>) {
        let calls = Arc::new(std::sync::atomic::AtomicI64::new(0));
        (
            Self {
                calls: calls.clone(),
            },
            calls,
        )
    }
}

impl Clock for CountingClock {
    fn now_ns(&self) -> i64 {
        self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
    }
}

/// Бэкофф в тестах — миллисекунды, не секунды: тесты не про таймауты сети,
/// а про то, что происходит после переподключения, и ждать реальную
/// продовую задержку здесь незачем.
fn test_cfg(symbol: &str) -> ConnConfig {
    ConnConfig {
        symbol: symbol.to_string(),
        tick_e9: 100_000,   // 0.0001, тот же масштаб, что в тестах book/mod.rs
        step_e9: 1_000_000, // 0.001
        ping_interval: Duration::from_secs(3600), // вне фокуса большинства тестов
        // Таймаут приёма (V5) в большинстве тестов вне фокуса так же, как пинг:
        // сценарии со «замолчавшим» сокетом задают свой короткий порог сами.
        recv_timeout: Duration::from_secs(3600),
        backoff: BackoffConfig {
            initial: Duration::from_millis(1),
            max: Duration::from_millis(5),
            multiplier: 2,
        },
    }
}

/// Конфигурация коллектора сессии: то же соединение, но с двумя потоками
/// стакана (`SUBSCRIBED_DEPTHS`, T45). Одно-символьные вызывающие
/// (`lob record`, `pick::measure`) остаются на одном `.50` — этот шов и
/// проверяет, что набор потоков приходит из конфигурации, а не зашит в
/// `Connection`.
fn test_cfg_with_both_depths(symbol: &str) -> PoolConnConfig {
    let mut cfg: PoolConnConfig = test_cfg(symbol).into();
    assert_eq!(
        cfg.depths,
        vec![ORDERBOOK_DEPTH],
        "одно-символьный вызывающий обязан остаться на быстром потоке"
    );
    cfg.depths = SUBSCRIBED_DEPTHS.to_vec();
    cfg
}

fn orderbook_msg(
    kind: &str,
    u: u64,
    cts_ms: i64,
    bids: &[(f64, f64)],
    asks: &[(f64, f64)],
) -> Utf8Bytes {
    orderbook_msg_at(ORDERBOOK_DEPTH, kind, u, cts_ms, bids, asks)
}

/// То же сообщение из топика заданной глубины (T45): `.50` — быстрый поток,
/// `.200` — глубокий. Fixture для проверки, что разрыв `u` одного потока не
/// трогает книгу и ресинк другого.
fn orderbook_msg_at(
    depth: u32,
    kind: &str,
    u: u64,
    cts_ms: i64,
    bids: &[(f64, f64)],
    asks: &[(f64, f64)],
) -> Utf8Bytes {
    let render = |levels: &[(f64, f64)]| -> String {
        levels
            .iter()
            .map(|&(p, q)| format!(r#"["{p}","{q}"]"#))
            .collect::<Vec<_>>()
            .join(",")
    };
    format!(
        r#"{{"topic":"orderbook.{depth}.SOLUSDT","type":"{kind}","ts":{cts_ms},"data":{{"b":[{}],"a":[{}],"u":{u},"seq":{u}}},"cts":{cts_ms}}}"#,
        render(bids),
        render(asks)
    )
    .into()
}

fn trade_msg(exch_ms: i64) -> Utf8Bytes {
    format!(
        r#"{{"topic":"publicTrade.SOLUSDT","type":"snapshot","ts":1,"data":[{{"T":{exch_ms},"s":"SOLUSDT","S":"Buy","v":"1.0","p":"150.00","L":"PlusTick","i":"x","BT":false}}]}}"#
    )
    .into()
}

/// Ждёт ровно `n` событий с таймаутом: без него баг в реализации вешает
/// не один тест, а всю команду `cargo test` до ручной остановки.
async fn collect_n(rx: &mut mpsc::Receiver<ConnEvent>, n: usize) -> Vec<ConnEvent> {
    let mut out = Vec::with_capacity(n);
    for _ in 0..n {
        let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
            .await
            .expect("не дождались события за 5с — соединение зависло")
            .expect("канал закрылся раньше, чем пришли все ожидаемые события");
        out.push(ev);
    }
    out
}

/// Приёмник, который помнит **индекс инструмента** каждого события: у отказа
/// подписки (A8.3) вся суть в привязке к топику, а обычный
/// `mpsc::Sender<ConnEvent>` её теряет по построению (его `ConnSink` индекс
/// игнорирует).
struct IndexSink(mpsc::Sender<(u16, ConnEvent)>);

impl ConnSink for IndexSink {
    async fn send_event(&self, symbol_idx: u16, ev: ConnEvent) {
        let _ = self.0.send((symbol_idx, ev)).await;
    }
}

/// Соединение с двумя инструментами — тот же вид, что у коллектора сессии
/// (один сокет несёт топики многих инструментов).
fn test_pool_cfg_two_symbols() -> PoolConnConfig {
    PoolConnConfig {
        symbols: vec![
            SymbolSpec {
                symbol: "AAAUSDT".to_string(),
                tick_e9: 100_000,
                step_e9: 1_000_000,
                index: 0,
            },
            SymbolSpec {
                symbol: "BBBUSDT".to_string(),
                tick_e9: 100_000,
                step_e9: 1_000_000,
                index: 1,
            },
        ],
        depths: vec![ORDERBOOK_DEPTH],
        ping_interval: Duration::from_secs(3600),
        recv_timeout: Duration::from_secs(3600),
        backoff: BackoffConfig {
            initial: Duration::from_millis(1),
            max: Duration::from_millis(5),
            multiplier: 2,
        },
    }
}

fn snapshot_msg(symbol: &str, u: u64) -> Utf8Bytes {
    format!(
        r#"{{"topic":"orderbook.50.{symbol}","type":"snapshot","ts":1,"data":{{"b":[["1.0","5.0"]],"a":[["1.0001","4.0"]],"u":{u},"seq":{u}}}}}"#
    )
    .into()
}
