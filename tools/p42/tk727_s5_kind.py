#!/usr/bin/env python3
"""TK-727, шаг 5, дополнение к поправке L-509 (вердикт проверяющего): потеря по видам выхода.

В `s5/trades.npz` нет колонки `kind` (вид выхода). Скрипт читает те же исходные CSV сделок (те же окна, смещение t0 и отбор суток, что `tk727_s5.load_trades`),
присоединяет `kind` по ключу (клетка, монета, сигнал, вход, выход) и печатает потерю по видам выхода для тех же строк модели, что R:R (`ok_mask`): семейство FAM и линии, FJ / SEL.
Пересчёта анализа нет: только присоединение и средние. Выход: `<out>/s5_kind.json`, таблица `s5_kind` в `<out>/tk727.sqlite`.

  tk727_s5_kind.py [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3

import numpy as np

import tk727_s5 as S5
import tk727_s5_an as AN


def read_kinds(cids=S5.ALL_CIDS) -> dict:
    """(cid, монета, сигнал, вход, выход — абсолютные мс) → вид выхода, для строк, попавших в зачётные сутки окна (как `load_trades`)."""
    out: dict = {}
    for w in S5.WIN_NAMES:
        lo, hi, root = S5.WINDOWS[w]
        t0 = json.load(open(os.path.join(root, "t0.json"), encoding="utf-8"))[w]
        for cid in cids:
            p = os.path.join(root, w, "%d-trades.csv" % cid)
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    sig, ent, ex = (int(r[k]) + int(t0) for k in ("sig_ms", "entry_ms", "exit_ms"))
                    ed = (ent - S5.EPOCH0) // S5.DAY_MS
                    if not (S5.day_of(lo) <= ed <= S5.day_of(hi)):
                        continue
                    key = (cid, r["sym"], sig, ent, ex)
                    if key in out and out[key] != r["kind"]:
                        raise SystemExit("ключ с двумя видами выхода: %r" % (key,))
                    out[key] = r["kind"]
    return out


def attach_kind(tr: dict, kinds: dict) -> np.ndarray:
    """Вид выхода для каждой строки `tr`; строка без пары — остановка скрипта (присоединение должно быть полным)."""
    res = np.empty(len(tr["cid"]), dtype="U12")
    for i in range(len(res)):
        k = kinds.get((int(tr["cid"][i]), str(tr["sym"][i]), int(tr["sig_ms"][i]), int(tr["entry_ms"][i]), int(tr["exit_ms"][i])))
        if k is None:
            raise SystemExit("нет пары в исходных сделках: строка %d" % i)
        res[i] = k
    return res


def loss_by_kind(pnl: np.ndarray, kind: np.ndarray) -> dict:
    """По видам выхода: все сделки (n, доля, средний $), проигрыши (pnl ≤ 0: n, доля числа, средняя потеря, доля суммы потерь). Потеря = −pnl."""
    n = len(pnl)
    loss = pnl <= 0
    nl, sl = int(loss.sum()), float(-pnl[loss].sum())
    kinds = {}
    for k in sorted(set(kind.tolist())):
        m = kind == k
        ml = m & loss
        kinds[k] = dict(n=int(m.sum()), share=float(m.mean()) if n else float("nan"), mean_pnl=float(pnl[m].mean()) if m.any() else float("nan"),
                        n_loss=int(ml.sum()), share_of_losses=float(ml.sum() / nl) if nl else float("nan"),
                        mean_loss=float(-pnl[ml].mean()) if ml.any() else float("nan"), share_of_loss_sum=float(-pnl[ml].sum() / sl) if sl > 0 else float("nan"))
    return dict(n=n, n_loss=nl, mean_loss=float(sl / nl) if nl else float("nan"), kinds=kinds)


def run(out: str) -> dict:
    tr = dict(np.load(os.path.join(out, "s5", "trades.npz"), allow_pickle=False))
    kinds = read_kinds()
    tr["kind"] = attach_kind(tr, kinds)
    res = {"kinds_all": {k: int((tr["kind"] == k).sum()) for k in sorted(set(tr["kind"].tolist()))}, "groups": {}}
    for g in ("FAM", "s4", "S1", "S2", "L401"):
        if g == "FAM":
            idx = AN.dedupe_family(tr)[0]
        else:
            idx = np.where(tr["cid"] == (7180023 if g == "s4" else S5.LINES[g]))[0]
        d = {k: tr[k][idx] for k in ("eday", "pnl", "V", "F2", "turn24", "kind")}
        for h, (lo, hi) in AN.HALVES.items():
            m = AN.ok_mask(d, lo, hi)
            res["groups"]["%s|%s" % (g, h)] = loss_by_kind(d["pnl"][m], d["kind"][m])
    json.dump(res, open(os.path.join(out, "s5_kind.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    db = sqlite3.connect(os.path.join(out, "tk727.sqlite"))
    db.execute("CREATE TABLE IF NOT EXISTS s5_kind (grp TEXT, half TEXT, kind TEXT, n INTEGER, share REAL, mean_pnl REAL, n_loss INTEGER, share_of_losses REAL, mean_loss REAL, share_of_loss_sum REAL)")
    db.execute("DELETE FROM s5_kind")
    for key, v in res["groups"].items():
        g, h = key.split("|")
        for k, x in v["kinds"].items():
            db.execute("INSERT INTO s5_kind VALUES (?,?,?,?,?,?,?,?,?,?)", (g, h, k, x["n"], x["share"], x["mean_pnl"], x["n_loss"], x["share_of_losses"], x["mean_loss"], x["share_of_loss_sum"]))
    db.commit()
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=S5.OUT)
    a = ap.parse_args()
    r = run(a.out)
    print("виды выхода (все строки):", r["kinds_all"])
    for key, v in r["groups"].items():
        print(key, "n", v["n"], "проигрышей", v["n_loss"], "средняя потеря %.3f" % v["mean_loss"])
        for k, x in v["kinds"].items():
            print("   %-6s n=%d (%.1f %%) средний $ %.3f | проигрышей %d (%.1f %% всех проигрышей), средняя потеря %.3f, доля суммы потерь %.1f %%" % (
                k, x["n"], x["share"] * 100, x["mean_pnl"], x["n_loss"], x["share_of_losses"] * 100, x["mean_loss"], x["share_of_loss_sum"] * 100))
