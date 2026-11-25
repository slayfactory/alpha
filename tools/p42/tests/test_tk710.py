#!/usr/bin/env python3
"""TK-710: повторяемое чтение «по вчера» (`tk616_forward.py thru`) — новый id чтения на прогон, прежние остаются, входы «закрытые» (≤ D − 4) и «по вчера» (≤ D), охраны, описание линий; крипто-подготовка
`tk626_prep.py --only crypto / --universe-name crypto`. Заготовки — `test_tk616_forward.py`, `test_tk691.py`, `test_tk708.py` (звенья цепочки до 6). Запуск: `python3 tools/p42/tests/test_tk710.py` (на srv)."""
from __future__ import annotations

import datetime
import json
import os
import sys
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import test_tk616_forward as T  # noqa: E402
import test_tk691 as V  # noqa: E402
import test_tk708 as X  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk626_prep as pp  # noqa: E402

D = T.D
NEW = ("NEWAUSDT", False, "2026-11-20", "2026-11-24")                  # прирост crypto, листинг 20.11 (до D = 24.11)


def prep_thru(e, rid, through, extra=(NEW,), coins=None):
    """Данные чтения `rid`: списки точки (база + прирост до D) и деревья окон чтения (по правилу `through_point`), только crypto (как `tk710_thru.sh`)."""
    f = fw.load_freeze(e.freeze)
    wins = {w.name: w for w in fw.windows_of(f)}
    pt = fw.through_point(f, through, rid, D("2030-01-01"))
    T.write_universe(e.fwd, rid, list(extra))
    T.mk_tree(e.fwd, rid, [wins[n] for n in pt["windows"]], D(through), coins=coins or (T.CRYPTO + [x[0] for x in extra if not x[1]]), first=T.FIRST)
    for c, first in T.FIRST.items():                                      # klines суток перед листингом прироста (как `prep4`)
        for n in pt["windows"]:
            p = os.path.join(e.fwd, rid, n, "klines", c)
            day = D(first) - datetime.timedelta(days=1)
            if os.path.isdir(p) and day >= wins[n].t0d:
                open(os.path.join(p, "%s-klines-%s.zip" % (c, day)), "w").close()
    return pt


def thru(x, through, today, rid=None, runner=None, **kw):
    rid = rid or "thru-" + through
    if not os.path.isdir(os.path.join(x.e.fwd, rid)):
        prep_thru(x.e, rid, through)
    r = runner or X.Runner5(D(through))
    rc = fw.run_point(x.e.args("", **X.kw5(x, through=through, read_id=rid, **kw)), runner=r, today=today, repo=x.e.root)
    return rc, r


def test_through_point_geometry_and_guards():
    with T.tmp() as t:
        f = fw.load_freeze(T.Env(t).freeze)
        pt = fw.through_point(f, "2026-11-24", "thru-2026-11-24", D("2026-11-25"))
        assert pt["d"] == "2026-11-24" and pt["entries_to"] == "2026-11-20" and pt["windows"] == ["w1"] and pt["decision"] is False and pt["id"] not in [p["id"] for p in f["points"]]
        assert fw.through_point(f, "2027-01-05", "thru-2027-01-05-r2", D("2027-01-06"))["windows"] == ["w1", "w2"]           # зачёт w2 начинается 25.12
        T.expect_guard(lambda: fw.through_point(f, "2026-11-24", "thru-2026-11-24", D("2026-11-24")), "ещё не закончились")           # сутки D идут — читать нельзя
        T.expect_guard(lambda: fw.through_point(f, "2026-11-24", "thru-2026-11-23", D("2026-11-25")), "нужен вид")
        T.expect_guard(lambda: fw.through_point(f, "2026-11-24", "L1", D("2026-11-25")), "нужен вид")
        T.expect_guard(lambda: fw.through_point(f, "2026-11-20", "thru-2026-11-20", D("2026-11-25")), "зачётных суток нет")             # D − 4 = 16.11 < 17.11
        T.expect_guard(lambda: fw.through_point(f, "вчера", "thru-вчера", D("2026-11-25")), "YYYY-MM-DD")
        assert fw.through_point(f, "2026-11-21", "thru-2026-11-21", D("2026-11-25"))["entries_to"] == "2026-11-17"                       # первые зачётные сутки — одни


def test_describe_rows_counts_trades_drawdown_and_days():
    day = lambda n, h=3: int(datetime.datetime(2026, 11, 17 + n, h, tzinfo=datetime.timezone.utc).timestamp() * 1000)               # noqa: E731
    rows = [("A", day(0), day(0) + 1, 3.0, 0.5, 0.0, "tp"), ("B", day(1), day(1) + 1, -4.0, 0.5, 0.0, "tp"), ("C", day(1, 5), day(1, 6), -1.0, 0.5, 0.0, "tp"), ("D", day(3), day(3) + 1, 2.0, 0.5, 0.0, "forced")]
    x = fw.describe_rows(rows, D("2026-11-21"))
    assert x["days"] == 5 and x["days_with_trades"] == 3 and x["n"] == 4 and x["forced"] == 1 and abs(x["sum"] - (2.5 - 4.5 - 1.5 + 1.5)) < 1e-9
    assert abs(x["maxdd"] - 6.0) < 1e-9 and abs(x["maxdd_pct_margin"] - 6.0 / fw.MARGIN_USD * 100) < 1e-9 and x["gap_longest"] == 4 and x["gap_tail"] == 4       # пик +2,5 → дно −3,5; ряд 2,5 / −6 / 0 / +1,5 / 0: четыре сутки под водой подряд, хвост — те же четыре
    assert abs(x["mean"] - x["sum"] / 4) < 1e-9 and x["best_day"] == 2.5 and x["worst_day"] == -6.0
    e = fw.describe_rows([], D("2026-11-21"))
    assert e["n"] == 0 and e["sum"] == 0.0 and e["maxdd"] == 0.0 and np.isnan(e["mean"])


def test_through_read_describes_every_line_and_each_run_has_its_own_id():
    with X.strict_all5(), T.tmp() as t:
        x = X.ctx5(t)
        e = x.e
        rc, r = thru(x, "2026-11-24", D("2026-11-25"))
        assert rc == 0 and e.e7 in r.bins and all(set(s) <= set(T.CRYPTO + ["NEWAUSDT"]) for s in r.symbols)                 # только crypto, линии идут бинарём TK-674
        marker = T.read_json(e.fwd, "READ-thru-2026-11-24.started")
        assert marker["through"] == "2026-11-24" and marker["read_id"] == "thru-2026-11-24" and marker["entries_to"] == "2026-11-20" and marker["candidate5_read"] is True and marker["candidate4"] == "tk691"
        assert os.path.exists(os.path.join(e.fwd, "READ-thru-2026-11-24.done")) and os.path.exists(os.path.join(e.fwd, "tables-thru-2026-11-24.json"))
        p = T.read_json(e.fwd, "point-thru-2026-11-24.json")
        lines = {"T674_%s|crypto" % i for i in ("L1", "L2", "S1", "S2")}
        assert lines <= set(p["thru"]) and p["point"]["id"] == "thru-2026-11-24" and p["prefix"]["diffs"] == {}
        for k, v in p["thru"].items():
            c, o = v["closed"], v["through"]
            assert c["last_day"] == "2026-11-20" and c["days"] == 4 and o["last_day"] == "2026-11-24" and o["days"] == 8, k
            assert o["n"] >= c["n"] > 0 and o["forced"] >= c["forced"] and o["days_with_trades"] >= c["days_with_trades"], (k, c["n"], o["n"])
            assert p["stats"][k]["alpha_k"] == 0.0 and p["stats"][k]["decision"] == "описательно", k
        assert any(v["through"]["n"] > v["closed"]["n"] for v in p["thru"].values())                                          # входы 21–24.11 прибавляют сделки
        out = fw.render_thru(p)
        assert "| T674_L1 | crypto | ≤ D − 4 (закрытые) 2026-11-17…2026-11-20 | 4 (" in out and "| T674_S2 | crypto | ≤ D (по вчера) 2026-11-17…2026-11-24 | 8 (" in out and "перехай" in out
        T.expect_guard(lambda: thru(x, "2026-11-24", D("2026-11-25")), "повторное чтение запрещено")                                  # тот же id — второго чтения нет
        rc2, _ = thru(x, "2026-11-24", D("2026-11-25"), rid="thru-2026-11-24-r2")                                              # новый id — новое чтение, старое на месте
        assert rc2 == 0 and os.path.exists(os.path.join(e.fwd, "point-thru-2026-11-24.json")) and os.path.exists(os.path.join(e.fwd, "point-thru-2026-11-24-r2.json"))
        p2 = T.read_json(e.fwd, "point-thru-2026-11-24-r2.json")
        assert {k: v["through"]["n"] for k, v in p2["thru"].items()} == {k: v["through"]["n"] for k, v in p["thru"].items()}      # те же данные — те же сделки
        rc3, _ = thru(x, "2026-11-25", D("2026-11-26"), runner=X.Runner5(D("2026-11-25")))                                       # следующие сутки: ещё один id, без охраны «точки по порядку»
        p3 = T.read_json(e.fwd, "point-thru-2026-11-25.json")
        assert rc3 == 0 and p3["point"]["entries_to"] == "2026-11-21" and sorted(os.path.basename(f) for f in os.listdir(e.fwd) if f.startswith("READ-thru-") and f.endswith(".started")) == [
            "READ-thru-2026-11-24-r2.started", "READ-thru-2026-11-24.started", "READ-thru-2026-11-25.started"]
        assert not os.path.exists(os.path.join(e.fwd, "READ-L1.started"))                                                        # точки заморозки не затронуты (30.11 — одно чтение, как было)


def test_through_read_stops_on_a_tree_with_tradfi_and_before_the_marker_when_the_day_is_not_over():
    with X.strict_all5(), T.tmp() as t:
        x = X.ctx5(t)
        e = x.e
        prep_thru(e, "thru-2026-11-24", "2026-11-24", coins=T.COINS + ["NEWAUSDT"])                                              # дерево со всеми контрактами (старая подготовка tk626_prep.sh)
        T.expect_guard(lambda: thru(x, "2026-11-24", D("2026-11-25")), "монеты вне списка")
        assert not os.path.exists(os.path.join(e.fwd, "READ-thru-2026-11-24.started"))                                         # охрана ДО маркера
        T.expect_guard(lambda: thru(x, "2026-11-24", D("2026-11-24"), rid="thru-2026-11-24"), "ещё не закончились")
        assert not os.path.exists(os.path.join(e.fwd, "READ-thru-2026-11-24.started"))


def test_point_command_ignores_through_and_thru_needs_it():
    with X.strict_all5(), T.tmp() as t:
        x = X.ctx5(t)
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert fw.main(["thru", "--root", x.e.fwd]) == 3
        assert "--through" in err.getvalue()
        with contextlib.redirect_stderr(io.StringIO()):
            assert fw.main(["point", "L1", "--through", "2026-11-24", "--root", x.e.fwd]) == 3                                   # без TK616F_GO / файлов — остановка охраной, а не чтение «по вчера»


def test_prep_crypto_only_lists_and_tree():
    with T.tmp() as t:
        e = T.Env(t)
        T.write_universe(t, "U", [NEW, ("NEWTUSDT", True, "2026-11-21", "2026-11-24")])
        u = os.path.join(t, "U", "universe")
        ld = os.path.join(t, "list")
        os.makedirs(ld)
        contracts = {c: {"contract_type": "PERPETUAL", "status": "TRADING"} for c in T.COINS + ["NEWAUSDT"]}
        contracts["NEWTUSDT"] = {"contract_type": "TRADIFI_PERPETUAL", "status": "TRADING"}
        json.dump({"contracts": contracts}, open(os.path.join(ld, "contracts.json"), "w"))
        with open(os.path.join(ld, "files.tsv"), "w") as f:
            for c in T.COINS + ["NEWAUSDT", "NEWTUSDT"]:
                for kind in ("aggTrades", "klines"):
                    for day in ("2026-11-14", "2026-11-22", "2026-11-23"):
                        f.write("\t".join([kind, c, day, "10", "e"]) + "\n")
        out = os.path.join(t, "U2")
        assert pp.main(["universe", "--list-dir", ld, "--base-dir", e.base_dir, "--d", "2026-11-23", "--out", out, "--only", "crypto"]) == 0
        miss = {l.split("\t")[1] for l in open(os.path.join(ld, "missing.tsv"), encoding="utf-8").read().splitlines()}
        assert miss == set(T.CRYPTO) | {"NEWAUSDT"}                                                                              # TradFi (в базе и прирост) не скачиваются
        assert pp.main(["universe", "--list-dir", ld, "--base-dir", e.base_dir, "--d", "2026-11-23", "--out", os.path.join(t, "U3")]) == 0
        assert {l.split("\t")[1] for l in open(os.path.join(ld, "missing.tsv"), encoding="utf-8").read().splitlines()} >= set(T.TRADFI)       # по умолчанию — все
        args = ["--out", os.path.join(t, "P"), "--universe", out, "--freeze", e.freeze, "--d", "2026-11-23", "--windows", "w1", "--universe-name", "crypto"]
        assert pp.main(["tree"] + args + ["--allow-test", "--dl", t + "/dl", "--rest", t + "/rest"]) == 0
        assert sorted(os.listdir(os.path.join(t, "P", "w1", "klines"))) == sorted(T.CRYPTO + ["NEWAUSDT"]) and pp.main(["check"] + args) == 0                # дерево без TradFi проходит ворота crypto


def main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("ok   ", n)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL ", n, "-", type(e).__name__, e)
            tb = traceback.extract_tb(e.__traceback__)
            print("       %s:%d  %s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].line))
    print("%d тестов, %d провалов" % (len(tests), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
