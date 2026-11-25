#!/usr/bin/env python3
"""TK-700: замок «одно чтение» отложенной выборки (январь–июнь 2026, В-170).

Ключ замка — sha256 файла заморозки. `<корень>/lock/<sha>` создаётся `os.mkdir` (атомарно): есть каталог — отказ, в любом состоянии (в том числе оборванный сразу после `mkdir`, до записи `lock.json`).
`lock.json`: sha, pid, nonce, state (claimed → started → done | failed), метки времени. Жетон для охраны движка (`tk700_win.token_ok`) — путь к `lock.json` + nonce, действует только в состоянии `started`.
Повторное чтение — только `reopen --chron <id>`: каталог замка переименовывается в `<sha>.reopened-<id>` (след остаётся), в `reopen.json` — запись летописи с решением координатора.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets
import socket

STATES = ("claimed", "started", "done", "failed")


class LockError(Exception):
    pass


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lock_dir(root, sha):
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise LockError("sha заморозки должен быть 64 шестнадцатеричными знаками: %r" % sha)
    return os.path.join(root, "lock", sha)


def _write_json(path, obj, exclusive=False):
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    if exclusive:
        os.link(tmp, path)                       # без перезаписи: `FileExistsError`, если файл уже есть
        os.unlink(tmp)
    else:
        os.replace(tmp, path)


def read_lock(root, sha):
    p = os.path.join(lock_dir(root, sha), "lock.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def claim(root, sha, info=None):
    """Одно чтение: атомарный `mkdir`; каталог уже есть → отказ (даже без `lock.json`: оборванный захват остаётся отказанным). Возвращает (путь lock.json, nonce)."""
    d = lock_dir(root, sha)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    try:
        os.mkdir(d)
    except FileExistsError:
        st = read_lock(root, sha)
        raise LockError("заморозка %s… уже читалась или читается (замок %s, состояние %s): повторное чтение — только решением координатора, `reopen --chron <запись летописи>`" % (
            sha[:12], d, st["state"] if st else "оборван после mkdir"))
    nonce = secrets.token_hex(16)
    lj = {"sha": sha, "pid": os.getpid(), "host": socket.gethostname(), "nonce": nonce, "state": "claimed", "claimed_utc": utc_now(), "info": info or {}}
    _write_json(os.path.join(d, "lock.json"), lj, exclusive=True)
    return os.path.join(d, "lock.json"), nonce


def set_state(root, sha, nonce, state, extra=None):
    if state not in STATES:
        raise LockError("состояние замка: %s" % state)
    lj = read_lock(root, sha)
    if lj is None or lj.get("nonce") != nonce:
        raise LockError("замок %s: чужой nonce или нет lock.json" % sha[:12])
    order = {s: i for i, s in enumerate(STATES)}
    if lj["state"] in ("done", "failed") or (state != "failed" and order[state] <= order[lj["state"]]):
        raise LockError("замок %s: переход %s → %s не допускается" % (sha[:12], lj["state"], state))
    lj["state"] = state
    lj[state + "_utc"] = utc_now()
    if extra:
        lj.update(extra)
    _write_json(os.path.join(lock_dir(root, sha), "lock.json"), lj)
    return lj


def token_env(lock_json_path, nonce):
    """Окружение для движка: жетон охраны `tk700_win.token_ok` (действует, пока state = started)."""
    return {"TK700_PNL_TOKEN": lock_json_path, "TK700_PNL_NONCE": nonce}


def reopen(root, sha, chron_id, note=""):
    """Повтор чтения: только с идентификатором записи летописи (решение координатора). Каталог замка уходит в `<sha>.reopened-<id>-<n>`; новый `claim` становится возможным."""
    if not chron_id or not re.fullmatch(r"[A-Za-z0-9_.:-]+", chron_id):
        raise LockError("reopen: нужен --chron <идентификатор записи летописи с решением координатора>")
    d = lock_dir(root, sha)
    if not os.path.isdir(d):
        raise LockError("reopen: замка %s нет" % sha[:12])
    n = 1
    while os.path.exists("%s.reopened-%s-%d" % (d, chron_id, n)):
        n += 1
    dst = "%s.reopened-%s-%d" % (d, chron_id, n)
    os.rename(d, dst)
    _write_json(os.path.join(dst, "reopen.json"), {"sha": sha, "chron": chron_id, "note": note, "utc": utc_now(), "pid": os.getpid()})
    return dst
