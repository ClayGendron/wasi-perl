#!/bin/sh
# Research links of perl.wasm from the objects of a finished build (build/target), with one extra
# object and extra flags. Writes build/research/<name>.wasm. Run from the worktree root:
#   link.sh <name> <main-object|-> [extra objects and flags...]
# "-" keeps Perl's own perlmain.o. The static extensions and libperl.a are the build's own, so the
# result runs the same interpreter as build/artifacts/perl.wasm.
set -eu
ROOT=$(pwd)
T="$ROOT/build/target"
OUT="$ROOT/build/research"
mkdir -p "$OUT"
name=$1; main=$2; shift 2
[ "$main" = - ] && main="$T/perlmain.o"
exts=$(sed -n '3264p' "$ROOT/build/logs/20_target.log" | tr ' ' '\n' | grep '^lib/auto/.*\.a$' | sed "s|^|$T/|")
# shellcheck disable=SC2086
"$ROOT/recipes/wasi-cc" -o "$OUT/$name.wasm" -Wl,--wrap=__wasi_proc_exit -Wl,--wrap=times \
    "$main" $exts "$T/libperl.a" "$ROOT/build/stubs/wasi-stubs.o" "$ROOT/build/stubs/wasi-posix-stubs.o" -lm "$@"
echo "$OUT/$name.wasm $(wc -c <"$OUT/$name.wasm") bytes"
