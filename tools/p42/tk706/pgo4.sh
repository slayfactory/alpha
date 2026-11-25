#!/bin/bash
# TK-706: PGO, попытка 4 — rustc SIGSEGV был в LoopVectorize (VPIRPhi::execute, LLVM 21) на fat LTO + profile-generate, а не в стеке: gen-сборка с `-C no-vectorize-loops` (инструментирование идёт до векторизации, профиль use-сборки не меняется); профиль как у p42 (fat LTO + cgu 1, без abort). Бенчи ждут tk706-mim и tk706-build5. Запуск на srv.
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH LC_ALL=C RUST_MIN_STACK=268435456
cd /root/perf706
LOG=/root/perf706/bins/build7.log
R8=/root/perf706/bench/results8b.jsonl
R9=/root/perf706/bench/results9.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
P="CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1"
while systemctl is-active --quiet tk706-pgo3; do sleep 5; done
cd /root/perf706/src-l
rm -rf /root/perf706/pgo-raw /root/perf706/pgo-train; mkdir -p /root/perf706/pgo-train
t0=$(date +%s)
env $P RUSTFLAGS="-C target-cpu=native -C no-vectorize-loops -C profile-generate=/root/perf706/pgo-raw" cargo build --release --offline --bin alpha --target-dir /root/perf706/tgt-v4-gen -j2 >> $LOG 2>&1
rc=$?
cp /root/perf706/tgt-v4-gen/release/alpha /root/perf706/bins/v4-gen 2>>$LOG
echo "== v4-gen rc=$rc took $(( $(date +%s)-t0 )) s" >> $LOG
[ $rc -eq 0 ] || { echo "v4-gen failed" >> $R8; exit 1; }
cd /root/perf706
rm -rf sc4-v4gen-H sc3-v4gen-H sc3-v4gen-L
export LLVM_PROFILE_FILE=/root/perf706/pgo-train/%p-%m.profraw
for t in miss hit; do
  python3 bench.py --bin /root/perf706/bins/v4-gen --sym-every 6 --cap-gb 6 --tag v4gen4-H1-$t --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-v4gen-H >> $R8 2>> bench/q8.err
done
./queue3.sh /root/perf706/bins/v4-gen v4gen4 lite
unset LLVM_PROFILE_FILE
LP=$(ls /opt/rbuild/rustup/toolchains/*/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata)
$LP merge -o pgo-train/merged.profdata pgo-train/*.profraw >> $LOG 2>&1
echo "merge rc=$? $(ls pgo-train/*.profraw | wc -l) profraw" >> $LOG
cd /root/perf706/src-l
t0=$(date +%s)
env $P RUSTFLAGS="-C target-cpu=native -C profile-use=/root/perf706/pgo-train/merged.profdata" cargo build --release --offline --bin alpha --target-dir /root/perf706/tgt-v4-use -j2 >> $LOG 2>&1
rc=$?
cp /root/perf706/tgt-v4-use/release/alpha /root/perf706/bins/v4-use 2>>$LOG
echo "== v4-use rc=$rc took $(( $(date +%s)-t0 )) s $(md5sum /root/perf706/bins/v4-use | cut -c1-8)" >> $LOG
[ $rc -eq 0 ] || { echo "v4-use failed" >> $R8; exit 1; }
cd /root/perf706
while systemctl is-active --quiet tk706-mim || systemctl is-active --quiet tk706-build5; do sleep 15; done
./queue4.sh /root/perf706/bins/v4-use v4b 2
./statq.sh v4b:/root/perf706/bins/v4-use
for t in miss hit; do
  python3 bench.py --window wb --bin /root/perf706/bins/v4-use --sym-every 6 --cap-gb 6 --tag hold-v4b-wb-$t --variants $HV --every 1 --threads 7 --sig /root/perf706/sc9-v4b-wb >> $R9 2>> bench/q9.err
done
echo done-pgo4 >> $R8
