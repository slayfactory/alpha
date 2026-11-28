#!/usr/bin/env python3
"""TK-735, §13.79.2 «Сверка приближения Φ движком» (пропущенный шаг; проверяющий, FIX п. 2): одно условие φ1 «RV60 ≥ θ» на списке клетки якоря s4 (7180023) в окне wc.

Движок (один прогон, две клетки): kA — якорь как есть (повторяет сохранённый список окна wc → воспроизводимость); kB — якорь + `nf=8 natr_min=θ%` (RV60 на закрытой минуте t−1, проценты цены;
якорь своего порога не имеет: `nf` и `natr_min` не заданы → умолчание 0, поэтому «заменяет» = «добавляет к пустому»). Список: сделки якоря окна wc (зачётные сутки 03.10–13.11) из базы SEL с φ1 ≥ θ.
Мера: число сделок и Σ r на одних и тех же сутках; расхождение > 10 % сделок → «приближение грубое». θ = медиана φ1 по сделкам базы SEL (p50, как первое условие L40).
Запуск — юнитом на srv: `python tk735_phi_check.py` (≈ 1 мин, один движок)."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk735_lib as T  # noqa: E402

ENGINE = "/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45"
ENGINE_MD5 = "06367f45fad0d7c5c1031d82dfa03615"
WC = "/data/p42-tk621/wc"
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"
STORED = "/data/p42-tk726/trades/tk726-sel-A/wc/%d-trades.csv" % T.ANCHOR
T0_ISO = "2026-09-01"
T0_MS = 1788220800000
CREDIT = (T.day_of("2026-10-03"), T.day_of("2026-11-13"))            # зачётные сутки wc (от 01.01.2026), включительно
LINE = ("lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05 t24=12 bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20")
OUT = os.path.join(T.ROOT, "phi-check")


def credited(a: dict) -> dict:
    m = (a["eday"] >= CREDIT[0]) & (a["eday"] <= CREDIT[1])
    return {k: v[m] for k, v in a.items()}


def main():
    if hashlib.md5(open(ENGINE, "rb").read()).hexdigest() != ENGINE_MD5:
        sys.exit("движок: md5 ≠ %s" % ENGINE_MD5)
    b = np.load(os.path.join(T.ROOT, "res", "base-SEL.npz"), allow_pickle=False)
    V = b["X"][:, T.PHI.index("V")]
    theta_bp = float(np.nanmedian(V))
    theta_pct = round(theta_bp / 100.0, 4)                                  # RV60 в bp цены → проценты цены (порог движка)
    os.makedirs(OUT, exist_ok=True)
    work = os.path.join(OUT, "work")
    os.makedirs(work, exist_ok=True)
    vf = os.path.join(OUT, "variants.txt")
    open(vf, "w", encoding="utf-8", newline="\n").write("kA %s\nkB %s nf=8 natr_min=%s\n" % (LINE, LINE, theta_pct))
    syms = open(C539, encoding="utf-8").read().strip().replace(",", " ").split()
    cmd = [ENGINE, "breakout", "tick", "--cache", WC + "/cache", "--trades", WC + "/aggtrades", "--variants", vf, "--symbols", ",".join(syms), "--out", work, "--threads", "6", "--cap-gb", "12",
           "--tick-store", "/data/p42-tk599/tick-store", "--tick-store-cap-gb", "3", "--tick-store-min-free-gb", "5"]
    env = dict(os.environ, P42_T0=T0_ISO, P42_SIDES="short")
    with open(os.path.join(OUT, "engine.log"), "w", encoding="utf-8") as lf:
        subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, check=True)
    eng = {}
    for k in ("kA", "kB"):
        p = glob.glob(os.path.join(work, k, "*-trades.csv"))
        assert len(p) == 1, (k, p)
        eng[k] = credited(T.read_trades_file(p[0], T0_MS))
    stored = credited(T.read_trades_file(STORED, T0_MS))
    key = lambda a: sorted(zip(a["sym"].tolist(), a["sig_ms"].tolist()))                     # noqa: E731
    reg_lo = T.REG["SEL"][0]
    sel = (b["in_anchor"].astype(bool)) & (b["day"] + reg_lo >= CREDIT[0]) & (b["day"] + reg_lo <= CREDIT[1])
    lst = sel & (V >= theta_bp)
    rep = dict(theta_bp=theta_bp, theta_pct=theta_pct, credit_days=list(CREDIT),
               reproduce=dict(engine_kA_n=int(len(eng["kA"]["sym"])), stored_n=int(len(stored["sym"])), engine_kA_sum=float(eng["kA"]["pnl"].sum()), stored_sum=float(stored["pnl"].sum()), same_trades=key(eng["kA"]) == key(stored)),
               list_anchor=dict(n=int(sel.sum()), sum=float(b["r"][sel].sum())),
               list_phi1=dict(n=int(lst.sum()), sum=float(b["r"][lst].sum())),
               engine_phi1=dict(n=int(len(eng["kB"]["sym"])), sum=float(eng["kB"]["pnl"].sum())))
    ks = set(zip(b["sym"][lst].tolist(), b["sig_ms"][lst].tolist()))
    ke = set(key(eng["kB"]))
    rep["common"] = len(ks & ke)
    rep["only_list"] = sorted(ks - ke)[:20]
    rep["only_engine"] = sorted(ke - ks)[:20]
    n_l, n_e = rep["list_phi1"]["n"], rep["engine_phi1"]["n"]
    rep["n_diff_pct"] = float(abs(n_l - n_e) / max(1, n_e) * 100.0)
    rep["rough"] = bool(rep["n_diff_pct"] > 10.0)
    json.dump(rep, open(os.path.join(T.ROOT, "res", "phi_check.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
