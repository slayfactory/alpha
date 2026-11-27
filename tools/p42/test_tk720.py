"""TK-720: чистые функции форварда B (просадка / перехай по префиксам, таблицы остановки, сутки по входу, сравнение сделок). Запуск: `python -m pytest test_tk720.py` или `python test_tk720.py`."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk720_fwd as F  # noqa: E402
import tk692_walk as W  # noqa: E402


def test_underwater_matches_walk():
    rng = np.random.default_rng(1)
    for _ in range(50):
        x = rng.normal(0.3, 2.0, rng.integers(1, 40)) * (rng.random(40)[: 1] > 0.1)
        x = np.where(rng.random(len(x)) < 0.3, 0.0, x)
        a, b = F.underwater(x), W.underwater(x)
        assert a["maxdd"] == b["maxdd"] and a["gap_longest"] == b["gap_longest"] and a["tail"] == b["tail"]


def test_running_dd_gap_matches_prefixes():
    rng = np.random.default_rng(2)
    paths = rng.normal(0.2, 1.5, (40, 30))
    paths[rng.random(paths.shape) < 0.25] = 0.0
    dd, gap = F.running_dd_gap(paths)
    for i in range(paths.shape[0]):
        for n in (1, 2, 5, 17, 30):
            u = W.underwater(paths[i, :n])
            assert abs(dd[i, n - 1] - u["maxdd"]) < 1e-12, (i, n)
            assert gap[i, n - 1] == u["gap_longest"], (i, n, gap[i, n - 1], u["gap_longest"])


def test_kill_tables_shape_and_monotone():
    rng = np.random.default_rng(3)
    sel = rng.normal(0.9, 5.0, 136)
    k = F.kill_tables(sel, boot=400)
    assert len(k["q95"]["dd"]) == F.KILL_N and len(k["cone"]["q50"]) == F.KILL_N
    assert all(b >= a - 1e-9 for a, b in zip(k["q95"]["dd"], k["q95"]["dd"][1:]))          # DD по n не убывает
    assert all(q99 >= q95 - 1e-9 for q95, q99 in zip(k["q95"]["dd"], k["q99"]["dd"]))
    assert k["level"] in (95, 99) and 0 <= k["q95"]["false_stop"] <= 1


def test_kill_flags():
    kill = {"level": 95, "q95": {"dd": [10.0] * 60, "gap": [3.0] * 60}}
    ok = F.kill_flags(np.array([1.0, -2.0, 1.5]), kill)
    assert ok["stop"] is False and ok["n"] == 3
    dd = F.kill_flags(np.array([1.0, -12.0]), kill)
    assert dd["stop_dd"] is True and dd["stop"] is True
    gp = F.kill_flags(np.array([1.0, 0, 0, 0, 0.0]), kill)          # 4 суток без нового максимума после первого → перехай 4 > 3
    assert gp["gap"] == 4 and gp["stop_gap"] is True
    none = F.kill_flags(np.zeros(61), kill)
    assert none["stop"] is None


def test_trades_daily_by_entry_day():
    t0_ms = F.D0_MS if hasattr(F, "D0_MS") else 0
    import datetime
    t0 = int(datetime.datetime(2026, 10, 25, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    tr = [dict(entry_ms=F.DAY_MS * 31 + 5, gross="3.0", fees="0.5"), dict(entry_ms=F.DAY_MS * 31 + 99, gross="-1.0", fees="0.25"), dict(entry_ms=F.DAY_MS * 32, gross="1.0", fees="0.1")]
    pnl, cnt = F.trades_daily(tr, t0, 340)
    d = (datetime.date(2026, 10, 25) - F.D0).days + 31                  # 25.10 + 31 = 25.11
    assert d == F.F0
    assert abs(pnl[d] - (2.5 - 1.25)) < 1e-12 and cnt[d] == 2 and abs(pnl[d + 1] - 0.9) < 1e-12 and cnt[d + 1] == 1


def test_compare_trades():
    def tr(sym, e, x, g, f):
        return dict(sym=sym, entry_ms=e, exit_ms=x, gross=g, fees=f)
    a = {c: [tr("A", 1, 2, "1.0", "0.1")] for c in F.CIDS}
    b = {c: [tr("A", 1, 2, "1.0", "0.1")] for c in F.CIDS}
    assert F.compare_trades(a, b)[0] is True
    b[F.CIDS[0]] = [tr("A", 1, 3, "1.0", "0.1")]
    ok, on, orf = F.compare_trades(a, b)
    assert ok is False and len(on) == 1 and len(orf) == 1


def test_forward_table_sharpe_gate_and_cum():
    pnl = np.array([1.0, -0.5, 2.0] + [0.1] * 10)
    cnt = np.ones(len(pnl), int)
    kill = {"level": 95, "q95": {"dd": [100.0] * 60, "gap": [100.0] * 60}}
    rows = F.forward_table(pnl, cnt, kill, 0.9)
    assert rows[0]["date"] == "2026-11-25" and rows[1]["date"] == "2026-11-26"
    assert rows[8]["sharpe"] is None and rows[9]["sharpe"] is not None                # < 10 суток — «мало дней»
    assert abs(rows[2]["cum"] - 2.5) < 1e-12 and abs(rows[2]["expect"] - 2.7) < 1e-12 and rows[2]["trades"] == 3


if __name__ == "__main__":
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
