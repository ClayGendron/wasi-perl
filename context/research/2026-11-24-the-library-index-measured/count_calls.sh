#!/bin/sh
# count_calls.sh <artifact set> <perl args...>: the host's path_filestat_get and path_open calls
# on the library's grant in one run, split by whether they succeeded, from the wasmtime command
# line's trace of every WASI call. The library is the first grant, so it is descriptor 3.
# Run from the checkout's root; the trace stays in a temporary file.
set -eu
set_dir=$1
shift
cli=build/tools/wasmtime-v51.0.0-x86_64-linux/wasmtime
trace=$(mktemp)
WASMTIME_LOG=wasmtime_wasi=trace NO_COLOR=1 RUST_BACKTRACE=0 "$cli" run -W exceptions=y \
    --dir "$set_dir/lib/perl5::/usr/local/lib/perl5" --dir "$set_dir/dev::/dev" \
    --argv0 perl "$set_dir/perl.wasm" "$@" 2>"$trace" >/dev/null </dev/null
awk '
/function="path_filestat_get"|function="path_open"/ {
    match($0, /function="[a-z_]+"/); fn = substr($0, RSTART + 10, RLENGTH - 11)
    if ($0 ~ /result=/) {
        if (lib[fn]) { if ($0 ~ /result=Ok/) ok[fn]++; else failed[fn]++ }
    } else {
        lib[fn] = ($0 ~ /fd=Fd\(3\)/)
    }
}
END {
    printf "path_filestat_get ok %d failed %d; path_open ok %d failed %d\n",
        ok["path_filestat_get"], failed["path_filestat_get"], ok["path_open"], failed["path_open"]
}' "$trace"
rm -f "$trace"
