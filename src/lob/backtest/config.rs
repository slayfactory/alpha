//! Настройка и итоги прогона: модель очереди, `DriveConfig`, трасса шагов, сигнал сделки-отскока и результат `BounceRun`.

use super::*;

// ---------------------------------------------------------------------------
// Прогон одного профиля поверх `Bot<MD>`, мотором — `strategy::on_event`.
// ---------------------------------------------------------------------------

/// Модель очереди и модель исполнения бэктестера (F3 плана 2026-09-20):
/// выбор крейта `hftbacktest 0.9.4` — модель очереди и `ExchangeKind` за ней.
///
/// Три пути исполнения крейта и их оптимизм — в doc-комментарии
/// `build_backtest`; счётчик кругов по пути (3) — `Fill::fill_by_cross`
/// (`n_fill_by_cross` в `forms.csv`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum QueueModelKind {
    /// Прежний движок Decision 20: `RiskAdverseQueueModel` +
    /// `NoPartialFillExchange`. Позиция в очереди двигается **только**
    /// сделками на нашей цене, исполнение — целой заявкой. Числа прежних
    /// прогонов (в том числе `qty` круга) не меняются — гейт F3.
    RiskAdverse,
    /// Модель очереди **по объёму**: `ProbQueueModel<PowerProbQueueFunc(n)>` +
    /// `PartialFillExchange`. Позиция в очереди двигается сделками и
    /// снятиями впереди с вероятностью `P = f(back) / (f(back) + f(front))`,
    /// `f(x) = xⁿ`; исполнение частичное. `n` — число предрегистрации
    /// (`--queue-model prob:<n>`); у крейта в примерах 3.0 — это **не** наше
    /// умолчание, умолчания нет вовсе.
    Prob { n: f64 },
}

/// Ёмкость буфера последних сделок крейта на шаг опроса, элементов (F3):
/// `last_trades_capacity`. Это **не** число сделки и не модельная величина, а
/// подсказка `Vec::with_capacity` — буфер растёт при необходимости; ёмкость
/// обязана быть больше нуля, иначе крейт вовсе не пишет сделки
/// (`proc/local.rs`: `ev.is(LOCAL_TRADE_EVENT) && self.trades.capacity() > 0`)
/// и путь исполнения (3) не определить. Читают буфер двое, оба — на одном
/// шаге и до очистки: детектор `run_round` (путь исполнения F3) и
/// `StrategyState::observe_wall_trades` (F7, Б-75 — съеденное в стену);
/// сразу после чтения буфер очищается.
pub(super) const LAST_TRADES_CAPACITY: usize = 64;

impl QueueModelKind {
    /// Имя модели для шапки `forms.csv`/`manifest.txt` — обратная запись
    /// флага: `risk-adverse` или `prob:<n>`.
    pub fn label(self) -> String {
        match self {
            QueueModelKind::RiskAdverse => "risk-adverse".to_string(),
            QueueModelKind::Prob { n } => format!("prob:{n}"),
        }
    }

    /// Разбор значения флага `--queue-model`: `risk-adverse` | `prob:<n>`.
    /// `n` — конечное положительное число (домен степенной функции очереди);
    /// умолчания нет — изобретённое число запрещено, `n` приходит флагом.
    pub fn parse(s: &str) -> Result<Self, String> {
        let s = s.trim();
        if s == "risk-adverse" {
            return Ok(QueueModelKind::RiskAdverse);
        }
        let Some(rest) = s.strip_prefix("prob:") else {
            return Err(format!(
                "--queue-model {s:?}: ожидается risk-adverse или prob:<n>"
            ));
        };
        let n: f64 = rest
            .parse()
            .map_err(|_| format!("--queue-model {s:?}: n не число"))?;
        if !n.is_finite() || n <= 0.0 {
            return Err(format!(
                "--queue-model {s:?}: n обязано быть конечным и больше нуля"
            ));
        }
        Ok(QueueModelKind::Prob { n })
    }
}

/// `--queue-model` разбирается `clap`'ом прямо в тип (аудит 21.09, С8) — как
/// `ExecLatency`; текст ошибки — тот же, что у `parse`.
impl std::str::FromStr for QueueModelKind {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Self::parse(s)
    }
}

/// Настройки прогона профиля.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DriveConfig {
    /// TK-115 Г-112 (TK-132 С-03): окно журнала ленты, секунды (`bounce-grid --tape-log`); 0 — выключено. Идёт из
    /// плана в состояние круга (`StrategyState::enable_tape`), глобального входа нет.
    pub tape_log_secs: u32,
    /// TK-272: окна `W` (секунды) журнала ленты за один проход (`bounce-grid --tape-log-ws`); 0 — слот не занят.
    pub tape_log_ws: [u32; TAPE_WS_MAX],
    /// Размер круга. План: минимальный лот биржи (Decision 22).
    pub order_qty: f64,
    /// Первый идентификатор ордеров; дальше — по порядку, каждый ордер
    /// уникален (требование трейта).
    pub first_order_id: u64,
    /// Модель очереди/исполнения этого прогона (F3): её ставит вызывающий
    /// (`lob bounce-grid --queue-model`), умолчания нет.
    pub queue_model: QueueModelKind,
    /// Пропуск «позиция занята» (T-31): `true` — прежнее поведение, сигнал до `idle_ns` прошлого круга —
    /// промах `PositionBusy`. `false` (`bounce-grid --busy-skip off`, только драйвер сетапов, где круг —
    /// свежий движок над окном `t0`): каждый сигнал — свой круг, обрыв суток счёт не прерывает, а след
    /// каждого шага (`BounceRun::trace`) уходит в `signals.csv` — занятость потом решает фильтр
    /// (`tools/compute/busy-replay.py`) тем же правилом.
    pub busy_skip: bool,
    /// Пропуск пустых шагов опроса в удержании (Э-04б, T-38, `bounce-grid --hold-step skip`): в фазе
    /// `Holding` без заявок круг ждёт следующего события ленты (`wait_next_feed`) и встаёт на ту же точку
    /// сетки `ON_EVENT_POLL_STEP_NS`, что и пошаговый опрос; пороги времени плана (досрочный выход, дедлайн)
    /// посещаются. Итог байт в байт тот же, что у `false` (прежний шаг 10 мс на каждой точке сетки).
    /// Только драйвер окон (знает конец данных круга); полный прогон суток шагает как прежде.
    pub hold_skip: bool,
}

/// Чем кончился шаг драйвера на сигнале (след T-31, `signals.csv`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TraceStep {
    /// Книга не была готова — попытки не было (`SignalStep::NotSubmitted`).
    NotSubmitted,
    /// Вход не исполнился и снят (TTL, стена, цена ушла, отказ биржи).
    TimedOut,
    Filled,
    /// Круг не сошёлся (`RoundOutcome::Inconsistent`).
    Inconsistent,
    /// Запись кончилась: при `busy_skip` здесь сутки обрываются.
    EndOfData,
    /// Окна для `t0` нет (драйвер сетапов): при `busy_skip` здесь сутки обрываются.
    NoWindow,
}

impl TraceStep {
    pub fn label(self) -> &'static str {
        match self {
            TraceStep::NotSubmitted => "not_submitted",
            TraceStep::TimedOut => "timed_out",
            TraceStep::Filled => "filled",
            TraceStep::Inconsistent => "inconsistent",
            TraceStep::EndOfData => "end_of_data",
            TraceStep::NoWindow => "no_window",
        }
    }
}

/// След одного сигнала (T-31): всё, что нужно, чтобы вне движка повторить правило «занято» —
/// занят при `t0 < idle_ns` прошлого принятого шага; `EndOfData`, `NoWindow` или остаток, который закрывали
/// до конца записи (`residual_ended`), обрывают сутки.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SignalTrace {
    /// Номер сигнала в порядке по `t0` (как `fill_signal` и `signal_index` в `rounds.csv`).
    pub signal: usize,
    pub step: TraceStep,
    /// Часы, когда стратегия снова `Idle`; `i64::MIN` — шаг без движка (`NoWindow`, `EndOfData` до `t0`).
    pub idle_ns: i64,
    /// Остаток закрывали по рынку страховкой; `Some(true)` — запись кончилась в процессе.
    pub residual: Option<bool>,
}

/// Шаг опроса `on_event` при активном круге. Не модельная величина и не
/// параметр стратегии (тот же класс, что был у снятого `EXIT_RESP_TIMEOUT_NS`
/// раньше) — только гранулярность, с которой драйвер замечает переходы фазы
/// внутри `on_event` (`interfaces.md`: модуль `lob/strategy` «прячет:
/// триггер, состояние», наружу виден только `Action`). Даёт джиттер ≤ шага
/// на моментах входа/выхода — 10 мс против `ENTRY_TTL_NS` = 2 с и `HOLD_NS` =
/// 10 с, то есть ≤ 0.5% и ≤ 0.1% соответственно; исполнение цены решает
/// очередь крейта на момент фактического пересечения, шаг её не трогает.
pub(super) const ON_EVENT_POLL_STEP_NS: i64 = 10_000_000;

// ---------------------------------------------------------------------------
// Сделка-отскока (В-44, таск 38): план приходит снаружи, исход выхода
// считается по причине.
// ---------------------------------------------------------------------------

/// Сигнал сделки-отскока: момент касания и **готовый план** (В-44). `profile` —
/// номер профиля касания, к которому отнести исход; драйвер не знает, чем
/// профили отличаются, он только считает — это то же разделение, что у
/// `Signal`/`profile_id` старого движка.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BounceSignal {
    pub t0_ns: i64,
    pub sigma: i8,
    pub plan: TradePlan,
    pub profile: u16,
    /// П-19 (TK-291): номер касания уровня (`touch_index`) подхода сигнала — только для столбца `rounds.csv`
    /// (`--touch-col`); на исполнение не влияет.
    pub touch_index: u32,
    /// Размер круга этого сигнала (R2, ревью 23.09): лот по цене **сигнала** —
    /// номинал `--order-usd` при цене касания, а не одной ценой на символ
    /// (цена последнего касания записи — заглядывание вперёд: сигнал 1-го
    /// числа получал размер по цене 30-го). `None` — размер прогона
    /// `DriveConfig::order_qty` (явный лот, одиночный `lob backtest`, тесты).
    pub qty: Option<f64>,
}

/// Чем кончились выходы: тейк / стоп / дедлайн / горизонт. У Decision 20
/// причина одна, у В-44 их три, и «сколько раз выбило стопом» — тот самый
/// вопрос практиков о винрейте (D 16:53: 3–4 движения на один стоп).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct ExitTally {
    pub take: u64,
    pub stop: u64,
    pub deadline: u64,
    pub horizon: u64,
    /// Трейл-тейк (решение владельца 2026-09-13): выход по откату от лучшего
    /// исхода. Считается отдельно от `take` — иначе не видно, сколько сделок
    /// вытянули больше 1:1 и сколько отдали трейлом.
    pub trail: u64,
    /// Досрочный выход «по прилипанию» (B4, В-58 п. 5): касание не разрешилось
    /// за `X` секунд, уровень остался лучшей ценой. Отдельно от `deadline`:
    /// это свойство касания, а не конец плана.
    pub early: u64,
    /// Выход по съеданию плотности (E5/E7): последняя нога круга ушла по
    /// порогу «съедено ≥ X % от максимума с входа».
    pub eaten: u64,
    /// Кругов с частичным выходом (E7): позиция закрывалась двумя ногами —
    /// доля тейком или по первому порогу съедания, остаток — по плану.
    pub partial: u64,
    /// F7 (Б-75): кругов с выходом «съели» — накопленные сделки в стену ≥ X %.
    pub eaten_by_trades: u64,
    /// F7 (Б-75): кругов с выходом «сняли» — стена упала без сделок.
    pub wall_gone: u64,
}

/// Итог одного профиля касаний: сигналы, круги, промахи (по причинам) и
/// причины выходов. Поля — те же, что нужны CSV таска 38.
#[derive(Debug, Clone, PartialEq)]
pub struct BounceRun {
    pub profile: u16,
    pub signals: u64,
    pub fills: Vec<Fill>,
    /// Индекс сигнала (в порядке прогона) для каждого круга: по нему CLI
    /// относит круг к осям В-44, не гоняя движок по разу на каждую ось.
    pub fill_signal: Vec<usize>,
    /// Причина выхода каждого круга, параллельно `fills`.
    pub fill_reason: Vec<ExitReason>,
    /// Время исполнения выхода каждого круга (нс биржи): из него считается
    /// покрытие суток кругами — доля времени, где биржа вообще нужна.
    pub fill_exit_ns: Vec<i64>,
    pub exits: ExitTally,
    /// Сколько входных ордеров биржа **отвергла** (статус `Rejected`) — прямой
    /// замер вместо догадки о причине неисполнения.
    pub entry_rejected: u64,
    /// Сколько **ног** входа биржа не поставила: пост-онли заявка, пересёкшая
    /// спред, получает `Expired` (GTX в `ack_new`), отвергнутая — `Rejected`
    /// (В-72, F4). Сумма по кругам суток — колонка `n_rejected_postonly`
    /// `forms.csv`; круг без единой поставленной ноги — «сигнал без входа»
    /// (`MissReason::EntryTimeout`), а не «занято».
    pub rejected_postonly: u64,
    /// Сколько раз цена входа в момент отправки **пересекала** спред (для
    /// покупки — `ask ≤ entry_px`): именно эти заявки пост-онли отвергает.
    pub entry_crossed: u64,
    /// Снятия неисполненного входа по потолку срока жизни (F5, В-74) —
    /// колонка `n_entry_cancelled_ttl` `forms.csv`.
    pub entry_cancelled_ttl: u64,
    /// Снятия по «стена снята» (F5, В-74): размер на цене уровня упал ниже
    /// порога В-66 — колонка `n_entry_cancelled_wall_dead`.
    pub entry_cancelled_wall_dead: u64,
    /// Снятия по «цена ушла из полосы лестницы» (F5, В-74) — колонка
    /// `n_entry_cancelled_price_left`.
    pub entry_cancelled_price_left: u64,
    /// Г-81 (П-17): сигналы без входа, снятые воротами `entry_gate=cxl<W>q<Q>` (ни одна нога не исполнилась) — колонка
    /// `n_entry_cancelled_cxl_gate` (только у набора с ключом). Снятия при частичном исполнении здесь не считаются.
    pub entry_cancelled_cxl_gate: u64,
    /// F8b (В5/Р2): сколько раз за сутки круг пережил потолок ожидания
    /// подтверждения отмены (`CANCEL_WAIT_NS`) — отдельно по входу
    /// (`EntryCancelReason::CancelTimeout`) и по лимитке выхода. Ноль —
    /// норма: предохранитель не срабатывал, а срабатывание видно в артефактах
    /// (`n_entry_cancelled_cancel_timeout`, `n_exit_cancel_timeout`).
    pub entry_cancelled_cancel_timeout: u64,
    /// Счётчик того же потолка на **выходе** — колонка `n_exit_cancel_timeout`.
    pub exit_cancel_timeout: u64,
    /// F8c (К1): исполнения заявок-сирот (ноги, чьё снятие не подтвердилось
    /// за потолок) — колонка `n_orphan_fills`. Ноль — норма; иначе позиция
    /// на бирже расходилась бы с учётом стратегии, и это должно быть видно.
    pub orphan_fills: u64,
    /// Г-81: мера ворот `(t0_ns сигнала, отмены за W ÷ размер стены)` — только у набора `entry_gate=cxl<W>qprobe`, колонка
    /// не пишется иначе; пусто, если у набора нет ключа.
    pub gate_measures: Vec<(i64, f64)>,
    /// Спред книги в момент отправки входа, в единицах цены — по одному
    /// значению на отправленный вход. Перевод в тики делает вызывающий (тик
    /// знает он, а не движок).
    pub spread_at_entry: Vec<f64>,
    /// Индексы сигналов, по которым вход **отправлен** (заявка ушла): по ним
    /// считается честная доля исполнения `круги / отправленные`, а не
    /// `круги / все касания` — последнее смешивает отказ с пропуском.
    pub submitted_signal: Vec<usize>,
    /// Г-102: входы суток (отправленные заявки) в окне `[00:00, 00:00 + carry_window_ns)` на этом (вариант, инструмент) —
    /// верхняя граница входов, затронутых суточным сбросом памяти отступа; колонка `n_retreat_window_entries`.
    pub retreat_window_entries: u64,
    /// Индексы сигналов, пропущенных как «позиция занята»: по ним вызывающий
    /// считает промахи по строкам осей, а не только итогом.
    pub busy_signal: Vec<usize>,
    /// Самая долгая блокировка позицией, нс: сколько сигнал ждал, пока
    /// освободится. Это и есть ответ на «почему 92 % сигналов пропущено» —
    /// длительность позиции, а не свойство рынка.
    pub busy_wait_ns_max: i64,
    /// Самая долгая жизнь круга (от входа до выхода), нс.
    pub round_ns_max: i64,
    pub misses: MissLedger,
    pub observations: Vec<FillObservation>,
    pub incomplete: bool,
    /// Сколько раз после круга позиция осталась открытой и её пришлось
    /// закрыть по рынку страховкой (2026-09-18). Ноль — норма; каждое
    /// срабатывание печатается и делает прогон `incomplete`.
    pub residual_flattened: u64,
    /// След каждого сигнала — только при `DriveConfig::busy_skip == false` (T-31), иначе пуст.
    pub trace: Vec<SignalTrace>,
}

impl BounceRun {
    /// Прогон, который не сделал ни шага: запись кончилась до первого сигнала.
    pub(super) fn nothing(profile: u16, signals: u64) -> Self {
        BounceRun {
            profile,
            signals,
            fills: Vec::new(),
            fill_signal: Vec::new(),
            fill_reason: Vec::new(),
            fill_exit_ns: Vec::new(),
            exits: ExitTally::default(),
            entry_rejected: 0,
            rejected_postonly: 0,
            entry_crossed: 0,
            entry_cancelled_ttl: 0,
            entry_cancelled_wall_dead: 0,
            entry_cancelled_price_left: 0,
            entry_cancelled_cxl_gate: 0,
            entry_cancelled_cancel_timeout: 0,
            exit_cancel_timeout: 0,
            orphan_fills: 0,
            gate_measures: Vec::new(),
            spread_at_entry: Vec::new(),
            submitted_signal: Vec::new(),
            retreat_window_entries: 0,
            busy_signal: Vec::new(),
            busy_wait_ns_max: 0,
            round_ns_max: 0,
            misses: MissLedger::default(),
            observations: Vec::new(),
            incomplete: true,
            residual_flattened: 0,
            trace: Vec::new(),
        }
    }
}
