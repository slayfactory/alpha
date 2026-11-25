#!/bin/bash
# TK-706: ws-measure2 (счётчики ключа хода, загрузок суток, ЦП в ходах) на флагах v1; v1-unwind (LTO без panic=abort). Запуск на srv.
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH LC_ALL=C
cd /root/perf706; LOG=/root/perf706/bins/build5.log
cd src-ws2; cargo check --release --offline --bin alpha --target-dir /root/perf706/tgt-chk2 -j2 >> $LOG 2>&1
rc=$?; echo "== check rc=$rc" >> $LOG; [ $rc -eq 0 ] || exit 1
while ! grep -q done-build3 /root/perf706/bins/build3.log; do sleep 15; done
bb(){ name=$1; shift; t0=$(date +%s); echo "== $name start $(date -u +%T)" >> $LOG
  env "$@" cargo build --release --offline --bin alpha --target-dir /root/perf706/tgt-$name -j2 >> $LOG 2>&1; rc=$?
  cp /root/perf706/tgt-$name/release/alpha /root/perf706/bins/$name 2>>$LOG; echo "== $name rc=$rc took $(( $(date +%s)-t0 )) s $(md5sum /root/perf706/bins/$name | cut -c1-8)" >> $LOG; }
cd /root/perf706/src-ws2
bb ws-measure2 CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 CARGO_PROFILE_RELEASE_PANIC=abort
cd /root/perf706/src-l
bb v1-unwind CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1
echo done-build5 >> $LOG
