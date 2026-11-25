#!/bin/bash
# TK-706: PGO поверх памяти исходов (приказ координатора 25.11): `pgo5.sh gen` — сборка с инструментированием (только сборка, Nice, без прогонов движка); `pgo5.sh use` — слияние профиля
# (/root/perf706/pgo5/*.profraw, снят `holdrun.sh train` в окне HOLD) и сборка `bins/m3` (профиль p42 = fat LTO + cgu 1 + abort, БЕЗ target-cpu=native; gen — с `-C no-vectorize-loops`:
# rustc SIGSEGV в LoopVectorize на fat LTO + profile-generate, LLVM 21). Дерево — /root/perf706/src-pgo (= код m2, SHA 44e78d709). Журнал — bins/pgo5.log.
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH LC_ALL=C RUST_MIN_STACK=268435456
LOG=/root/perf706/bins/pgo5.log
cd /root/perf706/src-pgo || exit 1
t0=$(date +%s)
case ${1:?gen|use} in
gen)
  rm -rf /root/perf706/pgo5-raw
  RUSTFLAGS="-C no-vectorize-loops -C profile-generate=/root/perf706/pgo5-raw" cargo build --profile p42 --offline --bin alpha --target-dir /root/perf706/tgt-p5-gen -j${J:-2} >> $LOG 2>&1
  rc=$?; cp /root/perf706/tgt-p5-gen/p42/alpha /root/perf706/bins/p5-gen 2>>$LOG
  echo "== p5-gen rc=$rc took $(( $(date +%s)-t0 )) s" >> $LOG; echo done-gen >> $LOG ;;
use)
  LP=$(ls /opt/rbuild/rustup/toolchains/*/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata)
  $LP merge -o /root/perf706/pgo5/merged.profdata /root/perf706/pgo5/*.profraw >> $LOG 2>&1
  echo "merge rc=$? $(ls /root/perf706/pgo5/*.profraw | wc -l) profraw" >> $LOG
  RUSTFLAGS="-C profile-use=/root/perf706/pgo5/merged.profdata" cargo build --profile p42 --offline --bin alpha --target-dir /root/perf706/tgt-p5-use -j${J:-2} >> $LOG 2>&1
  rc=$?; cp /root/perf706/tgt-p5-use/p42/alpha /root/perf706/bins/m3 2>>$LOG
  echo "== m3 rc=$rc took $(( $(date +%s)-t0 )) s $(md5sum /root/perf706/bins/m3 | cut -c1-8)" >> $LOG; echo done-use >> $LOG ;;
esac
