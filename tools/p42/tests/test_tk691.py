#!/usr/bin/env python3
"""TK-691 (§13.77, PS-988): пятое звено цепочки `tk691-frozen.json` в `tk616_forward.py` — решающая и читаемая вселенная `crypto` (c539, TradFi нет — В-175), порог N лонга на c539, возврат α клеток, не прошедших
ворота c539. Синтетика, заготовки — `test_tk616_forward.py` (среда `Env`, кандидаты tk638 / tk641) и `test_tk670.py` (кандидат tk670). Запуск: `python3 tools/p42/tests/test_tk691.py` (на srv)."""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import retitr_full as rf  # noqa: E402
import test_tk616_forward as T  # noqa: E402
import test_tk670 as U  # noqa: E402
import tk616_forward as fw  # noqa: E402

fw.REQUIRE_CANDIDATE4 = False                         # прежние тесты — без `tk691-frozen.json`; охрана «нет звена 5 — остановка» проверяется в `strict4()`
N_C539 = 250000


@contextlib.contextmanager
def strict4():
    fw.REQUIRE_CANDIDATE4 = True
    try:
        yield
    finally:
        fw.REQUIRE_CANDIDATE4 = False


@contextlib.contextmanager
def strict_all():
    with T.strict(), T.strict2(), U.strict3(), strict4():
        yield


class Rec(T.N24Runner):
    """Движок таблиц `L_n24` + запись бинарей запусков (`cmd[0]`) и строк `--variants`."""

    def __init__(self, last_day):
        super().__init__(last_day)
        self.bins, self.vids = [], []

    def run(self, cmd, env=None, log=None):
        if "--variants" in cmd:
            self.bins.append(cmd[0])
            self.vids.append([x.split(" ", 1)[0] for x in open(cmd[cmd.index("--variants") + 1], encoding="utf-8")])
        return super().run(cmd, env=env, log=log)


def make_candidate4(e, c, cp, c2, cp2, c3, cp3, passed=(True, True, True), mutate=None):
    """Звено 5 `tk691-frozen-1` против заморозки и звеньев среды `e`: universe.read = decision = crypto, N 250 000, ворота `passed` по (L_n24, L_mk2, D_S), α tk670 с возвратом α не прошедших."""
    f = json.loads(json.dumps(e.fz))
    un = dict(f["universe"])
    un["read"], un["decision"] = [fw.CAND4_DECISION], fw.CAND4_DECISION
    gates = {k: {"gate": g, "passed": bool(p)} for (k, g), p in zip((("L_n24", "G-638a"), ("L_mk2", "G-626c"), ("D_S", "G-670a")), passed)}
    not_read = [k for k in fw.CAND4_GATED if not gates[k]["passed"]]
    alpha = dict(c3["alpha"])
    for k in not_read:
        alpha[fw.CAND4_FALLBACK[k]] = round(alpha[fw.CAND4_FALLBACK[k]] + alpha[k], 10)
        alpha[k] = 0.0
    c4 = {"version": "tk691-frozen-1", "base_freeze": {"version": "tk626-frozen-1", "sha256": fw.sha256_text(e.freeze)},
          "prev_candidate": {"version": "tk638-frozen-1", "sha256": fw.sha256_text(cp)}, "prev_candidate2": {"version": "tk641-frozen-1", "sha256": fw.sha256_text(cp2)},
          "prev_candidate3": {"version": "tk670-frozen-1", "sha256": fw.sha256_text(cp3)}, "binary": f["binary"], "ds_binary": json.loads(json.dumps(c3["ds_binary"])), "universe": un, "point_shares": f["point_shares"],
          "points": f["points"], "windows": f["windows"], "filter": {"name": c["filter"]["name"], "N": N_C539}, "gates": gates, "not_read": not_read,
          "alpha": alpha, "alpha_k": {k: {p: v * s for p, s in f["point_shares"].items()} for k, v in alpha.items()}}
    if mutate:
        mutate(c4)
    path = fw.candidate4_path(e.freeze)
    json.dump(c4, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return c4, path


def cand4_env(t, passed=(True, True, True), mutate=None):
    e, c, cp, c2, cp2, c3, cp3 = U.cand3_env(t)
    c4, cp4 = make_candidate4(e, c, cp, c2, cp2, c3, cp3, passed, mutate)
    return e, c, cp, c2, cp2, c3, cp3, c4, cp4


def kw4(e, cp, cp2, cp3, cp4, **kw):
    out = U.cand3_kw(e, cp, cp2, cp3, candidate4="", candidate4_commit="c0ffee", candidate4_sha256=fw.sha256_text(cp4))
    out.update(kw)
    return out


def prep4(e, pid, last_day, mutate=None):
    """Точка c539: списки и contracts.csv как у прежних (all / crypto / tradfi — база не меняется), но деревья окон — только на монеты crypto (TradFi не готовится, В-175) + klines суток ПЕРЕД листингом прироста."""
    f = fw.load_freeze(e.freeze)
    wins = {w.name: w for w in fw.windows_of(f)}
    extra = T.EXTRA[pid]
    T.write_universe(e.fwd, pid, extra, mutate)
    T.mk_tree(e.fwd, pid, [wins[n] for n in fw.get_point(f, pid)["windows"]], last_day, coins=T.CRYPTO + [x[0] for x in extra if not x[1]], first=T.FIRST)
    for w in fw.windows_of(f):
        if w.name not in fw.get_point(f, pid)["windows"]:
            continue
        for c, first in T.FIRST.items():
            p = os.path.join(e.fwd, pid, w.name, "klines", c)
            day = T.D(first) - datetime.timedelta(days=1)
            if os.path.isdir(p) and day >= w.t0d:
                open(os.path.join(p, "%s-klines-%s.zip" % (c, day)), "w").close()


def test_candidate4_guards_every_rule():
    def bad(needle, mutate, passed=(True, True, True)):
        with T.tmp() as t:
            e, c, cp, c2, cp2, c3, cp3, c4, cp4 = cand4_env(t, passed, mutate)
            T.expect_guard(lambda: fw.load_candidate4(cp4, fw.load_freeze(e.freeze), e.freeze, c, cp, c2, cp2, c3, cp3), needle)

    with T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3, c4, cp4 = cand4_env(t)
        fz = fw.load_freeze(e.freeze)
        ok = fw.load_candidate4(cp4, fz, e.freeze, c, cp, c2, cp2, c3, cp3)
        assert ok["alpha"] == c3["alpha"] and ok["not_read"] == [] and ok["universe"]["read"] == ["crypto"] and ok["filter"]["N"] == N_C539
        T.expect_guard(lambda: fw.load_candidate4(cp4, fz, e.freeze, c, cp, c2, cp2, None, cp3), "цепочка tk626 → tk638 → tk641 → tk670 → tk691 разорвана")
        T.expect_guard(lambda: fw.load_candidate4(os.path.join(t, "нет.json"), fz, e.freeze, c, cp, c2, cp2, c3, cp3), "нет файла кандидата")
        open(cp3, "a").write("\n ")                                                                     # tk670 изменён после записи tk691
        T.expect_guard(lambda: fw.load_candidate4(cp4, fz, e.freeze, c, cp, c2, cp2, c3, cp3), "записан против другого файла")
    bad("версия кандидата 4", lambda x: x.update(version="tk691-frozen-2"))
    bad("записан против другого файла", lambda x: x["prev_candidate3"].update(sha256="0" * 64))
    bad("записан против другого файла", lambda x: x["prev_candidate2"].update(sha256="0" * 64))
    bad("записан против другого файла", lambda x: x["prev_candidate"].update(version="tk638-frozen-2"))
    bad("записан против другого файла", lambda x: x["base_freeze"].update(sha256="0" * 64))
    bad("поле 'binary'", lambda x: x["binary"].update(md5="0" * 32))
    bad("поле 'points'", lambda x: x["points"][1].update(d="2026-12-30"))
    bad("`ds_binary` расходится", lambda x: x["ds_binary"].update(md5="0" * 32))
    bad("`universe` расходится", lambda x: x["universe"]["base"]["crypto"].update(sha256="0" * 64))
    bad("`universe` расходится", lambda x: x["universe"].update(extra=1))
    bad("вселенные чтения", lambda x: x["universe"].update(read=["all", "crypto", "tradfi"]))               # TradFi не читается (В-175)
    bad("вселенные чтения", lambda x: x["universe"].update(decision="all"))
    bad("вселенные чтения", lambda x: x["universe"].update(read=["all"], decision="crypto"))                # решающая вселенная вне читаемых
    bad("порог N не положителен", lambda x: x["filter"].update(N=0))
    bad("порог N не положителен", lambda x: x["filter"].update(name="N24_q70"))
    bad("`gates`", lambda x: x["gates"].pop("D_S"))
    bad("`gates`", lambda x: x["gates"]["L_mk2"].update(passed="да"))
    bad("`not_read`", lambda x: x.update(not_read=["L_n24"]))                                              # ворота пройдены, а клетка помечена не читаемой
    bad("`not_read`", lambda x: x.update(not_read=[]), passed=(True, False, True))
    bad("α кандидата 4 заданы", lambda x: x["alpha"].pop("D_S"))
    bad("сумма α кандидата 4", lambda x: x["alpha"].update(D_S=0.006))
    bad("α кандидата 4 ≠ α tk670", lambda x: x["alpha"].update(L_n24=0.0, S_base=0.025))                    # не пройдена не помечена, α всё равно снята
    bad("α кандидата 4 ≠ α tk670", lambda x: x["alpha"].update(S_base=0.025, S_mk1=0.005), passed=(True, True, False))                  # α D_S ушла на S_base, а не на S_mk1
    bad("α кандидата 4 ≠ α tk670", lambda x: x["alpha"].update(S_base=0.020, L_base=0.015), passed=(False, True, True))                             # не возвращена
    bad("α по точкам", lambda x: x["alpha_k"]["D_S"].update(L5=0.0165))
    bad("α по точкам", lambda x: x["alpha_k"].pop("D_S"))


def test_real_files_chain_alpha_cells_and_universe_of_the_committed_file():
    """Реальная цепочка репозитория tk626 → tk638 → tk641 → tk670 → tk691: исход ворот c539 (`L_mk2` не прошла G-626c — g3), α возвращена `S_base`, читается и решает `crypto`, N лонга 250 000."""
    fz0 = fw.load_freeze(fw.FREEZE)
    cp, cp2, cp3, cp4 = fw.candidate_path(fw.FREEZE), fw.candidate2_path(fw.FREEZE), fw.candidate3_path(fw.FREEZE), fw.candidate4_path(fw.FREEZE)
    assert os.path.basename(cp4) == "tk691-frozen.json"
    c = fw.load_candidate(cp, fz0, fw.FREEZE)
    c2 = fw.load_candidate2(cp2, fz0, fw.FREEZE, c, cp)
    c3 = fw.load_candidate3(cp3, fz0, fw.FREEZE, c, cp, c2, cp2)
    c4 = fw.load_candidate4(cp4, fz0, fw.FREEZE, c, cp, c2, cp2, c3, cp3)
    assert c4["not_read"] == ["L_mk2"] and c4["gates"]["L_n24"]["passed"] and c4["gates"]["D_S"]["passed"] and not c4["gates"]["L_mk2"]["passed"]
    assert c4["alpha"] == {"S_base": 0.025, "S_mk1": 0.005, "L_base": 0.01, "L_n24": 0.005, "L_mk2": 0.0, "D_S": 0.005} and abs(sum(c4["alpha"].values()) - 0.05) <= 1e-12
    fz = fw.apply_candidate3(fw.apply_candidate2(fw.apply_candidate(fz0, c), c2, True), c3)
    fz, c1 = fw.apply_candidate4(fz, c, c4)
    assert "L_mk2" not in fz["cells"] and fz["universe"]["read"] == ["crypto"] and fz["universe"]["decision"] == "crypto" and c1["filter"]["N"] == 250000 and c["filter"]["N"] == 220000
    assert abs(fw.alpha_k(fz, "S_base", "L5") - 0.025 * 0.66) < 1e-12 and fw.alpha_k(fz, "L_mk2", "L5") == 0.0
    assert c4["universe"]["base"]["crypto"] == fz0["universe"]["base"]["crypto"] and c4["universe"]["base"]["crypto"]["n"] == 539 and c4["ds_binary"] == c3["ds_binary"] and c4["binary"] == fz0["binary"]


def test_alpha_returns_to_the_named_donors_when_gates_fail():
    with T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3, c4, cp4 = cand4_env(t, (False, False, False))
        fz = fw.load_freeze(e.freeze)
        ok = fw.load_candidate4(cp4, fz, e.freeze, c, cp, c2, cp2, c3, cp3)
        assert ok["not_read"] == ["L_n24", "L_mk2", "D_S"]
        assert ok["alpha"] == {"S_base": 0.03, "S_mk1": 0.01, "L_base": 0.01, "L_n24": 0.0, "L_mk2": 0.0, "D_S": 0.0} and abs(sum(ok["alpha"].values()) - 0.05) < 1e-12
    with T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3, c4, cp4 = cand4_env(t, (True, False, True))
        ok = fw.load_candidate4(cp4, fw.load_freeze(e.freeze), e.freeze, c, cp, c2, cp2, c3, cp3)
        assert ok["not_read"] == ["L_mk2"] and abs(ok["alpha"]["S_base"] - 0.025) < 1e-12 and ok["alpha"]["L_n24"] == 0.005 and ok["alpha"]["D_S"] == 0.005
        fz2, c1 = fw.apply_candidate4(fw.apply_candidate2(fw.apply_candidate(fw.load_freeze(e.freeze), c), c2, True), c, ok)
        assert "L_mk2" not in fz2["cells"] and c1["filter"]["N"] == N_C539 and c["filter"]["N"] != N_C539        # исходное звено 1 не мутировано
        assert fz2["universe"]["read"] == ["crypto"] and fz2["universe"]["decision"] == "crypto"


def test_run_point_reads_crypto_only_decides_on_it_with_the_new_n_and_alpha_only_on_crypto():
    with strict_all(), T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3, c4, cp4 = cand4_env(t)
        r = Rec(T.D("2026-11-30"))
        prep4(e, "L1", T.D("2026-11-30"))
        rc, r = T.run_c(e, "L1", T.D("2026-12-01"), T.D("2026-11-30"), runner=r, **kw4(e, cp, cp2, cp3, cp4))
        assert rc == 0
        marker = T.read_json(e.fwd, "READ-L1.started")
        assert marker["candidate4"] == "tk691" and marker["candidate4_sha256"] == fw.sha256_text(cp4) and marker["candidate4_N"] == N_C539 and marker["candidate4_not_read"] == []
        assert marker["universe_read"] == ["crypto"] and marker["universe_decision"] == "crypto" and list(marker["universe_n"]) == ["crypto"]
        assert all(set(s) <= set(T.CRYPTO + ["NEWAUSDT"]) for s in r.symbols) and any(set(T.TRADFI) & set(s) for s in r.symbols) is False      # TradFi в движок не уходит
        p1 = T.read_json(e.fwd, "point-L1.json")
        assert {k.split("|")[1] for k in p1["stats"]} == {"crypto"} and {k.split("|")[0] for k in p1["stats"]} == {"S_base", "S_mk1", "L_base", "L_mk2", "L_n24", "D_S"}
        assert p1["universe"]["n"] == {"crypto": len(T.CRYPTO) + 1} and all(v["n"] > 0 for v in p1["stats"].values())
        assert p1["candidate4"]["candidate4_N"] == N_C539 and p1["n24"]["crypto"]["status"] == "read" and p1["ds"]["crypto"]["status"] == "read"
        prep4(e, "L2", T.D("2026-12-31"))
        rc2, _ = T.run_c(e, "L2", T.D("2027-01-01"), T.D("2026-12-31"), runner=Rec(T.D("2026-12-31")), **kw4(e, cp, cp2, cp3, cp4))
        assert rc2 == 0
        p2 = T.read_json(e.fwd, "point-L2.json")
        s = p2["stats"]
        assert all(k.endswith("|crypto") for k in s) and abs(s["S_base|crypto"]["alpha_k"] - 0.020 * 0.04) < 1e-12 and abs(s["D_S|crypto"]["alpha_k"] - 0.005 * 0.04) < 1e-12
        assert abs(s["L_n24|crypto"]["alpha_k"] - 0.005 * 0.04) < 1e-12 and abs(s["L_mk2|crypto"]["alpha_k"] - 0.005 * 0.04) < 1e-12                  # α_k только у решающей вселенной
        assert p2["prefix"]["diffs"]["D_S|crypto"] == 0 and p2["universe"]["n"] == {"crypto": len(T.CRYPTO) + 1}                                    # прирост TradFi в тесте (NEWB) вселенную чтения не расширяет
        assert "решающая вселенная — crypto" in fw.render(p1)


def test_cells_with_failed_gates_are_not_read_and_alpha_goes_back():
    with strict_all(), T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3, c4, cp4 = cand4_env(t, (False, False, False))
        r = Rec(T.D("2026-11-30"))
        prep4(e, "L1", T.D("2026-11-30"))
        rc, r = T.run_c(e, "L1", T.D("2026-12-01"), T.D("2026-11-30"), runner=r, **kw4(e, cp, cp2, cp3, cp4))
        assert rc == 0
        marker = T.read_json(e.fwd, "READ-L1.started")
        assert marker["candidate4_not_read"] == ["L_n24", "L_mk2", "D_S"]
        assert e.ds not in r.bins and not any(c3["cells"]["D_S"]["vid"] in v for v in r.vids)                                                  # D_S не запускался
        assert not any(c2["cells"]["L_mk2"]["vid"] in v for v in r.vids) and not any(c["cells"]["L_n24"]["vid"] in v for v in r.vids)
        p1 = T.read_json(e.fwd, "point-L1.json")
        assert {k.split("|")[0] for k in p1["stats"]} == {"S_base", "S_mk1", "L_base"} and "n24" in p1 and p1["n24"] == {} and p1["ds"] == {}
        assert "клетки не читаются" in fw.render(p1)
        prep4(e, "L2", T.D("2026-12-31"))
        rc2, _ = T.run_c(e, "L2", T.D("2027-01-01"), T.D("2026-12-31"), runner=Rec(T.D("2026-12-31")), **kw4(e, cp, cp2, cp3, cp4))
        s = T.read_json(e.fwd, "point-L2.json")["stats"]
        assert abs(s["S_base|crypto"]["alpha_k"] - 0.030 * 0.04) < 1e-12 and abs(s["S_mk1|crypto"]["alpha_k"] - 0.010 * 0.04) < 1e-12 and abs(s["L_base|crypto"]["alpha_k"] - 0.010 * 0.04) < 1e-12


def test_missing_deciding_universe_list_or_file_stops_before_the_marker():
    with strict_all(), T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3, c4, cp4 = cand4_env(t)
        prep4(e, "L1", T.D("2026-11-30"))
        os.remove(os.path.join(e.fwd, "L1", "universe", "symbols-crypto.txt"))
        T.expect_guard(lambda: T.run_c(e, "L1", T.D("2026-12-01"), T.D("2026-11-30"), runner=Rec(T.D("2026-11-30")), **kw4(e, cp, cp2, cp3, cp4)), "нет списка решающей вселенной")
        assert not os.path.exists(os.path.join(e.fwd, "READ-L1.started"))
        os.remove(cp4)
        T.expect_guard(lambda: T.run_c(e, "L1", T.D("2026-12-01"), T.D("2026-11-30"), runner=Rec(T.D("2026-11-30")), **U.cand3_kw(e, cp, cp2, cp3, candidate4="")), "нет файла кандидата")
        assert not os.path.exists(os.path.join(e.fwd, "READ-L1.started"))
        T.expect_guard(lambda: T.run_c(e, "L1", T.D("2026-12-01"), T.D("2026-11-30"), runner=Rec(T.D("2026-11-30")), **U.cand3_kw(e, cp, cp2, cp3)), "нет файла кандидата")   # звена 5 рядом нет: α tk670 без него не применяются
        assert not os.path.exists(os.path.join(e.fwd, "READ-L1.started"))


def test_crypto_universe_check_keeps_the_base_and_the_increment_rule():
    with strict_all(), T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3, c4, cp4 = cand4_env(t)
        f = fw.load_freeze(e.freeze)
        c4_ = fw.load_candidate4(cp4, f, e.freeze, c, cp, c2, cp2, c3, cp3)
        fz, _ = fw.apply_candidate4(f, c, c4_)
        pt = fw.get_point(fz, "L1")
        prep4(e, "L1", T.D("2026-11-30"))
        u = os.path.join(e.fwd, "L1", "universe")
        ok = fw.check_universe(fz, u, e.base_dir, pt)
        assert list(ok["lists"]) == ["crypto"] and ok["lists"]["crypto"] == T.CRYPTO + ["NEWAUSDT"] and list(ok["sha256"]) == ["crypto"]
        lst = os.path.join(u, "symbols-crypto.txt")
        good = open(lst, encoding="utf-8").read()
        open(lst, "w", encoding="utf-8").write(",".join(T.CRYPTO[1:] + ["NEWAUSDT"]) + "\n")                       # базовой монеты нет в списке точки
        T.expect_guard(lambda: fw.check_universe(fz, u, e.base_dir, pt), "не содержит базовых монет")
        open(lst, "w", encoding="utf-8").write(good)
        open(os.path.join(e.base_dir, "symbols-crypto.txt"), "w", encoding="utf-8").write(",".join(T.CRYPTO[1:]) + "\n")      # база не та (sha256 заморожен)
        T.expect_guard(lambda: fw.check_universe(fz, u, e.base_dir, pt), "базовый список")
        T.write_list(os.path.join(e.base_dir, "symbols-crypto.txt"), T.CRYPTO)
        T.write_universe(e.fwd, "L1", [("NEWAUSDT", False, "2026-11-10", "2026-11-30")])                            # листинг ≤ 13.11 — не прирост
        T.expect_guard(lambda: fw.check_universe(fz, u, e.base_dir, pt), "прирост NEWAUSDT")
        T.write_universe(e.fwd, "L1", [("NEWAUSDT", False, "2026-11-20", "2026-11-30")], lambda lists, rows: rows[:] and rows.__setitem__(slice(None), [r for r in rows if r["symbol"] != "AAAUSDT"]))
        T.expect_guard(lambda: fw.check_universe(fz, u, e.base_dir, pt), "не содержит монет списка crypto")        # нет строки contracts.csv у монеты списка
        T.write_universe(e.fwd, "L1", [("NEWAUSDT", False, "2026-11-20", "2026-11-30")])
        assert fw.check_universe(fz, u, e.base_dir, pt)["lists"]["crypto"] == T.CRYPTO + ["NEWAUSDT"]


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
