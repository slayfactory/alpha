# П-42 · влияние осей (TK-689) — перенесено в БД (TK-693, В-179)

Результаты живут в БД летописи на srv (`/data/chron/chron.sqlite`, таблицы `infl_*`, схема v8), не в md. Снимок прежнего отчёта (md, графики PNG, таблицы) — `docs/archive/findings/p42-axis-influence-2026-11-23.md` и `docs/archive/findings/p42-axis-influence-2026-11-23/`.

```bash
python tools/p42/tk689_db.py runs                                   # прогоны (вселенная, окно, git, число осей)
python tools/p42/tk689_db.py top --side long --level 1 --n 10       # топ осей; --by r|usd|dd|…, --cat <вкладка>, --grid отбор
python tools/p42/tk689_db.py rollup --level 1                       # роллап по вкладкам «Признаки»
bash tools/p42/tk689_update.sh                                      # пересчёт, когда появились новые клетки (только изменившиеся уровни)
```

Порядок и схема — `docs/research/CHRONICLE.md`, раздел «Влияние осей».
