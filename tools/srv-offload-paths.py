#!/usr/bin/env python3
"""TK-675: перенести пути srv (каталог целиком или с исключёнными поддеревьями) на Storage Box по одному: rsync → сверка → удаление.
Как tools/srv-offload-dirs.py, но путь любой (/data/<имя>/<подкаталог>, /root/<имя>), назначение на боксе задаётся, часть файлов можно оставить.
spec.json — список {"src": "/data/p42-past", "dest": "offload-srv-2026-11-22/p42-past", "exclude": ["klines/BTCUSDT"]} (exclude — пути от src, остаются на srv).
По каждому пути, строго по очереди: отказ, если путь назван в файле юнита или занят процессом (cwd / открытый файл / exe / cmdline) → сводка до (файлы, ссылки, байты)
→ rsync -aH → сводка после = до (путь не менялся) → `ls -lAR` бокса (без исключённых поддеревьев) = локальной → повторный `rsync -aHn --itemize-changes` без отличий
→ sha256 выборки (локально заново = `sha256sum` на боксе) → при --delete: сводка ещё раз = до (иначе отказ), удаляются только перенесённые файлы и ссылки, исключённые остаются.
Строка JSON на путь — в --manifest.
--preflight: один обход /data /opt /root, ищет ссылки из остающегося на srv (снаружи и из исключённых поддеревьев), ведущие в переносимую часть (после переноса они повисли бы);
код 1, если нашлись. --delete сам запускает этот обход один раз до первого пути и при найденных ссылках ничего не копирует и не удаляет.
Запуск (долгий — только через systemd-run, Nice=19, IOSchedulingClass=idle): srv-offload-paths.py spec.json --manifest M.jsonl [--delete]"""
import argparse, glob, hashlib, json, os, posixpath, random, re, shlex, shutil, stat, subprocess, sys, time

SSH = "ssh -o BatchMode=yes"
ROOTS = ("/data", "/opt", "/root")


def log(*x):
    print(time.strftime("%H:%M:%S"), *x, flush=True)


def relof(r, src):
    """Путь r относительно src, всегда через `/`; сам src — пустая строка."""
    rel = os.path.relpath(r, src).replace(os.sep, "/")
    return "" if rel == "." else rel


def under(rel, excl):
    """rel (от src) равен исключению или лежит под ним."""
    return any(rel == e or rel.startswith(e + "/") for e in excl)


def scan(src, excl):
    """Сводка перенимаемых файлов (без исключённых поддеревьев и без перехода по ссылкам)."""
    reg = lnk = size = mt = 0
    files = []
    for r, ds, fs in os.walk(src):
        rel = relof(r, src)
        ds[:] = [d for d in ds if not under(posixpath.join(rel, d), excl)]
        for d in ds:
            if os.path.islink(os.path.join(r, d)):
                lnk += 1
        for f in fs:
            if under(posixpath.join(rel, f), excl):
                continue
            p = os.path.join(r, f)
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode):
                reg += 1
                size += st.st_size
                mt = max(mt, st.st_mtime_ns)
                files.append(p)
            elif stat.S_ISLNK(st.st_mode):
                lnk += 1
    return {"files": reg, "symlinks": lnk, "bytes": size, "max_mtime_ns": mt}, files


def parse_ls(text, dest, excl):
    """`ls -lAR dest` → число файлов, ссылок и сумма байт, без исключённых поддеревьев (по заголовкам `путь:`)."""
    reg = lnk = size = 0
    skip = False
    for line in text.splitlines():
        if line.endswith(":") and not line.startswith(("-", "l", "d", "total")):
            rel = line[:-1][len(dest):].lstrip("/")
            skip = under(rel, excl)
            continue
        if skip:
            continue
        m = re.match(r"^([-l])\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s", line)
        if m:
            if m.group(1) == "-":
                reg += 1
                size += int(m.group(2))
            else:
                lnk += 1
    return {"files": reg, "symlinks": lnk, "bytes": size}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def busy(src, excl):
    """Причины отказа: путь назван в файле юнита или держится процессом (кроме исключённых поддеревьев)."""
    why = []
    pat = re.compile(re.escape(src) + r"(?![A-Za-z0-9_.-])(/[^\s'\"\\\0]*)?")

    def hits(text):
        for m in pat.finditer(text):
            rel = (m.group(1) or "").strip("/")
            if not under(rel, excl) or not rel:
                return True
        return False

    for u in glob.glob("/etc/systemd/system/*.service") + glob.glob("/etc/systemd/system/*.timer") + glob.glob("/run/systemd/transient/*.service"):
        try:
            if hits(open(u, errors="replace").read()):
                why.append(f"юнит {os.path.basename(u)}")
        except OSError:
            pass
    me = str(os.getpid())
    for pid in filter(str.isdigit, os.listdir("/proc")):
        if pid == me:
            continue
        try:
            links = [os.readlink(f"/proc/{pid}/cwd"), os.readlink(f"/proc/{pid}/exe")] + [os.readlink(f"/proc/{pid}/fd/{x}") for x in os.listdir(f"/proc/{pid}/fd")]
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if "srv-offload-paths" in cmd:
            continue
        if any(hits(l) for l in links) or hits(cmd):
            why.append(f"процесс {pid}: {cmd[:80]}")
    return why


def delete_moved(src, excl):
    if not excl:
        shutil.rmtree(src)
        return
    dirs = []
    for r, ds, fs in os.walk(src):
        rel = relof(r, src)
        ds[:] = [d for d in ds if not under(posixpath.join(rel, d), excl)]
        for f in fs:
            if not under(posixpath.join(rel, f), excl):
                os.unlink(os.path.join(r, f))
        for d in ds:
            p = os.path.join(r, d)
            if os.path.islink(p):  # ссылка на каталог: os.walk кладёт её в ds, rmdir её не снимает
                os.unlink(p)
            else:
                dirs.append(p)
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass


def moved(p, srcs):
    """p лежит в переносимой части одного из путей (сам src или под ним, вне исключений)."""
    for s, ex in srcs:
        if p == s or p.startswith(s + os.sep):
            if not under(relof(p, s), ex):
                return True
    return False


def keeps(p, srcs):
    """Под переносимым каталогом p есть исключённое поддерево, остающееся на srv."""
    for s, ex in srcs:
        if p == s or p.startswith(s + os.sep):
            rel = relof(p, s)
            if any(not rel or e.startswith(rel + "/") for e in ex):
                return True
    return False


def preflight(spec, roots=None):
    srcs = [(s["src"].rstrip("/"), [e.strip("/") for e in s.get("exclude", [])]) for s in spec]
    bad = []
    for root in roots or ROOTS:
        for r, ds, fs in os.walk(root):
            for n in ds + fs:
                p = os.path.join(r, n)
                if not os.path.islink(p) or moved(p, srcs):
                    continue
                t = os.path.normpath(os.path.join(r, os.readlink(p)))
                if moved(t, srcs):
                    bad.append((p, t))
            # переносимый каталог обходим, только если под ним остаётся исключённое
            ds[:] = [d for d in ds if not moved(os.path.join(r, d), srcs) or keeps(os.path.join(r, d), srcs)]
    for p, t in bad:
        log(f"ССЫЛКА из остающегося в переносимое: {p} -> {t}")
    log(f"preflight: {len(bad)} ссылок")
    return 1 if bad else 0


def ssh(cmd, timeout=7200):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "box", cmd], capture_output=True, text=True, timeout=timeout)


def move_one(u, a, manifest):
    src, dest, excl = u["src"].rstrip("/"), u["dest"].strip("/"), [e.strip("/") for e in u.get("exclude", [])]
    rec = {"src": src, "dest": dest, "exclude": excl, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "deleted": False}
    why = None
    if not os.path.isdir(src):
        why = "нет каталога"
    if not why:
        w = busy(src, excl)
        if w:
            why = f"занят: {w[:3]}"
    if not why:
        before, files = scan(src, excl)
        rec["local_before"] = before
        log(f"до копии {src}: {before}")
        ssh(f"mkdir -p {shlex.quote(dest)}")
        flt = [f"--exclude=/{e}" for e in excl]
        t0 = time.time()
        r = subprocess.run(["rsync", "-aH", "--partial", f"--bwlimit={a.bwlimit}", "-e", SSH] + flt + [src + "/", f"box:{dest}/"], capture_output=True, text=True)
        log(f"rsync rc={r.returncode} за {time.time()-t0:.0f} с {r.stderr.strip()[-300:]}")
        if r.returncode != 0:
            why = f"rsync rc={r.returncode}"
    if not why:
        after, _ = scan(src, excl)
        rec["local_after"] = after
        if after != before:
            why = "путь менялся во время копии"
    if not why:
        out = ssh(f"ls -lAR {shlex.quote(dest)}")
        bs = parse_ls(out.stdout, dest, excl)
        rec["box"] = bs
        if out.returncode != 0 or any(bs[k] != before[k] for k in ("files", "symlinks", "bytes")):
            why = f"бокс {bs} != локально {before}"
    if not why:
        dry = subprocess.run(["rsync", "-aHn", "--itemize-changes", "-e", SSH] + flt + [src + "/", f"box:{dest}/"], capture_output=True, text=True)
        diff = [l for l in dry.stdout.splitlines() if l.strip()]
        rec["rsync_dry_diff_lines"] = len(diff)
        if dry.returncode != 0 or diff:
            why = f"повторный rsync -n видит расхождения: {diff[:3]}"
    if not why and files:
        pick = random.sample(files, min(a.sample, len(files)))
        rel = [relof(p, src) for p in pick]
        out = ssh("sha256sum " + " ".join(shlex.quote(f"{dest}/{x}") for x in rel))
        rem = {l.partition("  ")[2].strip(): l.partition("  ")[0] for l in out.stdout.splitlines()}
        bad = [x for p, x in zip(pick, rel) if rem.get(f"{dest}/{x}") != sha256(p)]
        rec["sample"] = {"n": len(pick), "bad": len(bad)}
        if bad or len(rem) != len(pick):
            why = f"sha256 выборки: {len(bad)} из {len(pick)} не сошлись {bad[:2]}"
    if not why and a.delete:
        last, _ = scan(src, excl)
        rec["local_before_delete"] = last
        if last != before:
            why = "путь менялся после сверки, не удалён"
    rec["verified"] = why is None
    rec["why_not"] = why
    if why is None and a.delete:
        delete_moved(src, excl)
        left, _ = scan(src, excl)
        rec["deleted"] = left["files"] == 0 and left["symlinks"] == 0
        rec["left_after_delete"] = left["files"]
        rec["left_symlinks_after_delete"] = left["symlinks"]
    rec["free_gb"] = round(shutil.disk_usage("/").free / 2 ** 30, 1)
    log(f"{'OK  ' if why is None else 'FAIL'} {src}: box={rec.get('box')} sample={rec.get('sample')} {'удалён' if rec['deleted'] else ''} {why or ''} свободно {rec['free_gb']} ГиБ")
    open(manifest, "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
    return why is None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--bwlimit", type=int, default=40000, help="КБ/с")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--only", help="имена src через запятую (подстрока пути)")
    a = ap.parse_args(argv)
    spec = json.load(open(a.spec))
    if a.preflight:
        return preflight(spec)
    if a.only:
        keys = a.only.split(",")
        spec = [s for s in spec if any(k in s["src"] for k in keys)]
    if a.delete and preflight(spec):
        log("отказ: --delete при ссылках из остающегося в переносимое; ничего не скопировано и не удалено")
        return 1
    ok = 0
    for u in spec:
        ok += bool(move_one(u, a, a.manifest))
    log(f"итого: {ok} из {len(spec)} проверены{' и удалены' if a.delete else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
