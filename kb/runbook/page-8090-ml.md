---
id: page-8090-ml
type: runbook
status: running
title: "Страница П-42 http://5.231.25.224:8090/ (юнит ml-page-8090)"
date: 2026-11-20
links: [srv, srv-pages, P-42, TK-631, TK-624, TK-678, TK-684, tools/p42/ml_page_update.sh, tools/p42/ml_progress.json, tools/p42/tk684_variants.json]
supersedes: []
---
Статическая страница `/data/pages/ml`, раздаётся `python3 -m http.server 8090` как `ml-page-8090` на [[srv]] ([[srv-pages]]). Протокол — [[P-42]].

- **Вкладки** (хеш): «Эквити» `#equity/<short|long|both>/<лонг>/<шорт>` (по умолчанию; выбор вариантов и сумма «шорт + лонг» — [[TK-684]]), «Цели TK-598» `#targets`, «Признаки» `#features/<набор>/<№>` (только таблицы, сквозной № признака — [[TK-678]]). 22.11 убраны «Прогресс», «Ход работ», «Модель TK-592» и виды «Где мы», «Кривая», «Базы», «Форма» ([[TK-684]]); любые прежние хеши открывают «Эквити»; их код — `docs/archive/page-8090-removed-tabs-2026-11-22/`.
- **Обновление:** `bash tools/p42/ml_page_update.sh` — заливает [[tools/p42/ml_progress.json]] (из него только `targets`), опись признаков, [[tools/p42/tk684_variants.json]] и генератор на srv, сверяет линии с летописью, пересобирает `/data/pages/ml/index.html`. Подробности — `docs/COMMANDS.md`.
- **Новый вариант в «Эквити»** ([[TK-684]]): одна запись в [[tools/p42/tk684_variants.json]] → `variants` — `id` (латиница, попадает в хеш), `side` (`long` / `short`), `group` (`base` или `frozen`), `label` (по-русски, ≤ 6 слов, без номеров задач), `status` (необязательно: пометка в колонке «Статус» — «подобрано на этих данных» / «заморожено 20.11, стоп 5 %»), `key` (ключ клетки — виден только в подсказке), `dir` (каталог сделок на srv с `{w}` вместо окна `wa` / `wb` / `wc`; внутри `long-trades.csv` или `short-trades.csv` и `*-daily.csv`), принятые числа: `chron` (имя клетки в таблице `metric` летописи) и / или `ref` (`entry`, `n`, `sum`, `sharpe`, `dd`, `gap` из записи летописи). Затем `bash tools/p42/ml_page_update.sh`: сборка сверяет линию с принятыми числами и останавливается при расхождении; движок не запускается. Проверка — `tk684_check.js` на Софии (1280 и 390).
- **Больше не используется страницей:** `tools/now_push.py` (кладёт `now.json` с ПК раз в минуту, [[TK-631]]) и таймер `tk613-progress` на srv (`data/tasks.json`) — не остановлены, решает координатор.
