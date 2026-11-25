#!/bin/bash
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH LC_ALL=C
while [ ! -f /root/perf706/bins/ws-measure ]; do sleep 10; done
sleep 3; systemctl stop tk706-build2b; sleep 3
cd /root/perf706/src-l; LOG=/root/perf706/bins/build3.log
P="CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 CARGO_PROFILE_RELEASE_PANIC=abort"
bb(){ name=$1; shift; t0=$(date +%s); echo "== $name start $(date -u +%T)" >> $LOG
  env "$@" cargo build --release --offline --bin alpha --target-dir /root/perf706/tgt-$name -j2 $FEAT >> $LOG 2>&1; rc=$?
  cp /root/perf706/tgt-$name/release/alpha /root/perf706/bins/$name 2>>$LOG; echo "== $name rc=$rc took $(( $(date +%s)-t0 )) s $(md5sum /root/perf706/bins/$name | cut -c1-8)" >> $LOG; }
FEAT="--features mimalloc" bb v3-mimalloc $P RUSTFLAGS="-C target-cpu=native"
rm -rf /root/perf706/pgo-raw
FEAT="" bb v4-gen $P RUSTFLAGS="-C target-cpu=native -C profile-generate=/root/perf706/pgo-raw"
echo done-build3 >> $LOG
