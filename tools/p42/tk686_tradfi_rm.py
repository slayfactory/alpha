#!/usr/bin/env python3
"""TK-686 / В-175: удалить данные контрактов TradFi (список — `tk686_tradfi_enum.py`, корни — `/root/tk686/tradfi-roots.tsv`).
Запуск на srv юнитом (`systemd-run`, Nice 19, ionice idle): без `--exec` — только план и сверка с таблицей; с `--exec` — удаление.
Порядок: .b42 (монета уходит из движка) → ссылки окон → zip, .tk, минутки → premium / metrics / funding → пустые каталоги → манифесты деревьев
(строки TradFi убираются, копия `.bak-tk686`) → `symbols-crypto.txt` → проход согласованности. Удаляются только записи, чей символ в списке TradFi
и которые лежат в корнях таблицы; каталог с подкаталогом пропускается. Журнал удалённых путей — /root/tk686/tradfi-removed.log.
Проверка склада (заголовок `.tk` против stat zip, как `valid_rows`; zip не читаются) — до и после, только для крипты."""
import csv
import hashlib
import json
import os
import struct
import sys
import time

EXEC = "--exec" in sys.argv
TSV = "/root/tk686/tradfi-roots.tsv"
LOG = "/root/tk686/tradfi-removed.log"
OUT = "/root/tk686/tradfi-rm-%s.json" % ("exec" if EXEC else "dry")
STORE = "/data/p42-tk599/tick-store"
FEAT = ("p42-premium", "p42-metrics", "p42-tk600", "p42-tk666", "p42-tk599/metrics")
CRYPTO_ALL = "/data/p42-tk621/universe/symbols-crypto.txt"


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


T = {r["symbol"] for r in csv.DictReader(open("/data/p42-tk621/universe/contracts.csv", encoding="utf-8")) if r["tradfi"] == "1"}
ex = json.load(open("/root/tk686/out/exchange.json"))
T |= {s for s, v in ex.items() if isinstance(v, dict) and v.get("contractType") == "TRADIFI_PERPETUAL"}
ROOTS = [l.split("\t")[0] for l in open(TSV, encoding="utf-8").read().splitlines()[1:]]
assert len(ROOTS) == 58 and all(r.startswith("/data/") and not r.startswith("/data/p42-tk686") for r in ROOTS)


def free():
    s = os.statvfs("/")
    return s.f_bavail * s.f_frsize


def sym_of(n):
    return n.split("-")[0].split(".")[0]


def is_feat(root):
    rel = root[len("/data/"):]
    return rel.startswith(FEAT)


def plan():
    P = {"b42": [], "link": [], "data": [], "feat": []}
    dirs, skipped = [], []
    for root in ROOTS:
        try:
            es = list(os.scandir(root))
        except OSError as e:
            skipped.append((root, str(e)))
            continue
        for e in es:
            if sym_of(e.name) not in T:
                continue
            ents = []
            if e.is_dir(follow_symlinks=False):
                ch = list(os.scandir(e.path))
                if any(c.is_dir(follow_symlinks=False) for c in ch):
                    skipped.append((e.path, "подкаталог"))
                    continue
                ents = ch
                dirs.append(e.path)
            else:
                ents = [e]
            for c in ents:
                st = c.stat(follow_symlinks=False)
                if c.is_symlink():
                    k = "link"
                elif c.name.endswith(".b42"):
                    k = "b42"
                elif is_feat(root):
                    k = "feat"
                else:
                    k = "data"
                P[k].append((c.path, st.st_size if k != "link" else 0))
    return P, dirs, skipped


def head_ok(zpath, tkpath):
    """Как `valid_rows`: размер, mtime_s, mtime_ns zip в заголовке `.tk`, длина файла = заголовок + блоки."""
    try:
        z = os.stat(zpath)
    except OSError:
        return "dangling"
    try:
        with open(tkpath, "rb") as f:
            h = f.read(96)
            if len(h) < 96 or h[:4] != b"TK42" or struct.unpack_from("<I", h, 4)[0] != 2:
                return "stale"
            size, ms = struct.unpack_from("<QQ", h, 8)
            (mns,) = struct.unpack_from("<I", h, 24)
            (ca,) = struct.unpack_from("<I", h, 68)
            (cq,) = struct.unpack_from("<I", h, 92)
            tot = 96 + ca + cq
            ok = size == z.st_size and ms == int(z.st_mtime_ns // 10**9) and mns == z.st_mtime_ns % 10**9 and os.fstat(f.fileno()).st_size == tot
            return "ok" if ok else "stale"
    except OSError:
        return "no_tk"


TREES = [d for d in ("/data/p42-tk621/wa", "/data/p42-tk621/wb", "/data/p42-tk621/wc", "/data/p42-tk599/wa", "/data/p42-tk599/wb", "/data/p42-tk599/wc")]


def store_check():
    res, memo = {}, {}
    for tr in TREES:
        c = {"ok": 0, "no_tk": 0, "stale": 0, "dangling": 0}
        ad = tr + "/aggtrades"
        for sd in sorted(os.scandir(ad), key=lambda e: e.name):
            if sd.name in T or not sd.is_dir():
                continue
            for z in os.scandir(sd.path):
                if not z.name.endswith(".zip"):
                    continue
                d = z.name.rsplit("-aggTrades-", 1)[1][:-4]
                k = (sd.name, d, os.path.realpath(z.path))
                if k not in memo:
                    memo[k] = head_ok(z.path, f"{STORE}/{sd.name}/{sd.name}-{d}.tk")
                c[memo[k]] += 1
        res[tr] = c
    return res


def filter_manifest(path):
    keep, drop = [], 0
    for l in open(path, encoding="utf-8").read().splitlines():
        tok = [t for t in l.split() if t.startswith("./")]
        s = tok[0][2:].split("/")[0].split(".")[0] if tok else None
        if s in T:
            drop += 1
        else:
            keep.append(l)
    return keep, drop


def main():
    out = {"exec": EXEC, "t": T and len(T)}
    say("TradFi", len(T), "корней", len(ROOTS), "режим", "УДАЛЕНИЕ" if EXEC else "план")
    out["free_before"] = free()
    P, dirs, skipped = plan()
    out["plan"] = {k: {"files": len(v), "bytes": sum(b for _, b in v)} for k, v in P.items()}
    out["plan"]["dirs"] = len(dirs)
    out["skipped"] = skipped[:20]
    tot_f = sum(len(v) for k, v in P.items() if k != "link")
    tot_b = sum(b for k, v in P.items() for _, b in v)
    say("план: файлов", tot_f, "ссылок", len(P["link"]), "ГиБ", round(tot_b / 2**30, 3), "пропущено", len(skipped))
    tsv_files = sum(int(l.split("\t")[1]) for l in open(TSV, encoding="utf-8").read().splitlines()[1:])
    tsv_links = sum(int(l.split("\t")[2]) for l in open(TSV, encoding="utf-8").read().splitlines()[1:])
    out["vs_table"] = {"files_table": tsv_files, "files_plan": tot_f, "links_table": tsv_links, "links_plan": len(P["link"])}
    say("таблица: файлов", tsv_files, "ссылок", tsv_links)
    out["store_before"] = store_check()
    say("склад до:", json.dumps(out["store_before"]))
    if not EXEC:
        json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say("план записан", OUT)
        return
    if skipped or tot_f != tsv_files or len(P["link"]) != tsv_links:
        say("СТОП: план расходится с таблицей / есть пропуски — удаление не начато")
        out["stopped"] = True
        json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        sys.exit(2)
    removed = {}
    with open(LOG, "a", encoding="utf-8") as lg:
        for ph in ("b42", "link", "data", "feat"):
            n = b = 0
            for p, sz in P[ph]:
                os.unlink(p)
                lg.write(f"{ph}\t{p}\t{sz}\n")
                n += 1
                b += sz
            removed[ph] = {"files": n, "bytes": b}
            say("удалено", ph, n, round(b / 2**30, 3), "ГиБ")
        nd = 0
        for d in dirs:
            try:
                os.rmdir(d)
                nd += 1
            except OSError as e:
                lg.write(f"rmdir-fail\t{d}\t{e}\n")
        lg.write(f"rmdir\t{nd}\t0\n")
        removed["dirs_removed"] = nd
    out["removed"] = removed
    out["free_after_rm"] = free()
    # манифесты
    man = {}
    trees = sorted({os.path.dirname(r) for r in ROOTS if r.endswith("/cache")})
    for tr in trees:
        for mn in ("manifest-cache.txt", "manifest-trades.txt", "manifest-klines.txt", "manifest-cache-sha256.txt"):
            mp = f"{tr}/{mn}"
            if not os.path.exists(mp):
                continue
            keep, drop = filter_manifest(mp)
            if drop:
                os.replace(mp, mp + ".bak-tk686") if not os.path.exists(mp + ".bak-tk686") else None
                open(mp, "w", encoding="utf-8").write("\n".join(keep) + "\n")
            man[mp] = {"lines_after": len(keep), "dropped": drop, "sha256": hashlib.sha256(open(mp, "rb").read()).hexdigest()[:16]}
    out["manifests"] = man
    # списки символов
    cr = set(open(CRYPTO_ALL, encoding="utf-8").read().replace("\n", ",").split(",")) - {""}
    out["symbols_crypto_universe"] = {"n": len(cr), "tradfi_in": len(cr & T)}
    for tr in trees:
        stems = sorted(f[:-4] for f in os.listdir(tr + "/cache") if f.endswith(".b42"))
        open(tr + "/symbols-crypto.txt", "w", encoding="utf-8").write(",".join(stems) + "\n")
    s566 = [s for s in open("/data/p42-t566/symbols.txt", encoding="utf-8").read().replace("\n", ",").split(",") if s and s not in T]
    open("/data/p42-t566/symbols-crypto.txt", "w", encoding="utf-8").write(",".join(s566) + "\n")
    out["symbols_t566_crypto"] = len(s566)
    # согласованность
    cons = {}
    for tr in trees:
        b42 = sorted(f[:-4] for f in os.listdir(tr + "/cache") if f.endswith(".b42"))
        c = {"b42": len(b42), "b42_tradfi": len(set(b42) & T), "b42_not_in_universe_crypto": len(set(b42) - cr), "universe_crypto_not_in_b42": len(cr - set(b42))}
        for sub in ("aggtrades", "klines"):
            d = f"{tr}/{sub}"
            if not os.path.isdir(d):
                continue
            nl = dang = tf = 0
            for sd in os.scandir(d):
                if sd.name in T:
                    tf += 1
                if sd.is_dir(follow_symlinks=False):
                    for z in os.scandir(sd.path):
                        if z.is_symlink():
                            nl += 1
                            dang += 0 if os.path.exists(z.path) else 1
            c[sub] = {"symlinks": nl, "dangling": dang, "tradfi_dirs": tf}
        for mn, key in (("manifest-cache.txt", "b42"),):
            mp = f"{tr}/{mn}"
            if os.path.exists(mp):
                c[mn] = {"lines": sum(1 for _ in open(mp, encoding="utf-8")), "vs_listing": c[key]}
        cons[tr] = c
    out["consistency"] = cons
    out["store_after"] = store_check()
    say("склад после:", json.dumps(out["store_after"]))
    out["same_store"] = out["store_before"] == out["store_after"]
    left, _, sk = plan()
    out["left_after"] = {k: len(v) for k, v in left.items()}
    out["free_after"] = free()
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("готово", OUT, "осталось записей TradFi:", {k: len(v) for k, v in left.items()}, "склад до = после:", out["same_store"])


main()
