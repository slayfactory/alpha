//! Конфиг варианта П-42 (§5.2): база = правила витрины; вариант = база + переопределения `ключ=значение`.
//!
//! Проценты хранятся в тысячных долях процента (`*_pm`: 0,3 % = 300), кратные NATR — в тысячных (`*_m`),
//! обороты — в единицах кэша (1e-5 котируемой валюты). Строка варианта: `<id> ключ=значение ключ=значение…`.

use super::data::NATR_PERIODS;
use anyhow::{bail, Context, Result};

#[derive(Clone, PartialEq, Eq, Hash)]
pub struct LevelCfg {
    pub tf_min: usize,
    pub k: usize,
    pub d_pm: i64,
    pub n: usize,
    pub tau_pm: i64,
    pub l_days: usize,
    /// §13.11: уровень умирает от прокола с первого пивота (минутный high/low за ценой > τ; предыстория и
    /// перепроверка при смене версии цены). Только `breakout tick`.
    pub pk: bool,
    /// TK-668 (`lx`): источник уровней — 0 пивоты (как раньше); 1 `D` (вчерашний максимум / минимум суток UTC); 2 `W` (прошлая календарная неделя UTC);
    /// 3 `P` (центр самой оборотной корзины профиля объёма вчерашних суток). При `lx = 1, 2, 3` уровни строятся только из источника по закрытым часовым барам
    /// (`k`, `d`, `n_bounce`, `tau`-группировка, `l_days` не действуют), правило «свежий уровень» (`fh/fk/fm/fb`) не действует. Только `breakout tick`.
    /// TK-697 D № 1: 4 — наклонная линия (`levels::build_line_levels`) по `lk` последним разворотам стороны (разворот — как у `lx = 0`: ключи `k`, `d`, свечи `tf` 30 / 60 / 240;
    /// `n_bounce`, `tau`, `l_days` не действуют, давность — `ld`), наклон против пробоя, предел наклона `lsl`; «свежий уровень» не действует.
    pub lx: u8,
    /// TK-668 (`lw`): для `lx = 2` — дни от начала сетки до первого понедельника окна (0…6); задаёт драйвер по дате `T0`.
    pub lw: usize,
    /// TK-697 D № 1 (`lk`): для `lx = 4` — число последних подтверждённых разворотов стороны, через которые идёт линия (2…8; 0 — не задано, при `lx ≠ 4` только 0).
    pub lk: usize,
    /// TK-697 D № 1 (`ld`): для `lx = 4` — давность старшей точки линии, сутки (1…30): линия живёт, пока возраст старшей точки ≤ `ld` суток (0 — не задано, при `lx ≠ 4` только 0).
    pub ld: usize,
    /// TK-697 D № 1 (`lsl`): для `lx = 4` — предел наклона линии, NATR60 ×1000 в час (0 — без предела); мерится один раз, при рождении линии.
    pub lsl_m: i64,
}

/// Ручной `Debug`: отпечаток параметров в `meta.json` при `lx = 0` — тот же, что у версий до TK-668 (`lx` и `lw` печатаются только при `lx ≠ 0`).
impl std::fmt::Debug for LevelCfg {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut d = f.debug_struct("LevelCfg");
        d.field("tf_min", &self.tf_min)
            .field("k", &self.k)
            .field("d_pm", &self.d_pm)
            .field("n", &self.n)
            .field("tau_pm", &self.tau_pm)
            .field("l_days", &self.l_days)
            .field("pk", &self.pk);
        if self.lx != 0 {
            d.field("lx", &self.lx).field("lw", &self.lw);
        }
        if self.lx == 4 {
            d.field("lk", &self.lk)
                .field("ld", &self.ld)
                .field("lsl_m", &self.lsl_m);
        }
        d.finish()
    }
}

#[derive(Clone, PartialEq, Eq, Hash)]
pub struct SigCfg {
    pub lv: LevelCfg,
    pub natr_p: usize,
    pub natr_min_pm: i64,
    pub t1h_u: i64,
    /// 0 — фильтра нет.
    pub t24_u: i64,
    pub track_pm: i64,
    pub e_pm: i64,
    pub color: bool,
    pub f_pm: i64,
    pub r_min: usize,
    pub ban: bool,
    /// §13.13: подход к уровню с правильной стороны — 0 выкл.; 1 — отвергать сигнал, пришедший в зону сверху (назад по сделкам:
    /// первая, дошедшая до уровня, решает «сверху», первая ниже зоны — «снизу»); 2 — только пометить (`ws`). Только `breakout tick`.
    pub ap: u8,
    /// §13.13: сделка на уровне или за ним (high ≥ цены версии) убивает уровень со следующей минуты. Только `breakout tick`.
    pub pb: bool,
    /// §13.14 «свежий уровень», (а): H в минутах — последнее касание уровня не старше H; 0 — (а) выключено. Только `breakout tick`.
    pub fr_h_min: usize,
    /// §13.14, (б) «проторговка»: окно K в минутах.
    pub fr_k: usize,
    /// §13.14, (б): доля K в % — минимум минут закрытия в полосе подхода; 0 — (б) выключено.
    pub fr_share: usize,
    /// §13.14, (б): ширина полосы подхода b в тысячных процента.
    pub fr_b_pm: i64,
    /// TK-669 (`fg`, «закол прощаем после отката»): X ×1000 NATR (0 — выкл.): минутные убийцы уровня `pk`, `pb`, `ban` в `tick::candidates` приостанавливают уровень, а не
    /// убивают его; приостановка снимается на первой минуте строго после минуты приостановки, чей low за уровнем ≤ `L − L·NATR[t−1]·X`. Только `breakout tick`.
    pub fg_m: i64,
    /// TK-681 (`nf`): источник фильтра живости пула — 0 NATR `natr_p` (как раньше); 1–8 — коды `vs` (`Sym::vol_for`: NATR 5 / 15 / 30 / 60,
    /// реализованная волатильность 5 / 15 / 30 / 60 минут); порог — `natr_min`, проценты цены, закрытая минутка `t−1`. Только `breakout tick`.
    pub nf: u8,
    /// TK-681 (`ev`): зона входа от волатильности — источник `V` (коды `vs`; 0 — выкл., зона = `e`): `e = clamp(ea·V[t−1], el, eh)`, затем как раньше
    /// `min(e, track)`; нет значения `V` — кандидата нет. Только `breakout tick`.
    pub ev: u8,
    /// TK-681 (`ea`): коэффициент `k` при `V`, ×1000.
    pub ea_m: i64,
    /// TK-681 (`el`, `eh`): зажим зоны входа, тысячные процента (по умолчанию 0,05 % и 1 %).
    pub el_pm: i64,
    pub eh_pm: i64,
    /// TK-738 (`vx`): потолок `V` = RV60 (реализованная волатильность 60 минут, код `vs = 8`) закрытой минутки `t−1`, тысячные процента цены (как `natr_min`);
    /// сигнал с `V > vx` кандидата не даёт, нет значения `V` — кандидата нет; 0 — выкл. Только `breakout tick`.
    pub vx_pm: i64,
    /// TK-738 (`hr`, `hw`): окно часа UTC сигнальной минуты `t`: начало `hr` и ширина `hw`, в минутах суток (ключи — часы, дробные можно); минута `m = t mod 1440`
    /// проходит, если `(m − hr) mod 1440 < hw` (окно заворачивается через сутки); `hw = 0` — выкл., `hw ≥ 1440` — окно на все сутки (проходит всё). Только `breakout tick`.
    pub hr_min: i64,
    pub hw_min: i64,
}

/// Ручной `Debug`: строка `params` в meta.json при `fg = 0` — та же, что у производного `Debug` до TK-669 (поле печатается только при `fg ≠ 0`).
impl std::fmt::Debug for SigCfg {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut d = f.debug_struct("SigCfg");
        d.field("lv", &self.lv)
            .field("natr_p", &self.natr_p)
            .field("natr_min_pm", &self.natr_min_pm)
            .field("t1h_u", &self.t1h_u)
            .field("t24_u", &self.t24_u)
            .field("track_pm", &self.track_pm)
            .field("e_pm", &self.e_pm)
            .field("color", &self.color)
            .field("f_pm", &self.f_pm)
            .field("r_min", &self.r_min)
            .field("ban", &self.ban)
            .field("ap", &self.ap)
            .field("pb", &self.pb)
            .field("fr_h_min", &self.fr_h_min)
            .field("fr_k", &self.fr_k)
            .field("fr_share", &self.fr_share)
            .field("fr_b_pm", &self.fr_b_pm);
        if self.fg_m != 0 {
            d.field("fg_m", &self.fg_m);
        }
        if self.nf != 0 {
            d.field("nf", &self.nf);
        }
        if self.ev != 0 {
            d.field("ev", &self.ev)
                .field("ea_m", &self.ea_m)
                .field("el_pm", &self.el_pm)
                .field("eh_pm", &self.eh_pm);
        }
        if self.vx_pm != 0 {
            d.field("vx_pm", &self.vx_pm);
        }
        if self.hw_min != 0 {
            d.field("hr_min", &self.hr_min)
                .field("hw_min", &self.hw_min);
        }
        d.finish()
    }
}

#[derive(Clone, PartialEq, Eq, Hash)]
pub struct ExitCfg {
    pub natr_p: usize,
    pub n_tp: usize,
    pub t_low_m: i64,
    pub t_high_m: i64,
    pub q_pct: i64,
    pub be: bool,
    pub a_m: i64,
    pub r_m: i64,
    pub s_pm: i64,
    pub slip_bps: i64,
    /// Лучший порядок внутри минутки (справочно, не для вердикта).
    pub best_order: bool,
    /// Задержка входа, мс (только шаг B по сделкам; 1м-счёт её не использует).
    pub lat_ms: i64,
    /// Задержка исполнения выхода по стопу/трейлу/безубытку, мс (§13.6, справочно): выход — по первой сделке не раньше
    /// срабатывания + задержка; тейки остаются лимитками. 0 — выход на сделке срабатывания (как в §13.2).
    pub lat_exit_ms: i64,
    /// §13.12: перекос цены лестницы тейков — ступень i стоит на `t_low + (t_high − t_low)·(i/(n−1))^γ` (γ ×1000; 1000 — равные шаги).
    /// Только `breakout tick`.
    pub tp_gamma_m: i64,
    /// §13.12: перекос объёма — вес ступени ∝ `1 + κ·i/(n−1)` (κ ×1000; 0 — равные доли); сумма долей остаётся q. Только `breakout tick`.
    pub tp_kappa_m: i64,
    /// §13.12: источник NATR выхода: 0 — закрытая минутка t−1 (30 баров до сигнальной, §13.2); 1 — включая сигнальную минуту t
    /// (заглядывание вперёд, справочно). Только `breakout tick`.
    pub natr_src: u8,
    /// §13.16: вход по ленте «как на момент сигнала + `lat`»: W, мс — максимальный возраст последней сделки стороны; 0 — прежний вход
    /// (следующая сделка стороны после `сигнал + lat`). Только `breakout tick`.
    pub ent_w_ms: i64,
    /// §13.16: допустимый перелёт цены-заменителя за уровень, бп (0 — строго за уровнем отвергается). Действует при `ent_w_ms > 0`.
    pub ent_x_bp: i64,
    /// §13.19 (`ls`): стоп за дальним краем зоны + k·NATR (k ×1000; 0 — выкл): `max(e − S%, min(e, P_far) − nat(k))`. Только `breakout tick`.
    pub ls_m: i64,
    /// §13.19 (`tt`): выход по времени — T мин от первого «пересекли»; 0 — выкл.
    pub tx_min: i64,
    /// §13.19 (`tb`): «пробой» глубиной X (тысячные процента от уровня) отменяет выход по времени.
    pub tx_x_pm: i64,
    /// §13.19 (`rf`): выход при возврате — глубина f (тысячные процента от уровня); 0 — выкл.
    pub rt_f_pm: i64,
    /// §13.19 (`rs`): длительность возврата, с.
    pub rt_s: i64,
    /// §13.19 (`bm`): безубыток — 0 как раньше; 1 выкл; 2 после 2-го тейка; 3 за уровнем.
    pub be_mode: u8,
    /// §13.19 (`e2`): второй вход 50/50 при подходе к уровню ближе d (тысячные процента от уровня); 0 — выкл. Требует `ew > 0`.
    pub e2_pm: i64,
    /// §13.21 (`sn`): стоп в NATR — k0 ×1000 (0 — выкл.): `d = clamp(k0·NATR·g, 0,5 %, S_жёсткий)`, стоп = цена входа лота ∓ `d`; NATR — закрытая
    /// минутка `t−1`, заморожен на входе. Только `breakout tick`.
    pub sn_m: i64,
    /// §13.21 (`sg`): семейство множителя `g` — 0 константа (`g = 1`); 1 линейно по времени удержания (`g = 1 + β·Δt_ч`, пересчёт на каждой сделке);
    /// 2 геометрически по числу исполненных тейков `j` (`g = ρ^j`); 3 шаг по `j` (`g = 1 + δ·j`).
    pub sg: u8,
    /// §13.21 (`sb`): β семейства `sg = 1`, 1/ч ×1000.
    pub sb_m: i64,
    /// §13.21 (`sr`): ρ семейства `sg = 2`, ×1000.
    pub sr_m: i64,
    /// §13.21 (`sd`): δ семейства `sg = 3`, ×1000.
    pub sd_m: i64,
    /// §13.21 (`tj`): шаг лестницы тейков δ ×1000 (0 — выкл.): после исполнения тейков оставшиеся ступени стоят на `mult_i·(1 + δ·j)` NATR, `j` — число
    /// исполненных тейков; пересчитанная лестница действует со следующей сделки. Только `breakout tick`.
    pub tj_m: i64,
    /// v22 (`tl`, TK-603): трейл лимиткой — 0 выкл. (трейл тейкером по цене сделки, как в v21); 1 — лимитный приказ на остаток по цене трейла, мейкер,
    /// без проскальзывания, исполняется сделкой «дошла или прошла» (по цене приказа и тогда, когда к приходу приказа цена уже за ним); 2 — то же, но приказ,
    /// пришедший при цене уже строго за ним, исполняется как рыночный выход v21. Стопы и безубыток остаются рыночными. Только `breakout tick`.
    pub tl: u8,
    /// §13.31 (`nx`): выход «сценарий отменён» до касания — цена ушла от уровня против позиции на X·NATR (X ×1000; 0 — выкл.): пока не было сделки
    /// `p ≥ L`, первая сделка `p ≤ L − X·NATR` (не выше `e − 1`) закрывает остаток; после касания не действует. Вид выхода `nt`, не «стоп».
    /// Только `breakout tick`.
    pub nx_m: i64,
    /// §13.31 (`sl`): резиновый стоп от уровня — `L − k·NATR` (k ×1000; 0 — выкл.), не выше `e − 1`; жёсткий стоп `s` остаётся потолком убытка.
    /// Только `breakout tick`.
    pub sl_m: i64,
    /// §13.31 (`cw`): стоп за проторговкой — окно W закрытых минут перед сигнальной (0 — выкл.).
    pub cs_w: i64,
    /// §13.31 (`cl`, `ch`): допустимая ширина проторговки в NATR ×1000 — `cl ≤ ширина ≤ ch`; вне диапазона — запасной стоп `sl`.
    pub cs_lo_m: i64,
    pub cs_hi_m: i64,
    /// §13.31 (`cm`): отступ стопа за дальний край проторговки, NATR ×1000.
    pub cs_m_m: i64,
    /// TK-649 (`tu`): выход «уровень не тронут за T минут от входа» (0 — выкл.): пока не было сделки `p ≥ L`, первая сделка не раньше `вход + T`
    /// закрывает остаток рыночным выходом; после касания не действует. Вид выхода `tu`. Только `breakout tick`.
    pub tu_min: i64,
    /// TK-669 (`tpp`): тейки лестницы в процентах цены входа (`t_low`, `t_high` — проценты, а не NATR); требует `tj = 0`. Только `breakout tick`.
    pub tpp: bool,
    /// TK-669 (`trp`): трейл v11 — откат `rr` в процентах цены входа, включается первым исполненным тейком (а не ходом `+a` NATR). Только `breakout tick`.
    pub trp: bool,
    /// TK-676 (`vf`): стоп от волатильности — форма расстояния `d` (проценты цены входа), заморожена на входе: 0 выкл.; 1 `a·V`; 2 `b + a·V`; 3 `a·V^p`;
    /// 4 `a·ln(1 + V/v0)`, `V` — в процентах цены; итог зажат в `[vm, s]` и работает «самым плотным» вместе с `s`, `ls`, `sl`. Только `breakout tick`.
    pub vf: u8,
    /// TK-676 (`vs`): источник `V` (закрытая минутка `t−1`): 1–4 NATR 5 / 15 / 30 / 60 баров, 5–8 реализованная волатильность 5 / 15 / 30 / 60 минут.
    pub vs: u8,
    /// TK-676 (`va`): коэффициент `a` ×1000.
    pub va_m: i64,
    /// TK-676 (`vb`): сдвиг `b`, тысячные процента (форма 2).
    pub vb_pm: i64,
    /// TK-676 (`vp`): степень `p` ×1000 (форма 3).
    pub vp_m: i64,
    /// TK-676 (`v0`): масштаб `v0`, тысячные процента (форма 4).
    pub v0_pm: i64,
    /// TK-676 (`vm`): нижний зажим расстояния, тысячные процента (по умолчанию 500 = 0,5 %); верхний — `s`.
    pub vm_pm: i64,
    /// TK-676 (`vz`, `vr`): режимный множитель `(V_z / V)^ρ` — источник `V_z` (коды `vs`; 0 выкл.) и ρ ×1000.
    pub vz: u8,
    pub vr_m: i64,
    /// TK-681 (`sm`): нижний зажим стопов от уровня (`sl`, `cw`) и порога `nx` — не ближе `sm` % цены входа; тысячные процента, 0 — выкл.
    pub sm_pm: i64,
    /// TK-677 (`w2`): доля второго лота (ближнего к уровню), % ставки; первый лот — остаток. 50 — прежние 50/50; только при включённом втором входе.
    pub w2_pct: i64,
    /// TK-677 (`e2t`): второй вход за N тиков до уровня (`p ≥ L − N·тик`, тик — `tick_at` на входе лота 1); 0 — выкл. Вместо `e2`, требует `ew > 0`.
    pub e2t: i64,
    /// TK-677 (`s2`): 1 — общий стоп позиции: жёсткий стоп лота 2 стоит на цене стопа лота 1; 0 — у каждого лота свой стоп от своей цены входа.
    pub s2: bool,
    /// TK-688 (`e1t`): вся ставка одной лимитной заявкой в N тиках до уровня (лимит `L − N·тик`, В-178 «исключительно до уровня, никогда не после»);
    /// 0 — выкл. Мейкер, как `mk`; требует `ew > 0`, несовместим с `mk`, `ek`, `xb`, `e2`, `e2t`, `pw`. `e1_decision` знак понимает (< 0 — за уровнем), ключом он отвергается.
    pub e1t: i64,
    /// TK-697 (`tn`, банк № 9 «тейк у следующего уровня»; только `breakout tick`): доля позиции, %, которую закрывает ПЕРВЫЙ тейк, поставленный у следующего уровня
    /// (0 — выкл., по умолчанию; 1…100, перебор 25 / 50 / 100). Определение (его проверяет критик, менять только с картой `kb/task/TK-697.md`):
    /// - следующий уровень — ближайший по цене уровень той же монеты СТРОГО за пробиваемым по ходу сделки (выше — лонг, ниже — шорт) из ТОГО ЖЕ набора уровней,
    ///   что дал сигнал (тот же `lx`, тот же `tf`: набор `build_levels_far(sym, &lv, сторона)`), и только из уровней, известных на минуте сигнала `t` и живых на ней
    ///   (`Level::born ≤ t < Level::end`, цена — версия `segs` минуты `t`). Считает фаза 1 один раз на сигнал (`TSig::nxt`); вход не раньше сигнала, поэтому
    ///   уровень, родившийся после сигнала, не виден (заглядывания вперёд нет);
    /// - цена тейка = следующий уровень − `tno`·NATR (лонг; шорт зеркально), целые цены 1e-8, как тейки лестницы (`tps`);
    /// - тейк заменяет ступень 1 лестницы: лот = `tn` % позиции. Оставшиеся (100 − `tn`) % — позиция, на которой лестница работает как обычно: ступени 2…n с ценой строго
    ///   выше тейка `tn` (ступени ниже него и ступень 1 сняты), в тейки уходит `q` % остатка, веса ступеней (`tpk`) нормируются заново по оставшимся ступеням, остаток
    ///   делится на ступени с остатком целого деления на последней; всё, что не ушло в тейки, — обычный выход (стоп, безубыток, трейл, выходы от уровня). `tn = 100` —
    ///   вся позиция закрыта этим тейком; при `q = 0` ступени лестницы безлотные, как и без `tn`;
    /// - откат на обычную лестницу (ключ на этой сделке не действует): следующего уровня нет; тейк ≤ цены входа лота (не по ту сторону, нулевое расстояние после отступа);
    ///   тейк дальше внешней ступени лестницы (`t_high`: расстояние тейка от входа больше расстояния внешней ступени; лестница в конфиге есть всегда — `validate` требует
    ///   `n_tp ≥ 2`, `t_low < t_high`).
    pub tn_pct: i64,
    /// TK-697 (`tno`): отступ тейка `tn` от следующего уровня внутрь, NATR ×1000 (0 — ровно на уровне, по умолчанию; перебор 0 / 0,1 / 0,25); значим только при `tn > 0`.
    pub tno_m: i64,
    /// TK-743 (`--keep-orders`, не ключ строки варианта): вести журнал заявок сделки (`TickOut::ord`); на счёт и `trades.csv` не влияет. В `Debug` не входит (meta.json прежний).
    pub keep_orders: bool,
}

/// Ручной `Debug`: при выключенных ключах §13.31 строка `params` в meta.json — та же, что у производного `Debug` до §13.31.
impl std::fmt::Debug for ExitCfg {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut d = f.debug_struct("ExitCfg");
        d.field("natr_p", &self.natr_p)
            .field("n_tp", &self.n_tp)
            .field("t_low_m", &self.t_low_m)
            .field("t_high_m", &self.t_high_m)
            .field("q_pct", &self.q_pct)
            .field("be", &self.be)
            .field("a_m", &self.a_m)
            .field("r_m", &self.r_m)
            .field("s_pm", &self.s_pm)
            .field("slip_bps", &self.slip_bps)
            .field("best_order", &self.best_order)
            .field("lat_ms", &self.lat_ms)
            .field("lat_exit_ms", &self.lat_exit_ms)
            .field("tp_gamma_m", &self.tp_gamma_m)
            .field("tp_kappa_m", &self.tp_kappa_m)
            .field("natr_src", &self.natr_src)
            .field("ent_w_ms", &self.ent_w_ms)
            .field("ent_x_bp", &self.ent_x_bp)
            .field("ls_m", &self.ls_m)
            .field("tx_min", &self.tx_min)
            .field("tx_x_pm", &self.tx_x_pm)
            .field("rt_f_pm", &self.rt_f_pm)
            .field("rt_s", &self.rt_s)
            .field("be_mode", &self.be_mode)
            .field("e2_pm", &self.e2_pm)
            .field("sn_m", &self.sn_m)
            .field("sg", &self.sg)
            .field("sb_m", &self.sb_m)
            .field("sr_m", &self.sr_m)
            .field("sd_m", &self.sd_m)
            .field("tj_m", &self.tj_m)
            .field("tl", &self.tl);
        if self.stop_redesign_opts() {
            d.field("nx_m", &self.nx_m)
                .field("sl_m", &self.sl_m)
                .field("cs_w", &self.cs_w)
                .field("cs_lo_m", &self.cs_lo_m)
                .field("cs_hi_m", &self.cs_hi_m)
                .field("cs_m_m", &self.cs_m_m);
        }
        if self.tu_min != 0 {
            d.field("tu_min", &self.tu_min);
        }
        if self.tpp || self.trp {
            d.field("tpp", &self.tpp).field("trp", &self.trp);
        }
        if self.vf != 0 {
            d.field("vf", &self.vf)
                .field("vs", &self.vs)
                .field("va_m", &self.va_m)
                .field("vb_pm", &self.vb_pm)
                .field("vp_m", &self.vp_m)
                .field("v0_pm", &self.v0_pm)
                .field("vm_pm", &self.vm_pm)
                .field("vz", &self.vz)
                .field("vr_m", &self.vr_m);
        }
        if self.w2_pct != 50 || self.e2t != 0 || self.s2 {
            d.field("w2_pct", &self.w2_pct)
                .field("e2t", &self.e2t)
                .field("s2", &self.s2);
        }
        if self.sm_pm != 0 {
            d.field("sm_pm", &self.sm_pm);
        }
        if self.e1t != 0 {
            d.field("e1t", &self.e1t);
        }
        if self.tn_pct != 0 {
            d.field("tn_pct", &self.tn_pct).field("tno_m", &self.tno_m);
        }
        d.finish()
    }
}

/// §13.21: фильтр кандидатов по признаку (`fa=<имя> fo=lo|hi fv=<число>`): кандидат отбрасывается ДО портфеля, если значение признака
/// меньше `fv` (`fo=lo`) или больше `fv` (`fo=hi`); нет строки в таблице или значение пусто/NaN — кандидат проходит. Таблица — `--ftab`.
/// Только `breakout tick`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct FilterCfg {
    /// Имя столбца таблицы признаков; пусто — фильтра нет.
    pub name: String,
    /// `fo`: `Some(false)` — `lo` (отбросить значения ниже `fv`), `Some(true)` — `hi` (выше `fv`).
    pub hi: Option<bool>,
    pub v: Option<f64>,
}

impl FilterCfg {
    pub fn on(&self) -> bool {
        !self.name.is_empty()
    }
}

#[derive(Clone, PartialEq)]
pub struct PortCfg {
    pub cool_min: usize,
    pub m_stops: usize,
    pub mm_min: usize,
    pub s_glob: usize,
    pub w_min: usize,
    pub ww_min: usize,
    pub p: usize,
    /// §13.5: 0 — одна сделка на уровень (любой выход расходует уровень); 1 — повторный вход после тейков/безубытка/
    /// трейла, уровень расходует только жёсткий стоп; 2 — выходы уровень не расходуют вовсе (ложный и истинный пробой —
    /// как и раньше). Только `breakout tick`.
    pub reentry: u8,
    /// §13.21 (`busy`): `true` (по умолчанию) — монета с открытой позицией отвергает новые сигналы (отказ «занята»); `false` — второй сигнал
    /// другого уровня той же монеты входит при открытой позиции (режим сигналов: одна сделка на уровень, лимиты портфеля — `p`, `c`, `m`, `s_glob`,
    /// `ww` — выключаются своими значениями). Только `breakout tick`.
    pub busy: bool,
    /// TK-679 (`pr`): кто из кандидатов берёт место, когда всех взять нельзя: 0 — как было (расстояние до уровня, монета по алфавиту);
    /// 1 — выше NATR(30) закрытой минуты `t−1`; 2 — выше оборот за 15 закрытых минут; 3 — за 60. Равенство — прежний порядок. Только `breakout tick`.
    pub pr: u8,
    /// TK-679 (`pw`): окно сбора кандидатов, мс. 0 — правило `pr` действует только среди сигналов с одним и тем же временем (мс). `> 0` — кандидат,
    /// который занял бы последнее свободное место, открывает окно `[t0, t0 + pw]`; прошедшие проверки кандидаты окна собираются, в `T = t0 + pw`
    /// ранжируются по `pr` и входят по ленте на `T + lat` (кандидат, у которого сделка в `(сигнал, T]` дошла до уровня, снят). При двух и более
    /// свободных местах вход сразу, как было. Требует `ew > 0`, несовместим с `mk`, `ek`, `xb`. Только `breakout tick`.
    pub pw_ms: i64,
}

/// Ручной `Debug`: отпечаток в `meta.json` при `pr = 0`, `pw = 0` — прежний (поля печатаются только при включённом приоритете).
impl std::fmt::Debug for PortCfg {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut d = f.debug_struct("PortCfg");
        d.field("cool_min", &self.cool_min)
            .field("m_stops", &self.m_stops)
            .field("mm_min", &self.mm_min)
            .field("s_glob", &self.s_glob)
            .field("w_min", &self.w_min)
            .field("ww_min", &self.ww_min)
            .field("p", &self.p)
            .field("reentry", &self.reentry)
            .field("busy", &self.busy);
        if self.pr != 0 || self.pw_ms != 0 {
            d.field("pr", &self.pr).field("pw_ms", &self.pw_ms);
        }
        d.finish()
    }
}

#[derive(Clone, PartialEq)]
pub struct Params {
    pub sig: SigCfg,
    pub exit: ExitCfg,
    pub port: PortCfg,
    /// §13.21: фильтр кандидатов по признаку (`fa/fo/fv`); выключен по умолчанию.
    pub filt: FilterCfg,
    /// §13.28 (`mk`): вход лимиткой — 0 выкл. (путь тейкера), 1 — mk1, 2 — mk2 (ретест у уровня); требует `ew > 0`. Только `breakout tick`.
    pub mk: u8,
    /// TK-660 (`ek`): вход по подтверждению E(k) — k, бп (0 — выкл., вход на сигнале); требует `ew > 0`, несовместим с `mk`. Только `breakout tick`.
    pub ek: i64,
    /// TK-669 (`xb`): вход за уровнем — X ×1000 NATR (0 — выкл.): условный приказ на пробой `p ≥ L + L·NATR·X`, срок 60 мин, вся ставка одним входом;
    /// требует `ew > 0`, `e2 = 0`, несовместим с `mk`, `ek`. Только `breakout tick`.
    pub xb: i64,
}

/// Ручной `Debug`: отпечаток параметров в `meta.json` при `mk = 0` — тот же, что у версий до §13.28 (поле `mk` печатается только при `mk ≠ 0`).
impl std::fmt::Debug for Params {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut d = f.debug_struct("Params");
        d.field("sig", &self.sig)
            .field("exit", &self.exit)
            .field("port", &self.port)
            .field("filt", &self.filt);
        if self.mk != 0 {
            d.field("mk", &self.mk);
        }
        if self.ek != 0 {
            d.field("ek", &self.ek);
        }
        if self.xb != 0 {
            d.field("xb", &self.xb);
        }
        d.finish()
    }
}

fn pm(x: f64) -> i64 {
    (x * 1000.0).round() as i64
}

impl SigCfg {
    /// Правило §13.14 «свежий уровень» включено (хотя бы одна из ветвей (а), (б)).
    pub fn fresh_on(&self) -> bool {
        self.fr_h_min > 0 || self.fr_share > 0
    }

    /// TK-681 (`ev`): зона входа на минуте с волатильностью `v` (доля цены ×1e8, −1 — нет значения) — доля цены ×1e8, уже с `min(·, track)`;
    /// `None` — зоны нет (нет значения `V`). При `ev = 0` — прежнее `min(e, track)`.
    pub fn zone_e8(&self, v: i64) -> Option<i64> {
        let track = self.track_pm * 1000;
        if self.ev == 0 {
            return Some((self.e_pm * 1000).min(track));
        }
        if v < 0 {
            return None;
        }
        let e = (i128::from(v) * i128::from(self.ea_m) / 1000)
            .max(i128::from(self.el_pm) * 1000)
            .min(i128::from(self.eh_pm) * 1000) as i64;
        Some(e.min(track))
    }
}

impl ExitCfg {
    /// Вход по ленте §13.16 включён (`ew > 0`).
    pub fn tape_entry(&self) -> bool {
        self.ent_w_ms > 0
    }

    /// Опции §13.19, зависящие от цены уровня (`ls`, `tt`, `rf`, `bm = 3`) включены — нужны `lp` и `P_far`.
    pub fn level_opts(&self) -> bool {
        self.ls_m > 0
            || self.tx_min > 0
            || self.rt_f_pm > 0
            || self.be_mode == 3
            || self.stop_redesign_opts()
            || self.tu_min != 0
    }

    /// Опции §13.31 (`nx`, `sl`, `cw`, `cl`, `ch`, `cm`) включены — `breakout tick` only.
    pub fn stop_redesign_opts(&self) -> bool {
        self.nx_m != 0
            || self.sl_m != 0
            || self.cs_w != 0
            || self.cs_lo_m != 0
            || self.cs_hi_m != 0
            || self.cs_m_m != 0
            || self.sm_pm != 0
    }

    /// Любая опция §13.19 включена (`breakout tick` only).
    pub fn lvl_exit_opts(&self) -> bool {
        self.level_opts() || self.be_mode != 0 || self.split()
    }

    /// Позиция из двух лотов: второй вход включён (`e2` или `e2t`).
    pub fn split(&self) -> bool {
        self.e2_pm > 0 || self.e2t > 0
    }

    /// Размеры лотов 1 и 2 (мкУЕ) при ставке `notional`: без второго входа — вся ставка в лоте 1.
    pub fn lots(&self, notional: i64) -> (i64, i64) {
        if !self.split() {
            return (notional, 0);
        }
        let b = notional * self.w2_pct / 100;
        (notional - b, b)
    }

    /// Опции §13.21 (NATR-стоп `sn/sg/sb/sr/sd`, шаг тейков `tj`) включены — `breakout tick` only.
    pub fn natr_stop_opts(&self) -> bool {
        self.sn_m != 0
            || self.sg != 0
            || self.sb_m != 0
            || self.sr_m != 0
            || self.sd_m != 0
            || self.tj_m != 0
    }

    /// Опции §13.12 не по умолчанию (лестница тейков с перекосом или NATR по другой минутке) — только `breakout tick`.
    pub fn take_grid_opts(&self) -> bool {
        self.tp_gamma_m != 1000 || self.tp_kappa_m != 0 || self.natr_src != 0
    }
}

impl Params {
    /// База витрины (§5.2).
    pub fn base() -> Params {
        Params {
            sig: SigCfg {
                lv: LevelCfg {
                    tf_min: 60,
                    k: 2,
                    d_pm: pm(0.5),
                    n: 2,
                    tau_pm: pm(0.2),
                    l_days: 7,
                    pk: false,
                    lx: 0,
                    lw: 0,
                    lk: 0,
                    ld: 0,
                    lsl_m: 0,
                },
                natr_p: 30,
                natr_min_pm: pm(0.4),
                t1h_u: 500_000 * 100_000,
                t24_u: 0,
                track_pm: pm(2.0),
                e_pm: pm(0.3),
                color: true,
                f_pm: pm(0.1),
                r_min: 15,
                ban: true,
                ap: 0,
                pb: false,
                fr_h_min: 0,
                fr_k: 120,
                fr_share: 0,
                fr_b_pm: pm(0.5),
                fg_m: 0,
                nf: 0,
                ev: 0,
                ea_m: 0,
                el_pm: 50,
                eh_pm: 1000,
                vx_pm: 0,
                hr_min: 0,
                hw_min: 0,
            },
            exit: ExitCfg {
                natr_p: 30,
                n_tp: 8,
                t_low_m: 1000,
                t_high_m: 5000,
                q_pct: 50,
                be: true,
                a_m: 2000,
                r_m: 2000,
                s_pm: pm(5.0),
                slip_bps: 1,
                best_order: false,
                lat_ms: 0,
                lat_exit_ms: 0,
                tp_gamma_m: 1000,
                tp_kappa_m: 0,
                natr_src: 0,
                ent_w_ms: 0,
                ent_x_bp: 0,
                ls_m: 0,
                tx_min: 0,
                tx_x_pm: 0,
                rt_f_pm: 0,
                rt_s: 0,
                be_mode: 0,
                e2_pm: 0,
                sn_m: 0,
                sg: 0,
                sb_m: 0,
                sr_m: 0,
                sd_m: 0,
                tj_m: 0,
                tl: 0,
                nx_m: 0,
                sl_m: 0,
                cs_w: 0,
                cs_lo_m: 0,
                cs_hi_m: 0,
                cs_m_m: 0,
                tu_min: 0,
                tpp: false,
                trp: false,
                vf: 0,
                vs: 0,
                va_m: 0,
                vb_pm: 0,
                vp_m: 0,
                v0_pm: 0,
                vm_pm: 500,
                vz: 0,
                vr_m: 0,
                sm_pm: 0,
                w2_pct: 50,
                e2t: 0,
                s2: false,
                e1t: 0,
                tn_pct: 0,
                tno_m: 0,
                keep_orders: false,
            },
            port: PortCfg {
                cool_min: 5,
                m_stops: 3,
                mm_min: 60,
                s_glob: 4,
                w_min: 10,
                ww_min: 60,
                p: 3,
                reentry: 0,
                busy: true,
                pr: 0,
                pw_ms: 0,
            },
            filt: FilterCfg::default(),
            mk: 0,
            ek: 0,
            xb: 0,
        }
    }

    /// Применяет одно переопределение; ключи — в `docs/research/P-42-level-breakout.md` §5.2.
    pub fn set(&mut self, key: &str, val: &str) -> Result<()> {
        // §13.21: строковые значения фильтра признаков — до числового разбора
        match key {
            "fa" => {
                self.filt.name = val.to_string();
                return Ok(());
            }
            "fo" => {
                self.filt.hi = Some(match val {
                    "lo" => false,
                    "hi" => true,
                    _ => bail!("fo ∈ {{lo, hi}}: {val}"),
                });
                return Ok(());
            }
            _ => {}
        }
        let f: f64 = val.parse().with_context(|| format!("{key}={val}"))?;
        let u = f.round() as usize;
        let b = f != 0.0;
        match key {
            "natr_p" => self.sig.natr_p = u,
            "natr_min" => self.sig.natr_min_pm = pm(f),
            "t1h" => self.sig.t1h_u = (f * 1e6 * 1e5).round() as i64,
            "t24" => self.sig.t24_u = (f * 1e6 * 1e5).round() as i64,
            "natr_exit_p" => self.exit.natr_p = u,
            "tf" => self.sig.lv.tf_min = u,
            "k" => self.sig.lv.k = u,
            "d" => self.sig.lv.d_pm = pm(f),
            "n_bounce" => self.sig.lv.n = u,
            "tau" => self.sig.lv.tau_pm = pm(f),
            "l_days" => self.sig.lv.l_days = u,
            "pk" => self.sig.lv.pk = b,
            "lx" => self.sig.lv.lx = u.min(255) as u8,
            "lw" => self.sig.lv.lw = u,
            "lk" => self.sig.lv.lk = u,
            "ld" => self.sig.lv.ld = u,
            "lsl" => self.sig.lv.lsl_m = pm(f),
            "track" => self.sig.track_pm = pm(f),
            "e" => self.sig.e_pm = pm(f),
            "color" => self.sig.color = b,
            "f" => self.sig.f_pm = pm(f),
            "r" => self.sig.r_min = u,
            "ban" => self.sig.ban = b,
            "ap" => self.sig.ap = u.min(255) as u8,
            "pb" => self.sig.pb = b,
            "fh" => self.sig.fr_h_min = (f * 60.0).round() as usize,
            "fk" => self.sig.fr_k = u,
            "fm" => self.sig.fr_share = u,
            "fb" => self.sig.fr_b_pm = pm(f),
            "n_tp" => self.exit.n_tp = u,
            "t_low" => self.exit.t_low_m = pm(f),
            "t_high" => self.exit.t_high_m = pm(f),
            "q" => self.exit.q_pct = f.round() as i64,
            "be" => self.exit.be = b,
            "a" => self.exit.a_m = pm(f),
            "rr" => self.exit.r_m = pm(f),
            "s" => self.exit.s_pm = pm(f),
            "slip" => self.exit.slip_bps = f.round() as i64,
            "best" => self.exit.best_order = b,
            "lat" => self.exit.lat_ms = f.round() as i64,
            "latx" => self.exit.lat_exit_ms = f.round() as i64,
            "tpg" => self.exit.tp_gamma_m = pm(f),
            "tpk" => self.exit.tp_kappa_m = pm(f),
            "ns" => self.exit.natr_src = u.min(255) as u8,
            "ew" => self.exit.ent_w_ms = f.round() as i64,
            "ex" => self.exit.ent_x_bp = f.round() as i64,
            "ls" => self.exit.ls_m = pm(f),
            "tt" => self.exit.tx_min = f.round() as i64,
            "tb" => self.exit.tx_x_pm = pm(f),
            "rf" => self.exit.rt_f_pm = pm(f),
            "rs" => self.exit.rt_s = f.round() as i64,
            "bm" => self.exit.be_mode = u.min(255) as u8,
            "e2" => self.exit.e2_pm = pm(f),
            "sn" => self.exit.sn_m = pm(f),
            "sg" => self.exit.sg = u.min(255) as u8,
            "sb" => self.exit.sb_m = pm(f),
            "sr" => self.exit.sr_m = pm(f),
            "sd" => self.exit.sd_m = pm(f),
            "tj" => self.exit.tj_m = pm(f),
            "tl" => self.exit.tl = u.min(255) as u8,
            "nx" => self.exit.nx_m = pm(f),
            "sl" => self.exit.sl_m = pm(f),
            "sm" => self.exit.sm_pm = pm(f),
            "cw" => self.exit.cs_w = f.round() as i64,
            "cl" => self.exit.cs_lo_m = pm(f),
            "ch" => self.exit.cs_hi_m = pm(f),
            "cm" => self.exit.cs_m_m = pm(f),
            "tu" => self.exit.tu_min = f.round() as i64,
            "tpp" => self.exit.tpp = b,
            "trp" => self.exit.trp = b,
            "vf" => self.exit.vf = u.min(255) as u8,
            "vs" => self.exit.vs = u.min(255) as u8,
            "va" => self.exit.va_m = pm(f),
            "vb" => self.exit.vb_pm = pm(f),
            "vp" => self.exit.vp_m = pm(f),
            "v0" => self.exit.v0_pm = pm(f),
            "vm" => self.exit.vm_pm = pm(f),
            "vz" => self.exit.vz = u.min(255) as u8,
            "vr" => self.exit.vr_m = pm(f),
            "w2" => self.exit.w2_pct = f.round() as i64,
            "e2t" => self.exit.e2t = f.round() as i64,
            "s2" => self.exit.s2 = b,
            "e1t" => self.exit.e1t = f.round() as i64,
            "tn" => self.exit.tn_pct = f.round() as i64,
            "tno" => self.exit.tno_m = pm(f),
            "fg" => self.sig.fg_m = pm(f),
            "nf" => self.sig.nf = u.min(255) as u8,
            "ev" => self.sig.ev = u.min(255) as u8,
            "ea" => self.sig.ea_m = pm(f),
            "el" => self.sig.el_pm = pm(f),
            "eh" => self.sig.eh_pm = pm(f),
            "vx" => self.sig.vx_pm = pm(f),
            "hr" => self.sig.hr_min = (f * 60.0).round() as i64,
            "hw" => self.sig.hw_min = (f * 60.0).round() as i64,
            "fv" => self.filt.v = Some(f),
            "c" => self.port.cool_min = u,
            "m" => self.port.m_stops = u,
            "mm" => self.port.mm_min = u,
            "s_glob" => self.port.s_glob = u,
            "w" => self.port.w_min = u,
            "ww" => self.port.ww_min = u,
            "p" => self.port.p = u,
            "reentry" => self.port.reentry = u.min(255) as u8,
            "busy" => self.port.busy = b,
            "pr" => self.port.pr = u.min(255) as u8,
            "pw" => self.port.pw_ms = f.round() as i64,
            "mk" => self.mk = u.min(255) as u8,
            "ek" => self.ek = f.round() as i64,
            "xb" => self.xb = pm(f),
            _ => bail!("неизвестный ключ {key}"),
        }
        Ok(())
    }

    pub fn validate(&self) -> Result<()> {
        // TK-681: окно 5 — только у фильтра живости (`natr_p`); NATR выхода — прежние периоды
        if !(NATR_PERIODS.contains(&self.sig.natr_p) || self.sig.natr_p == 5)
            || !NATR_PERIODS.contains(&self.exit.natr_p)
        {
            bail!("период NATR должен быть из {NATR_PERIODS:?} (natr_p — ещё 5)");
        }
        if !super::data::TF_MINUTES.contains(&self.sig.lv.tf_min) {
            bail!("tf должен быть из {:?}", super::data::TF_MINUTES);
        }
        if self.exit.t_low_m >= self.exit.t_high_m {
            bail!("t_low должен быть меньше t_high (лестница тейков идёт вверх)");
        }
        if self.sig.ap > 2 {
            bail!("ap ∈ {{0, 1, 2}}");
        }
        if self.sig.lv.lx > 4 {
            bail!("lx ∈ {{0, 1, 2, 3, 4}}");
        }
        if self.sig.lv.lw > 6 {
            bail!("lw ∈ 0…6 (дни от начала сетки до первого понедельника)");
        }
        if matches!(self.sig.lv.lx, 1..=3) && self.sig.lv.tf_min != 60 {
            bail!("lx = 1, 2, 3 требует tf = 60 (уровни источника — по часовым барам)");
        }
        self.validate_line()?;
        if self.exit.tp_gamma_m <= 0 || self.exit.tp_kappa_m <= -1000 {
            bail!("tpg > 0, tpk > −1");
        }
        if self.sig.fr_share > 100 || self.sig.fr_k == 0 || self.sig.fr_k > 1440 {
            bail!("fm ≤ 100, 1 ≤ fk ≤ 1440");
        }
        if self.exit.natr_src > 1 {
            bail!("ns ∈ {{0, 1}}");
        }
        if self.exit.ent_w_ms < 0 || self.exit.ent_x_bp < 0 {
            bail!("ew ≥ 0, ex ≥ 0");
        }
        if self.exit.ls_m < 0
            || self.exit.tx_min < 0
            || self.exit.tx_x_pm < 0
            || self.exit.rt_f_pm < 0
            || self.exit.rt_s < 0
            || self.exit.e2_pm < 0
        {
            bail!("ls, tt, tb, rf, rs, e2 ≥ 0");
        }
        if self.exit.be_mode > 3 {
            bail!("bm ∈ {{0, 1, 2, 3}}");
        }
        if self.exit.e2_pm > 0 && !self.exit.tape_entry() {
            bail!("e2 требует ew > 0 (вход по ленте)");
        }
        if self.mk > 2 {
            bail!("mk ∈ {{0, 1, 2}}");
        }
        if self.mk > 0 && !self.exit.tape_entry() {
            bail!("mk требует ew > 0 (вход по ленте)");
        }
        if self.ek < 0 {
            bail!("ek ≥ 0");
        }
        if self.ek > 0 && !self.exit.tape_entry() {
            bail!("ek требует ew > 0 (вход по ленте)");
        }
        if self.ek > 0 && self.mk > 0 {
            bail!("ek несовместим с mk");
        }
        if self.xb < 0 || self.sig.fg_m < 0 {
            bail!("xb ≥ 0, fg ≥ 0");
        }
        if self.xb > 0 && !self.exit.tape_entry() {
            bail!("xb требует ew > 0 (вход по ленте)");
        }
        if self.xb > 0 && (self.mk > 0 || self.ek > 0) {
            bail!("xb несовместим с mk и ek");
        }
        if self.xb > 0 && self.exit.split() {
            bail!("xb несовместим с e2 / e2t (вся ставка одним входом на срабатывании)");
        }
        self.validate_split()?;
        self.validate_e1t()?;
        self.validate_tn()?;
        if self.exit.tpp && self.exit.tj_m > 0 {
            bail!("tpp (тейки в процентах) несовместим с tj (шаг лестницы в NATR)");
        }
        if self.port.reentry > 2 {
            bail!("reentry ∈ {{0, 1, 2}}");
        }
        if self.port.pr > 3 {
            bail!("pr ∈ {{0, 1, 2, 3}}");
        }
        if !(0..=60_000).contains(&self.port.pw_ms) {
            bail!("pw ∈ [0, 60000] мс (признаки кандидата берутся на минуте сигнала)");
        }
        if self.port.pw_ms > 0
            && (!self.exit.tape_entry()
                || self.mk > 0
                || self.ek > 0
                || self.xb > 0
                || self.exit.e1t != 0)
        {
            bail!("pw требует ew > 0 (вход по ленте) и несовместим с mk, ek, xb, e1t");
        }
        self.validate_v21()?;
        self.validate_stops()?;
        self.validate_vol()?;
        self.validate_zone()?;
        if self.exit.n_tp < 2 || self.port.p == 0 || self.sig.lv.k == 0 || self.sig.lv.n == 0 {
            bail!("n_tp ≥ 2, p ≥ 1, k ≥ 1, n_bounce ≥ 1");
        }
        Ok(())
    }

    /// §13.31: `nx`, `sl`, `cw/cl/ch/cm` ≥ 0; ключи проторговки заданы только вместе с `cw` (ключ без `cw` — опечатка, а не «выкл.»); `cl < ch`.
    fn validate_stops(&self) -> Result<()> {
        let e = &self.exit;
        if e.nx_m < 0
            || e.sl_m < 0
            || e.cs_w < 0
            || e.cs_lo_m < 0
            || e.cs_hi_m < 0
            || e.cs_m_m < 0
            || e.sm_pm < 0
            || e.tu_min < 0
        {
            bail!("nx, sl, cw, cl, ch, cm, tu ≥ 0");
        }
        if e.cs_w == 0 && (e.cs_lo_m != 0 || e.cs_hi_m != 0 || e.cs_m_m != 0) {
            bail!("cl/ch/cm требуют cw > 0 (проторговка)");
        }
        if e.cs_w > 0 && e.cs_lo_m >= e.cs_hi_m {
            bail!("cw требует ch > cl (допустимая ширина проторговки)");
        }
        if e.sm_pm > 0 && e.nx_m == 0 && e.sl_m == 0 && e.cs_w == 0 {
            bail!("sm требует nx, sl или cw (нижний зажим цен от уровня)");
        }
        Ok(())
    }

    /// TK-677: `e2t` — вместо `e2`, по ленте; `w2`, `s2` — только при включённом втором входе (ключ без него — опечатка, а не «выкл.»).
    fn validate_split(&self) -> Result<()> {
        let e = &self.exit;
        if e.e2t < 0 {
            bail!("e2t ≥ 0");
        }
        if e.e2t > 0 && e.e2_pm > 0 {
            bail!("e2t несовместим с e2 (одно правило второго входа)");
        }
        if e.e2t > 0 && !e.tape_entry() {
            bail!("e2t требует ew > 0 (вход по ленте)");
        }
        if !e.split() && (e.w2_pct != 50 || e.s2) {
            bail!("w2, s2 требуют второй вход (e2 > 0 или e2t > 0)");
        }
        if !(5..=95).contains(&e.w2_pct) {
            bail!("w2 ∈ [5, 95] (% ставки во втором лоте)");
        }
        if e.s2 && e.sn_m != 0 {
            bail!("s2 (общий стоп позиции) несовместим с sn (NATR-стоп лота)");
        }
        Ok(())
    }

    /// TK-688: `e1t` — вход одной лимиткой до уровня (В-178): `0 ≤ e1t ≤ 1000` тиков, за уровнем не регистрируется.
    fn validate_e1t(&self) -> Result<()> {
        let e = &self.exit;
        if !(0..=1000).contains(&e.e1t) {
            bail!("e1t ∈ [0, 1000] (тики до уровня; за уровнем не регистрируется, В-178)");
        }
        if e.e1t != 0 && !e.tape_entry() {
            bail!("e1t требует ew > 0 (вход по ленте)");
        }
        if e.e1t != 0 && (self.mk > 0 || self.ek > 0 || self.xb > 0 || e.split()) {
            bail!("e1t несовместим с mk, ek, xb, e2, e2t (вся ставка одной лимитной заявкой)");
        }
        Ok(())
    }

    /// TK-697 D № 1: `lk` ∈ 2…8, `ld` ∈ 1…30 заданы явно (значений по умолчанию нет — сетку не подставить молча), `lsl` ∈ 0…100 (NATR60 в час, 0 — без предела) —
    /// ровно при `lx = 4`; ключ без `lx = 4` — опечатка, а не «выкл.». `ap ≠ 0` несовместим: `came_from_above` сравнивает историю суток с одной ценой уровня,
    /// у движущейся линии она не определена.
    fn validate_line(&self) -> Result<()> {
        let lv = &self.sig.lv;
        if lv.lx != 4 {
            if lv.lk != 0 || lv.ld != 0 || lv.lsl_m != 0 {
                bail!("lk, ld, lsl требуют lx = 4 (наклонная линия)");
            }
            return Ok(());
        }
        if !(2..=8).contains(&lv.lk) {
            bail!("lx = 4 требует lk ∈ 2…8 (число точек линии)");
        }
        if !(1..=30).contains(&lv.ld) {
            bail!("lx = 4 требует ld ∈ 1…30 (давность старшей точки, сутки)");
        }
        if !(0..=100_000).contains(&lv.lsl_m) {
            bail!("lsl ∈ [0, 100] (предел наклона, NATR60 в час; 0 — без предела)");
        }
        if self.sig.ap != 0 {
            bail!("ap ≠ 0 несовместим с lx = 4 (подход «сверху» сравнивает историю с одной ценой уровня)");
        }
        Ok(())
    }

    /// TK-697: `tn` ∈ [0, 100] (0 — выкл.), `tno` ≥ 0 и только вместе с `tn > 0` (отступ без доли — опечатка, а не «выкл.»).
    fn validate_tn(&self) -> Result<()> {
        let e = &self.exit;
        if !(0..=100).contains(&e.tn_pct) {
            bail!("tn ∈ [0, 100] (% позиции у первого тейка у следующего уровня; 0 — выкл.)");
        }
        if e.tno_m < 0 {
            bail!("tno ≥ 0 (отступ тейка от следующего уровня внутрь, NATR)");
        }
        if e.tn_pct == 0 && e.tno_m != 0 {
            bail!("tno требует tn > 0 (тейк у следующего уровня)");
        }
        Ok(())
    }

    /// TK-676: стоп от волатильности (`vf/vs/va/vb/vp/v0/vm/vz/vr`) — параметр задан ровно для своей формы; ключ без `vf` — опечатка, а не «выкл.».
    fn validate_vol(&self) -> Result<()> {
        let e = &self.exit;
        if e.vf == 0 {
            if e.vs != 0
                || e.va_m != 0
                || e.vb_pm != 0
                || e.vp_m != 0
                || e.v0_pm != 0
                || e.vm_pm != 500
                || e.vz != 0
                || e.vr_m != 0
            {
                bail!("vs/va/vb/vp/v0/vm/vz/vr требуют vf > 0 (стоп от волатильности)");
            }
            return Ok(());
        }
        if e.vf > 4 {
            bail!("vf ∈ {{0, 1, 2, 3, 4}}");
        }
        if !(1..=8).contains(&e.vs) {
            bail!("vs ∈ 1…8 (NATR 5/15/30/60, реализованная 5/15/30/60)");
        }
        if e.va_m <= 0 || e.vm_pm < 0 || e.vr_m < 0 {
            bail!("va > 0, vm ≥ 0, vr ≥ 0");
        }
        for (f, v, name) in [(2u8, e.vb_pm, "vb"), (3, e.vp_m, "vp"), (4, e.v0_pm, "v0")] {
            if e.vf == f && v <= 0 {
                bail!("vf={f} требует {name} > 0");
            }
            if e.vf != f && v != 0 {
                bail!("{name} задан при vf={}", e.vf);
            }
        }
        if (e.vz != 0) != (e.vr_m != 0) {
            bail!("vz и vr задаются вместе");
        }
        if e.vz != 0 && (!(1..=8).contains(&e.vz) || e.vz == e.vs) {
            bail!("vz ∈ 1…8 и ≠ vs");
        }
        if e.sn_m != 0 {
            bail!("vf несовместим с sn (одно расстояние стопа от волатильности)");
        }
        Ok(())
    }

    /// TK-681: источник фильтра живости (`nf`) и зона входа от волатильности (`ev/ea/el/eh`); ключ без `ev` — опечатка, а не «выкл.».
    fn validate_zone(&self) -> Result<()> {
        let s = &self.sig;
        if s.nf > 8 || s.ev > 8 {
            bail!("nf, ev ∈ 0…8 (0 — выкл.; NATR 5/15/30/60, реализованная 5/15/30/60)");
        }
        // TK-738: потолок RV60 и окно часа UTC; `hr` без `hw` — опечатка, а не «выкл.»
        if s.vx_pm < 0 {
            bail!("vx ≥ 0 (потолок RV60, проценты цены; 0 — выкл.)");
        }
        if !(0..=1440).contains(&s.hw_min) || !(0..1440).contains(&s.hr_min) {
            bail!("hr ∈ [0; 24) ч, hw ∈ [0; 24] ч (0 — выкл.)");
        }
        if s.hw_min == 0 && s.hr_min != 0 {
            bail!("hr требует hw > 0 (окно часа UTC)");
        }
        if s.ev == 0 {
            if s.ea_m != 0 || s.el_pm != 50 || s.eh_pm != 1000 {
                bail!("ea/el/eh требуют ev > 0 (зона входа от волатильности)");
            }
            return Ok(());
        }
        if s.ea_m <= 0 || s.el_pm <= 0 || s.el_pm > s.eh_pm {
            bail!("ev требует ea > 0 и 0 < el ≤ eh");
        }
        Ok(())
    }

    /// §13.21: NATR-стоп (`sn/sg/sb/sr/sd`), шаг тейков (`tj`), фильтр признаков (`fa/fo/fv`).
    fn validate_v21(&self) -> Result<()> {
        let e = &self.exit;
        if e.sn_m < 0 || e.sb_m < 0 || e.sr_m < 0 || e.sd_m < 0 || e.tj_m < 0 {
            bail!("sn, sb, sr, sd, tj ≥ 0");
        }
        if e.sg > 3 {
            bail!("sg ∈ {{0, 1, 2, 3}}");
        }
        if e.tl > 2 {
            bail!("tl ∈ {{0, 1, 2}}");
        }
        if e.sn_m == 0 && (e.sg != 0 || e.sb_m != 0 || e.sr_m != 0 || e.sd_m != 0) {
            bail!("sg/sb/sr/sd требуют sn > 0 (NATR-стоп)");
        }
        if e.sn_m > 0 && e.natr_src != 0 {
            bail!("sn требует ns = 0: NATR стопа — закрытая минутка t−1");
        }
        // параметр семейства задан ровно для своего семейства: ключ без семейства (или семейство без ключа) — опечатка, а не «выкл.»
        let fam = [(1u8, e.sb_m, "sb"), (2, e.sr_m, "sr"), (3, e.sd_m, "sd")];
        for (g, v, name) in fam {
            if e.sg == g && v == 0 {
                bail!("sg={g} требует {name} > 0");
            }
            if e.sg != g && v != 0 {
                bail!("{name} задан при sg={}", e.sg);
            }
        }
        let fl = &self.filt;
        if fl.on() {
            if !fl
                .name
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_')
            {
                bail!("fa: имя признака — буквы, цифры, «_»: {}", fl.name);
            }
            let (Some(_), Some(v)) = (fl.hi, fl.v) else {
                bail!("fa требует fo (lo|hi) и fv (число)");
            };
            if !v.is_finite() {
                bail!("fv должен быть конечным числом");
            }
        } else if fl.hi.is_some() || fl.v.is_some() {
            bail!("fo/fv без fa");
        }
        Ok(())
    }
}

/// Строка `<id> k=v k=v…` → (id, параметры).
pub fn parse_variant(line: &str) -> Result<(String, Params)> {
    let mut it = line.split_whitespace();
    let Some(id) = it.next() else {
        bail!("пустая строка варианта");
    };
    let mut p = Params::base();
    for kv in it {
        let Some((k, v)) = kv.split_once('=') else {
            bail!("ожидалось ключ=значение: {kv}");
        };
        p.set(k, v)?;
    }
    p.validate()?;
    Ok((id.to_string(), p))
}
