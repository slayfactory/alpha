//! Строитель `Backtest` крейта и окна глубины для прогона по окнам.

use super::*;

// ---------------------------------------------------------------------------
// Отчёт на N профилей (история 32–34): медиана и p95 RTT, сравнение с
// таблицей профилей, вердикт G4.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Строитель `Backtest` крейта: движок целиком в одном месте.
// ---------------------------------------------------------------------------

/// Строит `Backtest` крейта: модель очереди и модель исполнения — по
/// `queue_model` (F3 плана 2026-09-20), комиссии
/// `costs::{MAKER_FEE_BPS, TAKER_FEE_BPS}`, задержка из замеренной RTT
/// (`latency_from_rtt`). `events` — уже переведённый в формат крейта поток;
/// перевод из `Feed`/площадки — забота вызывающего (`commands::lob::backtest`),
/// не этого файла (грепом-тест внизу).
///
/// # Три пути исполнения крейта (знать наизусть — они определяют, что
/// оптимистично, а что нет)
///
/// 1. **Сделки на нашей цене** — исполнение частичное, через модель очереди:
///    сделка двигает позицию в очереди (`QueueModel::trade`), снятия впереди —
///    `depth` с вероятностью `P` (`ProbQueueModel`); исполняется тот объём,
///    на который очередь ушла в минус, кратно шагу лота
///    (`PartialFillExchange::check_if_buy_filled`, `Ordering::Equal`). Честно,
///    с вероятностной поправкой на снятия впереди. У `RiskAdverse` тот же
///    путь, но позиция двигается только сделками, а исполняется заявка
///    целиком.
/// 2. **Сделка ниже нашей цены (для покупки; в стену)** — исполняет **весь
///    остаток** заявки: приоритет цены (`Ordering::Greater` → `fill(leaves_qty)`).
///    Верно для стоящей заявки.
/// 3. **Лучший аск опустился до нашей цены без сделки** — `on_best_ask_update`
///    исполняет **весь остаток** (продавец пересёк нас лимиткой; для продажи —
///    зеркально, `on_best_bid_update`). Верно по смыслу, но **оптимистично по
///    размеру**: крейт отдаёт весь остаток, а не объём лучшего уровня.
///    Счётчик кругов по этому пути — `Fill::fill_by_cross`
///    (`n_fill_by_cross` в `forms.csv`): крейт пути не отдаёт, `run_round`
///    определяет его по тому, что в буфере последних сделок шага нет сделки,
///    которая могла бы исполнить ногу (`trade_could_fill`).
///
/// Буфер последних сделок (`last_trades_capacity`) нужен только детектору
/// пути (3); читает его `run_round`, сразу после чтения очищая.
#[cfg(test)]
pub fn build_backtest(
    events: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
) -> Backtest<FastMarketDepth> {
    build_backtest_from(
        vec![DataSource::Data(Data::from_data(events))],
        exec_latency,
        move || FastMarketDepth::new(tick_size, lot_size),
        queue_model,
    )
}

/// Тот же движок, что `build_backtest`, но события **не копируются**: `Backtest`
/// читает их прямо из `events` и живёт только внутри `f`. Для сетки форм
/// (`lob bounce-grid`, S2): 48 `Backtest` над одними сутками держат одну копию
/// событий, а не 48 — с копиями восемь потоков × 15 млн × 64 Б валили процесс
/// («memory allocation of 963 859 520 bytes failed», ZEC день 2, 2026-09-18).
///
/// Почему это безопасно (проверено по исходнику `hftbacktest` 0.9.4,
/// `backtest/data/mod.rs`): `DataPtr::from_ptr` даёт `managed = false` — крейт
/// буфер не освобождает; `IndexMut` у `Data` объявлен, но в `backtest/` не
/// вызывается — буфер только читается; выравнивание `Vec<Event>` — `Event`,
/// как и требует `get_unchecked`. Время жизни держит замыкание: `Backtest` не
/// переживает `events`, потому что не покидает эту функцию.
pub fn with_backtest_over<R>(
    events: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
    f: impl FnOnce(&mut Backtest<FastMarketDepth>) -> R,
) -> R {
    // SAFETY: см. док выше — буфер жив до конца функции, крейт только читает.
    let data = unsafe { borrowed_data(events) };
    let mut bt = build_backtest_from(
        vec![DataSource::Data(data)],
        exec_latency,
        move || FastMarketDepth::new(tick_size, lot_size),
        queue_model,
    );
    let out = f(&mut bt);
    drop(bt);
    out
}

/// `Data` крейта поверх чужого среза без копии.
///
/// # Safety
/// `events` обязан пережить всё, что читает возвращённый `Data` (крейт его не
/// освобождает: `managed = false`, и не пишет — `IndexMut` в `backtest/` не
/// вызывается).
pub(super) unsafe fn borrowed_data(events: &[Event]) -> Data<Event> {
    let bytes = std::ptr::slice_from_raw_parts_mut(
        events.as_ptr() as *mut u8,
        std::mem::size_of_val(events),
    );
    unsafe { Data::from_data_ptr(DataPtr::from_ptr(bytes), 0) }
}

/// Снимок книги движка `FastMarketDepth` (Э-05: копия `HashMapMarketDepth` крейта) в момент `t0`: уровни и **все** поля
/// лучших/крайних тиков (строит своя книга окон `WindowDepth` — те же поля, К3;
/// `of` — эталон сверки). Из него фабрика `depth` строителя собирает книгу
/// обеих сторон движка окна ровно той формы, что была бы у сплошного прогона
/// после тех же строк (перекрещённые «спрятанные» уровни и границы поиска
/// лучшей цены — тоже; пересобирать книгу событиями нельзя: порядок их
/// применения меняет, какая сторона окажется спрятана).
#[derive(Debug, Clone, PartialEq)]
pub struct DepthSnapshot {
    pub bids: Vec<(i64, f64)>,
    pub asks: Vec<(i64, f64)>,
    pub best_bid_tick: i64,
    pub best_ask_tick: i64,
    pub low_bid_tick: i64,
    pub high_ask_tick: i64,
    pub timestamp: i64,
}

impl DepthSnapshot {
    pub fn of(d: &FastMarketDepth) -> Self {
        let mut bids: Vec<(i64, f64)> = d.bid_depth.iter().collect();
        let mut asks: Vec<(i64, f64)> = d.ask_depth.iter().collect();
        bids.sort_unstable_by_key(|(t, _)| *t);
        asks.sort_unstable_by_key(|(t, _)| *t);
        Self {
            bids,
            asks,
            best_bid_tick: d.best_bid_tick,
            best_ask_tick: d.best_ask_tick,
            low_bid_tick: d.low_bid_tick,
            high_ask_tick: d.high_ask_tick,
            timestamp: d.timestamp,
        }
    }

    pub fn build(&self, tick_size: f64, lot_size: f64) -> FastMarketDepth {
        let mut d = FastMarketDepth::new(tick_size, lot_size);
        d.bid_depth.extend(self.bids.iter().copied());
        d.ask_depth.extend(self.asks.iter().copied());
        d.best_bid_tick = self.best_bid_tick;
        d.best_ask_tick = self.best_ask_tick;
        d.low_bid_tick = self.low_bid_tick;
        d.high_ask_tick = self.high_ask_tick;
        d.timestamp = self.timestamp;
        d
    }

    pub fn levels(&self) -> usize {
        self.bids.len() + self.asks.len()
    }
}

/// Окно одного сигнала: книга после строк `[0..m)` и срез с `m`, где `m` —
/// первая строка, у которой **хотя бы одна** метка (`local_ts` или `exch_ts`)
/// больше `t0`.
#[derive(Debug, Clone, PartialEq)]
pub struct SignalWindow {
    pub t0_ns: i64,
    pub start: usize,
    pub depth: DepthSnapshot,
}

/// Окна всех сигналов суток — **один** проход книги по событиям, общий для
/// всех форм (у них одни касания) и для **обеих** сторон движка.
///
/// У крейта две книги: локальная применяет строки по `local_ts`, биржевая —
/// по `exch_ts`, каждая в порядке записи до первой строки с меткой больше
/// текущего времени. В `t0` они разные: часы коллектора и биржи расходятся в
/// любую сторону (в записи 2026-09-12 локальные **отстают**, и снимок «по
/// `local_ts`» отдавал бирже строки из будущего — цена входа расходилась).
/// Поэтому снимок берётся после строк `[0..m)`, где `m` — первая строка с
/// `local_ts > t0` **или** `exch_ts > t0`; строки `[m..)` идут в срез, и
/// каждая сторона на первом же `elapse` доберёт из них свои (метка `<= t0`)
/// ровно по разу — итог на обеих сторонах тот же, что у сплошного прогона.
#[derive(Debug, Clone)]
pub struct SignalWindows {
    pub tick_size: f64,
    pub lot_size: f64,
    pub(super) windows: Vec<SignalWindow>,
}

impl SignalWindows {
    /// Окна на своей книге целых тиков (`WindowDepth`, К3) — снимки те же, что у
    /// `build_crate`, по всем полям (разностные тесты и `bounce-grid --windows-check`).
    pub fn build<R: EventRows + ?Sized>(
        events: &R,
        t0s: &[i64],
        tick_size: f64,
        lot_size: f64,
    ) -> Self {
        Self::build_on(
            events,
            t0s,
            tick_size,
            lot_size,
            WindowDepth::new(tick_size, lot_size),
        )
    }

    /// Прежний путь — книга движка (`FastMarketDepth`, копия книги крейта) и `DepthSnapshot::of`: эталон
    /// разностной сверки К3 (условие Судьи b86eed6), в счёте не участвует.
    pub fn build_crate<R: EventRows + ?Sized>(
        events: &R,
        t0s: &[i64],
        tick_size: f64,
        lot_size: f64,
    ) -> Self {
        Self::build_on(
            events,
            t0s,
            tick_size,
            lot_size,
            FastMarketDepth::new(tick_size, lot_size),
        )
    }

    pub(super) fn build_on<R: EventRows + ?Sized, B: WindowBook>(
        events: &R,
        t0s: &[i64],
        tick_size: f64,
        lot_size: f64,
        mut depth: B,
    ) -> Self {
        let mut t0s: Vec<i64> = t0s.to_vec();
        t0s.sort_unstable();
        t0s.dedup();
        let mut row = 0usize;
        let mut windows = Vec::with_capacity(t0s.len());
        for t0 in t0s {
            while row < events.len()
                && events.row_local_ts(row) <= t0
                && events.row_exch_ts(row) <= t0
            {
                depth.apply(&events.row(row));
                row += 1;
            }
            windows.push(SignalWindow {
                t0_ns: t0,
                start: row,
                depth: depth.snapshot(),
            });
        }
        Self {
            tick_size,
            lot_size,
            windows,
        }
    }

    /// Первое расхождение с другими окнами тех же `t0` (`None` — все снимки равны по всем
    /// полям): `t0` окна и что разошлось.
    pub fn first_mismatch(&self, other: &SignalWindows) -> Option<(i64, &'static str)> {
        if self.windows.len() != other.windows.len() {
            return Some((0, "число окон"));
        }
        self.windows.iter().zip(&other.windows).find_map(|(a, b)| {
            let (x, y) = (&a.depth, &b.depth);
            let field = if a.t0_ns != b.t0_ns {
                "t0"
            } else if a.start != b.start {
                "start"
            } else if x.bids != y.bids {
                "bids"
            } else if x.asks != y.asks {
                "asks"
            } else if x.best_bid_tick != y.best_bid_tick {
                "best_bid_tick"
            } else if x.best_ask_tick != y.best_ask_tick {
                "best_ask_tick"
            } else if x.low_bid_tick != y.low_bid_tick {
                "low_bid_tick"
            } else if x.high_ask_tick != y.high_ask_tick {
                "high_ask_tick"
            } else if x.timestamp != y.timestamp {
                "timestamp"
            } else {
                return None;
            };
            Some((a.t0_ns, field))
        })
    }

    pub fn window_at(&self, t0_ns: i64) -> Option<&SignalWindow> {
        let i = self.windows.partition_point(|w| w.t0_ns < t0_ns);
        self.windows.get(i).filter(|w| w.t0_ns == t0_ns)
    }

    pub fn len(&self) -> usize {
        self.windows.len()
    }

    pub fn is_empty(&self) -> bool {
        self.windows.is_empty()
    }

    /// Уровней во всех снимках — оценка памяти окон (16 Б на уровень).
    pub fn levels_total(&self) -> usize {
        self.windows.iter().map(|w| w.depth.levels()).sum()
    }
}

/// Книга, которую `SignalWindows::build_on` проводит по событиям суток: своя (`WindowDepth`)
/// или эталон крейта.
pub(super) trait WindowBook {
    fn apply(&mut self, ev: &Event);
    fn snapshot(&self) -> DepthSnapshot;
}

impl WindowBook for WindowDepth {
    fn apply(&mut self, ev: &Event) {
        // Порядок веток — как у крейта; строк очистки в нашем переводе нет
        // (`events_from_feed` шлёт явные нули).
        if ev.is(LOCAL_BID_DEPTH_EVENT) {
            self.update_bid_depth(ev.px, ev.qty);
        } else if ev.is(LOCAL_ASK_DEPTH_EVENT) {
            self.update_ask_depth(ev.px, ev.qty);
        }
    }

    fn snapshot(&self) -> DepthSnapshot {
        WindowDepth::snapshot(self)
    }
}

impl WindowBook for FastMarketDepth {
    fn apply(&mut self, ev: &Event) {
        if ev.is(LOCAL_BID_DEPTH_EVENT) {
            self.update_bid_depth(ev.px, ev.qty, ev.local_ts);
        } else if ev.is(LOCAL_ASK_DEPTH_EVENT) {
            self.update_ask_depth(ev.px, ev.qty, ev.local_ts);
        }
    }

    fn snapshot(&self) -> DepthSnapshot {
        DepthSnapshot::of(self)
    }
}

/// Движок одного окна: книга обеих сторон — из снимка, события — срез суток с
/// первой строки после `t0` (без копии), часы прибиты к `t0` строкой-якорем.
///
/// Якорь — нулевая заявка бида по цене 0: у `FastMarketDepth` это
/// заведомо пустой ход (тика 0 в книге нет, лучшему он не равен, границы
/// поиска при нулевом объёме не трогаются), но это событие ленты, и первое
/// `elapse(0)` ставит часы окна ровно на `t0` — как `elapse(t0 - now)` в
/// сплошном прогоне. Нулевая **сделка** якорем быть не может: при пустой
/// стороне книги крейт считает `price_tick - best_bid_tick` от `i64::MIN`.
// Восемь аргументов — цена параметров окна (книга, время, срез, тик, лот,
// задержка, модель очереди, замыкание); структура ради одного лишнего поля
// усложнила бы вызывающего сильнее, чем читается этот список.
#[allow(clippy::too_many_arguments)]
pub fn with_backtest_over_window<R>(
    depth: &DepthSnapshot,
    t0_ns: i64,
    rest: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
    f: impl FnOnce(&mut Backtest<FastMarketDepth>) -> R,
) -> R {
    let anchor = [Event {
        ev: LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT,
        exch_ts: t0_ns,
        local_ts: t0_ns,
        px: 0.0,
        qty: 0.0,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }];
    let mut sources = vec![DataSource::Data(Data::from_data(&anchor))];
    if !rest.is_empty() {
        // SAFETY: `rest` жив до конца функции, `Backtest` умирает раньше
        // (`drop(bt)` ниже); крейт только читает, см. `borrowed_data`.
        sources.push(DataSource::Data(unsafe { borrowed_data(rest) }));
    }
    let snap = depth.clone();
    let mut bt = build_backtest_from(
        sources,
        exec_latency,
        move || snap.build(tick_size, lot_size),
        queue_model,
    );
    let out = f(&mut bt);
    drop(bt);
    out
}

/// Общий низ трёх строителей: `QueueModelKind` выбирает пару «модель очереди +
/// модель исполнения» (`ExchangeKind`) движка. Три пути исполнения крейта —
/// в doc-комментарии `build_backtest`.
pub(super) fn build_backtest_from(
    sources: Vec<DataSource<Event>>,
    exec_latency: ExecLatency,
    depth_builder: impl Fn() -> FastMarketDepth + 'static,
    queue_model: QueueModelKind,
) -> Backtest<FastMarketDepth> {
    // Обе ветки — одинаковый набор параметров, кроме пары очередь/исполнение:
    // `L2AssetBuilder` типизирован моделью очереди, поэтому ветка компилируется
    // в свой `Asset`, а `Backtest` стирает его в `dyn Processor`.
    let asset = match queue_model {
        QueueModelKind::RiskAdverse => L2AssetBuilder::default()
            .data(sources)
            .latency_model(MeasuredLatency(exec_latency))
            .asset_type(LinearAsset::new(1.0))
            .fee_model(TradingValueFeeModel::new(CommonFees::new(
                MAKER_FEE_BPS / 10_000.0,
                TAKER_FEE_BPS / 10_000.0,
            )))
            .last_trades_capacity(LAST_TRADES_CAPACITY)
            .queue_model(RiskAdverseQueueModel::new())
            .exchange(ExchangeKind::NoPartialFillExchange)
            .depth(depth_builder)
            .build()
            .unwrap(),
        QueueModelKind::Prob { n } => L2AssetBuilder::default()
            .data(sources)
            .latency_model(MeasuredLatency(exec_latency))
            .asset_type(LinearAsset::new(1.0))
            .fee_model(TradingValueFeeModel::new(CommonFees::new(
                MAKER_FEE_BPS / 10_000.0,
                TAKER_FEE_BPS / 10_000.0,
            )))
            .last_trades_capacity(LAST_TRADES_CAPACITY)
            .queue_model(ProbQueueModel::<PowerProbQueueFunc, FastMarketDepth>::new(
                PowerProbQueueFunc::new(n),
            ))
            .exchange(ExchangeKind::PartialFillExchange)
            .depth(depth_builder)
            .build()
            .unwrap(),
    };
    Backtest::builder().add_asset(asset).build().unwrap()
}
