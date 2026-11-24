#!/usr/bin/env bash
# Before and after timings of the runner's fixed cost, each script under the machine lock.
#   measure.sh <base src folder> <session label>
# Run from the worktree, with the artifact set assembled at src/wasi_perl/artifacts/ and
# WASI_PERL_ARTIFACTS unset. "base" puts the pristine src first on PYTHONPATH; "after" is the
# worktree's own src through its .venv.
set -eu
base_src=$1 session=$2
here=$(cd "$(dirname "$0")" && pwd)
study=$here/../2026-11-24-perf-runner
out=$here/results
py=.venv/bin/python
lock=/home/user/wasi-perl-wt/machine.lock
unset WASI_PERL_ARTIFACTS
cache=$(mktemp -d)
chmod 700 "$cache"
# Warm the module cache for both trees (they share a key: the same perl.wasm and wasmtime-py).
flock "$lock" env PYTHONPATH=$base_src $py "$study/first_call.py" "$cache" > /dev/null
flock "$lock" $py "$study/first_call.py" "$cache" > /dev/null
for who in base after; do
  if [ "$who" = base ]; then pp=$base_src; else pp=; fi
  flock "$lock" env PYTHONPATH=$pp $py "$study/calls.py" "$who-$session" 60 "$out/calls.jsonl"
  flock "$lock" bash -c "for i in 1 2 3 4 5; do PYTHONPATH='$pp' $py '$study/first_call.py' '$cache' \
    | sed 's/^{/{\"label\": \"$who-$session\", /'; done >> '$out/first_call.jsonl'"
  flock "$lock" bash -c "for i in 1 2 3; do PYTHONPATH='$pp' $py '$here/peak_rss.py' '$cache' \
    | sed 's/^{/{\"label\": \"$who-$session\", /'; done >> '$out/peak_rss.jsonl'"
done
