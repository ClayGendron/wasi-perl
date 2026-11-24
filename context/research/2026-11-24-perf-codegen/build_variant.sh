#!/bin/sh
# build_variant.sh NAME: applies variants/NAME.diff to recipes/, rebuilds the target under the
# machine lock with 4 jobs, keeps build/variants/NAME.wasm and its log, and reverts recipes/.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
name=$1
cd "$root"
git diff --quiet -- recipes || { echo "recipes/ has changes" >&2; exit 1; }
[ ! -s "$here/variants/$name.diff" ] || git apply "$here/variants/$name.diff"
mkdir -p build/variants
t0=$(date +%s)
status=0
WASI_PERL_JOBS=4 flock /home/user/wasi-perl-wt/machine.lock sh scripts/20_target.sh || status=$?
t1=$(date +%s)
git checkout -q -- recipes
cp build/logs/20_target.log "build/variants/$name.log"
[ "$status" = 0 ] || { echo "$name: build failed ($status), see build/variants/$name.log"; exit 1; }
cp build/artifacts/perl.wasm "build/variants/$name.wasm"
echo "$name: built in $((t1 - t0)) s, $(stat -c %s "build/variants/$name.wasm") bytes"
