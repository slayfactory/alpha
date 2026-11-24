#!/usr/bin/env python3
"""Перенос устаревших документов в docs/archive/ с правкой ссылок (TK-705).

  python tools/docs_archive.py plan  <путь>... [--keep ФАЙЛ] [--freeze ФАЙЛ] [--tsv ВЫХОД]
  python tools/docs_archive.py apply <путь>... [--keep ФАЙЛ] [--freeze ФАЙЛ] [--tsv ВЫХОД]
  python tools/docs_archive.py lists <каталог> [--branches ТК,...]   # пересобрать keep.txt и freeze.txt

Путь — файл или каталог под git. docs/<X> -> docs/archive/<X> (как docs/archive/findings). Не переносятся:
  - файлы из --keep (по строке на путь; каталог = всё под ним): закреплены летописью (`file:`-ссылки БД, источники чисел);
  - файлы из --freeze и всё, на что они ссылаются: их правят открытые ветки, переписывать их нельзя;
  - уже лежащие в docs/archive/ и места, куда уже что-то лежит.
Ссылки правятся во всех текстовых файлах репо вне --freeze: пути вида docs/... в тексте и относительные md-ссылки
(и у переносимых файлов, и на них). --tsv — таблица «старый путь → новый» (для `chron.py relink`).
Только стандартная библиотека."""
import argparse
import posixpath
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCH = "docs/archive/"
TEXT_EXT = {".md", ".py", ".sh", ".rs", ".toml", ".json", ".jsonl", ".csv", ".txt", ".js", ".html", ".yml", ".yaml",
            ".service", ".timer", ".conf", ".tsv", ".sql", ".mjs", ".css", ".tpl", ".ipynb"}
TOKEN = re.compile(r"docs/[^\s)\]>\"'`,;|*{}<\\]+")
MDLINK = re.compile(r"(\]\(|src=\"|href=\")([^)\"#\s]+)")
NO_REWRITE = ("data/", "src/", "tools/", "docs/registry/", "docs/plan/runs.csv")  # данные и код: текст не правим (src — вывод --help, tools — чужие правки TK-703); документ, на который они ссылаются, остаётся


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def read_list(p):
    return [l.strip() for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()] if p else []


def under(path, roots):
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def is_text(p):
    return Path(p).suffix.lower() in TEXT_EXT or Path(p).name in ("AGENTS.md", "CLAUDE.md")


def read(p):
    try:
        return (ROOT / p).read_bytes().decode("utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return None


def new_path(old):
    return ARCH + old[len("docs/"):]


def token_path(tok):
    """docs/x/y.md:12 / docs/x/y.md. -> (путь, хвост)."""
    m = re.match(r"^(.*?)((?::\d+(?:-\d+)?)?[.:]*)$", tok)
    return m.group(1), m.group(2)


def build(args):
    files = [p for p in git("ls-files", "-z").split("\0") if p]
    fset = set(files)
    keep, freeze = read_list(args.keep), set(read_list(args.freeze))
    cand = [f for f in files if under(f, args.paths) and f.startswith("docs/") and not f.startswith(ARCH)
            and not under(f, keep) and f not in freeze]
    moves = {f: new_path(f) for f in cand}
    skipped = {"занято": [f for f in cand if new_path(f) in fset or (ROOT / new_path(f)).exists()]}
    for f in skipped["занято"]:
        moves.pop(f)
    # файлы, на которые ссылаются нетронутые (замороженные, src/, данные), остаются; сами переносимые — история, их ссылки не держат
    texts = {f: read(f) for f in files if is_text(f) and not f.startswith("data/")}
    held_all = set()
    while True:
        held = set()
        for f, t in texts.items():
            if t and f not in moves and (f in freeze or f.startswith(NO_REWRITE)):
                for tok in TOKEN.findall(t):
                    p, _ = token_path(tok)
                    if p in moves:
                        held.add(p)
        if not held:
            break
        for p in held:
            moves.pop(p)
        held_all |= held
    skipped["держат замороженные"] = sorted(held_all)
    return files, fset, moves, texts, freeze, skipped


def rewrite(f, t, moves, fset, freeze):
    """Новый текст файла f (пути docs/… и относительные ссылки)."""
    if f in freeze or f.startswith(NO_REWRITE):
        return t
    f_new = moves.get(f, f)

    def tok(m):
        p, tail = token_path(m.group(0))
        return moves[p] + tail if p in moves else m.group(0)

    out = TOKEN.sub(tok, t)
    if f.endswith(".md") or f.endswith(".html"):
        base_old, base_new = posixpath.dirname(f), posixpath.dirname(f_new)

        def lnk(m):
            target = m.group(2)
            if re.match(r"^([a-z]+:|/|#|mailto:)", target):
                return m.group(0)
            res = posixpath.normpath(posixpath.join(base_old, target))
            res_new = moves.get(res, res)
            if res_new == res and f_new == f:
                return m.group(0)
            if res not in fset and not any(x.startswith(res + "/") for x in fset):
                return m.group(0)
            rel = posixpath.relpath(res_new, base_new)
            return m.group(1) + rel

        out = MDLINK.sub(lnk, out)
    return out


def lists(out, prefixes):
    """keep.txt — пути, закреплённые летописью (`file:`-ссылки и источники чисел из БД на srv);
    freeze.txt — файлы, которые правят открытые ветки `bb/<префикс>-*` (diff против master), и два служебных."""
    sys.path.insert(0, str(ROOT / "tools"))
    import chron
    keep = set()
    for _, d, _ in chron.call("links_all"):
        if d.startswith("file:"):
            keep.add(re.sub(r":\d+(-\d+)?$", "", d[5:].split("#", 1)[0].rstrip("/")))
    for src, _ in chron.call("metrics_sources"):
        keep.add(re.sub(r":\d+(-\d+)?$", "", src))
    freeze = {"docs/plan/coord-handoff.md", "docs/plan/BUSINESS-TASK.md"}
    for pre in prefixes:
        for br in git("branch", "--list", "bb/%s-*" % pre, "--format=%(refname:short)").split():
            freeze |= set(git("-c", "core.quotepath=false", "diff", "--name-only", "master..." + br).splitlines())
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "keep.txt").write_text("".join(p + "\n" for p in sorted(keep)), encoding="utf-8")
    (out / "freeze.txt").write_text("".join(p + "\n" for p in sorted(freeze)), encoding="utf-8")
    print("keep %d путей, freeze %d файлов → %s" % (len(keep), len(freeze), out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "apply", "lists"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--branches", default="tk-674,tk-683,tk-690,tk-692,tk-697,tk-699,tk-700,tk-701,page-8090-audit,critic-viking-map")
    ap.add_argument("--keep")
    ap.add_argument("--freeze")
    ap.add_argument("--tsv")
    args = ap.parse_args()
    args.paths = [p.rstrip("/") for p in args.paths]
    if args.cmd == "lists":
        lists(args.paths[0], args.branches.split(","))
        return 0
    files, fset, moves, texts, freeze, skipped = build(args)
    changed = {}
    for f, t in texts.items():
        if t is None:
            continue
        n = rewrite(f, t, moves, fset, freeze)
        if n != t:
            changed[f] = n
    size = sum((ROOT / f).stat().st_size for f in moves)
    print("переносится %d файлов (%.1f МБ), правок ссылок в %d файлах; пропущено: %s" % (
        len(moves), size / 1048576, len(changed), "; ".join("%s %d" % (k, len(v)) for k, v in skipped.items())))
    if skipped["держат замороженные"] and args.cmd == "plan":
        for p in skipped["держат замороженные"][:20]:
            print("  держат замороженные:", p)
    if args.tsv:
        Path(args.tsv).write_text("".join("%s\t%s\n" % kv for kv in sorted(moves.items())), encoding="utf-8")
    if args.cmd == "plan":
        return 0
    for f, n in changed.items():
        (ROOT / f).write_bytes(n.encode("utf-8"))
    for old, new in sorted(moves.items()):
        (ROOT / new).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "mv", old, new], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
