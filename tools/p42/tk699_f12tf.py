#!/usr/bin/env python3
"""TK-699: f12 считал свечи уровня как 60 мин всегда — вход, ворота и дельта-пересчёт 10 столбцов на строках p30 / p240 (srv).

  tk699_f12tf.py prep  --win wa --side long --out DIR [--which tf|all] [--coin-mod N --coin-rem R]
      DIR/sigsx.csv (формат tk671_prep + 11-е поле tf_min по источнику строки), DIR/eps-key.csv (row_id, sym, sig_ms, level_px_e8, src, tf_min)
  tk699_f12tf.py gate-bin --dir DIR --old old.csv --new new.csv
      старый и новый бинарь на одном входе: 38 столбцов вне списка — байт в байт на всех строках; 10 столбцов — байт в байт на строках tf = 60
  tk699_f12tf.py gate-py  --win wa --side long --dir DIR --new new.csv [--sample 3000 --seed 699]
      эталон Python (`sigfeat_lib.level_features` / `fresh_prior_features` с `tf_min` строки) против new.csv, 10 столбцов, биты float64
  tk699_f12tf.py merge --side long --runroot /data/p42-tk699/run [--wins wa,wb,wc] [--apply]
      дельта из `<runroot>/prod-<win>-<side>/{f12.csv,eps-key.csv}` в `asm/<win>-<side>.parquet` и `data/<side>.parquet` (строки tf ≠ 60, 10 столбцов,
      float32 как в tk671_assemble); строки tf ≠ 60 без минуток монеты (TradFi, кэш удалён TK-686 / В-175) не пересчитываются: `meta_tk699_stale` = 1;
      без --apply — только счёт; с --apply — новые файлы `<файл>.tk699-new` рядом со старыми, на место их ставит
  tk699_f12tf.py swap --side long [--wins wa,wb,wc]
      старый файл → `<файл>.bak-tk699` (жёсткая ссылка), новый → на место; в момент, согласованный с читателями `data/*.parquet` (партии TK-674c читают файл на старте)
Свеча строки: источник `src` → tf (p30n* 30, p60n* и lx* 60, p240n* 240), как `rules_tf_min` в tk671_assemble.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PROD, ASM, DATA = "/data/p42-tk671/prod", "/data/p42-tk671/asm", "/data/p42-tk671/data"
SRC_TF = np.array([30, 30, 30, 60, 60, 60, 240, 240, 240, 60, 60, 60], dtype=np.int64)
COLS10 = ["age_h", "approach_dur_min", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "since_touch_prior_h",
          "touch_prior_none"]
CACHE = {"wa": "/data/p42-tk621/wa/cache", "wb": "/data/p42-tk621/wb/cache", "wc": "/data/p42-tk621/wc/cache"}
BAK = ".bak-tk699"
NEW = ".tk699-new"


def read_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, float_precision="round_trip", keep_default_na=True, na_values=[""], dtype={"sym": str, "ep_id": str, "side": str, "univ": str})


def bits(x: np.ndarray) -> np.ndarray:
    """float64 → int64 с единым NaN (равенство по битам; NaN равен NaN)."""
    v = np.asarray(x, dtype=np.float64).copy()
    v[np.isnan(v)] = np.nan
    return v.view(np.int64)


def cmd_prep(a):
    r = pd.read_parquet(f"{PROD}/lvl-{a.win}-{a.side}/rows.parquet")
    have = {f[:-4] for f in os.listdir(CACHE[a.win]) if f.endswith(".b42")}
    miss = ~r["sym"].isin(have)  # минуток монеты нет (TradFi: кэш и сутки сделок удалены) — f12 не посчитать
    n_miss = int(miss.sum())
    r = r[~miss]
    if a.coin_mod > 1:
        coins = sorted(r["sym"].unique())
        r = r[r["sym"].isin(set(coins[a.coin_rem::a.coin_mod]))]
    r = r.sort_values(["sym", "sig_ms", "level"], kind="stable").reset_index(drop=True)
    empty = r["members"].fillna("") == ""
    r["members"] = r["members"].where(~empty, r["first_bar"].astype("int64").astype(str) + ":" + r["level_px_e8"].astype("int64").astype(str))
    r["tf_min"] = SRC_TF[r["src"].to_numpy(dtype=np.int64)]
    if a.which == "tf":
        r = r[r["tf_min"] != 60].reset_index(drop=True)
    os.makedirs(a.out, exist_ok=True)
    r[["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "born", "end", "first_bar", "natr_e8", "members", "tf_min"]].to_csv(os.path.join(a.out, "sigsx.csv"), index=False)
    r[["row_id", "sym", "sig_ms", "level_px_e8", "src", "tf_min"]].to_csv(os.path.join(a.out, "eps-key.csv"), index=False)
    print(json.dumps(dict(rows=len(r), coins=int(r["sym"].nunique()), tf=r["tf_min"].value_counts().sort_index().to_dict(), rows_no_minutes=n_miss)))


def link(ek: pd.DataFrame, f12: pd.DataFrame) -> pd.DataFrame:
    """Строки f12.csv → строки eps-key по ключу (sym, sig_ms, lp_e8 = level_px_e8), как `tk671_assemble.rust_blocks`."""
    fk = pd.MultiIndex.from_arrays([f12["sym"], f12["sig_ms"], f12["lp_e8"]])
    ekm = pd.MultiIndex.from_arrays([ek["sym"], ek["sig_ms"], ek["level_px_e8"]])
    assert len(f12) == len(ek) and fk.is_unique and ekm.is_unique, "f12: число строк / дубли ключа (sym, sig_ms, lp_e8)"
    pos = ekm.get_indexer(fk)
    assert (pos >= 0).all(), "f12: ключ строки не найден среди eps-key"
    out = f12.copy()
    for c in ("row_id", "src", "tf_min"):
        out[c] = ek[c].to_numpy()[pos]
    return out.set_index("row_id", drop=False).sort_index()


def cmd_gate_bin(a):
    old, new = read_csv(a.old), read_csv(a.new)
    ek = pd.read_csv(os.path.join(a.dir, "eps-key.csv"), dtype={"sym": str})
    old, new = link(ek, old), link(ek, new)
    assert (old.index == new.index).all()
    rest = [c for c in new.columns if c not in COLS10 and c not in ("src", "tf_min", "row_id")]
    aff = new["tf_min"].to_numpy() != 60
    rep = dict(rows=int(len(new)), rows_tf60=int((~aff).sum()), rows_tf_ne60=int(aff.sum()), other_cols=len(rest), other_cols_diff={}, cols10={})
    for c in rest:
        if new[c].dtype == object:
            d = int((old[c].to_numpy() != new[c].to_numpy()).sum())
        else:
            d = int((bits(old[c].to_numpy()) != bits(new[c].to_numpy())).sum())
        if d:
            rep["other_cols_diff"][c] = d
    for c in COLS10:
        ne = bits(old[c].to_numpy()) != bits(new[c].to_numpy())
        rep["cols10"][c] = dict(diff_tf60=int(ne[~aff].sum()), diff_tf_ne60=int(ne[aff].sum()))
    rep["ok"] = (not rep["other_cols_diff"]) and all(v["diff_tf60"] == 0 for v in rep["cols10"].values())
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 1


def parse_members(s: str):
    return [(int(x.split(":")[0]), int(x.split(":")[1])) for x in s.split("|")]


def cmd_gate_py(a):
    import sigfeat_lib as L
    new = read_csv(a.new)
    ek = pd.read_csv(os.path.join(a.dir, "eps-key.csv"), dtype={"sym": str})
    new = link(ek, new)
    r = pd.read_parquet(f"{PROD}/lvl-{a.win}-{a.side}/rows.parquet").set_index("row_id")
    s = 1 if a.side == "long" else -1
    rng = np.random.default_rng(a.seed)
    pick = []
    for tf in (30, 60, 240):
        idx = new.index[new["tf_min"].to_numpy() == tf].to_numpy()
        if len(idx):
            pick += list(rng.choice(idx, size=min(a.sample, len(idx)), replace=False))
    pick.sort(key=lambda i: (r.at[i, "sym"], r.at[i, "sig_ms"]))
    cache, rep = {}, {c: dict(rows=0, diff=0) for c in COLS10}
    per_tf = {30: 0, 60: 0, 240: 0}
    bad = []
    for i in pick:
        row = r.loc[i]
        sym = row["sym"]
        if sym not in cache:
            cache = {sym: L.load_b42(f"{CACHE[a.win]}/{sym}.b42")}
        m = cache[sym]
        t = int(row["sig_ms"]) // 60_000
        R = s * int(row["level_px_e8"])
        mem = row["members"] if isinstance(row["members"], str) and row["members"] else f"{int(row['first_bar'])}:{int(row['level_px_e8'])}"
        members = parse_members(mem)
        tf = int(new.at[i, "tf_min"])
        lf = L.level_features(m, s, t, R, members, int(row["born"]), int(row["first_bar"]), int(row["dist_e8"]), int(row["natr_e8"]), tf)
        lf.update(L.fresh_prior_features(m, s, t, R, [x[0] for x in members], int(row["first_bar"]), tf))
        per_tf[tf] += 1
        for c in COLS10:
            rep[c]["rows"] += 1
            if bits(np.array([lf[c]]))[0] != bits(np.array([new.at[i, c]]))[0]:
                rep[c]["diff"] += 1
                if len(bad) < 10:
                    bad.append(dict(row_id=int(i), tf=tf, col=c, py=float(lf[c]), rust=float(new.at[i, c])))
    out = dict(sample_by_tf=per_tf, cols=rep, first_diffs=bad, ok=all(v["diff"] == 0 for v in rep.values()))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["ok"] else 1


def f32(x: np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype=np.float64).astype(np.float32)


def merge_file(path: str, news: dict, apply: bool, rep: dict, tag: str):
    """Заменить 10 столбцов `path` на строках `news[win]` (row_id; ключ строки — (meta_win, row_id)); остальные строки и столбцы — как были.
    `meta_tk699_stale` = 1 у строк tf ≠ 60, которых нет в дельте (минуток монеты нет)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    t = pq.read_table(path)
    rid = t["row_id"].to_numpy()
    win_col = t["meta_win"].to_pandas().to_numpy()
    tf = SRC_TF[t["meta_src"].to_numpy().astype(np.int64)]
    n = len(rid)
    old = {c: t[c].to_numpy().astype(np.float32) for c in COLS10}
    val = {c: v.copy() for c, v in old.items()}
    done = np.zeros(n, dtype=bool)
    for win, new in news.items():
        sel = np.where(win_col == win)[0]
        hit = pd.Series(sel, index=rid[sel]).reindex(new.index)
        assert hit.notna().all(), f"{tag} {win}: строки row_id не найдены"
        pidx = hit.to_numpy(dtype=np.int64)
        assert (tf[pidx] == new["tf_min"].to_numpy()).all(), f"{tag} {win}: источник строки ≠ tf_min"
        assert not done[pidx].any()
        for c in COLS10:
            val[c][pidx] = f32(new[c].to_numpy())
        done[pidx] = True
    stale = (tf != 60) & ~done
    rep[tag] = dict(rows_file=n, rows_tf60=int((tf == 60).sum()), rows_tf_ne60=int((tf != 60).sum()), rows_recomputed=int(done.sum()),
                    rows_stale_no_minutes=int(stale.sum()), cols={})
    for c in COLS10:
        ch = ~((old[c] == val[c]) | (np.isnan(old[c]) & np.isnan(val[c])))
        assert not ch[~done].any()
        assert (old[c][tf == 60].view(np.int32) == val[c][tf == 60].view(np.int32)).all()  # строки tf = 60 — те же биты
        rep[tag]["cols"][c] = dict(changed=int(ch.sum()), same=int(done.sum() - ch.sum()), nan_old=int(np.isnan(old[c][done]).sum()), nan_new=int(np.isnan(val[c][done]).sum()))
    if not apply:
        return
    out = t
    for c in COLS10:
        j = out.schema.get_field_index(c)
        out = out.set_column(j, out.schema.field(j), pa.array(val[c], type=pa.float32()))
    mark = pa.array(stale.astype(np.int8), type=pa.int8())
    j = out.schema.get_field_index("meta_tk699_stale")
    out = out.append_column("meta_tk699_stale", mark) if j < 0 else out.set_column(j, "meta_tk699_stale", mark)
    tmp = path + NEW
    pq.write_table(out, tmp, compression="zstd", row_group_size=max(out.num_rows, 1))


def swap_file(path: str):
    """Подмена: старый файл → `<файл>.bak-tk699` (жёсткая ссылка, место не занимает), новый → на место (одна атомарная замена)."""
    assert os.path.exists(path + NEW), path + NEW
    bak = path + BAK
    if os.path.exists(bak):
        raise SystemExit(f"уже есть {bak}")
    os.link(path, bak)
    os.replace(path + NEW, path)


def cmd_swap(a):
    for w in a.wins.split(","):
        swap_file(f"{ASM}/{w}-{a.side}.parquet")
    swap_file(f"{DATA}/{a.side}.parquet")
    print("swap ok", a.side, a.wins)


def cmd_merge(a):
    wins = a.wins.split(",")
    news = {}
    for w in wins:
        d = f"{a.runroot}/prod-{w}-{a.side}"
        new = link(pd.read_csv(f"{d}/eps-key.csv", dtype={"sym": str}), read_csv(f"{d}/f12.csv"))
        assert (new["tf_min"].to_numpy() != 60).all(), "в дельте только строки tf ≠ 60"
        news[w] = new
    rep = {}
    for w in wins:
        merge_file(f"{ASM}/{w}-{a.side}.parquet", {w: news[w]}, a.apply, rep, f"asm-{w}")
    merge_file(f"{DATA}/{a.side}.parquet", news, a.apply, rep, "data")
    print(json.dumps(dict(side=a.side, apply=a.apply, **rep), ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("prep")
    p.add_argument("--win", required=True, choices=("wa", "wb", "wc"))
    p.add_argument("--side", required=True, choices=("long", "short"))
    p.add_argument("--out", required=True)
    p.add_argument("--which", default="tf", choices=("tf", "all"))
    p.add_argument("--coin-mod", type=int, default=1)
    p.add_argument("--coin-rem", type=int, default=0)
    p.set_defaults(fn=cmd_prep)
    p = sp.add_parser("gate-bin")
    p.add_argument("--dir", required=True)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.set_defaults(fn=cmd_gate_bin)
    p = sp.add_parser("gate-py")
    p.add_argument("--win", required=True, choices=("wa", "wb", "wc"))
    p.add_argument("--side", required=True, choices=("long", "short"))
    p.add_argument("--dir", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--sample", type=int, default=3000)
    p.add_argument("--seed", type=int, default=699)
    p.set_defaults(fn=cmd_gate_py)
    p = sp.add_parser("merge")
    p.add_argument("--side", required=True, choices=("long", "short"))
    p.add_argument("--runroot", default="/data/p42-tk699/run")
    p.add_argument("--wins", default="wa,wb,wc")
    p.add_argument("--apply", action="store_true", help="записать новые файлы <файл>.tk699-new рядом со старыми (подмена — swap)")
    p.set_defaults(fn=cmd_merge)
    p = sp.add_parser("swap")
    p.add_argument("--side", required=True, choices=("long", "short"))
    p.add_argument("--wins", default="wa,wb,wc")
    p.set_defaults(fn=cmd_swap)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
