#!/usr/bin/env bash
# TK-702: мёртвый код крейта. Запуск: tools/vps-check.sh <дерево> deadcode (на одноразовой копии дерева на машине сборки).
# Все модули lib.rs становятся pub(crate), публичны только входы CLI (диспетчеры main.rs): rustc тогда видит недостижимое
# из команд (`cargo check --lib`: без тестов, примеров и bin/alpha-feat). Вывод — полный текст предупреждений dead_code;
# отметки в примерах / tests / bin сверять grep-ом.
set -euo pipefail
TGT="${1:?target-dir}"
J="${2:-3}"
sed -i -E 's/^pub mod /pub(crate) mod /' src/lib.rs
cat >> src/lib.rs <<'EOF'
pub use breakout::cli::{dispatch as d_bo, BreakoutCommand as c_bo};
pub use collector::cli::{dispatch as d_col, CollectCommand as c_col};
pub use commands::lob::{dispatch as d_lob, LobCommand as c_lob};
pub use statarb::cli::{dispatch as d_sa, StatarbCommand as c_sa};
EOF
cargo check --lib --target-dir "$TGT" -j "$J" 2>&1 | grep -v '^\s*$'
