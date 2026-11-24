#!/bin/sh
# wasm_opt.sh IN.wasm OUT.wasm FLAGS...: binaryen's wasm-opt (version_133, pinned in the memo)
# over a linked perl.wasm, under the machine lock, keeping the features the module declares.
set -eu
in=$1 out=$2; shift 2
B=/home/user/wasi-perl-downloads/binaryen/binaryen-version_133/bin
t0=$(date +%s)
flock /home/user/wasi-perl-wt/machine.lock "$B/wasm-opt" "$in" -o "$out" "$@"
t1=$(date +%s)
echo "$(basename "$out"): wasm-opt $* in $((t1 - t0)) s, $(stat -c %s "$out") bytes"
