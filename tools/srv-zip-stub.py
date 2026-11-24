#!/usr/bin/env python3
"""TK-694: сырьё aggTrades-zip, у которого есть действительный `.tk` склада тиков, уходит с srv на Storage Box, а на месте zip остаётся пустышка
(разрежённый файл того же размера и mtime до наносекунды, 0 блоков на диске). Движок, отпечаток кэша сигналов и `store-fill` читают у zip только
размер и mtime (`SrcMeta`), поэтому при действительном `.tk` пустышка неотличима от zip; без `.tk` — громкая ошибка загрузки, не тихая подмена.

  manifest ЛОК=БОКС [ЛОК=БОКС …] --store СКЛАД --out M.jsonl   по каждому zip: sha256 локально и на боксе (`sha256sum`), сверка с `.CHECKSUM` / `.sha256`
                                                               рядом, заголовок `.tk` = размер + mtime; запись {path, box, bytes, mtime_ns, sha256, verified}
  stub M.jsonl --store СКЛАД [--go]                            verified-записи → пустышки (без --go только счёт); файл изменился после манифеста — пропуск
  restore M.jsonl [--only ПОДСТРОКА]                           пустышки → настоящие zip с бокса (sha256 из манифеста, mtime из манифеста)

ЛОК — каталог с `<МОНЕТА>/<МОНЕТА>-aggTrades-<дата>.zip`, БОКС — тот же каталог на боксе. Запуск — только через systemd-run (Nice=19, idle IO)."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import struct
import subprocess
import sys
import time

ZIP = re.compile(r"^(.+)-aggTrades-(\d{4}-\d{2}-\d{2})\.zip$")
BATCH = 60
MAGIC = b"TK42"


def log(*x):
    print(time.strftime("%H:%M:%S"), *x, flush=True)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def is_stub(st):
    """Пустышка: размер есть, блоков на диске нет (на ФС без `st_blocks` — никогда)."""
    return st.st_size > 0 and getattr(st, "st_blocks", None) == 0


class Box:
    """Storage Box по ssh (`box` из ~/.ssh/config на srv); оболочка бокса принимает одну команду за вызов."""

    def sha256(self, paths):
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "box", "sha256sum " + " ".join(shlex.quote(p) for p in paths)],
                             capture_output=True, text=True, timeout=3600).stdout
        res = {}
        for line in out.splitlines():
            h, _, p = line.partition("  ")
            if p:
                res[p.strip()] = h
        return res

    def fetch(self, path, dst):
        subprocess.run(["rsync", "-e", "ssh -o BatchMode=yes", "box:" + path, dst], check=True)


def tk_ok(store, coin, day, st):
    """Заголовок `.tk` склада совпадает с размером и mtime zip (`store.rs`: смещения 8 / 16 / 24)."""
    try:
        with open(os.path.join(store, coin, "%s-%s.tk" % (coin, day)), "rb") as f:
            h = f.read(28)
    except OSError:
        return False
    if len(h) < 28 or h[:4] != MAGIC:
        return False
    size, ms = struct.unpack_from("<QQ", h, 8)
    ns = struct.unpack_from("<I", h, 24)[0]
    return size == st.st_size and ms == st.st_mtime_ns // 10 ** 9 and ns == st.st_mtime_ns % 10 ** 9


def sidecar_hash(p):
    for ext in (".CHECKSUM", ".sha256"):
        try:
            with open(p + ext, encoding="utf-8") as f:
                tok = f.read().split()
        except OSError:
            continue
        if tok:
            return tok[0].lower()
    return None


def walk_zips(local):
    for d, ds, fs in os.walk(local):
        ds.sort()
        for f in sorted(fs):
            m = ZIP.match(f)
            p = os.path.join(d, f)
            if m and not os.path.islink(p):
                yield p, m.group(1), m.group(2)


def read_manifest(path):
    recs = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f if l.strip()]
    return recs


def cmd_manifest(a, box=None):
    box = box or Box()
    done = {r["path"]: r for r in read_manifest(a.out) if r.get("verified")}
    items = []
    for spec in a.roots:
        local, _, bdir = spec.partition("=")
        for p, coin, day in walk_zips(local):
            items.append((p, bdir.rstrip("/") + "/" + os.path.relpath(p, local).replace(os.sep, "/"), coin, day))
    todo, stubs, resumed = [], 0, 0
    for it in items:
        st = os.lstat(it[0])
        if is_stub(st):
            stubs += 1
        elif it[0] in done and done[it[0]]["bytes"] == st.st_size and done[it[0]]["mtime_ns"] == st.st_mtime_ns:
            resumed += 1
        else:
            todo.append(it)
    log("zip: всего %d, уже пустышек %d, уже в манифесте %d, к сверке %d" % (len(items), stubs, resumed, len(todo)))
    bad = ok = 0
    with open(a.out, "a", encoding="utf-8") as out, concurrent.futures.ThreadPoolExecutor(1) as ex:
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            fut = ex.submit(box.sha256, [b for _, b, _, _ in batch])
            loc = []
            for p, b, coin, day in batch:
                st = os.lstat(p)
                loc.append((p, b, coin, day, st, sha256_file(p)))
            rem = fut.result()
            for p, b, coin, day, st, h in loc:
                st2 = os.lstat(p)
                why = None
                sc = sidecar_hash(p)
                if (st2.st_size, st2.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                    why = "файл менялся во время сверки"
                elif rem.get(b) != h:
                    why = "sha256 на боксе %s ≠ локальный" % (rem.get(b) or "нет файла")
                elif sc is not None and sc != h:
                    why = "sha256 не равен опубликованному (%s)" % os.path.basename(p)
                elif not tk_ok(a.store, coin, day, st):
                    why = "нет действительного .tk"
                rec = {"path": p, "box": b, "bytes": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": h,
                       "sidecar": "none" if sc is None else "ok", "verified": why is None}
                if why:
                    rec["why"] = why
                    bad += 1
                else:
                    ok += 1
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            if (i // BATCH) % 100 == 0:
                log("сверено %d из %d, не прошло %d" % (i + len(batch), len(todo), bad))
    log("манифест: verified %d, не прошло %d → %s" % (ok, bad, a.out))
    return 1 if bad else 0


def make_stub(p, size, mtime_ns, mode):
    tmp = p + ".stubtmp"
    with open(tmp, "wb") as f:
        f.truncate(size)
    os.utime(tmp, ns=(mtime_ns, mtime_ns))
    os.chmod(tmp, mode)
    st = os.lstat(tmp)
    if st.st_size != size or st.st_mtime_ns != mtime_ns:
        os.remove(tmp)
        raise SystemExit("пустышка %s: размер или mtime не совпали" % p)
    os.replace(tmp, p)


def cmd_stub(a):
    recs = [r for r in read_manifest(a.manifest) if r.get("verified")]
    n = freed = skipped = already = 0
    for r in recs:
        p = r["path"]
        try:
            st = os.lstat(p)
        except OSError:
            skipped += 1
            continue
        if is_stub(st):
            already += 1
            continue
        m = ZIP.match(os.path.basename(p))
        if (st.st_size, st.st_mtime_ns) != (r["bytes"], r["mtime_ns"]) or not tk_ok(a.store, m.group(1), m.group(2), st):
            skipped += 1
            log("пропуск (изменился или нет .tk):", p)
            continue
        if a.go:
            make_stub(p, r["bytes"], r["mtime_ns"], st.st_mode & 0o777)
        n += 1
        freed += r["bytes"]
        if n % 5000 == 0:
            log("пустышек %d, %.1f ГиБ" % (n, freed / 2 ** 30))
    log("%s: пустышек %d (%.2f ГиБ), уже были %d, пропущено %d" % ("сделано" if a.go else "план (без --go)", n, freed / 2 ** 30, already, skipped))
    return 0


def cmd_restore(a, box=None):
    box = box or Box()
    n = 0
    for r in read_manifest(a.manifest):
        p = r["path"]
        if not r.get("verified") or (a.only and a.only not in p):
            continue
        try:
            st = os.lstat(p)
        except OSError:
            st = None
        if st is not None and not is_stub(st):
            continue
        tmp = p + ".restore"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        box.fetch(r["box"], tmp)
        if sha256_file(tmp) != r["sha256"]:
            os.remove(tmp)
            raise SystemExit("восстановление %s: sha256 не сошёлся" % p)
        os.utime(tmp, ns=(r["mtime_ns"], r["mtime_ns"]))
        os.replace(tmp, p)
        n += 1
    log("восстановлено zip: %d" % n)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("roots", nargs="+", help="ЛОК=БОКС")
    m.add_argument("--store", required=True)
    m.add_argument("--out", required=True)
    s = sub.add_parser("stub")
    s.add_argument("manifest")
    s.add_argument("--store", required=True)
    s.add_argument("--go", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("manifest")
    r.add_argument("--only")
    a = ap.parse_args(argv)
    return {"manifest": cmd_manifest, "stub": cmd_stub, "restore": cmd_restore}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
