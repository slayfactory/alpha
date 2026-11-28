"""TK-729: закрытый список вариантов §3 (PS-1063) и сборка компонентов бота «маркетмейкер возврата к среднему» (MR) для ядра `tk729_lib.grid_run`.

Компоненты (все по прошлому): OU по монете (AR(1), окно 1 440 мин, пересчёт раз в 15 мин) → условие торговли (t Дики–Фуллера < −2,86 и 5 ≤ HL ≤ HLmax), σ_eq → шаг g = c·σ_eq (не ниже 0,15 %);
центр — фильтр Калмана (R = σ_eq², Q — дисперсия минутного сдвига 60-минутного среднего за 24 ч); датчик срыва RV60 > 3·σ_eq; наклон q·g/2; объёмы убывают с запасом;
фильтры шока BTC и оборота монеты; выходы за край / по времени (ядро).

Толкования (в отчёт, «Отступления»): датчик срыва — RV60 (доля цены на 60 мин) против 3·σ_eq (доля цены), без добавочного √60; Q — дисперсия d_s = (M60_s − M60_{s−60})/60 за прошлые 1 440 мин (около среднего);
в абляции (г), где условие п. 1 снято, σ_eq при b ∉ (0, 1) берётся равной RV60 (иначе шага нет).
"""
from __future__ import annotations

import numpy as np

import tk729_lib as L

CS = (0.5, 1.0, 1.5)  # c: g = c·σ_eq
HLS = (60, 120)  # HLmax, минут
HL_MIN = 5.0
DF_T = -2.86  # порог Дики–Фуллера 5 %
GMIN = 0.0015  # шаг не ниже 0,15 %
BREAK_K = 3.0
T_START_C0 = L.DAY0 * 1440  # C0 — с 01.01 (§1)
T_START_MR = (L.DAY0 + L.BURN) * 1440  # MR — с 21.01 (разгон 20 суток)
TIMEX_MIN = 360
PAUSE_MIN = 60
TAUS = (1.0, 2.0)  # порог тренда τ (§5.4)
T_LAG = 240  # окно тренда, минут (§5.2)
ABL = ("ABa", "ABb", "ABc", "ABd", "ABe", "ABf")
ABL_TEXT = {"ABa": "центр = среднее AR(1) вместо Калмана", "ABb": "без наклона по запасу", "ABc": "объёмы растут к краю (25·(1+0,5(k−1)), норм. 250 $)",
            "ABd": "без условия п. 1 (все монеты)", "ABe": "без фильтров шока п. 6 и датчика срыва п. 3", "ABf": "центр и шаг заморожены при запасе (§4)"}


def mr_name(c: float, hlmax: int) -> str:
    return "MR_c%g_h%d" % (c, hlmax)


_MR_P = dict(use_cen=1, gmode=1, vmode=1, dec=0.05, b=0.0, timex=TIMEX_MIN, pause=PAUSE_MIN, t_start=T_START_MR, skew_coef=0.5)


def base_variants() -> list[dict]:
    """C0 + MR(c, HLmax) — 7 вариантов фазы base."""
    v = [dict(name="C0", kind="C0", c=None, hlmax=None, P=dict(g0=0.005, use_cen=0, b=0.0, timex=0, pause=0, t_start=T_START_C0), center="none", ou_cond=False, shocks=False, step=None)]
    for c in CS:
        for hl in HLS:
            v.append(dict(name=mr_name(c, hl), kind="MR", c=c, hlmax=hl, P=dict(_MR_P), center="kalman", ou_cond=True, shocks=True, step="eq"))
    return v


def ablation_variants(c: float, hlmax: int) -> list[dict]:
    """5 абляций при выбранных (c, HLmax): по одной составляющей выключено."""
    full = [v for v in base_variants() if v["name"] == mr_name(c, hlmax)][0]
    out = []
    for ab in ABL:
        v = dict(full, P=dict(full["P"]), kind=ab, name="%s_%s" % (ab, full["name"]))
        if ab == "ABa":
            v["center"] = "mu"
        elif ab == "ABb":
            v["P"]["skew_coef"] = 0.0
        elif ab == "ABc":
            v["P"].update(vmode=0, b=0.5)
        elif ab == "ABd":
            v["ou_cond"] = False
            v["step"] = "eq_or_rv"
        elif ab == "ABe":
            v["shocks"] = False
        elif ab == "ABf":
            v["P"]["freeze"] = 1
        out.append(v)
    return out


_BTC = {}
_BTCT = {}


def regime_T(kal: np.ndarray, sig: np.ndarray, lag: int = T_LAG, hold: int = 15) -> np.ndarray:
    """§5.2: тренд монеты T = ln(K_t / K_{t−240}) / σ_eq (K — центр Калмана §3, σ_eq — размах §3); пересчёт раз в 15 минут (значение на закрытии минуты (t // 15)·15). NaN — σ_eq или K неизвестны."""
    n = len(kal)
    ks = np.full(n, np.nan)
    ks[lag:] = kal[:-lag]
    with np.errstate(invalid="ignore", divide="ignore"):
        t_raw = np.log(kal / ks) / sig
    return L.hold_every(t_raw, hold)


def reg_from_T(T: np.ndarray, tau: float) -> np.ndarray:
    """Режим: +1 вверх (T ≥ τ), −1 вниз (T ≤ −τ), 0 флэт (|T| < τ или T неизвестен)."""
    with np.errstate(invalid="ignore"):
        return np.where(T >= tau, 1, np.where(T <= -tau, -1, 0)).astype(np.int8)


def combine_btc(reg: np.ndarray, reg_btc: np.ndarray) -> np.ndarray:
    """§5.4: направление включается, только если режим BTC того же знака, иначе монета считается во флэте."""
    return np.where(reg == reg_btc, reg, 0).astype(np.int8)


def btc_T_cached() -> np.ndarray:
    """Тренд BTC T (тот же расчёт, что у монеты, по собственным Калману и σ_eq BTC) — один раз на процесс."""
    if "T" not in _BTCT:
        Sb = L.load_coin("BTCUSDT")
        _BTCT["T"] = coin_features(Sb)["T"]
    return _BTCT["T"]


def btc_block_cached() -> np.ndarray:
    """Шок рынка по BTCUSDT — считается один раз на процесс."""
    if "b" not in _BTC:
        Sb = L.load_coin("BTCUSDT")
        rv = L.rv_sqrt_sum(Sb, 60)
        _BTC["b"] = L.btc_block(Sb, rv)
    return _BTC["b"]


def coin_features(S: dict, mkt_block: np.ndarray | None = None) -> dict:
    """Признаки монеты, общие для вариантов (по закрытой минуте t): σ60, шаг цены, OU (held 15 мин), центр Калмана, среднее AR(1), шок BTC / оборота, NATR30 для разрезов."""
    n = len(S["c"])
    rr = L.returns(S)
    rv60 = L.rv_sqrt_sum(S, 60, rr)
    ou = L.ou_rolling(S, 1440)
    H = {k: L.hold_every(v, 15) for k, v in ou.items()}
    Q = L.ffill(L.hold_every(L.kalman_q(S), 15))
    sig = H["sig_eq"]
    with np.errstate(invalid="ignore"):
        R = L.ffill(np.where(np.isfinite(sig), sig ** 2, rv60 ** 2))
        y = np.where(S["pres"], np.log(S["c"]), 0.0)
    kal = np.exp(L.kalman_center(y, S["pres"], R, Q))
    mkt = np.zeros(n, bool) if mkt_block is None else mkt_block
    return dict(T=regime_T(kal, sig), rv60=rv60, tick=L.tick_days(S), kal=kal, mu=L.ffill(H["mu"]), hl=H["hl"], tstat=H["tstat"], sig=sig, sig_or_rv=np.where(np.isfinite(sig), sig, rv60),
                shock=L.turnover_block(S) | mkt, natr30=L.natr(S, 30))


def build_components(F: dict, v: dict):
    """cen, allow, gfr для варианта v."""
    if v["kind"] == "C0":
        return None, None, None
    sig_used = F["sig_or_rv"] if v["step"] == "eq_or_rv" else F["sig"]
    with np.errstate(invalid="ignore"):
        if v["ou_cond"]:
            ok = (F["tstat"] < DF_T) & (F["hl"] >= HL_MIN) & (F["hl"] <= v["hlmax"])
        else:
            ok = np.isfinite(sig_used)
        if v["shocks"]:
            ok = ok & (F["rv60"] <= BREAK_K * sig_used) & ~F["shock"]
    cen = F["kal"] if v["center"] == "kalman" else F["mu"]
    gfr = np.maximum(v["c"] * sig_used, GMIN)
    return cen, ok.astype(np.uint8), gfr


def rd_variants() -> list[dict]:
    """§5: 4 варианта RD(τ, BTC) на базе MR(1,5; 120) с замороженным центром (§4) + 4 прогона разметки 5.1 (те же MR и MR-заморозка, торговля без режима, режим только метка)."""
    mr = [v for v in base_variants() if v["name"] == mr_name(1.5, 120)][0]
    fz = [v for v in ablation_variants(1.5, 120) if v["kind"] == "ABf"][0]
    out = []
    for tau in TAUS:
        for btc in (0, 1):
            v = dict(fz, P=dict(fz["P"], regmode=1), kind="RD", name="RD_t%g_b%d" % (tau, btc), tau=tau, btc=btc, attrib=True)
            out.append(v)
    for tau in TAUS:
        out.append(dict(mr, P=dict(mr["P"]), kind="T51", name="T51_MR_t%g" % tau, tau=tau, btc=0, attrib=True))
        out.append(dict(fz, P=dict(fz["P"]), kind="T51", name="T51_FZ_t%g" % tau, tau=tau, btc=0, attrib=True))
    return out


def regime_for(F: dict, v: dict, Tb: np.ndarray | None) -> np.ndarray | None:
    if v.get("tau") is None:
        return None
    reg = reg_from_T(F["T"], v["tau"])
    if v.get("btc"):
        reg = combine_btc(reg, reg_from_T(Tb, v["tau"]))
    return reg


def run_variant(S: dict, F: dict, v: dict, Tb: np.ndarray | None = None) -> np.ndarray:
    cen, allow, gfr = build_components(F, v)
    reg = regime_for(F, v, Tb)
    return L.run_coin(S, F, L.make_params(**v["P"]), cen=cen, allow=allow, gfr=gfr, reg=reg, tag=reg, attrib=bool(v.get("attrib", False)), szm=v.get("szm"))
