"""TK-646: проверки tools/chron.py (запуск: python tools/tests/test_chron.py). БД — во временной папке; репозиторий только читается."""
import gc
import io
import json
import multiprocessing as mp
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import chron  # noqa: E402
import chron_site  # noqa: E402


def entry(title="заголовок", **kw):
    e = {"kind": "verdict", "title": title, "body": kw.pop("body", "текст " + title)}
    e.update(kw)
    return e


def _writer(args):
    db, p, k = args
    st = chron.Store(chron.connect(db))
    for i in range(k):
        st.add_entry(entry("w%d-%d" % (p, i)), [])
    return k


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "c.sqlite")
        os.environ["CHRON_DB"] = self.db
        self.st = chron.Store(chron.connect(self.db))

    def tearDown(self):
        self.st.c.close()
        os.environ.pop("CHRON_DB", None)
        self.tmp.cleanup()

    def cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = chron.main(list(argv))
        return rc, buf.getvalue()


class TestStore(Base):
    def test_entry_immutable(self):
        n = self.st.add_entry(entry("а"), [])
        self.assertEqual(n, "L-1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.c.execute("UPDATE entry SET title='б' WHERE id=1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.c.execute("DELETE FROM entry WHERE id=1")

    def test_correction_and_links_both_ways(self):
        self.st.add_entry(entry("старое"), [])
        n = self.st.add_entry(entry("новое", corrects="L-1", verdict="не работает", usd=-12.5, trades=40, sharpe=-0.3, same_data=1), ["TK-646"])
        g = self.st.get("L-1")
        self.assertEqual(g["corrected_by"], [2])
        self.assertIn({"node": n, "rel": "entry", "origin": "add", "label": "новое"}, g["links"]["in"])
        self.assertEqual({x["node"] for x in self.st.get("L-2")["links"]["out"]}, {"L-1", "TK-646"})
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.c.execute("DELETE FROM link")

    def test_bad_input_writes_nothing(self):
        with self.assertRaises(chron.ChronError):
            self.st.add_entry(entry("x"), ["L-99"])
        with self.assertRaises(chron.ChronError):
            self.st.add_entry(entry("x", verdict="хорошо"), [])
        with self.assertRaises(chron.ChronError):
            self.st.add_entry(entry("x", corrects="L-5"), [])
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM entry").fetchone()[0], 0)

    def test_prereg_inheritance(self):
        text = ("# P-91\n## 13. Раздел — записано 18.11.2026 до счёта\nа\n"
                "### 13.1 Правило\nб\n#### 5. Что считаем (колонки; одним пакетом после PASS)\nв\n"
                "### 13.2 Результат (20.11.2026, после PASS критика)\nг\n#### 1. Таблица\nд\n"
                "### 13.3 Поправки после счёта\nе\n### 13.4 Отступления (после PASS)\nж\n"
                "## 14. Признаки — записано 19.11.2026 ДО любого счёта по меткам\nз\n")
        root = Path(self.tmp.name)
        (root / "docs" / "research").mkdir(parents=True)
        (root / "docs" / "research" / "P-91-test.md").write_text(text, encoding="utf-8", newline="\n")
        secs = [c["sec"] for c in chron.build_docs(root, ["docs/research/P-91-test.md"], [], None)[0]["chunks"] if c["kind"] == "psec"]
        got = {s["num"]: (s["prereg"], s["prereg_at"]) for s in secs}
        self.assertEqual(got["13"], (1, "2026-11-18"))
        self.assertEqual(got["13.1"], (1, "2026-11-18"))
        self.assertEqual(got["13.1 п. 5"], (1, "2026-11-18"))  # «после PASS» в спецификации — не метка результата
        self.assertEqual(got["13.2"][0], 0)  # «Результат» в своём заголовке — наследование снято
        self.assertEqual(got["13.2 п. 1"][0], 0)  # и у его подпунктов
        self.assertEqual(got["13.3"][0], 0)
        self.assertEqual(got["13.4"][0], 0)
        self.assertEqual(got["14"], (1, "2026-11-19"))  # «ДО любого счёта»

    def test_psec_flag_append_only_latest_wins(self):
        self.st.new_protocol("P-90-test", "Тест")
        a = self.st.add_psec({"protocol": "P-90", "body": "## 1. Вопрос\nчто\n", "prereg": True}, [])
        self.assertEqual(self.st.stats()["sections_prereg"], 1)
        self.st.flag_psec(a, False, None, "результат после счёта")
        self.assertEqual(self.st.get(a)["psec"]["prereg"], 0)
        self.assertEqual(self.st.get("P-90-test")["sections"][0]["prereg"], 0)
        self.assertEqual([p["prereg"] for p in self.st.protocols() if p["id"] == "P-90-test"], [0])
        self.assertEqual(self.st.stats()["sections_prereg"], 0)
        self.st.flag_psec(a, True, "2026-11-20", "вернуть")
        g = self.st.get(a)
        self.assertEqual((g["psec"]["prereg"], g["psec"]["prereg_at"], len(g["flags"])), (1, "2026-11-20", 2))
        self.assertEqual(self.st.c.execute("SELECT prereg FROM psec WHERE id=?", (int(a[3:]),)).fetchone()[0], 1)  # строка раздела не тронута
        for sql in ("UPDATE psec_flag SET prereg=0", "DELETE FROM psec_flag"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.st.c.execute(sql)
        with self.assertRaises(chron.ChronError):
            self.st.flag_psec(a, False, None, " ")
        self.assertEqual(self.st.check_db(), [])

    def test_migrate_v2_to_v3(self):
        db = os.path.join(self.tmp.name, "old.sqlite")
        c = sqlite3.connect(db, isolation_level=None)
        for stmt in chron.split_sql(chron.SCHEMA.replace(chron.MIGRATE[3], "")):
            c.execute(stmt)
        c.execute(chron.FTS_SQL)
        c.execute("PRAGMA user_version=2")
        c.close()
        st = chron.Store(chron.connect(db))
        self.assertEqual(st.c.execute("PRAGMA user_version").fetchone()[0], chron.SCHEMA_VERSION)
        self.assertEqual(st.check_db(), [])
        self.assertEqual(st.stats()["flag_fixes"], 0)
        st.c.close()

    def test_psec_draft_lock_amend(self):
        self.st.new_protocol("P-90-test", "Тест")
        a = self.st.add_psec({"protocol": "P-90", "body": "## 1. Вопрос\nчто\n"}, [])
        b = self.st.add_psec({"protocol": "P-90", "body": "## 2. Правило вердикта\nα = 0,05\n"}, [])
        self.st.edit_psec(a, "## 1. Вопрос\nчто именно\n")
        self.st.lock_psec(a)
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.edit_psec(a, "## 1. Вопрос\nдругое\n")
        am = self.st.add_psec({"protocol": "P-90", "body": "### 1.1 Поправка\nуточнение\n", "amends": "P-90 §1", "prereg": True}, [])
        g = self.st.get(a)
        self.assertEqual(g["amended_by"], [int(am[3:])])
        secs = [s["id"] for s in self.st.get("P-90-test")["sections"]]
        self.assertEqual(secs, [int(a[3:]), int(am[3:]), int(b[3:])])  # поправка — сразу за исправляемым разделом
        self.assertEqual(self.st.get(b)["psec"]["kind"], "verdict_rule")
        self.assertEqual(self.st.get(b)["psec"]["alpha"], "0,05")
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.c.execute("DELETE FROM psec")

    def test_search_substring_and_yo(self):
        self.st.add_entry(entry("Перетитровка шорт-базы", body="заморожено ёмкость L_n24"), [])
        self.st.add_entry(entry("другое", body="ничего"), [])
        self.assertEqual(self.st.search("титровк"), ["L-1"])
        self.assertEqual(self.st.search("емкость"), ["L-1"])
        self.assertEqual(self.st.search("n24 L_"), ["L-1"])
        self.assertEqual(self.st.query({"q": "заморож", "verdict": "—"})["total"], 1)

    def test_parallel_writers_no_lost_rows(self):
        with mp.Pool(8) as pool:
            pool.map(_writer, [(self.db, p, 20) for p in range(8)])
        n, d = self.st.c.execute("SELECT count(*), count(DISTINCT title) FROM entry").fetchone()
        self.assertEqual((n, d), (160, 160))
        self.assertEqual(self.st.check_db(), [])

    def test_rpc_bootstrap(self):
        """Путь «с ПК на srv»: исходник + вызов на stdin `python -`, ответ строкой CHRON-RESULT (без ssh)."""
        self.st.add_entry(entry("по сети"), [])
        import base64
        payload = base64.b64encode(json.dumps({"op": "query", "args": [{}], "kw": {}}).encode()).decode()
        prog = "RPC = %r\n" % payload + (TOOLS / "chron.py").read_text(encoding="utf-8")
        r = subprocess.run([sys.executable, "-"], input=prog.encode("utf-8"), capture_output=True,
                           env=dict(os.environ, CHRON_RPC="1", CHRON_DB=self.db, PYTHONUTF8="1"))
        m = re.search(r"^CHRON-RESULT (.*)$", r.stdout.decode("utf-8"), re.M)
        self.assertTrue(m, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(m.group(1))["ok"]["rows"][0]["title"], "по сети")

    def test_cli_add_show_find_links_check(self):
        rc, out = self.cli("add", "--title", "вердикт П-90", "--project", "P-90", "--task", "TK-900", "--verdict", "не ясно",
                           "--usd", "12.5", "--trades", "33", "--sharpe", "0.4", "--same-data", "--body", "числа", "--link", "TK-901")
        self.assertEqual((rc, out.strip()), (0, "L-1"))
        rc, out = self.cli("show", "L-1")
        self.assertIn("найдено на этих данных", out)
        self.assertIn("TK-901", out)
        rc, out = self.cli("find", "вердикт", "--project", "П-90")
        self.assertIn("L-1", out)
        rc, out = self.cli("links", "TK-901")
        self.assertIn("← L-1", out)
        rc, out = self.cli("check")
        self.assertEqual(rc, 0, out)
        self.st.c.execute("INSERT INTO link VALUES ('L-1', 'commit:0000000deadbeef', 'commit', 't', 'now')")
        self.st.c.execute("INSERT INTO link VALUES ('L-1', 'L-77', 'entry', 't', 'now')")
        rc, out = self.cli("check")
        self.assertEqual(rc, 1)
        self.assertIn("commit:0000000deadbeef", out)
        self.assertIn("L-77", out)


JOURNAL = """# Журнал

текст
| дата | протокол | шаг |
|---|---|---|
| 25.09 ~19:00 | П-01 | протокол TK-001 |
| 26.09 ~01:31Z | П-01 | счёт |

## 2026-10-09 · П-12 охват (TK-063)
строка 1
- пункт

05.11 00:55 · TK-491 · П-34 — запись
18.11 · П-42 · ПРЕДРЕГИСТРАЦИЯ §13.22 подряд без пустой строки
продолжение

| 20.10 ~10:00 (GMT+4) | П-21 | без шапки |
"""

PROTO = """# П-90. Тест

вступление
## 1. Вопрос
```bash
# не заголовок
```
## 13.28 Раздел — записано 19.11.2026 ДО счёта
#### 7. Подпункт
текст
## Приложение А
"""


class TestParse(Base):
    def test_journal_chunks_lossless(self):
        ch = chron.chunk_lines(JOURNAL, "journal")
        self.assertEqual("".join(c["text"] for c in ch), JOURNAL)
        ents = [c for c in ch if c["kind"] == "entry"]
        self.assertEqual(len(ents), 6)
        for c in ents:
            c["body"], c["suffix"] = chron.split_suffix(c["text"])
        es = [chron.journal_entry(c) for c in ents]
        self.assertEqual(es[0]["ts"], "2026-09-25T19:00+04:00")
        self.assertEqual(es[1]["ts"], "2026-09-26T05:31+04:00")  # «Z» → GMT+4
        self.assertEqual((es[2]["task"], es[2]["project"]), ("TK-063", "П-12"))
        self.assertEqual(es[4]["kind"], "prereg")
        self.assertIn("продолжение", ents[4]["body"])
        self.assertEqual(es[5]["fields"]["col3"], "без шапки")

    def test_marks(self):
        pm = lambda body, kind="result", title="t", fields=None, project="П-42": chron.parse_marks(kind, title, body, fields, project)
        m = pm("ЛОНГ — опровергнуто: база A −$8,08; ШОРТ — не ясно")
        self.assertIsNone(m.get("verdict"))  # стороны разные — поле пусто, причина записана
        self.assertTrue(m["parsed"]["verdict"].startswith("—: несколько"))
        m = pm("сетка: **«не ясно»** — шорт клетка «не работает»; **327 сделок, +180,83 $, Sharpe 3,41**")
        self.assertEqual((m["verdict"], m["trades"], m["usd"], m["sharpe"]), ("не ясно", 327, 180.83, 3.41))
        self.assertIsNone(pm("**181 сделка, +294 $**", project="П-21").get("usd"))  # ставка не 500 $ — не пересчитывается
        self.assertIsNone(pm("**плюсовые 312 сделок +1 493,8 $**").get("usd"))  # часть сделок — не итог
        self.assertIsNone(pm("вердикт «подтверждено» недостижим").get("verdict"))
        self.assertIsNone(pm("прошли 10, опровергнуты 38").get("verdict"))
        self.assertIsNone(pm("не ясно", kind="prereg").get("verdict"))
        self.assertEqual(pm("Найдено на этих данных: нет (описательно).", kind="step")["same_data"], 0)
        self.assertEqual(pm("порог выбран на тех же 60 сутках")["same_data"], 1)
        self.assertNotIn("same_data", {k for k, v in pm("ничего").items() if v is not None and k != "parsed"})
        f = {"P&L $1000": "−$41 (a45 обе стороны)", "n": "170", "вердикт": "мало данных"}
        m = pm("", kind="experiment", fields=f, project=None)
        self.assertEqual((m["usd"], m["trades"], m["verdict"]), (-20.5, 170, "не ясно"))
        self.assertIsNone(pm("", kind="experiment", fields={"что мерили": "x", "результат": "работает"}).get("verdict"))

    def test_protocol_sections(self):
        ch = chron.section_chunks(PROTO)
        self.assertEqual("".join(c["text"] for c in ch), PROTO)
        heads = [c["heading"] for c in ch if c["kind"] == "psec"]
        self.assertEqual(len(heads), 5)  # «# не заголовок» внутри ``` не считается
        root = Path(self.tmp.name)
        (root / "docs" / "research").mkdir(parents=True)
        (root / "docs" / "research" / "P-90-test.md").write_text(PROTO, encoding="utf-8", newline="\n")
        docs = chron.build_docs(root, ["docs/research/P-90-test.md"], [], None)
        secs = [c["sec"] for c in docs[0]["chunks"] if c["kind"] == "psec"]
        self.assertEqual([s["num"] for s in secs], [None, "1", "13.28", "13.28 п. 7", None])
        self.assertEqual([s["prereg"] for s in secs], [0, 0, 1, 1, 0])
        self.assertEqual(secs[2]["prereg_at"], "2026-11-19")
        st = self.st.import_docs(docs, [])
        self.assertEqual(st["docs/research/P-90-test.md"]["rows"], 5)
        self.assertEqual(self.st.reconstruct("docs/research/P-90-test.md"), PROTO)
        # повторный перенос изменённого файла: новый раздел — новая строка, изменённый — поправка, старые не тронуты
        (root / "docs" / "research" / "P-90-test.md").write_text(PROTO.replace("текст\n", "текст 2\n") + "## 14. Новое\n", encoding="utf-8", newline="\n")
        docs = chron.build_docs(root, ["docs/research/P-90-test.md"], [], None)
        st = self.st.import_docs(docs, [])["docs/research/P-90-test.md"]
        self.assertEqual((st["new"], st["amended"], st["reused"]), (1, 1, 4))
        self.assertEqual(self.st.check_db(), [])
        # новый раздел в середине, заголовки подпунктов повторяют старые («7. Подпункт»): не поправки, место — между соседями
        v3 = PROTO.replace("текст\n", "текст 2\n").replace("## Приложение А", "## 13.29 Новый — записано 20.11.2026 ДО счёта\n"
                                                            "#### 7. Подпункт\nдругое\n#### 11a. Буква\nб\n## Приложение А") + "## 14. Новое\n"
        (root / "docs" / "research" / "P-90-test.md").write_text(v3, encoding="utf-8", newline="\n")
        docs = chron.build_docs(root, ["docs/research/P-90-test.md"], [], None)
        st = self.st.import_docs(docs, [])["docs/research/P-90-test.md"]
        self.assertEqual((st["new"], st["amended"]), (3, 0))
        self.assertEqual(self.st.reconstruct("docs/research/P-90-test.md"), v3)
        rows = self.st.c.execute("SELECT num, amends FROM psec WHERE protocol='P-90-test' AND (amends IS NULL OR num IS NOT NULL) ORDER BY ord, id").fetchall()
        nums = [r[0] for r in rows]
        self.assertEqual(nums[-5:], ["13.29", "13.29 п. 7", "13.29 п. 11a", None, "14"])  # новые — до «Приложения», не в конце
        self.assertEqual(sum(1 for r in rows if r[0] and r[0].startswith("13.29") and r[1]), 0)
        self.assertEqual(self.st.check_db(), [])

    def test_crlf_checkout_same_rows(self):
        """Файл после checkout с autocrlf (CRLF) — те же строки, что блоб git (LF): повторный перенос ничего не дублирует."""
        root = Path(self.tmp.name)
        (root / "docs" / "research").mkdir(parents=True)
        f = root / "docs" / "research" / "JOURNAL.md"
        f.write_bytes(JOURNAL.replace("\n", "\r\n").encode("utf-8"))
        self.st.import_docs(chron.build_docs(root, ["docs/research/JOURNAL.md"], [], None), [])
        f.write_bytes(JOURNAL.encode("utf-8"))
        st = self.st.import_docs(chron.build_docs(root, ["docs/research/JOURNAL.md"], [], None), [])["docs/research/JOURNAL.md"]
        self.assertEqual(st.get("unchanged"), 1)
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM entry WHERE body LIKE '%' || char(13) || '%'").fetchone()[0], 0)
        self.assertEqual(self.st.reconstruct("docs/research/JOURNAL.md"), JOURNAL)


def independent_count(path):
    """Счёт записей в md, независимый от разбора chron: строки таблиц (без шапки), блоки журнала, заголовки протоколов вне ```."""
    lines = (ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    sep = re.compile(r"^\|[\s:|-]*-[\s:|-]*\|?\s*$")
    if Path(path).name.startswith("P-"):
        n, fence = 0, None
        for ln in lines:
            m = re.match(r"^\s{0,3}(```|~~~)", ln)
            if m:
                fence = None if fence == m.group(1) else (fence or m.group(1))
                continue
            if fence is None and re.match(r"^#{1,6}\s", ln):
                n += 1
        return n
    n = 0
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if ln.startswith("|") and not sep.match(ln) and not sep.match(nxt):
            n += 1
        elif path.endswith("JOURNAL.md") and re.match(r"^(## |\[?\d\d\.\d\d( \d{1,2}:\d\d)? ·)", ln):
            n += 1
    return n


class TestRealMigration(Base):
    def test_all_sources_lossless(self):
        paths = list(chron.DEFAULT_SOURCES) + chron.protocol_files(ROOT)
        amap = chron.archive_map(ROOT, paths)
        docs = chron.build_docs(ROOT, paths, chron.load_kb_nodes(ROOT), chron.Git(ROOT), amap)
        st = self.st.import_docs(docs, [list(n) for n in chron.load_kb_nodes(ROOT)])
        total = 0
        for d in docs:
            src = amap.get(d["path"], d["path"])
            self.assertEqual(st[d["path"]]["rows"], independent_count(src), d["path"])
            self.assertEqual(self.st.reconstruct(d["path"]).encode("utf-8"), (ROOT / src).read_bytes().replace(b"\r\n", b"\n"), d["path"])
            total += st[d["path"]]["rows"]
        self.assertEqual(total, self.st.c.execute("SELECT (SELECT count(*) FROM entry) + (SELECT count(*) FROM psec)").fetchone()[0])
        again = self.st.import_docs(docs, None)
        self.assertEqual(sum(s.get("new", 0) + s.get("amended", 0) for s in again.values()), 0)
        self.assertEqual(self.st.check_db(), [])


class _Root:
    def __init__(self, root):
        self.root = Path(root)


def mrow(eid, **kw):
    r = {"entry": eid, "side": "long", "cell": "c", "uni": "h775", "win": "all", "trades": 10, "usd": 5.0, "mean_usd": 0.5, "sharpe": 1.0,
         "verdict": None, "same_data": None, "from_text": 0, "src": "docs/x.json", "src_commit": None, "src_ptr": None, "extra": None}
    r.update(kw)
    return r


class TestMetrics(Base):
    """TK-647: таблица metric, поправка номера раздела, дата «до счёта» от родителя."""

    def test_migrate_v3_to_v4(self):
        db = os.path.join(self.tmp.name, "v3.sqlite")
        c = sqlite3.connect(db, isolation_level=None)
        for stmt in chron.split_sql(chron.SCHEMA) + chron.split_sql(chron.MIGRATE[3]):
            c.execute(stmt)
        c.execute(chron.FTS_SQL)
        c.execute("PRAGMA user_version=3")
        c.close()
        st = chron.Store(chron.connect(db))
        try:
            self.assertEqual(st.c.execute("PRAGMA user_version").fetchone()[0], chron.SCHEMA_VERSION)
            for t in ("metric", "psec_num", "psec_cur", "metric_noupdate", "metric_nodelete", "psec_num_noupdate", "psec_num_nodelete"):
                self.assertIsNotNone(st.c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone(), t)
            st.new_protocol("P-90-test", "Тест")
            a = st.add_psec({"protocol": "P-90", "body": "## 1. Вопрос\nчто\n"}, [])
            st.flag_psec(a, True, "2026-11-20", "проверка")  # psec_flag из v3 читается видом v4
            self.assertEqual(st.get(a)["psec"]["prereg_at"], "2026-11-20")
            self.assertEqual(st.check_db(), [])
        finally:
            st.c.close()

    def test_metric_append_only_dedup(self):
        e = int(self.st.add_entry(entry("числа"), [])[2:])
        self.assertEqual(self.st.add_metrics([mrow(e), mrow(e, side="short", usd=-3.0)]), {"new": 2, "same": 0})
        self.assertEqual(self.st.add_metrics([mrow(e)]), {"new": 0, "same": 1})  # повтор не пишется
        for sql in ("UPDATE metric SET usd=0", "DELETE FROM metric"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.st.c.execute(sql)
        for bad in (mrow(e, verdict="хорошо"), mrow(e, side="up"), mrow(e, src=""), mrow(999)):
            with self.assertRaises(chron.ChronError):
                self.st.add_metrics([mrow(e, cell="новая"), bad])
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM metric").fetchone()[0], 2)  # битая партия не пишет ничего
        self.assertEqual(self.st.check_db(), [])

    def test_migrate_v4_to_v5(self):
        db = os.path.join(self.tmp.name, "v4.sqlite")
        c = sqlite3.connect(db, isolation_level=None)
        for stmt in chron.split_sql(chron.SCHEMA) + [x for v in (3, 4) for x in chron.split_sql(chron.MIGRATE[v])]:
            c.execute(stmt)
        c.execute(chron.FTS_SQL)
        c.execute("PRAGMA user_version=4")
        c.close()
        st = chron.Store(chron.connect(db, ro=True))  # чтение тоже переводит схему (один раз)
        try:
            self.assertEqual(st.c.execute("PRAGMA user_version").fetchone()[0], chron.SCHEMA_VERSION)
            for t in ("metric_gap", "metric_gap_noupdate", "metric_gap_nodelete", "metric_fix", "metric_fix_noupdate", "metric_fix_nodelete", "metric_cur"):
                self.assertIsNotNone(st.c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone(), t)
        finally:
            st.c.close()
        st = chron.Store(chron.connect(db))
        try:
            self.assertEqual(st.check_db(), [])
        finally:
            st.c.close()

    def test_gap_append_only_and_coverage(self):
        a = int(self.st.add_entry(entry("предрег", kind="experiment"), [])[2:])
        b = int(self.st.add_entry(entry("с числами", kind="result"), [])[2:])
        self.st.add_entry(entry("пусто", kind="verdict"), [])
        self.st.add_metrics([mrow(b)])
        g = {"entry": a, "reason": "предрегистрация", "note": "итога нет", "src": "docs/x.md", "src_ptr": "строка 3"}
        self.assertEqual(self.st.add_gaps([g]), {"new": 1, "same": 0})
        self.assertEqual(self.st.add_gaps([g]), {"new": 0, "same": 1})  # повтор (запись + причина) не пишется
        for bad in (dict(g, reason="лень"), dict(g, entry=999)):
            with self.assertRaises(chron.ChronError):
                self.st.add_gaps([dict(g, reason="не запускалось"), bad])
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM metric_gap").fetchone()[0], 1)  # битая партия не пишет ничего
        for sql in ("UPDATE metric_gap SET note=''", "DELETE FROM metric_gap"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.st.c.execute(sql)
        cov = self.st.metrics_coverage()[0]
        self.assertEqual((cov["outcome"], cov["outcome_with"], cov["outcome_gap"]), (3, 1, 1))
        self.assertEqual([r["id"] for r in self.st.query({"nums": "пометка"})["rows"]], [a])
        self.assertEqual([r["id"] for r in self.st.query({"nums": "без объяснения"})["rows"]], [3])
        self.assertEqual(self.st.query({"nums": "пометка"})["rows"][0]["gap"], "предрегистрация")
        self.assertEqual(self.st.get("L-%d" % a)["gaps"][0]["note"], "итога нет")
        self.assertEqual(self.st.check_db(), [])
        rc, out = self.cli("show", "L-%d" % a)
        self.assertIn("нет чисел: предрегистрация — итога нет", out)
        self.assertNotIn("снята", out)
        # числа добавлены позже: пометка остаётся в БД, но «снята», из покрытия и фильтра «пометка» уходит
        self.st.add_metrics([mrow(a)])
        self.st.c.commit()
        self.assertEqual(self.st.get("L-%d" % a)["gaps"][0]["lifted"], 1)
        self.assertEqual(self.st.query({"nums": "пометка"})["rows"], [])
        self.assertEqual(self.st.metrics_coverage()[0]["outcome_gap"], 0)
        rc, out = self.cli("show", "L-%d" % a)
        self.assertIn("снята: числа добавлены (было «нет чисел»): предрегистрация — итога нет", out)
        page = chron_site.Site(self.db, None).entry(a)
        self.assertIn("снята: числа добавлены · <s>нет чисел: предрегистрация", page)
        self.assertNotIn("<b>Нет чисел", page)
        gc.collect()  # связь Site (ro) держит файл на Windows

    def test_metric_fix_replaces_row(self):
        a = int(self.st.add_entry(entry("с ошибкой места", kind="result"), [])[2:])
        old = dict(mrow(a), from_text=1, src="docs/x.md", src_ptr="строка 65")
        new = dict(old, src_ptr="строка 29")
        self.st.add_metrics([old, new])
        h = chron.sha(json.dumps([a] + [new.get(k) for k in chron.METRIC_COLS], ensure_ascii=False, default=str))
        fx = {"entry": a, "src": "docs/x.md", "cell": "c", "replaces": "строка 65", "h": h}
        self.assertEqual(self.st.add_metric_fixes([fx]), {"new": 1, "same": 0})
        self.assertEqual(self.st.add_metric_fixes([fx]), {"new": 0, "same": 1})  # повтор не пишется
        with self.assertRaises(chron.ChronError):
            self.st.add_metric_fixes([dict(fx, replaces="строка 99")])  # прежней строки нет
        for sql in ("UPDATE metric_fix SET reason=''", "DELETE FROM metric_fix"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.st.c.execute(sql)
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM metric WHERE entry=?", (a,)).fetchone()[0], 2)  # прежняя осталась
        g = self.st.get("L-%d" % a)
        self.assertEqual((g["metrics_total"], [m["src_ptr"] for m in g["metrics"]]), (1, ["строка 29"]))
        self.assertEqual([(x["old_ptr"], x["new_ptr"]) for x in g["metric_fixes"]], [("строка 65", "строка 29")])
        self.assertEqual(self.st.metrics_query({"entry": a})["total"], 1)
        self.assertEqual(self.st.stats()["metrics"], 1)
        self.assertEqual(self.st.check_db(), [])
        rc, out = self.cli("show", "L-%d" % a)
        self.assertIn("(строка 65) → #", out)
        page = chron_site.Site(self.db, None).entry(a)
        self.assertIn("заменено строк: 1", page)
        gc.collect()

    def test_add_entry_with_metrics(self):
        n = self.st.add_entry(entry("вердикт"), [], None, [mrow(None, trades=7)])
        q = self.st.metrics_query({"entry": n})
        self.assertEqual((q["total"], q["rows"][0]["trades"]), (1, 7))

    def test_metrics_query_filter_sort(self):
        a = int(self.st.add_entry(entry("а", project="П-42", ts="2026-11-01"), [])[2:])
        b = int(self.st.add_entry(entry("б", project="П-02", ts="2026-11-02"), [])[2:])
        self.st.add_metrics([mrow(a, cell="L_n24", usd=10.0, sharpe=2.0, trades=100),
                             mrow(a, side="short", cell="S_n24", usd=-4.0, sharpe=None, trades=50, uni="o566"),
                             mrow(b, cell="x", usd=None, trades=5, from_text=1, verdict="не ясно")])
        q = self.st.metrics_query
        self.assertEqual(q({})["total"], 3)
        self.assertEqual([r["cell"] for r in q({"sort": "-usd"})["rows"]], ["L_n24", "S_n24", "x"])  # пустое — в конце
        self.assertEqual([r["cell"] for r in q({"sort": "usd"})["rows"]], ["S_n24", "L_n24", "x"])
        self.assertEqual(q({"sort": "-sharpe"})["rows"][0]["cell"], "L_n24")
        self.assertEqual(q({"project": "P-42"})["total"], 2)
        self.assertEqual(q({"side": "short"})["rows"][0]["cell"], "S_n24")
        self.assertEqual(q({"uni": "o566"})["total"], 1)
        self.assertEqual(q({"cell": "n24"})["total"], 2)
        self.assertEqual(q({"text": "1"})["rows"][0]["cell"], "x")
        self.assertEqual(q({"verdict": "—"})["total"], 2)
        self.assertEqual(q({"min_trades": 60})["total"], 1)
        self.assertEqual(q({"has_usd": 1})["total"], 2)
        self.assertEqual(q({"sort": "-date"})["rows"][0]["entry"], b)
        with self.assertRaises(chron.ChronError):
            q({"sort": "цвет"})
        cov = {r["project"]: r for r in self.st.metrics_coverage()}
        self.assertEqual((cov["П-42"]["with_metrics"], cov["П-42"]["rows"], cov["П-02"]["text_only"]), (1, 2, 1))

    def test_cli_add_metric(self):
        src = "docs/research/freeze/tk626-frozen.json"
        rc, out = self.cli("add", "--title", "числа П-90", "--project", "P-90", "--body", "т", "--metric",
                           "side=long; cell=L_n24; uni=h775; win=all; trades=181; usd=294,31; sharpe=5.11; verdict=не ясно; same_data=1; src=%s#selection" % src)
        self.assertEqual((rc, out.strip()), (0, "L-1"))
        m = self.st.metrics_query({"entry": "L-1"})["rows"][0]
        self.assertEqual((m["side"], m["trades"], m["usd"], m["same_data"], m["src_ptr"]), ("long", 181, 294.31, 1, "selection"))
        self.assertTrue(m["src_commit"])  # без @коммита — последний коммит файла
        self.assertAlmostEqual(m["mean_usd"], 294.31 / 181)
        for bad in ("side=long; trades=1; src=docs/нет-такого.json", "side=long; trades=1; src=%s@0000000deadbeef" % src,
                    "side=long; trades=1; src=AGENTS.md", "side=вверх; trades=1; src=%s" % src, "trades=1", "цвет=1; src=%s" % src):
            rc, _ = self.cli("add", "--title", "битое", "--body", "т", "--metric", bad)
            self.assertEqual(rc, 2, bad)
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM entry").fetchone()[0], 1)  # отказ — без записи
        rc, _ = self.cli("add", "--title", "из текста", "--body", "т", "--metric", "side=both; trades=3; usd=1; text=1; src=AGENTS.md")
        self.assertEqual(rc, 0)
        rc, out = self.cli("metrics", "--sort=-usd")
        self.assertIn("L_n24", out)

    def test_num_psec(self):
        self.st.new_protocol("P-90-test", "Тест")
        a = self.st.add_psec({"protocol": "P-90", "body": "## 8. Раздел\nтекст\n"}, [])
        self.st.num_psec(a, "8а", "пересборка")
        self.assertEqual(self.st.get(a)["psec"]["num"], "8а")
        self.assertEqual(self.st.resolve_psec("P-90", "8а"), a)
        self.assertEqual(self.st.c.execute("SELECT num FROM psec WHERE id=?", (int(a[3:]),)).fetchone()[0], "8")  # строка не тронута
        self.st.num_psec(a, "8б", "вторая")
        self.assertEqual(self.st.get(a)["psec"]["num"], "8б")  # последняя
        for sql in ("UPDATE psec_num SET num='x'", "DELETE FROM psec_num"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.st.c.execute(sql)
        for args in ((a, " ", "r"), (a, "9", " "), ("PS-999", "9", "r")):
            with self.assertRaises(chron.ChronError):
                self.st.num_psec(*args)
        rc, _ = self.cli("psec", "num", a, "--num", "8в", "--reason", "cli")
        self.assertEqual((rc, self.st.get(a)["psec"]["num"]), (0, "8в"))
        self.assertEqual(self.st.check_db(), [])

    def test_list_order_date_only_same_day(self):
        day = chron.now()[:10]
        self.st.add_entry(entry("с часом", ts=day + "T00:01+04:00"), [])
        self.st.add_entry(entry("без часа", ts=day), [])  # создана позже — выше
        self.st.add_entry(entry("старая без часа", ts="2026-01-02"), [])
        self.assertEqual([r["title"] for r in self.st.query({})["rows"]], ["без часа", "с часом", "старая без часа"])

    def test_prereg_date_from_parent(self):
        text = ("# P-91\n## 13. Раздел — записано 18.11.2026 до счёта\nа\n"
                "### 13.5 Правило (до счёта)\nб\n### 13.6 Своё — записано 19.11.2026 до счёта\nв\n")
        root = Path(self.tmp.name)
        (root / "docs" / "research").mkdir(parents=True)
        (root / "docs" / "research" / "P-91-test.md").write_text(text, encoding="utf-8", newline="\n")
        secs = [c["sec"] for c in chron.build_docs(root, ["docs/research/P-91-test.md"], [], None)[0]["chunks"] if c["kind"] == "psec"]
        got = {s["num"]: (s["prereg"], s["prereg_at"]) for s in secs}
        self.assertEqual(got["13.5"], (1, "2026-11-18"))  # своя метка без даты — дата родителя
        self.assertEqual(got["13.6"], (1, "2026-11-19"))


def crow(i, **kw):
    r = {"side": "short", "uni": "h775", "win": "01.07–13.11", "ckey": "k=%06d;n=24;q=0.3" % i, "bin": "abcdef0123456789", "cell": "S_%d" % i,
         "trades": 100 + i % 7, "usd": 12.5 - i % 5, "mean_usd": 0.125, "sharpe": 1.5, "extra": {"dd": 0.11, "dirs": {"07": 3}},
         "src": "/data/p42-tk674/cells/run1.jsonl", "src_ptr": "строка %d" % i}
    r.update(kw)
    return r


class TestCells(Base):
    """TK-674 (миграция 7): хранилище клеток бэктеста в таблице metric — ckey / bin, cells_put / cells_lookup / cells_count."""

    def old_db(self, name, versions):
        db = os.path.join(self.tmp.name, name)
        c = sqlite3.connect(db, isolation_level=None)
        for stmt in chron.split_sql(chron.SCHEMA) + [x for v in versions for x in chron.split_sql(chron.MIGRATE[v])]:
            c.execute(stmt)
        c.execute(chron.FTS_SQL)
        c.execute("PRAGMA user_version=%d" % max(versions))
        return c, db

    def test_migrate_v6_to_v7_old_rows_untouched(self):
        c, db = self.old_db("v6.sqlite", (3, 4, 5, 6))
        c.execute("INSERT INTO entry(kind, title, body, h, created) VALUES ('verdict', 'старая', 'текст', 'h', '2026-11-20T10:00+04:00')")
        c.execute("INSERT INTO fts(node, title, body) VALUES ('L-1', 'старая', 'текст')")
        r = mrow(1, cell="L_n24", usd=294.31)
        h = chron.sha(json.dumps([1] + [r.get(k) for k in chron.METRIC_COLS], ensure_ascii=False, default=str))
        c.execute("INSERT INTO metric(entry, side, cell, uni, win, trades, usd, from_text, src, h, created) VALUES (1,'long','L_n24','h775','all',10,294.31,0,'docs/x.json',?,'t')", (h,))
        c.close()
        st = chron.Store(chron.connect(db, ro=True))  # чтение тоже переводит схему
        try:
            self.assertEqual(st.c.execute("PRAGMA user_version").fetchone()[0], chron.SCHEMA_VERSION)
            cols = [x[1] for x in st.c.execute("PRAGMA table_info(metric)")]
            self.assertEqual(cols[-2:], ["ckey", "bin"])
            self.assertIn("metric_cell", {x[0] for x in st.c.execute("SELECT name FROM sqlite_master WHERE type='index'")})
            row = st.c.execute("SELECT ckey, bin, h, usd FROM metric_cur").fetchone()  # вид подхватил столбцы
            self.assertEqual((row["ckey"], row["bin"], row["h"], row["usd"]), (None, None, h, 294.31))
        finally:
            st.c.close()
        st = chron.Store(chron.connect(db))
        try:
            self.assertEqual(st.add_metrics([r]), {"new": 0, "same": 1})  # прежняя строка не пишется повторно: h не изменился
            for sql in ("UPDATE metric SET usd=0", "DELETE FROM metric"):
                with self.assertRaises(sqlite3.DatabaseError):
                    st.c.execute(sql)
            self.assertEqual(st.cells_put(1, [crow(1)]), {"new": 1, "same": 0, "diff": 0})
            self.assertEqual(st.check_db(), [])
            self.assertEqual((st.stats()["metrics"], st.stats()["cells"]), (1, 1))
        finally:
            st.c.close()

    def test_put_lookup_idempotent(self):
        e = int(self.st.add_entry(entry("клетки", kind="experiment"), [])[2:])
        rows = [crow(i) for i in range(5)] + [crow(9, bin="0123456789abcdef", ckey="k=000000;n=24;q=0.3")]
        self.assertEqual(self.st.cells_put("L-%d" % e, rows), {"new": 6, "same": 0, "diff": 0})
        self.assertEqual(self.st.cells_put(e, rows[:3] + [crow(5)]), {"new": 1, "same": 3, "diff": 0})  # повтор не пишется, новая — пишется
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM metric").fetchone()[0], 7)
        keys = ["k=%06d;n=24;q=0.3" % i for i in range(7)] + ["нет такой"]
        got = self.st.cells_lookup("short", "h775", "01.07–13.11", keys)
        self.assertEqual(sorted(got), sorted(keys[:6]))  # 6 разных ключей; k=000000 есть у двух бинарников → последняя запись
        self.assertEqual(got["k=000000;n=24;q=0.3"]["bin"], "0123456789abcdef")
        g = self.st.cells_lookup("short", "h775", "01.07–13.11", keys, bins=["abcdef0123456789"])
        self.assertEqual(g["k=000000;n=24;q=0.3"]["bin"], "abcdef0123456789")
        self.assertEqual(g["k=000003;n=24;q=0.3"]["extra"], {"dd": 0.11, "dirs": {"07": 3}})  # extra разобран из JSON
        self.assertEqual((g["k=000003;n=24;q=0.3"]["trades"], g["k=000003;n=24;q=0.3"]["entry"]), (103, e))
        self.assertEqual(self.st.cells_lookup("short", "h775", "01.07–13.11", keys, bins="0123456789abcdef").keys(), {"k=000000;n=24;q=0.3"})
        for side, uni, win in (("long", "h775", "01.07–13.11"), ("short", "o566", "01.07–13.11"), ("short", "h775", "all")):
            self.assertEqual(self.st.cells_lookup(side, uni, win, keys), {})  # сторона / вселенная / окно — часть ключа
        self.assertEqual(self.st.cells_lookup("short", "h775", "01.07–13.11", []), {})
        # те же ключи с другими числами — «diff» (движок обязан быть детерминированным), прежняя строка остаётся
        self.assertEqual(self.st.cells_put(e, [crow(1, usd=99.0)]), {"new": 0, "same": 1, "diff": 1})
        self.assertEqual(self.st.cells_lookup("short", "h775", "01.07–13.11", keys[1:2])[keys[1]]["usd"], 11.5)
        cnt = self.st.cells_count()
        self.assertEqual([(x["bin"], x["n"]) for x in cnt], [("0123456789abcdef", 1), ("abcdef0123456789", 6)])
        self.assertEqual(self.st.cells_count(["0123456789abcdef"])[0]["n"], 1)

    def test_put_refusals_write_nothing(self):
        e = int(self.st.add_entry(entry("клетки"), [])[2:])
        for bad in (crow(7, bin="XYZ"), crow(7, bin=None), crow(7, bin="abcdef0"), crow(7, side="up"), crow(7, ckey=""), crow(7, uni=None), crow(7, win=""), crow(7, src=None)):
            with self.assertRaises(chron.ChronError):
                self.st.cells_put(e, [crow(1), bad])
        for ent in (999, "L-999", "мусор"):
            with self.assertRaises(chron.ChronError):
                self.st.cells_put(ent, [crow(1)])
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM metric").fetchone()[0], 0)  # битая партия не пишет ничего

    def test_cells_append_only_and_check(self):
        e = int(self.st.add_entry(entry("клетки"), [])[2:])
        self.st.cells_put(e, [crow(i) for i in range(3)])
        for sql in ("UPDATE metric SET usd=0", "UPDATE metric SET ckey='x'", "DELETE FROM metric"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.st.c.execute(sql)
        self.assertEqual(self.st.check_db(), [])  # src клетки — путь на srv, не файл в git: check не краснеет
        self.assertEqual(self.st.metrics_sources(), [])
        self.st.c.execute("BEGIN")
        self.st.c.execute("INSERT INTO metric(entry, side, uni, win, src, h, created, ckey) VALUES (?, 'long', 'u', 'w', 's', 'hh', 't', 'k')", (e,))  # ckey без bin
        self.assertEqual(len(self.st.check_db()), 1)
        self.st.c.execute("ROLLBACK")

    def test_cells_not_in_general_lists(self):
        a = int(self.st.add_entry(entry("человеческие числа", project="П-42"), [])[2:])
        b = int(self.st.add_entry(entry("клетки", project="П-42"), [])[2:])
        self.st.add_metrics([mrow(a)])
        self.st.cells_put(b, [crow(i) for i in range(4)])
        self.assertEqual(self.st.metrics_query({})["total"], 1)  # общий список — без клеток
        self.assertEqual(self.st.metrics_query({"project": "П-42"})["total"], 1)
        self.assertEqual(self.st.metrics_query({"entry": b})["total"], 4)  # по записи — все
        self.assertEqual(self.st.metrics_query({"cells": "1"})["total"], 4)
        self.assertEqual(self.st.metrics_query({"cells": "1"})["rows"][0]["ckey"][:2], "k=")
        self.assertEqual(self.st.metrics_facets()["uni"], ["h775"])
        self.assertEqual([c["rows"] for c in self.st.metrics_coverage() if c["project"] == "П-42"], [1])
        self.assertEqual(self.st.get("L-%d" % b)["metrics_total"], 4)
        site = chron_site.Site(self.db, None)
        self.assertIn("4 строк", site.metrics_page({"cells": ["1"]}))
        self.assertIn("1 строк", site.metrics_page({}))
        self.assertIn("Числа (4)", site.entry(b))
        gc.collect()

    def test_batch_10k_seconds(self):
        import time
        e = int(self.st.add_entry(entry("клетки"), [])[2:])
        rows = [crow(i) for i in range(10000)]
        t = time.time()
        self.assertEqual(self.st.cells_put(e, rows), {"new": 10000, "same": 0, "diff": 0})
        t_put = time.time() - t
        t = time.time()
        got = self.st.cells_lookup("short", "h775", "01.07–13.11", [r["ckey"] for r in rows] + ["нет"], bins=["abcdef0123456789"])
        t_get = time.time() - t
        t = time.time()
        self.assertEqual(self.st.cells_put(e, rows), {"new": 0, "same": 10000, "diff": 0})
        t_again = time.time() - t
        self.assertEqual(len(got), 10000)
        self.assertLess(t_put, 10)
        self.assertLess(t_get, 5)
        self.assertLess(t_again, 10)
        self.assertEqual(self.st.check_db(), [])

    def test_cli_cells_lookup_count(self):
        e = int(self.st.add_entry(entry("клетки"), [])[2:])
        self.st.cells_put(e, [crow(i) for i in range(3)])
        kf = os.path.join(self.tmp.name, "keys.json")
        Path(kf).write_text(json.dumps(["k=000001;n=24;q=0.3", "k=000002;n=24;q=0.3", "нет"], ensure_ascii=False), encoding="utf-8")
        rc, out = self.cli("cells-lookup", "--side", "short", "--uni", "h775", "--win", "01.07–13.11", "--keys-file", kf)
        self.assertEqual((rc, json.loads(out)), (0, ["k=000001;n=24;q=0.3", "k=000002;n=24;q=0.3"]))
        rc, out = self.cli("cells-lookup", "--side", "short", "--uni", "h775", "--win", "01.07–13.11", "--keys-file", kf, "--bin", "ffffffffffff")
        self.assertEqual((rc, json.loads(out)), (0, []))
        rc, out = self.cli("cells-lookup", "--side", "short", "--uni", "h775", "--win", "01.07–13.11", "--keys-file", kf, "--bin", "abcdef0123456789,1111111111", "--rows")
        self.assertEqual(sorted(json.loads(out)), ["k=000001;n=24;q=0.3", "k=000002;n=24;q=0.3"])
        Path(kf).write_text("k=000000;n=24;q=0.3\nнет\n", encoding="utf-8")  # по ключу в строке
        rc, out = self.cli("cells-lookup", "--side", "short", "--uni", "h775", "--win", "01.07–13.11", "--keys-file", kf)
        self.assertEqual(json.loads(out), ["k=000000;n=24;q=0.3"])
        rc, out = self.cli("cells-count")
        self.assertIn("всего клеток 3", out)
        self.assertIn("abcdef0123456789", out)
        rc, out = self.cli("cells-count", "--bin", "ffffffffffff")
        self.assertIn("всего клеток 0", out)

    def test_rpc_cells(self):
        """Путь «с ПК на srv»: cells_put / cells_lookup / cells_count допущены к вызову по сети."""
        import base64
        e = int(self.st.add_entry(entry("клетки"), [])[2:])
        env = dict(os.environ, CHRON_RPC="1", CHRON_DB=self.db, PYTHONUTF8="1")

        def rpc(op, *args):
            payload = base64.b64encode(json.dumps({"op": op, "args": args, "kw": {}}, ensure_ascii=False).encode("utf-8")).decode()
            prog = "RPC = %r\n" % payload + (TOOLS / "chron.py").read_text(encoding="utf-8")
            r = subprocess.run([sys.executable, "-"], input=prog.encode("utf-8"), capture_output=True, env=env)
            m = re.search(r"^CHRON-RESULT (.*)$", r.stdout.decode("utf-8"), re.M)
            self.assertTrue(m, r.stderr.decode("utf-8", "replace"))
            return json.loads(m.group(1))
        self.assertEqual(rpc("cells_put", e, [crow(1)])["ok"], {"new": 1, "same": 0, "diff": 0})
        self.assertEqual(list(rpc("cells_lookup", "short", "h775", "01.07–13.11", ["k=000001;n=24;q=0.3"], None)["ok"]), ["k=000001;n=24;q=0.3"])
        self.assertEqual(rpc("cells_count", None)["ok"][0]["n"], 1)


class TestRelink(Base):
    """TK-704 (миграция 9): перенос файла в git — таблица file_move, `check` находит файл по новому пути."""

    def test_move_path(self):
        mv = [("docs/findings/a/", "docs/archive/findings/a/"), ("docs/x.md", "docs/archive/x.md"), ("docs/findings/a/keep.md", "docs/keep.md"),
              ("docs/archive/x.md", "docs/archive/y.md")]
        for p, want in (("docs/findings/a/res/c.json", "docs/archive/findings/a/res/c.json"), ("docs/findings/a", "docs/archive/findings/a"),
                        ("docs/x.md#sec", "docs/archive/y.md#sec"), ("docs/x.md:12-20", "docs/archive/y.md:12-20"),  # цепочка x → archive/x → archive/y
                        ("docs/findings/a/keep.md", "docs/keep.md"), ("docs/y.md", "docs/y.md"), ("docs/findings/ab/z", "docs/findings/ab/z")):
            self.assertEqual(chron.move_path(p, mv), want, p)
        with self.assertRaises(chron.ChronError):
            chron.move_path("a/b", [("a/", "c/"), ("c/", "a/")])
        self.assertEqual(chron.norm_move(r"./docs\a.md", "docs/b.md"), ("docs/a.md", "docs/b.md"))
        for bad in (("a/", "b"), ("", "b"), ("a", "a/")):
            with self.assertRaises(chron.ChronError):
                chron.norm_move(*bad)

    def test_relink_append_only(self):
        self.assertEqual(self.st.relink([["a/", "b/"], ["c.md", "d.md"]], "TK-705", "t"), {"new": 2, "same": 0})
        self.assertEqual(self.st.relink([["a/", "b/"]]), {"new": 0, "same": 1})  # повтор не пишется
        for pairs in ([["a/", "z/"]], [["b/", "a/"]], [["new.md", "x.md"], ["c.md", "e.md"]]):  # другой new; цикл; конфликт во второй паре
            with self.assertRaises(chron.ChronError):
                self.st.relink(pairs)
        self.assertEqual(self.st.file_moves(), [["a/", "b/"], ["c.md", "d.md"]])  # отказ ничего не записал
        for sql in ("UPDATE file_move SET new='q'", "DELETE FROM file_move"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.st.c.execute(sql)
        self.assertEqual(self.st.check_db(), [])

    def test_check_follows_move(self):
        e = int(self.st.add_entry(entry("числа в перенесённом файле"), [])[2:])
        self.st.add_metrics([mrow(e, src="docs/zz-old/chron.py")])
        self.st.c.execute("INSERT INTO link VALUES ('L-%d', 'file:docs/zz-old/chron.py#x', 'file', 't', 'now')" % e)
        rc, out = self.cli("check")
        self.assertEqual(rc, 1)
        self.assertIn("битая ссылка", out)
        self.assertIn("число: файла нет в git", out)
        rc, out = self.cli("relink", "docs/zz-old/", "tools/", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("ссылок file: 1, источников чисел 1", out)
        self.assertEqual(self.st.file_moves(), [])  # dry-run ничего не пишет
        rc, out = self.cli("relink", "docs/zz-old/", "tools/", "--reason", "TK-705")
        self.assertIn("записано переносов новых 1", out)
        rc, out = self.cli("check")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cli("relink", "docs/zz-old2/", "docs/no-such-dir-tk704/")[0], 2)  # нового пути нет в git
        self.assertEqual(self.st.file_moves(), [["docs/zz-old/", "tools/"]])

    def test_migrate_v8_to_v9(self):
        db = os.path.join(self.tmp.name, "v8.sqlite")
        c = sqlite3.connect(db, isolation_level=None)
        for stmt in chron.split_sql(chron.SCHEMA) + [x for v in range(3, 9) for x in chron.split_sql(chron.MIGRATE[v])]:
            c.execute(stmt)
        c.execute(chron.FTS_SQL)
        c.execute("PRAGMA user_version=8")
        c.close()
        st = chron.Store(chron.connect(db))
        self.assertEqual(st.c.execute("PRAGMA user_version").fetchone()[0], chron.SCHEMA_VERSION)
        self.assertEqual(st.file_moves(), [])
        self.assertEqual(st.check_db(), [])
        st.c.close()


class TestBackup(Base):
    """TK-704: ночная копия — летопись без клеток бэктеста; полная копия с клетками — в заданный день недели или по --full."""

    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.tmp.name, "bk")
        e = int(self.st.add_entry(entry("запись с числами", body="текст"), ["TK-646"])[2:])
        self.st.add_metrics([mrow(e)])
        self.st.cells_put(e, [crow(i) for i in range(5)])

    def test_slim_backup_without_cells(self):
        rc, out = self.cli("backup", "--dir", self.out, "--full-day", "-1")
        self.assertEqual(rc, 0, out)
        stamp = sorted(Path(self.out).glob("chron-2*.sqlite"))
        self.assertEqual(len(stamp), 1)
        self.assertEqual(list(Path(self.out).glob("chron-full-*")), [])
        c = sqlite3.connect(str(stamp[0]))
        self.assertEqual(c.execute("SELECT count(*) FROM metric").fetchone()[0], 1)  # 1 «человеческая» строка, 5 клеток не взято
        self.assertEqual(c.execute("SELECT count(*) FROM metric WHERE ckey IS NOT NULL").fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT count(*) FROM entry").fetchone()[0], 1)
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], chron.SCHEMA_VERSION)
        self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual(c.execute("SELECT count(*) FROM fts").fetchone()[0], 1)
        c.close()
        copy = chron.Store(chron.connect(str(stamp[0])))  # копия — рабочая летопись: триггеры, FTS, снимки
        self.assertEqual(copy.check_db(), [])
        copy.c.close()
        lines = [json.loads(x) for x in stamp[0].with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(1 for x in lines if x["t"] == "metric"), 1)

    def test_full_backup_and_retention(self):
        for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
            for pre in ("chron-", "chron-full-"):
                (Path(self.out)).mkdir(exist_ok=True)
                (Path(self.out) / (pre + d + ".sqlite")).write_bytes(b"x")
                (Path(self.out) / (pre + d + ".jsonl")).write_bytes(b"x")
        rc, out = self.cli("backup", "--dir", self.out, "--full", "--keep", "2", "--keep-full", "2")
        self.assertEqual(rc, 0, out)
        names = sorted(p.name for p in Path(self.out).glob("*.sqlite"))
        self.assertEqual(len([n for n in names if n.startswith("chron-full-")]), 2)
        self.assertEqual(len([n for n in names if not n.startswith("chron-full-")]), 2)
        self.assertNotIn("chron-2026-01-01.sqlite", names)  # самая старая ночная ушла вместе с JSONL
        self.assertFalse((Path(self.out) / "chron-2026-01-01.jsonl").exists())
        full = sorted(Path(self.out).glob("chron-full-2*.sqlite"))[-1]
        c = sqlite3.connect(str(full))
        self.assertEqual(c.execute("SELECT count(*) FROM metric WHERE ckey IS NOT NULL").fetchone()[0], 5)  # полная — с клетками
        c.close()


class TestMetricFiles(unittest.TestCase):
    """chron_metrics: строки чисел из JSON / CSV / таблиц md на маленьких файлах."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = _Root(self.root)
        import chron_metrics
        self.cm = chron_metrics

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")
        return name

    def test_json(self):
        f = self.put("tk900-h775.json", json.dumps({
            "cells": {"L_n24": {"n": 10, "sum": 20.0, "sharpe": 1.5, "p": 0.04, "verdict": "не ясно: мало"},
                      "S_n24": {"trades": 4, "usd": -2.0, "months": [{"month": "2026-07", "n": 4, "sum": -2.0}]}},
            "diag": {"n": 1, "sum": 1}}))
        rows = self.cm.rows_from_json(self.repo, f, True)
        self.assertEqual(len(rows), 3)  # diag пропущен; S — итог + месяц
        l = next(r for r in rows if r["cell"] == "L_n24")
        self.assertEqual((l["side"], l["uni"], l["trades"], l["usd"], l["mean_usd"], l["sharpe"], l["verdict"], l["extra"]["p"]),
                         ("long", "h775", 10, 20.0, 2.0, 1.5, "не ясно", 0.04))
        m = next(r for r in rows if r["win"] == "2026-07")
        self.assertEqual((m["side"], m["trades"], m["usd"]), ("short", 4, -2.0))
        other = self.cm.rows_from_json(self.repo, f, False)  # не П-42: $ не пересчитан
        self.assertTrue(all(r["usd"] is None and "usd_raw" in r["extra"] for r in other))

    def test_csv(self):
        f = self.put("r.csv", "# заметка\nside,family,n,usd,b1_n,b1_usd,a_n,a_usd\nL,f1,10,5.0,99,9.0,3,1.0\nS,f2,8,-4.0,99,9.0,2,2.0\n")
        rows = self.cm.rows_from_csv(self.repo, f, True)
        self.assertEqual(sorted((r["side"], r["cell"], r["win"] or "", r["trades"], r["usd"]) for r in rows),
                         [("long", "f1", "", 10, 5.0), ("long", "f1", "a", 3, 1.0), ("short", "f2", "", 8, -4.0), ("short", "f2", "a", 2, 2.0)])
        self.assertEqual(rows[0]["src_ptr"], "строка 3")  # строка файла с учётом комментария

    def test_md_tables(self):
        f = self.put("rep.md", "\n".join([
            "# Отчёт", "## Таблица h775", "",
            "лонг, D = 7 сут.:", "",
            "| n_ok | сделок | Σ $ | $/сделку | Sharpe | Σ $ (сделок) по месяцам |", "|---|---:|---:|---:|---:|---|",
            "| 0 | 245 | +154 | +0.63 | 2.11 | +1 (2) |", "",
            "| кандидат | сделок | сумма | среднее на сделку |", "|---|---:|---:|---:|", "| шорт C | 191 | −19,59 | −0,103 |", "",
            "| кандидат | сделок | $/сделку (bps) |", "|---|---:|---:|", "| база | 57 | −4.25 (−8) |", "",
            "| N | итог | $/сделку |", "|---|---|---:|", "| 3 | x | 1.0 |", "",
            "| монета | сделок | Σ $ |", "|---|---:|---:|", "| BTCUSDT | 5 | 1 |", ""]))
        rows = self.cm.rows_from_md(self.repo, f, True)
        got = [(r["side"], r["cell"], r["uni"], r["trades"], r["usd"], r["mean_usd"], r["sharpe"], r["from_text"]) for r in rows]
        self.assertEqual(got, [("long", "лонг, D = 7 сут. / 0", "h775", 245, 154.0, 0.63, 2.11, 1),
                               ("short", "шорт C", "h775", 191, -19.59, -0.103, None, 1),
                               (None, "база", "h775", 57, None, -4.25, None, 1)])
        other = self.cm.rows_from_md(self.repo, f, False)
        self.assertTrue(all(r["usd"] is None for r in other))  # не П-42: $ не пересчитан
        self.assertEqual([("usd_raw" in r["extra"], r["extra"].get("mean_raw")) for r in other], [(True, 0.63), (False, -0.103), (False, -4.25)])  # «сумма» без $ — только у П-42

    def test_md_coin_rows_and_selected(self):
        f = self.put("rep2.md", "\n".join([
            "| клетка | сделок | Σ $ |", "|---|---:|---:|", "| база | 10 | 5 |", "| BTCUSDT | 4 | 2 |", "| 1000PEPEUSDT лонг | 3 | 1 |", "",
            "| сторона | цель | выбранных (из всех) | сумма $ выбранных | среднее $ на сделку (S0) |", "|---|---|---:|---:|---:|",
            "| шорт | T1 | 76 (из 300) | +73.13 | +0.962 |", ""]))
        got = [(r["side"], r["cell"], r["trades"], r["usd"]) for r in self.cm.rows_from_md(self.repo, f, True)]
        self.assertEqual(got, [(None, "база", 10, 5.0), ("short", "шорт / T1", 76, 73.13)])  # строки по монете — не клетки

    def test_mean_derived_mark(self):
        self.assertTrue(chron.mean_derived({"trades": 3, "usd": 1.0, "mean_usd": 1.0 / 3}))
        self.assertFalse(chron.mean_derived({"trades": 3, "usd": 1.0, "mean_usd": 0.33}))  # напечатано в источнике
        self.assertFalse(chron.mean_derived({"trades": 0, "usd": 0.0, "mean_usd": None}))
        self.assertIn("=Σ/n", chron.fmt_metric_row(dict(mrow(1, trades=4, usd=2.0, mean_usd=0.5), src_commit=None)))

    def test_parse_metric_arg(self):
        r = self.cm.parse_metric_arg("side=обе; trades=1 200; usd=−3,5; src=docs/a.json@abc1234#x/y")
        self.assertEqual((r["side"], r["trades"], r["usd"], r["src"], r["src_commit"], r["src_ptr"]), ("both", 1200, -3.5, "docs/a.json", "abc1234", "x/y"))
        for bad in ("trades=1", "side=long; foo=1; src=a", "side=x; src=a", "trades; src=a"):
            with self.assertRaises((ValueError, KeyError)):
                self.cm.parse_metric_arg(bad)



class TestMetricSpec(unittest.TestCase):
    """TK-661: явная опись — выборка из JSON / CSV (в т. ч. удалённого файла по коммиту), строки «из текста», пометки «нет чисел»."""

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), cwd=str(self.root), capture_output=True,
                              encoding="utf-8", check=True).stdout.strip()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        import chron_metrics
        self.cm = chron_metrics
        (self.root / "docs").mkdir()
        (self.root / "docs/r.json").write_text(json.dumps({"L": {"by": {"a": {"n": 4, "total": 2.0, "pos": 0.5}, "b": {"n": 2, "total": -1.0}, "c": {"x": 1}}}}),
                                               encoding="utf-8")
        (self.root / "docs/r.csv").write_bytes("side,metric,cell,n,sum\nL,gf,k1,10,5.0\nS,gf,k2,8,-4.0\nL,touch,k1,10,0.9\n".encode("utf-8"))
        (self.root / "docs/j.md").write_bytes("строка 1\nE08 · лонг 89 кругов, +6,65 bps; 4 сделки 2,0 $\n".encode("utf-8"))
        (self.root / "docs/old.json").write_text(json.dumps({"S": {"n": 7, "sum": 3.5}}), encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "1")
        self.old = self.git("rev-parse", "--short", "HEAD")
        self.git("rm", "-q", "docs/old.json")
        self.git("commit", "-qm", "2")
        self.ents = [{"id": 1, "project": "П-42"}, {"id": 2, "project": None}]

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, items):
        return self.cm.spec_rows(self.root, {"items": items}, self.ents)

    def test_pick_text_gap_history(self):
        rows, gaps = self.rows([
            {"entry": 1, "src": "docs/r.json", "pick": [{"ptr": "L/by/*", "side": "L", "cell": "bk: {0}", "map": {"trades": "n", "usd": "total"}, "extra": ["pos"]}]},
            {"entry": 1, "src": "docs/r.csv", "pick": [{"where": {"metric": "gf"}, "side": "{side}", "cell": "{cell}", "map": {"trades": "n", "usd": "sum"}}]},
            {"entry": 1, "src": "docs/old.json", "commit": self.old, "pick": [{"ptr": "S", "side": "short", "map": {"trades": "n", "usd": "sum"}}]},
            {"entry": 2, "src": "docs/j.md", "line": 2, "text": 1, "rows": [{"cell": "a45-bid", "side": "long", "trades": 89, "extra": {"bps_point": 6.65}}]},
            {"entry": 2, "gap": "предрегистрация", "note": "итога нет", "src": "docs/j.md", "line": 1}])
        got = sorted((r["entry"], r["side"], r["cell"], r["trades"], r["usd"], r["mean_usd"], r["from_text"], r["src_ptr"]) for r in rows)
        self.assertEqual(got, [(1, "long", "bk: a", 4, 2.0, 0.5, 0, "L/by/a"), (1, "long", "bk: b", 2, -1.0, -0.5, 0, "L/by/b"),
                               (1, "long", "k1", 10, 5.0, 0.5, 0, "строка 2"), (1, "short", "S", 7, 3.5, 0.5, 0, "S"),
                               (1, "short", "k2", 8, -4.0, -0.5, 0, "строка 3"), (2, "long", "a45-bid", 89, None, None, 1, "строка 2")])
        self.assertEqual(next(r for r in rows if r["cell"] == "S")["src_commit"], self.old)  # удалённый файл — версия коммита
        self.assertEqual(next(r for r in rows if r["cell"] == "bk: a")["extra"], {"pos": 0.5})  # узел «c» без чисел пропущен
        self.assertEqual(gaps, [{"entry": 2, "reason": "предрегистрация", "note": "итога нет", "src": "docs/j.md", "src_ptr": "строка 1"}])
        self.assertEqual(len({r["h"] for r in rows}), len(rows))

    def test_refusals(self):
        bad = [
            {"entry": 2, "src": "docs/j.md", "line": 2, "text": 1, "rows": [{"cell": "x", "trades": 3, "usd": 1.0}]},  # $ не П-42 без stake: 500
            {"entry": 2, "src": "docs/j.md", "line": 2, "text": 1, "rows": [{"cell": "пусто"}]},  # строка без чисел
            {"entry": 1, "src": "docs/j.md", "line": 2, "rows": [{"cell": "x", "trades": 3}]},  # md без text: 1
            {"entry": 1, "src": "docs/old.json", "pick": [{"ptr": "S", "map": {"trades": "n"}}]},  # удалённый — только с коммитом
            {"entry": 1, "src": "docs/r.json", "pick": [{"ptr": "L/нет/*", "map": {"trades": "n"}}]},  # нет ключа
            {"entry": 2, "gap": "лень"},
            {"entry": 9, "gap": "предрегистрация"}]
        for it in bad:
            with self.assertRaises((ValueError, KeyError), msg=json.dumps(it, ensure_ascii=False)):
                self.rows([it])
        rows, _ = self.rows([{"entry": 2, "src": "docs/j.md", "line": 2, "text": 1, "stake": 500, "rows": [{"cell": "x", "trades": 4, "usd": 2.0}]}])
        self.assertEqual((rows[0]["usd"], rows[0]["mean_usd"]), (2.0, 0.5))
        self.assertIn("stake", rows[0]["extra"])

    def test_line_guard(self):
        ok = {"entry": 2, "src": "docs/j.md", "line": 2, "text": 1, "rows": [{"cell": "a", "trades": 89, "extra": {"bps_point": 6.65}, "replaces": "строка 9"}]}
        rows, _ = self.rows([ok])
        self.assertEqual((rows[0]["src_ptr"], rows[0]["replaces"]), ("строка 2", "строка 9"))
        self.assertEqual(self.rows([dict(ok, line="1-2")])[0][0]["src_ptr"], "строка 1-2")
        for bad in (dict(ok, line=3), dict(ok, line="2-5"), dict(ok, rows=[{"cell": "a", "trades": 90}])):  # за концом файла; числа нет на строке
            with self.assertRaises(ValueError, msg=json.dumps(bad, ensure_ascii=False)):
                self.rows([bad])
        for line, row in (("умерших: **11 224 843** (≈ 56 тыс.)", {"extra": {"n": 11224843, "k": 56000}}), ("45.8 млн кругов, −7,6 bps", {"trades": 45800000, "extra": {"b": -7.6}}),
                          ("CAP/AXL — ноль", {"extra": {"t": 0}}), ("BTC 09-21 237 МБ", {"extra": {"mb": 237}})):
            self.cm.check_text_line(line, "строка 1", row)
        with self.assertRaises(ValueError):
            self.cm.check_text_line("BTC 09-21 237 МБ", "строка 1", {"extra": {"mb": 21237}})

    def test_git_at(self):
        g = chron.Git(self.root)
        self.assertFalse(g.file_ok("docs/old.json"))
        self.assertTrue(g.at(self.old, "docs/old.json"))
        self.assertFalse(g.at(self.old, "docs/none.json"))
        self.assertFalse(g.at(None, "docs/old.json"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
