"""TK-729: закрытый список вариантов §2 (PS-1062) и сборка подключаемых компонентов (центр, фильтр, отбор) для ядра.

Центр / фильтр сейчас — EMA и ER60 из §2 (PS-1062); если поправка заменит их (модель Орнштейна–Уленбека), меняется только `build_components` и `comp_features`.
"""
from __future__ import annotations

import numpy as np

import tk729_lib as L

AS = (0.5, 1.0, 2.0)  # a: g = a·σ60
QS = (1 / 3, 1 / 2)  # q: квантиль ER60
T_START_C0 = L.DAY0 * 1440  # C0 — с 01.01 (§1)
T_START_AG = (L.DAY0 + L.BURN) * 1440  # AG — с 31.01 (разгон фильтров 30 суток)
TIMEX_MIN = 360  # запас старше 6 ч
PAUSE_MIN = 60  # пауза монеты после выхода за край
ABL = ("AB2", "AB3", "AB4", "AB5")  # без составляющей 2 / 3 / 4 / 5


def qname(q: float) -> str:
    return "q%d" % round(q * 100)


def ag_name(a: float, q: float) -> str:
    return "AG_a%g_%s" % (a, qname(q))


def base_variants() -> list[dict]:
    """C0 + AG(a, q) — 7 вариантов фазы 1."""
    v = [dict(name="C0", kind="C0", a=0.0, q=None, P=dict(g0=0.005, a=0.0, use_cen=0, b=0.0, timex=0, pause=0, t_start=T_START_C0), filt=False, sel=False)]
    for a in AS:
        for q in QS:
            v.append(dict(name=ag_name(a, q), kind="AG", a=a, q=q, P=dict(a=a, use_cen=1, b=0.5, timex=TIMEX_MIN, pause=PAUSE_MIN, t_start=T_START_AG), filt=True, sel=True))
    return v


def ablation_variants(a: float, q: float, tag: str = "") -> list[dict]:
    """4 абляции при выбранных (a, q): по одной составляющей выключено."""
    full = [v for v in base_variants() if v["name"] == ag_name(a, q)][0]
    out = []
    for ab in ABL:
        P = dict(full["P"])
        filt, sel = full["filt"], full["sel"]
        if ab == "AB2":
            P["use_cen"] = 0
        elif ab == "AB3":
            P["b"] = 0.0
        elif ab == "AB4":
            filt = False
        elif ab == "AB5":
            sel = False
        out.append(dict(name="%s_%s" % (ab, full["name"]), kind=ab, a=a, q=q, P=P, filt=filt, sel=sel))
    return out


def coin_features(S: dict) -> dict:
    """Признаки монеты, общие для всех вариантов (по закрытой минуте t): σ60, шаг цены, ER60 и пороги θ по обоим q, автокорреляция 5-минуток, EMA центра, NATR30."""
    rr = L.returns(S)
    F = dict(rv60=L.rv_sqrt_sum(S, 60, rr), tick=L.tick_days(S))
    er = L.er_kaufman(S, 60)
    F["er60"] = er
    F["thr"] = L.er_thresholds(er, list(QS))  # (NDAYS, 2)
    F["sel"] = L.sel_days(L.autocorr5_days(S))
    F["ema"] = L.center_ema(S, 60.0)
    F["natr30"] = L.natr(S, 30)
    return F


def build_components(F: dict, v: dict, n: int):
    """cen, allow, skw для варианта v (None = ядро берёт значения по умолчанию)."""
    cen = F["ema"] if v["P"].get("use_cen") else None
    thr = F["thr"][:, QS.index(v["q"])] if v["filt"] else None
    allow = L.allow_mask(F["er60"], thr, F["sel"] if v["sel"] else None)
    return cen, allow, None


def run_variant(S: dict, F: dict, v: dict) -> np.ndarray:
    cen, allow, skw = build_components(F, v, len(S["c"]))
    return L.run_coin(S, F, L.make_params(**v["P"]), cen=cen, allow=allow, skw=skw)
