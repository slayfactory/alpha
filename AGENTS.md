# alpha

Rust + Python. Данные и исследование для крипто-бирж: коллекторы по вебсокету, бэктесты, поиск торговых закономерностей. Бот — в роботе ФК «Викинг» (документация — MCP `viking-docs`).

Работа в bb по pstack: координатор — главный тред, исполнители и проверяющие — дочерние (`.claude/skills/poteto-mode/SKILL.md`). Приказы владельца — `.claude/preferences.md` (обязательны для всех тредов). **Разрешение (27.11):** следующий шаг по направлению владельца (протокол, критик, счёт, проверяющий, слияние после PASS, следующая задача из результата) — сразу, без «Запускаю?»; спрашивать — только новое направление, деньги, риск, необратимое.

**Child threads (исп / пров / чит / крит): English only, silent until the final report.** No text between tool calls (hook `.claude/hooks/child_silence.py`); mid-run only a blocker or an owner question (owner's words verbatim in Russian). MANDATORY (В-164, owner 27.11 «нахуя они вообще тогда что тоо пишут?»): final report ≤ 5 lines, one idea per line, ≤ 20 words each: status · answer + key numbers · branch + SHA · card (`kb/task/TK-NNN.md`) · deviations; the rest goes into the kb card. Repo docs (`docs/**`) stay Russian.

## Сейчас

- Состояние — `docs/plan/coord-handoff.md` (читать первым); П-42 (пробои) — c539, 539 крипто-контрактов (В-175).
- G-022 (Rust-коллекторы) на холде (В-166); коллекторы на srv, здоровье http://5.231.25.224:8088/; handoff — `docs/plan/G-022-handoff-2026-11-13.md`.
- srv: один движок счёта за раз (юнитам `MemoryMax 40G`). Арбитраж выключен (arb-live не запускать), П-40 на паузе.

## Где что

- Решения владельца — карточки `kb/decision/` (В-N с 14.10.2026, S-N = «SETTLED В-N»): `python tools/kb.py show В-NNN` / `find "<слова>"`; новое — карточкой В-<следующий номер> (`kb/README.md`). `.claude/decisions.md`, `docs/plan/SETTLED*.md` — заглушки; `docs/plan/BUSINESS-TASK.md` не редактировать.
- Исследование: скилл `.claude/skills/alpha-research/` (обязателен перед торговым выводом), проверки `docs/research/reviews/`.
- **Летопись — БД на srv** (TK-646; http://5.231.25.224:8091/chron/): вердикт / предрегистрация / поправка — `python tools/chron.py add`, раздел протокола — `chron.py psec add` (`--prereg` = до счёта, дальше неизменяем), не md; `JOURNAL.md`, `EXPERIMENTS.md`, `P-*.md` — заглушки; перед слиянием — `python tools/chron.py check`; порядок — `docs/research/CHRONICLE.md`.
- **База знаний `kb/`** (TK-629, http://5.231.25.224:8091/): результат треда — карточка; статус и «Дальше» — в карточке задачи, не в новых handoff-файлах; перед коммитом `python tools/kb.py check`.
- Разборы и замеры — `docs/findings/<тема>-<YYYY-MM-DD>.md` (только живое; опись — `README.md` там же); сырое — `data/` (gitignored); старое — `docs/archive/`. Архитектура и модули `src/` — `docs/ARCHITECTURE.md`, команды и грабли — `docs/COMMANDS.md`.

## Машины

Карточки — `kb/machine/`. ssh с ПК — всегда с `-i` и `-o UserKnownHostsFile=...` (кириллица в HOME).

- **srv `5.231.25.224`**: счёт, закачка, коллекторы (`/data/collect/`), сборки; долгие процессы — `systemd-run`.
- **VPS София `13.140.29.171`**: сборки и проверки по умолчанию; счёт — нет (кроме В-101); сторож места режет сборки при свободном < 23 ГБ.
- **Storage Box** `u677479@u677479.your-storagebox.de:23`, ключ; только ls, du, df, rsync/sftp.
- Не использовать: calc, Steam Deck, старый коллектор `139.99.91.22` (В-102, В-104).

## Проверки, git, код

- `bash tools/vps-check.sh <дерево> all|test <фильтр>` (test + clippy -D warnings + fmt на Софии; `CHECK_HOST=srv` — на srv; build — srv). Локально из cargo — только `cargo fmt --check`.
- Ствол — `master`; ветка = одна тема, свой worktree; слияние, тег `archive/<имя>`, без force-push — `playbooks/landing.md`. Слияние — после тестов изменённого модуля и вердикта проверяющего; красный CI — отдельной задачей на другой модели. В коммит — только свои файлы.
- Горячий путь стратегии: ноль аллокаций на событие после прогрева, время только через трейт `Clock`, без `async` с захватом состояния в потоке решений, без REST в событийном цикле, ордер собран до триггера, без `f64` для цены/размера, без `BTreeMap`/`HashMap` на пути события. Расстояния — в bps. Единственная стратегия — `lob::strategy::on_event`; тесты — `<модуль>/tests.rs`.
- Шаг не закрыт без артефакта на диске. Заменённый документ — в `docs/archive/` тем же коммитом, ссылки поправлены.
- Секреты — только имена переменных (`BYBIT_API_KEY`, `BYBIT_API_SECRET`), значения не печатать.

## Грабли

- `lob probe` ставит реальные ордера. Bybit даёт 403 с части стран (с ПК и srv доступ есть).
- На серверах никаких `find`/`du` по большим каталогам вне `systemd-run` (оборванный ssh оставляет их жить).
- На srv никаких `pkill`/`killall` по имени (19.11 убило чужой счёт TK-599): только `systemctl stop <свой юнит>`.
- Склад тиков `/data/p42-tk599/tick-store` доказан: никаких `--store-verify` и повторных сверок zip (владелец 19.11: «сверка зип архива до после? ты еблан?»); новый код — итоговые таблицы против эталона.
