#!/usr/bin/env python3
"""chron_site — сайт летописи (:8091, /chron/…): страницы записей, разделов, протоколов и чисел поверх БД chron.sqlite (TK-646, вынесено из chron.py в TK-704).

Запускается командой `python3 chron.py serve` (юнит `kb-page` на srv) — рядом с chron.py должны лежать этот файл и kb.py (chron_deploy.sh кладёт оба).
Только чтение БД; статика kb — из каталога --static."""
import html
import http.server
import json
import re
import socketserver
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from chron import (HERE, SIDE_RU, VERDICTS, ChronError, Store, canon_ref, connect, fmt_num, local_db, mean_derived,
                   node_kind)


def kb_module():
    sys.path.insert(0, str(HERE))
    try:
        import kb as kbmod
        return kbmod
    except ImportError:
        return None
    finally:
        sys.path.pop(0)


EXTRA_CSS = """
.ctl input[type=date]{flex:0 1 150px}
.vd{font-weight:600}.v-работает{color:var(--ok)}.v-не.работает{color:var(--bad)}
.erow{display:grid;grid-template-columns:72px 120px 1fr;gap:2px 12px;padding:8px 0;border-bottom:1px solid var(--line);align-items:baseline}
.erow .d{color:var(--mut);font-size:13px}.erow .t{min-width:0;overflow-wrap:anywhere}.erow .b{display:flex;gap:6px;flex-wrap:wrap;margin-top:3px}
.fields{border-collapse:collapse;font-size:14px;margin:.6em 0}.fields th{width:180px;white-space:normal}
.sec{border-top:1px solid var(--line);padding-top:4px;margin-top:10px}.sec .meta{margin:2px 0 4px}
.amend{border-left:3px solid #ecd3a8;padding-left:10px}
.pager{display:flex;gap:14px;margin:12px 0}
table.m{font-size:13px}table.m td,table.m th{padding:4px 7px;white-space:nowrap}table.m td.c{white-space:normal;min-width:140px;max-width:340px;overflow-wrap:anywhere}
table.m td.n{text-align:right;font-variant-numeric:tabular-nums}table.m th a{color:inherit}.neg{color:var(--bad)}.pos{color:var(--ok)}
.ft{color:var(--mut);font-size:12px}.ctl input.sm{flex:0 1 110px}
article.body{overflow-wrap:anywhere}
@media(max-width:640px){.erow{grid-template-columns:1fr auto}.erow .t{grid-column:1/-1}.fields th{width:110px}}
"""


class Site:
    def __init__(self, db, static):
        self.db, self.static = db, static
        self.kb = kb_module()

    def store(self):
        return Store(connect(self.db, ro=True))

    def page(self, title, body, cur=""):
        css = (self.kb.CSS if self.kb else "") + EXTRA_CSS
        nav = "".join('<a href="%s"%s>%s</a>' % (h, ' class="cur"' if k == cur else "", l) for k, h, l in (
            ("cards", "/index.html", "Карточки"), ("graph", "/graph.html", "Граф"), ("chron", "/chron/", "Летопись"), ("nums", "/chron/m/", "Числа"),
            ("proto", "/chron/p/", "Протоколы")))
        return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<link rel="icon" href="data:,"><title>%s</title><style>%s</style></head><body><header class="top"><b>alpha</b><nav>%s</nav></header>'
                '<main>%s</main></body></html>' % (html.escape(title), css, nav, body))

    def href(self, n, st=None):
        k = node_kind(n)
        if k == "entry":
            return "/chron/e/%s" % n[2:]
        if k == "psec":
            return "/chron/s/%s" % n[3:]
        if k == "commit":
            return "https://github.com/slayfactory/alpha/commit/%s" % n[7:]
        if k == "file":
            return "https://github.com/slayfactory/alpha/blob/master/%s" % quote(n[5:].split("#")[0])
        return "/chron/n/%s" % quote(n)

    def md(self, text, st):
        if not self.kb:
            return "<pre>%s</pre>" % html.escape(text)

        def link_for(t):
            c = canon_ref(t)
            n = st.canon(list(c)) if isinstance(c, tuple) else c
            return '<a href="%s">%s</a>' % (self.href(n or t), html.escape(t))
        try:
            return self.kb.md_to_html(text, link_for)
        except Exception:
            return "<pre>%s</pre>" % html.escape(text)

    def links_html(self, links):
        def ul(rows, arrow):
            if not rows:
                return "<p>—</p>"
            return '<ul class="links">%s</ul>' % "".join(
                '<li><a href="%s">%s</a><span>%s</span><span class="k">%s</span></li>' % (self.href(x["node"]), html.escape(x["node"]), html.escape(x["label"][:160]), html.escape(x["rel"]))
                for x in rows)
        return "<h2>Ссылки</h2>%s<h2>Обратные ссылки</h2>%s" % (ul(links["out"], "→"), ul(links["in"], "←"))

    def vbadge(self, v):
        return '<span class="badge vd v-%s">%s</span>' % (html.escape((v or "").replace(" ", ".")), html.escape(v or "не размечено"))

    def erow(self, r):
        corr = ' <span class="badge">исправлена L-%s</span>' % r["corrected_by"] if r.get("corrected_by") else ""
        if r.get("n_metrics"):
            corr += ' <a class="badge" href="/chron/m/?entry=%d">чисел %d</a>' % (r["id"], r["n_metrics"])
        elif r.get("gap"):
            corr += ' <span class="badge">нет чисел: %s</span>' % html.escape(r["gap"])
        return ('<div class="erow"><a class="id" href="/chron/e/%d">L-%d</a><span class="d">%s</span><span class="t">%s'
                '<span class="b">%s%s%s%s%s</span></span></div>') % (
            r["id"], r["id"], html.escape((r.get("ts") or "")[:16].replace("T", " ")), html.escape(r["title"]),
            ('<a class="badge" href="/chron/n/%s">%s</a>' % (quote(r["project"].replace("П-", "P-", 1)), html.escape(r["project"]))) if r.get("project") else "",
            ('<a class="badge" href="/chron/n/%s">%s</a>' % (quote(r["task"]), html.escape(r["task"]))) if r.get("task") else "",
            '<span class="badge">%s</span>' % html.escape(r["kind"]), self.vbadge(r.get("verdict")), corr)

    def index(self, qs):
        st = self.store()
        f = {k: (qs.get(k) or [""])[0].strip() for k in ("q", "project", "verdict", "kind", "since", "until", "task", "nums")}
        page_n = max(1, int((qs.get("page") or ["1"])[0] or 1))
        f.update(limit=100, offset=(page_n - 1) * 100)
        res = st.query(f)
        fc = st.facets()

        def sel(name, vals, first):
            return '<select name="%s" id="%s"><option value="">%s</option>%s</select>' % (name, name, first, "".join(
                '<option%s>%s</option>' % (" selected" if f[name] == v else "", html.escape(v)) for v in vals))
        ctl = ('<form class="ctl" method="get" action="/chron/"><input id="q" name="q" type="search" placeholder="поиск по тексту" value="%s">'
               '%s%s%s%s<input type="date" name="since" id="since" value="%s" title="с"><input type="date" name="until" id="until" value="%s" title="по">'
               '<input type="hidden" name="task" value="%s"><button>найти</button></form>') % (
            html.escape(f["q"]), sel("project", fc["projects"], "все проекты"), sel("verdict", fc["verdicts"] + ["—"], "любой вердикт"),
            sel("kind", fc["kinds"], "любой вид"), sel("nums", ["есть", "файл", "текст", "нет", "пометка", "без объяснения"], "числа: любые"),
            html.escape(f["since"]), html.escape(f["until"]), html.escape(f["task"]))
        rows = "".join(self.erow(r) for r in res["rows"]) or "<p>Ничего не найдено.</p>"
        q = {k: v for k, v in f.items() if v and k not in ("limit", "offset")}
        pager = ""
        if res["total"] > 100:
            pages = (res["total"] + 99) // 100
            pager = '<div class="pager">%s<span>стр. %d из %d</span>%s</div>' % (
                ('<a href="?%s">← назад</a>' % urlencode(dict(q, page=page_n - 1))) if page_n > 1 else "", page_n, pages,
                ('<a href="?%s">дальше →</a>' % urlencode(dict(q, page=page_n + 1))) if page_n < pages else "")
        body = '<h1>Летопись</h1>%s<p id="cnt">%d записей</p><div id="list">%s</div>%s' % (ctl, res["total"], rows, pager)
        return self.page("Летопись", body, "chron")

    def entry(self, n):
        st = self.store()
        r = st.get("L-%d" % n)
        e = r["entry"]
        meta = '<p class="meta">%s<span class="badge">%s</span>%s%s<span>%s</span></p>' % (
            self.vbadge(e["verdict"]), html.escape(e["kind"]),
            ('<a class="badge" href="/chron/n/%s">%s</a>' % (quote(e["project"].replace("П-", "P-", 1)), html.escape(e["project"]))) if e["project"] else "",
            ('<a class="badge" href="/chron/n/%s">%s</a>' % (quote(e["task"]), html.escape(e["task"]))) if e["task"] else "",
            html.escape((e["ts"] or "без даты").replace("T", " ")))
        nums = [("$ (ставка 500 $)", e["usd"]), ("сделок", e["trades"]), ("Шарп", e["sharpe"]),
                ("найдено на этих данных", {1: "да", 0: "нет"}.get(e["same_data"]))]
        tbl = "".join("<tr><th>%s</th><td>%s</td></tr>" % (k, html.escape(str(v))) for k, v in nums if v is not None)
        if e["hypothesis"]:
            tbl = "<tr><th>гипотеза</th><td>%s</td></tr>" % html.escape(e["hypothesis"]) + tbl
        notes = ""
        if e.get("parsed"):
            names = {"verdict": "вердикт", "usd": "$", "trades": "сделок", "sharpe": "Шарп", "same_data": "найдено на этих данных"}
            notes += '<details class="meta"><summary>откуда поля (разбор текста при переносе)</summary><table class="fields">%s</table></details>' % "".join(
                "<tr><th>%s</th><td>%s</td></tr>" % (names.get(k, k), html.escape(v)) for k, v in json.loads(e["parsed"]).items())
        if e["corrects"]:
            notes += '<p>Исправляет <a href="/chron/e/%d">L-%d</a>.</p>' % (e["corrects"], e["corrects"])
        if r["corrected_by"]:
            notes += "<p><b>Исправлена:</b> %s</p>" % ", ".join('<a href="/chron/e/%d">L-%d</a>' % (x, x) for x in r["corrected_by"])
        if e["fields"]:
            fl = json.loads(e["fields"])
            content = '<table class="fields">%s</table>' % "".join(
                "<tr><th>%s</th><td>%s</td></tr>" % (html.escape(k), self.kb.md_inline(v, lambda t: html.escape(t)) if self.kb else html.escape(v)) for k, v in fl.items())
        else:
            content = '<article class="body">%s</article>' % self.md(e["body"], st)
        src = '<p class="meta">%s · автор %s · записано %s</p>' % (html.escape(e["src"] or "chron add"), html.escape(e["author"] or "—"), html.escape(e["created"]))
        mt = ""
        if r["metrics_total"]:
            mt = '<h2 id="nums">Числа (%d%s)</h2>%s' % (r["metrics_total"], ", показаны первые %d" % len(r["metrics"]) if r["metrics_total"] > len(r["metrics"]) else "",
                                                     self.metrics_table(r["metrics"], with_entry=False))
            mt += '<p><a href="/chron/m/?entry=%d">все числа записи — фильтры и сортировка</a></p>' % n
        if r.get("metric_fixes"):
            mt += '<p class="meta">заменено строк: %d — %s (прежние в БД, не действуют)</p>' % (len(r["metric_fixes"]), "; ".join(
                "#%d %s → #%d %s" % (fx["metric"], html.escape(fx["old_ptr"] or ""), fx["replaced_by"], html.escape(fx["new_ptr"] or "")) for fx in r["metric_fixes"]))
        if r.get("gaps"):
            mt += "".join(('<p class="gap meta">снята: числа добавлены · <s>нет чисел: %s%s%s</s></p>' if gp.get("lifted") else '<p class="gap"><b>Нет чисел: %s</b>%s%s</p>') % (
                html.escape(gp["reason"]), " — " + html.escape(gp["note"]) if gp["note"] else "",
                ' <span class="meta">(%s%s)</span>' % (html.escape(gp["src"]), " " + html.escape(gp["src_ptr"]) if gp["src_ptr"] else "") if gp["src"] else "")
                for gp in r["gaps"])
        body = '%s<h1>L-%d <span style="color:var(--mut);font-weight:400">· %s</span></h1>%s%s%s%s%s%s' % (
            meta, n, html.escape(e["title"]), ('<table class="fields">%s</table>' % tbl) if tbl else "", notes, mt, content, src, self.links_html(r["links"]))
        return self.page("L-%d — %s" % (n, e["title"][:80]), body, "chron")

    def src_href(self, m):
        path = re.sub(r":\d+(-\d+)?$", "", m["src"])
        ref = m["src_commit"] or "master"
        line = re.match(r"^строка (\d+)", m["src_ptr"] or "")
        return "https://github.com/slayfactory/alpha/blob/%s/%s%s" % (quote(ref), quote(path), "#L%s" % line.group(1) if line else "")

    def metrics_table(self, rows, with_entry=True, sort_link=None):
        def th(key, label):
            if not sort_link or not key:
                return "<th>%s</th>" % label
            return '<th><a href="%s">%s</a></th>' % (html.escape(sort_link(key)), label)

        def money(v, nd=2):
            if v is None:
                return "—"
            return '<span class="%s">%s</span>' % ("neg" if v < 0 else "pos" if v > 0 else "", fmt_num(v, nd))
        head = "".join([th("entry", "запись")] if with_entry else []) + "".join(th(k, l) for k, l in (
            (None, "сторона"), (None, "клетка"), (None, "вселенная"), (None, "окно"), ("trades", "сделок"), ("usd", "Σ $ (500 $)"),
            ("mean", "$ / сделку"), ("sharpe", "Шарп"), (None, "вердикт"), (None, "найдено на этих данных"), (None, "источник")))
        body = []
        for m in rows:
            ex = json.loads(m["extra"]) if m.get("extra") else {}
            note = []
            if ex.get("usd_raw") is not None:
                note.append("$ в файле %s (ставка не названа)" % fmt_num(ex["usd_raw"]))
            if ex.get("pass") is not None:
                note.append("ворота: %s" % ("да" if ex["pass"] else "нет"))
            if ex.get("verdict_text"):
                note.append(ex["verdict_text"][:80])
            body.append("<tr>%s<td>%s</td><td class=\"c\">%s%s</td><td>%s</td><td>%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td>"
                        "<td class=\"n\">%s</td><td>%s</td><td>%s</td><td><a href=\"%s\">%s</a>%s</td></tr>" % (
                ('<td><a href="/chron/e/%d">L-%d</a></td>' % (m["entry"], m["entry"])) if with_entry else "",
                SIDE_RU.get(m["side"], "—"), html.escape(m["cell"] or "—"), ('<div class="ft">%s</div>' % html.escape("; ".join(note))) if note else "",
                html.escape(m["uni"] or "—"), html.escape(m["win"] or "—"), m["trades"] if m["trades"] is not None else "—", money(m["usd"]),
                money(m["mean_usd"], 3) + (' <span class="ft">= Σ/n</span>' if mean_derived(m) else ""), fmt_num(m["sharpe"]), html.escape(m["verdict"] or "—"), {1: "да", 0: "нет"}.get(m["same_data"], "—"),
                html.escape(self.src_href(m)), html.escape(Path(re.sub(r":\d+$", "", m["src"])).name),
                ' <span class="ft">%s%s</span>' % (html.escape((m["src_ptr"] or "")[:60]), " · «из текста»" if m["from_text"] else "")))
        return '<div class="tw"><table class="m"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (head, "".join(body))

    def metrics_page(self, qs):
        st = self.store()
        keys = ("entry", "project", "side", "uni", "win", "cell", "verdict", "same_data", "text", "min_trades", "src", "sort", "cells")
        f = {k: (qs.get(k) or [""])[0].strip() for k in keys}
        f["sort"] = f["sort"] or "-date"
        page_n = max(1, int((qs.get("page") or ["1"])[0] or 1))
        q = {k: v for k, v in f.items() if v}
        res = st.metrics_query(dict(f, limit=200, offset=(page_n - 1) * 200))
        fc = st.metrics_facets()

        def sel(name, vals, first, labels=None):
            return '<select name="%s" id="m-%s"><option value="">%s</option>%s</select>' % (name, name, first, "".join(
                '<option value="%s"%s>%s</option>' % (html.escape(v), " selected" if f[name] == v else "", html.escape((labels or {}).get(v, v))) for v in vals))

        def sort_link(key):
            cur = f["sort"]
            nxt = ("-" + key) if cur != "-" + key else key
            return "?" + urlencode(dict(q, sort=nxt))
        ctl = ('<form class="ctl" method="get" action="/chron/m/">'
               '<input name="cell" id="m-cell" type="search" placeholder="клетка (подстрока)" value="%s">%s%s%s%s%s%s%s'
               '<input class="sm" name="min_trades" id="m-min" type="number" min="0" placeholder="сделок ≥" value="%s">'
               '<input class="sm" name="entry" id="m-entry" placeholder="L-N" value="%s">'
               '%s%s<button>показать</button></form>') % (
            html.escape(f["cell"]), sel("project", fc["projects"], "все проекты"), sel("side", ["long", "short", "both", "—"], "любая сторона", SIDE_RU),
            sel("uni", fc["uni"], "любая вселенная"), sel("win", fc["win"], "любое окно"), sel("verdict", list(VERDICTS) + ["—"], "любой вердикт"),
            sel("same_data", ["1", "0", "—"], "найдено на этих данных: всё", {"1": "найдено на этих данных", "0": "не на этих данных", "—": "не сказано"}),
            sel("text", ["0", "1"], "источник: любой", {"0": "из файлов результатов", "1": "«из текста»"}),
            html.escape(f["min_trades"]), html.escape(f["entry"]), sel("cells", ["1"], "без клеток хранилища", {"1": "клетки хранилища (TK-674)"}),
            sel("sort", ["-date", "-usd", "usd", "-sharpe", "sharpe", "-trades", "-mean", "mean", "entry"], "порядок",
                {"-date": "новые записи", "-usd": "Σ $ ↓", "usd": "Σ $ ↑", "-sharpe": "Шарп ↓", "sharpe": "Шарп ↑", "-trades": "сделок ↓",
                 "-mean": "$ / сделку ↓", "mean": "$ / сделку ↑", "entry": "по записи"}))
        pager = ""
        if res["total"] > 200:
            pages = (res["total"] + 199) // 200
            pager = '<div class="pager">%s<span>стр. %d из %d</span>%s</div>' % (
                ('<a href="?%s">← назад</a>' % urlencode(dict(q, page=page_n - 1))) if page_n > 1 else "", page_n, pages,
                ('<a href="?%s">дальше →</a>' % urlencode(dict(q, page=page_n + 1))) if page_n < pages else "")
        note = ('<p class="meta">Σ $ — на ставку 500 $ (движок П-42); у других проектов ставка в файле не названа — $ пуст, сумма файла в примечании. '
                'Шарп — годовой по дневному ряду. «= Σ/n» — $ / сделку равен Σ $ / сделок (посчитан из них, если в источнике не напечатан). '
                '«из текста» — число из таблицы отчёта md, не из файла результатов.</p>')
        body = '<h1>Числа</h1>%s%s<p id="cnt">%d строк</p>%s%s' % (ctl, note, res["total"],
                                                                   self.metrics_table(res["rows"], True, sort_link) if res["rows"] else "<p>Ничего не найдено.</p>", pager)
        return self.page("Числа", body, "nums")

    def section(self, n):
        st = self.store()
        r = st.get("PS-%d" % n)
        s = r["psec"]
        meta = '<p class="meta"><a class="badge" href="/chron/p/%s">%s</a><span class="badge">%s</span>%s<span class="badge">%s</span>%s</p>' % (
            quote(s["protocol"]), html.escape(s["short"]), html.escape(s["kind"] or ""),
            ('<span class="badge st-done">до счёта %s</span>' % html.escape(s["prereg_at"] or "")) if s["prereg"] else "",
            "неизменяем" if s["locked"] else "черновик", (' <span>α %s</span>' % html.escape(s["alpha"])) if s["alpha"] else "")
        notes = ""
        if s["amends"]:
            notes += '<p>Поправка к <a href="/chron/s/%d">PS-%d</a>.</p>' % (s["amends"], s["amends"])
        if r["amended_by"]:
            notes += "<p><b>Поправки:</b> %s</p>" % ", ".join('<a href="/chron/s/%d">PS-%d</a>' % (x, x) for x in r["amended_by"])
        body = '%s<h1>PS-%d <span style="color:var(--mut);font-weight:400">· %s §%s</span></h1>%s<article class="body">%s</article><p class="meta">%s · записано %s</p>%s' % (
            meta, n, html.escape(s["short"]), html.escape(s["num"] or "—"), notes, self.md(s["body"], st),
            html.escape(s["src"] or "chron psec add"), html.escape(s["created"]), self.links_html(r["links"]))
        return self.page("PS-%d — %s" % (n, s["heading"][:80]), body, "proto")

    def protocols(self):
        st = self.store()
        rows = "".join('<div class="erow"><a class="id" href="/chron/p/%s">%s</a><span class="d">%d разд. · до счёта %d</span><span class="t">%s</span></div>' % (
            quote(p["id"]), html.escape(p["short"]), p["sections"], p["prereg"] or 0, html.escape(p["title"])) for p in st.protocols())
        return self.page("Протоколы", "<h1>Протоколы</h1><div id=\"list\">%s</div>" % rows, "proto")

    def protocol(self, pid):
        st = self.store()
        r = st.get(pid)
        if not r.get("protocol"):
            return None
        p = r["protocol"]
        parts = []
        for s in r["sections"]:
            full = st.get("PS-%d" % s["id"])["psec"]
            parts.append('<section class="sec%s" id="s%d"><p class="meta"><a href="/chron/s/%d">PS-%d</a>%s%s</p>%s</section>' % (
                " amend" if s["amends"] else "", s["id"], s["id"], s["id"],
                ' <span class="badge st-done">до счёта %s</span>' % html.escape(s["prereg_at"] or "") if s["prereg"] else "",
                (' <span class="badge">поправка к PS-%d</span>' % s["amends"]) if s["amends"] else "", self.md(full["body"], st)))
        toc = "".join('<li style="margin-left:%dpx"><a href="#s%d">%s</a></li>' % (max(0, s["level"] - 2) * 16, s["id"], html.escape(s["heading"][:140]))
                      for s in r["sections"] if s["level"] <= 3)
        body = '<h1>%s</h1><p class="meta"><span>%s</span><span>%d разделов</span><a href="/chron/n/%s">связи</a></p><details><summary>Оглавление</summary><ul>%s</ul></details>%s' % (
            html.escape(p["title"]), html.escape(p["id"]), len(r["sections"]), quote(p["short"]), toc, "".join(parts))
        return self.page(p["short"] + " — " + p["title"][:80], body, "proto")

    def node(self, n):
        st = self.store()
        r = st.get(n)
        parts = []
        if r.get("kb"):
            k = r["kb"]
            parts.append('<p class="meta"><a class="badge" href="/c/%s.html">карточка kb</a><span class="badge">%s</span><span class="badge st-%s">%s</span></p><h1>%s <span style="color:var(--mut);font-weight:400">· %s</span></h1>' % (
                quote(k["id"]), html.escape(k["type"]), html.escape(k["status"] or ""), html.escape(k["status"] or ""), html.escape(k["id"]), html.escape(k["title"])))
        else:
            parts.append("<h1>%s</h1>" % html.escape(n))
        for p in r.get("protocols") or []:
            parts.append('<p>Протокол: <a href="/chron/p/%s">%s</a> — %s</p>' % (quote(p["id"]), html.escape(p["id"]), html.escape(p["title"])))
        if r.get("entries"):
            parts.append("<h2>Записи летописи (%d)</h2><div>%s</div>" % (len(r["entries"]), "".join(self.erow(e) for e in r["entries"])))
        parts.append(self.links_html(r["links"]))
        return self.page(n, "".join(parts), "chron")


def make_handler(site):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=site.static, **kw)

        def log_message(self, *args):
            pass

        def send_html(self, text, code=200):
            b = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            p = unquote(u.path)
            if not p.startswith("/chron"):
                return super().do_GET()
            try:
                if p in ("/chron", "/chron/"):
                    return self.send_html(site.index(parse_qs(u.query)))
                m = re.match(r"^/chron/e/(\d+)/?$", p)
                if m:
                    return self.send_html(site.entry(int(m.group(1))))
                m = re.match(r"^/chron/s/(\d+)/?$", p)
                if m:
                    return self.send_html(site.section(int(m.group(1))))
                if p in ("/chron/p", "/chron/p/"):
                    return self.send_html(site.protocols())
                if p in ("/chron/m", "/chron/m/"):
                    return self.send_html(site.metrics_page(parse_qs(u.query)))
                m = re.match(r"^/chron/p/(.+?)/?$", p)
                if m:
                    out = site.protocol(m.group(1))
                    if out:
                        return self.send_html(out)
                m = re.match(r"^/chron/n/(.+?)/?$", p)
                if m:
                    return self.send_html(site.node(m.group(1)))
            except ChronError as e:
                return self.send_html(site.page("не найдено", "<h1>Не найдено</h1><p>%s</p>" % html.escape(str(e))), 404)
            except Exception as e:  # страница не должна ронять соединение
                return self.send_html(site.page("ошибка", "<h1>Ошибка</h1><p>%s</p>" % html.escape(repr(e))), 500)
            return self.send_html(site.page("не найдено", "<h1>Не найдено</h1>"), 404)

    return H


def serve(a):
    db = local_db()
    if not db:
        raise ChronError("serve — рядом с БД (srv) или с CHRON_DB")
    site = Site(db, a.static)
    socketserver.TCPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer((a.host, a.port), make_handler(site)) as srv:
        print("chron: http://%s:%d/chron/ (static %s)" % (a.host, a.port, a.static), flush=True)
        srv.serve_forever()
    return 0
