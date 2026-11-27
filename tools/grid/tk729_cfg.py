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
ABL = ("ABa", "ABb", "ABc", "ABd", "ABe")
ABL_TEXT = {"ABa": "центр = среднее AR(1) вместо Калмана", "ABb": "без наклона по запасу", "ABc": "объёмы растут к краю (25·(1+0,5(k−1)), норм. 250 $)",
            "ABd": "без условия п. 1 (все монеты)", "ABe": "без фильтров шока п. 6 и датчика срыва п. 3"}


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
        out.append(v)
    return out


_BTC = {}


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
    return dict(rv60=rv60, tick=L.tick_days(S), kal=kal, mu=L.ffill(H["mu"]), hl=H["hl"], tstat=H["tstat"], sig=sig, sig_or_rv=np.where(np.isfinite(sig), sig, rv60),
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


def run_variant(S: dict, F: dict, v: dict) -> np.ndarray:
    cen, allow, gfr = build_components(F, v)
    return L.run_coin(S, F, L.make_params(**v["P"]), cen=cen, allow=allow, gfr=gfr)
