"""Тест шага 3 TK-727 на синтетике: сквозной проход `analyze` и правило решения `decide`."""
import json
import os
import sqlite3
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk727_lib as L  # noqa: E402
import tk727_s3_an as A  # noqa: E402


def make_coin_files(out, noise, n_coins=10, seed=5):
    """σ(монета, сутки, час) → RR = σ·e^ε; оценщик i = σ·e^{η_i}, sd(η_i) = noise[i]; все входы HAR зашумлены умеренно."""
    rng = np.random.default_rng(seed)
    os.makedirs(os.path.join(out, "s3", "coin"), exist_ok=True)
    nd = 328
    hours = (np.sin(np.arange(24) / 24 * 2 * np.pi) * 0.3)
    day_eff = np.cumsum(rng.normal(0, 0.05, nd))
    day_eff -= day_eff.mean()
    for si in range(n_coins):
        a = rng.normal(-5.5, 0.4)
        rows = []
        for d in range(nd):
            mins = np.sort(rng.choice(1440, 20, replace=False))
            rows.append((d, mins, 0))
            if 181 <= d < 317:
                rows.append((d, np.sort(rng.choice(1440, 5, replace=False)), 1))
        D = {k: [] for k in ("m", "day", "hour", "set", "sym", "natr5", "natr15", "natr30", "natr60", "rv5", "rv15", "rv30", "rv60", "ewtr", "gk60", "bv60", "rvm60", "rvm1440", "rvm10080",
                             "natr30_prev", "tickf", "rr60", "rr20", "turn24")}
        for d, mins, tag in rows:
            h = mins // 60
            sig = np.exp(a + day_eff[d] + hours[h])
            n = len(mins)
            D["m"].append(((d + L.DAY0) * 1440 + mins).astype(np.int32))
            D["day"].append(np.full(n, d, np.int16))
            D["hour"].append(h.astype(np.int8))
            D["set"].append(np.full(n, tag, np.int8))
            D["sym"].append(np.full(n, si, np.int16))
            for k in ("natr5", "natr15", "natr30", "natr60", "rv5", "rv15", "rv30", "rv60", "ewtr", "gk60", "bv60"):
                D[k].append(sig * np.exp(rng.normal(0, noise[k], n)))
            D["rvm60"].append(sig * np.exp(rng.normal(0, 0.6, n)))
            D["rvm1440"].append(np.exp(a + day_eff[d]) * np.exp(rng.normal(0, 0.1, n)))
            D["rvm10080"].append(np.exp(a) * np.exp(rng.normal(0, 0.05, n)))
            D["natr30_prev"].append(sig * np.exp(rng.normal(0, 0.6, n)))
            D["tickf"].append(np.full(n, 1e-6))
            D["rr60"].append(sig * np.exp(rng.normal(0, 0.5, n)) * 2.0)
            D["rr20"].append(sig * np.exp(rng.normal(0, 0.5, n)) * 1.2)
            D["turn24"].append(np.exp(rng.normal(20 + si * 0.1, 0.5, n)))
        np.savez_compressed(os.path.join(out, "s3", "coin", "C%02d.npz" % si), **{k: np.concatenate(v) for k, v in D.items()})


def test_pipeline_finds_new_estimator_and_rejects_noise():
    L.NBOOT = 400
    noise = {k: 0.7 for k in ("natr5", "natr15", "natr30", "natr60", "rv5", "rv15", "rv30", "rv60", "ewtr", "gk60", "bv60")}
    noise["gk60"] = 0.05  # заведомо лучший новый оценщик
    with tempfile.TemporaryDirectory() as out:
        make_coin_files(out, noise)
        res = A.analyze(out, "тест")
        d = res["H60"]["decision"]
        assert d["winner"] == 10, d
        assert "новый оценщик № 10" in d["outcome"]
        db = sqlite3.connect(os.path.join(out, "tk727.sqlite"))
        n = db.execute("select count(*) from s3_diff where h=60 and set_='b' and loss='qlike' and per='FJ'").fetchone()[0]
        assert n == 11 * 2  # 11 кандидатов × два блока
        # масштаб сделан: средняя потеря NATR30 конечна
        m = db.execute("select mean from s3_loss where h=60 and loss='qlike' and per='SEL' and set_='b' and est='natr30'").fetchone()[0]
        assert np.isfinite(m) and m > 0
        # счётчики есть
        assert db.execute("select count(*) from s3_counts").fetchone()[0] > 0
        db.close()


def test_pipeline_no_winner_when_all_equal():
    L.NBOOT = 400
    noise = {k: 0.7 for k in ("natr5", "natr15", "natr30", "natr60", "rv5", "rv15", "rv30", "rv60", "ewtr", "gk60", "bv60")}
    with tempfile.TemporaryDirectory() as out:
        make_coin_files(out, noise, seed=9)
        res = A.analyze(out, "тест")
        d = res["H60"]["decision"]
        assert d["winner"] != 10
        assert "10" not in "".join(str(x) for x in d["passers"]) or d["winner"] is None or d["winner"] != 10


def test_decide_rules():
    def ent(rel, p=True, p21=True):
        return dict(rel=rel, pass_b=p, pass_b21=p21, L=1.0)

    def build(winners, rel5=None):
        res = {h: {"best18": (3, 1.0), "rel5": {}} for h in ("FJ", "SEL")}
        for h in res:
            for c in range(1, 13):
                if c == 3:
                    continue
                res[h][c] = ent(0.0, False, False)
                res[h]["rel5"][c] = ent(0.0, False, False)
            for c, r in winners.items():
                res[h][c] = ent(r)
            for c in (9, 10, 11, 12):
                res[h]["rel5"][c] = ent((rel5 or {}).get(c, 0.0), (rel5 or {}).get(c, 0.0) > 0)
        return res

    assert A.decide(build({}))["outcome"].startswith("NATR30 остаётся")
    assert A.decide(build({6: 0.03}))["winner"] == 6
    d = A.decide(build({12: 0.08}, {12: 0.06}))
    assert d["winner"] == 12 and "новый" in d["outcome"]
    # новый лучше NATR30, но не на 5 % лучше лучшего из 1–8 → не берётся
    d = A.decide(build({12: 0.08}, {12: 0.02}))
    assert d["winner"] is None and "NATR30 остаётся" in d["outcome"]
    # победитель старый и новый слабее 5 % → победитель старый
    d = A.decide(build({6: 0.03, 12: 0.04}, {12: 0.01}))
    assert d["winner"] == 6
    # разный знак границы при блоках → «не ясно»
    res = build({6: 0.03})
    res["FJ"][6]["pass_b21"] = False
    assert "не ясно" in A.decide(res)["outcome"]


def test_scale_factors_use_only_past():
    nd = L.NDAYS - L.DAY0
    day = np.repeat(np.arange(nd), 2)
    q = np.where(day < 10, 2.0, 100.0)
    k = A.scale_factors(day, q, np.ones(len(q), bool))
    assert np.isnan(k[0]) and np.isnan(k[1])
    assert abs(k[5] - 2.0) < 1e-12
    assert abs(k[11] - 2.0) < 1e-12  # сутки 11 видит сутки ≤ 9
    assert k[12] > 2.0  # сутки 12 видит сутки ≤ 10 (там уже 100)
