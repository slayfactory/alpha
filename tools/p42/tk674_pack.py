#!/usr/bin/env python3
"""П-42 TK-674: упаковка каталогов клеток `cells/<окно>-<сторона>/<хеш партии>/<cid>/` (5 CSV по 4 КБ-блоку на клетку ≈ 34 КБ на диске при ≈ 5 КБ данных) в
`cells-arch/<окно>-<сторона>/<хеш>.tar.zst`; каталог удаляется только после проверки (zstd -t, число записей архива = числу записей каталога, байт в байт у 20 случайных файлов).
  srv:  TK674_ROOT=/data/p42-tk674c python3 tk674_pack.py pack <окно>-<сторона> [--delete] [--only <хеш>] [--jobs 3]
        TK674_ROOT=/data/p42-tk674c TK674_BATCH=<rNN> TK674_SIDES=<L|S> python3 tk674_pack.py pack-batch   (партии rNN: каталоги из res/batches674<SUF>-<сторона>.json, три окна; после tables + ccheck)
        TK674_ROOT=/data/p42-tk674c python3 tk674_pack.py extract <каталог клетки>        (в `cells/…` на место; для чтения карты / сделок)
        TK674_ROOT=/data/p42-tk674c python3 tk674_pack.py drop <окно>-<сторона>            (убрать извлечённые каталоги партий, у которых есть архив)
Читатели каталогов клеток зовут `ensure_cell(путь)`: нет каталога — извлекается его клетка из архива партии. Готовый архив партии не перепаковывается (каталог рядом — извлечённая часть)."""
import os
import random
import subprocess
import sys

ROOT = os.environ.get("TK674_ROOT", "/data/p42-tk674c")
SAMPLE = 20


def arch_path(cell_dir):
    """`…/cells/<w>-<side>/<хеш>/<cid>` → (`…/cells-arch/<w>-<side>/<хеш>.tar.zst`, член `<хеш>/<cid>`)."""
    cell_dir = os.path.normpath(cell_dir)
    cid = os.path.basename(cell_dir)
    hd = os.path.dirname(cell_dir)
    ws = os.path.dirname(hd)
    base = os.path.dirname(ws)
    return os.path.join(os.path.dirname(base), os.path.basename(base) + "-arch", os.path.basename(ws), os.path.basename(hd) + ".tar.zst"), "%s/%s" % (os.path.basename(hd), cid)


def ensure_cell(cell_dir):
    """Каталог клетки на месте: нет — извлечь из архива партии (атомарно: во временный каталог рядом, затем rename). Возвращает путь; нет и архива — FileNotFoundError."""
    if os.path.isdir(cell_dir):
        return cell_dir
    arch, member = arch_path(cell_dir)
    if not os.path.exists(arch):
        raise FileNotFoundError("нет каталога клетки и архива партии: %s" % cell_dir)
    ws = os.path.dirname(os.path.dirname(os.path.normpath(cell_dir)))
    tmp = os.path.join(ws, ".x%d" % os.getpid())
    os.makedirs(tmp, exist_ok=True)
    try:
        subprocess.run(["tar", "--zstd", "-xf", arch, "-C", tmp, member], check=True)
        os.makedirs(os.path.dirname(cell_dir), exist_ok=True)
        if not os.path.isdir(cell_dir):
            os.rename(os.path.join(tmp, member), cell_dir)
    finally:
        subprocess.run(["rm", "-rf", tmp], check=False)
    return cell_dir


def cell_exists(cell_dir):
    """Клетка есть: каталог или архив её партии (архив пишется только для законченной партии)."""
    return os.path.isdir(cell_dir) or os.path.exists(arch_path(cell_dir)[0])


def entries(hd):
    """Число записей каталога партии: сам каталог + подкаталоги + файлы (как строки `tar -t`)."""
    n = 1
    for _, dirs, files in os.walk(hd):
        n += len(dirs) + len(files)
    return n


def pack_one(ws_dir, h, arch_dir, jobs):
    """Упаковать `<ws_dir>/<h>` → `<arch_dir>/<h>.tar.zst`; (записей, байт архива). Временный файл, rename после успеха."""
    out = os.path.join(arch_dir, h + ".tar.zst")
    tmp = out + ".part"
    os.makedirs(arch_dir, exist_ok=True)
    cmd = "tar -C %s -cf - %s | zstd -T%d -q -3 -f -o %s" % (_q(ws_dir), _q(h), jobs, _q(tmp))
    subprocess.run(["bash", "-o", "pipefail", "-c", cmd], check=True)
    os.rename(tmp, out)
    return entries(os.path.join(ws_dir, h)), os.path.getsize(out)


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


def verify_one(ws_dir, h, arch_dir, n):
    """zstd -t и число записей архива = n."""
    out = os.path.join(arch_dir, h + ".tar.zst")
    subprocess.run(["zstd", "-q", "-t", out], check=True)
    got = int(subprocess.run("tar --zstd -tf %s | wc -l" % _q(out), shell=True, check=True, capture_output=True, text=True).stdout.strip())
    if got != n:
        raise SystemExit("архив %s: записей %d ≠ %d в каталоге" % (out, got, n))


def verify_sample(ws_dir, arch_dir, hashes, k=SAMPLE, seed=674):
    """Байт в байт: k случайных файлов (хеш, клетка, файл) из каталогов против извлечённых из архива."""
    rnd = random.Random(seed)
    bad = []
    for _ in range(k):
        h = rnd.choice(hashes)
        cells = sorted(os.listdir(os.path.join(ws_dir, h)))
        c = rnd.choice(cells)
        files = sorted(os.listdir(os.path.join(ws_dir, h, c)))
        f = rnd.choice(files)
        p = os.path.join(ws_dir, h, c, f)
        arch = os.path.join(arch_dir, h + ".tar.zst")
        r = subprocess.run("tar --zstd -xOf %s %s | cmp -s - %s" % (_q(arch), _q("%s/%s/%s" % (h, c, f)), _q(p)), shell=True)
        if r.returncode:
            bad.append(p)
    return bad


def cmd_pack(ws, delete, only, jobs):
    ws_dir = os.path.join(ROOT, "cells", ws)
    arch_dir = os.path.join(ROOT, "cells-arch", ws)
    hashes = sorted(h for h in os.listdir(ws_dir) if os.path.isdir(os.path.join(ws_dir, h)) and (not only or h == only))
    done = [h for h in hashes if os.path.exists(os.path.join(arch_dir, h + ".tar.zst"))]      # готовый архив партии — не перепаковывать (каталог рядом может быть частью, извлечённой читателем)
    hashes = [h for h in hashes if h not in done]
    tot_n, tot_b, counts = 0, 0, {}
    for h in hashes:
        n, b = pack_one(ws_dir, h, arch_dir, jobs)
        counts[h] = n
        tot_n, tot_b = tot_n + n, tot_b + b
    print("%s: упаковано %d каталогов партий (архив уже был у %d), записей %d, архивы %.1f МБ" % (ws, len(hashes), len(done), tot_n, tot_b / 1e6), flush=True)
    if not hashes:
        return
    for h in hashes:
        verify_one(ws_dir, h, arch_dir, counts[h])
    bad = verify_sample(ws_dir, arch_dir, hashes)
    if bad:
        raise SystemExit("байтовая проверка не прошла: %s" % bad[:3])
    print("%s: проверка прошла (zstd -t, записи, %d файлов байт в байт)" % (ws, SAMPLE), flush=True)
    if delete:
        for h in hashes:
            subprocess.run(["rm", "-rf", os.path.join(ws_dir, h)], check=True)
        print("%s: каталоги удалены" % ws, flush=True)


def cmd_pack_batch():
    """Партия `TK674_BATCH` стороны `TK674_SIDES` (после tables + ccheck): каталоги партий из индекса `res/batches674<SUF>-<сторона>.json` (SUF — как в tk674_run) во всех трёх окнах → архивы, каталоги удаляются
    после проверки."""
    import json
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tk674_axes as ax
    batch, side = os.environ["TK674_BATCH"], os.environ["TK674_SIDES"]
    level = os.environ.get("TK674_LEVEL", "3")
    suf = sys.argv[sys.argv.index("--suf") + 1] if "--suf" in sys.argv else ax.SUF2 + level + batch      # FSUF = str(LEVEL) + BATCH (tk674_run: SUF = ax.SUF2 + FSUF); `--suf bsx` — шаг 3
    idx = json.load(open(os.path.join(ROOT, "res", "batches674%s-%s.json" % (suf, side)), encoding="utf-8"))
    for w in ("wa", "wb", "wc"):
        ws = "%s-%s" % (w, side)
        for h in sorted(idx):
            if not os.path.isdir(os.path.join(ROOT, "cells", ws, h)) and not os.path.exists(os.path.join(ROOT, "cells-arch", ws, h + ".tar.zst")):
                raise SystemExit("партия %s: нет ни каталога, ни архива %s/%s" % (batch, ws, h))
        for h in sorted(idx):
            if os.path.isdir(os.path.join(ROOT, "cells", ws, h)):
                cmd_pack(ws, True, h, 3)


def cmd_drop(ws):
    """Убрать извлечённые каталоги партий, у которых есть архив."""
    ws_dir, arch_dir = os.path.join(ROOT, "cells", ws), os.path.join(ROOT, "cells-arch", ws)
    n = 0
    for h in sorted(os.listdir(ws_dir)):
        if os.path.isdir(os.path.join(ws_dir, h)) and os.path.exists(os.path.join(arch_dir, h + ".tar.zst")):
            subprocess.run(["rm", "-rf", os.path.join(ws_dir, h)], check=True)
            n += 1
    print("%s: убрано извлечённых каталогов партий %d" % (ws, n))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "pack":
        jobs = int(a[a.index("--jobs") + 1]) if "--jobs" in a else 3
        only = a[a.index("--only") + 1] if "--only" in a else None
        cmd_pack(a[1], "--delete" in a, only, jobs)
    elif a and a[0] == "pack-batch":
        cmd_pack_batch()
    elif a and a[0] == "drop":
        cmd_drop(a[1])
    elif a and a[0] == "extract":
        print(ensure_cell(a[1]))
    else:
        raise SystemExit(__doc__)
