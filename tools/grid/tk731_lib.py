"""TK-731 (П-TK729 §8, PS-1071): ядро «развёрнутый R:R» — ES(k) ранний стоп, AB(g) анти-грид, HY(k) гибрид; C0 = ES(10).

Новый модуль рядом с `tk729_lib` (общие: загрузка, шаг цены, бутстреп, Шарп, просадка, константы; старое ядро `grid_run` не тронуто).
Грид-фаза (ES, C0, фаза сетки HY) — тот же расчёт, что у `tk729_lib.grid_run` при C0 (g = 0,5 %, 10 уровней × 25 $), с числом уровней k:
лимиты исполняются «строго за уровень», не более одного раза за минуту, встречный лимит — не раньше следующей минуты, издержки — мейкер 1,4 bps − 10 %, тейкер 3,5 bps + 1 bp на рыночных.
При k = 10 результат совпадает с C0 до доллара (тест + сверка по монетам).
Анти-грид (AB, фаза AB у HY): стоп-ордера. Стоп срабатывает при касании; исполнение — худшее из уровня и открытия минуты, + 1 bp, тейкер 3,5 bps.
Каждая минута, где что-то исполнилось, прогоняется по двум путям O→H→L→C и O→L→H→C; берётся путь с худшим P&L минуты (реализовано + оценка по закрытию), его состояние переносится дальше.
Стоп закрытия части, выставленный по ходу пути, может сработать позже на том же пути; стоп-вход той же части после её закрытия — не раньше следующей минуты (как встречный ордер C0);
стоп, при выставлении уже пройденный ценой (в т. ч. перевыставленный вход, цена закрылась за ним) — исполняется по открытию следующей минуты (худшее из уровня и открытия + 1 bp).

Цикл = от нулевого запаса до нулевого запаса. На границах половин (конец суток из `cutd`) открытый цикл режется: часть до границы оценивается по закрытию последней минуты (минус комиссии входа открытых лотов) и помечается,
остаток считается в следующей половине — Σ P&L циклов половины равна Σ P&L половины до цента (проверка).

Выход ядра `out` (NR2 × сутки отчёта) — строки R_*; гистограммы убытка на стоп hn/hs (3 вида × 2 половины × бины 0,005 $).
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
from tk729_lib import _ri, _rt, njit  # noqa: E402

KMAX = 10  # уровней у AB и край AB; у грид-фазы — k ≤ 10
EV_CAP = 1024

# раскладка вектора состояния S (все float64; индекс слота k = 1..10)
O_LST, O_LENT, O_LQ, O_LFE, O_LFR, O_LOP = 0, 11, 22, 33, 44, 55
O_SST, O_SENT, O_SQ, O_SFE, O_SFR, O_SOP = 66, 77, 88, 99, 110, 121
O_NL, O_NS, O_CYA, O_CYP = 132, 133, 134, 135
NS_ = 136

# строки выхода (по суткам отчёта)
(R_PNL, R_CN, R_CW, R_CWS, R_CLS, R_CF, R_ACT, R_EGN, R_EGU, R_STN, R_STU, R_EAN, R_EAU, R_LPN, R_LPU, R_ENT, R_G2A, R_A2G, R_SGN, R_ABS, R_GAP, R_MAB, R_MGR) = range(23)
NR2 = 23
# R_PNL — P&L суток (реализованное минус комиссии + изменение оценки открытого по закрытию); R_CN/R_CW/R_CWS/R_CLS — циклов закончено / с прибылью / Σ прибыльных / Σ прочих ($);
# R_CF — из них «разрезано на границе половины» (помечено); R_ACT — монета в работе; R_EG* — выходы по рынку за краем сетки (событие = весь запас): число, Σ $;
# R_ST* — части, закрытые своим стопом (AB); R_EA* — выходы по рынку за краем AB; R_LP* — круги сетки (лимитный выход лота); R_ENT — входов;
# R_G2A / R_A2G — переходы HY сетка→AB (пробой k-го уровня) / AB→сетка; R_SGN — Σ (лонг − шорт) в $·минут (оценка фандинга); R_ABS — Σ |запас| в $·минут; R_GAP — стопов, исполненных по открытию; R_MAB / R_MGR — минут в фазе AB / сетки.

NBIN = 24000
HW = 0.005
HLO = 100.0  # бин = floor((v + 100) / 0,005): диапазон −100 … +20 $

M_GRID, M_AB, M_HY = 0, 1, 2
BURN_ = L.BURN


def make_params(*, mode, kgrid=10, g_grid=0.005, g_ab=0.005, nlv_ab=KMAX, t_start=L.DAY0 * 1440, day0=L.DAY0, maker=L.MAKER, taker=L.TAKER, slip=L.SLIP, nom=25.0, gmin_ticks=2.0, force=0) -> np.ndarray:
    """mode 0 — сетка (ES(kgrid); kgrid = 10 — C0), 1 — AB(g_ab), 2 — HY(kgrid) (фаза сетки g_grid, фаза AB g_ab). force — только для теста: 0 — худший из двух путей (бой), 1 — только O→H→L→C, 2 — только O→L→H→C."""
    return np.array([float(mode), float(kgrid), g_grid, g_ab, float(nlv_ab), float(t_start), float(day0), maker, taker, slip, nom, gmin_ticks, float(force)], np.float64)


# ───────────────────────────── вспомогательные ─────────────────────────────

@njit(cache=True)
def _ev(ev, ctr, kind, val):
    i = ctr[0]
    if i < ev.shape[0]:
        ev[i, 0] = kind
        ev[i, 1] = val
        ctr[0] = i + 1
    else:
        ctr[1] += 1


@njit(cache=True)
def _open_lot(S, ev, ctr, acc, side, k, p, qty, fee, t):
    if S[O_NL] + S[O_NS] == 0.0:
        S[O_CYA] = 1.0
        S[O_CYP] = 0.0
    if side > 0:
        S[O_LST + k] = 1.0
        S[O_LENT + k] = p
        S[O_LQ + k] = qty
        S[O_LOP + k] = t
        S[O_LFE + k] = fee
        S[O_NL] += 1.0
    else:
        S[O_SST + k] = 1.0
        S[O_SENT + k] = p
        S[O_SQ + k] = qty
        S[O_SOP + k] = t
        S[O_SFE + k] = fee
        S[O_NS] += 1.0
    acc[0] -= fee
    _ev(ev, ctr, 4, 1.0)


@njit(cache=True)
def _close_lot(S, ev, ctr, acc, side, k, p, rate, t):
    """Закрытие лота по цене p с комиссией rate; возвращает чистый P&L лота (минус обе комиссии); конец цикла — событие 3."""
    if side > 0:
        q = S[O_LQ + k]
        gross = q * (p - S[O_LENT + k])
        fe = S[O_LFE + k]
        S[O_LST + k] = 0.0
        S[O_LFR + k] = t
        S[O_NL] -= 1.0
    else:
        q = S[O_SQ + k]
        gross = q * (S[O_SENT + k] - p)
        fe = S[O_SFE + k]
        S[O_SST + k] = 0.0
        S[O_SFR + k] = t
        S[O_NS] -= 1.0
    fc = q * p * rate
    acc[0] += gross - fc
    net = gross - fc - fe
    S[O_CYP] += net
    if S[O_NL] + S[O_NS] == 0.0:
        _ev(ev, ctr, 3, S[O_CYP])
        S[O_CYA] = 0.0
        S[O_CYP] = 0.0
    return net


@njit(cache=True)
def _close_all(S, ev, ctr, acc, cl, taker, slip, t):
    """Выход всего запаса по рынку на закрытии (худший вариант: лонг по cl·(1−slip), шорт по cl·(1+slip), тейкер); возвращает Σ чистого P&L."""
    px_l = cl * (1.0 - slip)
    px_s = cl * (1.0 + slip)
    tot = 0.0
    for k in range(1, KMAX + 1):
        if S[O_LST + k] == 1.0:
            tot += _close_lot(S, ev, ctr, acc, 1, k, px_l, taker, t)
        if S[O_SST + k] == 1.0:
            tot += _close_lot(S, ev, ctr, acc, -1, k, px_s, taker, t)
    return tot


@njit(cache=True)
def _mtm(S, cl):
    u = 0.0
    for k in range(1, KMAX + 1):
        if S[O_LST + k] == 1.0:
            u += S[O_LQ + k] * (cl - S[O_LENT + k])
        if S[O_SST + k] == 1.0:
            u += S[O_SQ + k] * (S[O_SENT + k] - cl)
    return u


@njit(cache=True)
def _fees_open(S):
    f = 0.0
    for k in range(1, KMAX + 1):
        if S[O_LST + k] == 1.0:
            f += S[O_LFE + k]
        if S[O_SST + k] == 1.0:
            f += S[O_SFE + k]
    return f


@njit(cache=True)
def _hist(hn, hs, kind, half, v):
    if half < 0:
        return
    b = int(math.floor((v + HLO) / HW))
    if b < 0:
        b = 0
    if b >= NBIN:
        b = NBIN - 1
    hn[kind, half, b] += 1.0
    hs[kind, half, b] += v


@njit(cache=True)
def _flush(evm, cm, out, do, hn, hs, hf):
    """События минуты → строки суток и гистограммы."""
    for i in range(cm[0]):
        kd = int(evm[i, 0])
        v = evm[i, 1]
        if kd == 0:
            out[R_EGN, do] += 1.0
            out[R_EGU, do] += v
            _hist(hn, hs, 0, hf, v)
        elif kd == 1:
            out[R_STN, do] += 1.0
            out[R_STU, do] += v
            _hist(hn, hs, 1, hf, v)
        elif kd == 2:
            out[R_EAN, do] += 1.0
            out[R_EAU, do] += v
            _hist(hn, hs, 2, hf, v)
        elif kd == 3:
            _cycle(out, do, v, False)
        elif kd == 4:
            out[R_ENT, do] += 1.0
        elif kd == 5:
            out[R_LPN, do] += 1.0
            out[R_LPU, do] += v
        elif kd == 6:
            out[R_GAP, do] += 1.0
    cm[0] = 0


@njit(cache=True)
def _cycle(out, do, pnl, flag):
    out[R_CN, do] += 1.0
    if pnl > 0.0:
        out[R_CW, do] += 1.0
        out[R_CWS, do] += pnl
    else:
        out[R_CLS, do] += pnl
    if flag:
        out[R_CF, do] += 1.0


# ───────────────────────────── исполнение ─────────────────────────────

@njit(cache=True)
def _grid_exec(S, ev, ctr, acc, U, D, nlv, qon_l, qon_s, t, tick, opn, hi_p, lo_p, qclose, maker, taker, slip, nom):
    """Грид-фаза: исполнение лимитов, выставленных на закрытии прошлой минуты (копия A из tk729_lib.grid_run при C0; уровни: покупка k — D[k], её продажа — D[k−1]; шорт k — U[k], выкуп — U[k−1])."""
    lo_i = _ri(lo_p, tick)
    hi_i = _ri(hi_p, tick)
    qc_i = _ri(qclose, tick)
    for k in range(1, nlv + 1):
        if S[O_LST + k] == 0.0 and qon_l and S[O_LFR + k] < t:
            pi = _ri(D[k], tick)
            if lo_i <= pi - 1:
                p = D[k]
                rate = maker
                if pi - qc_i >= 1:
                    p = min(p, opn * (1.0 + slip))
                    rate = taker
                qty = nom / p
                _open_lot(S, ev, ctr, acc, 1, k, p, qty, qty * p * rate, t)
        if S[O_LST + k] == 1.0 and S[O_LOP + k] < t:
            pi = _ri(D[k - 1], tick)
            if hi_i >= pi + 1:
                p = D[k - 1]
                rate = maker
                if qc_i - pi >= 1:
                    p = max(p, opn * (1.0 - slip))
                    rate = taker
                net = _close_lot(S, ev, ctr, acc, 1, k, p, rate, t)
                _ev(ev, ctr, 5, net)
        if S[O_SST + k] == 0.0 and qon_s and S[O_SFR + k] < t:
            pi = _ri(U[k], tick)
            if hi_i >= pi + 1:
                p = U[k]
                rate = maker
                if qc_i - pi >= 1:
                    p = max(p, opn * (1.0 - slip))
                    rate = taker
                qty = nom / p
                _open_lot(S, ev, ctr, acc, -1, k, p, qty, qty * p * rate, t)
        if S[O_SST + k] == 1.0 and S[O_SOP + k] < t:
            pi = _ri(U[k - 1], tick)
            if lo_i <= pi - 1:
                p = U[k - 1]
                rate = maker
                if pi - qc_i >= 1:
                    p = min(p, opn * (1.0 + slip))
                    rate = taker
                net = _close_lot(S, ev, ctr, acc, -1, k, p, rate, t)
                _ev(ev, ctr, 5, net)


@njit(cache=True)
def _ab_path(S, ev, ctr, acc, U, D, Ui, Di, nlv, qon_l, qon_s, t, opn, oi, hi, lo, ci, hfirst, slip, taker, nom):
    """Один путь минуты по стоп-ордерам. Стоп-покупки: вход лонга k на U[k], закрытие шорта k на D[k−1]; стоп-продажи: вход шорта k на D[k], закрытие лонга k на U[k−1]."""
    # фаза открытия: стопы, уже пройденные к открытию — по открытию (худшее из уровня и открытия) + slip
    for k in range(1, nlv + 1):
        if S[O_LST + k] == 1.0 and Ui[k - 1] >= oi:
            p = min(U[k - 1], opn) * (1.0 - slip)
            net = _close_lot(S, ev, ctr, acc, 1, k, p, taker, t)
            _ev(ev, ctr, 1, net)
            _ev(ev, ctr, 6, 1.0)
        if S[O_SST + k] == 1.0 and Di[k - 1] <= oi:
            p = max(D[k - 1], opn) * (1.0 + slip)
            net = _close_lot(S, ev, ctr, acc, -1, k, p, taker, t)
            _ev(ev, ctr, 1, net)
            _ev(ev, ctr, 6, 1.0)
    for k in range(1, nlv + 1):
        if S[O_LST + k] == 0.0 and qon_l and S[O_LFR + k] < t and Ui[k] <= oi:
            p = max(U[k], opn) * (1.0 + slip)
            qty = nom / p
            _open_lot(S, ev, ctr, acc, 1, k, p, qty, qty * p * taker, t)
            _ev(ev, ctr, 6, 1.0)
        if S[O_SST + k] == 0.0 and qon_s and S[O_SFR + k] < t and Di[k] >= oi:
            p = min(D[k], opn) * (1.0 - slip)
            qty = nom / p
            _open_lot(S, ev, ctr, acc, -1, k, p, qty, qty * p * taker, t)
            _ev(ev, ctr, 6, 1.0)
    # путь: три отрезка
    pts = np.empty(4, np.int64)
    pts[0] = oi
    pts[1] = hi if hfirst else lo
    pts[2] = lo if hfirst else hi
    pts[3] = ci
    x = oi
    for sg in range(3):
        x1 = pts[sg + 1]
        if x1 > x:
            cur = x
            while True:
                best = -1
                bk = 0
                bl = 1 << 60
                for k in range(1, nlv + 1):
                    if qon_l and S[O_LST + k] == 0.0 and S[O_LFR + k] < t:
                        lv = Ui[k]
                        if lv > cur and lv <= x1 and lv < bl:
                            bl = lv
                            best = 0
                            bk = k
                    if S[O_SST + k] == 1.0:
                        lv = Di[k - 1]
                        if lv > cur and lv <= x1 and lv < bl:
                            bl = lv
                            best = 1
                            bk = k
                if best < 0:
                    break
                if best == 0:
                    p = U[bk] * (1.0 + slip)
                    qty = nom / p
                    _open_lot(S, ev, ctr, acc, 1, bk, p, qty, qty * p * taker, t)
                else:
                    p = D[bk - 1] * (1.0 + slip)
                    net = _close_lot(S, ev, ctr, acc, -1, bk, p, taker, t)
                    _ev(ev, ctr, 1, net)
                cur = bl
        elif x1 < x:
            cur = x
            while True:
                best = -1
                bk = 0
                bl = -(1 << 60)
                for k in range(1, nlv + 1):
                    if qon_s and S[O_SST + k] == 0.0 and S[O_SFR + k] < t:
                        lv = Di[k]
                        if lv < cur and lv >= x1 and lv > bl:
                            bl = lv
                            best = 0
                            bk = k
                    if S[O_LST + k] == 1.0:
                        lv = Ui[k - 1]
                        if lv < cur and lv >= x1 and lv > bl:
                            bl = lv
                            best = 1
                            bk = k
                if best < 0:
                    break
                if best == 0:
                    p = D[bk] * (1.0 - slip)
                    qty = nom / p
                    _open_lot(S, ev, ctr, acc, -1, bk, p, qty, qty * p * taker, t)
                else:
                    p = U[bk - 1] * (1.0 - slip)
                    net = _close_lot(S, ev, ctr, acc, 1, bk, p, taker, t)
                    _ev(ev, ctr, 1, net)
                cur = bl
        x = x1


@njit(cache=True)
def _ab_minute(S, S1, S2, evm, cm, acc, ev1, c1, ev2, c2, acc1, acc2, U, D, Ui, Di, nlv, qon_l, qon_s, t, opn, oi, hi, lo, ci, cl_p, slip, taker, nom, force):
    """Минута AB: путь O→H→L→C; если что-то исполнилось — и путь O→L→H→C, берётся с меньшей суммой (реализовано + оценка открытого по закрытию)."""
    S1[:] = S
    c1[0] = 0
    c1[1] = 0
    acc1[0] = 0.0
    _ab_path(S1, ev1, c1, acc1, U, D, Ui, Di, nlv, qon_l, qon_s, t, opn, oi, hi, lo, ci, True, slip, taker, nom)
    if c1[0] == 0:
        return
    S2[:] = S
    c2[0] = 0
    c2[1] = 0
    acc2[0] = 0.0
    _ab_path(S2, ev2, c2, acc2, U, D, Ui, Di, nlv, qon_l, qon_s, t, opn, oi, hi, lo, ci, False, slip, taker, nom)
    e1 = acc1[0] + _mtm(S1, cl_p)
    e2 = acc2[0] + _mtm(S2, cl_p)
    if force == 1:
        e2 = e1 + 1.0
    elif force == 2:
        e2 = e1 - 1.0
    if e2 < e1:
        S[:] = S2
        acc[0] += acc2[0]
        for i in range(c2[0]):
            _ev(evm, cm, ev2[i, 0], ev2[i, 1])
    else:
        S[:] = S1
        acc[0] += acc1[0]
        for i in range(c1[0]):
            _ev(evm, cm, ev1[i, 0], ev1[i, 1])


# ───────────────────────────── ядро ─────────────────────────────

@njit(cache=True)
def rr_run(o, h, l, c, pres, tickd, P, cutd, out, hn, hs, dbg):
    """Счёт одной монеты. out — (NR2, сутки отчёта), hn/hs — (3, 2, NBIN); обнуляются снаружи. cutd — сутки отчёта, на конце которых открытый цикл режется (границы половин). dbg[0] — переполнение буфера событий."""
    n = c.shape[0]
    mode = int(P[0])
    kgrid = int(P[1])
    g_grid = P[2]
    g_ab = P[3]
    nlv_ab = int(P[4])
    t_start = int(P[5])
    day0 = int(P[6])
    maker = P[7]
    taker = P[8]
    slip = P[9]
    nom = P[10]
    gmin = P[11]
    force = int(P[12])
    S = np.zeros(NS_)
    S1 = np.zeros(NS_)
    S2 = np.zeros(NS_)
    for k in range(KMAX + 1):
        S[O_LFR + k] = -1.0
        S[O_SFR + k] = -1.0
        S[O_LOP + k] = -1.0
        S[O_SOP + k] = -1.0
    evm = np.zeros((EV_CAP, 2))
    cm = np.zeros(2, np.int64)
    ev1 = np.zeros((EV_CAP, 2))
    c1 = np.zeros(2, np.int64)
    ev2 = np.zeros((EV_CAP, 2))
    c2 = np.zeros(2, np.int64)
    acc = np.zeros(1)
    acc1 = np.zeros(1)
    acc2 = np.zeros(1)
    U = np.zeros(KMAX + 1)
    D = np.zeros(KMAX + 1)
    Ui = np.zeros(KMAX + 1, np.int64)
    Di = np.zeros(KMAX + 1, np.int64)
    phase = 1 if mode == M_AB else 0
    abdir = 0
    have_q = False
    qon_l = False
    qon_s = False
    zq = 0.0
    gq = 0.0
    nq = kgrid
    qclose = 0.0
    p0 = 0.0
    launched = False
    gabs = 0.0
    g_ok = False
    g_t = -1000000000
    lastc = np.nan
    unreal_prev = 0.0
    for t in range(n):
        d = t // 1440
        do = d - day0
        ok = pres[t]
        if ok:
            lastc = c[t]
        if t < t_start or do < 0:
            continue
        hf = 0 if (do >= BURN_ and do < 181) else (1 if (do >= 181 and do < out.shape[1]) else -1)
        tick = tickd[d]
        if ok and tick > 0.0:
            acc[0] = 0.0
            # ── A. исполнение котировок, выставленных на закрытии прошлой минуты
            if have_q and (qon_l or qon_s or S[O_NL] + S[O_NS] > 0.0):
                if phase == 0:
                    _grid_exec(S, evm, cm, acc, U, D, nq, qon_l, qon_s, t, tick, o[t], h[t], l[t], qclose, maker, taker, slip, nom)
                else:
                    for k in range(nq + 1):
                        Ui[k] = _ri(U[k], tick)
                        Di[k] = _ri(D[k], tick)
                    _ab_minute(S, S1, S2, evm, cm, acc, ev1, c1, ev2, c2, acc1, acc2, U, D, Ui, Di, nq, qon_l, qon_s, t, o[t], _ri(o[t], tick), _ri(h[t], tick), _ri(l[t], tick), _ri(c[t], tick), c[t], slip, taker, nom, force)
            # ── B. закрытие минуты: край / смена фазы
            cl = c[t]
            inv = S[O_NL] + S[O_NS] > 0.0
            if phase == 0:
                if have_q and inv and (cl < zq - nq * gq or cl > zq + nq * gq):
                    s = _close_all(S, evm, cm, acc, cl, taker, slip, t)
                    _ev(evm, cm, 0, s)
                    p0 = cl
                    launched = True
                    g_ok = False
                    if mode == M_HY:
                        abdir = 1 if cl > zq + nq * gq else -1
                        phase = 1
                        out[R_G2A, do] += 1.0
            else:
                up_edge = cl > zq + nq * gq and abdir >= 0
                dn_edge = cl < zq - nq * gq and abdir <= 0
                if have_q and inv and (up_edge or dn_edge):
                    s = _close_all(S, evm, cm, acc, cl, taker, slip, t)
                    _ev(evm, cm, 2, s)
                    p0 = cl
                    launched = True
                    g_ok = False
                    if mode == M_HY:
                        phase = 0
                        out[R_A2G, do] += 1.0
                elif mode == M_HY and have_q and (not inv) and ((abdir > 0 and cl < zq) or (abdir < 0 and cl > zq)):
                    p0 = cl
                    launched = True
                    g_ok = False
                    phase = 0
                    out[R_A2G, do] += 1.0
            if not launched:
                p0 = cl
                launched = True
                g_ok = False
            center = p0
            if (not g_ok) or t - g_t >= 60:
                gcur = g_grid if phase == 0 else g_ab
                gabs = max(gcur * center, gmin * tick)
                g_ok = True
                g_t = t
            nlq = kgrid if phase == 0 else nlv_ab
            on = True
            if phase == 0 or abdir <= 0:
                if center - nlq * gabs <= 2.0 * tick:
                    on = False
            for k in range(nlq + 1):
                U[k] = _rt(center + k * gabs, tick)
                D[k] = _rt(center - k * gabs, tick)
            if on:
                out[R_ACT, do] = 1.0
            qon_l = on and (phase == 0 or abdir >= 0)
            qon_s = on and (phase == 0 or abdir <= 0)
            zq = center
            gq = gabs
            nq = nlq
            qclose = cl
            have_q = True
            # учёт минуты
            out[R_PNL, do] += acc[0]
            if phase == 1:
                out[R_MAB, do] += 1.0
            else:
                out[R_MGR, do] += 1.0
            if S[O_NL] + S[O_NS] > 0.0:
                sl = 0.0
                ss = 0.0
                for k in range(1, KMAX + 1):
                    if S[O_LST + k] == 1.0:
                        sl += S[O_LQ + k] * cl
                    if S[O_SST + k] == 1.0:
                        ss += S[O_SQ + k] * cl
                out[R_SGN, do] += sl - ss
                out[R_ABS, do] += sl + ss
            _flush(evm, cm, out, do, hn, hs, hf)
            if cm[1] > 0 or c1[1] > 0 or c2[1] > 0:
                dbg[0] += 1.0
                cm[1] = 0
                c1[1] = 0
                c2[1] = 0
        # ── сутки закрыты: оценка открытого по последнему закрытию; разрез цикла на границе половины
        if t % 1440 == 1439:
            u = 0.0
            if S[O_NL] + S[O_NS] > 0.0 and np.isfinite(lastc):
                u = _mtm(S, lastc)
            out[R_PNL, do] += u - unreal_prev
            unreal_prev = u
            for i in range(cutd.shape[0]):
                if cutd[i] == do and S[O_CYA] == 1.0:
                    un = u - _fees_open(S)
                    _cycle(out, do, S[O_CYP] + un, True)
                    S[O_CYP] = -un
    return out


def run_rr(S: dict, tick: np.ndarray, P: np.ndarray, cutd=(L.BURN - 1, 180, L.NOUT - 1)):
    """Обёртка: → (out (NR2, сутки), hn, hs, dbg)."""
    n = len(S["c"])
    out = np.zeros((NR2, n // 1440 - int(P[6])))
    hn = np.zeros((3, 2, NBIN))
    hs = np.zeros((3, 2, NBIN))
    dbg = np.zeros(1)
    rr_run(S["o"], S["h"], S["l"], S["c"], S["pres"], tick, P, np.asarray(cutd, np.int64), out, hn, hs, dbg)
    return out, hn, hs, dbg


# ───────────────────────────── варианты §8 ─────────────────────────────

def variants() -> list[dict]:
    """Закрытый список §8: C0 (= ES(10), контроль) + 8 вариантов."""
    v = [dict(name="C0", fam="C0", P=dict(mode=M_GRID, kgrid=10))]
    for k in (2, 3, 5):
        v.append(dict(name="ES%d" % k, fam="ES", P=dict(mode=M_GRID, kgrid=k)))
    for g in (0.005, 0.01, 0.02):
        v.append(dict(name="AB%g" % (g * 100), fam="AB", P=dict(mode=M_AB, g_ab=g)))
    for k in (3, 5):
        v.append(dict(name="HY%d" % k, fam="HY", P=dict(mode=M_HY, kgrid=k, g_grid=0.005, g_ab=0.005)))
    return v


def variant_names(with_c0: bool = True) -> list[str]:
    return [v["name"] for v in variants() if with_c0 or v["name"] != "C0"]


# ───────────────────────────── гистограммы убытка на стоп ─────────────────────────────

def hist_stats(hn: np.ndarray, hs: np.ndarray, frac: float = 0.01) -> dict:
    """По гистограмме одного вида и половины: число, среднее, и среднее худшего `frac` по значению (по бинам; частичный бин — по его среднему)."""
    n = float(hn.sum())
    if n <= 0:
        return dict(n=0, mean=float("nan"), worst=float("nan"), p01=float("nan"))
    need = frac * n
    acc = 0.0
    tot = 0.0
    p01 = float("nan")
    for b in range(len(hn)):
        if hn[b] <= 0:
            continue
        take = min(hn[b], need - acc)
        tot += hs[b] / hn[b] * take
        if math.isnan(p01) and acc + hn[b] >= need:
            p01 = hs[b] / hn[b]
        acc += take
        if acc >= need - 1e-9:
            break
    return dict(n=int(n), mean=float(hs.sum() / n), worst=float(tot / need), p01=float(p01))
