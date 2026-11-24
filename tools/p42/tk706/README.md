# TK-706: замеры скорости движка `breakout tick` (25.11.2026)

Скрипты, которыми сняты числа `docs/findings/p42-engine-speedup-2026-11-25.md`; на srv лежат в `/root/perf706/` (рабочий каталог замеров, вне git). Мерные бинари — на srv `/root/perf706/bins/`, исходники мерных правок (счётчики `WS_*` в `sim_tail_e`) — только там (`ws-tick*.rs`), в репозиторий не входят: поведение движка они не меняют, но и в бой не идут.

- `bench.py` — один вызов движка на фикстуре (`--bin`, `--variants`, `--sym-every`, `--sig`, `--perf`, `--stat`), одна строка JSON: ЦП user + sys, стадии, хеш дерева вывода.
- `queue3.sh` (малые фикстуры L1 / H-малая), `queue4.sh` (H1: промах + повторы), `statq.sh` (`perf stat` на H1) — очереди; результаты `bench/results{3,4,6,7,8}.jsonl`.
- `build1.sh`, `build3.sh`, `pgo.sh`, `pgo2.sh` (контроль PGO на другом окне) — сборки `v1-lto`, `v2-native`, `v3-mimalloc`, PGO (`v4-gen` → обучение → `v4-use`).
- `dup3.py`, `share.py`, `share2.py` — дублирование входов по архивам клеток (`cells-arch/*.tar.zst`), только чтение.
