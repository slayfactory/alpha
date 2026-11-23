#!/usr/bin/env python3
"""TK-700: окна отложенной выборки январь–июнь 2026 (m01…m06), вселенная, охрана P&L.

Окна живут только при `TK700_WINSET=h1`: `tk604_stats` в конце модуля вызывает `apply(st)`, и таблицы окон / изоляции подменяются на H1; без переменной всё как раньше
(`ALLOWED_WINDOWS` w0/wa/wb/wc не тронут). Окно месяца = деревья TK-695 (янв–май) и TK-686 (июнь): T0 = первые зачётные сутки − 20 суток прогрева.

Охрана P&L (В-170: отложенная выборка читается один раз, после заморозки): в режиме h1 `subprocess.Popen` отказывает любой команде движка `breakout tick` на дереве H1 без `P42_SIGSX_ONLY=1`,
если нет действующего жетона замка (`TK700_PNL_TOKEN` — путь к lock.json своего замка, `TK700_PNL_NONCE` — тот же nonce). Жетон выдаёт только `tk700_holdout.py read` после гейта и замка.

  python3 tk700_win.py show                    # геометрия окон
  python3 tk700_win.py trees [--apply]         # символические ссылки /data/p42-tk621/m0N/{cache,aggtrades,klines} на деревья TK-695 / TK-686
  python3 tk700_win.py universe [--apply]      # c539 ∩ список месяца → /data/p42-tk700/universe/<окно>.txt (+ счётчики по месяцам)
  python3 tk700_win.py env <окно>              # присваивания для sh: T0, SIGSX (14:<до>), TREE, SYMS, LW — окна m01…m06 и ворот wa / wb / wc / wa20 (прогрев 20 суток)
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

WINSET_ENV = "TK700_WINSET"
TOKEN_ENV, NONCE_ENV = "TK700_PNL_TOKEN", "TK700_PNL_NONCE"
ROOT = os.environ.get("TK700_ROOT", "/data/p42-tk700")
TREE_ROOT = "/data/p42-tk621"                                   # деревья окон, которые читают скрипты TK-671 / 681 / 697 (`<корень>/<окно>/{cache,aggtrades,klines}`)
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"            # вселенная поиска (539 крипто-контрактов), через запятую
WARMUP_DAYS = 20
GRID_START_DAY = 14                                             # как у wa / wb / wc: сигналы с 14-х суток от T0 (суток до зачёта — 20, до 14-х суток дамп хранит только границы эпизодов)

# имя, месяц, T0, первые зачётные сутки, последние зачётные сутки, корень дерева, список контрактов месяца (TK-695 `plan/universe.json`; июнь — TK-686 `plan-crypto`)
_SPEC = (
    ("m01", "jan", "2025-12-12", "2026-01-01", "2026-01-31", "/data/p42-tk695/jan", "/data/p42-tk695/jan/plan/universe.json"),
    ("m02", "feb", "2026-01-12", "2026-02-01", "2026-02-28", "/data/p42-tk695/feb", "/data/p42-tk695/feb/plan/universe.json"),
    ("m03", "mar", "2026-02-09", "2026-03-01", "2026-03-31", "/data/p42-tk695/mar", "/data/p42-tk695/mar/plan/universe.json"),
    ("m04", "apr", "2026-03-12", "2026-04-01", "2026-04-30", "/data/p42-tk695/apr", "/data/p42-tk695/apr/plan/universe.json"),
    ("m05", "may", "2026-04-11", "2026-05-01", "2026-05-31", "/data/p42-tk695/may", "/data/p42-tk695/may/plan/universe.json"),
    ("m06", "jun", "2026-05-12", "2026-06-01", "2026-06-30", "/data/p42-tk686/wj", "/data/p42-tk686/plan-crypto/universe.json"),
)
NAMES = tuple(s[0] for s in _SPEC)
MONTH_TAG = {s[0]: s[1] for s in _SPEC}
LAST_DAY = datetime.date(2026, 7, 1)                            # T0 + 50 у июня; данные позже не читаются (последние зачётные сутки — 30.06)
D0 = datetime.date(2026, 1, 1)


# окна ворот (Июль–ноябрь, читать можно): T0 прежних окон; `wa20` — wa с прогревом 20 суток (T0 = 11.06; сутки до 17.06 — дерево w0 /data/p42-tk625/h775/w0)
GATE_T0 = {"wa": "2026-06-17", "wb": "2026-07-25", "wc": "2026-09-01", "wa20": "2026-06-11"}
GATE_SYMS = "/data/p42-tk621/universe/symbols-all.txt"
GATE_SIGSX_HI = 73                                              # зачётные сутки движка 14…73 (`START_DAY` + `COUNT_DAYS` − 1), как у двойников TK-671


def winset():
    return os.environ.get(WINSET_ENV, "")


def win_choices(base=("wa", "wb", "wc")):
    """Допустимые окна скриптов подготовки: прежние wa / wb / wc, а при `TK700_WINSET=h1` — окна отложенной выборки m01…m06 (таблицы окон `tk604_stats` тогда подменены на H1)."""
    return NAMES if winset() == "h1" else tuple(base)


def spec(name):
    for s in _SPEC:
        if s[0] == name:
            return dict(name=s[0], month=s[1], t0=s[2], lo=s[3], hi=s[4], root=s[5], universe=s[6])
    raise KeyError(name)


def check_spec():
    """Геометрия: T0 = lo − 20 суток, месяцы смыкаются, 181 сутки, данные до hi укладываются в T0 + 50."""
    prev = None
    for n, mt, t0, lo, hi, _, _ in _SPEC:
        t0d, lod, hid = (datetime.date.fromisoformat(x) for x in (t0, lo, hi))
        assert (lod - t0d).days == WARMUP_DAYS, (n, "прогрев", (lod - t0d).days)
        assert lod.day == 1 and (hid + datetime.timedelta(days=1)).day == 1, (n, "месяц целиком")
        assert prev is None or lod == prev + datetime.timedelta(days=1), (n, "не смыкается")
        prev = hid
    assert (prev - D0).days + 1 == 181
    return True


def sigsx_arg(name):
    """`P42_SIGSX` окна: от 14-х суток T0 до последних зачётных суток включительно."""
    s = spec(name)
    return "%d:%d" % (GRID_START_DAY, (datetime.date.fromisoformat(s["hi"]) - datetime.date.fromisoformat(s["t0"])).days)


def tree_dir(name):
    """Каталог, который читают скрипты (`<TREE_ROOT>/<окно>`): ссылки на cache / aggtrades / klines."""
    return TREE_ROOT + "/" + name


def tree_targets(name):
    s = spec(name)
    r = s["root"]
    if name == "m06":
        return {"cache": r + "/cache", "aggtrades": r + "/aggtrades", "klines": r + "/klines"}
    return {"cache": r + "/cache", "aggtrades": r + "/tree/aggtrades", "klines": r + "/tree/klines"}


def read_symbols(path):
    with open(path, encoding="utf-8") as f:
        t = f.read().strip()
    if t.startswith("["):
        return json.loads(t)
    return t.replace(",", " ").split()


def universe(name, c539=None):
    """FIX 2 критика: c539 ∩ список месяца (TradFi уже снят в списке месяца); вне c539 — ни одного контракта."""
    c = set(c539 if c539 is not None else read_symbols(C539))
    return sorted(c & set(read_symbols(spec(name)["universe"])))


def universe_file(name):
    return ROOT + "/universe/" + name + ".txt"


def apply(st):
    """Подмена таблиц окон в `tk604_stats` (вызывается из его конца при `TK700_WINSET=h1`); ставит охрану P&L."""
    check_spec()
    ws = tuple(st.Window(n, t0, lo, hi) for n, _, t0, lo, hi, _, _ in _SPEC)
    st.D0, st.LAST_DAY = D0, LAST_DAY
    st.MONTH_NAMES, st.MONTH_LEN = ("jan", "feb", "mar", "apr", "may", "jun"), (31, 28, 31, 30, 31, 30)
    st.NDAYS = 181
    st.MONTH_OF_DAY = st.np.repeat(st.np.arange(6), st.MONTH_LEN)
    st.MIN_TRADES = (20,) * 6
    st.TRADE_WINDOWS = st.HIST_WINDOWS = ws
    st.ALLOWED_WINDOWS = NAMES
    st.GRID_START_DAY, st.COUNT_DAYS = GRID_START_DAY, 37
    st.TRADE_END = st.GRID_START_DAY + st.COUNT_DAYS - 1                # 50: самый длинный интервал T0…hi (янв., мар.)
    st.LAST_MS = int(datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    st.check_windows(ws)
    install_fence()


# ---- охрана P&L -------------------------------------------------------------------------------------------------------------------

H1_PATH_MARKERS = tuple("/%s/" % n for n in NAMES) + ("/data/p42-tk700/", "/data/p42-tk695/", "/data/p42-tk686/wj")


class FenceError(Exception):
    pass


def token_ok(env=None):
    e = os.environ if env is None else env
    p, nonce = e.get(TOKEN_ENV), e.get(NONCE_ENV)
    if not p or not nonce or not os.path.exists(p):
        return False
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return False
    return j.get("nonce") == nonce and j.get("state") == "started"


def check_cmd(args, env=None):
    """Команда движка на дереве H1 без `P42_SIGSX_ONLY=1` и без жетона замка — отказ (в т. ч. внутри `bash -c "…"`)."""
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
    else:
        text = " ".join(os.fsdecode(a) for a in args)
    if "breakout" not in text or " tick" not in text:
        return
    if not any(m in text for m in H1_PATH_MARKERS):
        return
    e = dict(os.environ)
    e.update(env or {})
    if "P42_SIGSX_ONLY=1" in text or e.get("P42_SIGSX_ONLY") == "1":
        return
    if token_ok(e):
        return
    raise FenceError("TK-700: прогон движка с P&L на дереве отложенной выборки (январь–июнь) без замка одного чтения: %s" % text[:200])


_installed = False


def install_fence():
    global _installed
    if _installed:
        return
    orig = subprocess.Popen.__init__

    def init(self, args, *a, **kw):
        check_cmd(args, kw.get("env"))
        orig(self, args, *a, **kw)
    subprocess.Popen.__init__ = init
    _installed = True


# ---- служебные команды ------------------------------------------------------------------------------------------------------------

def env_of(name):
    """Переменные для sh-драйверов подготовки: окно ворот или месяц H1."""
    if name in GATE_T0:
        t0 = GATE_T0[name]
        tree = tree_dir(name)
        syms, sigsx = GATE_SYMS, "%d:%d" % (GRID_START_DAY, GATE_SIGSX_HI)
    else:
        s = spec(name)
        t0, tree, syms, sigsx = s["t0"], tree_dir(name), universe_file(name), sigsx_arg(name)
    lw = (7 - datetime.date.fromisoformat(t0).weekday()) % 7
    return {"T0": t0, "SIGSX": sigsx, "TREE": tree, "SYMS": syms, "LW": str(lw)}


def cmd_env(name):
    for k, v in env_of(name).items():
        print("%s=%s" % (k, v))


def cmd_show():
    check_spec()
    for n, mt, t0, lo, hi, root, _ in _SPEC:
        print(n, mt, "T0", t0, "зачёт", lo, hi, "SIGSX", sigsx_arg(n), root)


def cmd_trees(apply_):
    for n in NAMES:
        d = tree_dir(n)
        for k, tgt in tree_targets(n).items():
            ok = os.path.isdir(tgt)
            print(("ok " if ok else "НЕТ"), d + "/" + k, "->", tgt)
            if apply_ and ok:
                os.makedirs(d, exist_ok=True)
                link = os.path.join(d, k)
                if os.path.islink(link) and os.readlink(link) == tgt:
                    continue
                if os.path.lexists(link):
                    raise SystemExit("%s уже существует и не наша ссылка" % link)
                os.symlink(tgt, link)


def cmd_universe(apply_):
    c = read_symbols(C539)
    out = {}
    for n in NAMES:
        u = universe(n, c)
        out[n] = len(u)
        print(n, MONTH_TAG[n], "список месяца", len(read_symbols(spec(n)["universe"])), "∩ c539", len(u))
        if apply_:
            os.makedirs(os.path.dirname(universe_file(n)), exist_ok=True)
            with open(universe_file(n), "w", encoding="utf-8", newline="\n") as f:
                f.write(",".join(u) + "\n")
    if apply_:
        with open(os.path.join(ROOT, "universe", "counts.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] not in ("show", "trees", "universe", "env"):
        raise SystemExit(__doc__)
    if a[0] == "env":
        cmd_env(a[1])
    elif a[0] == "show":
        cmd_show()
    elif a[0] == "trees":
        cmd_trees("--apply" in a)
    else:
        cmd_universe("--apply" in a)
