#!/bin/bash
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH LC_ALL=C
cd /root/perf706/src-l; mkdir -p /root/perf706/bins
LOG=/root/perf706/bins/build.log
b(){ name=$1; shift; t0=$(date +%s); echo "== $name start $(date -u +%T)" >> $LOG
  env "$@" cargo build --release --offline --bin alpha --target-dir /root/perf706/tgt-$name -j2 >> $LOG 2>&1; rc=$?
  cp /root/perf706/tgt-$name/release/alpha /root/perf706/bins/$name 2>>$LOG; echo "== $name rc=$rc took $(( $(date +%s)-t0 )) s $(md5sum /root/perf706/bins/$name | cut -c1-8)" >> $LOG; }
P="CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 CARGO_PROFILE_RELEASE_PANIC=abort"
b v1-lto  $P
b v2-native $P RUSTFLAGS="-C target-cpu=native"
