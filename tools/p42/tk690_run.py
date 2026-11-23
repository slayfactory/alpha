#!/usr/bin/env python3
"""П-42 TK-690 (к шагу 3 TK-674, PS-986 п. 6): таблицы фильтров по потокам кандидатов nf / ev / ea — строки кандидатов 15 потоков × 3 окна c539 (шорт) для 28 столбцов-фильтров. P&L не читается.
  python3 tk690_run.py streams   — потоки из `res/gkey674bsx-S.json` TK-674: ключи прохода сигналов клеток с `no_key` > 0 → res/streams690.json
  python3 tk690_run.py dump      — движок: дамп сигналов потоков (`P42_SIGSX=14:73`, вариант на поток) → dump/<окно>/<поток>/short-sigsx.csv
  python3 tk690_run.py rows      — объединение потоков без дублей → prod/lvl-<окно>-short/rows.parquet (раскладка TK-671; ключ строки (sym, sig_ms, level_px_e8))
  python3 tk690_run.py feat      — признаки теми же скриптами TK-671 / TK-680 / TK-681: Rust (`tk671_feat.sh`), FAST, семейства coin / bd / hist, ok10_d3 → fam/, hist35/
  python3 tk690_run.py avail     — строки, у которых кластер монеты содержит соседа без кэша окна (TradFi удалён, В-175): cc_* считать нечем → fam/avail/ (вызывается из feat)
  python3 tk690_run.py asm       — сборка (`tk671_assemble.merge_families`) → data/short.parquet (раскладка таблицы TK-671: win, row_id, sym, meta_*, признаки float32), legacy.parquet (float64)
  python3 tk690_run.py tables    — составные столбцы фильтров (`tk674_run._pass`, столбцы — прежние `ftabcols674x-S.json`) → ftab674xs-all/ (все строки потоков) и ftab674xs/ (строки прежней `ftab674x` дословно + новые)
  python3 tk690_run.py gate      — ворота: равенство прежней таблице на общих ключах (байт в байт, все составные столбцы), дамп ≡ счётчики cands / no_key клеток TK-674, no_key = 0 по ключам дампа
  python3 tk690_run.py check     — движок: по клетке-фильтру на поток с новой таблицей → `filter_no_key` = 0 и cands те же (G-key на движке)
  python3 tk690_run.py slice <N> — проверка цепочки признаков на срезе строк существующей таблицы TK-671 (TK690_ROOT = каталог среза): признаки среза = столбцам таблицы TK-671
Среда — как у шага 3 TK-674 (TK674_UNI=c539, TK674_REBASE=1, TK674_X680=1, TK674_X680_ROOTS, TK674_BIN); TK690_ROOT — каталог результата (по умолчанию /data/p42-tk690), TK690_SRC — каталог TK-674 (только чтение)."""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = os.environ.get("TK690_ROOT", "/data/p42-tk690")
SRC = os.environ.get("TK690_SRC", "/data/p42-tk674c")        # каталог TK-674: прежние таблицы / карты — только чтение
P671 = "/data/p42-tk671"
PY = "/data/tk592/venv/bin/python"                           # как `tk671_pyfam.sh`
C671 = P671 + "/code/p"                                      # боевая копия скриптов TK-671 (prep, семейства)
FAST_BIN = "/data/p42-tk681/bin/alpha-feat-tk681"            # как `tk681_chain.sh`
STORE = "/data/p42-tk599/tick-store"
BD_ZIP = "/data/p42-book/zip"                                # архив bookDepth: вынесен на бокс (TK-675), возвращён rsync с бокса; на боксе 17 069 файлов / 8 370 571 232 Б
BD_FILES = 17069
CC_COLS = ("cc_15m", "cc_1h", "cc_4h", "cc_24h", "cc_7d")    # столбцы, зависящие от соседей по кластеру: у соседа-TradFi кэша нет (В-175)
SIDE, SN = "S", "short"
WINS = ("wa", "wb", "wc")
SIGSX_DAYS = "14:73"                                         # сутки торговой части сетки, как у дампов баз (`tk625_run.cmd_eng`)
KEYCOLS = ["sym", "sig_ms", "level_px_e8"]
ATTR = ["level", "dist_e8", "natr_e8", "born", "end", "first_bar", "members"]     # свойства строки: у одного ключа в разных потоках обязаны совпасть
PYFAMS = ("coin", "bd", "hist")                              # Python-семейства TK-671, из которых берутся столбцы-фильтры
RUST_ORDER = ("f12", "flow_deriv", "tcount", "tsize", "tox")
LEGACY = ("tpm60", "cons60", "zv6h", "mv5", "n24", "m24", "pierce_ep", "spread_tau")     # столбцы таблиц TK-654 / TK-638: в `feat_tabs` — double, не float32 таблицы TK-671
say = lambda *a: print(*a, flush=True)


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


class GuardError(SystemExit):
    pass


# ---- потоки ---------------------------------------------------------------------------------------------------------------------------
def streams_from_bad(bad, sigkey):
    """Потоки по клеткам с no_key > 0: ключ прохода сигналов → {vid, patch (только ключи прохода), cells, cands по окнам}. Порядок и vid — по отсортированному ключу."""
    seen = {}
    for b in bad:
        sk = sigkey(b["patch"])
        cands = {w: int(fc["cands"]) for w, fc in b["fc"].items()}
        no_key = {w: int(fc["no_key"]) for w, fc in b["fc"].items()}
        s = seen.setdefault(sk, dict(sigkey=[list(x) for x in sk], patch={k: b["patch"][k] for k, _ in sk}, cells=0, cands=cands, no_key=no_key))
        if s["cands"] != cands or s["no_key"] != no_key:
            raise GuardError("поток %s: счётчики cands / no_key у клеток одного потока различаются (%s против %s)" % (sk, s["cands"], cands))
        s["cells"] += 1
    out = []
    for i, sk in enumerate(sorted(seen)):
        out.append(dict(vid="n%02d" % i, **seen[sk]))
    return out


def cmd_streams(a):
    import tk674_axes as ax
    bad = json.load(open(os.path.join(SRC, "res", "gkey674bsx-S.json"), encoding="utf-8"))["bad"]
    ss = streams_from_bad(bad, ax.sigkey)
    if a.expect and len(ss) != a.expect:
        raise GuardError("потоков %d, ожидалось %d" % (len(ss), a.expect))
    json.dump(ss, open(q("res", "streams690.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("потоков %d, клеток %d; cands по окнам %s; no_key по окнам %s" % (
        len(ss), sum(s["cells"] for s in ss), {w: sum(s["cands"][w] for s in ss) for w in WINS}, {w: sum(s["no_key"][w] for s in ss) for w in WINS}))


def load_streams():
    return json.load(open(os.path.join(ROOT, "res", "streams690.json"), encoding="utf-8"))


# ---- движок: дамп потоков -------------------------------------------------------------------------------------------------------------
def cmd_dump(a):
    import tk674_run as run
    st, r5, _, _ = run.t._srv()
    run.engine_opts(r5)
    sha = run._bin()
    if run.bin_md5() not in json.load(open(os.path.join(SRC, "res", "eqbins674.json"), encoding="utf-8")):
        raise GuardError("бинарь %s не в списке равных бинарей TK-674 (res/eqbins674.json)" % run.BIN)
    say("G-bin: %s sha256 %s" % (run.BIN, sha))
    u = r5.Uni(run.UNI)
    ss = load_streams()
    for w in st.TRADE_WINDOWS:
        out = os.path.join(ROOT, "dump", w.name)
        if os.path.exists(os.path.join(out, "DONE")):
            continue
        variants = [(s["vid"], run.t.variant_line(r5, u, SIDE, s["patch"])) for s in ss]
        r5.tick(u, w, variants, out, run.one_side_env(SIDE) | {"P42_SIGSX": SIGSX_DAYS}, q("log", "dump-%s.log" % w.name))
        open(os.path.join(out, "DONE"), "w").close()
        say("dump %s: %d потоков готово" % (w.name, len(variants)))


# ---- строки ---------------------------------------------------------------------------------------------------------------------------
def build_rows(per_stream, win, t0_ms):
    """per_stream {vid: DataFrame дампа sigsx} → строки окна (одна на ключ (sym, sig_ms, level_px_e8), порядок как `tk671_lvl`), row_id — по порядку.
    Стоп (GuardError): у одного ключа в разных потоках различаются свойства строки; одна монета и момент с двумя уровнями."""
    allr = pd.concat([d.assign(vid=v) for v, d in per_stream.items()], ignore_index=True)
    g = allr.groupby(KEYCOLS, sort=False)[ATTR].nunique(dropna=False)
    bad = g[(g > 1).any(axis=1)]
    if len(bad):
        raise GuardError("%s: у %d ключей свойства строки различаются между потоками (первый: %s)" % (win, len(bad), bad.index[0]))
    rows = allr.drop_duplicates(KEYCOLS).drop(columns="vid")
    if rows.duplicated(["sym", "sig_ms"]).any():
        raise GuardError("%s: у %d ключей (sym, sig_ms) больше одного уровня" % (win, int(rows.duplicated(["sym", "sig_ms"], keep=False).sum())))
    rows = rows.sort_values(["sig_ms", "dist_e8", "sym", "level"], kind="stable").reset_index(drop=True)
    rows.insert(0, "row_id", np.arange(len(rows), dtype=np.int64))
    rows["abs_ms"] = rows["sig_ms"].astype(np.int64) + t0_ms
    rows["win"], rows["side"] = win, SN
    return rows


def dump_paths(win, ss):
    return {s["vid"]: os.path.join(ROOT, "dump", win, s["vid"], "%s-sigsx.csv" % SN) for s in ss}


def read_dumps(win, ss):
    import tk671_lvl as lvl
    return {v: lvl.read_sigsx(p) for v, p in dump_paths(win, ss).items()}


def rows_path(win, root=None):
    return os.path.join(root or ROOT, "prod", "lvl-%s-%s" % (win, SN), "rows.parquet")


def cmd_rows(a):
    import tk604_stats as st
    ss = load_streams()
    for w in st.TRADE_WINDOWS:
        per = read_dumps(w.name, ss)
        rows = build_rows(per, w.name, w.t0_ms)
        os.makedirs(os.path.dirname(rows_path(w.name)), exist_ok=True)
        rows.to_parquet(rows_path(w.name), index=False)
        say("rows %s: потоков %d, строк в дампах %d, уникальных ключей %d" % (w.name, len(per), sum(len(d) for d in per.values()), len(rows)))


# ---- недоступные значения (охрана «пусто проходит») -----------------------------------------------------------------------------------
def cc_unavail(coins, lab_by_mon, mon, sym, have):
    """Строки, у которых кластер монеты (метки месяца сигнала) содержит соседа без кэша окна: cc_* считать нечем (у такой строки в таблице TK-671 значение было — сосед-TradFi, кэш удалён по В-175).
    coins — вселенная в порядке меток; lab_by_mon {месяц: метки}; mon, sym — по строкам; have — множество монет с кэшем окна. Возвращает bool по строкам."""
    ix = {c: i for i, c in enumerate(coins)}
    out = np.zeros(len(sym), dtype=bool)
    memo = {}
    for r in range(len(sym)):
        k = (int(mon[r]), sym[r])
        if k not in memo:
            lab = lab_by_mon.get(k[0])
            i = ix.get(k[1], -1)
            if lab is None or i < 0 or lab[i] < 0:
                memo[k] = False
            else:
                mem = np.flatnonzero(lab == lab[i])
                memo[k] = any(coins[j] not in have for j in mem if j != i)
        out[r] = memo[k]
    return out


def cmd_avail(a):
    """fam/avail/<окно>-short.parquet: row_id, cc_unavail — строки, у которых кластер содержит соседа без кэша окна."""
    import tk671_coin as c71
    coins = c71.universe_coins()
    for w in WINS:
        rows = pd.read_parquet(rows_path(w))
        have = {f[:-4] for f in os.listdir(os.path.join(c71.CACHE_ROOT, w, "cache")) if f.endswith(".b42")}
        mon = c71.month_of_vec(rows["abs_ms"].to_numpy(np.int64))
        labels = c71.month_labels(np.unique(mon), coins, cache_root=c71.CACHE_ROOT, labels_dir=c71.LABELS_DIR)
        u = cc_unavail(coins, labels, mon, rows["sym"].astype(str).to_numpy(), have)
        pd.DataFrame(dict(row_id=rows["row_id"].to_numpy(), cc_unavail=u)).to_parquet(q("fam", "avail", "%s-%s.parquet" % (w, SN)), index=False)
        say("avail %s: строк %d, cc недоступно (сосед без кэша) %d" % (w, len(rows), int(u.sum())))


def bd_archive_guard():
    """Архив bookDepth полон: `bd_*` без архива молча не фильтруют (NaN проходит) — стоп, если файлов меньше, чем было на боксе."""
    n = sum(len(fs) for _, _, fs in os.walk(BD_ZIP))
    if n < BD_FILES:
        raise GuardError("архив bookDepth %s: файлов %d < %d (вернуть: rsync -aH box:offload-srv-2026-11-22/p42-book/zip/ %s/)" % (BD_ZIP, n, BD_FILES, BD_ZIP))
    return n


def parse_feats(ckey):
    """Имена признаков фильтра клетки по смыслу `F=...` в её ключе (токены через `&`)."""
    out = []
    for tok in ckey.split("F=", 1)[1].split("&"):
        for op in ("<=", ">=", "=="):
            if op in tok:
                out.append(tok.split(op)[0])
                break
    return out


def feat_nan(tab, f):
    """Строки, где признак `f` фильтра не определён: такая строка клеткой пропускается (`tk674_run._pass`: «пусто проходит»)."""
    if f == "sharp":
        return ~(np.isfinite(tab["cons60"]) & np.isfinite(tab["mv5"]))
    if f == "n24":
        return tab["m24"].astype(bool) | ~np.isfinite(tab["n24"])
    return ~np.isfinite(tab[f])


def cell_counts(tab, feats, idx, new):
    """Охрана клетки по её кандидатам (`idx` — строки таблицы, `new` — bool: строки без места в прежней таблице): кандидатов; строк с NaN в признаках фильтра (всего / новых);
    новых строк, где NaN — не «не определено», а «считать нечем» (`cc_unavail`): такую строку фильтр пропустил бы молча → клетка недействительна."""
    nan = np.zeros(len(idx), dtype=bool)
    una = np.zeros(len(idx), dtype=bool)
    for f in feats:
        nan |= feat_nan(tab, f)[idx]
        if f in CC_COLS:
            una |= tab["cc_unavail"][idx].astype(bool)
    nw = new[idx]
    return dict(cands=int(len(idx)), nan=int(nan.sum()), nan_new=int((nan & nw).sum()), unavail_new=int((una & nw).sum()))


# ---- признаки -------------------------------------------------------------------------------------------------------------------------
def sh(cmd, env, log):
    e = dict(os.environ)
    e.update(env)
    with open(log, "w", encoding="utf-8") as f:
        rc = subprocess.call(cmd, env=e, stdout=f, stderr=subprocess.STDOUT)
    if rc:
        raise GuardError("%s: код %d, журнал %s" % (cmd[0:3], rc, log))


def cmd_feat(a):
    import tk604_stats as st
    import tk671_hist as h71
    import tk680_hist as h80
    import tk625_feat as f5
    if any(not os.path.exists(os.path.join(ROOT, "fam", "bd", "%s-%s.parquet" % (w.name, SN))) for w in st.TRADE_WINDOWS):
        say("архив bookDepth: файлов %d" % bd_archive_guard())
    for w in st.TRADE_WINDOWS:
        rp = rows_path(w.name)
        rows = pd.read_parquet(rp)
        rust = os.path.join(ROOT, "fam", "rust", "%s-%s" % (w.name, SN))
        if not os.path.exists(os.path.join(rust, "tox.csv")):
            sh(["bash", os.path.join(HERE, "tk671_feat.sh"), w.name, SN, str(a.threads)], {"TK671_ROWS": rp, "TK671_RUST_OUT": os.path.join(ROOT, "fam", "rust")}, q("log", "rust-%s.log" % w.name))
            say("rust %s готово: %s" % (w.name, open(os.path.join(rust, "steps.log"), encoding="utf-8").read().strip().replace("\n", " | ")[-300:]))
        if not os.path.exists(os.path.join(rust, "fast.csv")):
            sh([FAST_BIN, "fast", "--eps", os.path.join(rust, "eps-tox.csv"), "--out", os.path.join(rust, "fast.csv"), "--base-date", w.t0, "--trades", "/data/p42-tk621/%s/aggtrades" % w.name,
                "--store", STORE, "--threads", str(a.threads)], {}, q("log", "fast-%s.log" % w.name))
        for fam in PYFAMS:
            out = q("fam", fam, "%s-%s.parquet" % (w.name, SN))
            if not os.path.exists(out):
                sh([PY, os.path.join(C671, "tk671_%s.py" % fam), "--rows", rp, "--win", w.name, "--side", SN, "--out", out, "--procs", str(a.threads)], {"PYTHONPATH": C671}, q("log", "%s-%s.log" % (fam, w.name)))
        ex = q("hist35", "%s-%s.parquet" % (w.name, SN))
        if not os.path.exists(ex):                                                       # ok10_d3 / d5: как `tk680_hist.main`, на строках этого набора
            _, hist = h71.load()
            got = h80.compute(hist, SN, rows["sym"].astype(str).to_numpy(dtype=object), rows["abs_ms"].to_numpy(np.int64), h80.NEW_DS + f5.DS)
            res = rows[["win", "row_id"]].copy()
            for c in [p + f5.col_name(h80.M, d) for p in ("", "x") for d in h80.NEW_DS]:
                res[c] = got[c]
            res.to_parquet(ex)
        say("feat %s готово" % w.name)
    cmd_avail(a)


def cmd_asm(a):
    import tk671_assemble as asm
    asm.RUST = os.path.join(ROOT, "fam", "rust")
    parts, legacy, hist = [], [], []
    for w in WINS:
        rows = pd.read_parquet(rows_path(w)).set_index("row_id", drop=False)
        fams = asm.rust_blocks(w, SN, rows)
        for fam in PYFAMS:
            f = pd.read_parquet(os.path.join(ROOT, "fam", fam, "%s-%s.parquet" % (w, SN)))
            fams[fam] = f.set_index("row_id") if "row_id" in f.columns else f
        order = [n for n in list(RUST_ORDER) + sorted(PYFAMS) if n in fams]
        feat, cov = asm.merge_families(fams, order, rows.index)
        meta = pd.DataFrame(dict(win=w, row_id=rows["row_id"].to_numpy(), sym=rows["sym"].astype(str).to_numpy(), meta_sig_ms=rows["sig_ms"].to_numpy(np.int64),
                                 meta_abs_ms=rows["abs_ms"].to_numpy(np.int64), meta_dist_e8=rows["dist_e8"].to_numpy(np.int64), meta_base_pass=np.int64(1)))
        parts.append(pd.concat([meta, feat.reset_index(drop=True)], axis=1))
        lg = pd.DataFrame(dict(win=w, row_id=rows["row_id"].to_numpy()))
        for c in LEGACY:
            for n in order:
                if c in fams[n].columns:
                    lg[c] = fams[n][c].reindex(rows.index).to_numpy(np.float64)
                    break
            else:
                raise GuardError("столбец %s не найден ни в одном блоке" % c)
        legacy.append(lg)
        say("asm %s: строк %d, признаков %d, дублей %d, переименовано %d" % (w, len(rows), feat.shape[1], len(cov["dups"]), len(cov["renamed"])))
    d = pd.concat(parts, ignore_index=True)
    d["sym"] = d["sym"].astype("category")
    d.to_parquet(q("data", "%s.parquet" % SN), compression="zstd", index=False)
    pd.concat(legacy, ignore_index=True).to_parquet(q("data", "legacy-%s.parquet" % SN), index=False)
    pd.concat([pd.read_parquet(os.path.join(ROOT, "hist35", "%s-%s.parquet" % (w, SN))) for w in WINS], ignore_index=True).to_parquet(q("hist35", "%s.parquet" % SN), index=False)


# ---- таблицы --------------------------------------------------------------------------------------------------------------------------
def read_lines(path):
    """(заголовок, {(sym, sig_ms): строка}, порядок ключей) CSV таблицы фильтров."""
    with open(path, encoding="utf-8", newline="") as f:
        head = f.readline().rstrip("\n")
        lines = {}
        order = []
        for ln in f:
            ln = ln.rstrip("\n")
            s, m, _ = ln.split(",", 2)
            k = (s, int(m))
            lines[k] = ln
            order.append(k)
    return head, lines, order


def merge_tables(old_path, new_path, out_path):
    """Прежние строки дословно + строки новой таблицы, которых в прежней нет; возвращает (общих ключей, число добавленных, расхождений на общих)."""
    h0, old, ord0 = read_lines(old_path)
    h1, new, ord1 = read_lines(new_path)
    if h0 != h1:
        raise GuardError("заголовки таблиц различаются: %s" % os.path.basename(old_path))
    common = [k for k in ord1 if k in old]
    diff = [k for k in common if old[k] != new[k]]
    add = [k for k in ord1 if k not in old]
    with open(old_path, "rb") as f:
        body = f.read()
    with open(out_path, "wb") as f:
        f.write(body if body.endswith(b"\n") else body + b"\n")
        f.write("".join(new[k] + "\n" for k in add).encode("utf-8"))
    return len(common), len(add), diff


def stream_tabs(win_names):
    """tabs для `write_ftab`: legacy-столбцы double (как таблицы TK-654 / TK-638), остальные — через `tk680_singles.add_columns` из таблицы потоков."""
    import tk680_axes as a80
    d = pd.read_parquet(os.path.join(ROOT, "data", "%s.parquet" % SN), columns=["win", "row_id", "sym", "meta_sig_ms"])
    lg = pd.read_parquet(os.path.join(ROOT, "data", "legacy-%s.parquet" % SN))
    tabs = {}
    for w in win_names:
        sel = (d["win"] == w).to_numpy()
        t = dict(sym=d["sym"].astype(str).to_numpy(dtype=object)[sel], ms=d["meta_sig_ms"].to_numpy(np.int64)[sel])
        l = lg[(lg["win"] == w).to_numpy()]
        if not np.array_equal(l["row_id"].to_numpy(), d["row_id"].to_numpy()[sel]):
            raise GuardError("legacy.parquet и data: row_id не совпадают (%s)" % w)
        for c in LEGACY:
            t[c] = l[c].to_numpy(np.float64)
        av = pd.read_parquet(os.path.join(ROOT, "fam", "avail", "%s-%s.parquet" % (w, SN)))
        if not np.array_equal(av["row_id"].to_numpy(), d["row_id"].to_numpy()[sel]):
            raise GuardError("fam/avail и data: row_id не совпадают (%s)" % w)
        t["cc_unavail"] = av["cc_unavail"].to_numpy(bool)
        tabs[w] = t
    a80.PARQUET = os.path.join(ROOT, "data", "%s.parquet")
    a80.HIST_EXTRA = os.path.join(ROOT, "hist35", "%s.parquet")
    a80.T681_FASTDIR = os.path.join(ROOT, "fam", "rust", "%s-%s")
    return tabs


def write_nan_report(tabs):
    """res/nan690.json: охрана 1 410 клеток по их кандидатам. Клетка недействительна, если среди новых (без места в прежней таблице) кандидатов есть строка с `cc_unavail` при фильтре по cc_*:
    значение посчитать нечем, а фильтр «пусто проходит» пропустил бы её молча. Счётчики NaN (всего / новых) — для сведения: «не определено» по определению признака (нет кластера, нет архива на сутки) — не недоступность."""
    import tk674_axes as ax
    ss = load_streams()
    cells = json.load(open(os.path.join(SRC, "res", "gkey674bsx-S.json"), encoding="utf-8"))["bad"]
    by_sk = {json.dumps(s["sigkey"]): s for s in ss}
    cand, new = {}, {}
    for w in WINS:
        t = tabs[w]
        ix = {(sy, int(m)): i for i, (sy, m) in enumerate(zip(t["sym"], t["ms"]))}
        _, old, _ = read_lines(os.path.join(SRC, "ftab674x", "%s-%s.csv" % (w, SIDE)))
        new[w] = np.array([(sy, int(m)) not in old for sy, m in zip(t["sym"], t["ms"])])
        for v, d in read_dumps(w, ss).items():
            cand[(v, w)] = np.array([ix[(str(sy), int(m))] for sy, m in zip(d["sym"], d["sig_ms"])], dtype=np.int64)
    out = []
    for b in cells:
        s = by_sk[json.dumps([list(x) for x in ax.sigkey(b["patch"])])]
        feats = parse_feats(b["ckey"])
        out.append(dict(cid=b["cid"], vid=s["vid"], feats=feats, win={w: cell_counts(tabs[w], feats, cand[(s["vid"], w)], new[w]) for w in WINS}))
    inv = [c for c in out if any(r["unavail_new"] for r in c["win"].values())]
    summ = dict(cells=len(out), with_cc=sum(1 for c in out if any(f in CC_COLS for f in c["feats"])), with_bd=sum(1 for c in out if any(f.startswith("bd_") for f in c["feats"])),
                invalid_cc=len(inv), invalid_bd=0, counted=len(out) - len(inv), bd_archive_files=bd_archive_guard(),
                nan_cells_any=sum(1 for c in out if any(r["nan"] for r in c["win"].values())), nan_cells_new=sum(1 for c in out if any(r["nan_new"] for r in c["win"].values())),
                unavail_rows_new=sum(int(tabs[w]["cc_unavail"][new[w]].sum()) for w in WINS))
    json.dump(dict(summary=summ, invalid=[c["cid"] for c in inv], cells=out), open(q("res", "nan690.json"), "w", encoding="utf-8"), ensure_ascii=False)
    say("охрана: клеток %d, с cc_* %d, с bd_* %d; недействительных по cc_* %d, по bd_* %d, посчитано %d; с NaN у кандидатов %d (из них новых строк %d); новых строк без cc (сосед без кэша) %d" % (
        summ["cells"], summ["with_cc"], summ["with_bd"], summ["invalid_cc"], summ["invalid_bd"], summ["counted"], summ["nan_cells_any"], summ["nan_cells_new"], summ["unavail_rows_new"]))
    return summ


def cmd_tables(a):
    import tk674_run as run
    import tk674_x680 as x6
    import tk680_singles as s80
    st, _, _, _ = run.t._srv()
    tabs = stream_tabs([w.name for w in st.TRADE_WINDOWS])
    s80.add_columns(SIDE, st, tabs, **x6.column_specs(SIDE))
    cols = json.load(open(os.path.join(SRC, "res", "ftabcols674x-S.json"), encoding="utf-8"))
    out_all = os.path.join(ROOT, "ftab674xs-all")
    os.makedirs(out_all, exist_ok=True)
    run.write_ftab(SIDE, st, tabs, None, cols=cols, out=out_all)
    for w in st.TRADE_WINDOWS:
        os.makedirs(os.path.join(ROOT, "ftab674xs"), exist_ok=True)
        n_common, n_add, diff = merge_tables(os.path.join(SRC, "ftab674x", "%s-%s.csv" % (w.name, SIDE)), os.path.join(out_all, "%s-%s.csv" % (w.name, SIDE)),
                                             os.path.join(ROOT, "ftab674xs", "%s-%s.csv" % (w.name, SIDE)))
        say("tables %s: общих ключей %d (строк расходится %d), добавлено %d" % (w.name, n_common, len(diff), n_add))
    write_nan_report(tabs)


# ---- ворота ---------------------------------------------------------------------------------------------------------------------------
def cmd_gate(a):
    ss = load_streams()
    res, bad = {}, []
    for w in WINS:
        per = read_dumps(w, ss)
        _, old, _ = read_lines(os.path.join(SRC, "ftab674x", "%s-%s.csv" % (w, SIDE)))
        _, new, _ = read_lines(os.path.join(ROOT, "ftab674xs", "%s-%s.csv" % (w, SIDE)))
        _, new_all, _ = read_lines(os.path.join(ROOT, "ftab674xs-all", "%s-%s.csv" % (w, SIDE)))
        for s in ss:
            keys = set(zip(per[s["vid"]]["sym"].astype(str), per[s["vid"]]["sig_ms"].astype(int)))
            n_dup = len(per[s["vid"]]) - len(keys)
            no_old = len(keys - set(old))
            no_new = len(keys - set(new))
            common = [k for k in keys if k in old]
            diff = [k for k in common if old[k] != new_all[k]]
            r = dict(cands=len(keys), cands_meta=s["cands"][w], no_key_old=no_old, no_key_meta=s["no_key"][w], no_key_new=no_new, common=len(common), diff=len(diff), dup_keys=n_dup)
            res.setdefault(s["vid"], {})[w] = r
            if len(keys) != s["cands"][w] or n_dup:
                bad.append("%s %s: ключей дампа %d (дублей %d) ≠ cands клеток TK-674 %d" % (s["vid"], w, len(keys), n_dup, s["cands"][w]))
            if no_old != s["no_key"][w]:
                bad.append("%s %s: ключей дампа без строки прежней таблицы %d ≠ no_key клеток TK-674 %d" % (s["vid"], w, no_old, s["no_key"][w]))
            if no_new:
                bad.append("%s %s: no_key после слияния %d ≠ 0" % (s["vid"], w, no_new))
            if diff:
                bad.append("%s %s: на %d из %d общих ключей строка отличается от прежней таблицы" % (s["vid"], w, len(diff), len(common)))
    json.dump(res, open(q("res", "gate690.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    streams_with_common = sum(1 for v in res.values() if sum(r["common"] for r in v.values()) > 0)
    say("ворота: потоков %d, потоков с общими ключами %d, общих ключей %d, no_key после слияния %d, нарушений %d" % (
        len(ss), streams_with_common, sum(r["common"] for v in res.values() for r in v.values()), sum(r["no_key_new"] for v in res.values() for r in v.values()), len(bad)))
    for b in bad[:30]:
        say("  FAIL", b)
    if streams_with_common < 3:
        bad.append("общих ключей есть у %d потоков (< 3)" % streams_with_common)
    if bad:
        raise GuardError("gate: нарушений %d" % len(bad))


def cmd_check(a):
    import tk604_run as r4
    import tk674_run as run
    st, r5, _, _ = run.t._srv()
    run.engine_opts(r5)
    run._bin()
    u = r5.Uni(run.UNI)
    ss = load_streams()
    cols = json.load(open(os.path.join(SRC, "res", "ftabcols674x-S.json"), encoding="utf-8"))
    col = next(iter(cols.values()))
    bad = []
    for w in st.TRADE_WINDOWS:
        out = os.path.join(ROOT, "check", w.name)
        variants = [(s["vid"], run.t.variant_line(r5, u, SIDE, s["patch"]) + " fa=%s fo=lo fv=%g" % (col, run.ax.FV_PASS)) for s in ss]
        r5.tick(u, w, variants, out, run.one_side_env(SIDE), q("log", "check-%s.log" % w.name), ftab=os.path.join(ROOT, "ftab674xs", "%s-%s.csv" % (w.name, SIDE)))
        for s in ss:
            meta = json.load(open(os.path.join(out, s["vid"], "meta.json"), encoding="utf-8"))[run.SIDENAME[SIDE]]
            fc = r4.filter_counters(meta)
            if fc["no_key"] or fc["cands"] != s["cands"][w.name]:
                bad.append("%s %s: %s ≠ cands %d, no_key 0" % (s["vid"], w.name, fc, s["cands"][w.name]))
    say("check: потоков %d × окон 3, нарушений %d" % (len(ss), len(bad)))
    for b in bad[:30]:
        say("  FAIL", b)
    if bad:
        raise GuardError("check: нарушений %d" % len(bad))


def cmd_slice(a):
    """Срез строк существующей таблицы TK-671 (случайные N на окно, семя 690) → rows.parquet среза с прежними row_id; признаки среза сверяются со столбцами таблицы TK-671."""
    import tk625_run as r5
    keep = set(r5.Uni(os.environ.get("TK674_UNI", "c539")).coins())              # у монет вне состава (TradFi, В-175) кэша окон нет
    for w in WINS:
        r = pd.read_parquet(os.path.join(P671, "prod", "lvl-%s-%s" % (w, SN), "rows.parquet"))
        r = r[(r["base_pass"] > 0) & r["sym"].isin(keep)].sample(n=a.n, random_state=690).sort_values("row_id")
        os.makedirs(os.path.dirname(rows_path(w)), exist_ok=True)
        r.to_parquet(rows_path(w), index=False)
        say("slice %s: строк %d" % (w, len(r)))


def cmd_slicegate(a):
    d = pd.read_parquet(os.path.join(ROOT, "data", "%s.parquet" % SN))
    ref = pd.read_parquet(os.path.join(P671, "data", "%s.parquet" % SN))
    m = d.merge(ref, on=["win", "row_id"], how="left", suffixes=("", "__ref"), validate="one_to_one")
    cols = [c for c in d.columns if c not in ("win", "row_id", "sym") and not c.startswith("meta_") and c + "__ref" in m.columns]
    av = pd.concat([pd.read_parquet(os.path.join(ROOT, "fam", "avail", "%s-%s.parquet" % (w, SN))).assign(win=w) for w in WINS], ignore_index=True)
    m = m.merge(av, on=["win", "row_id"], how="left", validate="one_to_one")
    una = m["cc_unavail"].fillna(False).to_numpy(bool)
    bad, explained = {}, {}
    for c in cols:
        x, y = m[c].to_numpy(np.float64), m[c + "__ref"].to_numpy(np.float64)
        diff = ~((x == y) | (np.isnan(x) & np.isnan(y)))
        if c in CC_COLS:                                         # объяснённое: у кластера строки есть сосед без кэша (TradFi, В-175), у нас NaN, в таблице TK-671 значение
            ex = diff & una & np.isnan(x) & np.isfinite(y)
            if ex.any():
                explained[c] = int(ex.sum())
            diff = diff & ~ex
        n = int(diff.sum())
        if n:
            bad[c] = n
    only = [c for c in d.columns if c not in ("win", "row_id", "sym") and not c.startswith("meta_") and c + "__ref" not in m.columns]
    say("срез: строк %d, общих столбцов %d, различных %d (объяснённых «сосед без кэша» %s), столбцов без пары в таблице TK-671 %d" % (len(d), len(cols), len(bad), explained or 0, len(only)))
    for c, n in sorted(bad.items(), key=lambda kv: -kv[1])[:40]:
        say("  %s: %d строк из %d" % (c, n, len(d)))
    if only:
        say("  без пары:", ", ".join(only[:30]))
    json.dump(dict(rows=len(d), cols=len(cols), bad=bad, explained=explained, only=only), open(q("res", "slicegate690.json"), "w", encoding="utf-8"), indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("streams")
    s.add_argument("--expect", type=int, default=15)
    sp.add_parser("dump")
    sp.add_parser("rows")
    f = sp.add_parser("feat")
    f.add_argument("--threads", type=int, default=3)
    sp.add_parser("avail")
    sp.add_parser("asm")
    sp.add_parser("tables")
    sp.add_parser("gate")
    sp.add_parser("check")
    sl = sp.add_parser("slice")
    sl.add_argument("n", type=int)
    sp.add_parser("slicegate")
    a = ap.parse_args()
    os.environ.setdefault("TK674_ROOT", ROOT)               # tk674_run при импорте берёт каталог из среды: писать в каталог TK-674 нельзя
    if os.environ["TK674_ROOT"] == SRC:
        raise GuardError("TK674_ROOT = каталог TK-674 (%s): результат пишется только в %s" % (SRC, ROOT))
    {"streams": cmd_streams, "dump": cmd_dump, "rows": cmd_rows, "feat": cmd_feat, "avail": cmd_avail, "asm": cmd_asm, "tables": cmd_tables, "gate": cmd_gate, "check": cmd_check, "slice": cmd_slice,
     "slicegate": cmd_slicegate}[a.cmd](a)


if __name__ == "__main__":
    main()
