#!/usr/bin/env python3
"""П-42, TK-683: гейт «байт в байт» ускоренного бинаря движка перед подменой между партиями карты TK-674.
  TK674_ROOT=/data/p42-tk683 TK674_BIN=<новый бинарь> python3 tk683_gate.py [--per-batch 4] [--threads 2] [--cells-root /data/p42-tk674 ...]
  c539 (TradFi-файлы удалены, баз h775 нет): TK674_UNI=c539 … --code /data/p42-tk674c/code --no-gbase --tag <имя> --cells-root /data/p42-tk674c … --full-root /data/p42-tk680n-g
  (`--full-root` — все партии и все клетки каталога: вызовы с полным набором проходов сигналов, как в счёте).
1) G-base: 6 строк баз (3 окна × L, S) = готовым базам h775 (`tk674_run.cmd_gates`) → <ROOT>/res/gbase674.json (его берёт TK674_GBASE);
2) выборка уже посчитанных клеток карты: на окно и сторону — две последние партии без фильтра с вариантами `e2t` / `mk` (путь `tick_at`),
   самая старая партия без фильтра (прежний бинарь) и последняя партия с фильтром, чья таблица `--ftab` определена однозначно и с тех пор не переписана; клетка пересчитывается новым бинарём на полной вселенной,
   все файлы каталога клетки сравниваются с посчитанными. Итог — <ROOT>/res/gate683.json, код возврата 1 при любом различии.
Чужие каталоги только читаются. P&L не печатается.
TK-706 (G1, критик): набор гейта дрейфует вслед за производством (`pick()` берёт «последние партии» — клеток tk674c 36 → 3, tick_at 12 против 30), поэтому сверка ведётся по ЗАМОРОЖЕННОМУ списку:
  --freeze <файл.json> --from-gate res/gate683….json … --fresh-root <каталог карты>   пишет список и копию посчитанных клеток (<файл>.ref/; клетки, которые цепочка убрала, берутся из cells-arch/*.tar.zst);
  --list <файл.json> --tag <имя>                                                       сверяет бинарь с этим списком, без выборки (один и тот же список — для каждого бинаря)."""
from __future__ import annotations

import argparse
import filecmp
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

AP = argparse.ArgumentParser()
AP.add_argument("--per-batch", type=int, default=4)
AP.add_argument("--threads", type=int, default=2)
AP.add_argument("--cells-root", nargs="*", default=["/data/p42-tk674"])
AP.add_argument("--full-root", nargs="*", default=[], help="каталоги карты, чьи партии сверяются целиком (все партии, все клетки)")
AP.add_argument("--cap-gb", type=int, default=2, help="память движка под сутки сделок (`--cap-gb`; у движка по умолчанию 24 ГБ); на результат не влияет")
AP.add_argument("--sig-cache", default="", help="каталог кэша сигналов движка (`--sig-cache`; в нём же файлы ступеней НОД)")
AP.add_argument("--code", default=os.path.dirname(os.path.abspath(__file__)), help="каталог с tk674_run.py")
AP.add_argument("--no-gbase", action="store_true", help="без сверки баз h775 (их файлов больше нет)")
AP.add_argument("--tag", default="", help="имя прогона: каталог gate/<tag>/…, итог res/gate683<tag>.json")
AP.add_argument("--freeze", default="", help="не считает: пишет замороженный список гейта (корень, окно-сторона, партия, ftab, клетки) и копию посчитанных клеток в <файл без .json>.ref/")
AP.add_argument("--list", default="", help="сверка по замороженному списку (`--freeze`), без выборки")
AP.add_argument("--from-gate", nargs="*", default=[], help="для --freeze: res/gate683*.json прежних гейтов; их клетки (кроме убранных цепочкой) входят в список")
AP.add_argument("--fresh-root", default="", help="для --freeze: каталог текущей карты, из последних партий которого берутся свежие клетки")
AP.add_argument("--fresh-batches", type=int, default=6, help="для --freeze: сколько последних партий на окно-сторону брать у --fresh-root")
AP.add_argument("--fresh-hours", type=float, default=36, help="для --freeze: брать только партии, чья отметка готовности не старше стольких часов (ступень, идущая сейчас)")
AP.add_argument("--fresh-per-batch", type=int, default=3, help="для --freeze: клеток на партию (сначала с tick_at, одна без)")
A = AP.parse_args()
sys.path.insert(0, A.code)
import tk674_run as m  # noqa: E402

t, say = m.t, m.say


def last(line, key):
    v = re.findall(r" %s=(\S+)" % key, line)
    return float(v[-1]) if v else 0.0


def tick_at_line(line):
    return last(line, "e2t") > 0 or last(line, "mk") > 0


_IDX = {}


def ftab_for(cells_root, ws, nm, lines, marker_mtime):
    """Таблица фильтров партии: та `ftab674<уровень>`, чья карта «смысл фильтра → столбец» (res/ftabcols674*) даёт столбцы `fa` всех строк партии
    (ключи клеток партии — res/batches674*); годится, только если единственная и с отметки готовности партии не переписана."""
    side = ws.split("-")[1]
    if side not in _IDX:
        _IDX[side] = {}
        for f in glob.glob(os.path.join(cells_root, "res", "batches674*-%s.json" % side)):
            _IDX[side].update(json.load(open(f, encoding="utf-8")))
    cks = _IDX[side].get(nm)
    if not cks or len(cks) != len(lines) or not all("F=" in k for k in cks):
        return None
    fa = [re.findall(r" fa=(\S+)", line)[-1] for _, line in lines]
    ok = []
    for f in glob.glob(os.path.join(cells_root, "res", "ftabcols674*-%s.json" % side)):
        cols = json.load(open(f, encoding="utf-8"))
        if all(cols.get(k.split("F=", 1)[1]) == c for k, c in zip(cks, fa)):
            ft = os.path.join(cells_root, "ftab674" + os.path.basename(f)[len("ftabcols674"):-len("-%s.json" % side)], ws + ".csv")
            if os.path.exists(ft) and max(os.path.getmtime(ft), os.path.getmtime(f)) < marker_mtime:
                ok.append(ft)
    return ok[0] if len(ok) == 1 else None


def pick(cells_root, ws, per_batch, full=False):
    """[(партия, ftab | None, [(cid, строка)])] для окна-стороны `ws`."""
    marks = sorted((os.path.getmtime(f), os.path.basename(f)[len(ws) + 1:]) for f in glob.glob(os.path.join(cells_root, "done", ws + "-*")))
    lines = {}

    def load(nm):
        if nm not in lines:
            vf = os.path.join(cells_root, "cells", ws, nm + ".variants.txt")
            lines[nm] = [tuple(x.rstrip("\n").split(" ", 1)) for x in open(vf, encoding="utf-8") if x.strip()] if os.path.exists(vf) else []
        return lines[nm]

    def some(nm):
        ls = [x for x in load(nm) if os.path.isdir(os.path.join(cells_root, "cells", ws, nm, x[0]))]
        hot = [x for x in ls if tick_at_line(x[1])]
        cold = [x for x in ls if not tick_at_line(x[1])]
        return hot[:max(per_batch - 1, 1)] + cold[:1] if hot else cold[:per_batch]

    if full:
        return [x for x in ((nm, ftab_for(cells_root, ws, nm, load(nm), mt) if nm.startswith("f") else None,
                             [v for v in load(nm) if os.path.isdir(os.path.join(cells_root, "cells", ws, nm, v[0]))]) for mt, nm in marks)
                if x[2] and (x[1] or not x[0].startswith("f"))]
    out, seen = [], set()
    plain = [(mt, nm) for mt, nm in marks if nm.startswith("p")]
    hot = [(mt, nm) for mt, nm in plain if any(tick_at_line(x[1]) for x in load(nm))]
    for mt, nm in hot[-2:] + plain[:1]:
        if nm not in seen:
            seen.add(nm)
            out.append((nm, None, some(nm)))
    for mt, nm in reversed([x for x in marks if x[1].startswith("f")]):
        ft = ftab_for(cells_root, ws, nm, load(nm), mt)
        if ft:
            out.append((nm, ft, some(nm)))
            break
    return [x for x in out if x[2]]


def refname(a):
    return (a[:-5] if a.endswith(".json") else a) + ".ref"


def arch_path(root, ws, nm):
    return os.path.join(root, "cells-arch", ws, nm + ".tar.zst")


def members(root, ws, nm):
    """клетки партии, у которых есть посчитанные файлы: каталог партии и (или) архив `cells-arch` (цепочка убирает каталоги клеток после архивации)."""
    out = set()
    d = os.path.join(root, "cells", ws, nm)
    if os.path.isdir(d):
        out |= set(os.listdir(d))
    ar = arch_path(root, ws, nm)
    if os.path.exists(ar):
        lst = subprocess.run(["tar", "--zstd", "-tf", ar], capture_output=True, text=True, check=True).stdout.split("\n")
        out |= {p.split("/")[1] for p in lst if p.count("/") == 2 and p.endswith("/")}
    return out


def put_ref(root, ws, nm, cids, dst):
    """копия посчитанных клеток партии в `dst/<корень>/<окно-сторона>/<партия>/<клетка>`: из каталога, иначе из архива."""
    rn = os.path.basename(root.rstrip("/"))
    base = os.path.join(dst, rn, ws, nm)
    os.makedirs(base, exist_ok=True)
    need = []
    for cid in cids:
        d = os.path.join(base, cid)
        s = os.path.join(root, "cells", ws, nm, cid)
        if os.path.isdir(d):
            continue
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            need.append(cid)
    if need:
        tmp = os.path.join(dst, ".x")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        subprocess.run(["tar", "--zstd", "--no-wildcards", "-xf", arch_path(root, ws, nm), "-C", tmp] + [nm + "/" + c for c in need], check=True)
        for c in need:
            shutil.move(os.path.join(tmp, nm, c), os.path.join(base, c))
        shutil.rmtree(tmp)


def freeze(a):
    """Замороженный список: клетки прежних гейтов (кроме убранных цепочкой, если их не достать из архива) + свежие клетки текущей карты; ничего не считает."""
    st, _r5, _, _ = t._srv()
    paths = {os.path.basename(r.rstrip("/")): r for r in a.cells_root + a.full_root}
    if a.fresh_root:
        paths[os.path.basename(a.fresh_root.rstrip("/"))] = a.fresh_root
    ref = refname(a.freeze)
    ents, lost, seen = {}, [], set()           # (корень, окно-сторона, партия) -> {cid: строка}

    _var = {}

    def var(rp, ws, nm):
        k = (rp, ws, nm)
        if k not in _var:
            vf = os.path.join(rp, "cells", ws, nm + ".variants.txt")
            _var[k] = dict(tuple(x.rstrip("\n").split(" ", 1)) for x in open(vf, encoding="utf-8") if x.strip()) if os.path.exists(vf) else {}
        return _var[k]

    def mark_mtime(rp, ws, nm):
        return os.path.getmtime(os.path.join(rp, "done", "%s-%s" % (ws, nm)))

    def add(rn, ws, nm, cid):
        if (rn, ws, nm, cid) in seen:
            return
        seen.add((rn, ws, nm, cid))
        ents.setdefault((rn, ws, nm), {})[cid] = None

    for gj in a.from_gate:
        for c in json.load(open(gj, encoding="utf-8"))["cells"]:
            add(c["root"], c["ws"], c["batch"], c["cid"])
    n_old = len(seen)
    if a.fresh_root:
        rn = os.path.basename(a.fresh_root.rstrip("/"))
        for w in st.TRADE_WINDOWS:
            for side in m.SIDES:
                ws = "%s-%s" % (w.name, side)
                marks = sorted((os.path.getmtime(f), os.path.basename(f)[len(ws) + 1:]) for f in glob.glob(os.path.join(a.fresh_root, "done", ws + "-*")))
                for mt, nm in [x for x in marks if x[0] > time.time() - a.fresh_hours * 3600][-a.fresh_batches:]:
                    have = members(a.fresh_root, ws, nm)
                    ls = [(cid, line) for cid, line in var(a.fresh_root, ws, nm).items() if cid in have]
                    hot = [x for x in ls if tick_at_line(x[1])]
                    cold = [x for x in ls if not tick_at_line(x[1])]
                    sel = hot[:max(a.fresh_per_batch - 1, 1)] + cold[:1] if hot else cold[:a.fresh_per_batch]
                    for cid, _ in sel:
                        add(rn, ws, nm, cid)
    # строки вариантов, таблицы фильтров, копия посчитанных клеток
    entries, n, n_hot, n_filt, per = [], 0, 0, 0, {}
    for (rn, ws, nm), cids in sorted(ents.items()):
        rp = paths[rn]
        have = members(rp, ws, nm)
        vs = var(rp, ws, nm)
        keep = [c for c in cids if c in have and c in vs]
        lost += ["%s/%s/%s/%s" % (rn, ws, nm, c) for c in cids if c not in keep]
        if not keep:
            continue
        ftab = None
        if nm.startswith("f"):
            _IDX.clear()
            ft = ftab_for(rp, ws, nm, list(vs.items()), mark_mtime(rp, ws, nm))
            if not ft:
                lost += ["%s/%s/%s/%s (нет однозначной таблицы фильтров)" % (rn, ws, nm, c) for c in keep]
                continue
            os.makedirs(os.path.join(ref, "ftab", rn, ws), exist_ok=True)
            ftab = os.path.join("ftab", rn, ws, nm + ".csv")
            shutil.copy2(ft, os.path.join(ref, ftab))
        put_ref(rp, ws, nm, keep, ref)
        hot = [tick_at_line(vs[c]) for c in keep]
        entries.append(dict(root=rn, ws=ws, batch=nm, ftab=ftab, cells=[[c, vs[c], h] for c, h in zip(keep, hot)]))
        n += len(keep)
        n_hot += sum(hot)
        n_filt += len(keep) if ftab else 0
        per[rn] = per.get(rn, 0) + len(keep)
    json.dump(dict(created=time.strftime("%Y-%m-%d %H:%M:%S"), from_gate=a.from_gate, fresh_root=a.fresh_root, ref=ref, lost=lost, entries=entries),
              open(a.freeze, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("freeze: клеток %d (из прежних гейтов %d, свежих %d; tick_at %d, с фильтром %d; по корням %s), недоступных %d%s" % (
        n, n_old, len(seen) - n_old, n_hot, n_filt, per, len(lost), "".join("\n   " + x for x in lost[:30])))


def jobs_list(a):
    """(корень, окно-сторона, партия, ftab, [(cid, строка)], каталог посчитанных клеток партии) из замороженного списка."""
    fr = json.load(open(a.list, encoding="utf-8"))
    ref = refname(a.list)
    out = []
    for e in fr["entries"]:
        base = os.path.join(ref, e["root"], e["ws"], e["batch"])
        vs = [(c, line) for c, line, _ in e["cells"]]
        miss = [c for c, _ in vs if not os.path.isdir(os.path.join(base, c))]
        if miss:
            raise SystemExit("в копии посчитанных клеток нет %d клеток партии %s/%s/%s (%s…)" % (len(miss), e["root"], e["ws"], e["batch"], miss[0]))
        out.append((e["root"], e["ws"], e["batch"], os.path.join(ref, e["ftab"]) if e["ftab"] else None, vs, base))
    return out


def main():
    a = A
    if a.freeze:
        freeze(a)
        return
    roots = [(r, False) for r in a.cells_root] + [(r, True) for r in a.full_root]
    if any(os.path.realpath(m.ROOT) == os.path.realpath(r) for r, _ in roots):
        raise SystemExit("TK674_ROOT гейта должен отличаться от каталога карты (чужие файлы не перезаписываются)")
    st, r5, _, _ = t._srv()
    import tk604_run as r4
    r4.THREADS = a.threads
    r4.BIN = m.BIN                              # без G-base (`cmd_gates`) бинарь раннеру задаётся здесь
    r5.TICK_STORE = list(r5.TICK_STORE) + ["--cap-gb", str(a.cap_gb)]
    if a.sig_cache:
        r5.TICK_STORE += ["--sig-cache", a.sig_cache]
    if not a.no_gbase:
        m.cmd_gates(a)                          # G-base: при различии — выход с кодом 1
    u = r5.Uni(m.UNI)
    res, bad, gone, n = [], [], [], 0
    if a.list:
        jobs = jobs_list(a)
        say("gate по замороженному списку %s: партий %d, клеток %d" % (a.list, len(jobs), sum(len(j[4]) for j in jobs)))
    else:
        jobs = []
        for root, full in roots:
            _IDX.clear()
            rn = os.path.basename(root.rstrip("/"))
            for w in st.TRADE_WINDOWS:
                for side in m.SIDES:
                    ws = "%s-%s" % (w.name, side)
                    for nm, ftab, vs in pick(root, ws, a.per_batch, full):
                        jobs.append((rn, ws, nm, ftab, vs, os.path.join(root, "cells", ws, nm)))
    for rn, ws, nm, ftab, vs, oldbase in jobs:
        w = next(x for x in st.TRADE_WINDOWS if x.name == ws.split("-")[0])
        side = ws.split("-")[1]
        out = os.path.join(m.ROOT, "gate", a.tag, rn, ws, nm) if a.tag else os.path.join(m.ROOT, "gate", ws, nm)
        r5.tick(u, w, vs, out, m.one_side_env(side), t.q("log", "gate%s-%s-%s-%s.log" % (a.tag, rn, ws, nm)), ftab=ftab)
        n0 = len(res)
        for cid, line in vs:
            old, new = os.path.join(oldbase, cid), os.path.join(out, cid)
            try:
                files = sorted(os.listdir(old))     # файл, которого у прежнего бинаря не было (lots.csv до TK-677), — в `extra`, не различие
                diff = [f for f in files if not (os.path.exists(os.path.join(new, f)) and filecmp.cmp(os.path.join(old, f), os.path.join(new, f), shallow=False))]
            except FileNotFoundError:           # TK-706: каталог клетки убрала загрузка таблиц цепочки, пока шёл гейт — клетка не сверена
                gone.append("%s/%s/%s/%s" % (rn, ws, nm, cid))
                continue
            n += 1
            res.append(dict(root=rn, ws=ws, batch=nm, cid=cid, tick_at=tick_at_line(line), filt=bool(ftab), files=len(files), diff=diff,
                            extra=sorted(set(os.listdir(new)) - set(files)) if os.path.isdir(new) else []))
            if diff:
                bad.append("%s/%s/%s/%s: %s" % (rn, ws, nm, cid, ", ".join(diff)))
        say("gate %s %s %s: клеток %d (убрано цепочкой %d), %s" % (rn, ws, nm, len(res) - n0, len(vs) - (len(res) - n0), "байт в байт" if not any(r["diff"] for r in res[n0:]) else "РАЗЛИЧИЯ"))
    json.dump(dict(bin=m.BIN, bin_md5=m.bin_md5(m.BIN), bin_sha256=r4.sha256(m.BIN), list=a.list, cells=res, bad=bad, gone=gone),
              open(t.q("res", "gate683%s.json" % a.tag), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("gate683: клеток %d (из них с tick_at %d, с фильтром %d), файлов %d, клеток с новым файлом %d, не сверено (убраны цепочкой) %d, различий %d%s" % (
        n, sum(r["tick_at"] for r in res), sum(r["filt"] for r in res), sum(r["files"] for r in res), sum(bool(r["extra"]) for r in res), len(gone), len(bad), "".join("\n   " + x for x in bad[:20])))
    if bad or (a.list and gone):
        sys.exit(1)


if __name__ == "__main__":
    main()
