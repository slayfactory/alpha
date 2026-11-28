#!/usr/bin/env python3
"""TK-745: упаковка журнала заявок и уровней сетапа движка (TK-743, `breakout tick --keep-orders`) в файлы сделок страницы :8090 «Сделки». Только srv; движок не запускает.

Вход — `<корень>/<префикс>-<h1|sel|n1>/<окно>/<cid>-{orders,setup}.csv`; ключ сделки — (окно, монета, номер уровня, сигнал от начала окна), как в `tk741_lib.read_win`.
Время в журнале — мс от начала окна (`t0.json`), в файле сделки — мс от `t0` файла минуток (`off` = t0 окна − t0 файла).

Формат в файле сделки (читает trades-app, `lib/orders.ts`):
  ord — заявки: [роль, лот, тип, доля ‰, вид конца f|c|o, время конца, цена конца ×1e8, причина, время срабатывания условия, путь, доля исполнения ‰ (0 — не исполнена; у стопа и rt после тейков меньше доли заявки)]; путь — плоский [время, цена ×1e8, код, …]: выставление и каждый пересчёт цены
        (код 0 — выставлена, 1 — трейл, 2 — остаток после тейков, 3 — прочий пересчёт); вид конца: f — исполнена, c — снята, o — без конца в журнале;
  lv  — уровни сетапа: L торгуемый уровень [цена, рождение, конец, причина конца, номер бара], V версия цены [цена, с, до, активная, №], Z зона [ближний край, нижний край зоны входа, дальний край, с, до],
        T пивот-член [цена, бар с, бар до], G выпавший член, X следующий уровень [цена, с], O прочий уровень на сигнале [цена, с, до, состояние, время состояния, № уровня, причина конца].
"""
from __future__ import annotations

import csv
import json
import re

# причины движка → короткие коды (на странице — русские подписи); неизвестная причина остаётся строкой как есть
NOTE_CODE = {"": "", "position closed": "pc", "lot 1 closed first": "l1", "price reached the level before the e2 distance": "pr", "rejected: no tape or beyond the level": "rj",
             "immediate: lot 1 entry already inside the e2 distance": "im", "later: price came within the e2 distance": "lt", "rt": "rt", "stop": "stop", "trail": "trail", "be": "be", "rest": "rest"}


def grp(role):
    """Группа легенды страницы: e1, e2, stop, rt, take (take1…takeN), other (nx, tu, tx, trail_limit, exit)."""
    return role if role in ("e1", "e2", "stop", "rt") else "take" if role.startswith("take") else "other"


def part(win):
    return "h1" if win.startswith("m") else "n1" if win == "n1" else "sel"


def load_orderlog(root, prefix, cid, t0, wins):
    """(окно, монета, уровень, сигнал от начала окна) → ([строки orders], [строки setup]); t0 окон журнала обязан совпасть с t0 сделок."""
    out = {}
    for w in wins:
        d = "%s/%s-%s" % (root, prefix, part(w))
        t0w = json.load(open(d + "/t0.json", encoding="utf-8"))[w]
        assert int(t0w) == t0[w], ("t0 окна журнала не равен t0 сделок", w, t0w, t0[w])
        for k, fn in enumerate(("orders", "setup")):
            with open("%s/%s/%d-%s.csv" % (d, w, cid, fn), encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    out.setdefault((w, r["sym"], int(r["level"]), int(r["sig_ms"])), ([], []))[k].append(r)
    return out


def pack_orders(rows, off):
    od = {}
    for r in rows:
        od.setdefault((int(r["lot"]), int(r["oid"]), r["role"]), []).append(r)
    out = []
    for (lot, oid, role), ev in od.items():
        ev.sort(key=lambda r: int(r["ms"]))
        assert ev[0]["event"] == "place", (role, lot, oid, ev[0]["event"])
        path = []
        for r in ev:
            if r["event"] == "place":
                path += [int(r["ms"]) + off, int(r["px_e8"]), 0]
            elif r["event"] == "reprice":
                path += [int(r["ms"]) + off, int(r["px_e8"]), {"trail": 1, "rest": 2}.get(r["note"], 3)]
        ends = [r for r in ev if r["event"] in ("fill", "cancel")]
        assert len(ends) <= 1 and (not ends or ends[0] is ev[-1]), ("заявка с двумя концами или событием после конца", role, lot, oid)
        end = ends[0] if ends else None
        out.append([role, lot, ev[0]["type"], int(ev[0]["share_pm"]), "o" if end is None else "f" if end["event"] == "fill" else "c",
                    0 if end is None else int(end["ms"]) + off, 0 if end is None else int(end["px_e8"]), "" if end is None else NOTE_CODE.get(end["note"], end["note"]),
                    int(end["aux_ms"]) + off if end is not None and end["event"] == "fill" and int(end["aux_ms"]) else 0, path,
                    int(end["share_pm"]) if end is not None and end["event"] == "fill" else 0])
    return out


# TK-747 (`lm`): строки `setup.csv` для разбора сделок — пивоты с минутами экстремума и подтверждения, проколы основного и внутренних, возраст сетапа, тик и допуск, касание, проторговка
DIAG_ROLES = ("main_pierce", "inner", "age", "tick", "first_touch", "consol")


def pack_diag(rows, off):
    """TK-747: строки разбора сделки — [роль, цена ×1e8, с, до, текст]; пивоты (`touch`) — только с минутами экстремума и подтверждения (включён `lm`). Времена в строках текста — мс от 01.09 UTC (как в CSV)."""
    ms = lambda v: None if v == "" else int(v) + off
    return [[r["role"], abs(int(r["px_e8"])), ms(r["from_ms"]), ms(r["to_ms"]), r["reason"]]
            for r in rows if r["role"] in DIAG_ROLES or (r["role"] == "touch" and "confirm_ms=" in r["reason"])]


def pack_setup(rows, off):
    ms = lambda v: None if v == "" else int(v) + off
    zone, out = {}, []
    for r in rows:
        role, px, a, b, why = r["role"], abs(int(r["px_e8"])), ms(r["from_ms"]), ms(r["to_ms"]), r["reason"]
        if role == "level":
            m = re.search(r"first_bar=(\d+);end=(\w+)", why)
            out.append(["L", px, a, b, m.group(2), int(m.group(1))])
        elif role == "ver":
            out.append(["V", px, a, b, 1 if why.endswith("active") else 0, int(why.split(";")[0][1:])])
        elif role in ("zone_near", "zone_entry", "zone_far"):
            zone[role] = (px, a, b)
        elif role == "touch":
            out.append(["T", px, a, b])
        elif role == "touch_gone":
            out.append(["G", px, a, b])
        elif role == "next":
            out.append(["X", px, a])
        elif role == "other":
            m = re.match(r"(live|pk|pb|ban|consumed)(?:@(\d+))?;level=(\d+);end=(\w+)", why)
            assert m, why
            out.append(["O", px, a, b, m.group(1), int(m.group(2)) + off if m.group(2) else None, int(m.group(3)), m.group(4)])
        elif role in DIAG_ROLES:
            continue      # TK-747: строки для разбора (текст, не линии графика) — в `dg` файла сделки (`pack_diag`)
        else:
            raise AssertionError("роль setup " + role)
    if zone:
        n, e, f = zone["zone_near"], zone["zone_entry"], zone["zone_far"]
        out.insert(0, ["Z", n[0], e[0], f[0], n[1], n[2]])
    return out


def count_orderlog(orows, srows):
    """Счёт по CSV напрямую (без упаковки) для клик-теста: по группам {группа: [заявок, отрезков (выставлено + пересчётов), исполнено, снято]}; по уровням — число строк по роли."""
    g = {}
    for r in orows:
        c = g.setdefault(grp(r["role"]), [0, 0, 0, 0])
        e = r["event"]
        if e == "place":
            c[0] += 1
            c[1] += 1
        elif e == "reprice":
            c[1] += 1
        elif e == "fill":
            c[2] += 1
        elif e == "cancel":
            c[3] += 1
    lv = {}
    for r in srows:
        if r["role"] in DIAG_ROLES:
            continue      # строки разбора (TK-747) в `lv` файла сделки не входят
        lv[r["role"]] = lv.get(r["role"], 0) + 1
    return {"g": g, "lv": lv}
