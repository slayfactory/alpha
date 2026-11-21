"""Тесты §13.71 (TK-671), семейство TK-636 (`tk671_coin`): ядра окон корреляций / соседей / событий = оригиналу `tk636_feat` (`wstat`, `corr_features`, `Events`), turn24h / возраст / месяц, кэш и состав меток кластеров,
сборка `compute` = `row_values` построчно (все 28 столбцов точно), причинность (мусор в доходностях ≥ t, событиях ≥ t и в данных после дня обновления кластеров не меняет значения), охрана суток ≥ 14.11. Синтетика, без srv.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_coin.py`."""
import datetime
import os
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk604_stats as st  # noqa: E402
import tk636_feat as f6  # noqa: E402
import tk671_coin as fam  # noqa: E402

N = 23040                                                        # 16 суток: 14 до 01.07 (T0 wa — 17.06) + 2 суток сигналов
T0_MS = {w.name: w.t0_ms for w in st.TRADE_WINDOWS}
COINS = ["A1USDT", "A2USDT", "A3USDT", "A4USDT", "B1USDT", "B2USDT", "B3USDT", "BTCUSDT", "C1USDT", "C2USDT", "ETHUSDT", "NOCACHEUSDT"]   # sorted, как вселенная
GRP = {"A": ["A1USDT", "A2USDT", "A3USDT", "A4USDT"], "B": ["B1USDT", "B2USDT", "B3USDT"]}


def same(a, b):
    return (a != a and b != b) or a == b


def eqv(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


def write_b42(path, o, c, qv):
    n = len(o)
    h = np.maximum(o, c) + 3
    l = np.maximum(np.minimum(o, c) - 3, 1)
    with open(path, "wb") as f:
        f.write(b"B42\x01" + n.to_bytes(4, "little"))
        f.write(np.stack([o, h, l, c, qv]).astype("<i8").tobytes())


def make_world(tmp, seed=1):
    rng = np.random.default_rng(seed)
    cache = os.path.join(tmp, "wa", "cache")
    os.makedirs(cache)
    btc = rng.normal(0, 0.0008, N)
    fac = {k: rng.normal(0, 0.0012, N) for k in GRP}
    qvs = {}
    for sy in COINS:
        if sy == "NOCACHEUSDT":
            continue
        r = rng.normal(0, 0.0010, N)
        if sy == "BTCUSDT":
            r = btc
        elif sy == "ETHUSDT":
            r = 0.9 * btc + rng.normal(0, 0.0004, N)
        else:
            r = r + 0.7 * btc
            for k, mem in GRP.items():
                if sy in mem:
                    r = r + 1.2 * fac[k]
        c = np.maximum(np.rint(1_000_000 * np.exp(np.cumsum(r))), 1).astype(np.int64)
        o = np.concatenate([[c[0]], c[:-1]])
        z = rng.random(N) < 0.02
        z[5000:5200] = sy == "C2USDT"
        o = np.where(z, 0, o).astype(np.int64)
        qvs[sy] = rng.integers(1, 10 ** 7, N).astype(np.int64)
        write_b42(os.path.join(cache, sy + ".b42"), o, c, qvs[sy])
    ev = os.path.join(tmp, "ev")
    os.makedirs(ev)
    syms = rng.choice(COINS + ["OUTSIDERUSDT"], 400)
    pd.DataFrame(dict(side=rng.choice(["long", "short"], 400), sym=syms, cross_ms=np.where(rng.random(400) < 0.05, np.nan, rng.integers(20160 * 60000, N * 60000, 400).astype(float)))).to_csv(os.path.join(ev, "wa.csv"), index=False)
    ct = os.path.join(tmp, "contracts.csv")
    pd.DataFrame(dict(symbol=[s for s in COINS if s != "C2USDT"], tradfi=[int(s.startswith("B")) for s in COINS if s != "C2USDT"],
                      onboard_date=["2026-06-20" if s == "A1USDT" else ("" if s == "A2USDT" else "2025-03-%02d" % (1 + i)) for i, s in enumerate(s for s in COINS if s != "C2USDT")])).to_csv(ct, index=False)
    return dict(tmp=tmp, cache=cache, ev=ev, contracts=ct, qv=qvs)


def make_rows(rng, n=150, side="long"):
    pool = COINS + ["ZZZUSDT"]
    sym = rng.choice(pool, n)
    sig = rng.integers(20200 * 60_000, (N - 3) * 60_000, n)
    df = pd.DataFrame(dict(row_id=np.arange(500, 500 + n, dtype=np.int64)[::-1], sym=sym, sig_ms=sig.astype(np.int64), natr_e8=rng.integers(0, 3_000_000, n).astype(np.int64)))
    df["win"], df["side"] = "wa", side
    df["abs_ms"] = T0_MS["wa"] + df["sig_ms"]
    return df


def run_compute(w, rows, side="long", procs=1, **kw):
    return fam.compute(rows, "wa", side, procs=procs, cache_dir=w["cache"], cache_root=w["tmp"], ev_dir=w["ev"], contracts_path=w["contracts"], coins=COINS, **kw)


# ---- ядра = оригиналу -----------------------------------------------------------------------------------------------------------------------

def test_wstat_kernel_equals_original():
    rng = np.random.default_rng(2)
    bx, by, dv = np.empty(2016), np.empty(2016), np.empty(2016)
    n = 3000
    for trial in range(40):
        x, y = rng.normal(0, 1, n), rng.normal(0, 1, n)
        y = 0.5 * x + y
        x[rng.integers(0, n, 300)] = np.nan
        y[rng.integers(0, n, 300)] = np.nan
        if trial == 0:
            x[:] = 0.0                                                 # нулевая дисперсия
        last = int(rng.integers(0, n))
        win, mn = [(15, 10), (60, 40), (48, 30), (288, 100), (2016, 700)][trial % 5]
        a = max(last - win + 1, 0)
        ref = f6.wstat(x, y, last, win, mn)
        got = fam._wstat(x, y, a, last, mn, bx, by, dv)
        assert same(got[0], ref[0]) and same(got[1], ref[1]), (trial, got, ref)


def test_corr_group_equals_original():
    rng = np.random.default_rng(3)
    nc, n1 = 9, 6000
    R1 = rng.normal(0, 0.001, (nc, n1))
    R1[2:] += 0.5 * R1[0]
    R1[rng.random(R1.shape) < 0.03] = np.nan
    R5 = rng.normal(0, 0.003, (nc, n1 // 5))
    R5[2:] += 0.5 * R5[0]
    R5[rng.random(R5.shape) < 0.03] = np.nan
    R5[4, 200:260] = np.nan
    t = np.concatenate([[0, 1, 7, 14, 15, 16, 61, 301, 1500, 5000, n1 - 1, n1], rng.integers(0, n1 + 1, 60)]).astype(np.int64)
    for ci, bi, ei, mates, name in ((3, 0, 1, [2, 4, 5, 6], "XUSDT"), (3, 0, 1, [2], "XUSDT"), (3, 0, 1, [], "XUSDT"), (0, 0, 1, [2, 3], "BTCUSDT"), (1, 0, 1, [2, 3, 4], "ETHUSDT"), (5, -1, -1, [6, 7], "YUSDT")):
        out = np.empty((len(t), 4 * fam.NW))
        m = np.array(mates, dtype=np.int64)
        fam._coup_group(R1, R5, ci, bi, ei, name == "BTCUSDT", name == "ETHUSDT", m, t, fam.W_N, fam.W_MIN, fam.W_R1, out)
        for i in range(len(t)):
            ref = f6.corr_features(R1, R5, ci, bi if bi >= 0 else None, ei if ei >= 0 else None, m, int(t[i]), name)
            for q, w in enumerate(f6.WIN_ORDER):
                for k, comp in enumerate(f6.COMP):
                    assert same(out[i, 4 * q + k], ref["%s_%s" % (comp, w)]), (ci, mates, int(t[i]), comp, w, out[i, 4 * q + k], ref["%s_%s" % (comp, w)])
        assert np.isfinite(out).any()


def test_peer_kernels_equal_original():
    rng = np.random.default_rng(4)
    coins = ["C%02d" % i for i in range(20)]
    syms = rng.choice(coins + ["OUT1", "OUT2"], 500)
    cross = rng.integers(0, 10_000_000, 500).astype(np.int64)
    ref = f6.Events(syms, cross)
    ei = fam.EventIndex(coins, syms, cross)
    t_ms = np.concatenate([[0, 1, 600_000, 3_600_000, 10_000_000, 10_000_001], rng.integers(0, 10_100_000, 80)]).astype(np.int64)
    mates = np.array([1, 4, 5, 9, 17], dtype=np.int64)
    out = np.empty((len(t_ms), 3))
    fam._peer_group(mates, t_ms, ei.off, ei.ev_t, fam.PEER_MIN, out)
    own_names = ["C03", "C07", "OUT1", "NOPE"] * 25
    own = ei.own_id(own_names[:len(t_ms)])
    pall = np.empty(len(t_ms))
    fam._peerall(t_ms, own, ei.all_t, ei.all_id, ei.n_ids, f6.PEERALL_W, pall)
    for i in range(len(t_ms)):
        for k, (name, w) in enumerate(f6.PEER_W):
            assert out[i, k] == ref.count([coins[m] for m in mates], int(t_ms[i]), w), (i, name)
        assert pall[i] == ref.count_all(own_names[i], int(t_ms[i]), f6.PEERALL_W), i
    assert out.max() > 0 and pall.max() > 1 and (own[:4] >= -1).all() and own[3] == -1


# ---- простые столбцы ------------------------------------------------------------------------------------------------------------------------

def test_turn24h_rows():
    rng = np.random.default_rng(5)
    qv = rng.integers(1, 10 ** 9, 5000).astype(np.int64)
    t = np.array([0, 1439, 1440, 1441, 3000, 4999, 5000, 5001, 7000, 9999], dtype=np.int64)
    got = fam.turn24h_rows(qv, t)
    for i, ti in enumerate(t):
        ref = float(qv[ti - 1440:ti].sum()) / 1e5 if ti >= 1440 else float("nan")
        assert same(got[i], ref), (ti, got[i], ref)


def test_contract_and_month_columns():
    abs_ms = np.array([T0_MS["wa"], 1_782_864_000_000 + 5, 1_782_863_999_999, 1_798_761_600_000, 1_767_225_599_999], dtype=np.int64)       # 2026 / 2027-01-01 / 2025-12-31
    syms = np.array(["A1USDT", "A2USDT", "B1USDT", "C2USDT", "A1USDT"])
    contracts = {"A1USDT": dict(tradfi="0", onboard_date="2026-06-20"), "A2USDT": dict(tradfi="1", onboard_date=""), "B1USDT": dict(tradfi="0", onboard_date="2025-03-01")}
    cls, age = fam.contract_columns(syms, abs_ms, contracts)
    for i in range(len(syms)):
        ct = contracts.get(syms[i])
        assert same(cls[i], float(ct["tradfi"]) if ct else float("nan"))
        assert same(age[i], f6.age_days(int(abs_ms[i]), ct["onboard_date"] if ct else ""))
    assert age[0] == 0.0                                                                     # до листинга — 0
    mo = fam.month_of_vec(np.concatenate([abs_ms, [1_782_864_000_000 + 40 * 86_400_000]]))
    assert mo.tolist() == [f6.month_of(int(x)) for x in np.concatenate([abs_ms, [1_782_864_000_000 + 40 * 86_400_000]])]


# ---- метки кластеров -------------------------------------------------------------------------------------------------------------------------

def test_month_labels_equal_original_and_cache():
    with tempfile.TemporaryDirectory() as tmp:
        w = make_world(tmp)
        wobj = f6.Win(os.path.join(tmp, "wa"), COINS)
        ref = f6.month_clusters(wobj, datetime.date(2026, 6, 17), 7)
        lab = fam.month_labels([7, 7, 3], COINS, cache_root=tmp, labels_dir=os.path.join(tmp, "lab"))
        assert list(lab) == [7] and np.array_equal(lab[7], ref)
        grp = {k: {int(lab[7][COINS.index(s)]) for s in mem} for k, mem in GRP.items()}
        assert all(len(v) == 1 and next(iter(v)) >= 0 for v in grp.values()) and grp["A"] != grp["B"]       # посаженные группы найдены, раздельно
        assert lab[7][COINS.index("BTCUSDT")] == -1 and lab[7][COINS.index("ETHUSDT")] == -1 and lab[7][COINS.index("C1USDT")] == -1
        assert os.path.exists(os.path.join(tmp, "lab", "clusters-07.npz"))
        for f in os.listdir(os.path.join(tmp, "wa", "cache")):                                         # из кэша меток кэши минуток не читаются
            os.remove(os.path.join(tmp, "wa", "cache", f))
        lab2 = fam.month_labels([7], COINS, cache_root=tmp, labels_dir=os.path.join(tmp, "lab"))
        assert np.array_equal(lab2[7], ref)
        with np.load(os.path.join(tmp, "lab", "clusters-07.npz")) as z:
            old_lab = z["lab"]
        np.savez(os.path.join(tmp, "lab", "clusters-07.npz"), lab=old_lab, coins=np.array(COINS[::-1]))      # другой список монет — кэш не принимается
        try:
            fam.month_labels([7], COINS, cache_root=tmp, labels_dir=os.path.join(tmp, "lab"))
            assert False
        except (RuntimeError, FileNotFoundError):
            pass


# ---- сборка -----------------------------------------------------------------------------------------------------------------------------------

def reference(w, rows, side):
    wobj = f6.Win(os.path.join(w["tmp"], "wa"), COINS)
    lab = {7: f6.month_clusters(wobj, datetime.date(2026, 6, 17), 7)}
    d = pd.read_csv(os.path.join(w["ev"], "wa.csv"), usecols=["side", "sym", "cross_ms"])
    d = d[(d["side"] == side) & d["cross_ms"].notna()]
    ev = f6.events_of(d[["sym", "cross_ms"]])
    ct = f6.read_contracts(w["contracts"])
    out = []
    for x in rows.itertuples():
        out.append(f6.row_values(wobj, lab, ev, x.sym, int(x.sig_ms), int(x.abs_ms), ct.get(x.sym)))
    return pd.DataFrame(out, index=rows["row_id"].to_numpy())


def test_compute_equals_row_values():
    rng = np.random.default_rng(6)
    for side in ("long", "short"):
        with tempfile.TemporaryDirectory() as tmp:
            w = make_world(tmp, seed=7 if side == "long" else 8)
            rows = make_rows(rng, side=side)
            info = {}
            got = run_compute(w, rows, side, info=info)
            ref = reference(w, rows, side)
            cols = [c for c in fam.COLS if c not in ("turn24h", "natr_bp")]
            assert list(got.columns) == list(f6.OUT_COLS) and got.index.name == "row_id" and (got.dtypes == np.float64).all()
            assert got.index.tolist() == rows["row_id"].tolist()                                       # порядок и состав строк — как на входе
            assert np.array_equal(got[cols].to_numpy(), ref[cols].to_numpy(), equal_nan=True)
            nn = got.notna()
            assert nn["cb_24h"].mean() > 0.6 and nn["cc_4h"].sum() > 10 and nn["peer15"].sum() > 5 and nn["clu_id"].sum() > 10 and (got["peerall15"] >= 0).all() and nn["cb_7d"].sum() > 20
            assert np.array_equal(got["natr_bp"].to_numpy(), rows["natr_e8"].to_numpy() / 1e4)
            miss = rows.loc[rows.sym.isin(["NOCACHEUSDT", "ZZZUSDT"]), "row_id"]
            assert got.loc[miss, [c for c in cols if c != "peerall15" and c not in ("cls", "age_d")]].isna().all().all() and got.loc[miss, "peerall15"].notna().all()
            assert set(info["missing_cache_coins"]) == {"NOCACHEUSDT", "ZZZUSDT"} and info["label_months"] == [7]
            # turn24h — по минуткам монеты
            for x in rows.itertuples():
                if x.sym in w["qv"]:
                    t = x.sig_ms // 60000
                    assert same(got.loc[x.row_id, "turn24h"], float(w["qv"][x.sym][t - 1440:t].sum()) / 1e5)
                else:
                    assert np.isnan(got.loc[x.row_id, "turn24h"])


def test_compute_procs_equal_serial():
    rng = np.random.default_rng(9)
    with tempfile.TemporaryDirectory() as tmp:
        w = make_world(tmp, seed=10)
        rows = make_rows(rng, n=120)
        a = run_compute(w, rows, procs=1)
        b = run_compute(w, rows, procs=3)
        assert np.array_equal(a.to_numpy(), b.to_numpy(), equal_nan=True)


def test_compute_causal_garbage():
    """Мусор в доходностях монеты, BTC / ETH и соседей с минуты t, в событиях ≥ t и в обороте ≥ t не меняет признаки строки; мусор в данных после дня обновления кластеров не меняет метки."""
    rng = np.random.default_rng(11)
    with tempfile.TemporaryDirectory() as tmp:
        w = make_world(tmp)
        rows = make_rows(rng, n=24)
        coins = COINS
        world, turn = fam.load_world(w["cache"], coins, procs=1, rows_by_coin={s: np.flatnonzero(rows.sym.to_numpy() == s) for s in np.unique(rows.sym)}, turn_t=rows.sig_ms.to_numpy() // 60000)
        lab = fam.month_labels([7], coins, cache_root=tmp)
        d = pd.read_csv(os.path.join(w["ev"], "wa.csv"))
        d = d[(d["side"] == "long") & d["cross_ms"].notna()]
        evs, evc = d["sym"].to_numpy(dtype=object), d["cross_ms"].to_numpy(dtype=float).astype(np.int64)
        base = fam.compute(rows, "wa", "long", procs=1, world=world, turn=turn, labels=lab, events=(evs, evc), contracts_path=w["contracts"], coins=coins, cache_dir=w["cache"])
        cols = [c for c in fam.COLS if c not in ("turn24h", "natr_bp")]
        n_cmp = 0
        for x in rows.itertuples():
            t, ts = x.sig_ms // 60000, x.sig_ms
            R1, R5 = world.R1.copy(), world.R5.copy()
            R1[:, t:] = rng.normal(0, 0.05, R1[:, t:].shape)
            R5[:, t // 5:] = rng.normal(0, 0.05, R5[:, t // 5:].shape)
            late = evc >= ts
            c2 = evc.copy()
            c2[late] = ts + rng.integers(0, 6 * 60 * 60_000, int(late.sum()))
            ev2 = (np.concatenate([evs, rng.choice(COINS, 20)]), np.concatenate([c2, ts + rng.integers(0, 3_600_000, 20)]))
            w2 = fam.World(coins, R1, R5, world.have)
            one = rows[rows.row_id == x.row_id]
            got = fam.compute(one, "wa", "long", procs=1, world=w2, turn=None, labels=lab, events=ev2, contracts_path=w["contracts"], coins=coins, cache_dir=w["cache"])
            for c in cols:
                assert same(got.loc[x.row_id, c], base.loc[x.row_id, c]), (x.row_id, c, got.loc[x.row_id, c], base.loc[x.row_id, c])
                n_cmp += 1
            qv = w["qv"].get(x.sym)
            if qv is not None:
                g = qv.copy()
                g[t:] = rng.integers(1, 10 ** 9, len(g) - t)
                assert same(fam.turn24h_rows(g, np.array([t]))[0], base.loc[x.row_id, "turn24h"])
        assert n_cmp > 500
        # кластеры: мусор после дня обновления (блоки ≥ b)
        wobj = f6.Win(os.path.join(tmp, "wa"), COINS)
        a_, b_ = f6.refresh_blocks(datetime.date(2026, 6, 17), datetime.date(2026, 7, 1))
        junk = wobj.R5.copy()
        junk[:, b_:] = rng.normal(0, 0.01, junk[:, b_:].shape)
        assert np.array_equal(f6.cluster_labels(junk, wobj.ix["BTCUSDT"], wobj.ix["ETHUSDT"], a_, b_), lab[7])


def test_guards_and_empty():
    rng = np.random.default_rng(12)
    with tempfile.TemporaryDirectory() as tmp:
        w = make_world(tmp)
        rows = make_rows(rng, n=8)
        for bad in (rows.assign(abs_ms=st.LAST_MS), rows.assign(side="short"), rows.assign(win="wb"), pd.concat([rows, rows.iloc[:1]])):
            try:
                run_compute(w, bad)
                assert False
            except (st.GuardError, ValueError):
                pass
        try:
            run_compute(w, rows, side="up")
            assert False
        except ValueError:
            pass
        got = run_compute(w, rows.iloc[:0])
        assert len(got) == 0 and list(got.columns) == list(f6.OUT_COLS)


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print("ok   ", name)
        except Exception:  # noqa: BLE001
            fails += 1
            print("FAIL ", name)
            traceback.print_exc()
    print("%d из %d зелёных" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
