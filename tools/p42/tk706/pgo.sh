#!/bin/bash
# TK-706: v3 (mimalloc) bench, PGO training on H1 + light fixtures, merge, build v4-use, bench
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH LC_ALL=C
cd /root/perf706
R8=/root/perf706/bench/results8.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
while ! grep -q done-build3 bins/build3.log; do sleep 15; done
./queue4.sh /root/perf706/bins/v3-mimalloc v3 2
./statq.sh v3:/root/perf706/bins/v3-mimalloc
rm -rf pgo-train sc4-v4gen-H sc3-v4gen-H sc3-v4gen-L; mkdir pgo-train
export LLVM_PROFILE_FILE=/root/perf706/pgo-train/%p-%m.profraw
for t in miss hit; do
python3 bench.py --bin /root/perf706/bins/v4-gen --sym-every 6 --cap-gb 6 --tag v4gen-H1-$t --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-v4gen-H >> $R8 2>> bench/q8.err
done
./queue3.sh /root/perf706/bins/v4-gen v4gen lite
unset LLVM_PROFILE_FILE
LP=$(ls /opt/rbuild/rustup/toolchains/*/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata)
$LP merge -o pgo-train/merged.profdata pgo-train/*.profraw >> bins/build4.log 2>&1
echo "merge rc=$? $(ls pgo-train/*.profraw | wc -l) profraw" >> bins/build4.log
cd /root/perf706/src-l
P="CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 CARGO_PROFILE_RELEASE_PANIC=abort"
t0=$(date +%s)
env $P RUSTFLAGS="-C target-cpu=native -C profile-use=/root/perf706/pgo-train/merged.profdata" cargo build --release --offline --bin alpha --target-dir /root/perf706/tgt-v4-use -j2 >> /root/perf706/bins/build4.log 2>&1
cp /root/perf706/tgt-v4-use/release/alpha /root/perf706/bins/v4-use
echo "== v4-use took $(( $(date +%s)-t0 )) s $(md5sum /root/perf706/bins/v4-use | cut -c1-8)" >> /root/perf706/bins/build4.log
cd /root/perf706
./queue4.sh /root/perf706/bins/v4-use v4 2
./statq.sh v4:/root/perf706/bins/v4-use
echo done-pgo >> $R8
