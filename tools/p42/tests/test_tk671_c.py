"""Тесты §13.71 (TK-671), семейства C: `tk671_ticks` (склад тиков), `tk671_hist` (история пробоев), `tk671_bk` (пробиваемость). Синтетика во временных каталогах, данных srv нет.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_c.py` (нужны numpy, pandas, pyarrow, numba — на srv `/data/tk592/venv/bin/python`) — выполняет все test_* и печатает итог."""
import datetime as dt
import json
import os
import struct
import sys
import tempfile
import traceback
import zipfile

os.environ["SIGFEAT_BASE_DATE"] = "2026-09-01"
os.environ["SIGFEAT_DAYS"] = "80"
import numpy as np
import pandas as pd
import pyarrow as pa

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import sigfeat_lib as L  # noqa: E402
import tk604_feat as tf  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_feat as f5  # noqa: E402
import tk671_bk as bk  # noqa: E402
import tk671_hist as hi  # noqa: E402
import tk671_ticks as tt  # noqa: E402

DAY = 86_400_000
MIN = 60_000


def zig(v):
    return (v << 1) ^ (v >> 63)


def varint(v):
    out = bytearray()
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def encode_day(ticks, g=1):
    """ticks = [(px, off_ms, bm)] по off не убывает → нагрузка блока A (как `store::encode`)."""
    first = ticks[0][0]
    b = bytearray(struct.pack("<qQ", first, g))
    ppx, poff = first, 0
    for px, off, bm in ticks:
        b += varint(zig((px - ppx) // g)) + varint(((off - poff) << 1) | bm)
        ppx, poff = px, off
    return bytes(b)


def write_day(root, store, sym, date, ticks, g=1, stale=False, flags=0):
    """zip aggTrades (price, transact_time) + файл склада v2 с заголовком под этот zip."""
    d0 = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc)
    day_ms = int(d0.timestamp() * 1000)
    os.makedirs(f"{root}/{sym}", exist_ok=True)
    zp = f"{root}/{sym}/{sym}-aggTrades-{date}.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr(f"{sym}-aggTrades-{date}.csv", "price,transact_time\n" + "".join("%s,%d\n" % (repr(px / 1e8), day_ms + off) for px, off, _ in ticks))
    zs = os.stat(zp)
    raw = encode_day(ticks, g)
    comp = pa.compress(raw, codec="zstd", asbytes=True)
    head = b"TK42" + struct.pack("<IQqII", 2, zs.st_size + (1 if stale else 0), *divmod(zs.st_mtime_ns, 1_000_000_000), len(ticks)) + struct.pack("<QQQQ", len(ticks), 0, 0, 0)
    head += struct.pack("<IIIIQII", len(raw), len(comp), flags, 0, 1, 0, 0)
    assert len(head) == tt.HEAD_LEN, len(head)
    os.makedirs(f"{store}/{sym}", exist_ok=True)
    with open(f"{store}/{sym}/{sym}-{date}.tk", "wb") as f:
        f.write(head + comp)
    return day_ms


def test_decode_matches_zip():
    ticks = [(100_000_000, 5, 0), (100_000_200, 5, 1), (99_999_900, 70, 0), (101_000_000, 123_456, 1), (101_000_000, 86_399_999, 0)]
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
        write_day(root, store, "AAAUSDT", "2026-09-03", ticks, g=100)
        tt.STORE_ROOT = store
        s = tt.StoreTicks(root, "AAAUSDT")
        a = s._read(2)
        o = L.Ticks._read(L.Ticks(root, "AAAUSDT"), 2)
        assert s.n_store == 1 and s.n_zip == 0
        assert np.array_equal(a[0], o[0]) and np.array_equal(a[1], o[1]), (a, o)
        assert s.span(2 * DAY, 2 * DAY + 100)[0].tolist() == [2 * DAY + 5, 2 * DAY + 5, 2 * DAY + 70]


def test_stale_or_inexact_store_falls_back_to_zip():
    ticks = [(100_000_000, 5, 0), (100_000_100, 9, 0)]
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
        write_day(root, store, "AAAUSDT", "2026-09-03", ticks, stale=True)
        write_day(root, store, "AAAUSDT", "2026-09-04", ticks, flags=tt.F_PX_INEXACT)
        tt.STORE_ROOT = store
        s = tt.StoreTicks(root, "AAAUSDT")
        for d in (2, 3):
            v = s._read(d)
            assert v is not None and v[1].tolist() == [100_000_000, 100_000_100]
        assert s.n_store == 0 and s.n_zip == 2


def test_missing_day_is_none_and_forward_day_is_guarded():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as store:
        tt.STORE_ROOT = store
        s = tt.StoreTicks(root, "AAAUSDT")
        assert s._read(5) is None and s.n_missing == 1
        write_day(root, store, "AAAUSDT", "2026-11-14", [(1, 1, 0)])     # зип ≥ 14.11 есть — читать нельзя
        try:
            s._read(74)
        except RuntimeError as e:
            assert "14.11" in str(e)
        else:
            raise AssertionError("сутки 14.11 должны быть закрыты")


def test_decode_corruption_returns_none():
    raw = np.frombuffer(encode_day([(5, 1, 0), (6, 2, 0)]), dtype=np.uint8)
    _, _, ok = tt._decode(raw[:-1].copy(), 2, 0)
    assert not ok
    _, _, ok = tt._decode(np.concatenate([raw, np.array([1], dtype=np.uint8)]), 2, 0)
    assert not ok


# ---- hist --------------------------------------------------------------------------------------------------------------------

EVC = "side,sym,level,sig_ms,indep,ok_ev,cross_ms,natr_e8,lp_e8,locate_fail,r05,t05,r10,t10,r20,t20\n"
WIN = {w.name: w for w in st.HIST_WINDOWS}


def ev_row(side, sym, w, abs_sig, abs_cross, code, abs_tres):
    t0 = WIN[w].t0_ms
    return "%s,%s,1,%d,1,1,%d,1000,1000000,0,%s,%d,%s,%d,%s,%d\n" % (side, sym, abs_sig - t0, abs_cross - t0, code, abs_tres - t0, code, abs_tres - t0, code, abs_tres - t0)


def utc_ms(m, d, h=0):
    return int(dt.datetime(2026, m, d, h, tzinfo=dt.timezone.utc).timestamp() * 1000)


def make_events(tmp, code9="o"):
    rows = {"w0": "", "wa": "", "wb": "", "wc": ""}
    rows["w0"] += ev_row("long", "AAAUSDT", "w0", utc_ms(6, 25), utc_ms(6, 25, 1), "o", utc_ms(6, 25, 2))
    rows["wa"] += ev_row("long", "AAAUSDT", "wa", utc_ms(7, 3), utc_ms(7, 3, 1), "o", utc_ms(7, 3, 2))
    rows["wa"] += ev_row("long", "AAAUSDT", "wa", utc_ms(7, 5), utc_ms(7, 5, 1), "f", utc_ms(7, 5, 2))
    rows["wa"] += ev_row("short", "AAAUSDT", "wa", utc_ms(7, 4), utc_ms(7, 4, 1), "o", utc_ms(7, 4, 2))
    rows["wa"] += ev_row("long", "AAAUSDT", "wa", utc_ms(7, 9), utc_ms(7, 9, 1), code9, utc_ms(7, 9, 2))
    rows["wb"] += ev_row("long", "AAAUSDT", "wb", utc_ms(9, 1), utc_ms(9, 1, 1), "o", utc_ms(9, 1, 2))
    for w, s in rows.items():
        open(os.path.join(tmp, w + ".csv"), "w", encoding="utf-8").write(EVC + s)
    cd = os.path.join(tmp, "cd.json")
    json.dump({"AAAUSDT": "1" * 160, "BBBUSDT": "1" * 160}, open(cd, "w"))
    un = os.path.join(tmp, "u.txt")
    open(un, "w").write("AAAUSDT,BBBUSDT\n")
    return dict(ev_dir=tmp, coindays=cd, universe=un)


def test_hist_counts_validity_foreign():
    with tempfile.TemporaryDirectory() as tmp:
        kw = make_events(tmp)
        hi._C.clear()
        t = utc_ms(7, 10)
        rows = pd.DataFrame({"row_id": [7, 8, 9], "sym": ["AAAUSDT", "AAAUSDT", "BBBUSDT"], "abs_ms": [t, utc_ms(7, 5, 1), t]})
        g = hi.compute(rows, "wa", "long", **kw)
        assert list(g.index) == [7, 8, 9] and list(g.columns) == hi.COLUMNS
        r0 = g.loc[7]
        assert r0["ok05_d7"] == 2 and r0["ok05_d14"] == 2                    # окно (03.07, 10.07]: успехи 03.07 и 09.07 (05.07 — возврат); 25.06 вне D = 14
        assert r0["xok05_d7"] == 1 and np.isnan(r0["ok05_d30"])              # окно D=30 начинается до 24.06 → NaN; чужой short — 04.07
        assert g.loc[9]["ok05_d7"] == 0                                      # монета без событий — 0, окно определено
        # строка 8 (05.07 01:00): событие 05.07 закрыто только в 02:00 + 5 мин — не учтено, 03.07 и 04.07(чужое) учтены
        r1 = g.loc[8]
        assert r1["ok05_d7"] == 1 and r1["xok05_d7"] == 1


def brute(cr, side, sym, t, d, m):
    k = (cr.side == side) & (cr.sym == sym) & (cr.code[m] == "o") & (cr.tres[m] + f5.L_MS <= t) & (cr.cross > t - d * DAY) & (cr.cross <= t)
    return int(k.sum())


def test_hist_vector_equals_brute_and_is_causal():
    with tempfile.TemporaryDirectory() as tmp:
        kw = make_events(tmp)
        hi._C.clear()
        cr, hist = hi.load(**kw)
        rng = np.random.default_rng(1)
        ts = rng.integers(utc_ms(7, 8), utc_ms(9, 3), size=60)
        rows = pd.DataFrame({"row_id": np.arange(60), "sym": ["AAAUSDT"] * 60, "abs_ms": ts})
        g = hi.compute(rows, "wa", "long", **kw)
        for i, t in enumerate(ts):
            for m in f5.MS_NAMES:
                for d in f5.DS:
                    v = hist["long"].valid(np.array(["AAAUSDT"], dtype=object), np.array([t]), d)[0]
                    want = brute(cr, "long", "AAAUSDT", int(t), d, m) if v else np.nan
                    got = g.loc[i, f5.col_name(m, d)]
                    assert got == want or (np.isnan(got) and np.isnan(want)), (i, m, d, got, want)
        # группировка по монетам = прямой вызов TK-625 на всём наборе (одинаковые значения)
        for pref, sd in (("", "long"), ("x", "short")):
            direct = f5.ok_matrix(f5.features(hist[sd], np.array(["AAAUSDT"] * 60, dtype=object), ts))
            for k, v in direct.items():
                w = g[pref + k].to_numpy()
                assert np.array_equal(np.isnan(v), np.isnan(w)) and np.array_equal(v[~np.isnan(v)], w[~np.isnan(w)]), k
        # причинность: исход события, закрывающегося после t (09.07), признак на t = 05.07 01:00 не меняет
        t = utc_ms(7, 5, 1)
        one = pd.DataFrame({"row_id": [0], "sym": ["AAAUSDT"], "abs_ms": [t]})
        base = hi.compute(one, "wa", "long", **kw).iloc[0]
    with tempfile.TemporaryDirectory() as tmp2:
        kw2 = make_events(tmp2, code9="f")
        hi._C.clear()
        again = hi.compute(one, "wa", "long", **kw2).iloc[0]
        assert all(base[c] == again[c] or (np.isnan(base[c]) and np.isnan(again[c])) for c in hi.COLUMNS)


def test_hist_guard_rejects_forward_days():
    with tempfile.TemporaryDirectory() as tmp:
        kw = make_events(tmp)
        hi._C.clear()
        try:
            hi.compute(pd.DataFrame({"row_id": [0], "sym": ["AAAUSDT"], "abs_ms": [utc_ms(11, 14)]}), "wc", "long", **kw)
        except st.GuardError:
            return
        raise AssertionError("время ≥ 14.11 должно отклоняться")


# ---- bk ----------------------------------------------------------------------------------------------------------------------

def make_eps(tmp):
    for w in st.HIST_WINDOWS:
        n = 30 if w.name == "wa" else 0
        lines = [",".join(tf.EP_COLS)]
        for i in range(n):
            sig = utc_ms(7, 2) + i * 2 * 3_600_000 - w.t0_ms
            lines.append("long,AAAUSDT,1,%d,1,1,%d,,1000,1000000,0,0" % (sig, i % 3 == 0))
        open(os.path.join(tmp, w.name + ".csv"), "w", encoding="utf-8").write("\n".join(lines) + "\n")


def test_bk_n_k_bk20_and_closure():
    with tempfile.TemporaryDirectory() as tmp:
        make_eps(tmp)
        bk._C.clear()
        t_last = utc_ms(7, 2) + 29 * 2 * 3_600_000
        t = t_last + 65 * MIN                                          # последний эпизод закрыт ровно к t
        rows = pd.DataFrame({"row_id": [1, 2, 3], "sym": ["AAAUSDT", "AAAUSDT", "ZZZUSDT"], "abs_ms": [t, t - 1, t]})
        g = bk.compute(rows, "wa", "long", ep_dir=tmp)
        assert g.loc[1, "bk_n"] == 30 and g.loc[1, "bk_k"] == 10 and abs(g.loc[1, "bk20"] - 10 / 30) < 1e-12 and np.isnan(g.loc[1, "bk40"])
        assert g.loc[2, "bk_n"] == 29                                   # на 1 мс раньше — последний эпизод ещё не закрыт
        assert g.loc[3, "bk_n"] == 0 and np.isnan(g.loc[3, "bk20"])
        g2 = bk.compute(rows, "wa", "short", ep_dir=tmp)               # история другой стороны пуста
        assert (g2["bk_n"] == 0).all()


if __name__ == "__main__":
    bad = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("ok  ", n)
        except Exception:
            bad += 1
            print("FAIL", n)
            traceback.print_exc()
    print("%d из %d прошли" % (len(tests) - bad, len(tests)))
    sys.exit(1 if bad else 0)
