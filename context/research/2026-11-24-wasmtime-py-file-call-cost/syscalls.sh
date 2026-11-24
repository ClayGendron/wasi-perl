#!/bin/sh
# Counts system calls per WASI call: runs the probe once with n=10 and once with n=2010 under
# `strace -f -c`, for one kind and one arm, and prints each syscall's count difference / 2000.
#   syscalls.sh <arm: py|py-blocking|cli|cli-timeout> <kind> <python> <cost.py> <probe.wasm> <data> <lib-dir or ->
set -eu
arm=$1 kind=$2 py=$3 cost=$4 probe=$5 data=$6 lib=$7
wt=${WASMTIME:-wasmtime}
run() {
  case $arm in
    py) strace -f -c -o "$1" "$py" "$cost" "$probe" "$data" "$2" 1 /dev/null --kinds "$kind" >/dev/null ;;
    py-blocking) strace -f -c -o "$1" "$py" "$cost" "$probe" "$data" "$2" 1 /dev/null --kinds "$kind" --lib "$lib" --blocking >/dev/null ;;
    py-patched) strace -f -c -o "$1" "$py" "$cost" "$probe" "$data" "$2" 1 /dev/null --kinds "$kind" --lib "$lib" >/dev/null ;;
    cli) strace -f -c -o "$1" "$wt" run --dir "$data::/d" "$probe" "$kind" "$2" >/dev/null ;;
    cli-timeout) strace -f -c -o "$1" "$wt" run -W timeout=3600s --dir "$data::/d" "$probe" "$kind" "$2" >/dev/null ;;
  esac
}
t=$(mktemp -d)
run "$t/small" 10
run "$t/large" 2010
python3 - "$t/small" "$t/large" "$arm" "$kind" <<'PY'
import sys
def counts(p):
    out = {}
    for line in open(p):
        f = line.split()
        if len(f) >= 5 and f[0][0].isdigit() and f[-1] not in ("total",):
            name = f[-1]
            calls = int(f[3])
            out[name] = calls
    return out
s, l = counts(sys.argv[1]), counts(sys.argv[2])
diff = {k: (l.get(k, 0) - s.get(k, 0)) / 2000 for k in set(s) | set(l)}
top = sorted(((v, k) for k, v in diff.items() if v >= 0.05), reverse=True)
print(sys.argv[3], sys.argv[4], "per WASI call:", ", ".join(f"{k} {v:.2f}" for v, k in top))
PY
rm -rf "$t"
