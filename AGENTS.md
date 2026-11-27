# alpha

Rust + Python. Данные и исследование для крипто-бирж: коллекторы по вебсокету, бэктесты, поиск торговых закономерностей. Бот — в роботе ФК «Викинг» (документация — MCP `viking-docs`).

Работа в bb по pstack: координатор — главный тред, исполнители и проверяющие — дочерние (`.claude/skills/poteto-mode/SKILL.md`). Постоянные приказы владельца — `.claude/preferences.md` (обязательны для всех тредов). **Постоянное разрешение (27.11):** следующий шаг по направлению владельца (протокол, критик, счёт, проверяющий, слияние после PASS, следующая задача из результата) запускается сразу, без вопроса «Запускаю?»; спрашивать — только новое направление, деньги, риск, необратимое.

**Child threads (исп / пров / чит / крит): English only, silent until the final report.** No text between tool calls: no narration, no progress lines (hook `.claude/hooks/child_silence.py`); the coordinator only receives the final report; write mid-run only for a blocker or a question to the owner (owner's words quoted verbatim in Russian). MANDATORY (В-164, owner 27.11 «нахуя они вообще тогда что тоо пишут?»): final report ≤ 5 lines, one idea per line, ≤ 20 words per line: status · answer + key numbers · branch + SHA · card path (`kb/task/TK-NNN.md`) · deviations; everything else (amendment numbers, fixes, internal details) goes into the kb card, not the message. Repo docs (`docs/**`) stay in Russian.

## Сейчас

- Состояние и что идёт — `docs/plan/coord-handoff.md` (читать первым); П-42 (пробои) — c539, 539 крипто-контрактов (В-175).
- G-022 (Rust-коллекторы) на холде (В-166), коллекторы работают на srv (здоровье http://5.231.25.224:8088/); handoff — `docs/plan/G-022-handoff-2026-11-13.md`.
- srv: один движок счёта за раз (юнитам `MemoryMax 40G`). Арбитраж выключен (arb-live не запускать), П-40 на паузе.

## Где что

- Решения владельца — карточки `kb/decision/` (В-N с 14.10.2026, ранние S-N = «SETTLED В-N»): `python tools/kb.py show В-NNN` / `find "<слова>"`; новое решение — карточкой В-<следующий номер>, порядок — `kb/README.md` («Как записать решение»). Старые `.claude/decisions.md`, `docs/plan/SETTLED*.md` — заглушки, снимки в `docs/archive/`. `docs/plan/BUSINESS-TASK.md` не редактировать.
- Исследование: скилл `.claude/skills/alpha-research/` (обязателен перед любым торговым выводом), проверки `docs/research/reviews/` (старые — `docs/archive/research/reviews/`).
- **Летопись — БД на srv** (`/data/chron/chron.sqlite`, TK-646; сайт http://5.231.25.224:8091/chron/): все выводы, журнал, реестр и протоколы с перелинковкой. **Новый вердикт / предрегистрация / поправка — `python tools/chron.py add`, новый раздел протокола — `chron.py psec add` (`--prereg` = до счёта, дальше неизменяем), не md.** `JOURNAL.md`, `EXPERIMENTS.md`, `P-*.md` — заглушки (снимки — `docs/archive/chronicle-2026-11-20/`); перед слиянием — `python tools/chron.py check`. Порядок — `docs/research/CHRONICLE.md`.
- **База знаний `kb/`** (TK-629, сайт http://5.231.25.224:8091/): результат треда — карточка, статус и «Дальше» — в карточке задачи, не в новых handoff-файлах; перед коммитом `python tools/kb.py check`; правила — `kb/README.md`.
- Разборы и замеры — `docs/findings/<тема>-<YYYY-MM-DD>.md` (только живое, опись — `docs/findings/README.md`; старое — `docs/archive/findings/`, TK-640); сырое — `data/` (gitignored). Архитектура — `docs/ARCHITECTURE.md`, команды и грабли — `docs/COMMANDS.md`.

## Машины

ssh с этого ПК — всегда с `-i` и `-o UserKnownHostsFile=...` (кириллица в HOME).

- **srv `5.231.25.224`** (8 vCPU, 62 ГБ): счёт, закачка, коллекторы (`/data/collect/`), сборки. Долгие процессы — `systemd-run`.
- **VPS София `13.140.29.171`** (4 ядра, 7,9 ГБ): сборки и проверки по умолчанию (`vps-check.sh`); счёт — нет (кроме В-101). Сторож места режет сборки при свободном < 23 ГБ.
- **Storage Box** `u677479@u677479.your-storagebox.de:23`, ключ; только ls, du, df, rsync/sftp.
- Не использовать: calc, Steam Deck, старый коллектор `139.99.91.22` (В-102, В-104).

## Команды

```bash
bash tools/vps-check.sh <рабочее дерево> all   # test + clippy -D warnings + fmt на Софии (CHECK_HOST=srv — на srv; build — srv)
bash tools/vps-check.sh <рабочее дерево> test <фильтр>
cargo fmt --check                              # единственное cargo, что можно локально
```

## Git

Ствол — `master`; ветка = одна тема, свой worktree. Слияние — после тестов изменённого модуля и вердикта проверяющего; CI GitHub не ждём, красный CI — отдельной задачей на другой модели (`playbooks/landing.md`). Законченная ветка — тег `archive/<имя>` и удаление; `archive/*` не удалять. Без force-push и переписывания `master`; в коммит — только свои файлы.

## Правила кода

- Горячий путь стратегии: ноль аллокаций на событие после прогрева, время только через трейт `Clock`, без `async` с захватом состояния в потоке решений, без REST в событийном цикле, ордер собран до триггера, без `f64` для цены/размера, без `BTreeMap`/`HashMap` на пути события. Расстояния — в bps.
- `src/`: `binlog/` · `book/` · `bybit/` · `feed/` · `lob/` (единственная стратегия `lob::strategy::on_event`) · `stats/` · `commands/`; тесты — `<модуль>/tests.rs`.
- Шаг не закрыт без артефакта на диске. Заменённый документ — в `docs/archive/` тем же коммитом, ссылки поправлены.
- Секреты — только имена переменных (`BYBIT_API_KEY`, `BYBIT_API_SECRET`), значения не печатать.

## Грабли

- `lob probe` ставит реальные ордера. Bybit даёт 403 с части стран (с ПК и srv доступ есть).
- На серверах никаких `find`/`du` по большим каталогам вне `systemd-run` (оборванный ssh оставляет их жить).
- На srv никаких `pkill`/`killall` по имени (19.11 убило чужой счёт TK-599): только `systemctl stop <свой юнит>`.
- Склад тиков `/data/p42-tk599/tick-store` доказан: никаких `--store-verify` и повторных сверок zip (владелец 19.11: «сверка зип архива до после? ты еблан?»); новый код — итоговые таблицы против эталона.
