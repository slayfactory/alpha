# alpha

Rust + Python. Данные и исследование для крипто-бирж: коллекторы по вебсокету, бэктесты, поиск торговых закономерностей. Бот — в роботе ФК «Викинг» (документация — MCP `viking-docs`).

Работа в T3 Code: координатор — главный тред, исполнители и проверяющие — дочерние треды `delegate_task` (`.claude/skills/poteto-mode/SKILL.md`). Клир контекста и длина ответа — глобальные хуки `~/.claude/hooks/ctx_guard.py`, `brevity.py`. Постоянные приказы владельца — `.claude/preferences.md` (обязательны для всех тредов).

**Child threads (исп / пров / чит): English only, no chatter.** Progress lines, messages, final report in English; owner's words quoted verbatim in Russian. No narration; one progress line only on state change. MANDATORY (В-164, not advice): final report ≤ 8 lines; more only with a line «Reason: …» stating why: status · answer + key numbers · branch + SHA · card path (`kb/task/TK-NNN.md`) · deviations (one line); everything else goes into the kb card, not the message. Do not comment intermediate stages: a turn that only waits (critic, unit, watcher) ends with one line «waiting: <what>» or nothing; no restating the brief, critic verdicts or previous reports. Repo docs (`docs/**`) stay in Russian.

## Сейчас (24.11.2026)

- **П-42 (пробои уровня):** состав — c539 (539 крипто-контрактов; TradFi убран, В-175). Идёт общий перебор одной линии TK-674, ступень 1 (пары → тройки → четвёрки → ≤ 2 кандидата на сторону → заморозка, В-170); торгуется одна линия, фикс. стопа 5 % нет. Январь–май 2026 докачаны на srv (В-181, TK-695). Эксперимент TK-692 (В-182, В-183) — перебор на январе–июне 2026 сразу, считаются клетки с P&L > 1 $; отложенная проверка замороженных линий на июне снята. Размер свечи уровня f12 (p30 / p240) — TK-699, подмена parquet после конца перебора. Заморозки вперёд 20–21.11 (`docs/research/freeze/tk626-frozen.json` и далее) сделаны на 775 — до первой точки 30.11 перезаморозить на крипте. Страница http://5.231.25.224:8090/, состояние — `docs/plan/coord-handoff.md` (читать первым).
- **G-022 — Rust-коллекторы — на холде (В-166), коллекторы работают** (В-149..В-155): на srv (`collect-*`, `soak-bybit`; здоровье http://5.231.25.224:8088/), код в `master` (TK-628). Читать первым: `docs/plan/G-022-handoff-2026-11-13.md`.
- srv: один движок счёта за раз (юнитам `MemoryMax 40G`). Арбитраж выключен (19.11; 23.11 владелец: «мы ниче с арбитражом не делаем только пробой тестим» — arb-live не запускать), П-40 на паузе.

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
