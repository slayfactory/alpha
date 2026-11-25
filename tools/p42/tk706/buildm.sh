#!/bin/bash
# TK-706: сборка бинаря профиля p42 (fat LTO + cgu 1 + abort) из дерева ветки с памятью исходов хода: дерево лежит в /root/perf706/src-memo (распаковано с ПК),
# бинарь — /root/perf706/bins/<имя>; запуск на srv: buildm.sh <имя>. Хеш дерева вывода H1 = базе (`92e20ecd72a6bef1`) — проверка прогоном `bench.py`.
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH LC_ALL=C
NAME=${1:?имя бинаря}
LOG=/root/perf706/bins/build-$NAME.log
cd ${SRC:-/root/perf706/src-memo} || exit 1
t0=$(date +%s)
cargo build --profile p42 --offline --bin alpha --target-dir /root/perf706/tgt-$NAME -j${J:-2} >> $LOG 2>&1
rc=$?
cp /root/perf706/tgt-$NAME/p42/alpha /root/perf706/bins/$NAME 2>>$LOG
echo "== $NAME rc=$rc took $(( $(date +%s)-t0 )) s $(md5sum /root/perf706/bins/$NAME | cut -c1-8)" >> $LOG
echo done-build-$NAME >> $LOG
