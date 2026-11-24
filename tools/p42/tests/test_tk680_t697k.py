"""TK-697 (третья поправка): набор t697k — единички ключей движка № 9 (`tn` / `tno`) и № 1 (`lx = 4`: `lk`, `ld`, `lsl`, `tf`), `tk680_axes.t697k_axes`. Запуск: python -m pytest tools/p42/tests/test_tk680_t697k.py"""
import json
import os
import subprocess
import sys
import textwrap

P42 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def run(code, **env):
    e = dict(os.environ, TK674_REBASE="1", PYTHONUTF8="1", TK680_SET="t697k", **env)
    r = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r)\n" % P42 + textwrap.dedent(code)], env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout.strip().splitlines()[-1])


CELLS = """
    import tk674_axes as ax, tk680_axes as a
    out = {}
    for side in "LS":
        base = ax.base_ckey(side)
        cells, axes = {}, {}
        for name, vals, bl in a.engine_axes(side):
            axes[name] = [lab for lab, _ in vals]
            for lab, patch in vals:
                c = a.make_cell(side, name, lab, patch)
                cells.setdefault(c["ckey"], c)
                if lab == bl:
                    assert c["ckey"] == base, (name, lab)
        out[side] = dict(base=base, cells=cells, axes=axes)
    print(json.dumps(out))
"""


def test_axes_and_cell_counts():
    out = run(CELLS)
    for side in "LS":
        o = out[side]
        assert o["axes"] == {"tn": ["off", "25", "50", "100"], "tno": ["off", "0", "0.1", "0.25"], "lk": ["off", "2", "3", "4"], "ld": ["off", "1", "3", "5"],
                             "lsl": ["off", "0", "0.5", "1", "2"], "lx": ["off", "tf30", "tf60"]}
        assert len(o["cells"]) - (o["base"] in o["cells"]) == 14        # 3 + 2 (tn / tno), 3 + 2 (lk / ld), 3 (lsl), 1 (tf 30): центр и tn 50 общие на оси
        sig = {tuple(map(tuple, c["sig"])) for c in o["cells"].values() if c["ckey"] != o["base"]}
        assert len({s for s in sig if s}) == 9                          # проходов сигналов: 9 клеток lx = 4 (k2 k3 k4 d1 d5 lsl × 3 tf30), tn / tno — выход


def test_keys_canonical():
    out = run(CELLS)
    for side in "LS":
        o = out[side]
        keys = set(o["cells"])
        center = [k for k in keys if "lx=4" in k and "lk=3" in k and "ld=3" in k and "lsl" not in k and "tf=" not in k]
        assert len(center) == 1                                         # центр одной клеткой: `lk` 3, `ld` 3, `lsl` 0, `tf` 60 — по умолчанию, в ключ не входят
        assert any(k.split() and "tn=50" in k.split() and not any(t.startswith("tno=") for t in k.split()) for k in keys)        # tn 50, tno = 0: tno в ключ не входит
        for k in keys:
            t = k.split(" F=")[0].split()
            assert ("lk=" in k) == ("lx=4" in t) or "lk=" not in k and "lx=4" not in t      # lk / ld / lsl только вместе с lx = 4
            assert ("tno=" not in k) or ("tn=" in k)                                         # tno только вместе с tn
        assert sum(1 for k in keys if "tf=30" in k) == 1
