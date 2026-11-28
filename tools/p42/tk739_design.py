"""TK-739 (П-42 шаг 8, §13.81–§13.81.4, PS-1084…1088): план точек — Соболь, отображение u → ключи движка, допустимость, OAT, далёкие области, коробка.
Чистые функции (numpy / scipy), без ввода-вывода и без P&L. Вариант пространства — P10 (ключи `vx`, `hr`, `hw` есть в бинаре TK-738; G-eng).
Координаты (порядок столбцов Соболя): LS, n_bounce, k, nf, u_lo, q_hi, F:n60, F:sharp, φ, w.
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")

SEED = 20261127
SEED_P10 = 20261140                  # §13.81, таблица вариантов
SEED_BOX = SEED + 1                  # коробка вокруг кандидата (§13.81.1 п. 4)
SEED_FR = SEED + 2                   # далёкие области (§13.81.1 п. 3)
COORDS = ("LS", "n_bounce", "k", "nf", "u_lo", "q_hi", "n60", "sharp", "phi", "w")
D = len(COORDS)
IDX = {c: i for i, c in enumerate(COORDS)}
LS_VALUES = ((0, 30), (0, 60), (0, 240), (1, 60), (3, 60))      # (lx, tf)
NF_VALUES = (0, 3, 8)
OFF_FROM = 0.85                                                 # u ≥ 0,85 → «выкл.» (доля 15 %, Д6)
U_LO = (0.10, 0.80)
Q_HI_GAP, Q_HI_MAX = 0.15, 0.95
N60_Q = (0.20, 0.90)
W_RANGE = (4.0, 24.0)
NB_RANGE, K_RANGE = (1, 5), (1, 4)
SHARP_X = 1.6                                                   # `thr674.json` S / SHARP / on (найден на SEL; метка «найдено на этих данных»)
BASE_LS, BASE_NB, BASE_K, BASE_NF = (0, 60), 2, 2, 0
BASE_NATR = 0.4                                                 # %
SIDE = "short"
BIN_SHORT = "562f689967dde28ebce44f4a00f1b7af"                  # `tk718_cells.BIN['short']` (md5 бинаря строк БД стороны, охрана `tk700_mass`)

# δ (назначено, §13.81.1 п. 4): целые — ±1 шаг; u_lo, q_hi, F:n60 — ±0,10 длины интервала u; φ — ±0,10·24 ч; w — ±0,10·20 ч
DELTA = dict(n_bounce=1, k=1, u_lo=0.10, q_hi=0.10, n60=0.10, phi=2.4, w=2.0)
ORD_COORDS = ("n_bounce", "k", "u_lo", "q_hi", "n60", "phi", "w")   # d = 7 порядковых / непрерывных (§13.81.1 п. 4)


def _clip_round(x, lo, hi):
    return int(min(max(int(np.floor(x + 0.5)), lo), hi))


def map_u(u):
    """u ∈ [0,1)^10 → значения координат (§13.81.1 п. 1). q_hi / n60 / w: u ≥ 0,85 → None («выкл.»); при w «выкл.» φ не действует (None)."""
    u = np.asarray(u, float)
    c = {}
    c["LS"] = LS_VALUES[min(int(u[IDX["LS"]] * 5), 4)]
    c["n_bounce"] = _clip_round(NB_RANGE[0] + u[IDX["n_bounce"]] * (NB_RANGE[1] - NB_RANGE[0]), *NB_RANGE)
    c["k"] = _clip_round(K_RANGE[0] + u[IDX["k"]] * (K_RANGE[1] - K_RANGE[0]), *K_RANGE)
    c["nf"] = NF_VALUES[min(int(u[IDX["nf"]] * 3), 2)]
    c["u_lo"] = U_LO[0] + u[IDX["u_lo"]] * (U_LO[1] - U_LO[0])
    for name, lo_fn, hi in (("q_hi", lambda: c["u_lo"] + Q_HI_GAP, Q_HI_MAX), ("n60", lambda: N60_Q[0], N60_Q[1])):
        v = u[IDX[name]]
        if v >= OFF_FROM:
            c[name] = None
        else:
            lo = lo_fn()
            c[name] = lo + (v / OFF_FROM) * (hi - lo)
    c["sharp"] = bool(u[IDX["sharp"]] >= 0.5)
    vw = u[IDX["w"]]
    if vw >= OFF_FROM:
        c["w"], c["phi"] = None, None
    else:
        c["w"] = W_RANGE[0] + (vw / OFF_FROM) * (W_RANGE[1] - W_RANGE[0])
        c["phi"] = 24.0 * u[IDX["phi"]]
    return c


def base_coords(gm):
    """Координаты базы B*: LS (0,60), n_bounce 2, k 2, nf 0, u_lo — эквивалент 0,4 % по таблице G-map, остальное выкл."""
    return dict(LS=BASE_LS, n_bounce=BASE_NB, k=BASE_K, nf=BASE_NF, u_lo=gm.u_base(), q_hi=None, n60=None, sharp=False, phi=None, w=None)


class GMap:
    """Таблица «квантиль → абсолютный порог» (G-map, Д10): V = NATR30 / RV60 на сигнальных минутах B* (без фильтра живости), n60 — на строках базы окон.
    Пороги — в % (V) и в штуках (n60); вход — отсортированные массивы."""

    def __init__(self, natr30, rv60, n60):
        self.natr30 = np.sort(np.asarray(natr30, float))
        self.rv60 = np.sort(np.asarray(rv60, float))
        self.n60 = np.sort(np.asarray(n60, float))
        self._ub = None

    def q(self, arr, u):
        return float(np.quantile(arr, min(max(u, 0.0), 1.0)))

    def thr_natr(self, nf, u):
        """Порог `natr_min`, %: квантиль u распределения источника фильтра живости (NATR30 для nf 0 / 3, RV60 для nf 8); округление 0,001 %."""
        if nf != 8 and abs(u - self.u_base()) < 1e-12:             # точка базы — точно строка B* (natr_min = 0,4)
            return BASE_NATR
        return round(self.q(self.rv60 if nf == 8 else self.natr30, u), 3)

    def thr_vx(self, q):
        return round(self.q(self.rv60, q), 3)

    def thr_n60(self, q):
        return round(self.q(self.n60, q), 1)

    def u_base(self):
        """Ранг 0,4 % в распределении NATR30 на сигнальных минутах B* (доля значений ниже 0,4 %)."""
        if self._ub is None:
            self._ub = float((self.natr30 < BASE_NATR).mean())
        return self._ub


def ls_eff(c):
    """Действующие n_bounce / k: при lx = 1, 3 (`params.rs`: `k`, `d`, `n_bounce`, `tau`, `l_days` не действуют) — значения базы."""
    if c["LS"][0] in (1, 3):
        return BASE_NB, BASE_K
    return c["n_bounce"], c["k"]


def f_compat(c, gm):
    """Фильтр по таблице признаков (F:n60, F:sharp) честен, только если кандидаты точки ⊆ строк таблицы (строки = кандидаты базы окна; нет строки → кандидат проходит, `params.rs` §13.21): тот же уровень (LS, n_bounce, k),
    тот же источник живости (nf 0 ≡ 3, NATR30) и пул не слабее базы (порог ≥ 0,4 %). Правило `tk674_axes.cell_assign` / `tk674_x680.COL_BAN_KEYS`. Возвращает (ok, причина)."""
    if c["n60"] is None and not c["sharp"]:
        return True, ""
    if c["LS"] != BASE_LS or c["n_bounce"] != BASE_NB or c["k"] != BASE_K:
        return False, "F × уровень"
    if c["nf"] == 8:
        return False, "F × nf = 8"
    if gm.thr_natr(c["nf"], c["u_lo"]) < BASE_NATR:
        return False, "F × пул слабее базы"
    return True, ""


def point_cell(c, gm):
    """Координаты → ключи движка: dict(valid, why, keys (строки), filt, ckey (cell_id), line, sig, feats). Допустимость — `Params::validate` (n_bounce ≥ 1, k ≥ 1, lx ≠ 0 ⇒ tf = 60, ключи окна) + f_compat."""
    import tk674_axes as ax
    import tk718_cells as t7
    import tk700_cells as t0
    lx, tf = c["LS"]
    nb, k = ls_eff(c)
    nf = 0 if c["nf"] in (0, 3) else 8                         # nf 3 ≡ 0 при natr_p = 30 (`tick.rs`: nf = 0 → natr_for(natr_p), nf = 3 → natr_for(VOL_PERIODS[2] = 30))
    ok, why = f_compat(c, gm)
    if lx != 0 and tf != 60:
        ok, why = False, "lx ≠ 0 требует tf = 60"
    keys0, _ = t7.split_ckey(t7.ANCHOR[SIDE])
    keys = dict(keys0)
    keys.update(tf=str(tf), lx=str(lx), n_bounce=str(nb), k=str(k), nf=str(nf), l_days="7")
    thr = gm.thr_natr(nf, c["u_lo"])
    keys["natr_min"] = "%g" % thr
    if c["q_hi"] is not None:
        keys["vx"] = "%g" % gm.thr_vx(c["q_hi"])
    if c["w"] is not None:
        keys["hr"] = "%g" % round(c["phi"], 2)
        keys["hw"] = "%g" % round(c["w"], 2)
    filt = []
    if c["n60"] is not None:
        filt.append(("n60", ">=", gm.thr_n60(c["n60"])))
    if c["sharp"]:
        filt.append(("sharp", "==", SHARP_X))
    line = t7.to_line(SIDE, keys)
    ckey = ax.canon(line, filt)
    return dict(valid=ok, why=why, keys=keys, filt=filt, ckey=ckey, line=line, sig=t0.sig_of(line),
                feats=",".join(sorted({f for f, _, _ in filt})), filt_json=[list(x) for x in filt])


def anchor_ckey():
    """Канонический ключ B* (якорь s4, `BS-l7-tf60`): должен совпасть с `tk726_cell.ckey` клетки 7180023 (G-src)."""
    import tk674_axes as ax
    import tk718_cells as t7
    keys0, _ = t7.split_ckey(t7.ANCHOR[SIDE])
    keys = dict(keys0, l_days="7", tf="60")
    return ax.canon(t7.to_line(SIDE, keys), [])


# ───────────────────────────── Соболь ─────────────────────────────

def sobol_u(d, seed, m):
    from scipy.stats import qmc
    return qmc.Sobol(d=d, scramble=True, seed=seed).random_base2(m)


def design_R(gm, S, seed=SEED_P10, m=17):
    """Опорный набор R: первые S допустимых точек потока Соболя (недопустимые заменяются следующей точкой, §13.81.1 п. 1). Возвращает (rows, replaced): rows — по порядку
    [dict(seq, u, coords, cell)], replaced — [(seq, причина)]. Эффективные дубли (одинаковый ckey) остаются в списке (сырые точки), уникальные — по `cell['ckey']`."""
    U = sobol_u(D, seed, m)
    rows, rep = [], []
    for i, u in enumerate(U):
        c = map_u(u)
        cell = point_cell(c, gm)
        if cell["valid"]:
            rows.append(dict(seq=i, u=u, coords=c, cell=cell))
            if len(rows) == S:
                break
        else:
            rep.append((i, cell["why"]))
    if len(rows) < S:
        raise SystemExit("поток Соболя 2^%d исчерпан: допустимых %d из %d" % (m, len(rows), S))
    return rows, rep


# ───────────────────────────── OAT ─────────────────────────────

def lin5(lo, hi):
    return [lo + (hi - lo) * i / 4.0 for i in range(5)]


def design_oat(gm):
    """Точки по осям: каждая координата — все значения сетки при остальных на базе B* (§13.81.1 п. 2). Категории и целые — все уровни; непрерывные — 5 уровней по интервалу, плюс «выкл.» у необязательных.
    φ действует только при w < 24: её точки — на w = 14 ч (середина интервала), φ ∈ {0, 6, 12, 18}. Возвращает [(метка, координаты)]."""
    b = base_coords(gm)
    out = [("base", dict(b))]

    def put(label, **kw):
        c = dict(b)
        c.update(kw)
        out.append((label, c))
    for v in LS_VALUES:
        put("LS=%d/%d" % v, LS=v)
    for v in range(NB_RANGE[0], NB_RANGE[1] + 1):
        put("n_bounce=%d" % v, n_bounce=v)
    for v in range(K_RANGE[0], K_RANGE[1] + 1):
        put("k=%d" % v, k=v)
    for v in NF_VALUES:
        put("nf=%d" % v, nf=v)
    for v in lin5(*U_LO):
        put("u_lo=%.3f" % v, u_lo=v)
    for v in lin5(b["u_lo"] + Q_HI_GAP, Q_HI_MAX):
        put("q_hi=%.3f" % v, q_hi=v)
    for v in lin5(*N60_Q):
        put("n60=%.3f" % v, n60=v)
    put("sharp=on", sharp=True)
    for v in lin5(*W_RANGE)[:-1]:
        put("w=%.1f" % v, w=v, phi=0.0)
    for v in (0.0, 6.0, 12.0, 18.0):
        put("phi=%.0f" % v, w=14.0, phi=v)
    return out


# ───────────────────────────── далёкие области ─────────────────────────────

FR_AXES = dict(e=(0.05, 0.1, 0.15, 0.3, 0.5), rf=(0.0, 0.1, 0.2, 0.3), tt=(0, 5, 10, 20, 40, 60), d=(0.25, 0.5, 1.0, 2.0))   # значения карты TK-674 (`tk674_axes.py`)


def design_far(gm, n=128, seed=SEED_FR):
    """128 точек скремблированного Соболя в 4-мерной решётке e × rf × tt × d (480 узлов), значения — ближайшие узлы; остальные ключи — на B*; дубли склеиваются по ckey."""
    import tk674_axes as ax
    import tk718_cells as t7
    import tk700_cells as t0
    U = sobol_u(4, seed, int(np.ceil(np.log2(n))))[:n]
    names = ("e", "rf", "tt", "d")
    out = []
    for i, u in enumerate(U):
        val = {}
        for j, nm in enumerate(names):
            g = FR_AXES[nm]
            val[nm] = g[min(int(u[j] * len(g)), len(g) - 1)]
        keys0, _ = t7.split_ckey(t7.ANCHOR[SIDE])
        keys = dict(keys0, l_days="7", tf="60")
        for nm in names:
            keys[nm] = "%g" % val[nm]
        line = t7.to_line(SIDE, keys)
        out.append(dict(seq=i, u=u, far=val, cell=dict(keys=keys, filt=[], ckey=ax.canon(line, []), line=line, sig=t0.sig_of(line), feats="", filt_json=[])))
    return out


# ───────────────────────────── коробка вокруг кандидата ─────────────────────────────

def box_axial(c, gm):
    """2d осевых соседей ±δ (d = 7); координата, у которой c «выкл.» (q_hi, n60, φ / w), или граница диапазона — сосед не строится (c на границе — соседи только внутри диапазона)."""
    out = []
    lo_hi = dict(n_bounce=NB_RANGE, k=K_RANGE, u_lo=U_LO, q_hi=(c["u_lo"] + Q_HI_GAP, Q_HI_MAX), n60=N60_Q, phi=(0.0, 24.0), w=W_RANGE)
    for nm in ORD_COORDS:
        if c.get(nm) is None:
            continue
        if nm in ("phi", "w") and c["w"] is None:
            continue
        if nm in ("n_bounce", "k") and c["LS"][0] in (1, 3):
            continue                                           # не действуют при lx = 1, 3
        for s in (-1, 1):
            x = c[nm] + s * DELTA[nm] * (1.0 if nm in ("n_bounce", "k", "phi", "w") else (lo_hi[nm][1] - lo_hi[nm][0]))
            lo, hi = lo_hi[nm]
            if nm == "phi":
                x = x % 24.0                                   # φ круговая
            elif x < lo - 1e-12 or x > hi + 1e-12:
                continue
            if nm == "w" and x >= W_RANGE[1]:
                continue
            d2 = dict(c)
            d2[nm] = x
            out.append(("%s%+d" % (nm, s), d2))
    return out


def box_sobol(c, gm, n=256, seed=SEED_BOX):
    """256 точек скремблированного Соболя в коробке ±δ вокруг c (категории и «выкл.» c зафиксированы). Координаты вне диапазона обрезаются по краю интервала (c у границы — коробка срезается)."""
    act = [nm for nm in ORD_COORDS if c.get(nm) is not None and not (nm in ("phi", "w") and c["w"] is None) and not (nm in ("n_bounce", "k") and c["LS"][0] in (1, 3))]
    U = sobol_u(max(len(act), 1), seed, int(np.ceil(np.log2(n))))[:n]
    lo_hi = dict(n_bounce=NB_RANGE, k=K_RANGE, u_lo=U_LO, q_hi=(c["u_lo"] + Q_HI_GAP, Q_HI_MAX), n60=N60_Q, phi=(0.0, 24.0), w=W_RANGE)
    out = []
    for u in U:
        d2 = dict(c)
        for j, nm in enumerate(act):
            span = lo_hi[nm][1] - lo_hi[nm][0]
            half = DELTA[nm] if nm in ("n_bounce", "k", "phi", "w") else DELTA[nm] * span
            x = c[nm] + (2 * u[j] - 1) * half
            if nm == "phi":
                x = x % 24.0
            else:
                x = min(max(x, lo_hi[nm][0]), lo_hi[nm][1])
                if nm in ("n_bounce", "k"):
                    x = int(np.floor(x + 0.5))
                if nm == "w":
                    x = min(x, W_RANGE[1] - 1e-6)
            d2[nm] = x
        out.append(d2)
    return out


def near_border(c, gm):
    """Центр ближе δ к границе диапазона любой оси → O3 (§13.81.3 п. 3). Только действующие порядковые / непрерывные координаты."""
    lo_hi = dict(n_bounce=NB_RANGE, k=K_RANGE, u_lo=U_LO, q_hi=(c["u_lo"] + Q_HI_GAP, Q_HI_MAX), n60=N60_Q, w=W_RANGE)
    for nm, (lo, hi) in lo_hi.items():
        if c.get(nm) is None or (nm in ("n_bounce", "k") and c["LS"][0] in (1, 3)):
            continue
        span = hi - lo
        dlt = DELTA[nm] if nm in ("n_bounce", "k", "w") else DELTA[nm] * span
        if c[nm] - lo < dlt - 1e-12 or hi - c[nm] < dlt - 1e-12:
            return True, nm
    return False, ""


# ───────────────────────────── столбцы поверхности (§13.81.2 шаг 3) ─────────────────────────────

LIN_NAMES = ("n_bounce", "k", "u_lo", "q_hi", "n60", "w", "phi_s", "phi_c", "LS0", "LS1", "LS2", "LS3", "LS4", "nf8", "sharp", "q_off", "n60_off", "w_off")
QUAD_NAMES = ("n_bounce", "k", "u_lo", "q_hi", "n60", "w")
_IU = np.triu_indices(len(LIN_NAMES), 1)


def features(c):
    """Столбцы поверхности точки: линейные (нормированные u-координаты в [-1, 1], φ — sin / cos, категории — one-hot, индикаторы «выкл.»), квадраты порядковых и все парные произведения линейных. «Выкл.» → 0 + индикатор."""
    lin = np.zeros(len(LIN_NAMES))
    li = {n: i for i, n in enumerate(LIN_NAMES)}
    sc = lambda x, lo, hi: 2.0 * (x - lo) / (hi - lo) - 1.0       # noqa: E731
    nb_e, k_e = ls_eff(c)                                          # при lx = 1, 3 `n_bounce`, `k` не действуют — значения базы (amend L-546)
    lin[li["n_bounce"]] = sc(nb_e, *NB_RANGE)
    lin[li["k"]] = sc(k_e, *K_RANGE)
    lin[li["u_lo"]] = sc(c["u_lo"], *U_LO)
    if c["q_hi"] is None:
        lin[li["q_off"]] = 1.0
    else:
        lin[li["q_hi"]] = sc(c["q_hi"], c["u_lo"] + Q_HI_GAP, Q_HI_MAX)
    if c["n60"] is None:
        lin[li["n60_off"]] = 1.0
    else:
        lin[li["n60"]] = sc(c["n60"], *N60_Q)
    if c["w"] is None:
        lin[li["w_off"]] = 1.0
    else:
        lin[li["w"]] = sc(c["w"], *W_RANGE)
        lin[li["phi_s"]] = np.sin(2 * np.pi * c["phi"] / 24.0)
        lin[li["phi_c"]] = np.cos(2 * np.pi * c["phi"] / 24.0)
    lin[li["LS%d" % LS_VALUES.index(tuple(c["LS"]))]] = 1.0
    lin[li["nf8"]] = 1.0 if c["nf"] == 8 else 0.0
    lin[li["sharp"]] = 1.0 if c["sharp"] else 0.0
    quad = np.array([lin[li[n]] ** 2 for n in QUAD_NAMES])
    iu = _IU
    pair = lin[iu[0]] * lin[iu[1]]
    return np.concatenate([lin, quad, pair])


def n_columns():
    return len(LIN_NAMES) + len(QUAD_NAMES) + len(LIN_NAMES) * (len(LIN_NAMES) - 1) // 2


def u_dist(u1, u2):
    """Расстояние в u-координатах (G-pow8: ‖θ − θ*‖); u — сырые координаты Соболя точек."""
    return float(np.linalg.norm(np.asarray(u1, float) - np.asarray(u2, float)))
