# TK-675 — диск srv 22.11: свободно 37,1 → 99 ГиБ, 45 путей перенесены на Storage Box и сверены

**Результат (измерено на srv, 22.11, UTC).** `/` занят 91 % → 74 %: свободно **37,1 → 99 ГиБ** (`df`: 280 из 394 ГиБ занято). Перенесено и удалено на srv **45 путей: 490043 файлов и 226457 ссылок, 69.8 ГиБ**; каждый путь перед удалением сверен (число файлов, ссылок и байты на боксе = локальным до копии, `rsync -aHn` без отличий, sha256 выборки 20 файлов = `sha256sum` на боксе) — манифест `srv-offload-2026-11-22.manifest.jsonl`. Удалено без копии (мусор): старые бинари коллекторов `.prev-*` (53), `alpha-bars-*`, `alpha-netfix-*` в `/data/collect`, `/tmp` старше суток (727 записей включая бинари), остатки сверки 14.11, кэш apt, журнал до 100 МиБ — +1,6 ГиБ. Коллекторы, страницы :8088/:8090/:8091, `arb-live` — `active` до и после.

Единицы: ГиБ = 2³⁰ байт. Бокс: `u677479@u677479.your-storagebox.de:23`, корень набора — `/home/offload-srv-2026-11-22/<путь>`; два пути (`aggtrades/bybit`, `klines/bybit`) — в старой копии `/home/alpha/aggtrades|klines/`. Свободно на боксе 735 → 691 ГиБ (занято 289 → 333 ГиБ после всех копий).

## 1. Что осталось на srv и почему (сырьё не перенесено)

Приказ координатора «сырьё, из которого собран склад тиков, — переносить» упёрся в то, что окна счёта — не копии, а **симлинки на сырьё**: `--preflight` нашёл 278 895 ссылок из `p42-tk621/{wa,wb,wc}`, `p42-tk599/{wa,wb,wc}`, `p42-tk616/fwd`, `p42-tk625/h775`, `p42-tk604/w0` на `/data/aggtrades/binance`, `/data/p42-past/aggtrades`, `/data/p42-past06/aggtrades`, `/data/p42-tk596/dl`, и ещё 3 ссылки `…/cache → /data/p42v2/cache`. TK-674 (`tk674_run.py`, `breakout tick --cache <окно>/cache --trades <окно>/aggtrades`) и прогноз вперёд (`tk626_prep.py`, `tk616_forward.py`) читают окна; перенос цели оставил бы ссылки висеть. Остались на srv: `/data/aggtrades/binance` 56,4 ГиБ, `/data/p42-past` 32,4, `/data/p42-past06` 21,2, `p42-tk596/dl` 0,25 (≈ 110 ГиБ; ссылок нет только у 5,8 ГиБ, из них 4,1 ГиБ `aggtrades/bybit` ушли). Дополнительно оставлены по `tk664_funding_capital.py` и `tk667_alloc.py` (минутные свечи каждой монеты для 1/σ суток): `/data/klines/binance` 6,1 ГиБ и `p42-tk596/{jul,aug}/klines` 6,9 ГиБ (в приказе — только `BTCUSDT`); `p42v2/cache` 2,2 ГиБ (ссылка из `p42-t566/cache`).

## 2. Перенесено (путь на srv → путь на боксе)

| Путь на srv | Бокс (`/home/…`) | Файлов | Ссылок | Байт | Оставлено на srv |
|---|---|---:|---:|---:|---|
| `/data/evaa-wave` | `offload-srv-2026-11-22/evaa-wave` | 19 | 0 | 734060033 | — |
| `/data/p42-tk596` | `offload-srv-2026-11-22/p42-tk596` | 1286 | 48586 | 5084565042 | `dl`, `jul/klines`, `aug/klines` |
| `/data/tk594/aug` | `offload-srv-2026-11-22/tk594/aug` | 25137 | 0 | 6438516519 | — |
| `/data/tk594/egate` | `offload-srv-2026-11-22/tk594/egate` | 1030 | 7086 | 426307784 | — |
| `/data/tk592/cache` | `offload-srv-2026-11-22/tk592/cache` | 566 | 0 | 2613264840 | — |
| `/data/tk592/klines` | `offload-srv-2026-11-22/tk592/klines` | 1694 | 42183 | 261418554 | — |
| `/data/tk592/aggtrades` | `offload-srv-2026-11-22/tk592/aggtrades` | 0 | 43209 | 0 | — |
| `/data/p42-book/zip` | `offload-srv-2026-11-22/p42-book/zip` | 17069 | 0 | 8370571232 | — |
| `/data/p42v2` | `offload-srv-2026-11-22/p42v2` | 46122 | 0 | 6729427805 | `cache` |
| `/data/grid` | `offload-srv-2026-11-22/grid` | 159 | 0 | 613826004 | — |
| `/data/perf/sh` | `offload-srv-2026-11-22/perf/sh` | 3103 | 0 | 1168027473 | — |
| `/data/perf/audit` | `offload-srv-2026-11-22/perf/audit` | 345 | 0 | 498049847 | — |
| `/data/perf/p2` | `offload-srv-2026-11-22/perf/p2` | 10 | 0 | 425901593 | — |
| `/data/perf/cap` | `offload-srv-2026-11-22/perf/cap` | 97 | 0 | 390577504 | — |
| `/data/perf/a8` | `offload-srv-2026-11-22/perf/a8` | 281 | 0 | 204254645 | — |
| `/data/perf/rp` | `offload-srv-2026-11-22/perf/rp` | 184 | 0 | 64207690 | — |
| `/data/perf/st` | `offload-srv-2026-11-22/perf/st` | 318 | 0 | 33250537 | — |
| `/data/perf/p1` | `offload-srv-2026-11-22/perf/p1` | 73 | 0 | 34149122 | — |
| `/data/perf/followups-deploy` | `offload-srv-2026-11-22/perf/followups-deploy` | 163 | 0 | 66202707 | — |
| `/root/p40core` | `offload-srv-2026-11-22/root/p40core` | 339994 | 0 | 8642837889 | — |
| `/root/p40` | `offload-srv-2026-11-22/root/p40` | 128 | 2 | 253332136 | — |
| `/root/p40prelim` | `offload-srv-2026-11-22/root/p40prelim` | 67 | 0 | 134964722 | — |
| `/root/p40ref` | `offload-srv-2026-11-22/root/p40ref` | 51 | 0 | 119005722 | — |
| `/root/p40prof` | `offload-srv-2026-11-22/root/p40prof` | 575 | 0 | 88564958 | — |
| `/root/p40charts` | `offload-srv-2026-11-22/root/p40charts` | 4490 | 0 | 40861206 | — |
| `/root/p40core-tk522f` | `offload-srv-2026-11-22/root/p40core-tk522f` | 12 | 0 | 27784811 | — |
| `/root/p40core-live` | `offload-srv-2026-11-22/root/p40core-live` | 5 | 0 | 30987184 | — |
| `/root/p40t` | `offload-srv-2026-11-22/root/p40t` | 20 | 0 | 16934177 | — |
| `/root/tk512` | `offload-srv-2026-11-22/root/tk512` | 8541 | 0 | 939871746 | — |
| `/root/tk516` | `offload-srv-2026-11-22/root/tk516` | 73 | 0 | 32774028 | — |
| `/root/tk528` | `offload-srv-2026-11-22/root/tk528` | 47 | 0 | 144347896 | — |
| `/root/tk531` | `offload-srv-2026-11-22/root/tk531` | 66 | 0 | 96547026 | — |
| `/root/rev534` | `offload-srv-2026-11-22/root/rev534` | 44 | 0 | 75145990 | — |
| `/data/arb-tab/live2` | `offload-srv-2026-11-22/arb-tab/live2` | 7987 | 0 | 5553900992 | — |
| `/data/arb-tab/w1` | `offload-srv-2026-11-22/arb-tab/w1` | 1862 | 0 | 3200341900 | — |
| `/data/arb-tab/out1` | `offload-srv-2026-11-22/arb-tab/out1` | 4 | 0 | 1049281122 | — |
| `/data/p42-holdout` | `offload-srv-2026-11-22/p42-holdout` | 9004 | 0 | 3319105752 | — |
| `/data/p42` | `offload-srv-2026-11-22/p42` | 1186 | 0 | 1026848139 | — |
| `/data/p42-retitr` | `offload-srv-2026-11-22/p42-retitr` | 842 | 0 | 2653341698 | — |
| `/data/p42-tk615` | `offload-srv-2026-11-22/p42-tk615` | 2718 | 2 | 2086966686 | — |
| `/data/p42-tk598/hold-in` | `offload-srv-2026-11-22/p42-tk598/hold-in` | 2268 | 85389 | 2871199629 | — |
| `/data/p42-tk607/store-l1` | `offload-srv-2026-11-22/p42-tk607/store-l1` | 4930 | 0 | 2912235547 | — |
| `/data/p42-tk607/store-rtfz-l1` | `offload-srv-2026-11-22/p42-tk607/store-rtfz-l1` | 1140 | 0 | 801068814 | — |
| `/data/aggtrades/bybit` | `alpha/aggtrades/bybit` | 3089 | 0 | 4390694219 | — |
| `/data/klines/bybit` | `alpha/klines/bybit` | 3244 | 0 | 255622886 | — |

## 3. Как сделано

`tools/srv-offload-paths.py` (тесты — `tools/tests/test_srv_offload_paths.py`): по очереди на путь — отказ при занятости, сводка до, `rsync -aH`, сводка после, `ls -lAR` бокса, `rsync -aHn`, sha256 выборки, удаление только перенесённых файлов. Четыре юнита `tk675-mv1..4` шли параллельно (`Nice=19`, `IOSchedulingClass=idle`, один поток к боксу ≈ 5 МБ/с), затем `tk675-mv5` для `bybit`; пробный запуск `tk675-trial` на `evaa-wave`. Порядок и урок про симлинки — [[srv-offload]].
