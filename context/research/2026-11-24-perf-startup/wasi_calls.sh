#!/bin/sh
# The WASI calls one perl program makes, counted from wasmtime's own trace of preview1.
#   wasi_calls.sh <perl.wasm> <artifacts folder> <label> -- <perl arguments...>
# Prints one line per function (calls, failures), then the path_open and path_filestat_get
# calls that failed and that succeeded, by count. The trace is kept in build/research/trace-<label>.log.
set -eu
wasm=$1; art=$2; label=$3; shift 4
W=/home/user/wasi-perl-downloads/wasmtime-v51.0.0-x86_64-linux/wasmtime
log="build/research/trace-$label.log"
WASMTIME_LOG=wasmtime_wasi=trace "$W" run -W exceptions=y -C cache=n \
    --dir "$art/lib/perl5::/usr/local/lib/perl5" --dir "$art/dev::/dev" "$wasm" "$@" >/dev/null 2>"$log" || true
echo "== $label: calls (failed) per WASI function"
grep -o 'function="[a-z_]*"}: wasmtime_wasi::p1::wasi_snapshot_preview1: result=[A-Za-z]*' "$log" |
    sed 's/function="\([a-z_]*\)".*result=\(.*\)/\1 \2/' | sort | uniq -c |
    awk '{n[$2]+=$1; if ($3=="Err") e[$2]+=$1} END {for (f in n) printf "%6d %6d  %s\n", n[f], e[f], f}' | sort -k3
echo "== $label: path_open results"
grep 'function="path_open"' "$log" | grep -o 'result=[A-Za-z]*' | sort | uniq -c
echo "== $label: path_filestat_get results"
grep 'function="path_filestat_get"' "$log" | grep -o 'result=[A-Za-z]*' | sort | uniq -c
echo "== $label: fd_read bytes total"
grep 'function="fd_read"' "$log" | grep -o 'result=Ok([0-9]*)' | tr -dc '0-9\n' | awk '{s+=$1} END {print s+0}'
