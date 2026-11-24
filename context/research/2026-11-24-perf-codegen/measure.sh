#!/bin/sh
# measure.sh fuel|wall NAME [ENGINE] [TAG]: one variant's fuel (no lock: it is a count) or its
# wall times (under the machine lock), into results/. Run from anywhere.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
data=${BENCH_DATA:-/tmp/claude-0/-home-user-wasi-perl/ecbe2564-8e44-50cd-9bc9-1c1b283f37e5/scratchpad/data}
kind=$1 name=$2 engine=${3:-default} tag=${4:-}
cd "$root"
export UV_SYSTEM_CERTS=1
unset UV_NATIVE_TLS || true
wasm=build/variants/$name.wasm
if [ "$kind" = fuel ]; then
    uv run -q python "$here/bench.py" fuel "$wasm" "$data" "$here/results/fuel-$name.json" >/dev/null
    echo "fuel $name: ok"
else
    out="$here/results/wall-$name-$engine$tag.json"
    flock /home/user/wasi-perl-wt/machine.lock uv run -q python "$here/bench.py" wall "$wasm" "$data" "$engine" "$out" >/dev/null
    echo "wall $name $engine$tag: ok"
fi
