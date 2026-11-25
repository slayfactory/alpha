#!/bin/bash
# TK-706: проверка компиляции дерева на srv (быстрее сборки p42): chk.sh <каталог дерева>; лимит ЦП 200 %, журнал — stdout.
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH LC_ALL=C
cd "${1:?каталог дерева}" || exit 1
cargo check --offline --bin alpha --target-dir /root/perf706/tgt-chk2 -j2 2>&1 | tail -${LINES_MAX:-80}
