import csv, json, os, sys, time
T = {r["symbol"] for r in csv.DictReader(open("/data/p42-tk621/universe/contracts.csv", encoding="utf-8")) if r["tradfi"] == "1"}
ex = json.load(open("/root/tk686/out/exchange.json"))
T |= {s for s, v in ex.items() if isinstance(v, dict) and v.get("contractType") == "TRADIFI_PERPETUAL"}
SKIP = {"collect", "lanes", "chron", "turbo-probe", "netwatch", "p42-tk686", "arb-tab"}
def skip(name): return name in SKIP or name.startswith(("collect-", "bbws-", "bnws-", "health", "g022-", "soak"))
def sym_of(n): return n.split("-")[0].split(".")[0]
roots = []  # (path, [entries matching T])
scanned = 0
def bfs(d, depth):
    global scanned
    scanned += 1
    try: es = list(os.scandir(d))
    except OSError: return
    hit = [e for e in es if sym_of(e.name) in T]
    if len(hit) >= 3:
        roots.append((d, hit, len(es))); return
    if depth >= 5: return
    for e in es:
        if e.is_dir(follow_symlinks=False) and not (depth == 0 and skip(e.name)):
            bfs(e.path, depth + 1)
t0 = time.time(); bfs("/data", 0)
print("scanned dirs", scanned, "symbol-roots", len(roots), f"{time.time()-t0:.0f} s", flush=True)
reg = {}; links = []  # root -> stats
rows = []
regset = set()
agg = {}
for d, hit, n in roots:
    nf = nl = 0; nb = 0; syms = set(); link_targets = []
    for e in hit:
        syms.add(sym_of(e.name))
        if e.is_dir(follow_symlinks=False):
            for c in os.scandir(e.path):
                if c.is_symlink(): nl += 1; link_targets.append(os.path.realpath(c.path))
                elif c.is_file(): nf += 1; nb += c.stat().st_size; regset.add(c.path)
        elif e.is_symlink(): nl += 1; link_targets.append(os.path.realpath(e.path))
        elif e.is_file(): nf += 1; nb += e.stat().st_size; regset.add(e.path)
    agg[d] = (nf, nl, nb, len(syms), n, link_targets)
out_tsv = open("/root/tk686/tradfi-roots.tsv", "w", encoding="utf-8")
out_tsv.write("root\tfiles\tsymlinks\tbytes\ttradfi_syms\tentries_in_root\n")
tot_nf = tot_nb = 0
tgt = set()
for d, (nf, nl, nb, ns, n, lt) in sorted(agg.items()):
    out_tsv.write(f"{d}\t{nf}\t{nl}\t{nb}\t{ns}\t{n}\n"); tot_nf += nf; tot_nb += nb
    tgt.update(lt)
out_tsv.close()
outside = [t for t in tgt if t not in regset]
ob = 0; of = 0
for t in outside:
    try: ob += os.stat(t).st_size; of += 1
    except OSError: pass
json.dump({"tradfi_total": len(T), "roots": len(roots), "regular_files": tot_nf, "regular_bytes": tot_nb, "symlink_targets_outside_roots_files": of, "symlink_targets_outside_bytes": ob,
           "symlinks": sum(v[1] for v in agg.values())}, open("/root/tk686/tradfi-summary.json", "w"), indent=1)
print("done", tot_nf, round(tot_nb / 2**30, 2), "GiB; outside-target files", of, round(ob / 2**30, 2), "GiB", flush=True)
